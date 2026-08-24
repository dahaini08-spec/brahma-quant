"""
fangcang_tradfi_db.py — TradFi方仓向量检索库 v1.0
设计院封印 2026-08-10 苏摩111

S级(7只): GLD/QQQ/NVDA/MSTR/AAPL/MSFT/SLV
A级(7只): WDC/MU/INTC/TSLA/GOOGL/AMD/USO
总案例: 1556个  WR=55.6%  EV=+0.725%

核心设计:
  - 正股日线K线(yfinance) → BBW标准化 → 与BTC方仓统一尺度
  - 独立Qdrant内存集合，不污染BTC/ETH/SOL库
  - 查询接口与 fangcang_vector_db 保持一致
"""
import json, time, logging
from pathlib import Path
from typing import Optional, List

import numpy as np

logger = logging.getLogger(__name__)

BASE      = Path(__file__).parent.parent
DATA_PATH = BASE / 'data' / 'fangcang_cases_tradfi.json'

# BBW标准化参数
BTC_AVG_BBW = 0.89
STOCK_AVG_BBW = {
    'GLD':0.84,'QQQ':0.72,'NVDA':2.31,'MSTR':3.12,
    'AAPL':1.19,'MSFT':1.24,'SLV':1.52,
    'WDC':2.87,'MU':2.41,'INTC':2.18,'TSLA':3.44,
    'GOOGL':1.89,'AMD':2.73,'USO':2.34,
}
# 代币 → 正股映射
TOKEN_TO_STOCK = {
    'XAUUSDT':'GLD','QQQUSDT':'QQQ','NVDAUSDT':'NVDA','MSTRUSDT':'MSTR',
    'AAPLUSDT':'AAPL','MSFTUSDT':'MSFT','XAGUSDT':'SLV',
    'SNDKUSDT':'WDC','MUUSDT':'MU','INTCUSDT':'INTC','TSLAUSDT':'TSLA',
    'GOOGLUSDT':'GOOGL','AMDUSDT':'AMD','CLUSDT':'USO',
}

# 归一化边界（TradFi BBW标准化后）
NORM_BOUNDS = {
    'bb_norm':      (0.10, 1.30),
    'squeeze_bars': (8.0,  100.0),
    'burst_atr':    (0.5,  3.5),
    'vol_ratio':    (1.0,  10.0),
    'rsi':          (15.0, 85.0),
}

TIER_CODE = {'S':1.0,'A':0.67,'B':0.33}

_client    = None
_COLL      = 'tradfi_cases'
_build_ts  = 0.0
_cases_raw: List[dict] = []


def _clip(v, lo, hi):
    return max(0.0, min(1.0, (v-lo)/(hi-lo))) if hi != lo else 0.5


def _normalize_bbw(bb_raw: float, stock: str) -> float:
    avg = STOCK_AVG_BBW.get(stock, 2.0)
    return bb_raw * (BTC_AVG_BBW / avg)


def _to_vector(c: dict) -> List[float]:
    stock  = c.get('stock_ticker', 'NVDA')
    bb_raw = c.get('min_bb_width', 1.0)
    bb_n   = c.get('min_bb_width_norm') or _normalize_bbw(bb_raw, stock)
    return [
        _clip(bb_n,              *NORM_BOUNDS['bb_norm']),
        _clip(c['squeeze_bars'], *NORM_BOUNDS['squeeze_bars']),
        _clip(c['burst_atr_mult'],*NORM_BOUNDS['burst_atr']),
        _clip(c['vol_ratio_peak'],*NORM_BOUNDS['vol_ratio']),
        _clip(c['rsi_at_burst'], *NORM_BOUNDS['rsi']),
        1.0 if c['direction'] == 'UP' else 0.0,
        1.0 if c.get('is_genuine_breakout') else 0.0,
        TIER_CODE.get(c.get('tier','A'), 0.67),
    ]


def _build():
    global _client, _cases_raw, _build_ts
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance, VectorParams, PointStruct, OptimizersConfigDiff
        )
    except ImportError:
        logger.warning('qdrant_client未安装，TradFi向量检索不可用')
        return False

    if not DATA_PATH.exists():
        logger.warning('TradFi案例库不存在: %s', DATA_PATH)
        return False

    t0 = time.time()
    cases = json.loads(DATA_PATH.read_text())
    if not cases:
        return False

    # BBW标准化写入
    for c in cases:
        stock = c.get('stock_ticker', 'NVDA')
        c['min_bb_width_norm'] = round(_normalize_bbw(c['min_bb_width'], stock), 4)

    client = QdrantClient(':memory:')
    client.create_collection(
        collection_name=_COLL,
        vectors_config=VectorParams(size=8, distance=Distance.COSINE),
        optimizers_config=OptimizersConfigDiff(indexing_threshold=0),
    )

    points = []
    for idx, c in enumerate(cases):
        points.append(PointStruct(
            id=idx, vector=_to_vector(c),
            payload={k: c[k] for k in (
                'symbol','stock_ticker','tier','direction',
                'min_bb_width','min_bb_width_norm','squeeze_bars',
                'burst_atr_mult','vol_ratio_peak','rsi_at_burst',
                'future_return_24h','is_genuine_breakout',
            ) if k in c}
        ))

    for i in range(0, len(points), 500):
        client.upsert(collection_name=_COLL, points=points[i:i+500])

    _client    = client
    _cases_raw = cases
    _build_ts  = time.time()
    logger.info('TradFi向量库建立: %d案例 %.2fs', len(cases), time.time()-t0)
    return True


def _ensure():
    global _client
    if _client is None:
        _build()


def query_tradfi(
    token:        str,
    bb_width_raw: float,
    squeeze_bars: float,
    burst_atr:    float,
    vol_ratio:    float,
    rsi:          float,
    direction:    str = 'UP',
    top_k:        int = 20,
) -> dict:
    """
    查询最相似的TradFi历史方仓案例。

    参数:
      token        — Binance代币符号（如 NVDAUSDT）
      bb_width_raw — 当前BB宽度（代币原始%）
      direction    — 'UP' or 'DOWN'

    返回:
      { n, wr, wr_directional, ev, median, cases }
    """
    _ensure()
    if _client is None:
        return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}

    try:
        stock  = TOKEN_TO_STOCK.get(token, 'NVDA')
        bb_n   = _normalize_bbw(bb_width_raw, stock)
        tier_c = 1.0  # 查询时不限制tier

        qvec = [
            _clip(bb_n,       *NORM_BOUNDS['bb_norm']),
            _clip(squeeze_bars,*NORM_BOUNDS['squeeze_bars']),
            _clip(burst_atr,  *NORM_BOUNDS['burst_atr']),
            _clip(vol_ratio,  *NORM_BOUNDS['vol_ratio']),
            _clip(rsi,        *NORM_BOUNDS['rsi']),
            1.0 if direction=='UP' else 0.0,
            1.0,  # genuine
            tier_c,
        ]

        res = _client.query_points(
            collection_name=_COLL,
            query=qvec,
            limit=top_k,
            with_payload=True,
        )
        cases  = [r.payload for r in res.points]
        rets   = np.array([c['future_return_24h'] for c in cases])
        if len(rets) == 0:
            return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}

        wr_long  = float((rets>0).mean())
        wr_short = float((rets<0).mean())
        wr_dir   = wr_long if direction=='UP' else wr_short
        return {
            'n':              len(cases),
            'wr':             round(wr_long, 3),
            'wr_directional': round(wr_dir, 3),
            'ev':             round(float(rets.mean()), 4),
            'median':         round(float(np.median(rets)), 4),
            'p10':            round(float(np.percentile(rets,10)), 3),
            'p90':            round(float(np.percentile(rets,90)), 3),
            'cases':          cases,
        }
    except Exception as e:
        logger.warning('TradFi查询失败: %s', e)
        return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}


def get_index_info() -> dict:
    _ensure()
    if _client is None:
        return {'status':'unavailable','n':0}
    try:
        info = _client.get_collection(_COLL)
        return {'status':'ok','n':info.points_count,
                'build_age_s': round(time.time()-_build_ts,1)}
    except Exception as e:
        return {'status':'error','error':str(e)}


# ── CLI ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    print('=== TradFi方仓向量库测试 ===')
    info = get_index_info()
    print('库状态:', info)

    tests = [
        ('NVDAUSDT', 3.0, 15, 0.9, 2.5, 65, 'UP'),
        ('XAUUSDT',  1.2, 12, 0.8, 2.0, 62, 'UP'),
        ('TSLAUSDT', 4.0, 20, 1.5, 3.0, 35, 'DOWN'),
        ('AAPLUSDT', 1.5, 18, 0.7, 1.8, 68, 'UP'),
    ]
    for token, bb, sq, burst, vol, rsi, d in tests:
        t0 = time.time()
        r = query_tradfi(token, bb, sq, burst, vol, rsi, d)
        ms = (time.time()-t0)*1000
        print('%-14s bb=%.1f%% rsi=%d %s: n=%d WR=%.0f%% EV=%+.3f%% %.0fms' % (
            token, bb, rsi, d, r['n'], 100*r['wr'], r['ev'], ms))

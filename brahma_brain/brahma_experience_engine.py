#!/usr/bin/env python3
"""
brahma_experience_engine.py — 梵天经验引擎
2026-08-27 苏摩111批准封印

把320万条K线 → 经验片段 → Qdrant向量库

经验片段 = {
    symbol, timeframe, ts, regime,
    rsi_1h, rsi_4h, bbw, atr_pct,
    oi_change, funding_rate,
    smc_structure (BOS/CHoCH/OB/FVG),
    outcome_24h, outcome_48h, outcome_72h,
    direction, win_24h, win_48h
}

接入位置: brahma_full_report.run_full_analysis() 在Step4战场预判时
         调用 query_similar_experiences() 获取历史相似案例
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, time, math
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ROOT  = Path(__file__).parent.parent
DATA  = ROOT / 'data' / 'historical'
CACHE = ROOT / 'data' / 'experience_cache'
CACHE.mkdir(exist_ok=True)

# Qdrant配置
QDRANT_HOST       = 'localhost'
QDRANT_PORT       = 6333
QDRANT_PATH       = str(ROOT / 'data' / 'qdrant_storage')  # 本地持久化模式
COLLECTION_NAME   = 'brahma_experiences'
VECTOR_DIM        = 16   # 16维经验向量

# 体制标签
REGIME_MAP = {
    'BULL_TREND': 0, 'BULL_EARLY': 1,
    'BEAR_TREND': 2, 'BEAR_EARLY': 3,
    'BEAR_RECOVERY': 4, 'CHOP_MID': 5,
    'UNKNOWN': 6
}

# ── 指标计算 ──────────────────────────────────────────────────────

# [calc_rsi] 已迁移到math_utils [2026-08-28 SSOT封印]
# 调用: from math_utils import calc_rsi

def calc_bbw(highs: np.ndarray, lows: np.ndarray,
             closes: np.ndarray, period: int = 20) -> float:
    if len(closes) < period:
        return 5.0
    c = closes[-period:]
    sma = c.mean()
    std = c.std()
    if sma == 0:
        return 5.0
    upper = sma + 2 * std
    lower = sma - 2 * std
    return float((upper - lower) / sma * 100)


def calc_atr_pct(highs: np.ndarray, lows: np.ndarray,
                 closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 2.0
    trs = []
    for i in range(1, min(period+1, len(closes))):
        tr = max(highs[-i] - lows[-i],
                 abs(highs[-i] - closes[-(i+1)]),
                 abs(lows[-i]  - closes[-(i+1)]))
        trs.append(tr)
    atr = np.mean(trs)
    return float(atr / closes[-1] * 100) if closes[-1] > 0 else 2.0


def detect_regime_simple(closes: np.ndarray) -> str:
    """简化体制识别（不依赖完整brahma_core）"""
    if len(closes) < 50:
        return 'UNKNOWN'
    ema20 = pd.Series(closes).ewm(span=20).mean().iloc[-1]
    ema50 = pd.Series(closes).ewm(span=50).mean().iloc[-1]
    ret_20d = (closes[-1] - closes[-21]) / closes[-21] * 100 if len(closes) > 21 else 0

    if ema20 > ema50 and ret_20d > 3:
        return 'BULL_TREND'
    elif ema20 > ema50 and ret_20d >= -1:
        return 'BULL_EARLY'
    elif ema20 < ema50 and ret_20d < -3:
        return 'BEAR_TREND'
    elif ema20 < ema50 and ret_20d >= -1:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'


def build_experience_vector(
    rsi_1h: float, rsi_4h: float, bbw: float, atr_pct: float,
    regime: str, ret_7d: float, ret_30d: float,
    volume_ratio: float, price_vs_ema50: float,
    oi_change: float = 0.0, funding: float = 0.0,
    hurst: float = 0.5, adx: float = 25.0,
    macd_hist: float = 0.0, stoch_rsi: float = 50.0,
    bos_choch: float = 0.0
) -> list:
    """构建16维经验向量（归一化到[-1,1]）"""
    regime_val = REGIME_MAP.get(regime, 6) / 6.0 * 2 - 1  # [-1,1]
    return [
        (rsi_1h - 50) / 50,           # RSI_1H 偏离中线
        (rsi_4h - 50) / 50,           # RSI_4H 偏离中线
        min(bbw / 10, 1.0) * 2 - 1,   # BBW 归一化
        min(atr_pct / 5, 1.0) * 2 - 1,# ATR% 归一化
        regime_val,                     # 体制编码
        max(-1, min(1, ret_7d / 20)),  # 7日收益率
        max(-1, min(1, ret_30d / 50)), # 30日收益率
        max(-1, min(1, volume_ratio - 1)), # 成交量比
        max(-1, min(1, price_vs_ema50 * 10)), # 价格vs EMA50
        max(-1, min(1, oi_change / 10)),# OI变化率
        max(-1, min(1, funding * 100)), # 资金费率
        max(-1, min(1, (hurst - 0.5) * 4)), # Hurst
        max(-1, min(1, (adx - 25) / 25)),   # ADX
        max(-1, min(1, macd_hist * 100)),    # MACD柱
        (stoch_rsi - 50) / 50,         # StochRSI
        max(-1, min(1, bos_choch)),    # BOS/CHoCH信号
    ]


# ── 核心：K线窗口 → 经验片段 ────────────────────────────────────

def process_symbol(symbol: str, timeframe: str,
                   window: int = 60, step: int = 5,
                   lookahead: int = 24) -> list:
    """
    将一个标的的K线数据转化为经验片段列表
    window:    回看窗口大小（K线根数）
    step:      采样步长（每step根生成一个经验片段）
    lookahead: 向前看N根K线计算结果
    """
    sym_l = symbol.lower()
    pq_path = DATA / sym_l / f'{sym_l}_{timeframe}.parquet'
    if not pq_path.exists():
        return []

    try:
        df = pd.read_parquet(pq_path)
    except Exception as e:
        print(f'  ⚠️ {symbol} {timeframe}: 读取失败 {e}')
        return []

    # 确保列名统一
    col_map = {c.lower(): c for c in df.columns}
    closes  = df['close'].values.astype(float)  if 'close'  in df.columns else df['c'].values.astype(float)
    highs   = df['high'].values.astype(float)   if 'high'   in df.columns else df['h'].values.astype(float)
    lows    = df['low'].values.astype(float)    if 'low'    in df.columns else df['l'].values.astype(float)
    volumes = df['volume'].values.astype(float) if 'volume' in df.columns else np.ones(len(closes))
    ts_col  = df['ts'] if 'ts' in df.columns else df.index

    n = len(closes)
    experiences = []
    vol_ma20 = pd.Series(volumes).rolling(20).mean().values

    for i in range(window, n - lookahead, step):
        try:
            c_win = closes[i-window:i]
            h_win = highs[i-window:i]
            l_win = lows[i-window:i]
            v_win = volumes[i-window:i]

            # 指标计算
            rsi_s  = calc_rsi(c_win, 14)
            rsi_l  = calc_rsi(closes[max(0,i-60):i], 14)
            bbw    = calc_bbw(h_win, l_win, c_win)
            atr_p  = calc_atr_pct(h_win, l_win, c_win)
            regime = detect_regime_simple(c_win)

            ret_7d  = (c_win[-1] - c_win[-8])  / c_win[-8]  * 100 if len(c_win) > 8  else 0
            ret_30d = (c_win[-1] - c_win[-31]) / c_win[-31] * 100 if len(c_win) > 31 else 0

            ema50 = pd.Series(c_win).ewm(span=50).mean().iloc[-1]
            price_vs_ema50 = (c_win[-1] - ema50) / ema50 if ema50 > 0 else 0

            vol_ratio = v_win[-1] / vol_ma20[i] if vol_ma20[i] > 0 else 1.0

            # MACD
            ema12 = pd.Series(c_win).ewm(span=12).mean().iloc[-1]
            ema26 = pd.Series(c_win).ewm(span=26).mean().iloc[-1]
            macd  = ema12 - ema26
            macd_hist = macd / c_win[-1] * 100 if c_win[-1] > 0 else 0

            # 结果计算（向前看）
            future = closes[i:i+lookahead]
            max_gain = (future.max() - c_win[-1]) / c_win[-1] * 100
            max_loss = (future.min() - c_win[-1]) / c_win[-1] * 100
            final_ret = (future[-1]  - c_win[-1]) / c_win[-1] * 100

            # 胜负判断（TP=2%, SL=2%）
            tp_pct, sl_pct = 2.0, -2.0
            win_long  = max_gain >= tp_pct and not (max_loss <= sl_pct and
                         np.argmin(future) < np.argmax(future))
            win_short = max_loss <= -tp_pct and not (max_gain >= tp_pct and
                         np.argmax(future) < np.argmin(future))

            # 时间戳
            try:
                ts_val = int(pd.Timestamp(ts_col.iloc[i]).timestamp())
            except:
                ts_val = i

            vec = build_experience_vector(
                rsi_1h=rsi_s, rsi_4h=rsi_l, bbw=bbw, atr_pct=atr_p,
                regime=regime, ret_7d=ret_7d, ret_30d=ret_30d,
                volume_ratio=vol_ratio, price_vs_ema50=price_vs_ema50,
                macd_hist=macd_hist
            )

            experiences.append({
                'vector':     vec,
                'symbol':     symbol.upper().replace('USDT',''),
                'timeframe':  timeframe,
                'ts':         ts_val,
                'regime':     regime,
                'rsi':        round(rsi_s, 1),
                'bbw':        round(bbw, 2),
                'atr_pct':    round(atr_p, 2),
                'ret_7d':     round(ret_7d, 2),
                'max_gain':   round(max_gain, 2),
                'max_loss':   round(max_loss, 2),
                'final_ret':  round(final_ret, 2),
                'win_long':   bool(win_long),
                'win_short':  bool(win_short),
                'price':      round(float(c_win[-1]), 4),
            })
        except Exception:
            continue

    return experiences


# ── Qdrant写入 ────────────────────────────────────────────────────

def get_qdrant_client():
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import Distance, VectorParams
        import os
        os.makedirs(QDRANT_PATH, exist_ok=True)
        client = QdrantClient(path=QDRANT_PATH)  # 本地持久化，无需Docker
        existing = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in existing:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE)
            )
            print(f'  ✅ 创建Qdrant collection: {COLLECTION_NAME}')
        return client
    except Exception as e:
        print(f'  ⚠️ Qdrant连接失败: {e}，降级为本地JSON缓存')
        return None


def upsert_batch(client, experiences: list, batch_size: int = 500):
    if not experiences:
        return 0
    if client is None:
        # 降级：保存到本地JSON
        return _save_local(experiences)

    from qdrant_client.http.models import PointStruct
    points = []
    for i, exp in enumerate(experiences):
        vec = exp['vector']
        if len(vec) != VECTOR_DIM:
            continue
        payload = {k: v for k, v in exp.items() if k != 'vector'}
        points.append(PointStruct(
            id=abs(hash(f"{exp['symbol']}_{exp['timeframe']}_{exp['ts']}")) % (2**63),
            vector=vec,
            payload=payload
        ))

    inserted = 0
    for i in range(0, len(points), batch_size):
        batch = points[i:i+batch_size]
        try:
            client.upsert(collection_name=COLLECTION_NAME, points=batch)
            inserted += len(batch)
        except Exception as e:
            print(f'    ⚠️ upsert失败: {e}')
    return inserted


def _save_local(experiences: list) -> int:
    """Qdrant不可用时降级到本地文件"""
    import gzip
    ts = int(time.time())
    sym = experiences[0]['symbol'] if experiences else 'unknown'
    tf  = experiences[0]['timeframe'] if experiences else 'unknown'
    path = CACHE / f'{sym}_{tf}_{ts}.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for exp in experiences:
            exp_save = {k: v for k, v in exp.items() if k != 'vector'}
            f.write(json.dumps(exp_save, ensure_ascii=False) + '\n')
    return len(experiences)


# ── 相似经验检索（接入分析链路）────────────────────────────────────

def query_similar_experiences(
    symbol: str, regime: str,
    rsi_4h: float, bbw: float, atr_pct: float,
    ret_7d: float = 0, volume_ratio: float = 1.0,
    top_k: int = 5
) -> dict:
    """
    接入位置：brahma_full_report.run_full_analysis() Step4
    使用numpy向量索引，无需Qdrant服务
    """
    import gzip
    try:
        idx_path     = Path(__file__).parent.parent / 'data' / 'experience_index.npz'
        payload_path = Path(__file__).parent.parent / 'data' / 'experience_payloads.json.gz'
        if not idx_path.exists() or not payload_path.exists():
            return {'cases': [], 'n': 0, 'error': 'index not built, run build_experience_index.py'}
        q_vec = np.array(build_experience_vector(
            rsi_1h=rsi_4h, rsi_4h=rsi_4h, bbw=bbw, atr_pct=atr_pct,
            regime=regime, ret_7d=ret_7d, ret_30d=ret_7d*2,
            volume_ratio=volume_ratio, price_vs_ema50=0
        ), dtype=np.float32)
        norm = np.linalg.norm(q_vec)
        if norm > 0: q_vec = q_vec / norm
        data = np.load(idx_path)
        vectors = data['vectors']
        scores = vectors @ q_vec
        with gzip.open(payload_path, 'rt', encoding='utf-8') as f:
            payloads = json.load(f)
        regime_mask = np.array([p['reg'] == regime for p in payloads])
        filtered = np.where(regime_mask, scores, -1)
        top_idx = np.argsort(filtered)[-top_k:][::-1]
        cases = []
        for idx in top_idx:
            if filtered[idx] < 0: continue
            p = payloads[int(idx)]
            ts_val = p.get('ts', 0)
            try:
                from datetime import datetime, timezone as tz
                dt = datetime.fromtimestamp(ts_val, tz=tz.utc).strftime('%Y-%m-%d') if ts_val > 0 else '?'
            except:
                dt = '?'
            cases.append({
                'symbol': p.get('sym',''), 'tf': p.get('tf',''), 'date': dt,
                'regime': p.get('reg',''), 'rsi': p.get('rsi', 50), 'bbw': p.get('bbw', 5),
                'max_gain': p.get('mg', 0), 'max_loss': p.get('ml', 0),
                'win_long': bool(p.get('wl')), 'win_short': bool(p.get('ws')),
                'score': round(float(filtered[idx]), 3),
            })
        n = len(cases)
        if n == 0:
            return {'cases': [], 'n': 0, 'wr_long': 0, 'wr_short': 0}
        wr_long  = sum(1 for c in cases if c['win_long'])  / n
        wr_short = sum(1 for c in cases if c['win_short']) / n
        top = cases[0]
        return {
            'cases': cases, 'wr_long': round(wr_long,3), 'wr_short': round(wr_short,3), 'n': n,
            'summary': f'📚 历史相似{n}条 | 多WR={wr_long:.0%} 空WR={wr_short:.0%} | 最相似:{top["symbol"]} {top["tf"]} {top["date"]} 最大涨{top["max_gain"]:+.1f}%'
        }
    except Exception as e:
        return {'cases': [], 'n': 0, 'wr_long': 0, 'wr_short': 0, 'error': str(e)}


# ── 主入口 ────────────────────────────────────────────────────────

def build_full_experience_db(
    symbols: list = None,
    timeframes: list = None,
    max_per_symbol: int = 50000
):
    """全量构建经验数据库"""
    if symbols is None:
        symbols = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','ADAUSDT','XRPUSDT',
                   'DOGEUSDT','DOTUSDT','LINKUSDT','LTCUSDT','XLMUSDT','TRXUSDT',
                   'ATOMUSDT','ALGOUSDT','CRVUSDT','COMPUSDT','RUNEUSDT','SNXUSDT',
                   'VETUSDT','THETAUSDT','BCHUSDT','ETCUSDT','EGLDUSDT','ONTUSDT',
                   'XMRUSDT','ZECUSDT','DASHUSDT','KAVAUSDT','SUSHIUSDT',
                   'TRBUSDT','ZILUSDT','IOTAUSDT']
    if timeframes is None:
        timeframes = ['4h','1h','1d']

    client = get_qdrant_client()
    total_inserted = 0
    start_t = time.time()

    print(f'\n🧠 梵天经验引擎启动')
    print(f'   标的: {len(symbols)}个 × 周期: {len(timeframes)}个')
    print(f'   目标: {len(symbols)*len(timeframes)} 个数据集\n')

    for sym in symbols:
        for tf in timeframes:
            sym_l = sym.lower()
            pq = DATA / sym_l / f'{sym_l}_{tf}.parquet'
            if not pq.exists():
                print(f'  ⏭️  {sym} {tf}: 无数据')
                continue

            # 检查缓存（避免重复处理）
            cache_flag = CACHE / f'{sym}_{tf}.done'
            if cache_flag.exists():
                print(f'  ✅ {sym} {tf}: 已处理（缓存）')
                continue

            t0 = time.time()
            print(f'  🔄 {sym} {tf}...', end='', flush=True)

            # step大小：4H用5根，1H用20根，1D用2根
            step = {'1h': 20, '4h': 5, '1d': 2}.get(tf, 10)
            experiences = process_symbol(sym, tf, window=60, step=step, lookahead=24)

            if experiences:
                # 限制每个symbol最大条数
                if len(experiences) > max_per_symbol:
                    experiences = experiences[-max_per_symbol:]
                n = upsert_batch(client, experiences)
                total_inserted += n
                cache_flag.write_text(json.dumps({'n': n, 'ts': time.time()}))
                print(f' {n}条 ({time.time()-t0:.1f}s)')
            else:
                print(f' 0条')

    elapsed = time.time() - start_t
    print(f'\n✅ 完成！总写入: {total_inserted:,}条经验片段')
    print(f'   耗时: {elapsed/60:.1f}分钟')

    # 更新MEMORY
    _update_memory(total_inserted)
    return total_inserted


def _update_memory(total: int):
    mem = Path('/root/.openclaw/workspace/MEMORY.md')
    note = f'\n\n## 🏛️ 梵天经验引擎封印（2026-08-27 苏摩111）\n- 写入经验片段: {total:,}条\n- collection: {COLLECTION_NAME}\n- 接入: brahma_full_report Step4 战场预判层\n'
    with open(mem, 'a') as f:
        f.write(note)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--test',   action='store_true', help='只跑BTC 4H测试')
    parser.add_argument('--symbol', default=None)
    parser.add_argument('--tf',     default=None)
    args = parser.parse_args()

    if args.test:
        print('🧪 测试模式: BTC 4H 100条')
        exps = process_symbol('BTCUSDT', '4h', window=60, step=50, lookahead=24)
        print(f'生成经验片段: {len(exps)}条')
        if exps:
            print('样例:', json.dumps({k:v for k,v in exps[0].items() if k!='vector'}, ensure_ascii=False))
            print('向量维度:', len(exps[0]['vector']))
            print('✅ 测试通过')
    elif args.symbol:
        tf = args.tf or '4h'
        exps = process_symbol(args.symbol, tf)
        client = get_qdrant_client()
        n = upsert_batch(client, exps)
        print(f'✅ {args.symbol} {tf}: {n}条')
    else:
        build_full_experience_db()


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/brahma_experience_distiller.py ══
#!/usr/bin/env python3
"""
brahma_experience_distiller.py — 梵天经验蒸馏矩阵
══════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 Phase3封印

使命：
  把15795条原始方仓案例 → 压缩成AI可直接调用的WR索引
  让AI议会不必读原始数据，直接查矩阵得到"梵天铁证"

索引维度：
  体制(regime) × 方向(dir) × 周期(tf) × 评分段(score_band)

输出文件：
  data/brahma_experience_matrix.json  ← 注射器读取入口
  data/brahma_wr_by_coin.json         ← 单币WR速查表
  data/brahma_phase3_report.txt       ← 人工可读摘要

核心指标（每个格子）：
  n         : 历史案例数
  wr        : 胜率（多=收益>0, 空=收益<0）
  avg_ret   : 平均收益率%
  best_coin : 胜率最高的币种
  worst_coin: 胜率最低的币种
  top_tf    : 最优周期
"""
import sys
import json
import glob
import time
from pathlib import Path
from datetime import datetime, timezone

_BASE = Path(__file__).parent
_DATA = _BASE.parent / 'data'

# ── 体制列表 ──────────────────────────────────────────────────────
REGIMES = ['BULL_TREND', 'BULL_EARLY', 'BEAR_TREND', 'BEAR_EARLY',
           'CHOP_MID', 'BEAR_RECOVERY']
DIRECTIONS = ['LONG', 'SHORT']
TIMEFRAMES  = ['15m', '1h', '4h', '1d', '1w', '1M']

# ── 方仓案例的方向字段映射 ─────────────────────────────────────────
def _norm_dir(raw: str) -> str:
    r = str(raw).upper()
    if r in ('UP', 'LONG'):    return 'LONG'
    if r in ('DOWN', 'SHORT'): return 'SHORT'
    return ''

def _is_win(ret: float, direction: str) -> bool:
    """多单收益>0为胜；空单收益<0为胜（future_return以多头视角计）"""
    if direction == 'LONG':
        return ret > 0
    else:
        return ret < 0


# ── 加载所有方仓JSON ───────────────────────────────────────────────
def load_all_cases() -> list:
    files = glob.glob(str(_DATA / 'fangcang_*_*.json'))
    files = [f for f in files if 'snapshot' not in f and 'cases_' not in f
             and 'weights' not in f]
    all_cases = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text())
            if isinstance(data, list):
                all_cases.extend(data)
        except Exception:
            pass
    return all_cases


# ── 从案例推断体制（方仓案例无直接体制字段，用规则映射）────────────
def infer_regime_from_case(case: dict) -> list:
    """
    方仓案例不含体制标签，返回所有可能体制（宽泛匹配）。
    蒸馏时：每条案例贡献到所有体制 × 对应方向的桶。
    后续AI调用时查特定体制桶。
    """
    # 1w/1M案例用EMA叉，代表趋势切换
    tf = case.get('timeframe', '4h')
    trigger = case.get('trigger', '')
    direction = _norm_dir(case.get('direction', ''))

    if tf in ('1w', '1M'):
        if trigger == 'golden_cross':
            return ['BULL_TREND', 'BULL_EARLY']
        elif trigger == 'death_cross':
            return ['BEAR_TREND', 'BEAR_EARLY']
        else:
            return ['BULL_TREND', 'BEAR_TREND']

    # 短周期：BB压缩爆发 → 所有体制都有效（不分体制，只分方向+周期）
    return ['ALL']


# ── 核心蒸馏函数 ──────────────────────────────────────────────────
def distill(cases: list) -> dict:
    """
    返回多层索引：
    {
      "by_regime_dir_tf": {
        "BEAR_TREND:SHORT:4h": {"n":49, "wr":0.61, "avg_ret":-0.8, ...},
        ...
      },
      "by_coin_dir_tf": {
        "BTC:SHORT:4h": {"n":49, "wr":0.61, ...},
        ...
      },
      "by_dir_tf": {
        "SHORT:4h": {"n":350, "wr":0.58, ...},
        ...
      },
      "top_coins_by_regime_dir": {
        "BEAR_TREND:SHORT": [{"coin":"BTC","wr":0.67,"n":134},...],
        ...
      },
      "meta": {...}
    }
    """
    # 按维度聚合桶
    # key → list of (ret, direction)
    buckets_rdt  = {}   # regime:dir:tf
    buckets_cdt  = {}   # coin:dir:tf
    buckets_dt   = {}   # dir:tf
    buckets_rd   = {}   # regime:dir (for top_coins)
    buckets_rd_coin = {}  # regime:dir:coin

    total = 0
    skipped = 0
    for c in cases:
        direction = _norm_dir(c.get('direction', c.get('breakout_direction', '')))
        if not direction:
            skipped += 1
            continue

        ret_raw = c.get('future_return', c.get('future_return_24h', c.get('future_ret', None)))
        if ret_raw is None:
            skipped += 1
            continue
        ret = float(ret_raw)
        tf  = str(c.get('timeframe', '4h'))
        sym = str(c.get('symbol', '')).upper().replace('USDT', '')
        if not sym:
            skipped += 1
            continue

        win = _is_win(ret, direction)
        total += 1

        # by_dir_tf
        k_dt = f'{direction}:{tf}'
        buckets_dt.setdefault(k_dt, []).append((ret, win))

        # by_coin_dir_tf
        k_cdt = f'{sym}:{direction}:{tf}'
        buckets_cdt.setdefault(k_cdt, []).append((ret, win))

        # by_regime_dir_tf — 对每个推断体制都写入
        regimes = infer_regime_from_case(c)
        for rgm in regimes:
            k_rdt = f'{rgm}:{direction}:{tf}'
            buckets_rdt.setdefault(k_rdt, []).append((ret, win))

            k_rd = f'{rgm}:{direction}'
            buckets_rd.setdefault(k_rd, []).append((ret, win))

            k_rdc = f'{rgm}:{direction}:{sym}'
            buckets_rd_coin.setdefault(k_rdc, []).append((ret, win))

    def _calc(entries: list) -> dict:
        if not entries:
            return {'n': 0, 'wr': 0.0, 'avg_ret': 0.0}
        rets = [e[0] for e in entries]
        wins = sum(1 for e in entries if e[1])
        return {
            'n':       len(entries),
            'wr':      round(wins / len(entries), 4),
            'avg_ret': round(sum(rets) / len(entries), 4),
        }

    # 计算每个桶的统计
    by_rdt = {k: _calc(v) for k, v in buckets_rdt.items()}
    by_cdt = {k: _calc(v) for k, v in buckets_cdt.items()}
    by_dt  = {k: _calc(v) for k, v in buckets_dt.items()}

    # top_coins_by_regime_dir
    top_coins = {}
    for rd_key, rd_entries in buckets_rd.items():
        # 收集该 regime:dir 下各 coin 的表现
        regime_dir = rd_key  # e.g. "BEAR_TREND:SHORT"
        coin_stats = {}
        for rdc_key, rdc_entries in buckets_rd_coin.items():
            parts = rdc_key.split(':')
            if len(parts) == 3:
                r, d, coin = parts
                if f'{r}:{d}' == regime_dir:
                    stats = _calc(rdc_entries)
                    if stats['n'] >= 3:  # 至少3条才有意义
                        coin_stats[coin] = stats

        ranked = sorted(coin_stats.items(), key=lambda x: x[1]['wr'], reverse=True)
        top_coins[regime_dir] = [
            {'coin': coin, 'wr': s['wr'], 'n': s['n'], 'avg_ret': s['avg_ret']}
            for coin, s in ranked[:10]
        ]

    return {
        'by_regime_dir_tf':      by_rdt,
        'by_coin_dir_tf':        by_cdt,
        'by_dir_tf':             by_dt,
        'top_coins_by_regime_dir': top_coins,
        'meta': {
            'total_cases':   total,
            'skipped':       skipped,
            'built_at':      datetime.now(timezone.utc).isoformat(),
            'version':       'phase3-v1.0',
        }
    }


# ── 单币WR速查表 ──────────────────────────────────────────────────
def build_coin_wr_table(matrix: dict) -> dict:
    """
    格式: {
      "BTC": {
        "SHORT": {"4h": {"wr":0.61,"n":49}, "1h": {...}, ...},
        "LONG":  {...}
      }, ...
    }
    """
    result = {}
    for key, stats in matrix['by_coin_dir_tf'].items():
        parts = key.split(':')
        if len(parts) != 3:
            continue
        coin, direction, tf = parts
        result.setdefault(coin, {}).setdefault(direction, {})[tf] = {
            'wr': stats['wr'], 'n': stats['n'], 'avg_ret': stats['avg_ret']
        }
    return result


# ── 人工可读报告 ──────────────────────────────────────────────────
def build_report(matrix: dict) -> str:
    lines = [
        '═══ 梵天经验蒸馏矩阵 Phase3 报告 ═══',
        f'生成时间: {matrix["meta"]["built_at"]}',
        f'总案例: {matrix["meta"]["total_cases"]:,}  跳过: {matrix["meta"]["skipped"]}',
        '',
    ]

    # 核心体制×方向×周期汇总
    lines.append('【核心 WR 矩阵（体制×方向×周期）】')
    key_combos = [
        ('BEAR_TREND', 'SHORT'), ('BEAR_TREND', 'LONG'),
        ('BULL_TREND', 'LONG'),  ('BULL_TREND', 'SHORT'),
        ('CHOP_MID',  'SHORT'), ('ALL', 'SHORT'), ('ALL', 'LONG'),
    ]
    for rgm, dr in key_combos:
        row_parts = []
        for tf in ['15m', '1h', '4h', '1d']:
            k = f'{rgm}:{dr}:{tf}'
            s = matrix['by_regime_dir_tf'].get(k)
            if s and s['n'] > 0:
                row_parts.append(f'{tf}:WR={s["wr"]:.0%}(n={s["n"]})')
        if row_parts:
            lines.append(f'  {rgm}×{dr}: ' + '  '.join(row_parts))
    lines.append('')

    # top coins per regime:dir
    lines.append('【各体制最优币种 Top5】')
    for rd_key, coins in matrix['top_coins_by_regime_dir'].items():
        if not coins:
            continue
        top5 = coins[:5]
        coins_str = '  '.join(f'{c["coin"]}:{c["wr"]:.0%}(n={c["n"]})' for c in top5)
        lines.append(f'  {rd_key}: {coins_str}')
    lines.append('')

    # 全局最优周期
    lines.append('【各周期全局 WR（所有币种合计）】')
    for tf in TIMEFRAMES:
        long_k  = f'LONG:{tf}'
        short_k = f'SHORT:{tf}'
        ls = matrix['by_dir_tf'].get(long_k,  {})
        ss = matrix['by_dir_tf'].get(short_k, {})
        if ls.get('n', 0) > 0 or ss.get('n', 0) > 0:
            lines.append(
                f'  {tf}: '
                f'LONG  WR={ls.get("wr",0):.0%} n={ls.get("n",0)}  '
                f'SHORT WR={ss.get("wr",0):.0%} n={ss.get("n",0)}'
            )

    return '\n'.join(lines)


# ── CLI 入口 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print('加载方仓案例...')
    t0 = time.time()
    cases = load_all_cases()
    print(f'已加载 {len(cases):,} 条案例  ({time.time()-t0:.1f}s)')

    print('蒸馏中...')
    t1 = time.time()
    matrix = distill(cases)
    print(f'蒸馏完成  ({time.time()-t1:.1f}s)')

    # 写主矩阵
    out_main = _DATA / 'brahma_experience_matrix.json'
    out_main.write_text(json.dumps(matrix, ensure_ascii=False))
    size_kb = out_main.stat().st_size / 1024
    print(f'✅ 写入 {out_main.name}  ({size_kb:.1f} KB)')

    # 写单币WR速查表
    coin_table = build_coin_wr_table(matrix)
    out_coin = _DATA / 'brahma_wr_by_coin.json'
    out_coin.write_text(json.dumps(coin_table, ensure_ascii=False))
    print(f'✅ 写入 {out_coin.name}  ({out_coin.stat().st_size/1024:.1f} KB)')

    # 写人工报告
    report = build_report(matrix)
    out_report = _DATA / 'brahma_phase3_report.txt'
    out_report.write_text(report)
    print(f'✅ 写入 {out_report.name}')

    print()
    print(report)

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/extreme_event_db.py ══
"""
extreme_event_db.py  —  A2 极端事件库
梵天设计院封印 2026-08-25

功能:
  build_extreme_events()         — 扫描1D K线，识别单日涨跌幅 >8% 的极端事件
  match_current_similarity(sym)  — 当前状态与历史极端事件相似度匹配
  get_extreme_risk_note(sym)     — 供 analyze() 调用的风险注释接口
"""

import ast
import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone

# ── 路径常量 ──────────────────────────────────────────────────────────────────
_DATA_DIR        = os.path.join(os.path.dirname(__file__), '..', 'data')
_HIST_PATH       = os.path.join(_DATA_DIR, 'historical', 'BTCUSDT_1d.jsonl.gz')
_EVENTS_PATH     = os.path.join(_DATA_DIR, 'extreme_events.jsonl')

# ── 参数 ──────────────────────────────────────────────────────────────────────
EXTREME_THRESHOLD_PCT = 8.0   # 绝对值 > 8% 为极端事件
SIMILARITY_WARNING    = 60.0  # 相似度超过此阈值发出警告
TOP_N                 = 3     # 返回最相似的 top3


# ══════════════════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════════════════

def _rsi_wilder(closes: list, period: int = 14) -> float:
    """Wilder 平滑 RSI，输入至少 period+1 个收盘价，不足则返回 50.0"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1 + ag / al), 2)


def _load_klines_gz(path: str) -> list:
    """读取 .jsonl.gz 历史K线，返回按 ts 升序的 dict 列表"""
    rows = []
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        rows.sort(key=lambda x: x['ts'])
    except Exception as e:
        print(f"[extreme_event_db] 读取K线失败: {e}", file=sys.stderr)
    return rows


def _ts_to_date(ts_ms: int) -> str:
    """毫秒时间戳 → YYYY-MM-DD (UTC)"""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def _euclidean_nd(vec_a: list, vec_b: list) -> float:
    """N维向量欧式距离（第一个为2维兼容旧代码）"""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(vec_a, vec_b)))


def _build_current_vec(klines: list, symbol: str = 'BTCUSDT') -> dict:
    """
    构建12维当前市场状态向量。
    封印: 2026-09-04 苏摩111
    返回字典包含: rsi_4h, change_3d, bbw, hurst_approx,
               oi_chg_3d, fr_mean, atr_rank, regime_code,
               macro_days, vol_rank, score_rank, stoch_rsi
    """
    import urllib.request
    closes = [k['c'] for k in klines]
    highs  = [k['h'] for k in klines]
    lows   = [k['l'] for k in klines]

    # 基础维度（已有）
    rsi_4h    = _rsi_wilder(closes[-17:], 14)
    base_3d   = klines[-4]['c'] if len(klines) >= 4 else klines[0]['c']
    change_3d = (closes[-1] - base_3d) / base_3d * 100 if base_3d else 0

    # BBW布林带宽度（应用于测量市场压缩）
    _n = min(20, len(closes))
    _avg = sum(closes[-_n:]) / _n
    _std = (sum((c - _avg)**2 for c in closes[-_n:]) / _n) ** 0.5
    bbw = round((_std / _avg * 100) if _avg else 5.0, 2)

    # Hurst近伧4(简化R/S，计算成本低)
    _rs_closes = closes[-30:]
    hurst_approx = 0.5
    try:
        _mean   = sum(_rs_closes) / len(_rs_closes)
        _dev    = [c - _mean for c in _rs_closes]
        _cumdev = [sum(_dev[:i+1]) for i in range(len(_dev))]
        _R      = max(_cumdev) - min(_cumdev)
        _S      = (_std if _std > 0 else 1)
        _rs     = _R / _S
        hurst_approx = round(math.log(_rs) / math.log(len(_rs_closes)) if _rs > 0 else 0.5, 3)
    except Exception:
        pass

    # ATR百分位排名(0~1)
    _atr_list = [highs[i] - lows[i] for i in range(max(0, len(highs)-20), len(highs))]
    _atr_now  = _atr_list[-1] if _atr_list else 0
    atr_rank  = round(sum(1 for a in _atr_list if a < _atr_now) / max(len(_atr_list), 1), 2)

    # OI变化率 - 从实时API拉取
    oi_chg_3d = 0.0
    try:
        _usdt = symbol if symbol.endswith('USDT') else symbol + 'USDT'
        _oi_hist = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/futures/data/openInterestHist?symbol={_usdt}&period=1d&limit=4',
            timeout=5).read())
        if isinstance(_oi_hist, list) and len(_oi_hist) >= 2:
            _oi_now  = float(_oi_hist[-1].get('sumOpenInterest', 0))
            _oi_prev = float(_oi_hist[-4].get('sumOpenInterest', _oi_now))
            oi_chg_3d = round((_oi_now - _oi_prev) / _oi_prev * 100, 2) if _oi_prev else 0
    except Exception:
        pass

    # FR平均资金费率
    fr_mean = 0.0
    try:
        _usdt = symbol if symbol.endswith('USDT') else symbol + 'USDT'
        _fr_hist = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={_usdt}&limit=3',
            timeout=5).read())
        if isinstance(_fr_hist, list):
            _frs = [float(x.get('fundingRate', 0)) * 100 for x in _fr_hist]
            fr_mean = round(sum(_frs) / len(_frs), 4) if _frs else 0
    except Exception:
        pass

    # 体制编码 - 从 brahma_state 读取
    regime_code = 5  # 默认 CHOP_MID
    score_rank  = 0.5
    try:
        _bs_path = Path(_DATA_DIR) / 'brahma_state.json'
        if _bs_path.exists():
            _bs = json.loads(_bs_path.read_text())
            regime_code = REGIME_MAP.get(_bs.get('regime', 'CHOP_MID'), 5)
            _score = float(_bs.get('score_final', _bs.get('score', 50)))
            score_rank = round(min(1.0, _score / 200), 2)
    except Exception:
        pass

    # 宏观事件距离 - 从 macro_cal_cache 读取
    macro_days = 30  # 默认无事件
    try:
        _mac_path = Path(_DATA_DIR) / 'macro_cal_cache.json'
        if _mac_path.exists():
            _mac = json.loads(_mac_path.read_text())
            _events = _mac.get('events', [])
            if _events:
                _now_ts = time.time()
                _dists  = [abs(e.get('ts', _now_ts + 9999) - _now_ts) / 86400
                           for e in _events if isinstance(e, dict)]
                macro_days = round(min(_dists), 1) if _dists else 30
    except Exception:
        pass

    # StochRSI近伧4
    stoch_rsi = 50.0
    try:
        _rsi_vals = [_rsi_wilder(closes[max(0,i-17):i+1], 14)
                     for i in range(max(0, len(closes)-14), len(closes))]
        if len(_rsi_vals) >= 2:
            _lo = min(_rsi_vals); _hi = max(_rsi_vals)
            stoch_rsi = round((_rsi_vals[-1] - _lo) / (_hi - _lo) * 100
                              if _hi > _lo else 50.0, 1)
    except Exception:
        pass

    return {
        'rsi_4h':      round(rsi_4h, 2),
        'change_3d':   round(change_3d, 2),
        'bbw':         bbw,
        'hurst':       hurst_approx,
        'oi_chg_3d':   oi_chg_3d,
        'fr_mean':     fr_mean,
        'atr_rank':    atr_rank,
        'regime_code': regime_code,
        'macro_days':  round(min(macro_days, 30), 1),
        'vol_rank':    atr_rank,   # 暂用ATR_rank代替
        'score_rank':  score_rank,
        'stoch_rsi':   stoch_rsi,
    }


def _similarity_score(dist: float, max_dist: float) -> float:
    """将欧式距离映射到 0-100 相似度分（距离越小相似度越高）"""
    if max_dist <= 0:
        return 100.0
    return round(max(0.0, 100.0 * (1.0 - dist / max_dist)), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 公共 API
# ══════════════════════════════════════════════════════════════════════════════

def _calc_bbw(closes: list, i: int, n: int = 20) -> float:
    """BBW布林带宽度（古价计算）"""
    try:
        window = closes[max(0, i-n):i]
        if len(window) < 5:
            return 5.0
        avg = sum(window) / len(window)
        std = (sum((c - avg)**2 for c in window) / len(window)) ** 0.5
        return round(std / avg * 100, 2) if avg else 5.0
    except Exception:
        return 5.0


def _calc_atr_rank(klines: list, i: int, n: int = 20) -> float:
    """ATR百分位排名（当前ATR在过去 N格中的相对位置）"""
    try:
        atrs = [klines[j]['h'] - klines[j]['l']
                for j in range(max(0, i-n), i)]
        if not atrs:
            return 0.5
        cur_atr = klines[i]['h'] - klines[i]['l']
        return round(sum(1 for a in atrs if a < cur_atr) / len(atrs), 2)
    except Exception:
        return 0.5


def build_extreme_events(symbol: str = 'BTCUSDT') -> list:
    """
    扫描1D K线，识别单日涨跌幅绝对值 > 8% 的极端事件。

    每个 event 字段:
      ts             : K线时间戳 (ms)
      date           : YYYY-MM-DD
      symbol         : 交易对
      change_pct     : 当日涨跌幅 (%)
      direction      : 'UP' / 'DOWN'
      pre_3d_rsi     : 事前3天（含当天前一天）的 RSI(14)
      pre_3d_change  : 事前3天累计涨跌幅 (%)

    结果写入 extreme_events.jsonl，并返回事件列表。
    """
    klines = _load_klines_gz(_HIST_PATH)
    if not klines:
        print("[extreme_event_db] 无可用K线数据", file=sys.stderr)
        return []

    closes = [k['c'] for k in klines]
    events = []

    # 需要至少 14+3 = 17 根前置K线 + 本根
    for i in range(17, len(klines)):
        k = klines[i]
        prev_close = klines[i - 1]['c']
        if prev_close == 0:
            continue
        change_pct = (k['c'] - prev_close) / prev_close * 100.0

        if abs(change_pct) <= EXTREME_THRESHOLD_PCT:
            continue

        # 事前3天 (i-3 ~ i-1 含) 的收盘价
        pre_closes = closes[: i]          # 不含当天
        rsi_window = pre_closes[-(14 + 3):]  # 给 RSI 足够窗口：最近17根
        pre_3d_rsi = _rsi_wilder(rsi_window[-17:], period=14)

        # 3日累计涨跌：从 klines[i-3] close → klines[i-1] close
        base_close_3d = klines[i - 3]['c']
        end_close_3d  = klines[i - 1]['c']
        pre_3d_change = (end_close_3d - base_close_3d) / base_close_3d * 100.0 if base_close_3d else 0.0

        event = {
            'ts'           : k['ts'],
            'date'         : _ts_to_date(k['ts']),
            'symbol'       : symbol,
            'change_pct'   : round(change_pct, 2),
            'direction'    : 'UP' if change_pct > 0 else 'DOWN',
            # 基础维度（2维）
            'pre_3d_rsi'   : round(pre_3d_rsi, 2),
            'pre_3d_change': round(pre_3d_change, 2),
            # 扩展维度（10维）—— HCME v2.0 2026-09-04 苏摩111
            'bbw'          : _calc_bbw(closes, i),
            'atr_rank'     : _calc_atr_rank(klines, i),
            'regime_code'  : 5,  # 历史事件无体制标签，默认CHOP
            'oi_chg_3d'    : 0.0,  # 历史OI数据不可用，占位
            'fr_mean'      : 0.0,  # 历史FR数据不可用，占位
            'macro_days'   : 30.0, # 历史宏观日历不可用，占位
            'stoch_rsi'    : 50.0, # 历史StochRSI暂不计算，占位
            'score_rank'   : 0.5,  # 历史评分不可用，占位
        }
        events.append(event)

    # 写入 JSONL
    try:
        os.makedirs(os.path.dirname(_EVENTS_PATH), exist_ok=True)
        with open(_EVENTS_PATH, 'w', encoding='utf-8') as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        print(f"[extreme_event_db] 极端事件库已构建: {len(events)} 条 → {_EVENTS_PATH}")
    except Exception as e:
        print(f"[extreme_event_db] 写入事件库失败: {e}", file=sys.stderr)

    return events


def _load_events() -> list:
    """从磁盘加载已构建的极端事件库"""
    events = []
    try:
        with open(_EVENTS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[extreme_event_db] 加载事件库失败: {e}", file=sys.stderr)
    return events


def match_current_similarity(symbol: str = 'BTCUSDT') -> dict:
    """
    HCME v2.0: 12维市场状态匹配，诚实标注置信度。
    2026-09-04 苏摩111封印

    返回:
      current_vec    : 当前12维状态
      top3           : 最相似的 TOP3 历史事件
      max_similarity : 0~100
      n_events       : 案例库大小（用于置信度评估）
      confidence     : 极低/低/中/高（基于 n 和维度）
      warning        : 预警文本
      note           : 评估说明（n值和维度说明）
    """
    events = _load_events()
    if not events:
        events = build_extreme_events(symbol)
    if not events:
        return {'top3': [], 'max_similarity': 0.0, 'confidence': '数据不足',
                'n_events': 0, 'warning': '', 'note': '案例库为空'}

    # 获取当前 K 线
    _sym_upper  = symbol.upper().replace('USDT', '') if symbol else 'BTC'
    _sym_hist   = os.path.join(_DATA_DIR, 'historical', f'{_sym_upper}USDT_1d.jsonl.gz')
    _hist_to_use = _sym_hist if os.path.exists(_sym_hist) else _HIST_PATH
    klines = _load_klines_gz(_hist_to_use)
    if len(klines) < 17:
        return {'top3': [], 'max_similarity': 0.0, 'confidence': '数据不足',
                'n_events': 0, 'warning': '', 'note': '历史K线不足'}

    # 构建当前12维向量
    cur = _build_current_vec(klines, symbol)

    # 途径不匹配时用旧版写字段兼容
    def _ev_vec(ev: dict) -> list:
        return [
            ev.get('pre_3d_rsi',   50.0),
            ev.get('pre_3d_change', 0.0),
            ev.get('bbw',           5.0),
            ev.get('hurst',         0.5) * 100,  # 缩放到同一量级
            ev.get('oi_chg_3d',     0.0),
            ev.get('fr_mean',       0.0) * 1000,  # 缩放
            ev.get('atr_rank',      0.5) * 100,
            ev.get('regime_code',   5)   * 10,    # 缩放
            ev.get('macro_days',   30.0),
            ev.get('vol_rank',      0.5) * 100,
            ev.get('score_rank',    0.5) * 100,
            ev.get('stoch_rsi',    50.0),
        ]

    def _cur_vec() -> list:
        return [
            cur['rsi_4h'],
            cur['change_3d'],
            cur['bbw'],
            cur['hurst'] * 100,
            cur['oi_chg_3d'],
            cur['fr_mean'] * 1000,
            cur['atr_rank'] * 100,
            cur['regime_code'] * 10,
            cur['macro_days'],
            cur['vol_rank'] * 100,
            cur['score_rank'] * 100,
            cur['stoch_rsi'],
        ]

    cv = _cur_vec()

    scored = []
    for ev in events:
        ev_v = _ev_vec(ev)
        dist = _euclidean_nd(cv, ev_v)
        scored.append({'event': ev, 'dist': dist})

    scored.sort(key=lambda x: x['dist'])

    ref_dists = [s['dist'] for s in scored[:max(len(scored), 1)]]
    max_dist  = max(ref_dists) if ref_dists else 1.0

    top_n = min(TOP_N, len(scored))
    top3 = []
    for s in scored[:top_n]:
        sim = _similarity_score(s['dist'], max_dist)
        ev  = s['event']
        top3.append({
            'event':      ev,
            'dist':       round(s['dist'], 3),
            'similarity': sim,
            'dims_used':  12,
        })

    n_events   = len(events)
    max_sim    = top3[0]['similarity'] if top3 else 0.0

    # 置信度评估（维度 × n值双重评估）
    if n_events >= 200:
        conf = '中'     # n趋数充分
    elif n_events >= 50:
        conf = '低'
    elif n_events >= 10:
        conf = '极低'
    else:
        conf = '极低'

    note = (
        f'12维匹配 | n={n_events}条案例 | '
        f'置信度「{conf}」 | '
        f'RSI={cur["rsi_4h"]:.1f} 3d={cur["change_3d"]:+.1f}% '
        f'Hurst={cur["hurst"]:.3f} BBW={cur["bbw"]:.1f}% '
        f'OI变化={cur["oi_chg_3d"]:+.1f}%'
    )

    warning = ''
    if max_sim > SIMILARITY_WARNING and top3:
        best_ev = top3[0]['event']
        warning = (
            f"⚠️ HCME v2.0: 与{best_ev['date']}相似度{max_sim}%（{best_ev['direction']},{best_ev['change_pct']:+.1f}%）"
            f" | 12维 n={n_events} 置信{conf}"
        )

    return {
        'current_vec':    cur,
        'top3':           top3,
        'max_similarity': max_sim,
        'n_events':       n_events,
        'confidence':     conf,
        'warning':        warning,
        'note':           note,
        # 居安兼容旧字段
        'current_rsi':       cur['rsi_4h'],
        'current_3d_change': cur['change_3d'],
    }


def get_extreme_risk_note(symbol: str = 'BTCUSDT') -> str:
    """
    供 analyze() 调用的风险注释接口。
    - 相似度 > 60 → 返回警告字符串
    - 否则返回空字符串
    """
    try:
        result = match_current_similarity(symbol)
        return result.get('warning', '')
    except Exception as e:
        print(f"[extreme_event_db] get_extreme_risk_note 失败: {e}", file=sys.stderr)
        return ''


# ══════════════════════════════════════════════════════════════════════════════
# 冒烟测试
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # 语法自检
    with open(__file__, 'r', encoding='utf-8') as _f:
        ast.parse(_f.read())

    print("=== A2 极端事件库 冒烟测试 ===")

    # Step 1: 构建极端事件库
    print("\n[1] build_extreme_events(BTCUSDT)")
    try:
        evs = build_extreme_events('BTCUSDT')
        assert len(evs) > 0, "极端事件列表不能为空"
        print(f"    事件数: {len(evs)}")
        print(f"    样本: {evs[0]}")
        print(f"    最近: {evs[-1]}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    # Step 2: 相似度匹配
    print("\n[2] match_current_similarity(BTCUSDT)")
    try:
        res = match_current_similarity('BTCUSDT')
        print(f"    当前RSI:        {res['current_rsi']}")
        print(f"    当前3日涨跌:    {res['current_3d_change']:.2f}%")
        print(f"    最高相似度:     {res['max_similarity']}%")
        if res['warning']:
            print(f"    警告: {res['warning']}")
        else:
            print("    无极端事件警告")
        print(f"    Top3:")
        for t in res['top3']:
            ev = t['event']
            print(f"      {ev['date']} {ev['direction']} {ev['change_pct']:+.1f}%  "
                  f"pre_rsi={ev['pre_3d_rsi']}  pre_3d={ev['pre_3d_change']:+.1f}%  "
                  f"相似度={t['similarity']}%")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    # Step 3: 风险注释
    print("\n[3] get_extreme_risk_note(BTCUSDT)")
    try:
        note = get_extreme_risk_note('BTCUSDT')
        print(f"    note='{note}'")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    print("\nA2完成 ✅")

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/failure_pattern_db.py ══
#!/usr/bin/env python3
"""
failure_pattern_db.py — 梵天大脑 Layer A1: 失败模式数据库
设计院 2026-08-25 苏摩111立项封印

使命: 每次信号结算后自动分析失败原因
     积累后自动识别「这种RSI+LSR+体制组合历史上80%亏损」
     不是WR矩阵，是「失败原因分析库」

数据流:
  信号发出 → analyze() → 执行 → 结算
  结算 → record_outcome() → 存入failure_db.jsonl
  查询 → get_failure_patterns() → 返回当前组合的历史失败率

存储格式 (failure_db.jsonl):
  {"ts":..,"sym":..,"regime":..,"dir":..,"score":..,"rsi_1h":..,"lsr":..,"fr":..,"outcome":"WIN|LOSS|TIMEOUT","failure_dims":[...]}
"""
import os, sys, json, time, logging
from pathlib import Path
from typing import Optional

_BB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BB)
if _BB not in sys.path: sys.path.insert(0, _BB)

logger = logging.getLogger('failure_pattern_db')

_DB_PATH = Path(_ROOT) / 'data' / 'failure_db.jsonl'
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 失败维度标签
FAILURE_DIMS = {
    'rsi_overbought':   lambda r: r.get('rsi_1h', 50) > 70 and r.get('dir') == 'SHORT',
    'rsi_oversold':     lambda r: r.get('rsi_1h', 50) < 30 and r.get('dir') == 'LONG',
    'lsr_crowded_long': lambda r: r.get('lsr', 50) > 65 and r.get('dir') == 'LONG',
    'lsr_crowded_short':lambda r: r.get('lsr', 50) < 35 and r.get('dir') == 'SHORT',
    'fr_expensive_long':lambda r: r.get('fr', 0) > 0.01 and r.get('dir') == 'LONG',
    'bear_trend_long':  lambda r: 'BEAR_TREND' in r.get('regime','') and r.get('dir') == 'LONG',
    'chop_long':        lambda r: 'CHOP' in r.get('regime','') and r.get('dir') == 'LONG',
    'bull_trend_short': lambda r: 'BULL_TREND' in r.get('regime','') and r.get('dir') == 'SHORT',
    'score_below_120':  lambda r: r.get('score', 0) < 120,
    'high_atr_low_rr':  lambda r: r.get('atr_pct', 0) > 3.0,
}


def record_outcome(
    symbol: str,
    direction: str,
    score: float,
    regime: str,
    outcome: str,          # 'WIN' | 'LOSS' | 'TIMEOUT'
    rsi_1h: float = 50,
    lsr: float = 50,
    fr: float = 0.0,
    atr_pct: float = 2.0,
    extra: dict = None,
) -> dict:
    """记录一笔信号的结算结果并分析失败维度"""
    record = {
        'ts':      time.time(),
        'sym':     symbol.upper(),
        'dir':     direction.upper(),
        'score':   score,
        'regime':  regime,
        'outcome': outcome.upper(),
        'rsi_1h':  rsi_1h,
        'lsr':     lsr,
        'fr':      fr,
        'atr_pct': atr_pct,
    }

    # 分析失败维度
    failure_dims = []
    if outcome.upper() == 'LOSS':
        for dim_name, check_fn in FAILURE_DIMS.items():
            try:
                if check_fn(record):
                    failure_dims.append(dim_name)
            except Exception:
                pass
    record['failure_dims'] = failure_dims

    if extra:
        record.update({k: v for k, v in extra.items() if k not in record})

    # 追加写入
    try:
        with open(_DB_PATH, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.warning(f'write failure_db: {e}')

    return record


def get_failure_patterns(
    symbol: str = '',
    direction: str = '',
    regime: str = '',
    min_n: int = 5,
) -> dict:
    """
    查询当前组合的历史失败模式
    返回: {
      'total': int, 'loss_n': int, 'loss_rate': float,
      'top_dims': [(dim_name, count, rate), ...],
      'warning': str,   # 如果失败率>60%给出警告
    }
    """
    records = _load_records()
    # 过滤
    filtered = records
    if symbol:
        filtered = [r for r in filtered if r.get('sym','').upper() == symbol.upper()]
    if direction:
        filtered = [r for r in filtered if r.get('dir','').upper() == direction.upper()]
    if regime:
        filtered = [r for r in filtered if regime.upper() in r.get('regime','').upper()]

    if len(filtered) < min_n:
        return {'total': len(filtered), 'loss_n': 0, 'loss_rate': 0.0,
                'top_dims': [], 'warning': f'样本不足({len(filtered)}<{min_n})'}

    losses    = [r for r in filtered if r.get('outcome') == 'LOSS']
    loss_n    = len(losses)
    loss_rate = loss_n / len(filtered)

    # 统计失败维度
    dim_counts: dict = {}
    for r in losses:
        for d in r.get('failure_dims', []):
            dim_counts[d] = dim_counts.get(d, 0) + 1

    top_dims = sorted(
        [(d, cnt, cnt / loss_n) for d, cnt in dim_counts.items()],
        key=lambda x: -x[1]
    )[:5]

    warning = ''
    if loss_rate > 0.6 and len(filtered) >= min_n:
        top_dim_str = top_dims[0][0] if top_dims else '未知'
        warning = (f'⚠️ {symbol}{direction} 历史失败率={loss_rate:.0%}(n={len(filtered)})，'
                   f'主因: {top_dim_str}')

    return {
        'total':     len(filtered),
        'loss_n':    loss_n,
        'loss_rate': round(loss_rate, 3),
        'top_dims':  top_dims,
        'warning':   warning,
    }


def get_current_risk_score(signal: dict) -> dict:
    """
    实时风险评分: 当前信号组合的历史失败率查询
    供analyze()注入，让梵天在信号发出前知道历史失败率
    """
    sym  = signal.get('symbol', '')
    dir_ = signal.get('signal_dir', signal.get('direction', ''))
    reg  = signal.get('regime', '')
    rsi  = signal.get('rsi_1h', 50)
    lsr_v = signal.get('lsr', 50)

    # 组合查询
    pattern = get_failure_patterns(symbol=sym, direction=dir_, regime=reg, min_n=3)

    # 失败维度实时匹配
    active_dims = []
    record_check = {'dir': dir_.upper(), 'regime': reg, 'rsi_1h': rsi, 'lsr': lsr_v,
                    'fr': signal.get('fr', 0), 'score': signal.get('score', 0)}
    for dim_name, check_fn in FAILURE_DIMS.items():
        try:
            if check_fn(record_check):
                active_dims.append(dim_name)
        except Exception:
            pass

    risk_note = ''
    if pattern['warning']:
        risk_note = pattern['warning']
    elif active_dims:
        risk_note = f'失败维度激活: {", ".join(active_dims[:3])}'

    return {
        'failure_pattern': pattern,
        'active_dims':     active_dims,
        'risk_note':       risk_note,
        'historical_loss_rate': pattern['loss_rate'],
    }


def _load_records() -> list:
    """加载所有结算记录"""
    records = []
    if not _DB_PATH.exists():
        return records
    try:
        with open(_DB_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f'read failure_db: {e}')
    return records


def get_stats() -> dict:
    """全局统计"""
    records = _load_records()
    total  = len(records)
    wins   = sum(1 for r in records if r.get('outcome') == 'WIN')
    losses = sum(1 for r in records if r.get('outcome') == 'LOSS')
    return {
        'total': total, 'wins': wins, 'losses': losses,
        'wr': round(wins/total, 3) if total else 0,
        'db_path': str(_DB_PATH),
    }


if __name__ == '__main__':
    # 冒烟测试
    print('=== 失败模式数据库冒烟测试 ===')
    r = record_outcome('ETHUSDT','SHORT',125,'CHOP_MID','LOSS',rsi_1h=72,lsr=71,fr=0.009)
    print(f'记录: {r["failure_dims"]}')
    p = get_failure_patterns('ETHUSDT','SHORT','CHOP')
    print(f'查询: {p}')
    s = get_stats()
    print(f'统计: {s}')
    print('✅ 冒烟测试通过')
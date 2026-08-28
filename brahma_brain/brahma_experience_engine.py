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

def calc_rsi(closes, period: int = 14) -> float:
    """[2026-08-28 精简] 委托math_utils.calc_rsi — SSOT"""
    from math_utils import calc_rsi as _mu_rsi
    return _mu_rsi(closes, period)
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

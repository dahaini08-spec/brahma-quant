#!/usr/bin/env python3
"""
梵天多时框验证 v2 — 15m主力时框
无前视偏差 · BTC+ETH · 2020-06~2026-08
"""
import pandas as pd
import numpy as np
import json
import os
from pathlib import Path

BASE = Path('/root/.openclaw/workspace/trading-system')
os.makedirs(BASE / 'data/validation', exist_ok=True)

# ── 数学工具 ──────────────────────────────────────────────
def calc_rsi(closes, period=14):
    arr = np.array(closes, dtype=float)
    if len(arr) < period + 1:
        return 50.0
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_g = (avg_g*(period-1) + gains[i]) / period
        avg_l = (avg_l*(period-1) + losses[i]) / period
    if avg_l == 0: return 100.0
    return 100 - (100 / (1 + avg_g/avg_l))

def calc_ema(closes, period):
    arr = np.array(closes, dtype=float)
    if len(arr) < 2: return float(arr[-1])
    k = 2/(period+1)
    v = float(arr[0])
    for c in arr[1:]:
        v = c*k + v*(1-k)
    return v

def calc_bbw(closes, period=20):
    arr = np.array(closes[-period:], dtype=float) if len(closes) >= period else np.array(closes, dtype=float)
    if len(arr) < 3: return 3.0
    mid = arr.mean(); std = arr.std()
    return (4*std/mid)*100 if mid > 0 else 3.0

def calc_atr_pct(highs, lows, closes, period=14):
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    if not trs: return 1.0
    atr = np.mean(trs[-period:])
    return atr / closes[-1] * 100

# ── 数据加载 ──────────────────────────────────────────────
def load_symbol(sym):
    """加载BTC或ETH的15m/1H/4H数据"""
    s = sym.lower()
    df15m = pd.read_parquet(BASE / f'data/historical/{s}usdt/{s}usdt_15m.parquet')
    df1h  = pd.read_parquet(BASE / f'data/historical/{s}usdt/{s}usdt_1h.parquet')
    df4h  = pd.read_parquet(BASE / f'data/historical/{s}usdt/{s}usdt_4h.parquet')
    
    # 加载无前视4H体制标签
    regime_file = BASE / f'data/historical/{s}usdt_regime_nolookahead.parquet'
    df_regime = pd.read_parquet(regime_file)
    
    # 构建4H ts → regime映射（只用可信窗口）
    regime_by_ts = {}
    for _, row in df_regime.iterrows():
        if row.get('reliable', True):
            regime_by_ts[int(row['ts'])] = row['regime']
    
    return df15m, df1h, df4h, regime_by_ts

# ── 多时框评分（15m触发点T时的联合评分）─────────────────
def mtf_score(ts_15m, df15m, df1h, df4h, regime_4h, idx_15m):
    """
    在15m触发时间点，结合1H+4H状态打分
    ts_15m: 触发时间戳（pandas Timestamp）
    返回: (score, direction, reason)
    """
    price = float(df15m['close'].values[idx_15m])
    
    # ── 4H大体制（已知，传入）────────────────────────────
    if not regime_4h or regime_4h == 'UNKNOWN':
        return 0, 'NONE', 'no_regime'
    
    # 宪法死穴
    if regime_4h == 'BEAR_TREND':
        allowed_dir = 'SHORT'
    elif regime_4h == 'BULL_TREND':
        allowed_dir = 'LONG'
    elif regime_4h == 'BEAR_RECOVERY':
        allowed_dir = 'LONG'
    elif regime_4h == 'BEAR_EARLY':
        allowed_dir = 'SHORT'
    elif regime_4h == 'CHOP_MID':
        allowed_dir = 'SHORT'  # 震荡偏空
    else:
        return 0, 'NONE', 'unknown_regime'
    
    # ── 1H确认层 ──────────────────────────────────────────
    # 找当前时间点之前的1H数据
    hist_1h = df1h[df1h.index <= ts_15m].tail(60)
    if len(hist_1h) < 20:
        return 0, 'NONE', 'insufficient_1h'
    
    closes_1h = hist_1h['close'].values.astype(float)
    highs_1h  = hist_1h['high'].values.astype(float)
    lows_1h   = hist_1h['low'].values.astype(float)
    
    rsi_1h  = calc_rsi(closes_1h)
    ema20_1h = calc_ema(closes_1h, 20)
    ema50_1h = calc_ema(closes_1h, min(50, len(closes_1h)))
    
    # 1H与4H方向一致性检查
    if allowed_dir == 'SHORT':
        # 做空要求：1H RSI < 65 且价格 < EMA20_1H（价格结构偏空）
        if rsi_1h > 75:  # 1H超买，做空信号强
            dir_confirm = 2
        elif rsi_1h > 60:
            dir_confirm = 1
        elif rsi_1h < 35:  # 1H超卖，不宜做空
            dir_confirm = -2
        else:
            dir_confirm = 0
        
        # EMA确认
        if price < ema20_1h:
            dir_confirm += 1
        else:
            dir_confirm -= 1
    else:  # LONG
        if rsi_1h < 25:
            dir_confirm = 2
        elif rsi_1h < 40:
            dir_confirm = 1
        elif rsi_1h > 65:
            dir_confirm = -2
        else:
            dir_confirm = 0
        
        if price > ema20_1h:
            dir_confirm += 1
        else:
            dir_confirm -= 1
    
    # 1H强烈反对时放弃
    if dir_confirm <= -2:
        return 0, 'NONE', '1h_conflict'
    
    # ── 15m入场层评分 ──────────────────────────────────────
    hist_15m = df15m.iloc[max(0, idx_15m-96):idx_15m+1]  # 近24H的15m数据
    if len(hist_15m) < 30:
        return 0, 'NONE', 'insufficient_15m'
    
    closes_15m = hist_15m['close'].values.astype(float)
    highs_15m  = hist_15m['high'].values.astype(float)
    lows_15m   = hist_15m['low'].values.astype(float)
    vols_15m   = hist_15m['volume'].values.astype(float)
    
    rsi_15m  = calc_rsi(closes_15m)
    ema20_15m = calc_ema(closes_15m, 20)
    bbw_15m  = calc_bbw(closes_15m)
    atr_pct  = calc_atr_pct(highs_15m, lows_15m, closes_15m)
    
    # 近100根价格位置
    pos_closes = closes_15m[-96:] if len(closes_15m) >= 96 else closes_15m
    prange = pos_closes.max() - pos_closes.min()
    price_pos = (price - pos_closes.min()) / prange if prange > 0 else 0.5
    
    # 量比（近20根）
    avg_v = vols_15m[-21:-1].mean() if len(vols_15m) > 20 else vols_15m.mean()
    vol_ratio = vols_15m[-1] / avg_v if avg_v > 0 else 1.0
    
    score = 0
    
    if allowed_dir == 'SHORT':
        # RSI维度（15m）
        if rsi_15m > 75:   score += 25
        elif rsi_15m > 65: score += 15
        elif rsi_15m > 55: score += 8
        elif rsi_15m < 35: score -= 15
        
        # EMA20位置（15m）
        if price < ema20_15m: score += 20
        else:                 score -= 10
        
        # BBW（压缩状态质量）
        if 0.5 <= bbw_15m <= 1.5: score += 18  # 15m压缩区
        elif bbw_15m < 0.3:        score -= 8   # 过度压缩
        elif bbw_15m > 3.0:        score -= 5   # 已爆发
        
        # 价格位置
        if price_pos > 0.70: score += 12
        elif price_pos < 0.30: score -= 12
        
        # 量比（放量确认）
        if vol_ratio > 1.8: score += 8
        
        # ATR（极端波动期避免入场）
        if atr_pct > 2.0: score -= 10
        
        # 体制加成
        if regime_4h == 'BEAR_TREND':  score += 20
        elif regime_4h == 'BEAR_EARLY': score += 10
        
        # 1H确认加成
        score += dir_confirm * 5
    
    else:  # LONG
        if rsi_15m < 25:   score += 25
        elif rsi_15m < 35: score += 15
        elif rsi_15m < 45: score += 8
        elif rsi_15m > 65: score -= 15
        
        if price > ema20_15m: score += 20
        else:                  score -= 10
        
        if 0.5 <= bbw_15m <= 1.5: score += 18
        elif bbw_15m < 0.3:        score -= 8
        elif bbw_15m > 3.0:        score -= 5
        
        if price_pos < 0.30: score += 12
        elif price_pos > 0.70: score -= 12
        
        if vol_ratio > 1.8: score += 8
        if atr_pct > 2.0:   score -= 10
        
        if regime_4h == 'BULL_TREND':     score += 20
        elif regime_4h == 'BEAR_RECOVERY': score += 10
        
        score += dir_confirm * 5
    
    return score, allowed_dir, f"regime={regime_4h},rsi1h={rsi_1h:.0f},rsi15m={rsi_15m:.0f}"


# ── 15m前向结算 ──────────────────────────────────────────
def settle_15m(df15m, entry_idx, direction, sl_pct, rr, max_bars=32):
    """15m K线上的先到先得结算，最长持仓8H(32根)"""
    tp_pct = sl_pct * rr
    entry_price = float(df15m['close'].values[entry_idx])
    highs = df15m['high'].values
    lows  = df15m['low'].values
    
    if direction == 'SHORT':
        sl_p = entry_price * (1 + sl_pct)
        tp_p = entry_price * (1 - tp_pct)
        for i in range(entry_idx+1, min(entry_idx+max_bars+1, len(df15m))):
            if highs[i] >= sl_p: return 'LOSS', i-entry_idx
            if lows[i]  <= tp_p: return 'WIN',  i-entry_idx
    else:
        sl_p = entry_price * (1 - sl_pct)
        tp_p = entry_price * (1 + tp_pct)
        for i in range(entry_idx+1, min(entry_idx+max_bars+1, len(df15m))):
            if lows[i]  <= sl_p: return 'LOSS', i-entry_idx
            if highs[i] >= tp_p: return 'WIN',  i-entry_idx
    return 'TIMEOUT', max_bars


# ── 主回测函数 ────────────────────────────────────────────
def run_15m_backtest(symbol, sl_pct=0.008, rr=1.5):
    """
    15m主力时框回测
    SL=0.8% (≈1.5×ATR_15m), RR=1.5, 最长持仓32根15m(8H)
    """
    print(f"\n{'='*65}")
    print(f"15m回测: {symbol.upper()} | SL={sl_pct*100:.1f}% RR={rr}")
    print('='*65)
    
    df15m, df1h, df4h, regime_by_ts_4h = load_symbol(symbol)
    
    # 构建4H ts→idx映射（用于查当前4H的体制）
    ts_4h_list = [int(t.timestamp()*1000) for t in df4h.index]
    # 构建按idx的regime查找
    regime_4h_by_idx = {}
    for i, ts in enumerate(ts_4h_list):
        if ts in regime_by_ts_4h:
            regime_4h_by_idx[i] = regime_by_ts_4h[ts]
    
    # 找可信起始索引（4H）
    reliable_4h_indices = sorted(regime_4h_by_idx.keys())
    if not reliable_4h_indices:
        print("❌ 无可信体制标签")
        return None
    min_4h_reliable_ts = ts_4h_list[reliable_4h_indices[0]]
    reliable_start_dt = pd.Timestamp(min_4h_reliable_ts, unit='ms', tz='UTC')
    print(f"可信起始: {reliable_start_dt.strftime('%Y-%m-%d')}")
    
    # 在15m数据中找可信起始索引
    min_15m_reliable_idx = None
    for i, t in enumerate(df15m.index):
        if t >= reliable_start_dt:
            min_15m_reliable_idx = max(i, 200)  # 至少需要200根历史
            break
    if min_15m_reliable_idx is None:
        print("❌ 15m数据不覆盖可信窗口")
        return None
    
    print(f"15m总数据: {len(df15m):,}根 | 可信起始idx: {min_15m_reliable_idx}")
    
    # ── 触发事件生成（15m级别E1~E7）────────────────────────
    closes_15m = df15m['close'].values.astype(float)
    highs_15m  = df15m['high'].values.astype(float)
    lows_15m   = df15m['low'].values.astype(float)
    
    print("生成15m触发事件...")
    triggers = []
    rsi_prev = 50.0
    
    # 预计算4H时间戳数组用于高效查找
    df4h_ts_arr = np.array([t.timestamp() for t in df4h.index])
    df15m_ts_arr = np.array([t.timestamp() for t in df15m.index])
    
    for i in range(min_15m_reliable_idx, len(closes_15m) - 50):
        # 获取当前时间点对应的4H体制（二分查找）
        cur_ts = df15m_ts_arr[i]
        cur_4h_idx = np.searchsorted(df4h_ts_arr, cur_ts, side='right') - 1
        if cur_4h_idx < 0:
            continue
        regime = regime_4h_by_idx.get(cur_4h_idx, None)
        if not regime:
            continue
        
        rsi_cur = calc_rsi(closes_15m[max(0,i-60):i+1])
        
        triggered = False
        
        # E1: RSI_15m从<48穿越≥55（动量启动）
        if rsi_prev < 48 and rsi_cur >= 55 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            triggered = True
        # E2: RSI_15m从>52跌破≤45（动量衰竭）
        if rsi_prev > 52 and rsi_cur <= 45 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            triggered = True
        # E3: RSI_15m超买（>72）做空触发
        if rsi_cur > 72 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            triggered = True
        # E4: RSI_15m超卖（<28）做多触发
        if rsi_cur < 28 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            triggered = True
        # E5: 价格突破近48根（12H）高点（做多）
        if i >= 48 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            h48 = highs_15m[i-48:i].max()
            if closes_15m[i] > h48 * 1.003:
                triggered = True
        # E6: 价格跌破近48根低点（做空）
        if i >= 48 and regime in ('BEAR_TREND','BEAR_EARLY'):
            l48 = lows_15m[i-48:i].min()
            if closes_15m[i] < l48 * 0.997:
                triggered = True
        
        if triggered:
            triggers.append((i, regime, df15m.index[i]))
        
        rsi_prev = rsi_cur
    
    # 去重（同idx只保留一次）
    seen = set()
    unique_triggers = []
    for idx, regime, t in triggers:
        if idx not in seen:
            unique_triggers.append((idx, regime, t))
            seen.add(idx)
    
    print(f"触发事件总数: {len(unique_triggers):,}")
    
    # ── 评分+结算 ─────────────────────────────────────────
    trades = []
    skip_until = 0  # 防止信号重叠（同方向持仓期内不重复入场）
    
    # 预先构建1H时间戳数组
    df1h_ts_arr = np.array([t.timestamp() for t in df1h.index])
    
    for idx, regime_4h, ts_trigger in unique_triggers:
        if idx < skip_until:
            continue
        
        score, direction, reason = mtf_score(
            ts_trigger, df15m, df1h, df4h, regime_4h, idx
        )
        
        if score < 40 or direction == 'NONE':
            continue
        
        # 结算
        result, bars_held = settle_15m(df15m, idx, direction, sl_pct, rr)
        
        cost_pct = 0.0016 if symbol == 'btc' else 0.0020
        if result == 'WIN':
            pnl = sl_pct * rr - cost_pct
        elif result == 'LOSS':
            pnl = -sl_pct - cost_pct
        else:
            pnl = -cost_pct * 0.3
        
        entry_price = float(df15m['close'].values[idx])
        
        trades.append({
            'dt':        str(ts_trigger)[:16],
            'year':      str(ts_trigger)[:4],
            'regime_4h': regime_4h,
            'direction': direction,
            'score':     score,
            'score_bucket': '80+' if score>=80 else ('60-79' if score>=60 else '40-59'),
            'result':    result,
            'bars_held': bars_held,
            'entry_price': round(entry_price, 4),
            'pnl':       round(pnl*100, 3),
        })
        
        # 防重叠：同方向持仓期内不重复入场（最长32根15m）
        skip_until = idx + bars_held
    
    df_t = pd.DataFrame(trades)
    print(f"有效信号数（score≥40）: {len(df_t):,}")
    
    if len(df_t) == 0:
        print("❌ 无有效信号")
        return None
    
    # ── 全局统计 ──────────────────────────────────────────
    total_wr = (df_t['result']=='WIN').mean()
    total_ev = df_t['pnl'].mean()
    total_pnl = df_t['pnl'].sum()
    df_t['cum_pnl'] = df_t['pnl'].cumsum()
    peak = df_t['cum_pnl'].cummax()
    max_dd = (df_t['cum_pnl'] - peak).min()
    
    # 连续亏损
    max_loss_streak = cur_streak = 0
    for r in df_t['result']:
        if r == 'LOSS': cur_streak += 1; max_loss_streak = max(max_loss_streak, cur_streak)
        else: cur_streak = 0
    
    print(f"\n{'─'*65}")
    print(f"全局 | n={len(df_t)} WR={total_wr:.1%} EV={total_ev:+.3f}%/笔")
    print(f"       累计PnL={total_pnl:+.2f}% 最大回撤={max_dd:.2f}% 最长连亏={max_loss_streak}笔")
    
    # ── 体制×方向矩阵 ─────────────────────────────────────
    print(f"\n{'─'*65}")
    print("体制×方向矩阵")
    print('─'*65)
    
    regime_matrix = {}
    for (reg, dir), g in df_t.groupby(['regime_4h','direction']):
        wr = (g['result']=='WIN').mean()
        ev = g['pnl'].mean()
        key = f"{reg}:{dir}"
        regime_matrix[key] = {'n':len(g),'wr':round(wr,4),'ev':round(ev,3)}
        flag = '✅' if wr>=0.55 else ('🟡' if wr>=0.50 else '❌')
        print(f"  {flag} {key:<30} n={len(g):>5} WR={wr:.1%} EV={ev:+.3f}%")
    
    # ── 评分分档 ──────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("评分分档")
    print('─'*65)
    for bucket, g in df_t.groupby('score_bucket'):
        wr = (g['result']=='WIN').mean()
        ev = g['pnl'].mean()
        flag = '✅' if wr>=0.55 else ('🟡' if wr>=0.50 else '❌')
        print(f"  {flag} score {bucket:<10} n={len(g):>5} WR={wr:.1%} EV={ev:+.3f}%")
    
    # ── 年份统计 ──────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("年份统计")
    print('─'*65)
    for year, g in df_t.groupby('year'):
        wr = (g['result']=='WIN').mean()
        ev = g['pnl'].mean()
        flag = '✅' if wr>=0.55 else ('🟡' if wr>=0.50 else '❌')
        print(f"  {flag} {year} n={len(g):>5} WR={wr:.1%} EV={ev:+.3f}%")
    
    # ── 1H层过滤效果分析 ──────────────────────────────────
    print(f"\n{'─'*65}")
    print("结算分布")
    print('─'*65)
    for res, g in df_t.groupby('result'):
        print(f"  {res}: {len(g)} ({len(g)/len(df_t):.1%}) 平均持仓={g['bars_held'].mean():.1f}根15m")
    
    return {
        'symbol': symbol,
        'total_trades': len(df_t),
        'global_wr': round(float(total_wr),4),
        'global_ev': round(float(total_ev),4),
        'max_drawdown': round(float(max_dd),2),
        'max_consec_loss': int(max_loss_streak),
        'total_pnl': round(float(total_pnl),2),
        'regime_matrix': regime_matrix,
    }


# ── 执行 ─────────────────────────────────────────────────
print("梵天多时框验证 v2 — 15m主力时框")
print(f"SL=0.8%, RR=1.5, 最长持仓8H(32根15m)")
print()

all_results = {}
for sym in ['btc', 'eth']:
    r = run_15m_backtest(sym, sl_pct=0.008, rr=1.5)
    if r:
        all_results[sym] = r

# ── 对比1H时框（中间层验证）──────────────────────────────
print(f"\n\n{'#'*65}")
print("附加：1H时框中间层验证（SL=1.2%, RR=1.3）")
print('#'*65)

def run_1h_backtest(symbol, sl_pct=0.012, rr=1.3):
    """1H时框回测，与15m形成完整时框谱系"""
    df15m, df1h, df4h, regime_by_ts_4h = load_symbol(symbol)
    
    ts_4h_list = [int(t.timestamp()*1000) for t in df4h.index]
    regime_4h_by_idx = {}
    for i, ts in enumerate(ts_4h_list):
        if ts in regime_by_ts_4h:
            regime_4h_by_idx[i] = regime_by_ts_4h[ts]
    
    reliable_4h_indices = sorted(regime_4h_by_idx.keys())
    if not reliable_4h_indices: return None
    min_4h_ts = ts_4h_list[reliable_4h_indices[0]]
    reliable_start = pd.Timestamp(min_4h_ts, unit='ms', tz='UTC')
    
    # 找1H可信起始
    min_1h_idx = None
    for i, t in enumerate(df1h.index):
        if t >= reliable_start:
            min_1h_idx = max(i, 100)
            break
    if min_1h_idx is None: return None
    
    closes_1h = df1h['close'].values.astype(float)
    highs_1h  = df1h['high'].values.astype(float)
    lows_1h   = df1h['low'].values.astype(float)
    
    # 预计算4H时间戳数组
    df4h_ts_arr = np.array([t.timestamp() for t in df4h.index])
    df1h_ts_arr = np.array([t.timestamp() for t in df1h.index])
    
    triggers_1h = []
    rsi_prev = 50.0
    
    for i in range(min_1h_idx, len(closes_1h)-30):
        cur_ts = df1h_ts_arr[i]
        cur_4h_idx = np.searchsorted(df4h_ts_arr, cur_ts, side='right') - 1
        if cur_4h_idx < 0: continue
        regime = regime_4h_by_idx.get(cur_4h_idx)
        if not regime: continue
        
        rsi_cur = calc_rsi(closes_1h[max(0,i-50):i+1])
        
        triggered = False
        if rsi_prev < 45 and rsi_cur >= 55 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            triggered = True
        if rsi_prev > 55 and rsi_cur <= 45 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            triggered = True
        if rsi_cur > 70 and regime in ('BEAR_TREND','BEAR_EARLY'):
            triggered = True
        if rsi_cur < 30 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            triggered = True
        
        if triggered:
            triggers_1h.append((i, regime))
        rsi_prev = rsi_cur
    
    seen = set(); unique = []
    for idx, reg in triggers_1h:
        if idx not in seen:
            unique.append((idx, reg)); seen.add(idx)
    
    trades = []
    skip_until = 0
    for idx, regime_4h in unique:
        if idx < skip_until: continue
        
        closes = closes_1h[max(0,idx-60):idx+1]
        ema20 = calc_ema(closes, 20)
        price = closes[-1]
        rsi = calc_rsi(closes)
        bbw = calc_bbw(closes)
        
        score = 0
        if regime_4h in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            direction = 'SHORT'
            if rsi > 65: score += 25
            elif rsi > 55: score += 15
            elif rsi < 35: score -= 15
            if price < ema20: score += 20
            else: score -= 10
            if 0.8 <= bbw <= 2.0: score += 18
            if regime_4h == 'BEAR_TREND': score += 20
            elif regime_4h == 'BEAR_EARLY': score += 10
        else:
            direction = 'LONG'
            if rsi < 35: score += 25
            elif rsi < 45: score += 15
            elif rsi > 65: score -= 15
            if price > ema20: score += 20
            else: score -= 10
            if 0.8 <= bbw <= 2.0: score += 18
            if regime_4h == 'BULL_TREND': score += 20
            elif regime_4h == 'BEAR_RECOVERY': score += 10
        
        if score < 35 or direction == 'NONE': continue
        if regime_4h == 'BEAR_TREND' and direction == 'LONG': continue
        
        tp_pct = sl_pct * rr
        ep = float(df1h['close'].values[idx])
        result = 'TIMEOUT'; bars = 24
        h1h = df1h['high'].values; l1h = df1h['low'].values
        if direction == 'SHORT':
            sl_p = ep*(1+sl_pct); tp_p = ep*(1-tp_pct)
            for j in range(idx+1, min(idx+25, len(df1h))):
                if h1h[j] >= sl_p: result='LOSS'; bars=j-idx; break
                if l1h[j] <= tp_p: result='WIN';  bars=j-idx; break
        else:
            sl_p = ep*(1-sl_pct); tp_p = ep*(1+tp_pct)
            for j in range(idx+1, min(idx+25, len(df1h))):
                if l1h[j] <= sl_p: result='LOSS'; bars=j-idx; break
                if h1h[j] >= tp_p: result='WIN';  bars=j-idx; break
        
        cost = 0.0016 if symbol=='btc' else 0.0020
        if result=='WIN': pnl = sl_pct*rr - cost
        elif result=='LOSS': pnl = -sl_pct - cost
        else: pnl = -cost*0.3
        
        trades.append({'regime':regime_4h,'direction':direction,'result':result,
                       'pnl':round(pnl*100,3),'year':str(df1h.index[idx])[:4]})
        skip_until = idx + bars
    
    if not trades: return None
    df_t2 = pd.DataFrame(trades)
    wr = (df_t2['result']=='WIN').mean()
    ev = df_t2['pnl'].mean()
    pnl_sum = df_t2['pnl'].sum()
    print(f"\n{symbol.upper()} 1H: n={len(df_t2)} WR={wr:.1%} EV={ev:+.3f}%/笔 累计={pnl_sum:+.2f}%")
    
    for (reg,dir), g in df_t2.groupby(['regime','direction']):
        wr2 = (g['result']=='WIN').mean()
        ev2 = g['pnl'].mean()
        flag = '✅' if wr2>=0.55 else ('🟡' if wr2>=0.50 else '❌')
        print(f"  {flag} {reg}:{dir} n={len(g)} WR={wr2:.1%} EV={ev2:+.3f}%")
    
    return {'symbol':symbol,'total':len(df_t2),'wr':round(float(wr),4),'ev':round(float(ev),4)}

r1h_results = {}
for sym in ['btc','eth']:
    r = run_1h_backtest(sym)
    if r: r1h_results[sym] = r

# ── 保存 ─────────────────────────────────────────────────
report = {'15m': all_results, '1h': r1h_results}
with open(BASE/'data/validation/phase_mtf_report.json','w',encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\n\n{'='*65}")
print("🏛️ 梵天多时框验证 · 完整时框谱系汇总")
print('='*65)
print(f"\n{'时框':<8} {'标的':<8} {'信号数':>8} {'WR':>8} {'EV/笔':>10} {'累计PnL':>10}")
print('─'*65)
for sym, r in all_results.items():
    print(f"  15m    {sym.upper():<6} {r['total_trades']:>8,} {r['global_wr']:>8.1%} {r['global_ev']:>+10.3f}% {r['total_pnl']:>+10.2f}%")
for sym, r in r1h_results.items():
    print(f"  1H     {sym.upper():<6} {r['total']:>8,} {r['wr']:>8.1%} {r['ev']:>+10.3f}%")

print(f"\n✅ 报告已保存: data/validation/phase_mtf_report.json")
print("多时框验证完成")

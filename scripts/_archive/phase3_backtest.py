#!/usr/bin/env python3
"""
梵天Layer2系统回测 · 阶段3
无前视偏差 · 精简版评分 · BTC+ETH全量
"""
import pandas as pd
import numpy as np
import json
import os
import math
from pathlib import Path

BASE = Path('/root/.openclaw/workspace/trading-system')
os.makedirs(BASE / 'data/validation', exist_ok=True)

# ── 数学工具 ──────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))

def calc_ema(closes, period):
    if len(closes) < 2:
        return closes[-1]
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_bbw(closes, period=20):
    if len(closes) < period:
        return 3.0
    arr = np.array(closes[-period:])
    mid = arr.mean()
    std = arr.std()
    if mid == 0:
        return 3.0
    return (4 * std / mid) * 100

def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if not trs:
        return closes[-1] * 0.01
    return np.mean(trs[-period:])

# ── 精简版评分（可本地计算，无API依赖） ──────────────────
def simplified_score(df_slice, regime):
    """
    精简版评分（去除实时API依赖）
    保留维度：RSI多周期 / EMA排列 / BBW / ATR / 价格位置
    返回：(score, direction)
    """
    closes = df_slice['close'].values
    highs = df_slice['high'].values
    lows = df_slice['low'].values
    
    if len(closes) < 30:
        return 0, 'NONE'
    
    rsi_4h = calc_rsi(closes[-50:])
    rsi_1h_proxy = calc_rsi(closes[-20:])  # 用近期4H代理1H RSI趋势
    
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, min(50, len(closes)))
    price = closes[-1]
    
    bbw = calc_bbw(closes)
    atr = calc_atr(highs, lows, closes)
    atr_pct = atr / price * 100
    
    # 价格位置（近100根）
    recent = closes[-100:] if len(closes) >= 100 else closes
    price_pos = (price - recent.min()) / (recent.max() - recent.min() + 1e-9)
    
    score = 0
    direction = 'NONE'
    
    # ── 做空评分（BEAR/CHOP体制）──
    if regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        direction = 'SHORT'
        
        # RSI维度（最重要）
        if rsi_4h > 70:
            score += 30  # 超买，做空强信号
        elif rsi_4h > 60:
            score += 20
        elif rsi_4h > 50:
            score += 10
        elif rsi_4h < 35:
            score -= 20  # 超卖，不宜做空
        
        # EMA排列（趋势确认）
        if price < ema20 < ema50:
            score += 25  # 空头EMA排列
        elif price < ema20:
            score += 15
        elif price > ema50:
            score -= 15  # 价格在EMA50上方，空单风险
        
        # BBW压缩（信号质量）
        if 1.0 <= bbw <= 2.5:
            score += 20  # 适度压缩，信号可靠
        elif bbw < 0.5:
            score -= 10  # 过度压缩，假突破风险
        elif bbw > 5.0:
            score -= 5   # 已经高波动，不适合入场
        
        # 价格位置
        if price_pos > 0.75:
            score += 15  # 在高位，做空有利
        elif price_pos < 0.25:
            score -= 15  # 在低位，做空不利
        
        # 体制调整
        if regime == 'BEAR_TREND':
            score += 20  # BEAR体制做空加成
        elif regime == 'BEAR_EARLY':
            score += 10
        # CHOP_MID: 0
    
    # ── 做多评分（BULL/BEAR_RECOVERY体制）──
    elif regime in ('BULL_TREND', 'BEAR_RECOVERY'):
        direction = 'LONG'
        
        # RSI维度
        if rsi_4h < 30:
            score += 30  # 超卖，做多强信号
        elif rsi_4h < 40:
            score += 20
        elif rsi_4h < 50:
            score += 10
        elif rsi_4h > 70:
            score -= 20  # 超买，不宜做多
        
        # EMA排列
        if price > ema20 > ema50:
            score += 25  # 多头EMA排列
        elif price > ema20:
            score += 15
        elif price < ema50:
            score -= 15
        
        # BBW
        if 1.0 <= bbw <= 2.5:
            score += 20
        elif bbw < 0.5:
            score -= 10
        elif bbw > 5.0:
            score -= 5
        
        # 价格位置
        if price_pos < 0.25:
            score += 15  # 在低位，做多有利
        elif price_pos > 0.75:
            score -= 15
        
        # 体制调整
        if regime == 'BULL_TREND':
            score += 20
        elif regime == 'BEAR_RECOVERY':
            score += 10
    
    return score, direction


# ── 触发事件模拟（E1~E4，基于4H数据） ──────────────────
def detect_trigger_events(df_4h, regime_map, min_idx=50):
    """
    模拟触发事件：
    E1: RSI从<50穿越到≥55（简化版）
    E2: RSI从>60跌破55
    E3: 价格突破48H高（12根4H）
    E4: 价格跌破48H低
    返回：触发时间点列表 [(idx, event_type), ...]
    """
    closes = df_4h['close'].values
    highs = df_4h['high'].values
    lows = df_4h['low'].values
    
    triggers = []
    rsi_prev = 50.0
    
    for i in range(min_idx, len(closes)):
        regime = regime_map.get(i, 'UNKNOWN')
        if regime == 'UNKNOWN':
            continue
        
        rsi_cur = calc_rsi(closes[max(0,i-50):i+1])
        
        # E1: RSI上穿55（潜在做多触发）
        if rsi_prev < 50 and rsi_cur >= 55 and regime in ('BULL_TREND', 'BEAR_RECOVERY'):
            triggers.append((i, 'E1_RSI_UP'))
        
        # E2: RSI下穿55（潜在做空触发）
        if rsi_prev > 60 and rsi_cur <= 55 and regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
            triggers.append((i, 'E2_RSI_DOWN'))
        
        # E3/E4: 价格突破48H高低（12根4H）
        if i >= 12:
            h48 = highs[i-12:i].max()
            l48 = lows[i-12:i].min()
            if closes[i] > h48 * 1.005 and regime in ('BULL_TREND', 'BEAR_RECOVERY'):
                triggers.append((i, 'E3_BREAK_HIGH'))
            if closes[i] < l48 * 0.995 and regime in ('BEAR_TREND', 'BEAR_EARLY'):
                triggers.append((i, 'E4_BREAK_LOW'))
        
        rsi_prev = rsi_cur
    
    # 去重：同一index只保留一个
    seen = set()
    unique_triggers = []
    for idx, ev in triggers:
        if idx not in seen:
            unique_triggers.append((idx, ev))
            seen.add(idx)
    
    return unique_triggers


# ── 前向结算 ─────────────────────────────────────────────
def forward_settle(df_4h, entry_idx, direction, sl_pct=0.020, tp_pct=None, rr=1.0, max_bars=48):
    """
    先到先得：TP或SL先触发算胜负
    tp_pct: None → 用 sl_pct * rr
    返回: 'WIN' / 'LOSS' / 'TIMEOUT'
    """
    if tp_pct is None:
        tp_pct = sl_pct * rr
    
    entry_price = df_4h['close'].values[entry_idx]
    highs = df_4h['high'].values
    lows = df_4h['low'].values
    
    if direction == 'SHORT':
        sl_price = entry_price * (1 + sl_pct)
        tp_price = entry_price * (1 - tp_pct)
        for i in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(df_4h))):
            if highs[i] >= sl_price:
                return 'LOSS', i - entry_idx
            if lows[i] <= tp_price:
                return 'WIN', i - entry_idx
    else:  # LONG
        sl_price = entry_price * (1 - sl_pct)
        tp_price = entry_price * (1 + tp_pct)
        for i in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(df_4h))):
            if lows[i] <= sl_price:
                return 'LOSS', i - entry_idx
            if highs[i] >= tp_price:
                return 'WIN', i - entry_idx
    
    return 'TIMEOUT', max_bars


# ── 主回测循环 ────────────────────────────────────────────
def run_backtest(symbol, sl_pct=0.020, rr=1.0):
    print(f"\n{'='*60}")
    print(f"回测标的: {symbol.upper()} | SL={sl_pct*100:.1f}% RR={rr:.1f}")
    print('='*60)
    
    # 加载数据
    sym_lower = symbol.lower()
    df4h = pd.read_parquet(BASE / f'data/historical/{sym_lower}usdt/{sym_lower}usdt_4h.parquet')
    
    # 加载无前视体制标签
    regime_file = BASE / f'data/historical/{sym_lower}usdt_regime_nolookahead.parquet'
    if not regime_file.exists():
        print(f"❌ 体制标签文件不存在: {regime_file}")
        return None
    
    df_regime = pd.read_parquet(regime_file)
    
    # 对齐体制标签到4H K线
    regime_by_ts = {}
    for _, row in df_regime.iterrows():
        regime_by_ts[row['ts']] = (row['regime'], row.get('reliable', True))
    
    # 构建idx→regime映射（只用可信窗口）
    regime_map = {}
    ts_list = [int(t.timestamp() * 1000) for t in df4h.index]
    reliable_start_ts = None
    
    for i, ts in enumerate(ts_list):
        if ts in regime_by_ts:
            regime, reliable = regime_by_ts[ts]
            if reliable:
                regime_map[i] = regime
                if reliable_start_ts is None:
                    reliable_start_ts = ts
    
    reliable_start_dt = pd.Timestamp(reliable_start_ts, unit='ms', tz='UTC') if reliable_start_ts else None
    print(f"可信体制窗口起始: {reliable_start_dt}")
    print(f"可信体制标签数: {len(regime_map)}")
    
    # 找可信起始索引
    min_reliable_idx = min(regime_map.keys()) if regime_map else 200
    
    # 生成触发事件
    print(f"生成触发事件...")
    triggers = detect_trigger_events(df4h, regime_map, min_idx=min_reliable_idx)
    print(f"触发事件总数: {len(triggers)}")
    
    # 回测每个触发事件
    trades = []
    for trigger_idx, event_type in triggers:
        regime = regime_map.get(trigger_idx, 'UNKNOWN')
        if regime == 'UNKNOWN':
            continue
        
        # 用T时刻之前的数据评分
        df_slice = df4h.iloc[max(0, trigger_idx-100):trigger_idx+1]
        if len(df_slice) < 30:
            continue
        
        score, direction = simplified_score(df_slice, regime)
        
        # 过滤低分信号
        if score < 50:
            continue
        if direction == 'NONE':
            continue
        
        # 体制死穴过滤（宪法）
        if regime == 'BEAR_TREND' and direction == 'LONG':
            continue  # BEAR_TREND_LONG封禁
        
        # 前向结算
        result, bars_held = forward_settle(df4h, trigger_idx, direction, sl_pct=sl_pct, rr=rr)
        
        entry_price = df4h['close'].values[trigger_idx]
        entry_dt = df4h.index[trigger_idx]
        
        # 计算实际盈亏（扣成本）
        cost_pct = 0.0016 if symbol == 'btc' else 0.0020  # 双边成本
        if result == 'WIN':
            pnl = sl_pct * rr - cost_pct
        elif result == 'LOSS':
            pnl = -sl_pct - cost_pct
        else:  # TIMEOUT
            pnl = -cost_pct * 0.5
        
        trades.append({
            'dt': str(entry_dt)[:10],
            'ts': trigger_idx,
            'event': event_type,
            'regime': regime,
            'direction': direction,
            'score': score,
            'score_bucket': '120+' if score >= 120 else ('90-119' if score >= 90 else '50-89'),
            'result': result,
            'bars_held': bars_held,
            'entry_price': round(float(entry_price), 2),
            'pnl': round(pnl * 100, 3),
        })
    
    df_trades = pd.DataFrame(trades)
    print(f"\n总信号数: {len(df_trades)}")
    
    if len(df_trades) == 0:
        print("❌ 无有效信号")
        return None
    
    # ── 分层统计 ─────────────────────────────────────────
    print(f"\n{'─'*50}")
    print("按 体制×方向 分层统计（WR / EV / n）")
    print('─'*50)
    
    results_matrix = {}
    
    for (regime, direction), group in df_trades.groupby(['regime', 'direction']):
        wins = (group['result'] == 'WIN').sum()
        total = len(group)
        wr = wins / total
        ev = group['pnl'].mean()
        
        key = f"{regime}:{direction}"
        results_matrix[key] = {
            'n': total,
            'wr': round(wr, 4),
            'ev_pct': round(ev, 3),
            'win': int(wins),
            'loss': int((group['result'] == 'LOSS').sum()),
            'timeout': int((group['result'] == 'TIMEOUT').sum()),
        }
        
        flag = '✅' if wr >= 0.52 else ('⚠️' if wr >= 0.48 else '❌')
        print(f"  {flag} {key}: n={total} WR={wr:.1%} EV={ev:+.3f}%")
    
    # 全局统计
    total_wr = (df_trades['result'] == 'WIN').mean()
    total_ev = df_trades['pnl'].mean()
    print(f"\n全局: n={len(df_trades)} WR={total_wr:.1%} EV={total_ev:+.3f}%/笔")
    
    # 按分数分档
    print(f"\n{'─'*50}")
    print("按评分分档统计")
    print('─'*50)
    for bucket, group in df_trades.groupby('score_bucket'):
        wr = (group['result'] == 'WIN').mean()
        ev = group['pnl'].mean()
        flag = '✅' if wr >= 0.52 else '❌'
        print(f"  {flag} score {bucket}: n={len(group)} WR={wr:.1%} EV={ev:+.3f}%")
    
    # 按年份统计
    df_trades['year'] = df_trades['dt'].str[:4]
    print(f"\n{'─'*50}")
    print("按年份统计")
    print('─'*50)
    for year, group in df_trades.groupby('year'):
        wr = (group['result'] == 'WIN').mean()
        ev = group['pnl'].mean()
        flag = '✅' if wr >= 0.52 else '❌'
        print(f"  {flag} {year}: n={len(group)} WR={wr:.1%} EV={ev:+.3f}%")
    
    # 最大连续亏损
    results_seq = df_trades['result'].tolist()
    max_consec_loss = 0
    cur_loss = 0
    for r in results_seq:
        if r == 'LOSS':
            cur_loss += 1
            max_consec_loss = max(max_consec_loss, cur_loss)
        else:
            cur_loss = 0
    print(f"\n最大连续亏损笔数: {max_consec_loss}")
    
    # 月度权益曲线（累计PnL）
    df_trades['cum_pnl'] = df_trades['pnl'].cumsum()
    peak = df_trades['cum_pnl'].cummax()
    drawdown = (df_trades['cum_pnl'] - peak).min()
    total_pnl = df_trades['pnl'].sum()
    print(f"累计PnL: {total_pnl:+.2f}%")
    print(f"最大回撤: {drawdown:.2f}%")
    
    return {
        'symbol': symbol,
        'total_trades': len(df_trades),
        'global_wr': round(float(total_wr), 4),
        'global_ev': round(float(total_ev), 4),
        'max_drawdown': round(float(drawdown), 2),
        'max_consec_loss': int(max_consec_loss),
        'total_pnl': round(float(total_pnl), 2),
        'regime_matrix': results_matrix,
        'trades_sample': trades[:5],
    }


# ── 执行回测 ──────────────────────────────────────────────
all_results = {}

for sym in ['btc', 'eth']:
    result = run_backtest(sym, sl_pct=0.020, rr=1.0)
    if result:
        all_results[sym] = result

# ── 综合报告 ──────────────────────────────────────────────
print(f"\n{'='*60}")
print("🏛️ 梵天Layer2系统回测 · 综合报告")
print('='*60)

for sym, r in all_results.items():
    print(f"\n{sym.upper()}:")
    print(f"  总信号: {r['total_trades']}  全局WR: {r['global_wr']:.1%}  EV: {r['global_ev']:+.3f}%/笔")
    print(f"  累计PnL: {r['total_pnl']:+.2f}%  最大回撤: {r['max_drawdown']:.2f}%  最长连亏: {r['max_consec_loss']}笔")
    print()
    print("  体制矩阵:")
    for k, v in r['regime_matrix'].items():
        flag = '✅' if v['wr'] >= 0.52 else ('⚠️' if v['wr'] >= 0.48 else '❌')
        print(f"    {flag} {k}: n={v['n']} WR={v['wr']:.1%} EV={v['ev_pct']:+.3f}%")

# 保存报告
report_path = BASE / 'data/validation/phase3_backtest_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)
print(f"\n✅ 报告已保存: {report_path}")
print("阶段3完成")

#!/usr/bin/env python3
"""
梵天阶段4：评分维度贡献度分析
方法：Leave-One-Out近似Shapley值
"""
import pandas as pd
import numpy as np
import json
import os
from pathlib import Path

BASE = Path('/root/.openclaw/workspace/trading-system')
os.makedirs(BASE / 'data/validation', exist_ok=True)

# ── 数学工具（复用阶段3）────────────────────────────────
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
        return float(closes[-1])
    k = 2 / (period + 1)
    ema = float(closes[0])
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_bbw(closes, period=20):
    if len(closes) < period:
        return 3.0
    arr = np.array(closes[-period:], dtype=float)
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
        return float(closes[-1]) * 0.01
    return float(np.mean(trs[-period:]))

def calc_hurst(closes, lags=None):
    """Hurst指数近似（R/S方法）"""
    if lags is None:
        lags = [4, 8, 16, 32]
    if len(closes) < 40:
        return 0.5
    rs_list = []
    for lag in lags:
        if len(closes) < lag * 2:
            continue
        sub = closes[-lag*2:]
        mean = np.mean(sub)
        deviations = np.cumsum(sub - mean)
        r = np.max(deviations) - np.min(deviations)
        s = np.std(sub)
        if s > 0:
            rs_list.append((lag, r/s))
    if len(rs_list) < 2:
        return 0.5
    log_lags = np.log([x[0] for x in rs_list])
    log_rs = np.log([x[1] for x in rs_list])
    hurst = np.polyfit(log_lags, log_rs, 1)[0]
    return float(np.clip(hurst, 0.1, 0.9))

def calc_volume_ratio(vols, period=20):
    if len(vols) < period + 1:
        return 1.0
    avg = np.mean(vols[-period-1:-1])
    if avg == 0:
        return 1.0
    return float(vols[-1] / avg)

# ── 全量维度计算 ──────────────────────────────────────────
def compute_all_dims(df_slice):
    """计算所有可本地验证的维度，返回dict"""
    closes = df_slice['close'].values.astype(float)
    highs  = df_slice['high'].values.astype(float)
    lows   = df_slice['low'].values.astype(float)
    vols   = df_slice['volume'].values.astype(float)
    price  = closes[-1]
    
    if len(closes) < 30:
        return None
    
    dims = {}
    
    # D1: RSI_4H
    dims['D1_RSI_4H'] = calc_rsi(closes[-50:])
    # D2: RSI_1H_proxy（最近20根4H的RSI，代理1H趋势）
    dims['D2_RSI_1H_proxy'] = calc_rsi(closes[-20:])
    # D3: EMA20位置（价格/EMA20 - 1）
    ema20 = calc_ema(closes, 20)
    dims['D3_EMA20_pos'] = (price - ema20) / ema20 * 100
    # D4: EMA50位置
    ema50 = calc_ema(closes, min(50, len(closes)))
    dims['D4_EMA50_pos'] = (price - ema50) / ema50 * 100
    # D5: EMA排列得分（多头=+1, 混合=0, 空头=-1）
    if price > ema20 > ema50:
        dims['D5_EMA_align'] = 1.0
    elif price < ema20 < ema50:
        dims['D5_EMA_align'] = -1.0
    else:
        dims['D5_EMA_align'] = 0.0
    # D6: BBW
    dims['D6_BBW'] = calc_bbw(closes)
    # D7: ATR%
    atr = calc_atr(highs, lows, closes)
    dims['D7_ATR_pct'] = atr / price * 100
    # D8: 价格位置（近100根）
    recent = closes[-100:] if len(closes) >= 100 else closes
    prange = recent.max() - recent.min()
    dims['D8_price_pos'] = float((price - recent.min()) / prange) if prange > 0 else 0.5
    # D9: 量比
    dims['D9_vol_ratio'] = calc_volume_ratio(vols)
    # D10: Hurst指数
    dims['D10_hurst'] = calc_hurst(closes)
    # D11: 价格动量（近12根收益率）
    if len(closes) >= 13:
        dims['D11_momentum_12'] = float((closes[-1] - closes[-13]) / closes[-13] * 100)
    else:
        dims['D11_momentum_12'] = 0.0
    # D12: 近期波动率（ATR近20根）
    dims['D12_vol_recent'] = calc_atr(highs[-20:], lows[-20:], closes[-20:]) / price * 100
    
    return dims

# ── 基于维度的评分函数（模块化，方便LOO）────────────────
def score_with_dims(dims, regime, excluded_dim=None):
    """
    基于已计算的维度给分，可排除某个维度（LOO）
    返回(score, direction)
    """
    if dims is None:
        return 0, 'NONE'
    
    score = 0
    direction = 'NONE'
    
    def use(dim_name, value):
        if excluded_dim and dim_name == excluded_dim:
            return 0
        return value
    
    rsi = use('D1_RSI_4H', dims.get('D1_RSI_4H', 50))
    ema_align = use('D5_EMA_align', dims.get('D5_EMA_align', 0))
    ema20_pos = use('D3_EMA20_pos', dims.get('D3_EMA20_pos', 0))
    ema50_pos = use('D4_EMA50_pos', dims.get('D4_EMA50_pos', 0))
    bbw = use('D6_BBW', dims.get('D6_BBW', 3))
    price_pos = use('D8_price_pos', dims.get('D8_price_pos', 0.5))
    vol_ratio = use('D9_vol_ratio', dims.get('D9_vol_ratio', 1))
    hurst = use('D10_hurst', dims.get('D10_hurst', 0.5))
    momentum = use('D11_momentum_12', dims.get('D11_momentum_12', 0))
    
    if regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        direction = 'SHORT'
        # RSI维度
        if rsi > 70:   score += 30
        elif rsi > 60: score += 20
        elif rsi > 50: score += 10
        elif rsi < 35: score -= 20
        # EMA排列
        if ema_align == -1.0:  score += 25
        elif ema20_pos < 0:    score += 15
        elif ema50_pos > 0:    score -= 15
        # BBW
        if 1.0 <= bbw <= 2.5:  score += 20
        elif bbw < 0.5:        score -= 10
        elif bbw > 5.0:        score -= 5
        # 价格位置
        if price_pos > 0.75:   score += 15
        elif price_pos < 0.25: score -= 15
        # Hurst（趋势性确认）
        if hurst > 0.6:        score += 8
        elif hurst < 0.4:      score -= 5
        # 量比（放量确认）
        if vol_ratio > 1.5:    score += 5
        # 动量（负动量做空加成）
        if momentum < -2:      score += 8
        elif momentum > 3:     score -= 8
        # 体制加成
        if regime == 'BEAR_TREND': score += 20
        elif regime == 'BEAR_EARLY': score += 10
    
    elif regime in ('BULL_TREND', 'BEAR_RECOVERY'):
        direction = 'LONG'
        if rsi < 30:   score += 30
        elif rsi < 40: score += 20
        elif rsi < 50: score += 10
        elif rsi > 70: score -= 20
        if ema_align == 1.0:   score += 25
        elif ema20_pos > 0:    score += 15
        elif ema50_pos < 0:    score -= 15
        if 1.0 <= bbw <= 2.5:  score += 20
        elif bbw < 0.5:        score -= 10
        elif bbw > 5.0:        score -= 5
        if price_pos < 0.25:   score += 15
        elif price_pos > 0.75: score -= 15
        if hurst > 0.6:        score += 8
        elif hurst < 0.4:      score -= 5
        if vol_ratio > 1.5:    score += 5
        if momentum > 2:       score += 8
        elif momentum < -3:    score -= 8
        if regime == 'BULL_TREND': score += 20
        elif regime == 'BEAR_RECOVERY': score += 10
    
    return score, direction

# ── 前向结算（复用阶段3逻辑）────────────────────────────
def forward_settle(df_4h, entry_idx, direction, sl_pct=0.020, rr=1.0, max_bars=48):
    tp_pct = sl_pct * rr
    entry_price = float(df_4h['close'].values[entry_idx])
    highs = df_4h['high'].values
    lows  = df_4h['low'].values
    if direction == 'SHORT':
        sl_price = entry_price * (1 + sl_pct)
        tp_price = entry_price * (1 - tp_pct)
        for i in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(df_4h))):
            if highs[i] >= sl_price: return 'LOSS'
            if lows[i]  <= tp_price: return 'WIN'
    else:
        sl_price = entry_price * (1 - sl_pct)
        tp_price = entry_price * (1 + tp_pct)
        for i in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(df_4h))):
            if lows[i]  <= sl_price: return 'LOSS'
            if highs[i] >= tp_price: return 'WIN'
    return 'TIMEOUT'

# ── 主分析流程 ────────────────────────────────────────────
def run_dimension_analysis(symbol):
    print(f"\n{'='*60}")
    print(f"维度贡献度分析: {symbol.upper()}")
    print('='*60)
    
    sym_lower = symbol.lower()
    df4h = pd.read_parquet(BASE / f'data/historical/{sym_lower}usdt/{sym_lower}usdt_4h.parquet')
    regime_file = BASE / f'data/historical/{sym_lower}usdt_regime_nolookahead.parquet'
    
    if not regime_file.exists():
        print(f"❌ 缺少体制标签文件")
        return None
    
    df_regime = pd.read_parquet(regime_file)
    regime_by_ts = {}
    for _, row in df_regime.iterrows():
        if row.get('reliable', True):
            regime_by_ts[int(row['ts'])] = row['regime']
    
    ts_list = [int(t.timestamp() * 1000) for t in df4h.index]
    regime_map = {}
    for i, ts in enumerate(ts_list):
        if ts in regime_by_ts:
            regime_map[i] = regime_by_ts[ts]
    
    min_idx = min(regime_map.keys()) if regime_map else 200
    
    # 生成信号样本（用阶段3同样的触发逻辑）
    closes_all = df4h['close'].values.astype(float)
    highs_all  = df4h['high'].values.astype(float)
    lows_all   = df4h['low'].values.astype(float)
    
    sample_indices = []
    rsi_prev = 50.0
    for i in range(min_idx, len(closes_all) - 50):
        regime = regime_map.get(i)
        if not regime:
            continue
        rsi_cur = calc_rsi(closes_all[max(0,i-50):i+1])
        triggered = False
        if rsi_prev < 50 and rsi_cur >= 55 and regime in ('BULL_TREND','BEAR_RECOVERY'):
            triggered = True
        if rsi_prev > 60 and rsi_cur <= 55 and regime in ('BEAR_TREND','BEAR_EARLY','CHOP_MID'):
            triggered = True
        if i >= 12:
            h48 = highs_all[i-12:i].max()
            l48 = lows_all[i-12:i].min()
            if closes_all[i] > h48*1.005 and regime in ('BULL_TREND','BEAR_RECOVERY'):
                triggered = True
            if closes_all[i] < l48*0.995 and regime in ('BEAR_TREND','BEAR_EARLY'):
                triggered = True
        if triggered:
            sample_indices.append(i)
        rsi_prev = rsi_cur
    
    # 去重
    sample_indices = sorted(set(sample_indices))
    print(f"触发事件总数: {len(sample_indices)}")
    
    # 计算所有维度 + 基础评分 + 结算结果
    records = []
    for idx in sample_indices:
        regime = regime_map.get(idx)
        if not regime or regime == 'BEAR_TREND' and True:
            pass  # BEAR_TREND:LONG 宪法封禁在后面处理
        
        df_slice = df4h.iloc[max(0, idx-100):idx+1]
        dims = compute_all_dims(df_slice)
        if dims is None:
            continue
        
        base_score, direction = score_with_dims(dims, regime)
        if base_score < 30 or direction == 'NONE':
            continue
        if regime == 'BEAR_TREND' and direction == 'LONG':
            continue
        
        result = forward_settle(df4h, idx, direction)
        records.append({
            'idx': idx,
            'regime': regime,
            'direction': direction,
            'base_score': base_score,
            'result': result,
            'win': result == 'WIN',
            'dims': dims,
        })
    
    print(f"有效信号数（score≥30）: {len(records)}")
    if len(records) < 20:
        print("❌ 样本量不足")
        return None
    
    base_wr = np.mean([r['win'] for r in records])
    print(f"基础WR（全维度）: {base_wr:.1%}")
    
    # ── LOO维度贡献度分析 ────────────────────────────────
    dim_names = [
        'D1_RSI_4H', 'D2_RSI_1H_proxy', 'D3_EMA20_pos', 'D4_EMA50_pos',
        'D5_EMA_align', 'D6_BBW', 'D7_ATR_pct', 'D8_price_pos',
        'D9_vol_ratio', 'D10_hurst', 'D11_momentum_12', 'D12_vol_recent'
    ]
    
    contributions = {}
    
    for dim in dim_names:
        loo_wins = []
        for r in records:
            loo_score, loo_dir = score_with_dims(r['dims'], r['regime'], excluded_dim=dim)
            if loo_score >= 30 and loo_dir != 'NONE' and loo_dir == r['direction']:
                loo_wins.append(r['win'])
        
        if len(loo_wins) < 10:
            contributions[dim] = {'loo_wr': None, 'delta_wr': None, 'n': len(loo_wins)}
            continue
        
        loo_wr = np.mean(loo_wins)
        delta = base_wr - loo_wr  # 正值=去掉该维度后WR下降=该维度有正贡献
        contributions[dim] = {
            'loo_wr': round(float(loo_wr), 4),
            'delta_wr': round(float(delta), 4),
            'n': len(loo_wins),
        }
    
    # 按贡献排序
    sorted_dims = sorted(
        [(k, v) for k, v in contributions.items() if v['delta_wr'] is not None],
        key=lambda x: x[1]['delta_wr'],
        reverse=True
    )
    
    print(f"\n{'─'*60}")
    print("维度贡献度排名（去掉该维度后WR的变化）")
    print(f"{'─'*60}")
    print(f"{'维度':<22} {'贡献Δ WR':>10} {'LOO WR':>10} {'样本n':>8}")
    print('─'*60)
    for dim, v in sorted_dims:
        delta = v['delta_wr']
        flag = '🔴 核心' if delta > 0.02 else ('🟡 有效' if delta > 0 else '⚪ 噪音' if delta > -0.01 else '❌ 负贡献')
        print(f"  {dim:<20} {delta:>+10.1%} {v['loo_wr']:>9.1%} {v['n']:>8}  {flag}")
    
    print(f"\n总结:")
    positive_dims = [k for k, v in sorted_dims if v['delta_wr'] and v['delta_wr'] > 0.01]
    noise_dims    = [k for k, v in sorted_dims if v['delta_wr'] and abs(v['delta_wr']) <= 0.01]
    negative_dims = [k for k, v in sorted_dims if v['delta_wr'] and v['delta_wr'] < -0.01]
    print(f"  核心有效维度: {positive_dims}")
    print(f"  噪音维度:     {noise_dims}")
    print(f"  负贡献维度:   {negative_dims}")
    
    return {
        'symbol': symbol,
        'base_wr': round(float(base_wr), 4),
        'sample_count': len(records),
        'contributions': {k: v for k, v in sorted_dims},
        'positive_dims': positive_dims,
        'noise_dims': noise_dims,
        'negative_dims': negative_dims,
    }

# ── 执行 ─────────────────────────────────────────────────
all_results = {}
for sym in ['btc', 'eth']:
    r = run_dimension_analysis(sym)
    if r:
        all_results[sym] = r

# 保存
import json
report_path = BASE / 'data/validation/phase4_dimension_report.json'
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)
print(f"\n✅ 报告已保存: {report_path}")
print("阶段4完成")

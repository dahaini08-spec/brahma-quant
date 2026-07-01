#!/usr/bin/env python3
"""
达摩院 · 梵天胜率地基训练 clean_foundation_v1.0  ★最终版★
设计院 · 2026-06-11

════════════════════════════════════════════════════════════════
【设计原则】
  ① 体制来源：4H大周期 (ema200+rsi) —— 大周期定框架
  ② 信号评分：技术三层 A(趋势)+B(结构)+C(动量) 0~200分
  ③ 动态分位阈值：p60/p70/p80/p90 —— 自适应每个标的分布
  ④ 双向全覆盖：LONG+SHORT，体制不过滤，数据说话
  ⑤ 大样本：8标的 × 8年 × 1H步长 ≈ 200万信号扫描点
  ⑥ 向量化结算：numpy批处理，不逐根循环

【体制定义（4H）】
  BULL_TREND    c > e200×1.15                      牛市稳定
  BULL_EARLY    c ∈ (e200×1.05, e200×1.15)         牛市初期
  CHOP_HIGH     c ∈ (e200, e200×1.05), rsi>55      高位震荡
  CHOP_MID      c ∈ (e200, e200×1.05), rsi 45-55   中位震荡
  CHOP_LOW      c ∈ (e200, e200×1.05), rsi<45      低位震荡
  BEAR_RECOVERY c ∈ (e200×0.88, e200), rsi>55      熊反弹
  BEAR_EARLY    c ∈ (e200×0.88, e200), rsi 42-55   熊初期
  BEAR_TREND    c ∈ (e200×0.88, e200), rsi<42      熊下跌
  BEAR_CRASH    c < e200×0.88                       崩盘

【评分三层（0~200重新校准）】
  Layer-A 趋势一致性 (0~80):
    EMA三线排列得分 (0~40)
    RSI位置+动量 (0~25)
    成交量趋势 (0~15)

  Layer-B 结构位置 (0~80):
    OB订单块距离 (0~35)
    关键支撑/压力位 (0~25)
    FVG缺口 (0~20)

  Layer-C 动量质量 (0~40):
    RSI背离 (0~20)
    ATR扩张 (0~10)
    K线形态 (0~10)

  总分 = A+B+C (0~200)
  体制加权得分 = 总分 × regime_mult[regime][direction]

【交易参数】
  止损: entry ± ATR×1.2
  止盈: entry ∓ ATR×2.0  (RR=1.67)
  最大持仓: 48H
  手续费+滑点: 0.12% (双边合计)
  分析用评分阈值: p60/p70/p80/p90 四档
════════════════════════════════════════════════════════════════
"""

import gc, json, math, time, warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── 路径 ─────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
BT_DIR   = ROOT / 'data' / 'backtest'
DATA_DIR = Path(__file__).parent / 'data'
OUT_DIR  = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)

# ── 常量 ─────────────────────────────────────────────────────
SL_ATR      = 1.2
TP_ATR      = 2.0
RR          = TP_ATR / SL_ATR   # 1.667
HOLD_MAX_1H = 48
COST        = 0.0012             # 双边0.12%
LEVERAGE    = 5

ALL_REGIMES = [
    'BULL_TREND','BULL_EARLY',
    'CHOP_HIGH','CHOP_MID','CHOP_LOW',
    'BEAR_RECOVERY','BEAR_EARLY','BEAR_TREND','BEAR_CRASH',
]
DIRECTIONS  = ['LONG','SHORT']

# 体制×方向信号偏好权重（体制提示方向，不封禁）
REGIME_BIAS = {
    'BULL_TREND':    {'LONG':1.30,'SHORT':0.70},
    'BULL_EARLY':    {'LONG':1.25,'SHORT':0.75},
    'CHOP_HIGH':     {'LONG':1.10,'SHORT':0.90},
    'CHOP_MID':      {'LONG':1.00,'SHORT':1.00},
    'CHOP_LOW':      {'LONG':0.90,'SHORT':1.10},
    'BEAR_RECOVERY': {'LONG':0.90,'SHORT':1.10},
    'BEAR_EARLY':    {'LONG':0.75,'SHORT':1.25},
    'BEAR_TREND':    {'LONG':0.70,'SHORT':1.30},
    'BEAR_CRASH':    {'LONG':0.50,'SHORT':1.50},
}
MIN_N = 50   # 报告最小样本


# ══════════════════════════════════════════════════════════════
# 1. 4H体制计算
# ══════════════════════════════════════════════════════════════

def calc_regime_4h(df4h: pd.DataFrame) -> pd.Series:
    """4H大周期体制（返回与df4h同index的Series）"""
    c    = df4h['close'].astype(float)
    e200 = c.ewm(span=200, adjust=False).mean()
    d    = c.diff()
    ag   = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al   = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi  = (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(50)

    regime = pd.Series('CHOP_MID', index=df4h.index, dtype=object)
    regime[c < e200 * 0.88]                                          = 'BEAR_CRASH'
    mask_be = (c >= e200*0.88) & (c < e200)
    regime[mask_be & (rsi < 42)]                                     = 'BEAR_TREND'
    regime[mask_be & rsi.between(42, 55)]                            = 'BEAR_EARLY'
    regime[mask_be & (rsi > 55)]                                     = 'BEAR_RECOVERY'
    mask_ch = (c >= e200) & (c < e200*1.05)
    regime[mask_ch & (rsi < 45)]                                     = 'CHOP_LOW'
    regime[mask_ch & rsi.between(45, 55)]                            = 'CHOP_MID'
    regime[mask_ch & (rsi > 55)]                                     = 'CHOP_HIGH'
    regime[(c >= e200*1.05) & (c < e200*1.15)]                      = 'BULL_EARLY'
    regime[c >= e200 * 1.15]                                         = 'BULL_TREND'
    return regime


# ══════════════════════════════════════════════════════════════
# 2. 三层评分（向量化，0~200）
# ══════════════════════════════════════════════════════════════

def build_scores(df1h: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    """
    在1H DataFrame上计算三层评分，体制用4H前向合并
    返回扩展后的DataFrame（含regime / score_long / score_short）
    """
    df = df1h.copy()
    c  = df['close'].astype(float)
    h  = df['high'].astype(float)
    l  = df['low'].astype(float)
    v  = df['volume'].astype(float)
    n  = len(df)

    # ── 基础指标 ─────────────────────────────────────────────
    e21  = c.ewm(span=21,  adjust=False).mean()
    e55  = c.ewm(span=55,  adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()

    d   = c.diff()
    ag  = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al  = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi = (100 - 100/(1 + ag/al.replace(0,np.nan))).fillna(50)

    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    cv, hv, lv, vv    = c.values, h.values, l.values, v.values
    e21v, e55v, e200v = e21.values, e55.values, e200.values
    rsiv, atrv        = rsi.values, atr.values
    vmean = pd.Series(vv).rolling(20).mean().values

    # ─────────────────────────────────────────────────────────
    # Layer-A: 趋势一致性 (0~80)
    # ─────────────────────────────────────────────────────────

    # EMA三线排列 (0~40)
    bull_align = (e21v > e55v) & (e55v > e200v)              # 完美多排
    bear_align = (e21v < e55v) & (e55v < e200v)              # 完美空排
    semi_bull  = (cv > e200v) & ~bull_align                   # 价格在200上但不完美
    semi_bear  = (cv < e200v) & ~bear_align                   # 价格在200下但不完美

    ema_long  = np.where(bull_align, 40,
                np.where(semi_bull,  20,
                np.where(bear_align,  0, 10)))
    ema_short = np.where(bear_align, 40,
                np.where(semi_bear,  20,
                np.where(bull_align,  0, 10)))

    # RSI位置加分 (0~25)
    rsi_long  = np.where(rsiv < 30, 25,
                np.where(rsiv < 40, 18,
                np.where(rsiv < 50, 10,
                np.where(rsiv < 60,  5, 0))))
    rsi_short = np.where(rsiv > 70, 25,
                np.where(rsiv > 60, 18,
                np.where(rsiv > 50, 10,
                np.where(rsiv > 40,  5, 0))))

    # 成交量趋势 (0~15)
    vol_ratio = np.where(vmean > 0, vv/(vmean+1e-9), 1.0)
    vol_score = np.where(vol_ratio > 2.0, 15,
                np.where(vol_ratio > 1.5, 10,
                np.where(vol_ratio > 1.2,  5, 0)))

    score_a_long  = (ema_long  + rsi_long  + vol_score).clip(0, 80)
    score_a_short = (ema_short + rsi_short + vol_score).clip(0, 80)

    # ─────────────────────────────────────────────────────────
    # Layer-B: 结构位置 (0~80)
    # ─────────────────────────────────────────────────────────

    # OB订单块：近20根最高/低点与当前距离（ATR标准化）
    roll_h20 = pd.Series(hv).rolling(20).max().fillna(method='bfill').values
    roll_l20 = pd.Series(lv).rolling(20).min().fillna(method='bfill').values

    dist_from_h = np.where(atrv > 0, (roll_h20 - cv)/(atrv+1e-9), 0).clip(0,10)
    dist_from_l = np.where(atrv > 0, (cv - roll_l20)/(atrv+1e-9), 0).clip(0,10)

    # 做空OB：价格接近近高点（0~35）
    ob_short = np.where(dist_from_h < 0.5, 35,
               np.where(dist_from_h < 1.5, 25,
               np.where(dist_from_h < 3.0, 15,
               np.where(dist_from_h < 5.0,  8, 2))))
    # 做多OB：价格接近近低点（0~35）
    ob_long  = np.where(dist_from_l < 0.5, 35,
               np.where(dist_from_l < 1.5, 25,
               np.where(dist_from_l < 3.0, 15,
               np.where(dist_from_l < 5.0,  8, 2))))

    # FVG公平价值缺口 (0~20)
    fvg_bull = (pd.Series(lv).shift(-1) - pd.Series(hv).shift(1)).clip(lower=0)
    fvg_bear = (pd.Series(lv).shift(1)  - pd.Series(hv).shift(-1)).clip(lower=0)
    fvg_bull_s = ((fvg_bull/(pd.Series(cv)+1e-9))*500).clip(0,20).fillna(0).values
    fvg_bear_s = ((fvg_bear/(pd.Series(cv)+1e-9))*500).clip(0,20).fillna(0).values

    # 摆动位距离 (0~25) - 近50根最高/低点
    sw_h50 = pd.Series(hv).rolling(50).max().fillna(method='bfill').values
    sw_l50 = pd.Series(lv).rolling(50).min().fillna(method='bfill').values
    dist_sw_h = np.where(atrv>0,(sw_h50-cv)/(atrv+1e-9),5).clip(0,10)
    dist_sw_l = np.where(atrv>0,(cv-sw_l50)/(atrv+1e-9),5).clip(0,10)
    sw_short = np.where(dist_sw_h < 1.0, 25,
               np.where(dist_sw_h < 3.0, 15,
               np.where(dist_sw_h < 6.0,  8, 3)))
    sw_long  = np.where(dist_sw_l < 1.0, 25,
               np.where(dist_sw_l < 3.0, 15,
               np.where(dist_sw_l < 6.0,  8, 3)))

    score_b_long  = (ob_long  + fvg_bull_s + sw_long ).clip(0, 80)
    score_b_short = (ob_short + fvg_bear_s + sw_short).clip(0, 80)

    # ─────────────────────────────────────────────────────────
    # Layer-C: 动量质量 (0~40)
    # ─────────────────────────────────────────────────────────

    # RSI背离 (0~20)
    price_hi20 = (pd.Series(cv) == pd.Series(cv).rolling(20).max()).values
    price_lo20 = (pd.Series(cv) == pd.Series(cv).rolling(20).min()).values
    rsi_hi20   = (pd.Series(rsiv) == pd.Series(rsiv).rolling(20).max()).values
    rsi_lo20   = (pd.Series(rsiv) == pd.Series(rsiv).rolling(20).min()).values

    bear_div_s = (price_hi20 & ~rsi_hi20).astype(int) * 20  # 顶背离→空
    bull_div_s = (price_lo20 & ~rsi_lo20).astype(int) * 20  # 底背离→多

    # ATR扩张 (0~10) - 当前ATR vs 20根均值
    atr_ma20 = pd.Series(atrv).rolling(20).mean().values
    atr_expand = np.where(atr_ma20>0, atrv/(atr_ma20+1e-9), 1.0)
    atr_score = np.where(atr_expand > 1.5, 10,
                np.where(atr_expand > 1.2,  7,
                np.where(atr_expand > 1.0,  4, 0)))

    # K线形态 (0~10) - 长下影线=多头信号，长上影线=空头信号
    body    = np.abs(cv - pd.Series(cv).shift(1).values).clip(min=1e-9)
    up_wick = hv - np.maximum(cv, pd.Series(cv).shift(1).values)
    dn_wick = np.minimum(cv, pd.Series(cv).shift(1).values) - lv
    candle_long  = np.where(dn_wick > body*1.5, 10,
                   np.where(dn_wick > body*0.8,  5, 0))
    candle_short = np.where(up_wick > body*1.5, 10,
                   np.where(up_wick > body*0.8,  5, 0))

    score_c_long  = (bull_div_s + atr_score + candle_long ).clip(0, 40)
    score_c_short = (bear_div_s + atr_score + candle_short).clip(0, 40)

    # ── 合并总分 ─────────────────────────────────────────────
    raw_long  = (score_a_long  + score_b_long  + score_c_long ).clip(0, 200)
    raw_short = (score_a_short + score_b_short + score_c_short).clip(0, 200)

    # 前200根置零（指标预热）
    raw_long[:200]  = 0
    raw_short[:200] = 0

    df['score_long']  = raw_long
    df['score_short'] = raw_short
    df['atr']         = atrv

    # ── 4H体制合并 ───────────────────────────────────────────
    regime4h = calc_regime_4h(df4h)
    df4h_r   = regime4h.reindex(df.index, method='ffill')
    df['regime'] = df4h_r.fillna('CHOP_MID').values

    return df


# ══════════════════════════════════════════════════════════════
# 3. 向量化结算
# ══════════════════════════════════════════════════════════════

def settle_batch(hv, lv, cv, entry_idx, entries, sls, tps, directions, hold_max=48):
    n_bars = len(hv)
    n_tr   = len(entry_idx)
    results  = np.full(n_tr, 'TO', dtype=object)
    pnl_pcts = np.zeros(n_tr)

    for t in range(n_tr):
        i0 = int(entry_idx[t])
        e  = entries[t]; sl = sls[t]; tp = tps[t]; d = directions[t]
        end = min(i0 + hold_max + 1, n_bars)
        hit = False
        for j in range(i0+1, end):
            if d == 'SHORT':
                if hv[j] >= sl:
                    results[t]  = 'SL'; pnl_pcts[t] = (e-sl)/e - COST; hit=True; break
                if lv[j] <= tp:
                    results[t]  = 'TP'; pnl_pcts[t] = (e-tp)/e - COST; hit=True; break
            else:
                if lv[j] <= sl:
                    results[t]  = 'SL'; pnl_pcts[t] = (sl-e)/e - COST; hit=True; break
                if hv[j] >= tp:
                    results[t]  = 'TP'; pnl_pcts[t] = (tp-e)/e - COST; hit=True; break
        if not hit:
            fin = cv[min(end-1, n_bars-1)]
            pnl_pcts[t] = ((e-fin)/e if d=='SHORT' else (fin-e)/e) - COST
    return results, pnl_pcts


# ══════════════════════════════════════════════════════════════
# 4. 全周期双向回测
# ══════════════════════════════════════════════════════════════

def run_backtest(df_feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    全周期双向回测
    采用动态分位阈值：p70（top30%信号）作为入场条件
    同时记录每笔信号的原始分位，供事后多阈值分析
    """
    cv   = df_feat['close'].values.astype(float)
    hv   = df_feat['high'].values.astype(float)
    lv   = df_feat['low'].values.astype(float)
    atrv = df_feat['atr'].values.astype(float)
    sl_  = df_feat['score_long'].values.astype(float)
    ss_  = df_feat['score_short'].values.astype(float)
    regm = df_feat['regime'].values
    n    = len(df_feat)

    # 动态分位阈值（全局，基于有效行）
    valid_mask = np.arange(n) >= 200
    sl_valid = sl_[valid_mask]
    ss_valid = ss_[valid_mask]

    p50_l, p70_l, p80_l, p90_l = (np.nanpercentile(sl_valid,p) for p in [50,70,80,90])
    p50_s, p70_s, p80_s, p90_s = (np.nanpercentile(ss_valid,p) for p in [50,70,80,90])

    def score_tier(score, p50, p70, p80, p90):
        if   score >= p90: return 'p90'
        elif score >= p80: return 'p80'
        elif score >= p70: return 'p70'
        elif score >= p50: return 'p50'
        else:              return 'low'

    records = []
    i = 200
    while i < n - HOLD_MAX_1H - 2:
        regime  = regm[i]
        atr_i   = float(atrv[i])
        if atr_i <= 0 or np.isnan(atr_i):
            i += 1; continue

        for direction in DIRECTIONS:
            score = float(sl_[i] if direction == 'LONG' else ss_[i])
            p50_ = p50_l if direction == 'LONG' else p50_s
            # 只取top50%以上的信号（排除明显噪音）
            if score < p50_: continue

            tier = score_tier(score,
                              p50_l if direction=='LONG' else p50_s,
                              p70_l if direction=='LONG' else p70_s,
                              p80_l if direction=='LONG' else p80_s,
                              p90_l if direction=='LONG' else p90_s)

            # 体制偏好乘数加权后的信号分
            bias    = REGIME_BIAS.get(regime,{}).get(direction, 1.0)
            wscore  = round(score * bias, 1)

            entry = cv[i]
            if direction == 'SHORT':
                sl_p = entry + atr_i * SL_ATR
                tp_p = entry - atr_i * TP_ATR
            else:
                sl_p = entry - atr_i * SL_ATR
                tp_p = entry + atr_i * TP_ATR

            records.append({
                'idx':       i,
                'ts':        df_feat.index[i],
                'sym':       symbol,
                'regime':    regime,
                'direction': direction,
                'score':     score,
                'wscore':    wscore,
                'tier':      tier,
                'bias':      bias,
                'entry':     entry,
                'sl':        sl_p,
                'tp':        tp_p,
            })
        i += 1

    if not records:
        return pd.DataFrame()

    tdf = pd.DataFrame(records)
    results, pnls = settle_batch(
        hv, lv, cv,
        tdf['idx'].values, tdf['entry'].values,
        tdf['sl'].values,  tdf['tp'].values,
        tdf['direction'].values,
    )
    tdf['result']  = results
    tdf['pnl_lev'] = (pnls * LEVERAGE * 100).round(3)
    return tdf


# ══════════════════════════════════════════════════════════════
# 5. 统计分析
# ══════════════════════════════════════════════════════════════

def wilson_ci(wins, total, z=1.96):
    if total == 0: return (0.,0.)
    p = wins/total
    d = 1 + z**2/total
    c = (p + z**2/(2*total)) / d
    m = z * math.sqrt(p*(1-p)/total + z**2/(4*total**2)) / d
    return (round(max(0,c-m),3), round(min(1,c+m),3))

def stats(sub):
    wins   = (sub['result']=='TP').sum()
    losses = (sub['result']=='SL').sum()
    settled= wins+losses
    if settled == 0: return None
    wr = wins/settled
    pf = wins/losses if losses>0 else (9.99 if wins>0 else 0.)
    ci = wilson_ci(wins, settled)
    avg_pnl = sub['pnl_lev'].mean()
    return dict(n=settled, n_all=len(sub), wins=int(wins), losses=int(losses),
                wr=round(wr,4), pf=round(min(pf,9.99),3),
                ci=list(ci), avg_pnl=round(float(avg_pnl),3),
                verdict=('STRONG' if pf>=1.5 else 'POSITIVE' if pf>=1.2
                         else 'MARGINAL' if pf>=1.0 else 'NEGATIVE'))

def analyze(trades: pd.DataFrame) -> dict:
    out = {}

    # ① 体制×方向（核心矩阵）
    rd_rows = []
    for (regime, direction), sub in trades.groupby(['regime','direction']):
        s = stats(sub)
        if s and s['n'] >= MIN_N:
            s['regime'] = regime; s['direction'] = direction
            rd_rows.append(s)
    out['regime_dir'] = sorted(rd_rows, key=lambda x:-x['n'])

    # ② 体制×方向×分位档（揭示评分有效性）
    rd_tier_rows = []
    for (regime, direction, tier), sub in trades.groupby(['regime','direction','tier']):
        s = stats(sub)
        if s and s['n'] >= MIN_N//2:
            s['regime']=regime; s['direction']=direction; s['tier']=tier
            rd_tier_rows.append(s)
    out['regime_dir_tier'] = sorted(rd_tier_rows, key=lambda x: -x['n'])

    # ③ 总体统计
    total  = len(trades)
    wins   = (trades['result']=='TP').sum()
    losses = (trades['result']=='SL').sum()
    tos    = total - wins - losses
    settled= wins+losses
    wr_all = wins/settled if settled>0 else 0
    pf_all = wins/losses  if losses>0  else 0
    out['summary'] = dict(
        total_signals=int(total), settled=int(settled),
        wins=int(wins), losses=int(losses), timeouts=int(tos),
        wr=round(float(wr_all),4), pf=round(float(pf_all),3),
        avg_pnl_lev=round(float(trades['pnl_lev'].mean()),3),
    )
    return out


# ══════════════════════════════════════════════════════════════
# 6. 主入口
# ══════════════════════════════════════════════════════════════

SYM_LIST = [
    ('BTCUSDT',  BT_DIR/'BTCUSDT_1h_full.parquet',   BT_DIR/'BTCUSDT_4h_full.parquet'),
    ('ETHUSDT',  BT_DIR/'ETHUSDT_1h_full.parquet',   BT_DIR/'ETHUSDT_4h_full.parquet'),
    ('BNBUSDT',  DATA_DIR/'bnbusdt_1h_2018_2026.parquet',  DATA_DIR/'bnbusdt_4h_2018_2026.parquet'),
    ('SOLUSDT',  DATA_DIR/'solusdt_1h_2018_2026.parquet',  DATA_DIR/'solusdt_4h_2018_2026.parquet'),
    ('DOGEUSDT', DATA_DIR/'dogeusdt_1h_2018_2026.parquet', DATA_DIR/'dogeusdt_4h_2018_2026.parquet'),
    ('ADAUSDT',  DATA_DIR/'adausdt_1h_2018_2026.parquet',  DATA_DIR/'adausdt_4h_2018_2026.parquet'),
    ('LINKUSDT', DATA_DIR/'linkusdt_1h_2018_2026.parquet', DATA_DIR/'linkusdt_4h_2018_2026.parquet'),
    ('LTCUSDT',  DATA_DIR/'ltcusdt_1h_2018_2026.parquet',  DATA_DIR/'ltcusdt_4h_2018_2026.parquet'),
]


def main(quick=False):
    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t0     = time.time()

    syms = SYM_LIST[:2] if quick else SYM_LIST

    print()
    print('═'*65)
    print('  梵天胜率地基训练 clean_foundation_v1.0')
    print('  体制源: 4H大周期  评分: 三层200分  动态分位阈值')
    print('  数据: 2017~2026全周期  双向: LONG+SHORT')
    print('═'*65)
    print(f'  标的({len(syms)}个): {" ".join(s for s,_,_ in syms)}')
    print()

    all_trades = []; sym_stats = {}

    for sym, f1h, f4h in syms:
        ts = time.time()
        if not Path(f1h).exists() or not Path(f4h).exists():
            print(f'[{sym}] ⚠️ 数据文件缺失，跳过'); continue

        print(f'[{sym}] 加载数据...')
        df1h = pd.read_parquet(f1h)[['open','high','low','close','volume']].copy()
        df4h = pd.read_parquet(f4h)[['open','high','low','close','volume']].copy()
        df1h.index = pd.to_datetime(df1h.index, utc=True); df1h = df1h.sort_index()
        df4h.index = pd.to_datetime(df4h.index, utc=True); df4h = df4h.sort_index()

        print(f'  1H:{len(df1h):,}根  4H:{len(df4h):,}根  ({df1h.index[0].date()}~{df1h.index[-1].date()})')

        df_f = build_scores(df1h, df4h)

        # 体制分布
        rdist = df_f['regime'].value_counts()
        print(f'  4H体制: ', end='')
        for r in ALL_REGIMES:
            n = rdist.get(r,0)
            if n: print(f'{r}={n:,}', end=' ')
        print()

        # 评分分布
        for d, col in [('L','score_long'),('S','score_short')]:
            sc = df_f[col].iloc[200:]
            p50,p70,p80,p90 = (np.nanpercentile(sc,p) for p in [50,70,80,90])
            print(f'  score_{d}: p50={p50:.0f} p70={p70:.0f} p80={p80:.0f} p90={p90:.0f} max={sc.max():.0f}')

        trades = run_backtest(df_f, sym)
        if trades.empty:
            print(f'  ❌ 无交易，跳过'); continue

        wins   = (trades['result']=='TP').sum()
        losses = (trades['result']=='SL').sum()
        wr     = wins/(wins+losses) if wins+losses>0 else 0
        print(f'  ✅ 交易:{len(trades):,}笔  WR={wr*100:.1f}%  W={wins} L={losses}  耗时={time.time()-ts:.1f}s')

        all_trades.append(trades)
        sym_stats[sym] = {'n':len(trades),'wr':round(wr,4)}
        del df_f; gc.collect()

    if not all_trades:
        print('❌ 所有标的无数据'); return

    combined = pd.concat(all_trades, ignore_index=True)

    print()
    print('═'*65)
    print(f'  合并总样本: {len(combined):,}笔  ({len(all_trades)}标的)')
    print('═'*65)

    res = analyze(combined)
    s   = res['summary']

    print(f'\n  总体: signals={s["total_signals"]:,}  settled={s["settled"]:,}  '
          f'WR={s["wr"]*100:.1f}%  PF={s["pf"]:.3f}  avgPNL(5x)={s["avg_pnl_lev"]:.2f}%')

    # ── 核心矩阵 ──────────────────────────────────────────────
    ICONS = {'STRONG':'✅✅','POSITIVE':'✅ ','MARGINAL':'⚠️ ','NEGATIVE':'❌ '}
    print()
    print(f'  {"体制":<20} {"方向":<7} {"n":>7} {"WR":>7} {"PF":>7}  评级         WR-CI')
    print('  '+'-'*72)
    for row in res['regime_dir']:
        icon = ICONS.get(row['verdict'],'? ')
        print(f"  {row['regime']:<20} {row['direction']:<7} {row['n']:>7,} "
              f"{row['wr']*100:>6.1f}% {row['pf']:>7.3f}  {icon} {row['verdict']:<12} "
              f"[{row['ci'][0]:.2f},{row['ci'][1]:.2f}]")

    # ── 评分分位效果验证 ──────────────────────────────────────
    print()
    print('  ─── 评分分位 vs WR（验证评分有效性）─────────────────')
    tier_grp = defaultdict(lambda:{'w':0,'l':0})
    for _, row in combined.iterrows():
        if row['result'] in ('TP','SL'):
            tier_grp[row['tier']]['w' if row['result']=='TP' else 'l'] += 1
    print(f'  {"分位档":<8} {"n":>7} {"WR":>8}')
    for tier in ['p90','p80','p70','p50']:
        d = tier_grp[tier]
        n = d['w']+d['l']
        wr = d['w']/n if n else 0
        print(f'  {tier:<8} {n:>7,} {wr*100:>7.1f}%')

    # ── 高价值区间 ──────────────────────────────────────────
    print()
    print('  ─── 高价值区间（n≥100, PF≥1.3）─────────────────────')
    top = sorted([r for r in res['regime_dir_tier'] if r['n']>=100 and r['pf']>=1.3],
                 key=lambda x:-x['pf'])
    for row in top[:12]:
        icon = ICONS.get(row['verdict'],'?')
        print(f"  {icon} {row['regime']:<20} {row['direction']:<6} tier={row['tier']:<4} "
              f"n={row['n']:>5}  WR={row['wr']*100:.1f}%  PF={row['pf']:.3f}")

    # ── 危险区间 ──────────────────────────────────────────────
    bad = sorted([r for r in res['regime_dir_tier'] if r['n']>=100 and r['pf']<=0.80],
                 key=lambda x: x['pf'])
    if bad:
        print()
        print('  ─── 危险区间（n≥100, PF≤0.80）──────────────────────')
        for row in bad[:8]:
            print(f"  ❌ {row['regime']:<20} {row['direction']:<6} tier={row['tier']:<4} "
                  f"n={row['n']:>5}  WR={row['wr']*100:.1f}%  PF={row['pf']:.3f}")

    # ── 保存 ──────────────────────────────────────────────────
    elapsed = time.time() - t0
    payload = {
        '_meta': {
            'ts': ts_str, 'version': 'clean_foundation_v1',
            'syms': [s for s,_,_ in syms],
            'regime_src': '4H大周期 ema200+rsi',
            'score_layers': 'A(趋势80)+B(结构80)+C(动量40)=200分',
            'threshold': '动态百分位p50~p90',
            'rr': f'SL={SL_ATR}×ATR TP={TP_ATR}×ATR RR={RR:.2f}',
            'hold_max': f'{HOLD_MAX_1H}H', 'leverage': LEVERAGE,
            'data': '2017~2026全周期', 'elapsed_s': round(elapsed,1),
            'note': '从零地基版，体制来自4H，评分三层，动态分位阈值，干净唯一版本',
        },
        'summary':         res['summary'],
        'regime_dir':      res['regime_dir'],
        'regime_dir_tier': res['regime_dir_tier'],
        'sym_stats':       sym_stats,
    }
    out_path = OUT_DIR / f'clean_foundation_v1_{ts_str}.json'
    def _serial(obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        raise TypeError(f'Not serializable: {type(obj)}')
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_serial))

    print()
    print(f'  ✅ 保存: {out_path.name}')
    print(f'  总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)')
    print('═'*65)
    return payload


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true', help='只跑BTC+ETH快速验证')
    args = ap.parse_args()
    main(quick=args.quick)

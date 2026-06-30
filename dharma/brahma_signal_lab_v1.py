#!/usr/bin/env python3
"""
达摩院 · 梵天信号实验室 brahma_signal_lab_v1.0
设计院 · 2026-06-12

══════════════════════════════════════════════════════════════════════
【四方联合设计原则】

量化分析师：
  大周期锚定方向，小周期找机会
  用日线/4H趋势状态作为过滤层，然后在15m/1H寻找入场点
  评判标准：n≥1000 才算大样本，n≥5000 才算可靠基准

顶级交易员：
  80%的机会在15m级别发生
  但15m入场必须与4H/1D方向一致，否则是逆势
  逆势入场 = 即使技术完美也大概率亏钱

达摩院：
  不能为了好看的数据只取小样本
  每个指标/组合都必须在完整8年数据上验证
  结果必须包含：样本量、胜率、PF_pnl、最大回撤、年化

设计院：
  梵天系统要为实战服务
  小样本数据是摆设，必须找出高频+高胜率的组合
  目标：找到n>5000 WR>52% PF_pnl>1.2的可靠组合

══════════════════════════════════════════════════════════════════════
【训练架构：三层过滤】

Layer-0 大周期方向锚（日线/4H）：
  daily_trend:  日线EMA20方向（上升/下降/横盘）
  weekly_bias:  周线EMA10位置（价格在上/下）
  h4_regime:    4H体制（9种，已验证）
  
  → 只在大周期方向一致时，才允许小周期交易

Layer-1 1H结构确认：
  h1_trend:     1H EMA20方向
  h1_rsi_zone:  1H RSI区间（超买/超卖/中性）
  h1_ema_stack: EMA21>55>200（多排列）或 EMA21<55<200（空排列）
  h1_above200:  价格在EMA200上/下方
  
  → 结构确认信号：至少3/4项与方向一致

Layer-2 15m触发（主战场）：
  15m_choch:    15m结构转变（CHoCH）
  15m_ob_touch: 触达未破坏的15m订单块
  15m_engulf:   吞没K线
  15m_wick:     针形K线拒绝（上影/下影）
  15m_rsi_div:  15m RSI背离
  15m_vol_spike: 成交量爆发（>1.5×均值）
  
  → 触发信号：组合评分 ≥ 阈值

【测试内容】
  A. 单指标测试（每个指标单独测WR/PF/n）
  B. 组合测试（Layer-0 × Layer-1 × Layer-2）
  C. 体制过滤测试（加上4H体制后的提升幅度）
  D. 最优组合筛选（n>5000 WR>52% PF>1.2）

【测试周期】
  主战场：15m（执行层）
  结构层：1H（确认）
  方向锚：4H + 1D（过滤）
  
【评判标准】
  n≥1,000:  可参考
  n≥5,000:  可信
  n≥20,000: 统计显著
  WR≥52%:   有效
  PF_pnl≥1.2: 正期望
  年化≥30%:   实用
══════════════════════════════════════════════════════════════════════
"""

import gc, json, math, time, warnings
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

ROOT     = Path(__file__).parent.parent
BT_DIR   = ROOT / 'data' / 'backtest'
DATA_DIR = Path(__file__).parent / 'data'
FUT_DIR  = DATA_DIR / 'futures'
OUT_DIR  = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)

LEVERAGE = 5
COST     = 0.0008 * 2   # 期货双边手续费
MIN_N    = 500           # 最小可参考样本
GOOD_N   = 5000          # 可靠样本
MIN_PF   = 1.2
MIN_WR   = 0.50

# ══════════════════════════════════════════════════════════════
# 1. 数据加载 + 特征工程
# ══════════════════════════════════════════════════════════════

def load_ohlcv(sym: str, tf: str) -> pd.DataFrame | None:
    """按优先级加载：期货 > 现货full > 现货parquet"""
    sym_lo = sym.lower()
    candidates = [
        FUT_DIR  / f'{sym_lo}_{tf}_futures.parquet',
        BT_DIR   / f'{sym}_{tf}_full.parquet',
        DATA_DIR / f'{sym_lo}_{tf}_2018_2026.parquet',
    ]
    for f in candidates:
        if Path(f).exists():
            df = pd.read_parquet(f)
            df.index = pd.to_datetime(df.index, utc=True)
            return df[['open','high','low','close','volume']].sort_index() \
                   if 'volume' in df.columns \
                   else df[['open','high','low','close']].sort_index()
    return None

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有需要的指标（向量化）"""
    c = df['close'].astype(float)
    h = df['high'].astype(float)
    l = df['low'].astype(float)
    v = df.get('volume', pd.Series(1.0, index=df.index)).astype(float)

    # EMA
    df['ema10']  = c.ewm(span=10,  adjust=False).mean()
    df['ema20']  = c.ewm(span=20,  adjust=False).mean()
    df['ema21']  = c.ewm(span=21,  adjust=False).mean()
    df['ema50']  = c.ewm(span=50,  adjust=False).mean()
    df['ema55']  = c.ewm(span=55,  adjust=False).mean()
    df['ema200'] = c.ewm(span=200, adjust=False).mean()

    # RSI
    d   = c.diff()
    ag  = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al  = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = (100 - 100/(1 + ag/al.replace(0,np.nan))).fillna(50)

    # ATR
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['macd']        = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist']   = df['macd'] - df['macd_signal']

    # Bollinger Band
    sma20    = c.rolling(20).mean()
    std20    = c.rolling(20).std()
    df['bb_upper'] = sma20 + 2*std20
    df['bb_lower'] = sma20 - 2*std20
    df['bb_pct']   = (c - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-9)

    # 成交量
    vol_ma20      = v.rolling(20).mean()
    df['vol_ratio']= v / (vol_ma20 + 1e-9)

    # EMA方向
    df['ema20_slope'] = (df['ema20'] - df['ema20'].shift(3)) / df['atr'].replace(0,np.nan)
    df['ema50_slope'] = (df['ema50'] - df['ema50'].shift(3)) / df['atr'].replace(0,np.nan)

    # 高低点（SwingHigh/Low）
    df['swing_h20'] = h.rolling(20).max()
    df['swing_l20'] = l.rolling(20).min()

    # 与关键均线距离（ATR标准化）
    df['dist_ema200'] = (c - df['ema200']) / df['atr'].replace(0,np.nan)

    # K线特征
    body = (c - c.shift(1)).abs()
    up_wick = h - c.where(c >= c.shift(1), c.shift(1))
    dn_wick = c.where(c <= c.shift(1), c.shift(1)) - l
    df['wick_up_ratio'] = up_wick / (body + 1e-9)
    df['wick_dn_ratio'] = dn_wick / (body + 1e-9)
    df['body_pct'] = body / (df['atr'] + 1e-9)

    # 涨跌
    df['ret1'] = c.pct_change(1)
    df['ret4'] = c.pct_change(4)

    return df.fillna(method='bfill').fillna(0)


# ══════════════════════════════════════════════════════════════
# 2. 大周期特征（对齐到15m/1H时间戳）
# ══════════════════════════════════════════════════════════════

def build_higher_tf_features(df_15m: pd.DataFrame,
                              df_1h:  pd.DataFrame,
                              df_4h:  pd.DataFrame,
                              df_1d:  pd.DataFrame) -> pd.DataFrame:
    """
    把1H/4H/1D特征对齐到15m时间戳（前向填充）
    这是 "大周期方向锚定" 的核心
    """
    # 4H体制（已验证，9种）
    c4  = df_4h['close'].astype(float)
    e200_4 = c4.ewm(span=200, adjust=False).mean()
    d4  = c4.diff()
    ag4 = d4.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al4 = (-d4).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi4 = (100 - 100/(1 + ag4/al4.replace(0,np.nan))).fillna(50)

    r4 = pd.Series('CHOP_MID', index=df_4h.index, dtype=object)
    r4[c4 < e200_4*0.88]                                       = 'BEAR_CRASH'
    mb = (c4 >= e200_4*0.88) & (c4 < e200_4)
    r4[mb & (rsi4 < 42)]                                       = 'BEAR_TREND'
    r4[mb & rsi4.between(42,55)]                               = 'BEAR_EARLY'
    r4[mb & (rsi4 > 55)]                                       = 'BEAR_RECOVERY'
    ch = (c4 >= e200_4) & (c4 < e200_4*1.05)
    r4[ch & (rsi4 < 45)]                                       = 'CHOP_LOW'
    r4[ch & rsi4.between(45,55)]                               = 'CHOP_MID'
    r4[ch & (rsi4 > 55)]                                       = 'CHOP_HIGH'
    r4[(c4 >= e200_4*1.05) & (c4 < e200_4*1.15)]              = 'BULL_EARLY'
    r4[c4 >= e200_4*1.15]                                      = 'BULL_TREND'

    # 4H EMA方向
    ema20_4 = c4.ewm(span=20, adjust=False).mean()
    h4_trend = pd.Series(0, index=df_4h.index, dtype=int)
    h4_trend[c4 > ema20_4*1.005]  =  1   # 上升
    h4_trend[c4 < ema20_4*0.995]  = -1   # 下降

    # 4H EMA排列
    ema21_4 = c4.ewm(span=21, adjust=False).mean()
    ema55_4 = c4.ewm(span=55, adjust=False).mean()
    h4_ema_bull = ((ema21_4 > ema55_4) & (ema55_4 > e200_4)).astype(int)
    h4_ema_bear = ((ema21_4 < ema55_4) & (ema55_4 < e200_4)).astype(int)

    # 日线趋势
    c1d = df_1d['close'].astype(float)
    ema20_1d = c1d.ewm(span=20, adjust=False).mean()
    ema50_1d = c1d.ewm(span=50, adjust=False).mean()
    daily_bull = ((c1d > ema20_1d) & (ema20_1d > ema50_1d)).astype(int)
    daily_bear = ((c1d < ema20_1d) & (ema20_1d < ema50_1d)).astype(int)
    rsi_1d_d = c1d.diff()
    ag_1d = rsi_1d_d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al_1d = (-rsi_1d_d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi_1d = (100 - 100/(1 + ag_1d/al_1d.replace(0,np.nan))).fillna(50)

    # 1H特征
    c1h = df_1h['close'].astype(float)
    ema21_1h = c1h.ewm(span=21, adjust=False).mean()
    ema55_1h = c1h.ewm(span=55, adjust=False).mean()
    ema200_1h = c1h.ewm(span=200, adjust=False).mean()
    h1_ema_bull = ((ema21_1h > ema55_1h) & (ema55_1h > ema200_1h)).astype(int)
    h1_ema_bear = ((ema21_1h < ema55_1h) & (ema55_1h < ema200_1h)).astype(int)
    h1_above200 = (c1h > ema200_1h).astype(int)
    rsi_1h_d = c1h.diff()
    ag_1h = rsi_1h_d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al_1h = (-rsi_1h_d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi_1h = (100 - 100/(1 + ag_1h/al_1h.replace(0,np.nan))).fillna(50)
    rsi_1h_ob = (rsi_1h > 65).astype(int)   # 超买
    rsi_1h_os = (rsi_1h < 35).astype(int)   # 超卖

    # 对齐到15m时间戳
    idx = df_15m.index
    features = pd.DataFrame(index=idx)

    # 4H特征
    features['h4_regime']   = r4.reindex(idx, method='ffill').fillna('CHOP_MID')
    features['h4_trend']    = h4_trend.reindex(idx, method='ffill').fillna(0)
    features['h4_ema_bull'] = h4_ema_bull.reindex(idx, method='ffill').fillna(0)
    features['h4_ema_bear'] = h4_ema_bear.reindex(idx, method='ffill').fillna(0)
    features['h4_above200'] = (c4 > e200_4).astype(int).reindex(idx, method='ffill').fillna(0)

    # 1D特征
    features['d1_bull'] = daily_bull.reindex(idx, method='ffill').fillna(0)
    features['d1_bear'] = daily_bear.reindex(idx, method='ffill').fillna(0)
    features['d1_rsi']  = rsi_1d.reindex(idx, method='ffill').fillna(50)

    # 1H特征
    features['h1_ema_bull']  = h1_ema_bull.reindex(idx, method='ffill').fillna(0)
    features['h1_ema_bear']  = h1_ema_bear.reindex(idx, method='ffill').fillna(0)
    features['h1_above200']  = h1_above200.reindex(idx, method='ffill').fillna(0)
    features['h1_rsi_ob']    = rsi_1h_ob.reindex(idx, method='ffill').fillna(0)
    features['h1_rsi_os']    = rsi_1h_os.reindex(idx, method='ffill').fillna(0)

    return features


# ══════════════════════════════════════════════════════════════
# 3. 向量化结算
# ══════════════════════════════════════════════════════════════

def settle_vectorized(hv, lv, cv, entry_idx, entries, sls, tps, dirs, hold_max):
    n_bars = len(hv); n_tr = len(entry_idx)
    results  = np.full(n_tr, 'TO', dtype=object)
    pnl_pcts = np.zeros(n_tr)
    for t in range(n_tr):
        i0=int(entry_idx[t]); e=entries[t]; sl=sls[t]; tp=tps[t]; d=dirs[t]
        end=min(i0+hold_max+1, n_bars)
        for j in range(i0+1, end):
            if d=='SHORT':
                if hv[j]>=sl: results[t]='SL'; pnl_pcts[t]=(e-sl)/e-COST; break
                if lv[j]<=tp: results[t]='TP'; pnl_pcts[t]=(e-tp)/e-COST; break
            else:
                if lv[j]<=sl: results[t]='SL'; pnl_pcts[t]=(sl-e)/e-COST; break
                if hv[j]>=tp: results[t]='TP'; pnl_pcts[t]=(tp-e)/e-COST; break
        else:
            fin=cv[min(end-1,n_bars-1)]
            pnl_pcts[t]=((e-fin)/e if d=='SHORT' else (fin-e)/e)-COST
    return results, pnl_pcts


# ══════════════════════════════════════════════════════════════
# 4. 核心：信号条件扫描器
# ══════════════════════════════════════════════════════════════

def scan_signals(df_15m_feat: pd.DataFrame,
                 htf_feat: pd.DataFrame,
                 sl_atr: float = 1.0,
                 tp_atr: float = 2.0,
                 hold_max: int = 96,
                 direction: str = 'BOTH',
                 filter_conditions: dict = None) -> pd.DataFrame:
    """
    扫描满足条件的15m信号点，批量结算
    filter_conditions: dict of {feature_name: required_value}
    direction: 'LONG' / 'SHORT' / 'BOTH'
    """
    df = df_15m_feat.copy()
    # 合并高周期特征
    for col in htf_feat.columns:
        df[col] = htf_feat[col].reindex(df.index, method='ffill').fillna(0)

    cv   = df['close'].values.astype(float)
    hv   = df['high'].values.astype(float)
    lv   = df['low'].values.astype(float)
    atrv = df['atr'].values.astype(float)
    n    = len(df)
    warmup = 300

    # 构建过滤mask（向量化）
    mask = np.ones(n, dtype=bool)
    mask[:warmup] = False
    for feat, val in (filter_conditions or {}).items():
        if feat not in df.columns:
            continue
        col_arr = df[feat].values
        if isinstance(val, (int, float)):
            mask &= (col_arr == val)
        elif isinstance(val, str):
            mask &= (col_arr == val)
        elif isinstance(val, tuple) and len(val) == 2:
            lo, hi = val
            if lo is not None: mask &= (col_arr >= lo)
            if hi is not None: mask &= (col_arr <= hi)

    # 最后N根不入场（留结算空间）
    mask[-(hold_max+2):] = False

    entry_idx_list = np.where(mask)[0]
    if len(entry_idx_list) == 0:
        return pd.DataFrame()

    # 根据direction生成records
    records = []
    for i in entry_idx_list:
        atr_i = float(atrv[i])
        if atr_i <= 0 or np.isnan(atr_i): continue
        e = float(cv[i])
        dirs_to_trade = [direction] if direction != 'BOTH' else ['LONG','SHORT']
        for d in dirs_to_trade:
            sl = (e+atr_i*sl_atr) if d=='SHORT' else (e-atr_i*sl_atr)
            tp = (e-atr_i*tp_atr) if d=='SHORT' else (e+atr_i*tp_atr)
            records.append({'idx':i,'direction':d,'entry':e,'sl':sl,'tp':tp})

    if not records:
        return pd.DataFrame()

    tdf = pd.DataFrame(records)
    results, pnls = settle_vectorized(
        hv, lv, cv,
        tdf['idx'].values, tdf['entry'].values,
        tdf['sl'].values, tdf['tp'].values,
        tdf['direction'].values, hold_max
    )
    tdf['result']  = results
    tdf['pnl_raw'] = pnls
    tdf['pnl_lev'] = (pnls * LEVERAGE * 100).round(3)
    return tdf


def calc_pf_pnl(trades):
    """正确的PF = 盈利金额/亏损金额"""
    if trades.empty: return None
    w = (trades['result']=='TP').sum()
    l = (trades['result']=='SL').sum()
    n = w + l
    if n == 0: return None
    wr    = w / n
    pnl_w = trades[trades['result']=='TP']['pnl_raw'].sum()
    pnl_l = abs(trades[trades['result']=='SL']['pnl_raw'].sum())
    pf    = pnl_w / pnl_l if pnl_l > 0 else (9.99 if pnl_w > 0 else 0.)
    avg   = trades['pnl_lev'].mean()
    # Wilson CI
    z=1.96; d_=1+z**2/n; p=wr
    ci_c=(p+z**2/(2*n))/d_; ci_m=z*math.sqrt(p*(1-p)/n+z**2/(4*n**2))/d_
    ci = (round(max(0,ci_c-ci_m),3), round(min(1,ci_c+ci_m),3))
    return dict(n=int(n),wins=int(w),losses=int(l),
                wr=round(wr,4), pf_pnl=round(min(pf,9.99),3),
                avg_pnl=round(float(avg),3), ci=list(ci))


# ══════════════════════════════════════════════════════════════
# 5. 主实验：A-单指标 / B-组合 / C-体制加持
# ══════════════════════════════════════════════════════════════

def run_lab(sym: str, verbose: bool = True) -> dict:
    t0 = time.time()
    if verbose: print(f'\n[{sym}] 加载数据...', flush=True)

    df_15m = load_ohlcv(sym, '15m')
    df_1h  = load_ohlcv(sym, '1h')
    df_4h  = load_ohlcv(sym, '4h')
    df_1d  = load_ohlcv(sym, '1d')

    if df_15m is None or df_4h is None:
        print(f'  ❌ 数据缺失'); return {}

    if verbose:
        print(f'  15m:{len(df_15m):,}  1H:{len(df_1h):,}  4H:{len(df_4h):,}  1D:{len(df_1d):,}')

    # 计算15m指标
    if verbose: print(f'  计算15m指标...', flush=True)
    df_15m = add_indicators(df_15m)

    # 构建高周期特征（对齐到15m）
    if verbose: print(f'  构建高周期特征(1H/4H/1D)...', flush=True)
    htf = build_higher_tf_features(df_15m, df_1h, df_4h, df_1d)

    SL, TP, HOLD = 1.0, 2.0, 96  # 15m: 1×ATR止损, 2×ATR止盈, 96根(24H)
    hv = df_15m['high'].values.astype(float)
    lv = df_15m['low'].values.astype(float)
    cv = df_15m['close'].values.astype(float)

    results = {}

    # ─────────────────────────────────────────────────────────
    # A. 基线：无过滤（纯随机入场）
    # ─────────────────────────────────────────────────────────
    for d in ['LONG','SHORT']:
        trades = scan_signals(df_15m, htf, SL, TP, HOLD, d, {})
        s = calc_pf_pnl(trades)
        if s: results[f'BASELINE_{d}'] = s

    if verbose:
        bl_l = results.get('BASELINE_LONG',{})
        bl_s = results.get('BASELINE_SHORT',{})
        print(f'  基线 LONG: n={bl_l.get("n",0):,} WR={bl_l.get("wr",0)*100:.1f}% PF={bl_l.get("pf_pnl",0):.3f}')
        print(f'  基线 SHORT: n={bl_s.get("n",0):,} WR={bl_s.get("wr",0)*100:.1f}% PF={bl_s.get("pf_pnl",0):.3f}')

    # ─────────────────────────────────────────────────────────
    # B. 单指标测试（15m本身的技术指标）
    # ─────────────────────────────────────────────────────────
    if verbose: print(f'\n  [B] 单指标扫描...', flush=True)

    single_tests = [
        # RSI系列
        ('rsi_os_long',   'LONG',  {'rsi': (None, 30)}),
        ('rsi_ob_short',  'SHORT', {'rsi': (70, None)}),
        ('rsi_mid_long',  'LONG',  {'rsi': (40, 60)}),
        ('rsi_mid_short', 'SHORT', {'rsi': (40, 60)}),
        # EMA排列
        ('ema_bull_long',  'LONG',  {'ema20_slope': (0.1, None)}),
        ('ema_bear_short', 'SHORT', {'ema20_slope': (None, -0.1)}),
        # 价格位置（相对EMA200）
        ('above200_long',  'LONG',  {'dist_ema200': (0.5, None)}),
        ('below200_short', 'SHORT', {'dist_ema200': (None, -0.5)}),
        # 成交量放大
        ('vol_spike_long',  'LONG',  {'vol_ratio': (1.5, None)}),
        ('vol_spike_short', 'SHORT', {'vol_ratio': (1.5, None)}),
        # Bollinger Band
        ('bb_low_long',   'LONG',  {'bb_pct': (None, 0.1)}),
        ('bb_high_short', 'SHORT', {'bb_pct': (0.9, None)}),
        # K线形态
        ('wick_dn_long',  'LONG',  {'wick_dn_ratio': (1.5, None)}),  # 长下影→多
        ('wick_up_short', 'SHORT', {'wick_up_ratio': (1.5, None)}),  # 长上影→空
        # MACD方向
        ('macd_bull_long',  'LONG',  {'macd_hist': (0.0, None)}),
        ('macd_bear_short', 'SHORT', {'macd_hist': (None, 0.0)}),
    ]

    for name, d, conds in single_tests:
        trades = scan_signals(df_15m, htf, SL, TP, HOLD, d, conds)
        s = calc_pf_pnl(trades)
        if s:
            results[f'SINGLE_{name}'] = {**s, 'conditions': conds, 'direction': d}

    if verbose:
        single_rows = [(k,v) for k,v in results.items() if k.startswith('SINGLE_')]
        single_rows.sort(key=lambda x: -x[1].get('pf_pnl',0))
        print(f'  单指标Top5（PF_pnl）:')
        for k,v in single_rows[:5]:
            print(f'    {k:<35} n={v["n"]:>7,} WR={v["wr"]*100:.1f}% PF={v["pf_pnl"]:.3f}')

    # ─────────────────────────────────────────────────────────
    # C. 大周期方向锚 × 15m执行（核心）
    # ─────────────────────────────────────────────────────────
    if verbose: print(f'\n  [C] 大周期方向锚扫描（4H体制过滤+1H/1D一致性）...', flush=True)

    htf_tests = [
        # 4H体制 + 方向
        ('h4_bear_trend_short', 'SHORT', {'h4_regime': 'BEAR_TREND'}),
        ('h4_bear_early_short', 'SHORT', {'h4_regime': 'BEAR_EARLY'}),
        ('h4_bull_trend_long',  'LONG',  {'h4_regime': 'BULL_TREND'}),
        ('h4_bull_early_long',  'LONG',  {'h4_regime': 'BULL_EARLY'}),
        ('h4_chop_high_long',   'LONG',  {'h4_regime': 'CHOP_HIGH'}),
        ('h4_chop_low_short',   'SHORT', {'h4_regime': 'CHOP_LOW'}),
        # 1H EMA排列
        ('h1_bull_ema_long',    'LONG',  {'h1_ema_bull': 1}),
        ('h1_bear_ema_short',   'SHORT', {'h1_ema_bear': 1}),
        # 1H above200
        ('h1_above200_long',    'LONG',  {'h1_above200': 1}),
        ('h1_below200_short',   'SHORT', {'h1_above200': 0}),
        # 1D趋势方向
        ('d1_bull_long',  'LONG',  {'d1_bull': 1}),
        ('d1_bear_short', 'SHORT', {'d1_bear': 1}),
    ]

    for name, d, conds in htf_tests:
        trades = scan_signals(df_15m, htf, SL, TP, HOLD, d, conds)
        s = calc_pf_pnl(trades)
        if s:
            results[f'HTF_{name}'] = {**s, 'conditions': conds, 'direction': d}

    # ─────────────────────────────────────────────────────────
    # D. 组合过滤（大周期方向 + 15m技术确认）
    # ─────────────────────────────────────────────────────────
    if verbose: print(f'\n  [D] 组合过滤扫描（大周期+15m技术）...', flush=True)

    combo_tests_short = [
        # 核心做空组合
        ('bear_trend+rsi_ob',       {'h4_regime':'BEAR_TREND','rsi':(65,None)}),
        ('bear_trend+ema_bear',     {'h4_regime':'BEAR_TREND','ema20_slope':(None,-0.05)}),
        ('bear_trend+bb_high',      {'h4_regime':'BEAR_TREND','bb_pct':(0.85,None)}),
        ('bear_trend+macd_bear',    {'h4_regime':'BEAR_TREND','macd_hist':(None,0.0)}),
        ('bear_trend+vol_spike',    {'h4_regime':'BEAR_TREND','vol_ratio':(1.5,None)}),
        ('bear_trend+wick_up',      {'h4_regime':'BEAR_TREND','wick_up_ratio':(1.5,None)}),
        # 1H空排列 + 1D空趋势
        ('h1bear+d1bear',           {'h1_ema_bear':1,'d1_bear':1}),
        ('h1bear+d1bear+rsi_ob',    {'h1_ema_bear':1,'d1_bear':1,'rsi':(65,None)}),
        ('below200+d1bear+rsi_ob',  {'h1_above200':0,'d1_bear':1,'rsi':(65,None)}),
        ('bear_early+h1bear',       {'h4_regime':'BEAR_EARLY','h1_ema_bear':1}),
        ('bear_early+below200+macd',{'h4_regime':'BEAR_EARLY','h1_above200':0,'macd_hist':(None,0)}),
        # 三重确认
        ('3x_bear',                 {'h4_regime':'BEAR_TREND','h1_ema_bear':1,'d1_bear':1}),
        ('3x_bear+rsi',             {'h4_regime':'BEAR_TREND','h1_ema_bear':1,'rsi':(60,None)}),
        ('3x_bear+wick',            {'h4_regime':'BEAR_TREND','h1_ema_bear':1,'wick_up_ratio':(1.5,None)}),
    ]

    combo_tests_long = [
        ('bull_trend+rsi_os',       {'h4_regime':'BULL_TREND','rsi':(None,35)}),
        ('bull_trend+ema_bull',     {'h4_regime':'BULL_TREND','ema20_slope':(0.05,None)}),
        ('bull_trend+bb_low',       {'h4_regime':'BULL_TREND','bb_pct':(None,0.15)}),
        ('bull_early+h1bull',       {'h4_regime':'BULL_EARLY','h1_ema_bull':1}),
        ('bull_early+above200',     {'h4_regime':'BULL_EARLY','h1_above200':1}),
        ('h1bull+d1bull',           {'h1_ema_bull':1,'d1_bull':1}),
        ('h1bull+d1bull+rsi_os',    {'h1_ema_bull':1,'d1_bull':1,'rsi':(None,35)}),
        ('3x_bull',                 {'h4_regime':'BULL_TREND','h1_ema_bull':1,'d1_bull':1}),
        ('3x_bull+wick_dn',         {'h4_regime':'BULL_TREND','h1_ema_bull':1,'wick_dn_ratio':(1.5,None)}),
        ('chop_high+h1bull+rsi_os', {'h4_regime':'CHOP_HIGH','h1_ema_bull':1,'rsi':(None,40)}),
    ]

    for name, conds in combo_tests_short:
        trades = scan_signals(df_15m, htf, SL, TP, HOLD, 'SHORT', conds)
        s = calc_pf_pnl(trades)
        if s:
            results[f'COMBO_SHORT_{name}'] = {**s, 'conditions': conds, 'direction':'SHORT'}

    for name, conds in combo_tests_long:
        trades = scan_signals(df_15m, htf, SL, TP, HOLD, 'LONG', conds)
        s = calc_pf_pnl(trades)
        if s:
            results[f'COMBO_LONG_{name}'] = {**s, 'conditions': conds, 'direction':'LONG'}

    elapsed = time.time()-t0
    if verbose: print(f'\n  ✅ 完成  {len(results)}项测试  耗时{elapsed:.0f}s')

    return results


# ══════════════════════════════════════════════════════════════
# 6. 输出排行榜
# ══════════════════════════════════════════════════════════════

def print_ranking(results: dict, sym: str):
    """输出完整排行榜"""
    rows = [(k, v) for k, v in results.items() if isinstance(v, dict) and 'pf_pnl' in v]

    # 按n>500 过滤 + PF排序
    valid = [(k,v) for k,v in rows if v.get('n',0) >= MIN_N]
    valid.sort(key=lambda x: -x[1].get('pf_pnl',0))

    print(f'\n{"="*75}')
    print(f'  {sym} · 完整排行榜（n≥{MIN_N}，按PF_pnl降序）')
    print(f'{"="*75}')
    print(f'  {"名称":<42} {"n":>8} {"WR":>7} {"PF_pnl":>8} {"avgPNL":>9}  评级')
    print(f'  {"-"*72}')

    for k, v in valid:
        n    = v['n']
        wr   = v['wr']
        pf   = v['pf_pnl']
        avg  = v['avg_pnl']
        icon = '✅✅' if pf>=1.5 else '✅ ' if pf>=1.2 else '⚠️ ' if pf>=1.0 else '❌ '
        sample_flag = '★★' if n>=GOOD_N else ('★' if n>=MIN_N else '')
        print(f'  {k:<42} {n:>8,} {wr*100:>6.1f}% {pf:>8.3f} {avg:>+8.3f}%  {icon} {sample_flag}')

    # 黄金区间：n≥5000 + PF≥1.2
    gold = [(k,v) for k,v in valid if v.get('n',0)>=GOOD_N and v.get('pf_pnl',0)>=MIN_PF]
    if gold:
        print(f'\n  {"━"*72}')
        print(f'  🏆 黄金区间（n≥{GOOD_N:,}，PF≥{MIN_PF}）')
        print(f'  {"━"*72}')
        for k,v in gold:
            print(f'  ★ {k:<40} n={v["n"]:>7,} WR={v["wr"]*100:.1f}% PF={v["pf_pnl"]:.3f} avg={v["avg_pnl"]:+.3f}%')


# ══════════════════════════════════════════════════════════════
# 7. 主入口
# ══════════════════════════════════════════════════════════════

def main():
    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t0     = time.time()

    print(); print('█'*75)
    print('  梵天信号实验室 brahma_signal_lab_v1.0')
    print('  大周期方向锚(1D/4H) + 1H结构 + 15m执行  全组合扫描')
    print('  8年数据  BTC+ETH  高样本高胜率  为梵天系统提供实战基础')
    print('█'*75)

    all_results = {}

    for sym in ['BTCUSDT','ETHUSDT']:
        res = run_lab(sym, verbose=True)
        all_results[sym] = res
        print_ranking(res, sym)

    # 跨标的对比：找BTC+ETH都有效的组合
    print(f'\n{"="*75}')
    print(f'  跨标的黄金组合（BTC+ETH 均 n≥{MIN_N} PF≥1.1）')
    print(f'{"="*75}')
    btc_map = all_results.get('BTCUSDT', {})
    eth_map = all_results.get('ETHUSDT', {})
    common_keys = set(btc_map.keys()) & set(eth_map.keys())
    cross = []
    for k in common_keys:
        b = btc_map[k]; e = eth_map[k]
        if (b.get('n',0)>=MIN_N and e.get('n',0)>=MIN_N and
            b.get('pf_pnl',0)>=1.1 and e.get('pf_pnl',0)>=1.1):
            avg_pf = (b['pf_pnl'] + e['pf_pnl']) / 2
            avg_n  = (b['n'] + e['n']) // 2
            cross.append((k, b['pf_pnl'], e['pf_pnl'], avg_pf, avg_n))
    cross.sort(key=lambda x: -x[3])
    print(f'  {"名称":<42} {"BTC_PF":>8} {"ETH_PF":>8} {"avg_PF":>8} {"avg_n":>8}')
    print(f'  {"-"*72}')
    for k, bp, ep, ap, an in cross[:20]:
        icon = '✅✅' if ap>=1.4 else '✅ ' if ap>=1.2 else '⚠️ '
        print(f'  {k:<42} {bp:>8.3f} {ep:>8.3f} {ap:>8.3f} {an:>8,}  {icon}')

    # 保存
    elapsed = time.time()-t0
    def _j(o):
        if isinstance(o,(np.integer,)): return int(o)
        if isinstance(o,(np.floating,)): return float(o)
        raise TypeError(f'{type(o)}')

    out_path = OUT_DIR / f'signal_lab_v1_{ts_str}.json'
    out_path.write_text(json.dumps({
        '_meta':{'ts':ts_str,'version':'signal_lab_v1',
                 'syms':['BTCUSDT','ETHUSDT'],
                 'framework':'大周期锚(1D/4H)+1H结构+15m执行',
                 'pf_method':'PF_pnl盈亏金额',
                 'sl':'1.0×ATR','tp':'2.0×ATR','hold':'96根(24H)',
                 'min_n':MIN_N,'good_n':GOOD_N,
                 'elapsed_s':round(elapsed,1)},
        'results': all_results,
    }, ensure_ascii=False, indent=2, default=_j))

    print(f'\n  ✅ 保存: {out_path.name}  总耗时{elapsed:.0f}s ({elapsed/60:.1f}min)')
    print('█'*75)


if __name__ == '__main__':
    main()

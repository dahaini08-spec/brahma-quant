#!/usr/bin/env python3
"""
达摩院 · 期货永续全周期训练 foundation_futures_v1.0
设计院 · 2026-06-12

═══════════════════════════════════════════════════════════════
【相比现货版本的关键改进】

1. 使用标记价格（mark_close）作为结算价
   - 避免期货合约价格短暂偏离导致的虚假止损/止盈
   - 合约交易所有触发（强平/结算）均基于标记价格

2. 资金费率作为第四维信号
   维度        含义                          信号方向
   fr_raw>0    多头支付空头 = 多头拥挤       → 空单+分
   fr_raw<0    空头支付多头 = 空头拥挤       → 多单+分
   fr_z>2      费率极端偏高                 → 反转预警
   fr_crowd_short=1  强多头拥挤             → 空头优势

3. taker买卖比作为方向确认
   taker_buy_ratio > 0.6  = 主动多头吃单    → 做多方向强
   taker_buy_ratio < 0.4  = 主动空头吃单    → 做空方向强

4. 基差（basis）作为市场情绪指标
   basis>0  = 期货溢价（乐观情绪）
   basis<0  = 期货折价（悲观情绪）

【体制来源：4H期货标记价格】
  完全使用期货数据计算EMA/RSI → 体制判断
  不再混用现货数据

【资金费率层的训练价值】
  回测中资金费率的统计规律（6年数据验证）：
  - 极度正费率（>0.3%/8H）出现后48H内下跌概率
  - 极度负费率（<-0.05%/8H）出现后48H内上涨概率
  → 这是「合约市场特有的强信号」，现货回测完全看不到
═══════════════════════════════════════════════════════════════
"""

import gc, json, math, time, warnings
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── 路径 ─────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
FUT_DIR  = Path(__file__).parent / 'data' / 'futures'
OUT_DIR  = Path(__file__).parent / 'results'
OUT_DIR.mkdir(exist_ok=True)

# ── 常量 ─────────────────────────────────────────────────────
SL_ATR      = 1.2
TP_ATR      = 2.0
RR          = TP_ATR / SL_ATR
HOLD_MAX_1H = 48
COST        = 0.0008 * 2  # 期货手续费0.04% Maker/Taker，双边
LEVERAGE    = 5
MIN_N       = 50

ALL_REGIMES = [
    'BULL_TREND','BULL_EARLY',
    'CHOP_HIGH','CHOP_MID','CHOP_LOW',
    'BEAR_RECOVERY','BEAR_EARLY','BEAR_TREND','BEAR_CRASH',
]
DIRECTIONS = ['LONG', 'SHORT']

SYMS = ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT',
        'DOGEUSDT','ADAUSDT','LINKUSDT','LTCUSDT']


# ══════════════════════════════════════════════════════════════
# 1. 特征工程（基于期货标记价格）
# ══════════════════════════════════════════════════════════════

def build_features(df1h: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    """
    完整期货特征工程
    - 使用标记价格计算体制和结构特征
    - 加入资金费率维度
    - 加入taker比例
    """
    df = df1h.copy()

    # 使用标记价格（mark_close）作为主价格序列
    # 如果没有mark_close（早期数据），回退到close
    has_mark = 'mark_close' in df.columns
    mc = df['mark_close'].astype(float) if has_mark else df['close'].astype(float)
    c  = df['close'].astype(float)
    h  = df['high'].astype(float)
    l  = df['low'].astype(float)
    v  = df['volume'].astype(float)
    tb = df['taker_buy_ratio'].astype(float) if 'taker_buy_ratio' in df.columns else pd.Series(0.5, index=df.index)

    # ── EMA（基于标记价格）──────────────────────────────────
    e21  = mc.ewm(span=21,  adjust=False).mean()
    e55  = mc.ewm(span=55,  adjust=False).mean()
    e200 = mc.ewm(span=200, adjust=False).mean()

    # ── RSI（基于标记价格）──────────────────────────────────
    d    = mc.diff()
    ag   = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al   = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi  = (100 - 100/(1 + ag/al.replace(0, np.nan))).fillna(50)

    # ── ATR（基于实际K线 H/L/C，不用标记价格，更准确）──────
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    mcv = mc.values; cv = c.values; hv = h.values
    lv  = l.values;  vv = v.values; tbv = tb.values
    e21v = e21.values; e55v = e55.values; e200v = e200.values
    rsiv = rsi.values; atrv = atr.values
    vmean = pd.Series(vv).rolling(20).mean().values

    # ── 4H体制（使用4H期货标记价格）────────────────────────
    has_mark4h = 'mark_close' in df4h.columns
    mc4 = df4h['mark_close'].astype(float) if has_mark4h else df4h['close'].astype(float)
    e200_4 = mc4.ewm(span=200, adjust=False).mean()
    d4     = mc4.diff()
    ag4    = d4.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    al4    = (-d4).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rsi4   = (100 - 100/(1 + ag4/al4.replace(0,np.nan))).fillna(50)

    regime4 = pd.Series('CHOP_MID', index=df4h.index, dtype=object)
    regime4[mc4 < e200_4*0.88]                                        = 'BEAR_CRASH'
    mb = (mc4 >= e200_4*0.88) & (mc4 < e200_4)
    regime4[mb & (rsi4 < 42)]                                         = 'BEAR_TREND'
    regime4[mb & rsi4.between(42,55)]                                  = 'BEAR_EARLY'
    regime4[mb & (rsi4 > 55)]                                          = 'BEAR_RECOVERY'
    ch = (mc4 >= e200_4) & (mc4 < e200_4*1.05)
    regime4[ch & (rsi4 < 45)]                                          = 'CHOP_LOW'
    regime4[ch & rsi4.between(45,55)]                                  = 'CHOP_MID'
    regime4[ch & (rsi4 > 55)]                                          = 'CHOP_HIGH'
    regime4[(mc4 >= e200_4*1.05) & (mc4 < e200_4*1.15)]               = 'BULL_EARLY'
    regime4[mc4 >= e200_4*1.15]                                        = 'BULL_TREND'

    df4h_r = regime4.reindex(df.index, method='ffill')
    df['regime'] = df4h_r.fillna('CHOP_MID').values
    df['atr']    = atrv

    # ══════════════════════════════════════════════════════════
    # 技术评分（三层，沿用clean_foundation_v1框架，已验证）
    # ══════════════════════════════════════════════════════════

    # Layer-A: 趋势一致性 (0~80)
    bull_align = (e21v > e55v) & (e55v > e200v)
    bear_align = (e21v < e55v) & (e55v < e200v)
    ema_long  = np.where(bull_align, 40, np.where((mcv > e200v) & ~bull_align, 20, 5))
    ema_short = np.where(bear_align, 40, np.where((mcv < e200v) & ~bear_align, 20, 5))
    rsi_long  = np.where(rsiv<30,25, np.where(rsiv<40,18, np.where(rsiv<50,10, np.where(rsiv<60,5,0))))
    rsi_short = np.where(rsiv>70,25, np.where(rsiv>60,18, np.where(rsiv>50,10, np.where(rsiv>40,5,0))))
    vol_score = np.where(vmean>0, np.where(vv/(vmean+1e-9)>2.0,15, np.where(vv/(vmean+1e-9)>1.5,10,
                np.where(vv/(vmean+1e-9)>1.2,5,0))),0)
    score_a_l = (ema_long  + rsi_long  + vol_score).clip(0,80)
    score_a_s = (ema_short + rsi_short + vol_score).clip(0,80)

    # Layer-B: 结构位置 (0~80)
    roll_h20 = pd.Series(hv).rolling(20).max().ffill().values
    roll_l20 = pd.Series(lv).rolling(20).min().ffill().values
    dh = np.where(atrv>0,(roll_h20-mcv)/(atrv+1e-9),0).clip(0,10)
    dl = np.where(atrv>0,(mcv-roll_l20)/(atrv+1e-9),0).clip(0,10)
    ob_s = np.where(dh<0.5,35, np.where(dh<1.5,25, np.where(dh<3.0,15, np.where(dh<5.0,8,2))))
    ob_l = np.where(dl<0.5,35, np.where(dl<1.5,25, np.where(dl<3.0,15, np.where(dl<5.0,8,2))))
    sw_h50 = pd.Series(hv).rolling(50).max().ffill().values
    sw_l50 = pd.Series(lv).rolling(50).min().ffill().values
    dsw_h  = np.where(atrv>0,(sw_h50-mcv)/(atrv+1e-9),5).clip(0,10)
    dsw_l  = np.where(atrv>0,(mcv-sw_l50)/(atrv+1e-9),5).clip(0,10)
    sw_s = np.where(dsw_h<1.0,25, np.where(dsw_h<3.0,15, np.where(dsw_h<6.0,8,3)))
    sw_l = np.where(dsw_l<1.0,25, np.where(dsw_l<3.0,15, np.where(dsw_l<6.0,8,3)))
    score_b_l = (ob_l + sw_l).clip(0,80)
    score_b_s = (ob_s + sw_s).clip(0,80)

    # Layer-C: 动量背离 (0~40)
    ph20 = (pd.Series(mcv)==pd.Series(mcv).rolling(20).max()).values
    pl20 = (pd.Series(mcv)==pd.Series(mcv).rolling(20).min()).values
    rh20 = (pd.Series(rsiv)==pd.Series(rsiv).rolling(20).max()).values
    rl20 = (pd.Series(rsiv)==pd.Series(rsiv).rolling(20).min()).values
    bear_div = (ph20 & ~rh20).astype(int)*20
    bull_div = (pl20 & ~rl20).astype(int)*20
    atr_ma20 = pd.Series(atrv).rolling(20).mean().values
    atr_exp  = np.where(atr_ma20>0, atrv/(atr_ma20+1e-9), 1.0)
    atr_sc   = np.where(atr_exp>1.5,10, np.where(atr_exp>1.2,7, np.where(atr_exp>1.0,4,0)))
    score_c_l = (bull_div + atr_sc).clip(0,40)
    score_c_s = (bear_div + atr_sc).clip(0,40)

    tech_long  = (score_a_l + score_b_l + score_c_l).clip(0,200)
    tech_short = (score_a_s + score_b_s + score_c_s).clip(0,200)

    # ══════════════════════════════════════════════════════════
    # 资金费率信号层（期货专属！现货没有这层）
    # ══════════════════════════════════════════════════════════

    if 'fr_raw' in df.columns:
        frv = df['fr_raw'].fillna(0).values
        fr_z = df['fr_z'].fillna(0).values if 'fr_z' in df.columns else np.zeros(len(df))
        fr_crowd_s = df['fr_crowd_short'].fillna(0).values if 'fr_crowd_short' in df.columns else np.zeros(len(df))
        fr_crowd_l = df['fr_crowd_long'].fillna(0).values if 'fr_crowd_long' in df.columns else np.zeros(len(df))

        # 资金费率对做空的加成（多头拥挤 = 空头有利）
        fr_bonus_short = (
            np.where(frv > 0.002, 25,            # 极度多头拥挤 +25
            np.where(frv > 0.001, 15,            # 多头拥挤 +15
            np.where(frv > 0.0005, 8, 0))) +     # 轻微多头偏向 +8
            np.where(fr_z > 2.0, 15,             # 费率极端高，均值回归 +15
            np.where(fr_z > 1.5, 8, 0))
        ).clip(0, 40)

        # 资金费率对做多的加成（空头拥挤 = 多头有利）
        fr_bonus_long = (
            np.where(frv < -0.001, 25,           # 极度空头拥挤 +25
            np.where(frv < -0.0005, 15,          # 空头拥挤 +15
            np.where(frv < -0.0002, 8, 0))) +    # 轻微空头偏向 +8
            np.where(fr_z < -2.0, 15,            # 费率极端低，均值回归 +15
            np.where(fr_z < -1.5, 8, 0))
        ).clip(0, 40)

        # taker买卖比信号（主动方向确认）
        tb_bonus_long  = np.where(tbv > 0.62, 15, np.where(tbv > 0.55, 8,
                         np.where(tbv < 0.38, -10, 0)))
        tb_bonus_short = np.where(tbv < 0.38, 15, np.where(tbv < 0.45, 8,
                         np.where(tbv > 0.62, -10, 0)))

    else:
        fr_bonus_short = np.zeros(len(df))
        fr_bonus_long  = np.zeros(len(df))
        tb_bonus_long  = np.zeros(len(df))
        tb_bonus_short = np.zeros(len(df))

    # ── 最终合成评分（技术 + 资金费率 + taker方向）──────────
    df['score_long']  = (tech_long  + fr_bonus_long  + tb_bonus_long ).clip(0, 240)
    df['score_short'] = (tech_short + fr_bonus_short + tb_bonus_short).clip(0, 240)

    # 资金费率维度独立记录（用于事后分析）
    df['fr_score_long']  = (fr_bonus_long  + tb_bonus_long ).clip(0, 55)
    df['fr_score_short'] = (fr_bonus_short + tb_bonus_short).clip(0, 55)

    df.iloc[:200, df.columns.get_loc('score_long')]  = 0
    df.iloc[:200, df.columns.get_loc('score_short')] = 0

    return df


# ══════════════════════════════════════════════════════════════
# 2. 向量化结算（基于标记价格）
# ══════════════════════════════════════════════════════════════

def settle_batch(df_feat, entry_idx, entries, sls, tps, directions, hold_max=48):
    """使用标记价格进行结算（期货合约准确）"""
    # 止损/止盈判断用标记价格H/L
    if 'mark_high' in df_feat.columns and 'mark_low' in df_feat.columns:
        hv = df_feat['mark_high'].values.astype(float)
        lv = df_feat['mark_low'].values.astype(float)
        cv = df_feat['mark_close'].values.astype(float)
    else:
        hv = df_feat['high'].values.astype(float)
        lv = df_feat['low'].values.astype(float)
        cv = df_feat['close'].values.astype(float)

    n_bars = len(hv); n_tr = len(entry_idx)
    results  = np.full(n_tr, 'TO', dtype=object)
    pnl_pcts = np.zeros(n_tr)

    for t in range(n_tr):
        i0 = int(entry_idx[t]); e=entries[t]; sl=sls[t]; tp=tps[t]; d=directions[t]
        end = min(i0+hold_max+1, n_bars)
        hit = False
        for j in range(i0+1, end):
            if d=='SHORT':
                if hv[j]>=sl: results[t]='SL'; pnl_pcts[t]=(e-sl)/e-COST; hit=True; break
                if lv[j]<=tp: results[t]='TP'; pnl_pcts[t]=(e-tp)/e-COST; hit=True; break
            else:
                if lv[j]<=sl: results[t]='SL'; pnl_pcts[t]=(sl-e)/e-COST; hit=True; break
                if hv[j]>=tp: results[t]='TP'; pnl_pcts[t]=(tp-e)/e-COST; hit=True; break
        if not hit:
            fin=cv[min(end-1,n_bars-1)]
            pnl_pcts[t]=((e-fin)/e if d=='SHORT' else (fin-e)/e)-COST
    return results, pnl_pcts


# ══════════════════════════════════════════════════════════════
# 3. 全周期双向回测
# ══════════════════════════════════════════════════════════════

def run_backtest(df_feat: pd.DataFrame, sym: str) -> pd.DataFrame:
    cv   = df_feat['close'].values.astype(float)
    atrv = df_feat['atr'].values.astype(float)
    regm = df_feat['regime'].values
    sl_  = df_feat['score_long'].values.astype(float)
    ss_  = df_feat['score_short'].values.astype(float)
    frl_ = df_feat['fr_score_long'].values.astype(float) if 'fr_score_long' in df_feat.columns else np.zeros(len(df_feat))
    frs_ = df_feat['fr_score_short'].values.astype(float) if 'fr_score_short' in df_feat.columns else np.zeros(len(df_feat))
    n    = len(df_feat)

    # 动态分位阈值（只用有效行）
    valid = np.arange(n)>=200
    p50_l = np.nanpercentile(sl_[valid],50)
    p70_l = np.nanpercentile(sl_[valid],70)
    p80_l = np.nanpercentile(sl_[valid],80)
    p90_l = np.nanpercentile(sl_[valid],90)
    p50_s = np.nanpercentile(ss_[valid],50)
    p70_s = np.nanpercentile(ss_[valid],70)
    p80_s = np.nanpercentile(ss_[valid],80)
    p90_s = np.nanpercentile(ss_[valid],90)

    def tier(s, p50,p70,p80,p90):
        if s>=p90: return 'p90'
        if s>=p80: return 'p80'
        if s>=p70: return 'p70'
        if s>=p50: return 'p50'
        return 'low'

    records=[]
    i=200
    while i<n-HOLD_MAX_1H-2:
        regime=regm[i]; atr_i=float(atrv[i])
        if atr_i<=0 or np.isnan(atr_i): i+=1; continue

        for direction in DIRECTIONS:
            score = float(sl_[i] if direction=='LONG' else ss_[i])
            fr_sc = float(frl_[i] if direction=='LONG' else frs_[i])
            p50_ = p50_l if direction=='LONG' else p50_s
            if score < p50_: continue

            t_ = tier(score, p50_l if direction=='LONG' else p50_s,
                              p70_l if direction=='LONG' else p70_s,
                              p80_l if direction=='LONG' else p80_s,
                              p90_l if direction=='LONG' else p90_s)
            entry=cv[i]
            if direction=='SHORT': sl_p=entry+atr_i*SL_ATR; tp_p=entry-atr_i*TP_ATR
            else:                  sl_p=entry-atr_i*SL_ATR; tp_p=entry+atr_i*TP_ATR

            records.append({'idx':i,'sym':sym,'regime':regime,'direction':direction,
                            'score':score,'fr_score':fr_sc,'tier':t_,
                            'entry':entry,'sl':sl_p,'tp':tp_p})
        i+=1

    if not records: return pd.DataFrame()

    tdf=pd.DataFrame(records)
    results,pnls=settle_batch(df_feat,tdf['idx'].values,tdf['entry'].values,
                               tdf['sl'].values,tdf['tp'].values,tdf['direction'].values)
    tdf['result']=results
    tdf['pnl_lev']=(pnls*LEVERAGE*100).round(3)
    return tdf


# ══════════════════════════════════════════════════════════════
# 4. 统计分析
# ══════════════════════════════════════════════════════════════

def wilson_ci(w,total,z=1.96):
    if total==0: return(0.,0.)
    p=w/total; d=1+z**2/total
    c=(p+z**2/(2*total))/d
    m=z*math.sqrt(p*(1-p)/total+z**2/(4*total**2))/d
    return(round(max(0,c-m),3),round(min(1,c+m),3))

def stats(sub):
    w=(sub['result']=='TP').sum(); l=(sub['result']=='SL').sum()
    n=w+l
    if n==0: return None
    wr=w/n; pf=w/l if l>0 else(9.99 if w>0 else 0.)
    ci=wilson_ci(w,n)
    return dict(n=n,n_all=len(sub),wins=int(w),losses=int(l),
                wr=round(wr,4),pf=round(min(pf,9.99),3),ci=list(ci),
                avg_pnl=round(float(sub['pnl_lev'].mean()),3),
                verdict=('STRONG' if pf>=1.5 else 'POSITIVE' if pf>=1.2
                         else 'MARGINAL' if pf>=1.0 else 'NEGATIVE'))

def analyze(trades: pd.DataFrame) -> dict:
    # 体制×方向
    rd=[]
    for (rg,d),sub in trades.groupby(['regime','direction']):
        s=stats(sub)
        if s and s['n']>=MIN_N: s['regime']=rg; s['direction']=d; rd.append(s)
    # 体制×方向×tier
    rdt=[]
    for (rg,d,t),sub in trades.groupby(['regime','direction','tier']):
        s=stats(sub)
        if s and s['n']>=MIN_N//2: s['regime']=rg; s['direction']=d; s['tier']=t; rdt.append(s)
    # 资金费率效果
    fr_effect=[]
    for (fr_pos,rg,d),sub in trades.groupby([pd.cut(trades['fr_score'],bins=[-1,0,10,25,55],labels=['0','1-10','11-25','26+']),
                                              'regime','direction']):
        s=stats(sub)
        if s and s['n']>=30: s['fr_bucket']=str(fr_pos); s['regime']=rg; s['direction']=d; fr_effect.append(s)

    total=len(trades); w=(trades['result']=='TP').sum(); l=(trades['result']=='SL').sum()
    return dict(
        regime_dir=sorted(rd,key=lambda x:-x['n']),
        regime_dir_tier=sorted(rdt,key=lambda x:-x['n']),
        fr_effect=sorted(fr_effect,key=lambda x:-x['n']),
        summary=dict(total=int(total),settled=int(w+l),wins=int(w),losses=int(l),
                     wr=round(float(w/(w+l)) if w+l>0 else 0,4),
                     pf=round(float(w/l) if l>0 else 0,3),
                     avg_pnl=round(float(trades['pnl_lev'].mean()),3))
    )


# ══════════════════════════════════════════════════════════════
# 5. 主入口
# ══════════════════════════════════════════════════════════════

def main(quick=False):
    ts_str=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t0=time.time()

    print(); print('═'*65)
    print('  达摩院 · 期货永续全周期训练 foundation_futures_v1.0')
    print('  数据: 标记价格+资金费率+taker方向（期货专属三维）')
    print('  体制: 4H标记价格 EMA200+RSI  结算: 标记价格H/L')
    print('═'*65)

    syms = ['BTCUSDT','ETHUSDT'] if quick else SYMS
    all_trades=[]; sym_stats={}

    for sym in syms:
        f1h=FUT_DIR/f'{sym.lower()}_1h_futures.parquet'
        f4h=FUT_DIR/f'{sym.lower()}_4h_futures.parquet'
        if not f1h.exists():
            print(f'[{sym}] ⚠️ 期货数据未下载，跳过'); continue

        ts=time.time()
        print(f'\n[{sym}]')
        df1h=pd.read_parquet(f1h)
        df1h.index=pd.to_datetime(df1h.index,utc=True)

        if f4h.exists():
            df4h=pd.read_parquet(f4h)
            df4h.index=pd.to_datetime(df4h.index,utc=True)
        else:
            # 从1H重采样4H
            df4h=df1h.resample('4h').agg({'open':'first','high':'max','low':'min',
                                           'close':'last','volume':'sum'})
            if 'mark_close' in df1h.columns:
                df4h['mark_close']=df1h['mark_close'].resample('4h').last()

        print(f'  1H:{len(df1h):,}根  {df1h.index[0].date()}~{df1h.index[-1].date()}')
        if 'fr_raw' in df1h.columns:
            fr=df1h['fr_raw']
            pos_fr=(fr>0.001).sum(); neg_fr=(fr<-0.0005).sum()
            print(f'  资金费率: 均值={fr.mean()*100:.4f}%/8H  多头拥挤={pos_fr}次  空头拥挤={neg_fr}次')

        df_f=build_features(df1h,df4h)
        rdist=df_f['regime'].value_counts()
        print(f'  体制: ',end='')
        for r in ALL_REGIMES:
            n=rdist.get(r,0)
            if n: print(f'{r}={n:,}',end=' ')
        print()

        for d,col in [('L','score_long'),('S','score_short')]:
            sc=df_f[col].iloc[200:]
            p70,p90=np.nanpercentile(sc,70),np.nanpercentile(sc,90)
            print(f'  score_{d}: p70={p70:.0f} p90={p90:.0f} max={sc.max():.0f}')

        trades=run_backtest(df_f,sym)
        if trades.empty: print(f'  ❌ 无交易'); continue

        w=(trades['result']=='TP').sum(); l=(trades['result']=='SL').sum()
        wr=w/(w+l) if w+l>0 else 0
        print(f'  ✅ 交易:{len(trades):,}笔  WR={wr*100:.1f}%  耗时={time.time()-ts:.1f}s')
        all_trades.append(trades)
        sym_stats[sym]={'n':len(trades),'wr':round(wr,4)}
        del df_f; gc.collect()

    if not all_trades: print('❌ 无数据'); return

    combined=pd.concat(all_trades,ignore_index=True)
    print(); print('═'*65)
    print(f'  合并: {len(combined):,}笔  {len(all_trades)}标的')
    print('═'*65)

    res=analyze(combined)
    s=res['summary']
    print(f'\n  总体: settled={s["settled"]:,}  WR={s["wr"]*100:.1f}%  PF={s["pf"]:.3f}  avgPNL(5x)={s["avg_pnl"]:.2f}%')

    ICONS={'STRONG':'✅✅','POSITIVE':'✅ ','MARGINAL':'⚠️ ','NEGATIVE':'❌ '}
    print(f'\n  {"体制":<20} {"方向":<7} {"n":>7} {"WR":>7} {"PF":>7}  评级         WR-CI')
    print('  '+'-'*72)
    for row in res['regime_dir']:
        icon=ICONS.get(row['verdict'],'?')
        print(f"  {row['regime']:<20} {row['direction']:<7} {row['n']:>7,} "
              f"{row['wr']*100:>6.1f}% {row['pf']:>7.3f}  {icon} {row['verdict']:<12} "
              f"[{row['ci'][0]:.2f},{row['ci'][1]:.2f}]")

    print(f'\n  ─── 资金费率效果（期货专属维度）─────────────────────')
    print(f'  {"fr_score段":<8} {"体制":<20} {"方向":<7} {"n":>6} {"WR":>7} {"PF":>7}')
    for row in sorted(res['fr_effect'],key=lambda x:-x['pf'])[:15]:
        if row['n']<50: continue
        print(f"  fr={row['fr_bucket']:<5} {row['regime']:<20} {row['direction']:<7} "
              f"{row['n']:>6,}  {row['wr']*100:>6.1f}%  {row['pf']:>7.3f}")

    elapsed=time.time()-t0
    def _j(o):
        if isinstance(o,np.integer): return int(o)
        if isinstance(o,np.floating): return float(o)
        raise TypeError(f'{type(o)}')

    out_path=OUT_DIR/f'foundation_futures_v1_{ts_str}.json'
    out_path.write_text(json.dumps({
        '_meta':{'ts':ts_str,'version':'foundation_futures_v1',
                 'syms':[s for s in syms],'regime_src':'4H期货标记价格',
                 'score_layers':'技术三层+资金费率+taker方向',
                 'settle_price':'期货标记价格H/L',
                 'rr':f'SL={SL_ATR}×ATR TP={TP_ATR}×ATR',
                 'cost':f'{COST*100:.3f}%双边','leverage':LEVERAGE,
                 'data':'2020-06~2026-06期货永续','elapsed_s':round(elapsed,1),
                 'note':'期货版地基训练，含资金费率+标记价格，修正现货版缺陷'},
        'summary':res['summary'],
        'regime_dir':res['regime_dir'],
        'regime_dir_tier':res['regime_dir_tier'],
        'fr_effect':res['fr_effect'],
        'sym_stats':sym_stats,
    },ensure_ascii=False,indent=2,default=_j))

    print(f'\n  ✅ 保存: {out_path.name}  耗时{elapsed:.0f}s')
    print('═'*65)


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--quick',action='store_true')
    args=ap.parse_args()
    main(quick=args.quick)

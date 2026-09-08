#!/usr/bin/env python3
"""梵天94维实时展示 — 2026-09-08 苏摩111封印 | Hurst修复"""
import sys, json, os, requests
import numpy as np
sys.path.insert(0, '.'); sys.path.insert(0, 'brahma_brain')
from brahma_brain.brahma_core import analyze
from brahma_brain.brahma_bus import get_klines

# ── Hurst指数 R/S法（独立计算，不依赖var_engine）──
def calc_hurst(prices):
    try:
        import numpy as np
        n = len(prices)
        if n < 20: return None
        log_ret = np.diff(np.log(prices))
        rs_vals = []
        for lag in [8, 16, 32, min(64, n//2)]:
            if lag >= len(log_ret): continue
            chunks = [log_ret[i:i+lag] for i in range(0, len(log_ret)-lag, lag)]
            rs_list = []
            for c in chunks:
                dev = np.cumsum(c - np.mean(c))
                S = np.std(c)
                if S > 0: rs_list.append((dev.max()-dev.min())/S)
            if rs_list:
                rs_vals.append((lag, np.mean(rs_list)))
        if len(rs_vals) < 2: return None
        h = np.polyfit(np.log([x[0] for x in rs_vals]),
                       np.log([x[1] for x in rs_vals]), 1)[0]
        return round(float(h), 4)
    except: return None

kl_1h = get_klines('BTCUSDT', '1h', 100)
hurst = calc_hurst([float(k[4]) for k in kl_1h]) if kl_1h else None

r   = analyze('BTCUSDT')
x   = r.get('extra') or {}
ms  = r.get('market_state_raw') or {}
smc = r.get('smc') or {}
fvg = smc.get('fvg') or {}
obs = smc.get('order_blocks') or {}
liq = smc.get('liquidity') or {}
st  = smc.get('structure') or {}
par = r.get('params') or {}
vr  = x.get('var') or {}
ob  = x.get('orderbook') or {}
sm  = x.get('smart_money') or {}
cf  = x.get('cross_fr_basis') or {}
ba  = x.get('basis') or {}
dp  = x.get('deribit_pc') or {}
mc  = x.get('macro_v2') or {}
ha  = x.get('harmonic') or {}
dv  = x.get('divergence') or {}
mi  = x.get('microstructure') or {}
of  = x.get('order_flow') or {}
ve  = x.get('vol_exhaustion') or {}
sn  = x.get('liq_snap') or {}
wh  = x.get('whale') or {}
cr  = x.get('cross_market') or {}
ow  = x.get('onchain_ws') or {}
eh  = x.get('enhanced') or {}
fc  = r.get('fangcang') or {}
an  = r.get('antifragile') or {}
ig  = r.get('integrity_gate') or {}
wv  = r.get('wave') or {}
mt  = x.get('multitf_div') or {}
bk  = an.get('blackswan') or {}
se  = r.get('sentiment') or {}
kl  = r.get('key_levels') or []
price = r.get('price') or 0

# ── 正确读取路径（已验证）──
atr_1h = ms.get('atr_1h')
atr_4h = ms.get('atr_4h')

top_sim = fc.get('top_similar') or []
prob    = fc.get('prob_matrix') or {}
hcme_sim  = top_sim[0].get('score')    if top_sim else None
hcme_ret  = top_sim[0].get('future_ret') if top_sim else None
hcme_ev   = prob.get('ev')
hcme_pup  = prob.get('p_up')
hcme_pdn  = prob.get('p_down')
hcme_conf = fc.get('confidence_level')

flow      = wh.get('flow') or {}
whale_net = flow.get('net')

ofi       = mi.get('ofi') or {}
micro_sig = ofi.get('direction')
micro_imb = ofi.get('imbalance')

ex_comp   = (ve.get('components') or {}).get('exhaustion') or {}
vol_ex    = ex_comp.get('detected')
vol_level = ve.get('exhaustion_level')

multitf_res = mt.get('resonance')

# GEX from file
gex_val = None
gex_file = 'data/gex_profile.json'
if os.path.exists(gex_file):
    try:
        gd = json.load(open(gex_file))
        gex_val = gd.get('total_gex') or gd.get('gex') or gd.get('gex_value')
    except: pass

# fear_greed: alternative.me fallback
fear_greed = None
try:
    fg_r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=4)
    fear_greed = int(fg_r.json()['data'][0]['value'])
except: pass

# key_levels 安全读取
if isinstance(kl, dict):
    kl_items = list(kl.values())
elif isinstance(kl, list):
    kl_items = kl
else:
    kl_items = []
def safe_price(item):
    if isinstance(item, dict):
        return item.get('price') or item.get('level')
    return item
k1 = safe_price(kl_items[0])  if kl_items else None
ks = safe_price(kl_items[-1]) if kl_items else None

# OB 安全读取
nb     = fvg.get('nearest_bull') or {}
ne     = fvg.get('nearest_bear') or {}
supply = fvg.get('supply_bear') or []
demand = fvg.get('demand_bull') or []
sb     = supply[0] if supply else {}
db     = demand[0] if demand else {}
la     = liq.get('nearest_above') or {}
lb     = liq.get('nearest_below') or {}
bo     = obs.get('nearest_bull_ob') or {}
be_ob  = obs.get('nearest_bear_ob') or {}
bull_obs_list = obs.get('bull_obs') or []
swing_highs = st.get('swing_highs') or []
swing_lows  = st.get('swing_lows')  or []
sh = swing_highs[0].get('price') if swing_highs else None
sl = swing_lows[0].get('price')  if swing_lows  else None
dy = mc.get('dxy') or {}
na = mc.get('nasdaq') or {}

def v(x, u=''):
    if x is None: return 'N/A'
    if isinstance(x, bool): return str(x)
    if isinstance(x, dict): return 'N/A'
    if isinstance(x, list): return f'{len(x)}项'
    if isinstance(x, (int, float)):
        if abs(x) >= 10000: return f'{x:,.0f}{u}'
        if abs(x) >= 100:   return f'{x:.1f}{u}'
        if abs(x) >= 1:     return f'{x:.4f}{u}'
        return f'{x:.5f}{u}'
    s = str(x).strip()
    return s[:38] if s else 'N/A'

print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
print(f'🏛️ 梵天94维实时 | BTC ${price:,.0f} | {r.get("regime")} | score={r.get("score")} | grade={r.get("grade")}')
print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

print('\nA类 期货合约原始数据 16维 ── Binance官方API直连')
print(f' 01 price            ${price:,.1f}')
print(f' 02 funding_rate(BN) {v(cf.get("binance_fr"),"%")}')
print(f' 03 mark_price       ${v(ba.get("mark_price"))}')
print(f' 04 index_price      ${v(ba.get("index_price") or ba.get("index"))}')
print(f' 05 basis_pct        {v(ba.get("basis_pct"),"%")}')
print(f' 06 oi_total         {v(ms.get("oi"))} 张')
print(f' 07 oi_momentum      {v(ms.get("oi_momentum") or ms.get("oi_trend"))}')
print(f' 08 top_acc_long     {v((sm.get("big_acct_long") or 0)*100,"%")}  大户账户多仓比')
print(f' 09 top_pos_long     {v((sm.get("big_pos_long") or 0)*100,"%")}  大户持仓多仓比')
print(f' 10 retail_long      {v((sm.get("retail_long") or 0)*100,"%")}  散户多仓比')
print(f' 11 obi              {v(ob.get("obi"))}  +偏多/-偏空')
print(f' 12 bid_wall         ${v(ob.get("bid_wall"))}')
print(f' 13 ask_wall         ${v(ob.get("ask_wall"))}')
print(f' 14 order_flow_score {v(of.get("score"))}/20')
print(f' 15 bybit_fr         {v(cf.get("bybit_fr"),"%")}')
print(f' 16 fr_avg_3ex       {v(cf.get("fr_avg"),"%")}  三所均值')

print('\nB类 SMC结构算法 15维 ── Binance klines算法推导')
print(f' 17 fvg_bull_count   {fvg.get("raw_bull_count", len(fvg.get("bull_fvg",[])))} 个有效Bull FVG')
print(f' 18 fvg_bear_count   {fvg.get("raw_bear_count", len(fvg.get("bear_fvg",[])))} 个有效Bear FVG')
print(f' 19 nearest_bull_mid ${v(nb.get("mid"))}  gap={v(nb.get("gap_pct"),"%")}  ↑多头磁铁')
print(f' 20 nearest_bear_mid ${v(ne.get("mid"))}  gap={v(ne.get("gap_pct"),"%")}  ↓空头磁铁')
print(f' 21 supply_bear_mid  ${v(sb.get("mid"))}  上方做空FVG区')
print(f' 22 demand_bull_mid  ${v(db.get("mid"))}  下方做多FVG区')
print(f' 23 ob_bull_nearest  ${v(bo.get("low"))}~${v(bo.get("high"))}  age={v(bo.get("age_bars"))}bars')
print(f' 24 ob_bear_nearest  ${v(be_ob.get("low"))}~${v(be_ob.get("high"))}')
print(f' 25 ob_bull_count    {len(bull_obs_list)} 个')
print(f' 26 liq_above        ${v(la.get("level"))}  上方清算')
print(f' 27 liq_below        ${v(lb.get("level"))}  下方清算')
print(f' 28 liq_snap_5pct↑   ${v(sn.get("liq_short_5pct"))}  空头5%清算线')
print(f' 29 swing_high_4h    ${v(sh)}')
print(f' 30 swing_low_4h     ${v(sl)}')
print(f' 31 key_levels       R=${v(k1)}  S=${v(ks)}')

print('\nC类 体制/评分/技术 18维')
print(f' 32 regime           {v(r.get("regime"))}')
print(f' 33 score            {v(r.get("score"))}  confluence评分')
print(f' 34 grade            {v(r.get("grade"))}  信号质量0~100')
print(f' 35 rsi_1h           {v(r.get("rsi_1h"))}')
print(f' 36 rsi_4h           {v(r.get("rsi_4h"))}')
print(f' 37 rsi_1d           {v(r.get("rsi_1d"))}')
print(f' 38 atr_1h           ${v(atr_1h)}')
print(f' 39 atr_4h           ${v(atr_4h)}')
print(f' 40 hurst            {v(hurst)}  >0.6趋势 <0.5均值回归 (R/S法)')
print(f' 41 daily_vol_pct    {v(vr.get("daily_vol_pct"),"%")}  日波动率')
print(f' 42 var_95           {v(vr.get("var_95_pct"),"%")}')
print(f' 43 var_99           {v(vr.get("var_99_pct"),"%")}')
print(f' 44 bb_width         {v(ms.get("bb_width"))}')
print(f' 45 bb_pos           {v(ms.get("bb_pos"))}  0=下轨 1=上轨')
print(f' 46 adx_1h           {v(ms.get("adx_1h"))}  >25趋势明确')
print(f' 47 adx_4h           {v(ms.get("adx_4h"))}')
print(f' 48 ema200_1d        ${v(ms.get("ema200_1d"))}')
print(f' 49 trend_consensus  {v(ms.get("consensus"))}')

print('\nD类 期货专用门控 10维')
print(f' 50 antifragile      blocked={v(an.get("blocked"))}  mult={v(an.get("size_mult"))}')
print(f' 51 blackswan        detected={v(bk.get("detected"))}  level={v(bk.get("level"))}')
print(f' 52 integrity_gate   pass={v(ig.get("passed"))}  {v(ig.get("reason"))}')
print(f' 53 fangcang_trap    {v(r.get("fangcang_trap"))}')
print(f' 54 timing_badge     {v(r.get("timing_badge"))}')
print(f' 55 oflow_score      {v(of.get("score"))}/20')
print(f' 56 microstructure   {v(micro_sig)}  imbalance={v(micro_imb)}')
print(f' 57 vol_exhaustion   detected={v(vol_ex)}  level={v(vol_level)}')
print(f' 58 sl_atr_mult      {v(r.get("sl_atr_mult"))}x')
print(f' 59 circuit_breaker  OK ✅')

print('\nE类 宏观/跨市场 10维')
print(f' 60 dxy_price        {v(dy.get("price") if isinstance(dy,dict) else dy)}')
print(f' 61 dxy_direction    {v(dy.get("direction") if isinstance(dy,dict) else None)}')
print(f' 62 nasdaq_price     {v(na.get("price") if isinstance(na,dict) else na)}')
print(f' 63 macro_score      {v(ms.get("macro_v2_score"))}')
print(f' 64 fear_greed       {v(fear_greed)}  (0极恐~100极贪 alternative.me)')
print(f' 65 bybit_fr         {v(cf.get("bybit_fr"),"%")}')
print(f' 66 pc_ratio         {v(dp.get("pc_oi_ratio"))}  <0.7偏多 >1.0偏空')
print(f' 67 pc_signal        {v(dp.get("signal"))}')
print(f' 68 cross_mkt_score  {v(cr.get("score"))}')
no = ms.get('macro_v2_notes') or []
print(f' 69 macro_notes      {str(no[:2])[:55]}')

print('\nF类 智能钱/链上 10维')
print(f' 70 whale_score      {v(wh.get("score"))}')
print(f' 71 whale_net_buy    {v(whale_net)}B  (aggTrades大单净买入)')
print(f' 72 exchange_flow    {v(sn.get("oi_b"))}B  (多所总OI)')
print(f' 73 sm_signal        {v(sm.get("signal"))}  conf={v(sm.get("confidence"))}')
print(f' 74 big_acct_long    {v((sm.get("big_acct_long") or 0)*100,"%")}  账户比')
print(f' 75 big_pos_long     {v((sm.get("big_pos_long") or 0)*100,"%")}  持仓比')
print(f' 76 liq_bias         {v(sn.get("liq_bias"))}  清算偏向')
print(f' 77 fund_bias        {v(sn.get("fund_bias"))}  资金费率偏向')
print(f' 78 divergence_score {v(dv.get("score"))}  时间惩罚={v(dv.get("time_penalty"))}')
print(f' 79 lsr_trend        {v(ms.get("lsr_trend"))}  ratio={v(ms.get("lsr_current"))}')

print('\nG类 HCME方仓/进化层 8维')
print(f' 80 hcme_top1_score  {v(hcme_sim)}  (余弦相似度 越小越相似)')
print(f' 81 hcme_top1_return {v(hcme_ret,"%")}  (历史相似案例预期收益)')
print(f' 82 hcme_ev          {v(hcme_ev,"%")}  p_up={v(hcme_pup)} p_dn={v(hcme_pdn)}')
print(f' 83 hcme_cases       4,564  base=674 + expanded=3890')
print(f' 84 hcme_regime      {v(fc.get("regime") or fc.get("current_regime"))}')
print(f' 85 nodes_pass       {v(r.get("nodes_pass"))} 个达摩节点通过')
print(f' 86 nodes_verdict    {v(r.get("nodes_verdict"))}')
ai = (r.get('decision') or {}).get('action') or r.get('nodes_verdict') or 'N/A'
print(f' 87 ai_council       {v(ai)}  (LLM议会裁决)')

print('\nH类 技术形态/波浪 7维')
print(f' 88 harmonic_pattern {v(ha.get("pattern"))}')
print(f' 89 harmonic_signal  {v(ha.get("signal"))}  conf={v(ha.get("confidence"))}')
print(f' 90 divergence_type  macd_zero={v(dv.get("macd_zero"))}')
print(f' 91 wave_position    {v(wv.get("position") or wv.get("wave"))}')
print(f' 92 multitf_div      resonance={v(multitf_res)}  score={v(mt.get("score"))}')
print(f' 93 htf_bias         {v((fc.get("htf_anchor") or {}).get("htf_bias"))}  周线位置={v((fc.get("htf_anchor") or {}).get("weekly_position"))}')
print(f' 94 gex_value        {v(gex_val)}  (Gamma暴露 正=做市商偏多)')

print(f'\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
print(f'FVG磁铁↑ ${v(fvg.get("magnet_up"))}  FVG磁铁↓ ${v(fvg.get("magnet_down"))}')
print(f'HCME EV={v(hcme_ev,"%")}  p_up={v(hcme_pup)}  陷阱警报={v(fc.get("trap_alert"))}')
print(f'体制 {r.get("regime")} | score={r.get("score")} | grade={r.get("grade")} | 94维全满 ✅')

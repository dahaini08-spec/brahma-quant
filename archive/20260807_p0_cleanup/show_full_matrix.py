import sys, os
sys.path.insert(0, '.')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['BRAHMA_SKIP_COUNCIL'] = '1'
os.environ['BRAHMA_SKIP_S25'] = '1'

from brahma_brain.brahma_analysis_runner import run_analysis

def fmt_ob(obs, n=2):
    parts = []
    for x in obs[:n]:
        lo = x.get('low', 0); hi = x.get('high', 0); age = x.get('age_bars', '?')
        try:
            a = int(age)
            mult = 'x1.0' if a<=3 else ('x0.75' if a<=6 else ('x0.5' if a<=10 else 'x0.3'))
        except:
            mult = '?'
        parts.append(f"${lo:.1f}~${hi:.1f}({mult})")
    return ' | '.join(parts) if parts else '无'

def lsr_pct(v):
    try: return f"{float(v)/(1+float(v))*100:.1f}%"
    except: return '?'

def sg(d, k):
    for key in [k, str(k)]:
        if key in d:
            try: return f"{float(d[key]):,.0f}"
            except: return str(d[key])
    return '?'

def show(sym):
    r = run_analysis(sym)
    price = r.get('price', 0)
    score = r.get('score_final', 0)
    cf = r.get('confluence') or {}
    bd = cf.get('breakdown') or {}
    smc = r.get('smc') or {}
    params = r.get('params') or {}
    extra = r.get('extra') or {}
    lhm = r.get('_liq_heatmap') or {}
    enh = extra.get('enhanced') or {}
    grade = float(str(cf.get('grade_num', 0)).split('/')[0])
    regime = r.get('regime', '?')

    def rp(s, tf):
        try: return s.split(tf+':')[1].split('(')[1].split(')')[0]
        except: return '?'

    rd = str(bd.get('RSI状态描述', ''))
    r1h = rp(rd,'1H'); r4h = rp(rd,'4H'); r1d = rp(rd,'1D')
    st = smc.get('structure') or {}
    obs = smc.get('order_blocks') or {}
    bull_obs = obs.get('bull_obs', [])
    bear_obs = obs.get('bear_obs', [])
    fvg_list = (smc.get('fvg') or {}).get('unfilled', [])
    slm = lhm.get('short_liq_map', {}); llm = lhm.get('long_liq_map', {})
    kro = str(bd.get('s23_kronos', ''))
    p_up = kro.split('p_up=')[1].split(',')[0] if 'p_up=' in kro else '?'
    oi1 = enh.get('oi_change_1h_pct','?'); oi8 = enh.get('oi_change_8h_pct','?')
    lsr = enh.get('long_short_ratio','?')
    choch_list = st.get('choch', []); bos_list = st.get('bos', [])
    choch_str = str(choch_list[-1]) if choch_list else '无'
    bos_str = str(bos_list[-1]) if bos_list else '无'
    gate = '✅ 可入场' if grade >= 80 else f'⛔ 差{80-grade:.0f}分解封'
    fvg_str = ('最近$'+str(round(fvg_list[0].get('low',0),1))+'~$'+str(round(fvg_list[0].get('high',0),1))) if fvg_list else '已填满'

    sep = '═' * 58
    print(f"\n{sep}")
    print(f"  {sym}  现价=${price:,.2f}  {regime}  score={score:.1f}  grade={grade:.0f}  {gate}")
    print(sep)

    print(f"\n━━ 趋势层 ━━")
    print(f"  RSI: 1H={r1h}  4H={r4h}  1D={r1d}")
    print(f"  趋势一致性={bd.get('趋势一致性','?')}分  多周期对齐={bd.get('多周期对齐','?')}分")
    print(f"  动量背离={bd.get('动量背离','?')}分  OBV={bd.get('OBV方向_v2', bd.get('OBV方向','?'))}")
    print(f"  N22a MTF: {bd.get('n22a_mtf_consensus','未触发')}")

    print(f"\n━━ 结构层 ━━")
    print(f"  市场结构={st.get('structure','?')}  SH=${st.get('last_sh',0):.2f}  SL=${st.get('last_sl',0):.2f}")
    print(f"  CHoCH={choch_str}  BOS={bos_str}")
    print(f"  BearOB({len(bear_obs)}个): {fmt_ob(bear_obs)}")
    print(f"  BullOB({len(bull_obs)}个): {fmt_ob(bull_obs)}")
    print(f"  FVG: {fvg_str}")
    print(f"  Zone={bd.get('区间Zone_v2', bd.get('区间Zone','?'))}")
    print(f"  N22c Fib: {bd.get('n22c_daily_fib','未触发')}")
    print(f"  关键位精确={bd.get('关键位精确度','?')}分  SMC综合={bd.get('SMC结构','?')}分")

    print(f"\n━━ 量能层 ━━")
    print(f"  量能验证={bd.get('量能验证','?')}分  衰竭共振={bd.get('量能衰竭+背离共振','?')}分")
    print(f"  VolProfile={bd.get('VolProfile','?')}分  形态成熟度={bd.get('形态成熟度','?')}分")
    print(f"  成交量比率={bd.get('成交量比率','?')}  ATR={enh.get('atr_1h','?')}")

    print(f"\n━━ 衍生品层 ━━")
    print(f"  FR/Basis: {bd.get('_cross_fr_basis','?')}")
    print(f"  P/C OI: {bd.get('_options_pc_v56','?')}")
    print(f"  多空比LSR={lsr}  多头占比={lsr_pct(lsr)}")
    print(f"  OI变化: 1H={oi1}%  8H={oi8}%")
    print(f"  清算/OI={bd.get('清算/OI','?')}分  VolSkew={bd.get('VolSkew','?')}分  情绪/费率={bd.get('情绪/费率','?')}分")
    if slm:
        print(f"  空头清算墙: +2%=${sg(slm,2)}  +5%=${sg(slm,5)}  +10%=${sg(slm,10)}")
    if llm:
        print(f"  多头清算墙: -2%=${sg(llm,2)}  -5%=${sg(llm,5)}  -10%=${sg(llm,10)}")

    print(f"\n━━ 外部/AI/宏观层 ━━")
    print(f"  聪明钱: {bd.get('_smart_money','?')}")
    print(f"  宏观v2: {bd.get('_macro_v2','?')}")
    print(f"  因果验证: {bd.get('_causal_regime','?')}")
    print(f"  Kronos: p_up={p_up}  {kro[:70]}")
    print(f"  HMM: {bd.get('HMM乘数','?')}")
    print(f"  N22b WR矩阵: {bd.get('n22b_wr_matrix','未触发')}")
    print(f"  季节性: {bd.get('p2_seasonal','?')}")
    print(f"  宏观事件: {bd.get('宏观+事件','?')}分")
    print(f"  N03时段: {bd.get('N03时段奖励','?')}")
    print(f"  N10覆盖: {bd.get('N10_全覆盖奖励','?')}")
    print(f"  N15仓位: {bd.get('N15_分层仓位','?')}")

    el = params.get('entry_lo', 0); eh = params.get('entry_hi', 0)
    sl = params.get('stop_loss', 0); t1 = params.get('tp1', 0); t2 = params.get('tp2', 0)
    sl_p = round((sl/eh-1)*100,2) if eh else 0
    t1_p = round((t1/el-1)*100,2) if el else 0
    t2_p = round((t2/el-1)*100,2) if el else 0

    print(f"\n━━ 入场参数 ━━")
    print(f"  入场区: ${el:.2f} ~ ${eh:.2f}")
    print(f"  SL:    ${sl:.2f}（{sl_p:+.2f}%）")
    print(f"  TP1:   ${t1:.2f}（{t1_p:.2f}%）")
    print(f"  TP2:   ${t2:.2f}（{t2_p:.2f}%）")
    print(f"  RR={params.get('rr1','?')}  仓位={params.get('position_size','?')}  杠杆={params.get('leverage','?')}x")
    if grade < 80:
        print(f"\n  解封触发器:")
        print(f"   ① CHoCH出现（跌破SL ${st.get('last_sl',0):.2f}）")
        print(f"   ② Bear OB形成 + 价格拉升至入场区")
        print(f"   ③ grade {grade:.0f}→80（差{80-grade:.0f}分）")
    else:
        print(f"\n  ✅ 已解封可入场")

syms = sys.argv[1:] if len(sys.argv)>1 else ['SNDKUSDT','MUUSDT','SPCXUSDT']
for sym in syms:
    show(sym)
    print()

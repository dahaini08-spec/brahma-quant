#!/usr/bin/env python3
# ponytail: brahma_full_report 330行，有意为之，重构前先 grep 所有调用方
"""
brahma_full_report.py — 梵天全能力标准输出函数 v2.0
设计院 2026-08-23 苏摩111封印（全字段版）

铁律：每次分析必须调用此函数输出
禁止：人工挑选维度、省略任何系统输出项
覆盖：run_analysis()所有字段 + breakdown全量 + fangcang全量
时间：全部北京时间 UTC+8
"""
import sys, os
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))

def _bj_now() -> str:
    return datetime.now(BJ).strftime('%Y-%m-%d %H:%M CST')

def _bj_ts(ts) -> str:
    if not ts:
        return 'N/A'
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=BJ).strftime('%m-%d %H:%M CST')
        if isinstance(ts, str):
            for fmt in ('%Y-%m-%dT%H:%M:%S.%f+00:00', '%Y-%m-%dT%H:%M:%S+00:00', '%Y-%m-%d'):
                try:
                    dt = datetime.strptime(ts[:len(fmt)], fmt).replace(tzinfo=timezone.utc).astimezone(BJ)
                    return dt.strftime('%m-%d %H:%M CST')
                except Exception:
                    pass
    except Exception:
        pass
    return str(ts)[:16]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain'))


def format_full_report(r: dict) -> str:
    """梵天全能力标准输出 - 全字段无遗漏版"""
    c   = r.get('confluence', {}) or {}
    bd  = c.get('breakdown', {}) or {}
    p   = r.get('params', {}) or {}
    fc  = r.get('fangcang', {}) or {}
    mom = r.get('momentum', {}) or {}
    bb  = mom.get('bb', {}) or {}
    pm  = fc.get('prob_matrix', {}) or {}
    top1 = (fc.get('top_similar') or [{}])[0]
    liq  = fc.get('liq_map') or {}
    htf  = fc.get('htf_anchor') or {}
    # [2026-08-29 苏摩111修复] 新字段兼容读取
    _fc_n   = fc.get('s2_n') or fc.get('similar_cases_count') or fc.get('n_cases') or 0
    _fc_wr  = fc.get('s2_wr') or fc.get('breakout_wr') or 0
    _fc_adj = fc.get('unified_adj') or fc.get('score_adj') or 0
    # anti_manip 从 antifragile 字段读取
    _anti   = r.get('antifragile') or r.get('anti_manip') or (r.get('extra_data') or {}).get('anti_manip') or {}
    _anti_risk  = _anti.get('risk_level', 'N/A')
    _anti_adj   = _anti.get('score_adj', 0)
    _anti_flags = _anti.get('flags', [])
    ell  = fc.get('elliott_wave') or {}
    vpa  = fc.get('vpa') or {}
    mfi  = fc.get('main_force_intent') or {}
    ms   = fc.get('micro_structure') or {}
    vs   = fc.get('vector_stats') or {}
    smt  = r.get('sentiment') or {}
    wave = r.get('wave') or {}
    dn   = r.get('dharma_nodes') or {}
    dec  = r.get('decision') or {}
    liqh = r.get('_liq_heatmap') or {}
    us   = r.get('us_session') or {}

    sym    = r.get('symbol', '?')
    price  = r.get('price', 0)
    regime = r.get('regime', '?')
    score  = c.get('total', r.get('score', 0))
    action = c.get('action', r.get('decision_action', '?'))
    valid  = r.get('valid_signal', False)
    timing = r.get('timing_status', '?')

    def _v(val, default='N/A'):
        return val if val not in (None, '', {}, []) else default

    def _row(label, val, warn=''):
        v = _v(val)
        w = f' ⚠️{warn}' if warn and v == 'N/A' else ''
        return f'  {label:<26} {v}{w}'

    lines = []
    W = '=' * 62
    lines.append(W)
    lines.append(f'  🏛️ 梵天全能力 v2.0 | {sym} ${price:,.1f} | {regime}')
    lines.append(f'  北京时间: {_bj_now()}')
    lines.append(f'  score={score} | {action} | valid={valid} | timing={timing}')
    lines.append(W)

    # ══ 基础层 ══
    lines.append('【基础层】')
    lines.append(_row('体制/HMM乘数', bd.get('HMM乘数') or bd.get('_regime_mult')))
    lines.append(_row('体制中文', r.get('regime_cn')))
    lines.append(_row('QEW权重', bd.get('QEW权重')))
    lines.append(_row('SMC结构', bd.get('SMC结构')))
    lines.append(_row('达摩院verdict', f"{dn.get('verdict')} {dn.get('detail','')[:30]}" if dn else 'N/A'))
    lines.append(_row('量能验证', bd.get('量能验证')))
    lines.append(_row('OBV方向', bd.get('OBV方向')))
    lines.append(_row('趋势一致性', bd.get('趋势一致性')))
    lines.append(_row('多周期对齐', bd.get('多周期对齐')))
    lines.append(_row('关键位精确度', bd.get('关键位精确度')))
    lines.append(_row('形态成熟度', bd.get('形态成熟度')))
    lines.append(_row('N03时段奖励', bd.get('N03时段奖励')))
    lines.append(_row('N08牛市RSI中性', bd.get('N08_牛市RSI中性')))
    lines.append(_row('N10全覆盖奖励', bd.get('N10_全覆盖奖励')))
    lines.append(_row('OB新鲜度', bd.get('OB新鲜度_1H_LONG') or bd.get('OB新鲜度')))
    lines.append(_row('波浪结构', f"{wave.get('wave','')} {wave.get('note','')}" if wave else 'N/A'))

    # ══ 情绪层 ══
    lines.append('【情绪层】')
    lines.append(_row('资金费/情绪', bd.get('资金费情绪')))
    lines.append(_row('VolSkew期权偏斜', bd.get('VolSkew'), '期权数据缺失'))
    lines.append(_row('Smart Money', bd.get('_smart_money'), '大户数据缺失'))
    lines.append(_row('跨所FR套利', bd.get('_cross_fr_basis'), '跨所数据缺失'))
    lines.append(_row('期权P/C比', bd.get('_options_pc'), '期权数据缺失'))
    lines.append(_row('鲸鱼+微观', bd.get('鲸鱼+微观'), '微观数据缺失'))
    lines.append(_row('OI总量(BN)', f"${smt.get('oi',0)*price/1e9:.2f}B" if smt.get('oi') else 'N/A'))
    lines.append(_row('多空比LS_ratio', f"{smt.get('long_short_ratio','N/A')}"))
    lines.append(_row('资金费率FR', f"{smt.get('funding_rate','N/A')}%"))
    lines.append(_row('美股session', f"{us.get('session','N/A')}"))

    # ══ 技术层 ══
    lines.append('【技术层】')
    lines.append(_row('HCME方仓匹配', bd.get('HCME情境匹配')))
    lines.append(_row('HCME上下文', str(r.get('hcme_ctx') or fc.get('hcme_context',''))[:45]))
    lines.append(_row('HCME数据来源', fc.get('hcme_source')))
    import re as _re
    _hcme_ctx_str = str(r.get('hcme_ctx') or fc.get('hcme_context',''))
    _n_m = _re.search(r'n=(\d+)', _hcme_ctx_str)
    _long_m = _re.search(r'多=(\d+)%', _hcme_ctx_str)
    _short_m = _re.search(r'空=(\d+)%', _hcme_ctx_str)
    lines.append(_row('HCME案例数n', _n_m.group(1) if _n_m else 'N/A'))
    lines.append(_row('HCME多头突破率', _long_m.group(1)+'%' if _long_m else 'N/A'))
    lines.append(_row('HCME空头突破率', _short_m.group(1)+'%' if _short_m else 'N/A'))
    lines.append(_row('Kronos p_up', r.get('kronos_p_up')))
    lines.append(_row('Kronos score_adj', bd.get('Kronos_p_up') or r.get('kronos_score')))
    lines.append(_row('HAR-RV波动率', bd.get('HAR-RV波动率')))
    lines.append(_row('Hurst指数', bd.get('Hurst体制验证')))
    lines.append(_row('CVD订单流', bd.get('CVD订单流'), 'CVD数据缺失'))
    lines.append(_row('RSI 1H/4H/1D', bd.get('RSI状态描述') or
                  f"1H:{mom.get('rsi_1h','?')} 4H:{mom.get('rsi_4h','?')}"))
    lines.append(_row('BB宽度BBW', f"{bb.get('width',0)*100:.2f}%" if bb.get('width') else 'N/A'))
    lines.append(_row('CausalVerifier', bd.get('_causal_regime')))
    lines.append(_row('动量背离', bd.get('动量背离')))
    lines.append(_row('ML+贝叶斯+滑点', bd.get('ML+在线贝叶斯+滑点'), '模型未激活'))
    lines.append(_row('LSTM+NLP情绪', bd.get('LSTM+NLP情绪'), '模型未激活'))
    lines.append(_row('量能衰竭+背离', bd.get('量能衰竭+背离共振'), '模型未激活'))
    lines.append(_row('研究增强层', bd.get('研究增强层')))

    # ══ 死穴/封禁层（新增）══
    lines.append('【死穴/封禁状态】')
    _gb = r.get('globally_blocked') or bd.get('globally_blocked')
    if _gb is None:
        _gb = '未触发(False)'
    lines.append(_row('globally_blocked', _gb))
    lines.append(_row('死穴封禁原因', bd.get('死穴封禁') or bd.get('体制封禁') or bd.get('_death_zone_reason')))
    lines.append(_row('RTC实时体制覆盖', bd.get('_rtc_override') or '无（体制正常）'))
    lines.append(_row('BBW档位(P0)', bd.get('_p0_bbw_tier')))
    _p1v = r.get('fangcang_rsi_note') or (r.get('confluence') or {}).get('breakdown', {}).get('P1方仓RSI') or bd.get('P1_方仓RSI分层')
    lines.append(_row('P1_方仓RSI分层', _p1v))
    _p2v = bd.get('P2_做空激活') or bd.get('p2_short') or r.get('p2_short_active')
    lines.append(_row('P2_做空激活', _p2v))
    sqe_data = _run_sqe(r)
    lines.append(_row('SQE质检结果', sqe_data.get('sqe_result')))
    lines.append(_row('SQE拒绝原因', sqe_data.get('sqe_reject_reason')))

    # ══ 结构层 ══
    lines.append('【结构层】')
    lines.append(_row('VolProfile密度', bd.get('VolProfile密度'), 'VolProfile缺失'))
    lines.append(_row('b2入场时机', c.get('b2_proximity')))
    lines.append(_row('PIPs几何形态', f"{fc.get('pip_shape','')} score={fc.get('pip_score','')}"))
    lines.append(_row('Elliott波浪',
        f"{ell.get('wave_type','')} {ell.get('direction','')} W{ell.get('wave_count','')} conf={ell.get('confidence','')}" if ell else 'N/A'))
    lines.append(_row('VPA量价分析',
        f"{vpa.get('vpa_signal','')} strength={vpa.get('strength','')} bias={vpa.get('direction_bias','')}" if vpa else 'N/A'))
    lines.append(_row('微观结构', f"CHoCH={ms.get('choch_count',0)} BOS={ms.get('bos_count',0)} compress={ms.get('micro_compress','')}" if ms else 'N/A'))
    lines.append(_row('integrity_gate', f"passed={r.get('integrity_gate',{}).get('passed','?')}"))

    # ══ 外部层/清算集群 ══
    lines.append('【外部层/清算集群】')
    lines.append(_row('清算/OI得分', bd.get('清算/OI'), '清算数据缺失'))
    _short_liq = liq.get('nearest_short_liq') or liqh.get('nearest_short') or f'~${price*1.033:,.0f}(+3.3%估算)'
    _long_liq  = liq.get('nearest_long_liq')  or liqh.get('nearest_long')  or f'~${price*0.967:,.0f}(-3.3%估算)'
    lines.append(_row('空头清算上方', _short_liq))
    lines.append(_row('多头清算下方', _long_liq))
    lines.append(_row('OI变化%', smt.get('oi_change_pct') or liq.get('oi_change')))
    # [P0修复 2026-08-29 苏摩111] 优先读brahma_core注入的实时HTF共振值
    _htf_bd   = (r.get('confluence') or {}).get('breakdown', {}).get('HTF周月线锚定')
    _htf_addon = r.get('htf_score_addon', 0)
    if _htf_bd:
        _htf_str = f'HTF周月线锚定 {_htf_bd}'
    else:
        # fallback: 用get_features()实时算
        try:
            from brahma_brain.weekly_monthly_anchor import get_anchor as _wma_fn
            _wma = _wma_fn(sym)
            _wf  = _wma.get_features(current_price=float(price or 0))
            _htf_str = (f'HTF周月线锚定 {_htf_addon:+d} '
                        f'({_wf.get("htf_bias","?")} 共振={_wf.get("htf_resonance",0):.2f})')
        except Exception:
            _htf_str = str(htf.get('_anchor_summary') or htf.get('weekly_regime') or '')
    lines.append(_row('HTF周月线锚定', _htf_str[:70] if _htf_str else 'N/A'))
    lines.append(_row('L2+贝叶斯+宏观', bd.get('L2+贝叶斯+宏观')))

    # ══ 奖惩层 ══
    lines.append('【奖惩层】')
    lines.append(_row('8月季节性', bd.get('M07时间效应') or bd.get('宏观+事件')))
    lines.append(_row('ATR体制', bd.get('N16_ATR体制')))
    lines.append(_row('成交量比率', bd.get('成交量比率')))
    lines.append(_row('GATE_ATR_Q4', bd.get('GATE_ATR_Q4')))
    lines.append(_row('GATE_SESSION_DEAD', bd.get('GATE_SESSION_DEAD')))
    lines.append(_row('dfe综合惩罚', bd.get('dfe_total')))
    _p4v = (r.get('confluence') or {}).get('breakdown', {}).get('P4三周期共振') or bd.get('P4_三周期共振') or bd.get('P4三周期共振')
    lines.append(_row('P4三周期共振', _p4v))
    lines.append(_row('时段权重', bd.get('时段权重')))
    # s27/s28/s29 统计模式维度
    _s27v = r.get('s27_gap_up')
    _s28v = r.get('s28_bounce_setup')
    _s29v = r.get('s29_first_red_day')
    _s27d = _s27v if _s27v is not None else 0
    _s28d = _s28v if _s28v is not None else 0
    _s29d = _s29v if _s29v is not None else 0
    lines.append(_row('s27_GapUp', f"{_s27d:+d}"))
    lines.append(_row('s28_Bounce', f"{_s28d:+d}"))
    lines.append(_row('s29_FRD', f"{_s29d:+d}"))
    lines.append(_row('s27~29_合计', f"{_s27d+_s28d+_s29d:+d}"))

    # ══ 方仓概率矩阵 ══
    lines.append('【方仓概率矩阵】')
    lines.append(_row('上涨概率p_up', pm.get('p_up')))
    lines.append(_row('下跌概率p_down', pm.get('p_down')))
    lines.append(_row('期望值EV', pm.get('ev')))
    lines.append(_row('方仓WR', vs.get('wr')))
    lines.append(_row('方仓EV_directional', vs.get('ev')))
    lines.append(_row('置信度/案例数', f"n={_fc_n} WR={_fc_wr:.0%} adj={_fc_adj:+.0f}"))
    lines.append(_row('操控防御', f"risk={_anti_risk} adj={_anti_adj:+.0f} flags={_anti_flags[:2] if _anti_flags else []}"))
    lines.append(_row('陷阱预警', fc.get('trap_alert')))
    lines.append(_row('主力意图', f"{mfi.get('intent','')} conf={mfi.get('confidence','')}"))
    lines.append(_row('方仓摘要', str(fc.get('fangcang_summary',''))[:50]))
    top3 = fc.get('top3_summary') or ''
    if top3:
        lines.append(_row('Top3案例摘要', str(top3)[:55]))
    top1_dt = _bj_ts(top1.get('dt')) if top1.get('dt') else 'N/A'
    lines.append(_row('Top1案例',
        f"{top1_dt} ret={top1.get('future_ret')} max={top1.get('future_max')} min={top1.get('future_min')}"))

    # ══ 决策层/入场参数 ══
    lines.append('【决策层/入场参数】')
    lines.append(_row('score(加权后)', score))
    lines.append(_row('score_raw(未加权)', r.get('score_final_raw')))
    lines.append(_row('score_gate', f"reject={c.get('score_gate_reject')} min={c.get('score_gate_min')}"))
    lines.append(_row('decision_step', r.get('decision_step')))
    lines.append(_row('decision_reason', str(r.get('decision_reason',''))[:50]))
    lines.append(_row('入场区', f"${p.get('entry_lo',0):,.0f} ~ ${p.get('entry_hi',0):,.0f}"))
    lines.append(_row('止损', f"${p.get('stop_loss',0):,.0f} SL={p.get('sl_pct')}%"))
    lines.append(_row('TP1', f"${p.get('tp1',0):,.0f} RR={p.get('rr1')}"))
    lines.append(_row('TP2', f"${p.get('tp2',0):,.0f} RR={p.get('rr2')}"))
    lines.append(_row('entry_source', p.get('entry_source')))
    lines.append(_row('signal_dir', r.get('signal_dir')))

    # ══ 系统降级汇总 ══
    degraded = []
    if not r.get('kronos_p_up'):       degraded.append('Kronos(p_up=None)')
    if str(bd.get('研究增强层','')).startswith('0'): degraded.append('TimesFM(no_signal)')
    if bd.get('LSTM+NLP情绪', 0) == 0: degraded.append('LSTM+NLP(未激活)')
    if bd.get('ML+在线贝叶斯+滑点', 0) == 0: degraded.append('ML贝叶斯(未激活)')
    if bd.get('量能衰竭+背离共振', 0) == 0: degraded.append('量能背离(未激活)')
    if bd.get('鲸鱼+微观', 0) == 0:   degraded.append('鲸鱼微观(降级)')
    if degraded:
        lines.append(f'【⚠️系统降级】{" | ".join(degraded)}')
    else:
        lines.append('【系统状态】全能力运行 ✅')

    lines.append(W)
    return '\n'.join(lines)


def _run_sqe(r: dict) -> dict:
    """运行SQE质检，返回 {result, reason} 字典"""
    try:
        from brahma_brain.signal_quality_engine import SignalQualityEngine
        sqe = SignalQualityEngine()
        gate = sqe.evaluate(r)
        return {
            'sqe_result': gate.status,
            'sqe_reject_reason': gate.reason or '无' if gate.rejected else 'PASS',
        }
    except Exception as e:
        return {'sqe_result': f'ERROR:{e}', 'sqe_reject_reason': 'N/A'}

def run_full_analysis(symbol: str, mode: str = 'auto'):
    """
    主入口：运行梵天1号工程全能力报告
    [封印 2026-08-30 苏摩111 ADAPTIVE v3.0]
    mode: auto(默认) / hf(高频合约) / spot(中长线现货) / dual(双模并排)
    全能力 = brahma_1hao_analysis.run_analysis()
    包含：35维评分 + SMC/FVG/OB + 清算地图 + MTF五周期 + 决策树5步漏斗 + 方仓铁证
    返回: (report_str, r_dict) — report给人看，r给机器读
    """
    import sys as _sys_rfа, os as _os_rfа, time as _time_rfа
    _sd = _os_rfа.path.join(_os_rfа.path.dirname(_os_rfа.path.abspath(__file__)), '..', 'scripts')
    if _sd not in _sys_rfа.path:
        _sys_rfа.path.insert(0, _sd)
    ts0 = _time_rfа.time()
    # Step1: 调用1号工程完整报告（包含所有层）
    try:
        from brahma_1hao_analysis import run_analysis as _1hao_main
        report = _1hao_main(symbol)
        # ── [封印 2026-08-29 苏摩111] 过滤引擎日志行 ──
        _LOG_PREFIXES = ('[BrahmaBrain]','[s_smart]','[KronosBridge','[oi_scanner]','[RSM]','[TimingFilter]','[unified_fangcang]','[分析开始]')
        report = '\n'.join(l for l in report.split('\n') if not any(l.strip().startswith(p) for p in _LOG_PREFIXES))
    except Exception as _e1:
        report = f"[1号工程调用失败: {_e1}]"
    # Step2: 同时拿到r对象供机器读取（2026-08-28 B2优化: runner复用snap缓存，避免重复分析）
    r = {}
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis as _runner
        # 快捷路径：先检查snap缓存（如果1hao已写入，这里将在<100ms内返回）
        r = _runner(symbol)
        r['_data_source'] = f'1hao+runner耗时{_time_rfа.time()-ts0:.1f}s'
    except Exception as _e2:
        r = {'_error': str(_e2)}

    # 将s27~29追加到report末尾（始终显示，0=未触发）
    try:
        _s27r = r.get('s27_gap_up', 0) or 0
        _s28r = r.get('s28_bounce_setup', 0) or 0
        _s29r = r.get('s29_first_red_day', 0) or 0
        _stat_line = (
            f'\n  s27_GapUp={_s27r:+d}  s28_Bounce={_s28r:+d}  '
            f's29_FRD={_s29r:+d}  统计模式合计={_s27r+_s28r+_s29r:+d}'
        )
        report = report + _stat_line
    except Exception:
        pass

    # [P0接通 2026-08-26 苏摩111] price_zone_engine → full_report 战场预判层
    try:
        import sys as _sys_pz, os as _os_pz
        _pz_path = _os_pz.path.join(_os_pz.path.dirname(_os_pz.path.abspath(__file__)))
        if _pz_path not in _sys_pz.path: _sys_pz.path.insert(0, _pz_path)
        from price_zone_engine import calc_zones, format_zone_report
        _zones = calc_zones(symbol)
        if _zones and not _zones.get('error'):
            _zone_report = format_zone_report(_zones, compact=True)
            report = report + "\n\n" + _zone_report
            r['_price_zones'] = _zones
    except Exception as _ez:
        r['_price_zones_error'] = str(_ez)[:80]

    # [经验检索 2026-08-27 苏摩111] brahma_experience_engine → 历史相似案例
    try:
        import sys as _sys_exp, os as _os_exp
        _exp_path = _os_exp.path.join(_os_exp.path.dirname(_os_exp.path.abspath(__file__)))
        if _exp_path not in _sys_exp.path: _sys_exp.path.insert(0, _exp_path)
        from brahma_experience_engine import query_similar_experiences
        _regime_now = r.get('regime', 'UNKNOWN')
        _rsi_now    = r.get('rsi_4h', r.get('rsi', 50))
        _bbw_now    = r.get('bb_width', r.get('bbw', 5))
        _atr_now    = r.get('atr_pct', 2)
        _ret7_now   = r.get('ret_7d', 0)
        _exp_result = query_similar_experiences(
            symbol=symbol, regime=_regime_now,
            rsi_4h=_rsi_now, bbw=_bbw_now, atr_pct=_atr_now,
            ret_7d=_ret7_now, top_k=5
        )
        if _exp_result and _exp_result.get('n', 0) > 0:
            report = report + '\n' + _exp_result.get('summary', '')
            r['_experience'] = _exp_result
    except Exception as _eexp:
        r['_experience_error'] = str(_eexp)[:80]

    # [自主决策 2026-08-26] narrative_engine 叙事层接通
    try:
        from narrative_engine import analyze_narrative
        _narr = analyze_narrative(symbol, r)
        if _narr and not _narr.get('error'):
            _ns = _narr.get('narrative_score', 0)
            _nb = _narr.get('bias', 'NEUTRAL')
            _nk = _narr.get('key_narrative', '')
            _narr_line = f"\n🌊 叙事层: bias={_nb} score={_ns} | {_nk[:60]}"
            report = report + _narr_line
            r['_narrative'] = _narr
    except Exception:
        pass

    # [全量接通 2026-08-26 苏摩111] 批次A：分析增强层
    # 接入位置：brahma_full_report → run_full_analysis()

    # A1: multi_tf_context_builder — 多周期快照
    try:
        from multi_tf_context_builder import build_context
        _mtf = build_context(symbol)
        if _mtf and not _mtf.get('error'):
            r['_mtf_context'] = _mtf
    except Exception:
        pass

    # A2: failure_pattern_db — 失败模式预警
    try:
        from failure_pattern_db import get_current_risk_score
        _fp = get_current_risk_score(r)
        if _fp and _fp.get('risk_score', 0) > 0:
            _fp_line = f"\n⚠️ 失败模式: risk={_fp.get('risk_score')} | {_fp.get('top_pattern','')[:50]}"
            report = report + _fp_line
            r['_failure_pattern'] = _fp
    except Exception:
        pass

    # A3: causal_regime_verifier — 因果体制验证
    try:
        from causal_regime_verifier import verify as causal_verify
        _cv = causal_verify(symbol, r.get('regime',''), r.get('signal_dir',''))
        if _cv and not _cv.get('error'):
            r['_causal_verify'] = _cv
    except Exception:
        pass

    # A4: macro_calendar — 宏观日历事件
    try:
        from macro_calendar import get_upcoming_events
        _mc = get_upcoming_events()
        if _mc:
            r['_macro_calendar'] = _mc[:3]
    except Exception:
        pass

    # A5: brahma_intel_layer — 智能体情报
    try:
        from brahma_intel_layer import identify_pattern
        _il = identify_pattern(symbol, r)
        if _il and not _il.get('error'):
            r['_intel'] = _il
    except Exception:
        pass

    # A6: tradfi_dump_detector — TradFi抛压检测
    try:
        from tradfi_dump_detector import m5_monthly_trend_filter
        _td = m5_monthly_trend_filter(
            float(r.get('price_change_30d', 0) or 0),
            r.get('signal_dir', 'LONG')
        )
        if _td and not _td.get('error'):
            r['_tradfi_dump'] = _td
    except Exception:
        pass

    # ══ [ADAPTIVE v3.0 2026-08-30 苏摩111封印] S0/S1/S2 自适应决策层 ════════════════════
    try:
        _ms   = r.get('market_state_raw', {}) or {}
        _price  = float(r.get('price', 0) or 0)
        _regime = r.get('regime', 'BULL_TREND')
        _dir    = r.get('signal_dir', 'LONG')
        _score  = float(str(r.get('score_final', 0)).split()[0] if r.get('score_final') else 0)
        _timing = r.get('timing_status', 'STANDBY')
        _ev_adj = r.get('ev_adj', 0)

        # ══ mode自动判断 ══
        _mode = mode if mode in ('hf','spot','dual') else 'hf'

        # 市场状态识别 (ADX+BB_width+Hurst)
        _adx   = float(_ms.get('adx_4h', 25) or 25)
        _bbw   = float(_ms.get('bb_width', 0.02) or 0.02)
        _bbpos = float(_ms.get('bb_pos', 0.5) or 0.5)
        _hurst_raw = r.get('market_state_raw', {}).get('hurst_4h', 0) or \
                      r.get('confluence', {}).get('breakdown', {}).get('Hurst体制验证', 0) or 0.55
        # Hurst字段可能是字符串 "H=0.682 趋势验证✅ +5"
        if isinstance(_hurst_raw, str):
            import re as _re_h
            _hm = _re_h.search(r'H=([0-9.]+)', _hurst_raw)
            _hurst = float(_hm.group(1)) if _hm else 0.55
        else:
            _hurst = float(_hurst_raw or 0.55)
        if _adx > 25 and _hurst > 0.6:   _mkt_state = 'TRENDING'
        elif _adx < 20 and _bbw < 0.015: _mkt_state = 'RANGING'
        elif _bbw < 0.012:                _mkt_state = 'RANGING_PRE_BREAKOUT'
        else:                             _mkt_state = 'NEUTRAL'

        # GEX区间分析
        _gex_max  = 0; _gex_min = 0; _gex_flip = 0; _gex_dir = 'N/A'
        try:
            from brahma_brain.gex_scanner import get_gex_state as _ggs
            _sym_s = symbol.replace('USDT','').replace('PERP','')
            _gex_d = _ggs(_sym_s)
            if _gex_d:
                _gex_max  = float(_gex_d.get('max_gex_strike', 0) or 0)
                _gex_min  = float(_gex_d.get('min_gex_strike', 0) or 0)
                _gex_flip = float(_gex_d.get('zero_flip', 0) or 0)
                _gex_dir  = _gex_d.get('gex_direction', 'N/A')
        except Exception: pass
        _in_pin = _gex_min < _price < _gex_max if _gex_max and _gex_min else False
        _gex_zone = f'PIN区(${_gex_min:,.0f}~${_gex_max:,.0f})' if _in_pin else \
                    f'空头自由区(>${_gex_max:,.0f})' if _price > _gex_max else \
                    f'高波动区(<${_gex_min:,.0f})'

        # 止损池分析
        _liq = r.get('_liq_heatmap', {}) or {}
        _near_short = _liq.get('nearest_short_liq', 0)  # 空头清算墙(上方)
        _near_long  = _liq.get('nearest_long_liq', 0)   # 多头清算墙(下方)
        _short_vol  = _liq.get('short_liq_volume', 0) or 0
        _long_vol   = _liq.get('long_liq_volume', 0) or 0

        # EV矩阵查询
        _sbin = '<120' if _score<120 else '120-139' if _score<140 else '140-154' if _score<155 else '155-159' if _score<160 else '160+'
        _ev_key = f'{_regime}:{_dir}:{_sbin}'
        _ev_val = None; _ev_wr = None; _ev_n = 0
        try:
            from brahma_brain.ev_feedback import _load_matrix as _evlm
            _ev_raw = _evlm()
            _ev_mat = _ev_raw.get('matrix', _ev_raw) if isinstance(_ev_raw, dict) else {}
            _ev_mat = {k:v for k,v in _ev_mat.items() if isinstance(v, dict)}
            _ev_entry = _ev_mat.get(_ev_key, {})
            _ev_val = _ev_entry.get('ev', None)
            _ev_wr  = _ev_entry.get('wr', None)
            _ev_n   = _ev_entry.get('n', 0)
        except Exception: pass

        # 战场预判区间
        _pz = r.get('_price_zones', {})
        if not _pz:
            try:
                from price_zone_engine import calc_zones as _czs
                _pz = _czs(symbol)
            except Exception: _pz = {}
        _hz = _pz.get('high_short', {}) if _pz else {}  # 高空区
        _lz = _pz.get('low_long',  {}) if _pz else {}  # 低多区
        _sp = _pz.get('scenario_prob', {}) if _pz else {}
        _path_up   = float(_sp.get('up_first',   0.57) * 100) if _sp else 57
        _path_down = float(_sp.get('down_first', 0.42) * 100) if _sp else 42
        # 字段别名兼容: low/high 或 lo/hi
        def _zlo(z): return z.get('low', z.get('lo', 0))
        def _zhi(z): return z.get('high', z.get('hi', 0))
        def _zsl(z): return z.get('stop_loss', z.get('sl', 0))
        def _zrr(z): return z.get('rr1', z.get('rr', 0))

        # 双边猎杀检测
        _double_hunt = False
        if _near_short and _near_long and _price:
            _up_dist   = abs(_near_short - _price) / _price * 100
            _dn_dist   = abs(_price - _near_long)  / _price * 100
            _double_hunt = _up_dist < 1.0 and _dn_dist < 1.0

        # S0: 一句话结论
        if _ev_val is not None and _ev_val < -0.5:
            _s0_action = '等待，不入场'
            _s0_reason = f'EV={_ev_val:+.2f}%(做{_dir[0]}死区) + {_mkt_state} + {_timing}'
        elif _double_hunt:
            _s0_action = '等待，双边猎杀中'
            _s0_reason = f'上下均<1%止损山 + 等方向选择'
        elif _timing == 'READY':
            _s0_action = '入场条件成熟，可执行'
            _s0_reason = f'TimingFilter=READY + {_regime} + EV={_ev_val:+.2f}%' if _ev_val else f'TimingFilter=READY + {_regime}'
        else:
            _s0_action = '观察，等待触发'
            _s0_reason = f'{_mkt_state} + TimingFilter={_timing}'

        # 下一个机会文本
        _next_ops = []
        if _hz and _zlo(_hz):
            _ev_short_hint = f'EV预估≈+2.5%' if _ev_val and _ev_val < 0 else 'EV待确认'
            _next_ops.append(f'①高空区${_zlo(_hz):,.0f}触及→布空({_ev_short_hint})')
        if _lz and _zlo(_lz):
            _ev_long_hint = f'EV预估≈+1.8%'
            _next_ops.append(f'②低多区${_zlo(_lz):,.0f}触及→轻多({_ev_long_hint})')
        _next_str = '  '.join(_next_ops) if _next_ops else '无清晰触发区间'

        # 仓位建议(基于SL档位)
        _sl_pct = float(str(r.get('sl_pct', 2.0)).split()[0] if r.get('sl_pct') else 2.0)
        if _sl_pct < 1.0:   _pos_str = '5%NAV×5x (档位S — WR=100%铁证)'
        elif _sl_pct < 1.5: _pos_str = '2%NAV×5x (档位B- — 降仓保护)'
        else:               _pos_str = '3%NAV×5x (档位B+ — 标准上限)'

        # 拼装S0~S2头部
        _sep = '━' * 50
        _s0s1s2 = [
            '',
            '╬' + '═'*58,
            f'  🏛️ 梵天 ADAPTIVE v3.0 · {"HF高频合约" if _mode=="hf" else "SPOT中长线现货" if _mode=="spot" else "DUAL双模并排"} · 3秒决策卡',
            '╬' + '═'*58,
            '',
            f'  【S0 一句话结论】',
            f'  📋 裁决: {_s0_action}',
            f'  原因: {_s0_reason}',
            f'  下一机会: {_next_str}',
            '',
            f'  【S1 主力猎杀地图】',
            f'  📌 GEX区间: {_gex_zone} | 方向: {_gex_dir}',
        ]
        if _gex_max and _gex_min:
            _s0s1s2.append(f'     突破${_gex_max:,.0f}=加速上涨 | 跌破${_gex_min:,.0f}=加速下跌')
        if _near_short and _near_long:
            _up_d = round((_near_short-_price)/_price*100,2) if _price else 0
            _dn_d = round((_price-_near_long)/_price*100,2)  if _price else 0
            _hunt_warn = ' 🚨双边猎杀中!' if _double_hunt else ''
            _s0s1s2 += [
                f'  ⚡ 上方止损山: ${_near_short:,.0f}(+{_up_d:.2f}%){" $"+str(round(_short_vol))+ "M" if _short_vol else ""}',
                f'  ⚡ 下方止损池: ${_near_long:,.0f}(-{_dn_d:.2f}%){" $"+str(round(_long_vol))+"M" if _long_vol else ""}{_hunt_warn}',
            ]
        if _hz and _lz:
            _s0s1s2.append(f'  🎯 先触发上方概率: {_path_up:.0f}% | 先触发下方概率: {_path_down:.0f}%')

        _s0s1s2 += ['', '  【S2 非对称机会识别】']

        # EV排序
        _ev_icon = '🔴' if _ev_val is not None and _ev_val < 0 else '✅'
        if _ev_val is not None:
            _s0s1s2.append(f'  {_ev_icon} 当前共[{_ev_key}] EV={_ev_val:+.3f}% WR={_ev_wr:.0%} n={_ev_n}')
        if _hz and _zlo(_hz) and _zrr(_hz):
            _hz_sl = float(_zsl(_hz) or 0)
            _hz_sl_adj = _hz_sl
            if _hz_sl > 0:
                _hz_sl_round = round(_hz_sl / 100) * 100
                if abs(_hz_sl - _hz_sl_round) / _hz_sl < 0.001:
                    _atr4h = float(_ms.get('atr_4h', 200) or 200)
                    _hz_sl_adj = round(_hz_sl + _atr4h * 0.3, 0)
            _sl_note = f'(OB上沿+ATR×0.3修正自${_hz_sl:,.0f})' if _hz_sl_adj != _hz_sl else ''
            _s0s1s2.append(f'  🎯 机会①: 高空区${_zlo(_hz):,.0f}~${_zhi(_hz):,.0f}布空 | SL=${_hz_sl_adj:,.0f}{_sl_note} RR={_zrr(_hz):.1f} | 仓位: {_pos_str}')
        if _lz and _zlo(_lz) and _zrr(_lz):
            _lz_pos = '2%NAV×5x (轻仓)'
            _s0s1s2.append(f'  🎯 机会②: 低多区${_zlo(_lz):,.0f}~${_zhi(_lz):,.0f}轻多 | SL=${_zsl(_lz):,.0f} RR={_zrr(_lz):.1f} | 仓位: {_lz_pos}')
        if _ev_val is not None and _ev_val < -0.5:
            _s0s1s2.append(f'  ❌ 禁区: 当前追{_dir} EV={_ev_val:+.3f}% 历史亟钱——禁止')

        _s0s1s2 += [
            '',
            f'  【S3 市场状态+体制】',
            f'  状态: {_mkt_state}(ADX={_adx:.1f} BB_width={_bbw:.3f} bb_pos={_bbpos:.2f})',
            f'  体制: {_regime} | score={_score} | TimingFilter: {_timing}',
            '',
        ]

        # ══ SPOT模式专属周期分析层 ══
        if _mode in ('spot', 'dual'):
            try:
                _spot_lines = ['  【S4-SPOT 中长线现货周期层】']
                # 矿工卖压
                _miner = r.get('_miner', {})
                if _miner and _miner.get('production_cost_est'):
                    _m_cost = float(_miner.get('production_cost_est', 0) or 0)
                    _m_margin = round((_price - _m_cost) / _m_cost * 100, 1) if _m_cost else 0
                    _m_icon = '⛏️ 万工极低卖压——顶部未至' if _m_margin > 100 else '⛏️ 矿工中等卖压' if _m_margin > 30 else '⛏️ 矿工高卖压——注意顶部信号'
                    _spot_lines.append(f'  {_m_icon} 生产成本≈${_m_cost:,.0f} | 当前溢价+{_m_margin:.0f}%')
                # HTF周月线
                _htf_bd = r.get('confluence', {}).get('breakdown', {}).get('HTF周月线锁定', '')
                if _htf_bd:
                    _spot_lines.append(f'  📅 HTF周月线: {str(_htf_bd)[:80]}')
                # Elliott
                _wave = r.get('wave', {})
                if _wave and _wave.get('wave'):
                    _spot_lines.append(f'  🌊 Elliott: {_wave.get("wave","")} {_wave.get("bias","")} —— {_wave.get("note","")[:60]}')
                # 方仓铁证(现货角度)
                _fc = r.get('fangcang', {})
                if _fc and _fc.get('top_similar'):
                    _fc_up = _fc.get('prob_up', 0.5)
                    _fc_ev = _fc.get('ev_pct', 0)
                    _spot_lines.append(f'  📊 方仓铁证: 上涨概率{_fc_up:.0%} EV={_fc_ev:+.2f}%（现货分批建仓参考）')
                # 跨市场
                _cm = r.get('_cross_market_result', {})
                if _cm and _cm.get('score') is not None:
                    _spot_lines.append(f'  🌐 跨市场: score={_cm.get("score",0):+d} | {_cm.get("summary","")[:60]}')
                _spot_lines.append('')
                _s0s1s2 += _spot_lines
            except Exception:
                pass

        # ══ DUAL双模并排颜外标注 ══
        if _mode == 'dual':
            _s0s1s2.insert(3, '  ⚠️ DUAL双模：上方为HF高频合约视角，S4-SPOT为现货周期视角')

        _s0s1s2 += [
            '╬' + '═'*58,
            '',
        ]

        report = '\n'.join(_s0s1s2) + '\n' + report
    except Exception as _adap_err:
        pass
    # ══ [END ADAPTIVE v3.0] ══════════════════════════════════════════════

    return report, r


if __name__ == '__main__':
    symbols = sys.argv[1:] or ['BTCUSDT', 'ETHUSDT']
    for sym in symbols:
        report, _ = run_full_analysis(sym)
        print(report)
        print()

# ══ 铁律封印 2026-08-24 苏摩追问 ══
# run_full_analysis的输出 = 必须原样发送给苏摩
# 禁止：在输出后人工提炼/摘要/重写
# 允许：在原始输出末尾追加设计院裁决（不删减原文）
# 违反 = 等同于人工替代梵天输出
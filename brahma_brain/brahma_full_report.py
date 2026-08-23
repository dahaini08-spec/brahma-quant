#!/usr/bin/env python3
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
    _htf_str = str(htf.get('_anchor_summary') or htf.get('weekly_regime') or '')
    lines.append(_row('HTF锚', _htf_str[:55] if _htf_str else 'N/A'))
    lines.append(_row('L2+贝叶斯+宏观', bd.get('L2+贝叶斯+宏观')))

    # ══ 奖惩层 ══
    lines.append('【奖惩层】')
    lines.append(_row('8月季节性', bd.get('M07时间效应') or bd.get('宏观+事件')))
    lines.append(_row('ATR体制', bd.get('N16_ATR体制')))
    lines.append(_row('成交量比率', bd.get('成交量比率')))
    lines.append(_row('GATE_ATR_Q4', bd.get('GATE_ATR_Q4')))
    lines.append(_row('GATE_SESSION_DEAD', bd.get('GATE_SESSION_DEAD')))
    lines.append(_row('dfe综合惩罚', bd.get('dfe_total')))
    lines.append(_row('P4三周期共振', bd.get('P4_三周期共振')))
    lines.append(_row('时段权重', bd.get('时段权重')))

    # ══ 方仓概率矩阵 ══
    lines.append('【方仓概率矩阵】')
    lines.append(_row('上涨概率p_up', pm.get('p_up')))
    lines.append(_row('下跌概率p_down', pm.get('p_down')))
    lines.append(_row('期望值EV', pm.get('ev')))
    lines.append(_row('方仓WR', vs.get('wr')))
    lines.append(_row('方仓EV_directional', vs.get('ev')))
    lines.append(_row('置信度/案例数', f"{fc.get('confidence_level','?')} n={fc.get('similar_cases_count','?')}"))
    lines.append(_row('陷阱预警', fc.get('trap_alert')))
    lines.append(_row('主力意图', f"{mfi.get('intent','')} conf={mfi.get('confidence','')}"))
    lines.append(_row('方仓摘要', str(fc.get('fangcang_summary',''))[:50]))
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


def run_full_analysis(symbol: str):
    """
    主入口：运行梵天分析并返回全能力报告
    铁律：此函数是坐梵天分析的唯一入口，任何分析请求必须过此函数
    禁止：API手工计算、人工挑选维度、站内近似输出
    """
    from brahma_brain.brahma_analysis_runner import run_analysis
    import time
    ts_start = time.time()
    r = run_analysis(symbol)
    elapsed = round(time.time() - ts_start, 1)
    # 注入数据来源标注（防止人工替代）
    r['_data_source'] = f'brahma_analysis_runner.run_analysis() 耗时{elapsed}s 来源=梵天系统'
    report = format_full_report(r)
    # 在报告头部注入来源标注
    source_line = f'  数据来源: run_analysis() 老{elapsed}s | 禁止人工替代 ✅'
    report = report.replace(
        f'  北京时间:',
        f'{source_line}\n  北京时间:'
    )
    return report, r


if __name__ == '__main__':
    symbols = sys.argv[1:] or ['BTCUSDT', 'ETHUSDT']
    for sym in symbols:
        report, _ = run_full_analysis(sym)
        print(report)
        print()

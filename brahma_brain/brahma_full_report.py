#!/usr/bin/env python3
"""
brahma_full_report.py — 梵天全能力标准输出函数
设计院 2026-08-23 苏摩111封印

铁律：每次分析必须调用此函数输出
禁止：人工挑选维度、省略任何系统输出项
覆盖：25维标准模板 + 系统降级标注 + 入场参数完整版
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain'))


def format_full_report(r: dict) -> str:
    """
    梵天全能力标准输出 - 25维全量
    输入：run_analysis()返回值
    输出：完整格式化报告字符串
    """
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

    sym    = r.get('symbol', '?')
    price  = r.get('price', 0)
    regime = r.get('regime', '?')
    score  = c.get('total', 0)
    action = c.get('action', '?')
    valid  = r.get('valid_signal', False)
    timing = r.get('timing_status', '?')

    # ── 辅助函数 ──
    def _v(val, default='N/A'):
        return val if val not in (None, '', {}, []) else default

    def _row(label, val, unit='', warn=''):
        v = _v(val)
        w = f' ⚠️{warn}' if warn and v == 'N/A' else ''
        return f'  {label:<22} {v}{unit}{w}'

    lines = []
    lines.append(f'{"="*56}')
    lines.append(f'  🏛️ 梵天全能力 | {sym} ${price:,.0f} | {regime}')
    lines.append(f'  score={score} | {action} | valid={valid} | timing={timing}')
    lines.append(f'{"="*56}')

    # ── 基础层 ──
    lines.append('【基础层】')
    lines.append(_row('体制/HMM乘数', bd.get('HMM乘数') or bd.get('_regime_mult'), ' ×'))
    lines.append(_row('HMM置信度', bd.get('HMM乘数') or (c.get('hm_conf') or bd.get('_hm_conf'))))
    lines.append(_row('SMC结构', bd.get('SMC结构')))
    lines.append(_row('量能验证', bd.get('量能验证')))
    lines.append(_row('N03时段奖励', bd.get('N03时段奖励'), '', '时段数据缺失'))
    lines.append(_row('N08牛市RSI中性', bd.get('N08_牛市RSI中性'), '', ''))
    lines.append(_row('OB新鲜度', bd.get('OB新鲜度_1H_LONG') or bd.get('OB新鲜度')))
    lines.append(_row('趋势一致性', bd.get('趋势一致性')))

    # ── 情绪层 ──
    lines.append('【情绪层】')
    lines.append(_row('资金费/情绪', bd.get('资金费情绪')))
    lines.append(_row('多空比 LS_ratio', bd.get('LS_ratio') or bd.get('情绪/费率')))
    lines.append(_row('Taker主动买入', bd.get('Taker买入比') or bd.get('鲸鱼+微观')))
    lines.append(_row('Smart Money', bd.get('_smart_money'), '', '大户数据缺失'))
    lines.append(_row('跨所FR套利', bd.get('_cross_fr_basis'), '', '跨所数据缺失'))
    lines.append(_row('期权P/C比', bd.get('_options_pc'), '', '期权数据缺失'))

    # ── 技术层 ──
    lines.append('【技术层】')
    lines.append(_row('HCME方仓匹配', bd.get('HCME情境匹配')))
    hcme_ctx = r.get('hcme_ctx') or fc.get('hcme_context', '')
    lines.append(_row('HCME上下文', str(hcme_ctx)[:40] if hcme_ctx else 'N/A'))
    lines.append(_row('Kronos ML p_up', r.get('kronos_p_up')))
    lines.append(_row('Kronos score_adj', bd.get('Kronos_p_up') or r.get('kronos_score')))
    lines.append(_row('HAR-RV波动率', bd.get('HAR-RV波动率')))
    lines.append(_row('Hurst指数', bd.get('Hurst体制验证')))
    lines.append(_row('CVD订单流', bd.get('CVD订单流'), '', 'CVD数据缺失'))
    lines.append(_row('RSI 1H/4H/1D', bd.get('RSI状态描述') or
                  f"1H:{mom.get('rsi_1h','?')} 4H:{mom.get('rsi_4h','?')}"))
    lines.append(_row('BB宽度BBW', f"{bb.get('width',0)*100:.2f}%" if bb.get('width') else 'N/A'))

    # ── 结构层 ──
    lines.append('【结构层】')
    lines.append(_row('VolProfile密度', bd.get('VolProfile密度'), '', 'VolProfile缺失'))
    lines.append(_row('b2入场时机', c.get('b2_proximity')))
    lines.append(_row('PIPs几何形态', fc.get('pip_score') or bd.get('PIPs几何')))
    lines.append(_row('Elliott波浪', ell.get('pattern') or ell.get('summary', '')[:30]))
    lines.append(_row('VPA量价分析', vpa.get('pattern') or vpa.get('signal', '')[:30]))
    lines.append(_row('CausalVerifier', bd.get('_causal_regime')))

    # ── 外部层（三所清算）──
    lines.append('【外部层/清算集群】')
    lines.append(_row('清算/OI得分', bd.get('清算/OI'), '', '清算数据缺失'))
    lines.append(_row('空头清算上方', liq.get('nearest_short_liq') or liq.get('short_liq_levels')))
    lines.append(_row('多头清算下方', liq.get('nearest_long_liq') or liq.get('long_liq_levels')))
    lines.append(_row('OI变化', liq.get('oi_change') or bd.get('OI变化')))
    lines.append(_row('HTF周线体制', htf.get('weekly_regime') or htf.get('_anchor_summary', '')[:30]))
    lines.append(_row('HTF共识', htf.get('consensus')))

    # ── 奖惩层 ──
    lines.append('【奖惩层】')
    lines.append(_row('8月季节性', bd.get('M07时间效应') or bd.get('宏观+事件')))
    lines.append(_row('ATR体制', bd.get('N16_ATR体制')))
    lines.append(_row('成交量比率', bd.get('成交量比率')))
    lines.append(_row('P4三周期共振', bd.get('P4_三周期共振')))
    lines.append(_row('N10全覆盖奖励', bd.get('N10_全覆盖奖励')))

    # ── 方仓概率矩阵 ──
    lines.append('【方仓概率矩阵】')
    lines.append(_row('上涨概率p_up', pm.get('p_up')))
    lines.append(_row('下跌概率p_down', pm.get('p_down')))
    lines.append(_row('期望值EV', pm.get('ev')))
    lines.append(_row('置信度', fc.get('confidence_level')))
    lines.append(_row('陷阱预警', fc.get('trap_alert')))
    lines.append(_row('主力意图', mfi.get('intent')))
    lines.append(_row('Top1案例', f"{top1.get('dt')} ret={top1.get('future_ret')} max={top1.get('future_max')} min={top1.get('future_min')}"))

    # ── 决策层 ──
    lines.append('【决策层/入场参数】')
    lines.append(_row('score', score))
    lines.append(_row('score_gate', f"reject={c.get('score_gate_reject')} min={c.get('score_gate_min')}"))
    lines.append(_row('入场区', f"${p.get('entry_lo',0):,.0f} ~ ${p.get('entry_hi',0):,.0f}"))
    lines.append(_row('止损', f"${p.get('stop_loss',0):,.0f} SL={p.get('sl_pct')}%"))
    lines.append(_row('TP1', f"${p.get('tp1',0):,.0f} RR={p.get('rr1')}"))
    lines.append(_row('TP2', f"${p.get('tp2',0):,.0f} RR={p.get('rr2')}"))
    lines.append(_row('entry_source', p.get('entry_source')))
    lines.append(_row('signal_dir', r.get('signal_dir')))

    # ── 系统降级状态 ──
    degraded = []
    if not r.get('kronos_p_up'): degraded.append('Kronos(p_up=None)')
    oi_mem = '[oi_scanner] 内存危险' in str(r.get('_runner_meta',''))
    if oi_mem: degraded.append('OI_Scanner(内存降级)')
    if bd.get('研究增强层','').startswith('0'): degraded.append('TimesFM(no_signal)')
    if bd.get('LSTM+NLP情绪',0) == 0: degraded.append('LSTM+NLP(未激活)')
    if degraded:
        lines.append(f'【⚠️系统降级项】{" | ".join(degraded)}')
    else:
        lines.append('【系统状态】全能力运行 ✅')

    lines.append(f'{"="*56}')
    return '\n'.join(lines)


def run_full_analysis(symbol: str) -> str:
    """主入口：运行梵天分析并返回全能力报告"""
    from brahma_brain.brahma_analysis_runner import run_analysis
    r = run_analysis(symbol)
    return format_full_report(r), r


if __name__ == '__main__':
    symbols = sys.argv[1:] or ['BTCUSDT', 'ETHUSDT']
    for sym in symbols:
        report, _ = run_full_analysis(sym)
        print(report)
        print()

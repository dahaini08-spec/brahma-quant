"""
brahma_brain/formatter.py — 分析报告格式化模块
职责：单一。输入analyze()结果dict → 输出人类可读报告字符串
无副作用，无IO，可独立测试

提取自 brahma_core.py L3484 · 2026-06-10 设计院架构拆分 v25.0
"""
from typing import Any


def format_report(r: dict) -> str:
    """生成完整分析报告文本 - 全能力六层版"""
    if 'error' in r:
        return f'❌ 分析失败: {r["error"]}'
    if r.get('signal_dir') == 'NEUTRAL':
        return f'📊 {r["symbol"]}\n{r["summary"]}\n→ 无明确信号，等待'

    sym    = r['symbol'].replace('USDT','')
    p      = r['price']
    cf     = r['confluence']
    pa     = r['params']
    ms     = r['momentum']
    sent   = r['sentiment']
    extra  = r.get('extra', {})
    wave   = r.get('wave', {})
    dir_arrow = '▲ 哆' if r['signal_dir'] == 'LONG' else '▼ 箜'

    # ── L1 宏观层 ──────────────────────────────────────────
    macro = extra.get('macro', {})
    macro_notes = macro.get('notes', [])
    macro_str = '  '.join(macro_notes[:2]) if macro_notes else '未获取'

    # ── L2 结构层（艾略特）─────────────────────────────────
    ew = extra.get('elliott', {})
    wave_pos  = ew.get('wave_pos', wave.get('note', '结构未识别'))
    wave_conf = ew.get('confidence', 0)
    fib_tgt   = ew.get('fib_targets', {})
    ew_str    = f'{wave_pos}（置信{wave_conf}%）'
    fib_str   = '  '.join([f'{k}=${v:,.1f}' for k,v in list(fib_tgt.items())[:2]]) if fib_tgt else ''

    # ── L3 指标层（MACD背离）──────────────────────────────
    div    = extra.get('divergence', {})
    rsi_d  = div.get('rsi_div', {})
    macd_d = div.get('macd_div', {})
    zero   = div.get('macd_zero', '0轴位置未知')
    rsi_details  = rsi_d.get('details', [])
    macd_details = macd_d.get('details', [])
    div_details  = div.get('details_4h', rsi_details + macd_details)
    div_score    = div.get('score', 0)
    div_str = ' / '.join(div_details[:3]) if div_details else '无背离信号'

    # ── L4 量能层 ──────────────────────────────────────────
    vol = extra.get('volume', {})
    vol_details = vol.get('details', [])
    vol_str = ' / '.join(vol_details[:3]) if vol_details else '量能数据未获取'

    # ── L5 形态层 ──────────────────────────────────────────
    pat = extra.get('pattern', {})
    pat_list = pat.get('patterns', [])
    pat_str  = ' / '.join(pat_list[:3]) if pat_list else '无K线形态触发'

    # ── SMC层 ──────────────────────────────────────────────
    smc_raw   = r.get('smc', {})
    smc_score = smc_raw.get('score', {}) if isinstance(smc_raw, dict) else {}
    smc_details = smc_score.get('details', []) if isinstance(smc_score, dict) else []
    smc_str   = ' / '.join(smc_details[:3]) if smc_details else ''

    # ── 谐波 & 多周期详情 ─────────────────────────────────
    harmonic = extra.get('harmonic', {})
    h_patterns = harmonic.get('patterns', [])
    h_best = harmonic.get('best', {})
    h_str = ' / '.join(h_patterns[:2]) if h_patterns else '无谐波形态'
    if h_best and h_best.get('in_prz'):
        h_str += f' ★PRZ命中 ${h_best["PRZ_lo"]:,.1f}~${h_best["PRZ_hi"]:,.1f}'

    multitf = extra.get('multitf', {})
    mt_summary = multitf.get('tf_summary', {})
    mt_str = '  '.join([f'{tf}:{v}' for tf, v in mt_summary.items()]) if mt_summary else ''
    mt_consensus = multitf.get('consensus', '?')
    mt_alignment = multitf.get('alignment', 0)
    mt_misalign = multitf.get('misalign', '')

    enhanced = extra.get('enhanced', {})
    en_notes = enhanced.get('notes', [])
    en_lsr = enhanced.get('lsr', {})
    en_sess = enhanced.get('session', {})

    lines = [
        f'╔══════════════════════════════════════════════════════╗',
        f'  梵天大脑 · {sym}/USDT  ${p:,.2f}  {r["regime"]}',
        f'  {dir_arrow}  共振 {cf["total"]}/150 {cf["grade"]}  行动: {cf["action"]}',
        f'╠══════════════════════════════════════════════════════╣',
        f'',
        f'【L1 宏观方向】',
        f'  {macro_str}',
        f'',
        f'【L2 结构定位】',
        f'  浪型: {ew_str}',
        f'  趋势: {wave.get("wave","?")} → {wave.get("note","?")}',
    ]
    if fib_str:
        lines.append(f'  斐波目标: {fib_str}')

    lines += [
        f'',
        f'【L3 指标验证】',
        f'  RSI  1H={ms.get("rsi_1h","?")}  4H={ms.get("rsi_4h","?")}  日={ms.get("rsi_1d","?")}',
        f'  ATR  {ms.get("atr_1h","?")} ({ms.get("atr_pct","?")}%)  BB位置: {round(ms.get("bb",{}).get("pos",0)*100,1)}%',
        f'  MACD {zero}',
        f'  背离 [{div_score}分] {div_str}',
        f'',
        f'【L4 量能确认】',
        f'  {vol_str}',
        f'',
        f'【L5 结构触发】',
        f'  K线形态: {pat_str}',
    ]
    if smc_str:
        lines.append(f'  SMC信号: {smc_str}')

    # 谐波 & 多周期
    lines += [
        f'',
        f'【P0 谐波+多周期】',
        f'  谐波: {h_str}',
    ]
    if mt_str:
        lines.append(f'  {mt_str[:80]}')
    if mt_consensus:
        lines.append(f'  多周期共识: {mt_consensus}  对齐度: {mt_alignment}/10')
    if mt_misalign:
        lines.append(f'  {mt_misalign}')
    if en_sess:
        sess_name = en_sess.get('session','?')
        sess_mult = en_sess.get('vol_mult',1)
        lines.append(f'  时段: {sess_name}({sess_mult}x)')
    if en_notes:
        lines.append(f'  增强信号: {" / ".join(en_notes[:2])}')

    # P2 层
    whale = extra.get('whale', {})
    cross = extra.get('cross_market', {})
    micro = extra.get('microstructure', {})
    whale_notes = whale.get('notes', [])
    cross_notes = cross.get('notes', [])
    micro_notes = micro.get('notes', [])
    corr_info   = cross.get('corr', {})
    dxy_info    = cross.get('dxy', {})
    risk_info   = cross.get('risk', {})

    if whale_notes or cross_notes or micro_notes:
        lines += [
            f'',
            f'【P2 鲸鱼+跨市场+微观】',
        ]
        if corr_info:
            lines.append(f'  {corr_info.get("note","")}')
        if dxy_info:
            lines.append(f'  {dxy_info.get("note","")}')
        if risk_info:
            regime = risk_info.get("regime","?")
            fg     = risk_info.get("fear_greed",0)
            lines.append(f'  风险偏好: {regime}  恐贪={fg}')
        for n in whale_notes[:2]:
            lines.append(f'  🐋 {n}')
        for n in micro_notes[:2]:
            lines.append(f'  🔬 {n}')
        wh_s  = whale.get('score', 0)
        cx_s  = cross.get('score', 0)
        mc_s  = micro.get('score', 0)
        lines.append(f'  P2评分: 鲸鱼{wh_s}/15  跨市场{cx_s}/15  微观{mc_s}/15')

    lines += [
        f'',
        f'【L6 执行方案】— 唯一入场指令',
        f'  干线周期  primary={pa.get("primary_tf","4H")}(方向/止损)  entry={pa.get("entry_tf","1H")}(入场触发)',
        f'  入场区  ${pa["entry_lo"]:,.2f} ~ ${pa["entry_hi"]:,.2f}',
        f'  止损    ${pa["stop_loss"]:,.2f}  宽={pa["sl_pct"]}%  ATR倍数={pa.get("sl_atr_mult",0):.2f}x',
        f'  止损依据  {pa.get("sl_basis","swing_4h+atr4h×0.3")}',
        f'  T1    ${pa["tp1"]:,.2f}  R:R={pa["rr1"]}  (必须≥2.5)',
        f'  T2    ${pa["tp2"]:,.2f}  R:R={pa["rr2"]}',
        f'  R:R门槛 {"PASS ✅" if pa.get("valid") else "FAIL ⚠️ 等待更好入场位"}',
        f'  资金费率 {sent["funding_rate"]:+.4f}%  多空比 {sent["long_short_ratio"]}',
        f'',
        f'【共振评分 {cf["total"]}/150】',
    ]
    for k, v in cf['breakdown'].items():
        try:
            vi = int(v) if v != '?' else 0
        except (TypeError, ValueError):
            vi = 0
        bar = '█' * (vi // 3) + '░' * max(0, 7 - vi // 3)
        lines.append(f'  {k:10s} {vi:3d}/30  {bar}')

    lines.append(f'╚══════════════════════════════════════════════════════╝')
    return '\n'.join(lines)

# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ['ETHUSDT', 'BTCUSDT']
    for sym in symbols:
        print(f'\n{"="*60}')
        r = analyze(sym)
        print(format_report(r))
        print(f'耗时: {r.get("elapsed", "?")}s')
    assert VERSION, 'brahma_brain version ok'

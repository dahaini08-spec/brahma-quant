"""
brahma_brain/formatter.py — 分析报告格式化模块
职责：单一。输入analyze()结果dict → 输出人类可读报告字符串
无副作用，无IO，可独立测试

提取自 brahma_core.py L3484 · 2026-06-10 设计院架构拆分 v25.0
"""
from typing import Any


def _fmt_price(price: float) -> str:
    """
    动态价格精度格式化——小币补全小数位，比特币等大币保持整数格式
    < 0.0001  → 8位小数   e.g. 0.00003412
    < 0.01    → 6位小数   e.g. 0.003412
    < 1       → 4位小数   e.g. 0.3412
    < 10      → 3位小数   e.g. 2.341
    < 1000    → 2位小数   e.g. 62.34
    >= 1000   → 2位小数+逗号  e.g. 62,525.30
    """
    if price is None:
        return '?'
    try:
        price = float(price)
    except Exception:
        return str(price)
    if price == 0:
        return '0'
    if price < 0.0001:
        return f'${price:.8f}'
    elif price < 0.01:
        return f'${price:.6f}'
    elif price < 1:
        return f'${price:.4f}'
    elif price < 10:
        return f'${price:.3f}'
    elif price < 1000:
        return f'${price:,.2f}'
    else:
        return f'${price:,.2f}'


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
            if v == '?' or v is None:
                vi = 0
            elif isinstance(v, (int, float)):
                vi = int(v)
            else:
                # 从字符串中提取首个数字（含负号），如 "+6 CVD..." → 6，"-9(FR...)" → -9
                import re as _re
                _m = _re.search(r'([+-]?\d+)', str(v))
                vi = int(_m.group(1)) if _m else 0
        except (TypeError, ValueError):
            vi = 0
        bar = '█' * (abs(vi) // 3) + '░' * max(0, 7 - abs(vi) // 3)
        sign = '-' if vi < 0 else '+' if vi > 0 else ' '
        lines.append(f'  {k:10s} {sign}{abs(vi):2d}/30  {bar}')

    lines.append(f'╚══════════════════════════════════════════════════════╝')
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════
# 设计院 2026-06-30 封印 v2.0 — 标准字段集 + 固化输出卡片
# 规则：所有分析输出必须能提取此字段集，缺失字段值为 None
# ══════════════════════════════════════════════════════════════════

# 12个必需字段 + 6个附加字段
STANDARD_FIELDS = [
    'regime',           # 体制: BEAR_TREND / BEAR_EARLY / CHOP_MID / BULL_TREND / BEAR_RECOVERY
    'score',            # 梵天评分 0~175
    'direction',        # SHORT / LONG / NEUTRAL
    'entry_lo',         # 入场区下沿
    'entry_hi',         # 入场区上沿
    'sl',               # 止损价格
    'tp1',              # 止盈1
    'tp2',              # 止盈2
    'rr',               # R:R比率
    'structure_grade',  # 结构等级 0~100
    'gex_min',          # GEX磁铁（最近支撑/压力）
    'trigger_conf',     # 15M触发置信度 0~100
    # 附加字段
    'valid',            # 是否有效信号
    'price',            # 当前价格
    'rsi_1h',           # 1H RSI
    'rsi_4h',           # 4H RSI
    'fr',               # 资金费率
    'consensus',        # 多周期共识
]


def extract_standard_fields(r: dict) -> dict:
    """
    从 brahma_core.analyze() 结果中提取标准字段集
    所有分析输出的唯一字段提取入口
    返回严格对应 STANDARD_FIELDS 的 dict，缺失字段值为 None
    """
    pa    = r.get('params', {}) or {}
    ms    = r.get('momentum', {}) or {}
    sent  = r.get('sentiment', {}) or {}
    cf    = r.get('confluence', {}) or {}
    extra = r.get('extra', {}) or {}
    cf_bd = cf.get('breakdown', {}) or {}

    # GEX磁铁：从 confluence breakdown 或 extra 提取
    gex_min = cf_bd.get('_gex_min')
    if gex_min is None:
        gex_raw = extra.get('gex', {})
        if isinstance(gex_raw, dict):
            gex_min = gex_raw.get('min_strike') or gex_raw.get('max_strike')

    # 15M触发置信
    trig = extra.get('trigger', {})
    trigger_conf = (trig.get('confidence') or trig.get('conf')) if isinstance(trig, dict) else None
    if trigger_conf is None:
        for k, v in cf_bd.items():
            if '触发' in str(k) and isinstance(v, (int, float)):
                trigger_conf = v
                break

    # 多周期共识
    mt = extra.get('multitf', {})
    consensus = (mt.get('consensus') if isinstance(mt, dict) else None) or r.get('consensus')

    return {
        'symbol':          r.get('symbol'),
        'regime':          r.get('regime'),
        'score':           r.get('score_final') or r.get('score') or cf.get('grade_num'),
        'direction':       r.get('signal_dir') or r.get('direction'),
        'entry_lo':        pa.get('entry_lo'),
        'entry_hi':        pa.get('entry_hi'),
        'sl':              pa.get('stop_loss'),
        'tp1':             pa.get('tp1'),
        'tp2':             pa.get('tp2'),
        'rr':              pa.get('rr1'),
        'structure_grade': (r.get('effective_grade') or r.get('structure_grade')
                             or r.get('ms', {}).get('structure', {}).get('grade')),
        'gex_min':         gex_min,
        'trigger_conf':    trigger_conf,
        'valid':           r.get('valid_signal') or r.get('valid'),
        'price':           r.get('price'),
        'rsi_1h':          ms.get('rsi_1h'),
        'rsi_4h':          ms.get('rsi_4h'),
        'fr':              sent.get('funding_rate'),
        'consensus':       consensus,
        'elapsed':         r.get('elapsed'),
    }


SEP = '─' * 48


def _build_action_guide(r: dict, f: dict) -> list:
    """
    操作指南层 — 苏摩看到的不是评分，而是「现在该怎么做」
    返回 lines 列表，插入到 format_standard_card 尾部
    """
    import re as _re
    lines = []

    direction = f.get('direction', 'NEUTRAL')
    valid     = bool(f.get('valid'))
    score     = float(f.get('score') or 0)
    entry_lo  = f.get('entry_lo')
    entry_hi  = f.get('entry_hi')
    sl        = f.get('sl')
    tp1       = f.get('tp1')
    rr        = f.get('rr')
    rsi_1h    = f.get('rsi_1h')
    rsi_4h    = f.get('rsi_4h')
    price     = f.get('price')
    regime    = f.get('regime', '')

    # 体制分类
    is_bear   = 'BEAR' in (regime or '')
    is_bull   = 'BULL' in (regime or '')
    is_chop   = 'CHOP' in (regime or '')

    # 评分层级
    score_level = 'SKIP' if score < 130 else ('WATCH' if score < 155 else 'ENTER')

    # RSI状态
    rsi4h_val = float(rsi_4h) if rsi_4h not in (None, '?', '') else 50
    rsi1h_val = float(rsi_1h) if rsi_1h not in (None, '?', '') else 50
    rsi_hot   = rsi4h_val > 70
    rsi_cold  = rsi4h_val < 35

    # breakdown里抽取止损依据
    cf_dict   = r.get('confluence', {}) or {}
    bd        = cf_dict.get('breakdown', {}) or {}
    sl_basis  = (r.get('params') or {}).get('sl_basis', '')
    if not sl_basis:
        # 从 breakdown 找支摙1H/4H中最相关的
        for k in ['RSI4H时机惩罚', 'N16_ATR体制', 'SMC结构', '关键位精确度']:
            if k in bd and str(bd[k]) not in ('0', '', '?'):
                sl_basis = f'{k}[{str(bd[k])[:30]}]'
                break
    if not sl_basis:
        sl_basis = 'swing_4H支撑位+ATR缓冲'

    lines.append('[操作指令]')

    # 情况一：有效信号，直接入场
    if valid and score_level == 'ENTER' and entry_lo and sl and tp1:
        dir_cn = '做多' if direction == 'LONG' else '做空'
        sl_pct = abs((float(sl) - float(entry_lo)) / float(entry_lo) * 100) if entry_lo else 0
        lines += [
            f'  ✅ 现在：可入场 {dir_cn}',
            f'  入场区: {_fmt_price(entry_lo)} ~ {_fmt_price(entry_hi)}',
            f'  止损:   {_fmt_price(sl)}  (宽{sl_pct:.1f}%，依据: {sl_basis[:40]})',
            f'  止盈:   TP1={_fmt_price(tp1)}  R:R={rr}',
            f'  仓位建议: NAV的5% 小仓验证，冲破确认后加仓',
        ]

    # 情况二：评分较高但等待15M触发
    elif score_level == 'ENTER' and not valid:
        dir_cn = '做多' if direction == 'LONG' else '做空'
        lines += [
            f'  ⏳ 等待15M触发再入场 ({dir_cn})',
            f'  入场区: {_fmt_price(entry_lo)} ~ {_fmt_price(entry_hi)}',
            f'  触发条件: 15M出现CHoCH或强势阳线破局部高点',
            f'  止损:   {_fmt_price(sl)}  (SL依据: {sl_basis[:40]})',
            f'  止盈:   TP1={_fmt_price(tp1)}  R:R={rr}',
        ]

    # 情况三：监控区（130-155）
    elif score_level == 'WATCH':
        wait_condition = ''
        if rsi_hot and direction == 'LONG':
            wait_condition = f'RSI_4H={rsi4h_val:.0f}→等回落至≠55后重新审视'
        elif rsi_cold and direction == 'SHORT':
            wait_condition = f'RSI_4H={rsi4h_val:.0f}→等回升至≥45后重新审视'
        elif entry_lo:
            dir_cn = '做多' if direction == 'LONG' else '做空'
            wait_condition = f'等价格回踩到 {_fmt_price(entry_lo)} 布局{dir_cn}'
        else:
            wait_condition = '评分不足，等待更强信号'
        lines += [
            f'  👁 监控中——不开仓',
            f'  条件: {wait_condition}',
            f'  评分={score:.0f}，需要达到≥155才执行',
        ]

    # 情况四：Skip + 为什么 + 下一个窗口
    else:
        # 找出最大的减分因素向苏摩解释
        neg_items = []
        for k, v in bd.items():
            m = _re.search(r'([+-]?\d+)', str(v))
            if m:
                n = int(m.group(1))
                if n < -3:
                    neg_items.append((n, k, str(v)[:40]))
        neg_items.sort(key=lambda x: x[0])

        next_condition = ''
        if rsi_hot and direction == 'LONG':
            next_condition = f'RSI_4H需从{rsi4h_val:.0f}降至≤55 预计{int((rsi4h_val-55)/3)}–{int((rsi4h_val-55)/2)}H'
        elif entry_lo and price:
            gap = abs((float(price) - float(entry_lo)) / float(price) * 100)
            next_condition = f'价格需回调{gap:.1f}%至布局区 {_fmt_price(entry_lo)}'
        else:
            next_condition = '等待更强局面形成'

        lines.append(f'  ⚫ SKIP —— 当前不入场')
        if neg_items:
            lines.append(f'  主要阭制:')
            for n, k, desc in neg_items[:3]:
                lines.append(f'    {n:+d}  {k}: {desc}')
        lines.append(f'  下一个窗口条件: {next_condition}')

    return lines

def format_standard_card(r: dict, ts: str = None) -> str:
    """
    固化版标准信号卡 — 统一推送格式
    唯一对外推送文本，基于 extract_standard_fields 保证字段完整
    """
    if r.get('error'):
        return f'❌ {r.get("symbol","?")} 分析失败: {r["error"]}'

    f   = extract_standard_fields(r)
    sym = (f['symbol'] or '?').replace('USDT', '')
    p         = f['price']
    direction = f['direction'] or 'NEUTRAL'
    regime    = f['regime'] or '?'
    score     = f['score'] or 0
    valid     = f['valid']
    entry_lo  = f['entry_lo']
    entry_hi  = f['entry_hi']
    sl        = f['sl']
    tp1       = f['tp1']
    tp2       = f['tp2']
    rr        = f['rr']
    grade     = f['structure_grade']
    gex_min   = f['gex_min']
    tconf     = f['trigger_conf']
    rsi1h     = f['rsi_1h']
    rsi4h     = f['rsi_4h']
    fr        = f['fr']
    consensus = f['consensus'] or '?'
    elapsed   = f.get('elapsed') or 0

    if direction == 'NEUTRAL':
        return (
            f'📊 {sym}/USDT · {regime}\n'
            f'   score={score} | RSI1H={rsi1h} | 无明确方向，等待'
        )

    # SKIP/WATCH 状态仍然展示完整卡片+操作指令（苏摩需要知道为什么等和下一步条件）
    # 不再因 valid=False 提前 return

    dir_icon   = '🔴 SHORT' if direction == 'SHORT' else '🟢 LONG'
    valid_icon = '✅' if valid else '⏳'
    thresh_icon = '🚨' if score >= 160 else ('✅' if score >= 140 else '⚠️')

    lines = [
        SEP,
    ]
    if ts:
        lines.append(f'  ⏱ {ts}')
    lines += [
        f'  {thresh_icon} {sym}/USDT · {dir_icon}  score={score}/175',
        f'  体制: {regime} | 多周期: {consensus}',
    ]
    if p:
        lines.append(f'  当前价: ${p:,.2f}')
    lines.append(f'')
    if entry_lo and entry_hi:
        lines.append(f'  📍 入场区  ${entry_lo:,.2f} ~ ${entry_hi:,.2f}')
    if sl:
        lines.append(f'  🛑 止损    ${sl:,.2f}')
    if tp1 and tp2:
        lines.append(f'  🎯 TP1 ${tp1:,.2f}   TP2 ${tp2:,.2f}')
    if rr:
        g_str = f'  结构等级 {grade}' if grade else ''
        lines.append(f'  📐 R:R {rr}{g_str}')
    lines.append(f'')
    if rsi1h and rsi4h:
        fr_str = f'  |  资金费率 {fr:+.4f}%' if fr is not None else ''
        lines.append(f'  RSI  1H={rsi1h}  4H={rsi4h}{fr_str}')
    if gex_min and tconf:
        lines.append(f'  GEX磁铁 ${gex_min:,.0f}  |  15M置信 {tconf}/100')
    elapsed_str = f' ({elapsed:.1f}s)' if elapsed else ''
    lines.append(f'  {valid_icon} 触发状态: {"有效信号" if valid else "等待15M确认"}{elapsed_str}')

    # ── 操作指令层（苏摩最需要的：当前该怎么做）──────────────────
    lines.append('')
    action_lines = _build_action_guide(r, f)
    lines += action_lines

    # ── 方仓 + 决策树 [设计院封印 2026-08-09 苏摩111] ─────────────────
    try:
        _fc = r.get('fangcang', {})
        if _fc and _fc.get('status') == 'ok':
            from brahma_brain.fangcang_engine import format_fangcang_card
            _fc_text = format_fangcang_card(_fc)
            if _fc_text:
                for _fl in _fc_text.split('\n'):
                    lines.append(_fl)
        else:
            _fc_status = (_fc or {}).get('status', 'not_run')
            _fc_reason = (_fc or {}).get('reason', '')
            lines.append('  【方仓】 ' + _fc_status +
                         ('：' + _fc_reason[:40] if _fc_reason else '：数据不足'))
    except Exception:
        pass
    try:
        _dt = r.get('decision', {})
        _dt_action = _dt.get('action', '')
        _dt_step   = _dt.get('step_passed', 0)
        _dt_reason = _dt.get('reason', '')
        if _dt_action:
            _icons = {'EXECUTE':'🚀','WAIT_15M':'⏳','WAIT_ENTRY':'👀','SKIP':'❌'}
            lines.append(f'  【决策树】 {_icons.get(_dt_action,"🌲")} {_dt_action} | 通过{_dt_step}/5步')
            if _dt_reason:
                lines.append(f'    {_dt_reason[:60]}')
            _ep = _dt.get('entry_plan', {})
            if _ep and _ep.get('price'):
                lines.append(f'    入场:${_ep["price"]:,.2f} SL:${_ep.get("sl_price",0):,.2f} RR:{_ep.get("rr",0):.1f}x')
    except Exception:
        pass
    # ── [END 方仓+决策树] ──────────────────────────────────────────────

    lines.append(SEP)

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


# ══════════════════════════════════════════════════════════════════════════════
# 设计院顶层标签体系 v1.0  ·  2026-06-30 封印
# 每一条梵天输出必须携带独有标签，标签不对 = 拒绝识别
# ══════════════════════════════════════════════════════════════════════════════

# 标签格式：  [BRAHMA:{级别}:{来源}:{符号}:{评分}:{方向}:{体制}:{时间戳}]
# 级别:    SIG  = 有效信号（score≥140 valid=True）
#          WATCH= 观察信号（score 120~139 / neutral）
#          WARN = 警告（score<120 / 字段缺失）
#          ERR  = 系统错误（异常 / inline污染 / 字段missing）
# 来源:    RUNNER=brahma_analysis_runner（唯一合法来源）
#          INLINE=裸HTTP临时计算（非法，强制标 ERR）
# 时间戳:  UTC YYYYMMDDHHmm（6位分钟精度，防重放）

import hashlib as _hashlib
from datetime import datetime as _dt, timezone as _tz


def build_output_tag(r: dict, source: str = 'RUNNER') -> str:
    """
    为每条梵天输出生成唯一防混淆标签

    规则：
      - source='RUNNER' + valid=True + score≥140  → SIG
      - source='RUNNER' + score 120~139            → WATCH
      - source='RUNNER' + score<120 或字段缺失     → WARN
      - source!='RUNNER' 或 error 或 inline估算   → ERR
      - 每条标签含 sha8 校验（前8位md5），防止篡改

    返回: 形如 [BRAHMA:SIG:RUNNER:BTC:151.8:SHORT:BEAR_TREND:202606301112:a3f9c1d2]
    """
    ts = _dt.now(_tz.utc).strftime('%Y%m%d%H%M')

    # ── 错误/非法来源 ──────────────────────────────────────
    if r.get('error') or source != 'RUNNER':
        level = 'ERR'
        sym   = r.get('symbol', 'UNKNOWN').replace('USDT', '')
        score = '0'
        direction = 'ERR'
        regime    = 'ERR'
        payload   = f'ERR:{sym}:{ts}'
        sha8 = _hashlib.md5(payload.encode()).hexdigest()[:8]
        return f'[BRAHMA:{level}:{source}:{sym}:{score}:{direction}:{regime}:{ts}:{sha8}]'

    # ── 正常路径：提取标准字段 ─────────────────────────────
    f         = extract_standard_fields(r)
    sym       = (f.get('symbol') or r.get('symbol', '?')).replace('USDT', '')
    score_raw = f.get('score') or 0
    score     = round(float(score_raw), 1)
    direction = f.get('direction') or 'NEUTRAL'
    regime    = f.get('regime') or 'UNKNOWN'
    valid     = bool(f.get('valid'))
    missing   = [k for k in ['regime','score','direction','entry_lo','sl','tp1'] if f.get(k) is None]

    # ── 级别判定 ───────────────────────────────────────────
    if missing:
        level = 'WARN'
    elif source != 'RUNNER':
        level = 'ERR'
    elif valid and score >= 140:
        level = 'SIG'
    elif score >= 120:
        level = 'WATCH'
    else:
        level = 'WARN'

    # ── sha8 防篡改校验 ───────────────────────────────────
    payload = f'{level}:{sym}:{score}:{direction}:{regime}:{ts}'
    sha8 = _hashlib.md5(payload.encode()).hexdigest()[:8]

    return f'[BRAHMA:{level}:{source}:{sym}:{score}:{direction}:{regime}:{ts}:{sha8}]'


def tag_is_valid_signal(tag: str) -> bool:
    """快速检查标签是否为有效信号（SIG级别 + RUNNER来源）"""
    return tag.startswith('[BRAHMA:SIG:RUNNER:') and ':ERR:' not in tag


def tag_parse(tag: str) -> dict:
    """解析标签为结构化字典，便于下游系统识别"""
    try:
        inner = tag.strip('[]').replace('BRAHMA:', '', 1)
        parts = inner.split(':')
        # parts: level, source, sym, score, direction, regime, ts, sha8
        return {
            'level':     parts[0],
            'source':    parts[1],
            'symbol':    parts[2] + 'USDT',
            'score':     float(parts[3]),
            'direction': parts[4],
            'regime':    parts[5],
            'ts':        parts[6],
            'sha8':      parts[7] if len(parts) > 7 else '',
            'valid_sig': parts[0] == 'SIG' and parts[1] == 'RUNNER',
        }
    except Exception as e:
        return {'level': 'ERR', 'error': str(e), 'raw': tag}


# ══════════════════════════════════════════════════════════════════════════════
# 梵天全景矩阵报告 v1.0
# 设计院六方联合封印 2026-07-13
# 融合：35维评分权重 + 外部扩展层 + 时机过滤 + 顶级合约视角
# 调用：from brahma_brain.formatter import brahma_panorama_report
# ══════════════════════════════════════════════════════════════════════════════

def brahma_panorama_report(r: dict, compact: bool = False) -> str:
    """
    梵天全景矩阵报告 — 完整版
    将 run_analysis() 的所有维度格式化为可读的全景报告

    compact=True → 精简版（推送用）
    compact=False → 完整版（分析审计用）
    """
    from datetime import datetime, timezone

    if r.get('error'):
        return f'❌ 分析失败: {r["error"]}'

    ts_now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # ── 基础字段 ─────────────────────────────────────────────────────────
    f         = extract_standard_fields(r)
    sym_raw   = f['symbol'] or 'BTCUSDT'
    sym       = sym_raw.replace('USDT', '')
    regime    = f['regime'] or '?'
    score     = float(f['score'] or 0)
    direction = f['direction'] or 'NEUTRAL'
    valid     = bool(f['valid'])
    entry_lo  = f['entry_lo']
    entry_hi  = f['entry_hi']
    sl        = f['sl']
    tp1       = f['tp1']
    tp2       = f['tp2']
    rr        = f['rr']
    grade     = f['structure_grade']
    rsi_1h    = f['rsi_1h']
    rsi_4h    = f['rsi_4h']
    price     = f['price']
    consensus = f['consensus'] or '?'

    # ── 扩展字段 ─────────────────────────────────────────────────────────
    timing    = r.get('timing_status', 'UNKNOWN')
    t_score   = r.get('timing_score', 0)
    action    = r.get('action', 'WATCH')
    ext_bonus = r.get('_ext_score_bonus', 0)
    ext_det   = r.get('_ext_score_detail', {})

    # 外部层数据
    liq  = r.get('_liq_heatmap', {}) or {}
    cfr  = r.get('_cross_fr', {}) or {}
    wh   = r.get('_whale', {}) or {}
    opc  = r.get('_options_pc', {}) or {}
    mn   = r.get('_miner', {}) or {}

    # breakdown（评分明细）
    cf_dict  = r.get('confluence', {}) or {}
    breakdown = cf_dict.get('breakdown', {}) or {}

    # ── 图标计算 ─────────────────────────────────────────────────────────
    dir_icon   = '🟢 LONG' if direction == 'LONG' else ('🔴 SHORT' if direction == 'SHORT' else '⚪ NEUTRAL')
    valid_icon = '✅ 有效信号' if valid else '⏳ 等待确认'
    score_icon = '🚨' if score >= 165 else ('🔥' if score >= 155 else ('⚠️' if score >= 130 else '📊'))
    timing_icon = {'READY': '🟢', 'MONITOR': '🟡', 'WAIT': '⏸', 'STANDBY': '⚫'}.get(timing, '❓')

    SEP = '─' * 44

    lines = [
        f'🏛️ **梵天全景矩阵** · {sym}/USDT  {ts_now}',
        SEP,
    ]

    # ── A: 核心信号 ───────────────────────────────────────────────────────
    lines += [
        f'**A · 核心信号**',
        f'  {score_icon} score={score:.1f}/175  {valid_icon}',
        f'  方向: {dir_icon}  |  体制: {regime}',
        f'  多周期共识: {consensus}  |  结构等级: {grade}',
        f'  动作: {action}  |  {timing_icon} 时机: {timing}(得分={t_score})',
        '',
    ]

    # ── B: 策略参数 ───────────────────────────────────────────────────────
    if valid and entry_lo and sl and tp1:
        lines += [
            f'**B · VIP策略参数**',
        ]
        if price:
            lines.append(f'  当前价: {_fmt_price(price)}')
        lines += [
            f'  📍 入场区: {_fmt_price(entry_lo)} ~ {_fmt_price(entry_hi)}',
            f'  🛑 止损:   {_fmt_price(sl)}',
            f'  🎯 TP1:    {_fmt_price(tp1)}' + (f'   TP2: {_fmt_price(tp2)}' if tp2 else ''),
            f'  📐 R:R     {rr}',
            '',
        ]

    # ── B2: SMC结构细节（OB + FVG + 流动性）─────────────────────────────
    if not compact:
        smc_data = r.get('smc', {})
        if isinstance(smc_data, dict):
            ob_data  = smc_data.get('order_blocks', {})
            fvg_data = smc_data.get('fvg', {})
            liq_data = smc_data.get('liquidity', {})
            pd_data  = smc_data.get('pd_zone', {})

            smc_lines = ['**B2 · SMC结构细节**']

            # OB展示（最近各1个）
            bull_obs = ob_data.get('bull_obs', [])
            bear_obs = ob_data.get('bear_obs', [])
            if bull_obs or bear_obs:
                smc_lines.append('  📦 Order Blocks:')
                for ob in bull_obs[:2]:
                    smc_lines.append(f'    🟢 做多OB {_fmt_price(ob["low"])}~{_fmt_price(ob["high"])}  距当前{ob["dist_pct"]:.2f}%')
                for ob in bear_obs[:2]:
                    smc_lines.append(f'    🔴 做空OB {_fmt_price(ob["low"])}~{_fmt_price(ob["high"])}  距当前{ob["dist_pct"]:.2f}%')

            # FVG展示
            bull_fvg = fvg_data.get('bull_fvg', [])
            bear_fvg = fvg_data.get('bear_fvg', [])
            if bull_fvg or bear_fvg:
                smc_lines.append('  🕳️ FVG(公平价值缺口):')
                for fvg in bull_fvg[:2]:
                    filled = '✅已回填' if fvg.get('filled') else '⏳未回填'
                    smc_lines.append(f'    🟢 多FVG {_fmt_price(fvg["bottom"])}~{_fmt_price(fvg["top"])}  gap={fvg["gap_pct"]:.2f}%  {filled}')
                for fvg in bear_fvg[:2]:
                    filled = '✅已回填' if fvg.get('filled') else '⏳未回填'
                    smc_lines.append(f'    🔴 空FVG {_fmt_price(fvg["bottom"])}~{_fmt_price(fvg["top"])}  gap={fvg["gap_pct"]:.2f}%  {filled}')

            # 流动性池
            eq_highs = liq_data.get('equal_highs', [])
            eq_lows  = liq_data.get('equal_lows', [])
            if eq_highs or eq_lows:
                smc_lines.append('  💧 流动性池:')
                for h in eq_highs[:2]:
                    smc_lines.append(f'    🔴 空头止损池(等高) {_fmt_price(h["level"])}  距{h["dist_pct"]:.2f}%')
                for l in eq_lows[:2]:
                    smc_lines.append(f'    🟢 多头止损池(等低) {_fmt_price(l["level"])}  距{l["dist_pct"]:.2f}%')

            # PD区域
            if pd_data:
                smc_lines.append(f'  🎯 PD区域: {pd_data.get("zone","?")}  位置={pd_data.get("position",0):.1%}  {pd_data.get("note","")[:40]}')

            if len(smc_lines) > 1:
                lines += smc_lines
                lines.append('')

    # ── B2.2: score审计 + OI信号 + LLM裁决 ────────────────────────────────
    if not compact:
        _sa = r.get('_score_audit', {}) or {}
        _oi_sc = r.get('_oi_score')
        _oi_dt = r.get('_oi_details', []) or []
        _llc   = r.get('_llm_council', {}) or {}
        _cp    = r.get('_condition_plan', {}) or {}
        b22_lines = []

        # score审计trail
        if _sa.get('raw') is not None:
            b22_lines.append(f'**B2.2 · 评分审计**')
            b22_lines.append(f'  原始分={_sa.get("raw")} 外部层{_sa.get("ext_adj",0):+.1f} OI{_sa.get("oi_bonus",0):+.1f} → **最终={_sa.get("final")}**')

        # OI信号
        if _oi_sc is not None:
            _oi_icon = '📈' if _oi_sc > 0 else '📉'
            b22_lines.append(f'  OI信号: {_oi_icon} {_oi_sc:+.1f}分  {" | ".join(str(d)[:40] for d in _oi_dt[:2])}')

        # LLM裁决
        if _llc.get('verdict'):
            _cached = ' (缓存)' if _llc.get('cached') else ''
            b22_lines.append(f'  LLM裁决{_cached}: {_llc["verdict"][:50]}  adj={_llc.get("adj",0):+.0f}')

        # 条件单计划卡 [FIX 2026-08-02: condition_order_matrix返回triggers而非legs]
        _cp_triggers = _cp.get('triggers', {})
        if _cp_triggers or _cp.get('symbol'):
            _n_trig = len(_cp_triggers)
            _cp_sym = _cp.get('symbol', '')
            _imm_warn = _cp.get('immediate_warnings', [])
            b22_lines.append(f'  🎯 条件单计划[{_cp_sym}]: {_n_trig}条触发规则')
            if _imm_warn:
                b22_lines.append(f'  ⚠️ 立即警告: {_imm_warn[0][:50]}')
            # 展示P0/P3关键触发
            if 'P0_生死线' in _cp_triggers:
                _p0 = _cp_triggers['P0_生死线']
                b22_lines.append(f"    P0生死线: {str(_p0.get('condition',''))[:40]}")
            if 'P3_时间止损' in _cp_triggers:
                _p3 = _cp_triggers['P3_时间止损']
                b22_lines.append(f"    P3时间线: {str(_p3.get('deadline',''))[:25]}")

        if b22_lines:
            lines += b22_lines
            lines.append('')

    # ── B2.5: 达摩院节点裁决 (nodes_verdict) ────────────────────────────
    if not compact:
        _dv = r.get('nodes_verdict', '')
        _dn = r.get('dharma_nodes', {}) or {}
        if _dv and _dn:
            _dn_detail = _dn.get('detail', '') if isinstance(_dn, dict) else ''
            _dn_mult   = _dn.get('score_mult', 1.0) if isinstance(_dn, dict) else 1.0
            _dn_pass   = _dn.get('nodes_pass', True) if isinstance(_dn, dict) else True
            _dn_icon   = '✅' if _dn_pass else '⚠️'
            dv_lines = [f'**B2.5 · 达摩院裁决**  {_dn_icon} {_dv}  (score×{_dn_mult:.2f})']
            if _dn_detail:
                for _dl in str(_dn_detail).split('|')[:3]:
                    _dl = _dl.strip()
                    if _dl:
                        dv_lines.append(f'  {_dl[:70]}')
            lines += dv_lines
            lines.append('')

        # ── B3: 清算热力图集群（近距离展示）────────────────────────────────
    if not compact:
        liq_full = r.get('_liq_heatmap', {}) or {}
        if liq_full:
            liq3_lines = ['**B3 · 清算集群地图**']

            # [Bug3/4修复 2026-08-05] 优先级: liq_density三所实单 > tardis历史 > 估算
            # Bug4修复: 空头爆仓是"价格上涨触发"（不是"下跌"）
            _ld_walls = liq_full.get('_liq_density_walls', {})
            if _ld_walls and (_ld_walls.get('above_walls') or _ld_walls.get('below_walls')):
                # 最高优先级：liq_density三所实时强平数据
                _liq_bias = _ld_walls.get('liq_bias', 'NEUTRAL')
                _sources  = _ld_walls.get('sources', '')
                liq3_lines.append(f'  [数据源: 三所实时强平 {_sources} | 偏向={_liq_bias}]')
                _ab = _ld_walls.get('above_walls', [])[:4]  # 上方空头清算墙
                _bl = _ld_walls.get('below_walls', [])[:4]  # 下方多头清算墙
                _px = liq_full.get('price', 0) or 0
                if _ab:
                    liq3_lines.append(' 🔴 上方空头爆仓墙（价格上涨触发→轧空目标）:')
                    for _wp, _wv in _ab:
                        _d = (_wp - _px) / _px * 100 if _px else 0
                        liq3_lines.append(f'    +{_d:.2f}% → {_fmt_price(_wp)}  💥{_wv/1e6:.1f}M')
                if _bl:
                    liq3_lines.append(' 🟢 下方多头爆仓墙（价格下跌触发→洗盘风险）:')
                    for _wp, _wv in _bl:
                        _d = (_px - _wp) / _px * 100 if _px else 0
                        liq3_lines.append(f'    -{_d:.2f}% → {_fmt_price(_wp)}  💥{_wv/1e6:.1f}M')
            elif liq_full.get('_has_tardis'):
                _tdate = liq_full.get('tardis_date', '?')
                liq3_lines.append(f'  [数据源: Tardis真实清算 {_tdate}]')
                _tw_short = liq_full.get('tardis_short_walls', {})
                _tw_long  = liq_full.get('tardis_long_walls', {})
                if _tw_short:
                    liq3_lines.append(' 📈 空头爆仓密集区（价格上涨触发→轧空目标）:')
                    for _pct, _entry in sorted(_tw_short.items(), key=lambda x: float(x[0]))[:5]:
                        _wp, _wv = _entry
                        liq3_lines.append(f'    +{_pct}% → {_fmt_price(_wp)}  💥{_wv:.1f}M')
                if _tw_long:
                    liq3_lines.append(' 📉 多头爆仓密集区（价格下跌触发→洗盘风险）:')
                    for _pct, _entry in sorted(_tw_long.items(), key=lambda x: float(x[0]))[:5]:
                        _wp, _wv = _entry
                        liq3_lines.append(f'    -{_pct}% → {_fmt_price(_wp)}  💥{_wv:.1f}M')
            else:
                # 回落到50x/100x杠杆估算数据（已修复Bug1：nearest用50x）
                short_map = liq_full.get('short_liq_map', {})
                long_map  = liq_full.get('long_liq_map', {})
                _px = liq_full.get('price', 0) or 0
                if short_map or long_map:
                    liq3_lines.append('  📊 杠杆清算估算（上方空头清算墙）:')
                    for lev_k in ['50', '20', '10']:
                        if lev_k in short_map:
                            _lvl = short_map[lev_k]
                            _d = (_lvl - _px) / _px * 100 if _px else 0
                            liq3_lines.append(f'    {lev_k}x → {_fmt_price(_lvl)}  (+{_d:.2f}%)')
                    liq3_lines.append('  📊 杠杆清算估算（下方多头清算墙）:')
                    for lev_k in ['50', '20', '10']:
                        if lev_k in long_map:
                            _lvl = long_map[lev_k]
                            _d = (_px - _lvl) / _px * 100 if _px else 0
                            liq3_lines.append(f'    {lev_k}x → {_fmt_price(_lvl)}  (-{_d:.2f}%)')

            # 订单簿信息（共用）
            ask_cls = liq_full.get('top_ask_clusters', [])
            bid_cls = liq_full.get('top_bid_clusters', [])
            if ask_cls:
                liq3_lines.append(f'  📌 订单簿ASK密集: {_fmt_price(ask_cls[0][0])}  量={ask_cls[0][1]/1e6:.2f}M')
            if bid_cls:
                liq3_lines.append(f'  📌 订单簿BID密集: {_fmt_price(bid_cls[0][0])}  量={bid_cls[0][1]/1e6:.2f}M')

            if len(liq3_lines) > 1:
                lines += liq3_lines
                lines.append('')

    # ── C: 评分权重矩阵（高贡献维度）─────────────────────────────────────
    if not compact and breakdown:
        lines.append(f'**C · 评分权重矩阵（实盘维度）**')

        # 分类整理 breakdown
        plus_items  = []
        minus_items = []
        mult_items  = []
        for k, v in breakdown.items():
            sv = str(v)
            if sv.startswith('×') or '乘数' in k or 'mult' in k.lower() or '降级' in sv:
                mult_items.append((k, sv))
            elif sv.startswith('+') or (sv and sv[0].isdigit() and int(sv.split()[0]) > 0 if sv.split() and sv.split()[0].lstrip('+-').isdigit() else False):
                try:
                    val = float(sv.split('(')[0].replace('+','').strip())
                    if val > 0:
                        plus_items.append((k, sv, val))
                except:
                    if '+' in sv:
                        plus_items.append((k, sv, 0))
            elif sv.startswith('-') or (sv.startswith('0') and '(' not in sv):
                minus_items.append((k, sv))

        # 排序：贡献值高的先显示
        try:
            plus_items.sort(key=lambda x: -x[2])
        except:
            pass

        lines.append('  🟢 得分贡献（主力）:')
        for k, v, _ in plus_items[:8]:
            lines.append(f'    {k[:18]:<18} {v[:30]}')

        if minus_items:
            lines.append('  🔴 扣分/风险项:')
            for k, v in minus_items[:5]:
                lines.append(f'    {k[:18]:<18} {v[:30]}')

        if mult_items:
            lines.append('  ⚙️ 体制/结构乘数:')
            for k, v in mult_items[:4]:
                lines.append(f'    {k[:18]:<18} {v[:30]}')
        lines.append('')

    # ── D: 外部扩展层（今日修复 2026-07-13）─────────────────────────────
    lines += [
        f'**D · 外部扩展层  +{ext_bonus}分**',
    ]

    # liq_heatmap [Bug3修复 2026-08-05: 展示三所实时数据]
    liq_b = ext_det.get('liq_heatmap', 0)
    if not isinstance(liq_b, str):
        _ld_w = liq.get('_liq_density_walls', {})
        if _ld_w and (_ld_w.get('above_walls') or _ld_w.get('below_walls')):
            # 优先用三所实时强平数据
            _ld_src = _ld_w.get('sources', '')
            _ld_bias = _ld_w.get('liq_bias', 'N')
            _ld_ab = _ld_w.get('above_walls', [])
            _ld_bl = _ld_w.get('below_walls', [])
            _ld_px = liq.get('price', 0) or 0
            _nearest_short = f'{_fmt_price(_ld_ab[0][0])}(+{(_ld_ab[0][0]-_ld_px)/_ld_px*100:.1f}%,{_ld_ab[0][1]/1e6:.0f}M)' if _ld_ab and _ld_px else '无'
            _nearest_long  = f'{_fmt_price(_ld_bl[0][0])}(-{(_ld_px-_ld_bl[0][0])/_ld_px*100:.1f}%,{_ld_bl[0][1]/1e6:.0f}M)' if _ld_bl and _ld_px else '无'
            liq_str = f'三所={_ld_src}|{_ld_bias}|空头墙:{_nearest_short}  多头墙:{_nearest_long}'
        else:
            nsl = liq.get('nearest_short_liq', 0)
            nll = liq.get('nearest_long_liq', 0)
            d_short = liq.get('dist_to_short_liq', 0)
            d_long  = liq.get('dist_to_long_liq', 0)
            liq_str = f'50x空头{_fmt_price(nsl)}(+{d_short:.1f}%)  50x多头{_fmt_price(nll)}(-{d_long:.1f}%)' if nsl else '数据获取中'
        lines.append(f'  🔥 清算热力图  {liq_b:+d}分  {liq_str}')
    else:
        lines.append(f'  🔥 清算热力图  skip({liq_b})')

    # cross_fr
    fr_b = ext_det.get('cross_fr', 0)
    if not isinstance(fr_b, str):
        frs_str = ''
        if cfr.get('frs'):
            parts = [f'{ex}:{v:+.4f}%' for ex, v in cfr['frs'].items() if v is not None]
            frs_str = '  '.join(parts[:3])
        lines.append(f'  📊 跨所FR套利  {fr_b:+d}分  spread={cfr.get("spread",0):+.4f}%  [{frs_str}]')
    else:
        lines.append(f'  📊 跨所FR套利  skip')

    # whale
    wh_b = ext_det.get('whale', 0)
    if not isinstance(wh_b, str):
        wh_str = f'{wh.get("whale_direction","?")}  LS={wh.get("whale_ls_trend","?")}  OI_1h={wh.get("oi_1h_chg",0):+.2f}%'
        lines.append(f'  🐋 鲸鱼监控    {wh_b:+d}分  {wh_str}')
    else:
        lines.append(f'  🐋 鲸鱼监控    skip({wh_b})')

    # options_pc
    pc_b = ext_det.get('options_pc', 0)
    if not isinstance(pc_b, str):
        pc_str = f'P/C={opc.get("pc_oi_ratio","?")}  {opc.get("interpretation_oi","?")}' if opc else '数据获取中'
        lines.append(f'  📈 期权P/C比   {pc_b:+d}分  {pc_str}')
    else:
        lines.append(f'  📈 期权P/C比   skip')

    # miner BTC专属 / ETH用供应感知替代
    mn_b = ext_det.get('miner', 0)
    if mn_b is not None and not isinstance(mn_b, str):
        if 'BTC' in sym_raw:
            mn_str = f'利润率={mn.get("miner_margin_pct",0):+.1f}%  {mn.get("pressure_signal","?")}' if mn else '数据获取中'
            lines.append(f'  ⛏️ 矿工卖压    {mn_b:+d}分  {mn_str}')
        elif 'ETH' in sym_raw:
            eth_spread = cfr.get('spread', 0) or 0
            eth_oi1h   = wh.get('oi_1h_chg', 0) or 0
            if eth_spread > 0.008:
                supply_sig = f'正FR套利spread={eth_spread:+.4f}% 多头付费追切'
            elif eth_spread < -0.008:
                supply_sig = f'负费率 空头付费→自然强化多头'
            else:
                supply_sig = f'FR中性(spread={eth_spread:+.4f}%)'
            oi_str = f'  OI_1h={eth_oi1h:+.2f}%' if abs(eth_oi1h) > 0.1 else ''
            lines.append(f'  🟣 ETH供应感知  ——  {supply_sig}{oi_str}  (PoS无矿工卖压)')

    lines.append('')

    # ── E: 市场感知层 ─────────────────────────────────────────────────────
    rsi_1h_disp = f'{rsi_1h:.1f}' if rsi_1h else '?'
    rsi_4h_disp = f'{rsi_4h:.1f}' if rsi_4h else '?'
    fr_val = f['fr']
    fr_str = f'  FR={fr_val:+.4f}%' if fr_val is not None else ''
    lines += [
        f'**E · 市场感知**',
        f'  RSI  1H={rsi_1h_disp}  4H={rsi_4h_disp}{fr_str}',
    ]

    # Kronos + 矛盾预警
    kronos_bd = breakdown.get('s23_kronos', '')
    p_up_val  = float(r.get('s23_p_up', 0.5) or 0.5)
    if kronos_bd:
        lines.append(f'  Kronos: {str(kronos_bd)[:60]}')
    # Kronos与score方向矛盾检测
    if score >= 155 and direction == 'LONG' and p_up_val < 0.15:
        lines.append(f'  ⚡ Kronos矛盾: score={score:.0f}看多 但p_up={p_up_val:.2f}极低 → 短期动量背离 建议等CHoCH确认')
    elif score >= 155 and direction == 'SHORT' and p_up_val > 0.85:
        lines.append(f'  ⚡ Kronos矛盾: score={score:.0f}看空 但p_up={p_up_val:.2f}极高 → 短期反弹风险 建议等回调确认')

    # 宏观
    macro_bd = breakdown.get('宏观+事件', '')
    if macro_bd:
        lines.append(f'  宏观层: {str(macro_bd)[:60]}')

    # 智能钱
    sm_bd = breakdown.get('_smart_money', '')
    if sm_bd:
        lines.append(f'  智能钱: {str(sm_bd)[:60]}')

    lines.append('')

    # ── F: 关键风险信号 ───────────────────────────────────────────────────
    risks = []
    atr_bd = breakdown.get('N16_ATR体制', '')
    if 'ATR禁区' in str(atr_bd):
        risks.append(f'⚠️ ATR低波动禁区: {str(atr_bd)[:50]}')
    fix1_bd = breakdown.get('FIX1_假牛市', '')
    if fix1_bd:
        risks.append(f'⚠️ 假牛市检测: {str(fix1_bd)[:50]}')
    obv_bd = breakdown.get('OBV方向_v2', '')
    if 'OBV反向' in str(obv_bd):
        risks.append('⚠️ OBV反向（量能未配合方向）')
    if float(breakdown.get('宏观+事件', 0) if isinstance(breakdown.get('宏观+事件'), (int,float)) else 0) < -8:
        risks.append(f'⚠️ 宏观环境不利: {breakdown.get("宏观+事件")}')

    if risks:
        lines.append(f'**F · 风险信号**')
        for risk in risks:
            lines.append(f'  {risk}')
        lines.append('')

    # ── G: 结论 ───────────────────────────────────────────────────────────
    if valid:
        if action == 'ENTER_FULL' and timing == 'READY':
            conclusion = '🚀 信号完整，时机就绪，可执行建仓'
        elif action in ('ENTER_FULL', 'ENTER') and timing == 'MONITOR':
            conclusion = '🟡 信号有效，等待时机门（CHoCH/RSI确认）'
        elif action == 'ENTER_WATCH':
            conclusion = '👀 WATCH状态，监控入场条件'
        else:
            conclusion = f'✅ {action} | {timing}'
    else:
        gap = 155 - score
        conclusion = f'⏳ 距有效信号门槛差 {gap:.1f}分（需score≥155）'

    lines += [
        f'**G · 结论**',
        f'  {conclusion}',
        SEP,
    ]

    # ── 全景摘要卡 (_panorama_card) ──────────────────────────────────
    if not compact:
        _pc = r.get('_panorama_card', '') or ''
        if _pc and len(str(_pc)) > 20:
            lines.append('**全景摘要**')
            for _pl in str(_pc).split('\n')[:8]:
                if _pl.strip():
                    lines.append(f'  {_pl[:80]}')
            lines.append('')

    # ── 方仓经验引擎摘要 [设计院封印 2026-08-09 苏摩111] ────────────────
    # 注入每张信号卡片：历史相似案例 + 概率矩阵
    # [修复] unavailable时也要显示原因，不能静默跳过
    try:
        _fc = r.get('fangcang', {})
        if _fc and _fc.get('status') == 'ok':
            from brahma_brain.fangcang_engine import format_fangcang_card
            _fc_text = format_fangcang_card(_fc)
            if _fc_text:
                lines.append('')
                for _fl in _fc_text.split('\n'):
                    lines.append(_fl)
        else:
            _fc_status = (_fc or {}).get('status', 'not_run')
            _fc_reason = (_fc or {}).get('reason', '')
            lines.append('')
            lines.append('  【方仓引擎】 ' + _fc_status +
                         ('：' + _fc_reason[:40] if _fc_reason else '：未运行或数据不足'))
    except Exception:
        pass  # 降级静默，不影响主卡片

    # ── 决策树输出 [设计院封印 2026-08-09 苏摩111] ────────────────────
    # [修复] brahma_engine.analyze()现已调用decide()，结果注入_result['decision']
    try:
        _dt = r.get('decision', {})
        _dt_action = _dt.get('action', '')
        _dt_reason = _dt.get('reason', '')
        _dt_step   = _dt.get('step_passed', 0)
        if _dt_action:
            _dt_icons = {'EXECUTE': '🚀', 'WAIT_15M': '⏳', 'WAIT_ENTRY': '👀', 'SKIP': '❌'}
            _dt_icon  = _dt_icons.get(_dt_action, '🌲')
            lines.append('')
            lines.append(f'  【决策树】 {_dt_icon} {_dt_action} | 通过{_dt_step}/5步')
            if _dt_reason:
                lines.append(f'    原因: {_dt_reason[:60]}')
            _ep = _dt.get('entry_plan', {})
            if _ep and _ep.get('price'):
                lines.append(f'    入场: ${_ep["price"]:,.2f}  SL: ${_ep.get("sl_price",0):,.2f}  RR: {_ep.get("rr",0):.1f}x')
    except Exception:
        pass  # 决策树格式化失败不影响主卡片

    return '\n'.join(lines)

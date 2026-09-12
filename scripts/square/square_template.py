#!/usr/bin/env python3
"""
square_template.py — 姓赵不宣广场统一品牌模板引擎 v1.0
[设计院封印 2026-09-11 苏摩111]

替代旧版LLM重写（rewrite_as_trader），改纯Python模板填充：
  - 0秒延迟（vs LLM 45秒超时）
  - 0风格漂移（vs LLM每次不同语气）
  - 0内部术语泄漏（模板不产生禁用词）
  - 100%品牌统一（每帖🌿前缀+📊后缀）

4套模板：
  1. 旗舰帖 — 战场报告（80维全输出）
  2. 信号帖 — 异动捕捉（20维SMC+OI+FR+清算）
  3. 教育帖 — 实盘案例教学（1个SMC概念+当日实盘）
  4. 宏观帖 — 事件前瞻（宏观+体制+影响标的）
"""

from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

# ═══════════════════════════════════════════════════════════════
# 统一品牌锚点
# ═══════════════════════════════════════════════════════════════

BRAND_PREFIX = '🌿 姓赵不宣'
BRAND_SUFFIX = '📊 梵天系统 | 80维数据驱动 | 不是建议'

# ═══════════════════════════════════════════════════════════════
# 1. 旗舰帖模板 — 战场报告
# ═══════════════════════════════════════════════════════════════

def build_battlefield_report(sym, analysis_data):
    """
    旗舰帖：战场报告，观点先行+数据支撑
    核心改变：不罗列数据，先给判断，再用数据解释为什么
    """
    date_str = datetime.now(CST).strftime('%m/%d')

    regime = analysis_data.get('regime', 'CHOP_MID')
    score = analysis_data.get('score', 0)
    fvg_dir = analysis_data.get('fvg_dir', 'NEUTRAL')
    fvg_magnet = analysis_data.get('fvg_magnet', 0)
    fvg_pct = analysis_data.get('fvg_pct', 0)
    liq_wall = analysis_data.get('liq_wall', 0)
    liq_pool = analysis_data.get('liq_pool', 0)
    liq_wall_pct = analysis_data.get('liq_wall_pct', 0)
    liq_pool_pct = analysis_data.get('liq_pool_pct', 0)
    oi_signal = analysis_data.get('oi_signal', 'NO_DATA')
    cvd_1h = analysis_data.get('cvd_1h', 0)
    big_long = analysis_data.get('big_long', 50)
    retail_long = analysis_data.get('retail_long', 50)
    hurst = analysis_data.get('hurst', 0.5)
    kappa = analysis_data.get('kappa', 0)
    fr = analysis_data.get('fr', 0)
    atr_1h = analysis_data.get('atr_1h', 0)
    atr_4h = analysis_data.get('atr_4h', 0)
    price = analysis_data.get('price', 0)
    failure_state = analysis_data.get('failure_state', 'GREEN')
    entry_lo = analysis_data.get('entry_lo', 0)
    entry_hi = analysis_data.get('entry_hi', 0)
    sl = analysis_data.get('sl', 0)
    tp1 = analysis_data.get('tp1', 0)
    tp2 = analysis_data.get('tp2', 0)
    bias = analysis_data.get('bias', 'NONE')
    vip_status = analysis_data.get('vip_status', 'WAIT')

    regime_cn = _translate_regime(regime)

    # ── 核心观点生成（观点先行）──
    view_point = _generate_viewpoint(
        sym, regime, score, fvg_dir, fvg_magnet, price,
        liq_wall, liq_pool, oi_signal, cvd_1h,
        big_long, retail_long, hurst, kappa, bias, vip_status
    )

    # ── 操作建议（可执行）──
    action_line = _generate_action(
        fvg_dir, fvg_magnet, price, liq_wall, liq_pool,
        oi_signal, entry_lo, entry_hi, sl, tp1, bias, vip_status
    )

    # ── 矛盾/风险点 ──
    contradiction = _generate_contradiction(
        fvg_dir, oi_signal, big_long, retail_long, hurst, kappa
    )

    # 失效期
    failure_desc = ''
    if failure_state == 'RED':
        failure_desc = '\n⚠️ 失效期RED → 仓位减半'
    elif failure_state == 'YELLOW':
        failure_desc = '\n⚠️ 失效期YELLOW → 仓位×0.75'

    lines = [
        f'{BRAND_PREFIX} | 早间战场报告 {date_str}',
        f'',
        f'{view_point}',
        f'',
        f'━━━ 结构锚点 ━━━',
        f'FVG磁铁: {fvg_dir}@${fvg_magnet:,.0f}（{_fvg_direction(fvg_dir, fvg_magnet, price)}）',
        f'清算: 上方止损墙${liq_wall:,.0f}({liq_wall_pct:+.1f}%) / 下方支撑池${liq_pool:,.0f}({liq_pool_pct:+.1f}%)',
        f'OI: {_oi_description(oi_signal)} | CVD {cvd_1h:+.0f}',
        f'',
        f'━━━ 操作建议 ━━━',
        f'{action_line}',
        f'',
    ]
    if contradiction:
        lines.append(f'━━━ 风险提示 ━━━')
        lines.append(contradiction)
        lines.append(f'')
    lines.extend([
        f'体制: {regime_cn} score={score:.0f} | Hurst={hurst:.2f} | κ={kappa:.3f} | FR={fr:.4f}%',
        f'ATR 1H=${atr_1h:,.0f} 4H=${atr_4h:,.0f}',
        f'{failure_desc}',
        f'',
        f'{BRAND_SUFFIX}',
        f'#{sym} #合约交易',
    ])
    return '\n'.join(lines)


def _generate_viewpoint(sym, regime, score, fvg_dir, fvg_magnet, price,
                       liq_wall, liq_pool, oi_signal, cvd_1h,
                       big_long, retail_long, hurst, kappa, bias, vip_status):
    """生成核心观点：方向+理由，一句话说清"""
    # 基于FVG+OI+CVD+清算的综合判断
    if fvg_dir == 'BULL' and fvg_magnet > price:
        # FVG磁铁在上方→价格大概率被拉上去
        if oi_signal == 'SHORT_BUILD':
            return f'{sym}在${price:,.0f}，FVG磁铁拉向${fvg_magnet:,.0f}（+{(fvg_magnet-price)/price*100:.1f}%），上方空头在增仓。磁铁+空头增仓=大概率先反弹扫空头，到${fvg_magnet:,.0f}附近再回落。'
        elif oi_signal == 'LONG_UNWIND':
            return f'{sym}在${price:,.0f}，FVG磁铁拉向${fvg_magnet:,.0f}但多头在撤资。反弹概率有但没接力盘，到${fvg_magnet:,.0f}大概率是假突破。'
        else:
            return f'{sym}在${price:,.0f}，FVG磁铁拉向${fvg_magnet:,.0f}（+{(fvg_magnet-price)/price*100:.1f}%）。等价格到磁铁位再看结构确认方向。'
    elif fvg_dir == 'BEAR' and fvg_magnet < price:
        if oi_signal == 'SHORT_BUILD':
            return f'{sym}在${price:,.0f}，Bear FVG磁铁在${fvg_magnet:,.0f}（-{(price-fvg_magnet)/price*100:.1f}%），空头持续增仓+CVD卖方主导。磁铁向下+空头加码=下跌动能未结束，先看到支撑池${liq_pool:,.0f}。'
        else:
            return f'{sym}在${price:,.0f}，Bear FVG磁铁在${fvg_magnet:,.0f}。价格被往下拉，但如果到支撑池${liq_pool:,.0f}企稳+1H收阳，可能是个诱空后的反弹。'
    elif fvg_dir == 'BULL' and abs(fvg_magnet - price) / price < 0.005:
        return f'{sym}在${price:,.0f}，Bull FVG磁铁已在当前价。磁铁效应已到位，接下来看是突破向上还是回落。上方止损墙${liq_wall:,.0f}是第一目标。'
    elif fvg_dir == 'BEAR' and abs(fvg_magnet - price) / price < 0.005:
        return f'{sym}在${price:,.0f}，Bear FVG磁铁已到位。价格在磁铁位=方向选择点。破支撑池${liq_pool:,.0f}看空，反弹上止损墙${liq_wall:,.0f}看多。'
    else:
        # 无明确方向
        if score < 60:
            return f'{sym}在${price:,.0f}，震荡中段score={score:.0f}，FVG无明确方向。这种位置我不做，等结构走出来再说。'
        return f'{sym}在${price:,.0f}，FVG方向不明确，等价格到止损墙${liq_wall:,.0f}或支撑池${liq_pool:,.0f}附近再判断。'


def _generate_action(fvg_dir, fvg_magnet, price, liq_wall, liq_pool,
                     oi_signal, entry_lo, entry_hi, sl, tp1, bias, vip_status):
    """生成可操作的建议"""
    if vip_status == 'ENTER' and entry_lo > 0:
        if bias == 'SHORT':
            return f'反弹到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阴 → 做空 | 止损${sl:,.0f} | 目标${tp1:,.0f}'
        elif bias == 'LONG':
            return f'回调到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阳 → 做多 | 止损${sl:,.0f} | 目标${tp1:,.0f}'
    # WAIT状态也要给具体监控条件
    if fvg_dir == 'BULL' and fvg_magnet > price:
        return f'不做空。等价格反弹到${fvg_magnet:,.0f}附近+1H收阴再评估做空。如果直接突破止损墙${liq_wall:,.0f}，追多也不追，等回踩确认。'
    elif fvg_dir == 'BEAR' and fvg_magnet < price:
        return f'不做多。等价格到支撑池${liq_pool:,.0f}+1H收阳再评估试多。如果直接破支撑池，不抄底，等清算结束后看结构。'
    elif fvg_dir == 'BULL' and abs(fvg_magnet - price) / max(price,1) < 0.005:
        return f'等方向选择。破止损墙${liq_wall:,.0f}+放量=追多确认；破支撑池${liq_pool:,.0f}+放量=追空确认。横盘就不动。'
    elif fvg_dir == 'BEAR' and abs(fvg_magnet - price) / max(price,1) < 0.005:
        return f'等方向选择。反弹到止损墙${liq_wall:,.0f}+1H收阴=可空；跌破支撑池${liq_pool:,.0f}+放量=可追空。'
    else:
        return f'等价格到止损墙${liq_wall:,.0f}或支撑池${liq_pool:,.0f}附近再判断。中间位置不动。'


def _generate_contradiction(fvg_dir, oi_signal, big_long, retail_long, hurst, kappa):
    """生成矛盾/风险提示"""
    contradictions = []
    if fvg_dir == 'BULL' and oi_signal in ('SHORT_BUILD', 'LONG_UNWIND'):
        contradictions.append(f'FVG看多但OI={oi_signal}，磁铁向上但资金在做空/撤资')
    if fvg_dir == 'BEAR' and big_long > 58:
        contradictions.append(f'FVG看空但大户{big_long:.0f}%偏多，主力可能知道什么')
    if hurst < 0.45:
        contradictions.append(f'Hurst={hurst:.2f}<0.5随机游走，趋势信号不可靠')
    if kappa < -0.1 and fvg_dir == 'BEAR':
        contradictions.append(f'κ={kappa:.3f}期权Call强但FVG看空，期权市场在押多')
    if kappa > 0.1 and fvg_dir == 'BULL':
        contradictions.append(f'κ={kappa:.3f}期权Put强但FVG看多，期权市场在防跌')
    if not contradictions:
        return ''
    return '\n'.join(contradictions)


def _fvg_direction(fvg_dir, fvg_magnet, price):
    """FVG磁铁方向简述"""
    if not fvg_magnet or not fvg_dir or fvg_dir == 'NEUTRAL':
        return '无方向'
    pct = (fvg_magnet - price) / price * 100 if price > 0 else 0
    if pct > 0.5:
        return f'拉上方{pct:+.1f}%'
    elif pct < -0.5:
        return f'拉下方{pct:+.1f}%'
    return '已在当前价'


# ═══════════════════════════════════════════════════════════════
# 2. 信号帖模板 — 异动捕捉
# ═══════════════════════════════════════════════════════════════

def build_signal_alert(sym, chg, price, high, low, vol, fr, ls,
                       fvg_dir='', fvg_magnet=0, fvg_mid=0,
                       ob_test='', oi_signal='', cvd=0,
                       liq_above=0, liq_below=0,
                       entry_cond='', sl_price=0, tp_price=0,
                       monitor_fr='', monitor_oi=''):
    """
    信号帖：异动捕捉，20维SMC+OI+FR+清算
    """
    now_str = datetime.now(CST).strftime('%m/%d %H:%M')

    # 结构读
    structure_lines = []
    if fvg_dir and fvg_magnet:
        structure_lines.append(f'{fvg_dir} FVG中点{fvg_mid:.4f}，磁铁{fvg_magnet:.4f}')
    if ob_test:
        structure_lines.append(ob_test)
    if oi_signal:
        structure_lines.append(f'OI {oi_signal} → {_oi_short(oi_signal)}')
    structure_lines.append(f'FR {fr:.4f}% = {_fr_meaning(fr)}')
    if liq_above and liq_below:
        structure_lines.append(f'清算：上方空头止损密集{liq_above:.4f}，下方多头止损{liq_below:.4f}')

    # 判断
    judge_lines = []
    pullback = (high - price) / high * 100 if high > 0 else 0
    if chg > 0:
        if fr < -0.1:
            judge_lines.append(f'轧空信号确认，但已从高点回落{pullback:.0f}%，不追')
        else:
            judge_lines.append(f'涨幅{chg:.0f}%后追高风险大，等回踩确认')
    else:
        rebound = (price - low) / low * 100 if low > 0 else 0
        judge_lines.append(f'跌幅{abs(chg):.0f}%，已从低点反弹{rebound:.0f}%')

    if entry_cond:
        judge_lines.append(entry_cond)
    if sl_price and tp_price:
        judge_lines.append(f'止损{sl_price:.4f} | 目标{tp_price:.4f}')

    # 监控
    monitor_lines = []
    if monitor_fr:
        monitor_lines.append(monitor_fr)
    if monitor_oi:
        monitor_lines.append(monitor_oi)

    lines = [
        f'{BRAND_PREFIX} | 异动捕捉',
        f'',
        f'{sym} {chg:+.0f}% | {now_str} CST',
        f'现价 {price:.4f}U | 高 {high:.4f} | 低 {low:.4f} | 成交额 {vol/1e6:.0f}万U',
        f'',
        f'━━━ 结构读 ━━━',
    ]
    lines.extend([f'  {s}' for s in structure_lines])
    lines.append(f'')
    lines.append(f'━━━ 判断 ━━━')
    lines.extend([f'  {j}' for j in judge_lines])
    lines.append(f'')
    if monitor_lines:
        lines.append(f'━━━ 监控 ━━━')
        lines.extend([f'  {m}' for m in monitor_lines])
        lines.append(f'')
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'#{sym} #合约交易')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 3. 教育帖模板 — 实盘案例教学
# ═══════════════════════════════════════════════════════════════

def build_education_post(edu_id, concept, definition_lines, live_example, how_to_use, historical_case=''):
    """
    教育帖：实盘案例教学
    edu_id: 编号 #001
    concept: 'Bear FVG填补'
    definition_lines: ['价格急跌时留下的空白区间...', 'Bear FVG = 下跌中形成，回填时是阻力']
    live_example: 'ETH 4H Bear FVG $2,439-$2,478\n当前$2,458 = FVG中点\n磁铁向下，目标$2,403支撑池'
    how_to_use: 'Bear FVG中点+1H收阴 → 可空\n止损放FVG上沿$2,478之上'
    historical_case: '上周BTC也走了同样的FVG填补：8/28 BULL FVG $77,200-$78,400，9/2回填到$78,193后回落3.2%'
    """
    lines = [
        f'{BRAND_PREFIX} | 实盘教学 #{edu_id:03d}',
        f'',
        f'今日{live_example.split(chr(10))[0]}',
        f'',
        f'什么是{concept}？',
    ]
    for d in definition_lines:
        lines.append(d)
    lines.append(f'')
    lines.append(f'实盘：{live_example}')
    lines.append(f'')
    lines.append(f'怎么用？')
    lines.append(how_to_use)
    if historical_case:
        lines.append(f'')
        lines.append(historical_case)
    lines.append(f'')
    lines.append(f'{BRAND_SUFFIX}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 4. 宏观帖模板 — 事件前瞻
# ═══════════════════════════════════════════════════════════════

def build_macro_outlook(event_name, event_time, expectations,
                         hot_script, cool_script,
                         btc_regime, btc_score, eth_hurst, oi_trend,
                         action_advice, hashtag='#CPIWatch'):
    """
    宏观帖：事件前瞻
    """
    lines = [
        f'{BRAND_PREFIX} | 宏观前瞻',
        f'',
        f'明日{event_name} | {event_time}',
        f'',
        f'━━━ 预期 ━━━',
    ]
    for e in expectations:
        lines.append(e)
    lines.append(f'')
    lines.append(f'━━━ 两种剧本 ━━━')
    lines.append(f'Hot（偏热）：{hot_script}')
    lines.append(f'Cool（偏冷）：{cool_script}')
    lines.append(f'')
    lines.append(f'━━━ 梵天读数 ━━━')
    lines.append(f'BTC体制{_translate_regime(btc_regime)} score={btc_score} → 宏观决定方向')
    lines.append(f'ETH Hurst={eth_hurst:.2f} → 波动率即将放大')
    lines.append(f'OI={oi_trend} → 资金在撤资=事件前避险')
    lines.append(f'')
    lines.append(f'━━━ 操作建议 ━━━')
    lines.append(action_advice)
    lines.append(f'')
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'{hashtag} #BTC')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 辅助翻译函数（人话输出，不露内部代码）
# ═══════════════════════════════════════════════════════════════

def _translate_regime(regime):
    """体制代码翻译成人话"""
    mapping = {
        'BULL_TREND': '牛市趋势',
        'BEAR_TREND': '熊市趋势',
        'CHOP_MID': '震荡中段',
        'CHOP_HIGH': '震荡高位',
        'CHOP_LOW': '震荡低位',
        'BEAR_EARLY': '熊市初期',
        'BEAR_RECOVERY': '熊市恢复',
        'BULL_EARLY': '牛市初期',
    }
    return mapping.get(regime, regime)


def _fvg_description(fvg_dir, fvg_magnet, price):
    """FVG磁铁描述"""
    if not fvg_magnet or not fvg_dir or fvg_dir == 'NEUTRAL':
        return '无明确FVG方向'
    pct = (fvg_magnet - price) / price * 100 if price > 0 else 0
    direction = '拉上方' if pct > 0 else '已在当前价' if abs(pct) < 0.5 else '拉下方'
    return f'{fvg_dir}@${fvg_magnet:,.0f}（{direction}{pct:+.1f}%）'


def _oi_description(oi_signal):
    """OI趋势描述"""
    mapping = {
        'SHORT_BUILD': '空头增仓（SHORT_BUILD）',
        'LONG_BUILD': '多头增仓（LONG_BUILD）',
        'SHORT_SQUEEZE': '空头被挤（SHORT_SQUEEZE）',
        'LONG_UNWIND': '多头减仓离场（LONG_UNWIND）',
        'NO_DATA': '数据不足',
    }
    return mapping.get(oi_signal, oi_signal)


def _oi_short(oi_signal):
    """OI简短描述"""
    mapping = {
        'SHORT_BUILD': '空头建仓',
        'LONG_BUILD': '多头建仓',
        'SHORT_SQUEEZE': '空头被挤',
        'LONG_UNWIND': '多头离场',
    }
    return mapping.get(oi_signal, oi_signal)


def _smart_money_description(big_long, retail_long):
    """聪明钱描述"""
    diff = big_long - retail_long
    if diff > 10:
        return f'大户{big_long:.0f}%多（偏多，散户{retail_long:.0f}%）'
    elif diff < -10:
        return f'大户{big_long:.0f}%多（偏空，散户{retail_long:.0f}%）'
    else:
        return f'大户{big_long:.0f}%多（中性，散户{retail_long:.0f}%）'


def _kappa_description(kappa):
    """kappa描述"""
    if kappa < -0.05:
        return 'Call需求强（期权市场偏多）'
    elif kappa > 0.05:
        return 'Put需求强（期权市场偏空）'
    else:
        return '期权中性'


def _hurst_desc(hurst):
    """Hurst描述"""
    if hurst > 0.6:
        return '强趋势持续'
    elif hurst > 0.5:
        return '弱趋势'
    else:
        return '随机游走'


def _fr_meaning(fr):
    """FR含义"""
    if fr > 0.01:
        return '多头过热，每8h付费'
    elif fr < -0.01:
        return '空头付费，轧空信号'
    else:
        return '正常'


def _build_trigger(bias, entry_lo, entry_hi, sl, tp1, tp2, vip_status):
    """触发条件描述"""
    if vip_status != 'ENTER':
        return '等待结构确认，暂无入场条件'
    if bias == 'SHORT':
        return (f'价格到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阴 → 可空 '
                f'| SL${sl:,.0f} | TP${tp1:,.0f}→${tp2:,.0f}')
    elif bias == 'LONG':
        return (f'价格到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阳 → 可多 '
                f'| SL${sl:,.0f} | TP${tp1:,.0f}→${tp2:,.0f}')
    else:
        return '无方向，等待'


# ═══════════════════════════════════════════════════════════════
# 帖子审计函数
# ═══════════════════════════════════════════════════════════════

def audit_post(content):
    """审计帖子是否符合品牌铁律，返回(ok, issues)"""
    issues = []

    # 铁律1: 必须有品牌前缀
    if BRAND_PREFIX not in content:
        issues.append('缺少🌿姓赵不宣前缀')

    # 铁律1: 必须有品牌后缀
    if '梵天系统' not in content or '不是建议' not in content:
        issues.append('缺少📊梵天系统后缀')

    # 铁律9: 不允许"你怎么看"
    for bad in ['你怎么看', '你认为', '你会持有']:
        if bad in content:
            issues.append(f'假互动：含「{bad}」')

    # 铁律10: 必须>100字
    char_count = len(content)
    if char_count < 100:
        issues.append(f'字数{char_count}<100，不达Square流量boost门槛')

    # 内部术语泄漏检查
    forbidden = ['BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
                'BEAR_RECOVERY', 'brahma', 'brahma_', '梵天设计院',
                'HCME', 'confluence_score', 'Kronos']
    for w in forbidden:
        if w in content:
            issues.append(f'内部术语泄漏：{w}')

    return len(issues) == 0, issues


# ═══════════════════════════════════════════════════════════════
# 从brahma_manual_analysis输出解析关键字段
# ═══════════════════════════════════════════════════════════════

def parse_analysis_output(report_text):
    """从run_analysis()返回的文本中解析出模板需要的字段"""
    import re

    data = {}
    lines = report_text.split('\n')

    for line in lines:
        # 体制
        m = re.search(r'【体制】(\w+)\s+score=([-\d.]+)\s+grade=(\d+)', line)
        if m:
            data['regime'] = m.group(1)
            data['score'] = float(m.group(2))
            data['grade'] = int(m.group(3))

        # FVG磁铁
        m = re.search(r'主磁铁:\s*(\w+)@\$?([\d,.]+)', line)
        if m:
            data['fvg_dir'] = m.group(1)
            data['fvg_magnet'] = float(m.group(2).replace(',', ''))

        # FVG百分比
        m = re.search(r'中点\$?[\d,.]+\(([+-]?[\d.]+)%\)', line)
        if m:
            data['fvg_pct'] = float(m.group(1))

        # 清算
        m = re.search(r'止损墙:\s*\$?([\d,.]+)\s*\(([+-]?[\d.]+)%', line)
        if m:
            data['liq_wall'] = float(m.group(1).replace(',', ''))
            data['liq_wall_pct'] = float(m.group(2))
        m = re.search(r'支撑池:\s*\$?([\d,.]+)\s*\(([+-]?[\d.]+)%', line)
        if m:
            data['liq_pool'] = float(m.group(1).replace(',', ''))
            data['liq_pool_pct'] = float(m.group(2))

        # OI
        m = re.search(r'主信号:\s*(\w+)', line)
        if m:
            data['oi_signal'] = m.group(1)

        # CVD
        m = re.search(r'CVD\s+1H=([-\d]+)', line)
        if m:
            data['cvd_1h'] = int(m.group(1))

        # 大户
        m = re.search(r'大户(\d+)%多', line)
        if m:
            data['big_long'] = float(m.group(1))
        m = re.search(r'散户(\d+)%多', line)
        if m:
            data['retail_long'] = float(m.group(1))

        # Hurst
        m = re.search(r'H=([\d.]+)', line)
        if m:
            data['hurst'] = float(m.group(1))

        # kappa
        m = re.search(r'κ=([-\d.]+)', line)
        if m:
            data['kappa'] = float(m.group(1))

        # FR
        m = re.search(r'FR=([\d.]+)%', line)
        if m:
            data['fr'] = float(m.group(1))

        # ATR
        m = re.search(r'ATR1H=\$([\d,.]+)', line)
        if m:
            data['atr_1h'] = float(m.group(1).replace(',', ''))
        m = re.search(r'ATR4H=\$([\d,.]+)', line)
        if m:
            data['atr_4h'] = float(m.group(1).replace(',', ''))

        # 价格
        m = re.search(r'基准\$?([\d,.]+)', line)
        if m and 'price' not in data:
            data['price'] = float(m.group(1).replace(',', ''))

        # 失效期
        m = re.search(r'失效期:\s*(\w+)', line)
        if m:
            data['failure_state'] = m.group(1)

        # 共振入场区
        m = re.search(r'入场区间:\s*\$?([\d,.]+)\s*~\s*\$?([\d,.]+)', line)
        if m:
            data['entry_lo'] = float(m.group(1).replace(',', ''))
            data['entry_hi'] = float(m.group(2).replace(',', ''))

        # VIP状态
        if 'WAIT' in line and '交易员大脑' in line:
            data['vip_status'] = 'WAIT'
        if 'ENTER' in line and '交易员大脑' in line:
            data['vip_status'] = 'ENTER'

    # 从VIP卡片提取SL/TP
    m = re.search(r'止损\s*\$?([\d,.]+)', report_text)
    if m:
        data['sl'] = float(m.group(1).replace(',', ''))
    m = re.search(r'目标\s*\$?([\d,.]+)', report_text)
    if m:
        data['tp1'] = float(m.group(1).replace(',', ''))

    # 方向
    if '方向=NONE' in report_text or '方向=SHORT' in report_text:
        if '方向=SHORT' in report_text:
            data['bias'] = 'SHORT'
        elif '方向=LONG' in report_text:
            data['bias'] = 'LONG'
        else:
            data['bias'] = 'NONE'

    return data


if __name__ == '__main__':
    # 测试模板
    print('=== 旗舰帖测试 ===')
    test_data = {
        'regime': 'CHOP_MID', 'score': 7, 'price': 77069,
        'fvg_dir': 'BULL', 'fvg_magnet': 78193, 'fvg_pct': 1.8,
        'liq_wall': 78458, 'liq_pool': 75382, 'liq_wall_pct': 2.0, 'liq_pool_pct': -2.2,
        'oi_signal': 'SHORT_BUILD', 'cvd_1h': -2,
        'big_long': 68, 'retail_long': 62,
        'hurst': 0.431, 'kappa': -0.099, 'fr': 0.003,
        'atr_1h': 304, 'atr_4h': 716,
        'failure_state': 'RED',
        'vip_status': 'WAIT', 'bias': 'NONE',
        'entry_lo': 0, 'entry_hi': 0, 'sl': 0, 'tp1': 0, 'tp2': 0,
    }
    post = build_battlefield_report('BTC', test_data)
    print(post)
    print()
    ok, issues = audit_post(post)
    print(f'审计: {"✅通过" if ok else "❌"+str(issues)}')
    print()
    print('=== 宏观帖测试 ===')
    macro = build_macro_outlook(
        'CPI公布', '09/12 20:30 CST',
        ['市场预期CPI同比3.4% / 核心CPI 2.5%', '当前加息概率70% / 恐贪指数68=Greed'],
        'BTC跌破$75,382支撑池 → 清算连环 → 测试$72,000',
        'BTC突破$78,458止损墙 → 轧空 → 测试$80,000',
        'CHOP_MID', 7, 0.426, 'LONG_UNWIND',
        'CPI前减仓50% | SL加宽1.5倍 | 不在数据公布瞬间挂单\nCPI后等1H收线确认方向再入场'
    )
    print(macro)
    print()
    ok, issues = audit_post(macro)
    print(f'审计: {"✅通过" if ok else "❌"+str(issues)}')

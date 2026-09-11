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
    旗舰帖：战场报告，80维全输出
    analysis_data: brahma_manual_analysis.run_analysis()返回的解析数据
    """
    date_str = datetime.now(CST).strftime('%m/%d')

    # 从analysis_data提取各维度数据
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

    # 体制翻译（人话，不露代码）
    regime_cn = _translate_regime(regime)

    # FVG磁铁描述
    fvg_desc = _fvg_description(fvg_dir, fvg_magnet, price)

    # OI翻译
    oi_desc = _oi_description(oi_signal)

    # 聪明钱描述
    sm_desc = _smart_money_description(big_long, retail_long)

    # kappa描述
    kappa_desc = _kappa_description(kappa)

    # 失效期描述
    failure_desc = ''
    if failure_state == 'RED':
        failure_desc = '\n⚠️ 失效期RED → 仓位减半'
    elif failure_state == 'YELLOW':
        failure_desc = '\n⚠️ 失效期YELLOW → 仓位×0.75'

    # 触发条件
    trigger_desc = _build_trigger(bias, entry_lo, entry_hi, sl, tp1, tp2, vip_status)

    lines = [
        f'{BRAND_PREFIX} | 早间战场报告 {date_str}',
        f'',
        f'━━━ 体制 ━━━',
        f'{sym}: {regime_cn} score={score:.0f}',
        f'',
        f'━━━ FVG磁铁 ━━━',
        f'{sym}: {fvg_desc}',
        f'',
        f'━━━ 清算地图 ━━━',
        f'{sym}: 上方止损墙${liq_wall:,.0f}({liq_wall_pct:+.1f}%) / 下方支撑池${liq_pool:,.0f}({liq_pool_pct:+.1f}%)',
        f'',
        f'━━━ 今日触发条件 ━━━',
        f'{trigger_desc}',
        f'',
        f'━━━ OI/聪明钱 ━━━',
        f'{sym}: {oi_desc} | CVD {cvd_1h:+.0f} | {sm_desc}',
        f'',
        f'━━━ 波动率/期权 ━━━',
        f'Hurst={hurst:.2f} {_hurst_desc(hurst)} | κ={kappa:.3f} {kappa_desc} | FR={fr:.4f}%',
        f'ATR 1H=${atr_1h:,.0f} 4H=${atr_4h:,.0f}',
        f'{failure_desc}',
        f'',
        f'{BRAND_SUFFIX}',
        f'#{sym} #合约交易',
    ]
    return '\n'.join(lines)


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

#!/usr/bin/env python3
"""
news_formatter.py — 新闻局排版引擎 v2.0
==========================================
设计院 · 梵天系统 2026-05-28

升级目标：10年以上交易员水准
  - 市场叙事，不是数据堆砌
  - 每个信号说清楚「为什么是现在」
  - 失效条件明确（止损的逻辑，不只是价格）
  - 情绪 + 结构 + 动量 三层共振判断
  - 语言简练有力，像老手写给老手看的

两种格式：
  format_flash()   → Jarvis推送（简洁快讯）
  format_square()  → 广场发帖（深度版）
"""
import re, json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
try:
    from dir_util import parse_direction, dir_str as _dir_str_util
    _HAS_DIR_UTIL = True
except ImportError:
    _HAS_DIR_UTIL = False

BASE     = Path(__file__).parent.parent
BJ       = timezone(timedelta(hours=8))

# 广场注册链接（可配置）
REF_LINK = ''
try:
    _rf = Path(__file__).parent.parent / 'config' / 'ref_link.txt'
    if _rf.exists(): REF_LINK = _rf.read_text().strip()
except: pass

# ── 禁词 ─────────────────────────────────────────────
BANNED = [
    '梵天','达摩院','brahma','神级','体制代码','信仰',
    '打爆','爆仓','稳赚','无风险','一定涨','一定跌',
    '内部','私聊','加我','交流群','telegram','discord',
    '新浪财经',  # 钉钉1专属格式，禁止出现在广场帖
]
def clean(t: str) -> str:
    for w in BANNED:
        t = re.sub(re.escape(w), '', t, flags=re.IGNORECASE)
    return t

# ── 基础 ─────────────────────────────────────────────
def now_bj(fmt='%Y-%m-%d %H:%M'):
    return datetime.now(BJ).strftime(fmt)

def price_fmt(v):
    try:
        v = float(v)
        if v >= 10000:  return f'${v:,.0f}'
        elif v >= 1000: return f'${v:,.1f}'
        elif v >= 100:  return f'${v:.2f}'
        elif v >= 10:   return f'${v:.3f}'
        else:           return f'${v:.4f}'
    except: return str(v)

def dir_str(direction: str) -> tuple:
    """方向解析：优先使用 dir_util 标准库，禁止默认做多"""
    if _HAS_DIR_UTIL:
        return _dir_str_util(direction)
    d_raw = str(direction).strip()
    d = d_raw.upper()
    if 'SHORT' in d or '空' in d_raw:
        return '做空', '📉', '▼', '🔴'
    if 'LONG' in d or '多' in d_raw:
        return '做多', '📈', '▲', '🟢'
    return '观望', '⏸', '—', '⚪'

REGIME_META = {
    'BEAR_TREND':    ('🐻 熊市下行', '做空为王，反弹是陷阱'),
    'BEAR_RECOVERY': ('↩️ 熊市反弹', '反弹不等于反转，警惕假突破'),
    'BEAR_EARLY':    ('📉 开始下跌', '趋势初段，动量最强的阶段'),
    'CHOP_MID':      ('↔️ 中位震荡', '方向未定，等待选择'),
    'BULL_TREND':    ('🐂 牛市上行', '顺势做多，回踩是机会'),
    'BULL_PEAK':     ('🔔 顶部区域', '贪婪信号密集，管好止盈'),
}

FG_NOTE = {
    range(0, 15):   ('💀', '极度恐慌', '市场割肉为主，情绪底未必价格底'),
    range(15, 30):  ('😨', '恐惧区间', '悲观情绪蔓延，关注量能是否萎缩'),
    range(30, 45):  ('😟', '偏空情绪', '方向性不强，观望为主'),
    range(45, 55):  ('😐', '中性区间', '多空胶着，等待催化剂'),
    range(55, 70):  ('😏', '偏多情绪', '多头占优，但注意不要追高'),
    range(70, 85):  ('🤑', '贪婪区间', '情绪过热，逢高减仓信号'),
    range(85, 101): ('🔥', '极度贪婪', '历史上大多是阶段性顶部区域'),
}
def fg_meta(fg: int) -> tuple:
    for r, meta in FG_NOTE.items():
        if fg in r: return meta
    return ('📊', f'F&G={fg}', '')


# ════════════════════════════════════════════════════════
# 核心：市场叙事引擎
# ════════════════════════════════════════════════════════
def market_narrative(regime: str, fg: int, ls: float, top: float, oi_dir: str = 'NEUTRAL') -> str:
    """
    生成一段像老手写的市场判断
    不是罗列数据，而是讲清楚当前市场的核心矛盾
    """
    parts = []
    fg_icon, fg_label, fg_warn = fg_meta(fg)

    # 1. 定性当前结构
    if 'BEAR_TREND' in regime:
        parts.append(f'当前处于下行趋势，价格结构偏弱，多头每次反弹都在制造更低的高点。')
    elif 'BEAR_RECOVERY' in regime:
        parts.append(f'技术面出现反弹迹象，但大方向仍偏空，需区分反弹和反转。')
    elif 'CHOP_MID' in regime:
        parts.append(f'市场在关键区间内横盘整理，多空分歧明显，方向选择可能在酝酿中。')
    elif 'BULL_TREND' in regime:
        parts.append(f'趋势向上，主升浪结构完整，回踩不破关键支撑则多头逻辑未破。')
    elif 'BULL_PEAK' in regime:
        parts.append(f'价格在高位区域，上涨动能边际递减，获利盘压力加大。')
    else:
        parts.append(f'趋势信号不明朗，市场处于转换期。')

    # 2. 情绪层分析（核心矛盾）
    core_conflict = ''
    if fg <= 25 and ls >= 65:
        core_conflict = f'核心矛盾：F&G={fg} 显示市场恐慌，但 {ls:.0f}% 散户仍持多——说明多头未真正出清，下跌可能还未到位。'
    elif fg <= 25 and ls < 45:
        core_conflict = f'情绪信号：F&G={fg} 极度恐惧且散户多头仅 {ls:.0f}%，历史上此类极端悲观往往出现在底部附近，反弹机会值得关注。'
    elif fg >= 75 and ls >= 70:
        core_conflict = f'风险警示：F&G={fg} 贪婪 + 散户 {ls:.0f}% 做多，过度拥挤的多头在流动性收缩时容易引发踩踏。'
    elif abs(top - ls) >= 12:
        if top < ls:
            core_conflict = f'机构 vs 散户分歧：大户 {top:.0f}% 做多 vs 散户 {ls:.0f}%，机构偏空信号值得警惕。'
        else:
            core_conflict = f'机构 vs 散户分歧：大户 {top:.0f}% 做多 vs 散户仅 {ls:.0f}%，机构在悄悄积累多头仓位。'

    if core_conflict:
        parts.append(core_conflict)

    # 3. OI方向补充
    if oi_dir == 'UP':
        parts.append(f'持仓量上升，价格验证中——资金在增加方向性押注。')
    elif oi_dir == 'DOWN':
        parts.append(f'持仓量下降，获利了结或去杠杆信号——需关注后续方向。')

    return ' '.join(parts)


# ════════════════════════════════════════════════════════
# 核心：单信号解析引擎（老手视角）
# ════════════════════════════════════════════════════════
def signal_commentary(sig: dict, regime: str, ls_global: float) -> dict:
    """
    为单个信号生成深度解析
    返回：{why_now, setup_quality, invalidation, entry_tip}
    """
    sym      = sig.get('symbol','').replace('USDT','')
    direction = sig.get('direction') or sig.get('signal_dir','')
    score    = float(sig.get('score', 0))
    p        = sig.get('params', {}) or {}
    # 方向判断：明确SHORT→做空，明确LONG→做多，否则观望（不默认做多）
    _d = str(direction).upper().strip()
    d_cn = '做空' if ('SHORT' in _d or _d == '空') else ('做多' if ('LONG' in _d or _d == '多') else '观望')

    price    = float(p.get('price', 0))
    entry_lo = float(p.get('entry_lo', 0))
    entry_hi = float(p.get('entry_hi', 0))
    stop     = float(p.get('stop', 0))
    tp1      = float(p.get('tp1', 0))
    rr1      = float(p.get('rr1', 0))
    ls_s     = float(p.get('ls_long', ls_global))
    fr       = float(p.get('fr', 0))
    sig_regime = p.get('regime', regime)

    result = {}

    # ── 为什么是现在（why now）──────────────────────
    reasons = []
    if d_cn == '做空':
        if 'BEAR' in sig_regime:
            reasons.append('趋势结构偏空')
        if ls_s >= 63:
            reasons.append(f'散户 {ls_s:.0f}% 做多过度拥挤')
        if fr > 0.0008:
            reasons.append(f'资金费率 {fr*100:+.3f}%（多头付费，空头收费）')
        if score >= 145:
            reasons.append('多因子强共振（技术+情绪+动量同向）')
        elif score >= 130:
            reasons.append('技术+情绪双重确认')
        if rr1 >= 2.0:
            reasons.append(f'风险回报比 {rr1:.1f}x 达标')
    else:  # 做多
        if 'BULL' in sig_regime:
            reasons.append('趋势结构偏多')
        if ls_s < 42:
            reasons.append(f'散户 {ls_s:.0f}% 做多极低，悲观情绪出清')
        if fr < -0.0005:
            reasons.append(f'资金费率 {fr*100:+.3f}%（空头付费，多头收费）')
        if score >= 145:
            reasons.append('多因子强共振')
        if rr1 >= 2.0:
            reasons.append(f'风险回报比 {rr1:.1f}x 达标')

    result['why_now'] = '、'.join(reasons) if reasons else '技术面信号触发'

    # ── 建仓质量评估 ─────────────────────────────────
    if score >= 145 and rr1 >= 2.0:
        result['setup_quality'] = '高质量建仓机会'
    elif score >= 145 and rr1 >= 1.5:
        result['setup_quality'] = '强信号，R:R达标'
    elif score >= 130 and rr1 >= 1.5:
        result['setup_quality'] = '中等质量，可轻仓参与'
    elif score >= 120:
        result['setup_quality'] = '临界信号，等待进一步确认'
    else:
        result['setup_quality'] = '弱势信号，仅供参考'

    # ── 失效条件（最重要的部分）────────────────────
    if d_cn == '做空' and stop > 0 and entry_lo > 0:
        stop_pct = abs(stop - entry_lo) / entry_lo * 100
        if stop > entry_hi:
            result['invalidation'] = f'价格有效站上 {price_fmt(stop)} 并收盘确认，空头逻辑失效'
        else:
            result['invalidation'] = f'止损 {price_fmt(stop)}（-{stop_pct:.1f}%），破位立即出场不等待'
    elif d_cn == '做多' and stop > 0 and entry_lo > 0:
        stop_pct = abs(entry_lo - stop) / entry_lo * 100
        result['invalidation'] = f'跌破 {price_fmt(stop)}（-{stop_pct:.1f}%）则多头逻辑破坏，止损出场'
    else:
        result['invalidation'] = '需结合实时价格设置止损'

    # ── 入场建议 ─────────────────────────────────────
    if price > 0 and entry_lo > 0:
        if d_cn == '做空':
            if price <= entry_hi * 1.003:
                result['entry_tip'] = f'当前价已在入场区，可分批建仓（50%先进，触及止损前再加50%）'
            elif price < entry_lo:
                result['entry_tip'] = f'价格已跌过入场区，等反弹至 {price_fmt(entry_hi)} 附近再参与，不追空'
            else:
                result['entry_tip'] = f'限价等待，挂单 {price_fmt(entry_lo)}~{price_fmt(entry_hi)} 区间'
        else:  # 做多
            gap_to_entry = (price - entry_hi) / entry_hi * 100 if entry_hi else 0
            if price <= entry_hi * 1.005:
                result['entry_tip'] = f'当前价在入场区（{price_fmt(entry_lo)}~{price_fmt(entry_hi)}），可分批买入'
            elif gap_to_entry <= 3.0:
                result['entry_tip'] = f'现价高于入场区{gap_to_entry:.1f}%，等回落至 {price_fmt(entry_hi)} 以下再入场，不追高'
            else:
                result['entry_tip'] = f'限价挂单 {price_fmt(entry_lo)}~{price_fmt(entry_hi)}，等价格回落，不追高'
    else:
        result['entry_tip'] = '参考上方点位，实时确认入场'

    # ── 体制风险标注（做多时若BEAR体制则加警告）──────────────
    if d_cn == '做多' and 'BEAR' in str(p.get('regime', sig_regime)):
        r = p.get('regime', sig_regime)
        if 'CRASH' in r:
            result['regime_warn'] = '⚠️ 当前暴跌体制，做多系统封锁，仅供观察'
        elif 'TREND' in r:
            result['regime_warn'] = '⚠️ 空头趋势体制，逆势做多胜率低，严控仓位'
        elif 'EARLY' in r:
            result['regime_warn'] = '⚠️ 熊市初期，多单需等体制确认'
    if d_cn == '做空' and 'BULL' in str(p.get('regime', sig_regime)):
        result['regime_warn'] = '⚠️ 牛市体制逆势做空，风险较大'

    return result


# ════════════════════════════════════════════════════════
# Jarvis 快讯（精简 + 有态度）
# ════════════════════════════════════════════════════════
def _fmt_tickers(tickers: dict) -> str:
    """多标的24H涨跌标尺栏 — 最多8个标的"""
    if not tickers: return ''
    SHOW = ['BTC','ETH','BNB','SOL','DOGE','XRP','ADA','LTC','AVAX','LINK']
    rows = []
    for sym in SHOW:
        t = tickers.get(sym)
        if not t: continue
        chg = t['chg']
        arrow = '▲' if chg >= 0 else '▼'
        rows.append(f'{sym} {arrow}{abs(chg):.1f}%')
    return '  '.join(rows)

def _sentiment_summary(fg: int, fg_prev: int, ls: float, top: float,
                       oi_dir: str, oi_chg: float, funding: float) -> list:
    """全局舆论三行摘要"""
    lines = []
    # F&G趋势
    trend = '↑' if fg > fg_prev else ('↓' if fg < fg_prev else '→')
    fg_icon, fg_label_str, _ = fg_meta(fg)
    lines.append(f'恐慌贪婪 F&G={fg}{trend}({fg_prev}) {fg_label_str} {fg_icon}')
    # 多空情绪
    crowd = ''
    if ls >= 75: crowd = ' ⚠️多头极拥挤'
    elif ls <= 30: crowd = ' ⚠️空头极拥挤'
    vs = '大户偏多' if top >= 55 else ('大户偏空' if top <= 45 else '大户中性')
    lines.append(f'散户多{ls:.0f}%{crowd}  {vs}({top:.0f}%)')
    # OI + 资金费率
    oi_str = f'OI{oi_chg:+.1f}%' if oi_chg else 'OI平'
    fr_str = f'FR{funding*100:+.4f}%' if abs(funding) > 0.0001 else 'FR中性'
    lines.append(f'{oi_str}  {fr_str}')
    return lines


def _regime_view(regime: str, fg: int, ls: float, top: float) -> str:
    """市场一句话核心判断（明确操作倾向）"""
    if 'BEAR_CRASH' in regime:
        return '暴跌体制，做多风险较高(-30分)，建议等待企稳信号 [v24.3 降权非禁止]'
    if 'BEAR_TREND' in regime:
        return '空头趋势延续，做多成功率低，逢反弹关注空单机会'
    if 'BEAR_EARLY' in regime:
        if ls >= 60:
            return f'熊市初期+散户未降仓（{ls:.0f}%多）——顺势偏空，等反弹入场空单'
        return '熊市确认中，观望为主，等结构性做空机会'
    if 'BEAR_RECOVERY' in regime:
        if fg <= 30:
            return '熊市反弹，情绪低位有机会，控制仓位参与，非趋势行情'
        return '熊市反弹进行中，轻仓多单，随时准备止盈'
    if 'CHOP' in regime:
        return '震荡整理，无明确方向，缩小仓位等待突破选方向'
    if 'BULL_TREND' in regime:
        return '牛市主升，回踩支撑是做多机会，持有优质多单'
    if 'BULL_RECOVERY' in regime:
        return '牛市修正结束迹象，关注确认信号后做多'
    return ''

def _core_conflict(regime: str, fg: int, ls: float, top: float) -> str:
    """核心矛盾一句话（补充覆盖更多体制场景）"""
    # 极端情绪
    if fg <= 20 and ls >= 68:
        return f'⚡ 极度恐慌盘面但散户仍多({ls:.0f}%)，筹码未出清，继续下行风险'
    if fg <= 25 and ls >= 65:
        return f'⚡ 恐惧情绪+多头未降仓({ls:.0f}%)，下跌空间仍在'
    if fg <= 20 and ls <= 40:
        return f'⚡ 极度悲观+空头拥挤({ls:.0f}%多)，警惕短线反弹洗盘'
    if fg >= 75 and ls >= 72:
        return f'⚡ 极度贪婪+多头拥挤，高位风险大，注意止盈'
    # 体制+大小户分歧
    if 'BEAR' in regime and top < ls - 12:
        return f'⚡ 机构偏空({top:.0f}%) vs 散户偏多({ls:.0f}%) — 跟机构方向，逢高空'
    if 'BULL' in regime and top > ls + 12:
        return f'⚡ 机构偏多({top:.0f}%) vs 散户偏空({ls:.0f}%) — 跟机构方向，回踩多'
    # CHOP震荡专属
    if 'CHOP' in regime and 40 <= ls <= 65:
        return f'⚡ 震荡体制+多空均衡({ls:.0f}%散户多)，方向待定，轻仓等突破'
    if 'CHOP' in regime and ls > 65:
        return f'⚡ 震荡体制+散户偏多({ls:.0f}%)，若向下突破杀伤力较大'
    # BEAR_RECOVERY反弹
    if 'BEAR_RECOVERY' in regime and fg <= 35:
        return f'⚡ 熊市反弹+情绪低位(F&G={fg})，反弹可参与但控仓，新高前谨慎'
    # BEAR_EARLY早期
    if 'BEAR_EARLY' in regime and ls >= 60:
        return f'⚡ 熊市初期+散户未降仓({ls:.0f}%多)，下跌趋势中逆势多风险高'
    return ''


def format_flash(data: dict) -> str:
    regime   = data.get('regime', 'UNKNOWN')
    fg       = int(data.get('fg', 25))
    fg_prev  = int(data.get('fg_prev', fg))
    ls       = float(data.get('ls_long', 50))
    top      = float(data.get('top_long', 50))
    nav      = float(data.get('nav', 0))
    pos      = int(data.get('pos_cnt', 0))
    sigs     = data.get('signals', [])
    oi_chg   = float(data.get('oi_change_pct', 0))
    funding  = float(data.get('funding', 0))
    tickers  = data.get('tickers', {})
    bj       = now_bj('%m/%d %H:%M')

    r_label, _ = REGIME_META.get(regime, (regime, ''))
    _, fg_label_str, _ = fg_meta(fg)
    fg_trend = '↑' if fg > fg_prev else ('↓' if fg < fg_prev else '→')

    SEP  = '─' * 28
    SEP2 = '━' * 28

    def bar(v, width=14):
        v = max(0, min(100, float(v)))
        f = round(v / 100 * width)
        return '█' * f + '░' * (width - f)

    def pct_bar(v, width=14):
        """彩色进度条，附百分比"""
        return f'[{bar(v, width)}] {v:.0f}%'

    o = []

    # ══════ 顶栏 ══════════════════════════════════════
    o.append(f'📡 行情快讯  {bj} BJ')
    o.append(SEP2)

    # 24H 涨跌（截断到6个标的，避免太长）
    SHOW6 = ['BTC','ETH','BNB','SOL','DOGE','XRP']
    t6 = []
    for sym in SHOW6:
        t = tickers.get(sym)
        if not t: continue
        c = t['chg']
        t6.append(f'{sym} {"▲" if c>=0 else "▼"}{abs(c):.1f}%')
    if t6:
        o.append('  ' + '  '.join(t6))
    # 剩余4个
    REST4 = ['ADA','LTC','AVAX','LINK']
    r4 = []
    for sym in REST4:
        t = tickers.get(sym)
        if not t: continue
        c = t['chg']
        r4.append(f'{sym} {"▲" if c>=0 else "▼"}{abs(c):.1f}%')
    if r4:
        o.append('  ' + '  '.join(r4))

    # 体制一行
    rv = _regime_view(regime, fg, ls, top)
    regime_line = r_label
    if rv:
        regime_line += f'  ·  {rv}'
    o.append(f'  {regime_line}')
    o.append('')

    # ══════ 情绪仪表盘 ══════════════════════════════
    o.append('▌情绪仪表盘')
    fg_icon = '😱' if fg<=20 else ('😨' if fg<=35 else ('😐' if fg<=55 else ('😏' if fg<=75 else '🤑')))
    # 对齐标签宽度=5
    o.append(f'  恐惧贪婪  {pct_bar(fg)}  {fg_icon} {fg_label_str} {fg_trend}({fg_prev})')
    o.append(f'  散户多空  {pct_bar(ls)}  多/空')
    o.append(f'  机构多空  {pct_bar(top)}  多/空')

    oi_s = f'OI {oi_chg:+.1f}%' if oi_chg else 'OI 平稳'
    fr_s = f'FR {funding*100:+.4f}%' if abs(funding)>0.0001 else 'FR 中性'
    o.append(f'  {oi_s}  {fr_s}  ·  账户 ${nav:.0f}  持仓 {pos}个')

    conflict = _core_conflict(regime, fg, ls, top)
    if conflict:
        o.append(f'  {conflict}')
    o.append('')

    # ══════ 行情研判 ════════════════════════════════
    # 读 Kronos 缓存（零网络调用）
    _kronos = None
    try:
        import json as _json
        _kronos = _json.load(open('/tmp/kronos_signal.json'))
    except Exception:
        pass

    o.append('▌行情研判')
    analysis = _deep_analysis(regime, fg, ls, top, oi_chg, funding, tickers, kronos=_kronos)
    for i, a in enumerate(analysis):
        marker = '①②③④⑤'[i] if i < 5 else '·'
        o.append(f'  {marker} {a}')
    o.append('')

    # ══════ 信号雷达 ════════════════════════════════
    if not sigs:
        o.append('▌信号雷达  暂无有效信号')
        o.append(f'  {SEP}')
        o.append('  当前结构未成形，等待市场给出明确入场条件')
    else:
        eff = sorted(sigs, key=lambda x: float(x.get('score',0)), reverse=True)
        o.append(f'▌信号雷达  {len(eff)}个信号')
        for sig in eff:
            sym   = sig.get('symbol','').replace('USDT','')
            d_cn, _, d_arrow, _ = dir_str(sig.get('direction') or sig.get('signal_dir',''))
            score = float(sig.get('score',0))
            params = sig.get('params',{}) or {}
            badge  = '🔴' if score>=145 else '🟠' if score>=120 else '⚪'
            level  = 'S1' if score>=145 else 'S2' if score>=120 else 'S3'

            comm  = signal_commentary(sig, regime, ls)
            ep    = float(params.get('entry_lo',0))
            ehi   = float(params.get('entry_hi',ep))
            sl    = float(params.get('stop',0))
            tp1   = float(params.get('tp1',0))
            tp2   = float(params.get('tp2',0))
            rr1   = float(params.get('rr1',0))
            pr    = float(params.get('price',0))

            o.append(f'  {SEP}')
            # 信号卡片标题
            # S1 强信号特殊标题
            if score >= 145:
                o.append(f'  {badge} 🚨 强信号  ${sym}  {d_cn}{d_arrow}')
                o.append(f'     [{level}] {score:.0f}分  ⭐⭐⭐  高质量建仓机会')
            elif score >= 120:
                o.append(f'  {badge} ${sym}  {d_cn}{d_arrow}  [{level}] {score:.0f}分  ⭐⭐')
            else:
                o.append(f'  {badge} ${sym}  {d_cn}{d_arrow}  [{level}] {score:.0f}分')

            # 价格行
            if pr > 0 and ep > 0:
                if d_cn == '做多':
                    gap = (pr - ehi) / ehi * 100
                    pos_tag = '✅ 在区间内' if gap <= 0.5 else f'↑ 高于入场区 {gap:.1f}%'
                else:
                    gap = (ep - pr) / ep * 100
                    pos_tag = '✅ 在区间内' if gap <= 0.5 else f'↓ 低于入场区 {abs(gap):.1f}%'
                o.append(f'  现价 {price_fmt(pr)}  {pos_tag}')
                o.append(f'  入场 {price_fmt(ep)} ~ {price_fmt(ehi)}')

            # 止损/目标行
            if sl > 0 and tp1 > 0:
                sl_pct = abs(ep-sl)/ep*100 if ep else 0
                o.append(f'  止损 {price_fmt(sl)} ({sl_pct:.1f}%)  →  目标① {price_fmt(tp1)}  R:R {rr1:.1f}x')
            if tp2 and tp2 != tp1 and sl and ep and sl != ep:
                rr2 = abs(tp2-ep)/abs(sl-ep)
                o.append(f'  目标② {price_fmt(tp2)}  R:R {rr2:.1f}x')

            # 警告/理由/建议
            if comm.get('regime_warn'):
                o.append(f'  {comm["regime_warn"]}')
            elif comm.get('why_now'):
                o.append(f'  ▸ {comm["why_now"]}')
            if comm.get('entry_tip'):
                o.append(f'  → {comm["entry_tip"]}')

        o.append(f'  {SEP}')
        # 操作结论
        best = eff[0]
        bs   = float(best.get('score',0))
        bsym = best.get('symbol','').replace('USDT','')
        bd,_,_,_ = dir_str(best.get('direction') or best.get('signal_dir',''))
        if bs >= 145:
            o.append(f'  ⚡ 优先执行 ${bsym} {bd}（{bs:.0f}分）— 确认后建仓，严守止损')
        elif bs >= 120:
            o.append(f'  ⚡ 观察 ${bsym} {bd}，等确认K线 + 量能配合再入')
        else:
            o.append(f'  ⚡ 弱信号环境 — 空仓观望是最优操作')

    o.append('')
    o.append(SEP2)
    o.append('📌 定时快照 · 实时行情以最新价为准')
    o.append('⚠️ 合约有风险，操作须自行评估')
    return clean('\n'.join(o))


# ════════════════════════════════════════════════════════
def format_square(data: dict, max_signals: int = 3) -> str:
    """广场版：老手风格深度快讯，与 format_flash 同等级别"""
    regime   = data.get('regime', 'UNKNOWN')
    fg       = int(data.get('fg', 25))
    fg_prev  = int(data.get('fg_prev', fg))
    ls       = float(data.get('ls_long', 50))
    top      = float(data.get('top_long', 50))
    oi_dir   = data.get('oi_dir', 'NEUTRAL')
    oi_chg   = float(data.get('oi_change_pct', 0))
    funding  = float(data.get('funding', 0))
    tickers  = data.get('tickers', {})
    sigs     = sorted(data.get('signals', []), key=lambda x: float(x.get('score',0)), reverse=True)
    bj       = now_bj('%Y/%m/%d %H:%M')

    r_label, r_tip = REGIME_META.get(regime, (regime, ''))
    fg_icon, fg_label_str, _ = fg_meta(fg)
    fg_trend = '↑' if fg > fg_prev else ('↓' if fg < fg_prev else '→')

    SEP  = '─' * 30
    SEP2 = '━' * 30

    def bar(v, width=14):
        v = max(0, min(100, float(v)))
        f = round(v / 100 * width)
        return '█' * f + '░' * (width - f)

    o = []

    # ══ 标题 ══════════════════════════════════════
    o.append(f'📡 加密市场快讯  {bj} BJ')
    o.append(SEP2)

    # 24H 行情（两行展示，广场用竖排更清晰）
    SHOW = ['BTC','ETH','BNB','SOL','DOGE','XRP','ADA','LTC','AVAX','LINK']
    row1, row2 = [], []
    for i, sym in enumerate(SHOW):
        t = tickers.get(sym)
        if not t: continue
        c = t['chg']
        cell = f'{sym} {"▲" if c>=0 else "▼"}{abs(c):.1f}%'
        (row1 if i < 5 else row2).append(cell)
    if row1: o.append('  ' + '  '.join(row1))
    if row2: o.append('  ' + '  '.join(row2))
    o.append('')

    # ══ 体制 + 核心观点 ════════════════════════════
    rv = _regime_view(regime, fg, ls, top)
    o.append(f'▌市场体制  {r_label}')
    if rv:
        o.append(f'  {rv}')
    o.append('')

    # ══ 情绪仪表盘 ══════════════════════════════
    o.append('▌情绪仪表盘')
    o.append(f'  恐惧贪婪  [{bar(fg)}] {fg}{fg_trend}  {fg_label_str} {fg_icon}')
    o.append(f'  散户多空  [{bar(ls)}] 多{ls:.0f}% / 空{100-ls:.0f}%')
    o.append(f'  机构多空  [{bar(top)}] 多{top:.0f}% / 空{100-top:.0f}%')
    oi_s = f'OI {oi_chg:+.1f}%' if oi_chg else 'OI 平稳'
    fr_s = f'FR {funding*100:+.4f}%' if abs(funding)>0.0001 else 'FR 中性'
    o.append(f'  {oi_s}  ·  {fr_s}')
    conflict = _core_conflict(regime, fg, ls, top)
    if conflict:
        o.append(f'  {conflict}')
    o.append('')

    # ══ 行情研判（广场版：稍精简，3句）══════════════
    # 读 Kronos 缓存
    _kronos_sq = None
    try:
        import json as _json2
        _kronos_sq = _json2.load(open('/tmp/kronos_signal.json'))
    except Exception:
        pass

    o.append('▌行情研判')
    analysis = _deep_analysis(regime, fg, ls, top, oi_chg, funding, tickers, kronos=_kronos_sq)
    markers = ['①','②','③','④','⑤']
    for i, a in enumerate(analysis[:4]):
        o.append(f'  {markers[i]} {a}')
    o.append('')

    # ══ 信号雷达 ════════════════════════════════
    effective = [s for s in sigs if float(s.get('score',0)) >= 100][:max_signals]
    if not effective:
        o.append('▌信号雷达  暂无有效信号')
        o.append(SEP)
        o.append('  市场在等待方向，此时最大的错误是强行出手')
    else:
        o.append(f'▌信号雷达  {len(effective)}个信号')
        for sig in effective:
            sym   = sig.get('symbol','').replace('USDT','')
            d_cn, _, d_arrow, _ = dir_str(sig.get('direction') or sig.get('signal_dir',''))
            score = float(sig.get('score',0))
            p     = sig.get('params',{}) or {}
            badge = '🔴' if score>=145 else '🟠' if score>=120 else '⚪'
            level = 'S1' if score>=145 else 'S2' if score>=120 else 'S3'
            stars = '⭐⭐⭐' if score>=145 else '⭐⭐' if score>=120 else '⭐'
            comm  = signal_commentary(sig, regime, ls)

            ep   = float(p.get('entry_lo',0))
            ehi  = float(p.get('entry_hi',ep))
            sl   = float(p.get('stop',0))
            tp1  = float(p.get('tp1',0))
            tp2  = float(p.get('tp2',0))
            rr1  = float(p.get('rr1',0))
            pr   = float(p.get('price',0))

            o.append(SEP)
            # 信号标题 — S1用双行强调
            if score >= 145:
                o.append(f'  {badge} 🚨 强信号 ${sym}  {d_cn}{d_arrow}  [{level}] {score:.0f}分  {stars}')
            else:
                o.append(f'  {badge} ${sym}  {d_cn}{d_arrow}  [{level}] {score:.0f}分  {stars}')

            if pr > 0 and ep > 0:
                if d_cn == '做多':
                    gap = (pr - ehi) / ehi * 100
                    gap_tag = '✅ 在区间' if gap <= 0.5 else f'↑ +{gap:.1f}%'
                else:
                    gap = (ep - pr) / ep * 100
                    gap_tag = '✅ 在区间' if gap <= 0.5 else f'↓ -{abs(gap):.1f}%'
                o.append(f'  现价 {price_fmt(pr)}  {gap_tag}  入场 {price_fmt(ep)}~{price_fmt(ehi)}')
            if sl > 0 and tp1 > 0:
                sl_pct = abs(ep-sl)/ep*100 if ep else 0
                o.append(f'  止损 {price_fmt(sl)} (-{sl_pct:.1f}%)  目标① {price_fmt(tp1)}  R:R {rr1:.1f}x')
            if tp2 and tp2 != tp1 and sl and ep and sl != ep:
                rr2 = abs(tp2-ep)/abs(sl-ep)
                o.append(f'  目标② {price_fmt(tp2)}  R:R {rr2:.1f}x')

            # 体制警告优先，否则用why_now
            if comm.get('regime_warn'):
                o.append(f'  {comm["regime_warn"]}')
            elif comm.get('why_now'):
                o.append(f'  ▸ {comm["why_now"]}')
            if comm.get('entry_tip'):
                o.append(f'  → {comm["entry_tip"]}')

        o.append(SEP)
        best = effective[0]
        bs   = float(best.get('score',0))
        bsym = best.get('symbol','').replace('USDT','')
        bd,_,_,_ = dir_str(best.get('direction') or best.get('signal_dir',''))
        if bs >= 145:
            o.append(f'  ⚡ 优先关注 ${bsym} {bd}（{bs:.0f}分）— 价格触达入场区后建仓')
        elif bs >= 120:
            o.append(f'  ⚡ 观察 ${bsym} {bd}，等确认K线再入，不追高')
        else:
            o.append(f'  ⚡ 无高质量信号，空仓等待是最优操作')

    o.append('')
    o.append(SEP2)
    o.append('📌 本内容为定时行情快照，实时行情以最新价为准')
    o.append('⚠️ 合约有风险，操作前请自行评估')
    # ── P2 IP标签 + 互动钩子（每帖必带）──────────────────────────
    o.append('')
    o.append('#梵天AI评分 #合约交易 #空头专家 #实盘透明')
    o.append('')
    o.append('👇 你有这些品种的仓位吗？留言聊聊')
    return clean('\n'.join(o))


# ════════════════════════════════════════════════════════
def auto_post_square(content: str) -> tuple:
    # ── 内容矛盾检测（发帖前最后一道门）──
    try:
        from content_validator import validate_post as _cv_validate
        _cv_ok, _cv_issues = _cv_validate(content)
        if not _cv_ok:
            _errs = [f"[{i['id']}] {i['msg']}" for i in _cv_issues if i['level']=='ERROR']
            print(f'[auto_post_square] ❌ 内容矛盾检测拦截: ' + '; '.join(_errs))
            return False, None, 'contradiction: ' + '; '.join(i['id'] for i in _cv_issues)
        for i in _cv_issues:
            print(f'[auto_post_square] ⚠️ [{i["id"]}] {i["msg"]}')
    except ImportError:
        pass
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'scripts' / 'square'))
        from poster import post_to_square
        result = post_to_square(content)
        # poster 可能返回 (ok, post_id, info) 或 (ok, results_list)
        if len(result) == 3:
            ok, post_id, info = result
            return ok, post_id, info
        elif len(result) == 2:
            ok, detail = result
            return ok, '', str(detail)
        return False, '', str(result)
    except Exception as e:
        return False, '', str(e)


if __name__ == '__main__':
    test = {
        'regime': 'BEAR_RECOVERY',
        'fg': 22, 'fg_label': 'FEAR',
        'ls_long': 63, 'top_long': 48,
        'oi_dir': 'DOWN',
        'nav': 127.37, 'pos_cnt': 0,
        'signals': [
            {'symbol': 'ETHUSDT', 'direction': 'SHORT', 'score': 151,
             'params': {'price': 2550, 'entry_lo': 2560, 'entry_hi': 2580,
                        'stop': 2630, 'tp1': 2420, 'tp2': 2300,
                        'rr1': 2.3, 'rr2': 3.5,
                        'ls_long': 63, 'fr': 0.0018, 'regime': 'BEAR_RECOVERY'}},
            {'symbol': 'BTCUSDT', 'direction': 'LONG', 'score': 128,
             'params': {'price': 68400, 'entry_lo': 67800, 'entry_hi': 68200,
                        'stop': 66500, 'tp1': 71000,
                        'rr1': 1.8, 'ls_long': 42, 'fr': -0.0003, 'regime': 'CHOP_MID'}},
        ]
    }
    print('===== Jarvis快讯 =====')
    print(format_flash(test))
    print()
    print('===== 广场帖 =====')
    print(format_square(test))


def _deep_analysis(regime: str, fg: int, ls: float, top: float,
                   oi_chg: float, funding: float, tickers: dict,
                   kronos: dict = None) -> list:
    """10年顶级交易员视角行情研判，3-5句，每句有观点有依据。"""
    out = []

    # ── 加载学习结论（零成本，本地读取）───────────────────
    _learning = {}
    try:
        import json as _j, pathlib as _p
        _lf = _p.Path(__file__).parent.parent / 'data' / 'news_learning.json'
        if _lf.exists():
            _learning = _j.loads(_lf.read_text())
    except Exception:
        pass
    _insights  = _learning.get('insights', [])
    _warnings  = _learning.get('warnings', [])
    _weak_hrs  = _learning.get('weak_hour_bands', [])

    # 1. 体制定性
    if 'BEAR_CRASH' in regime:
        out.append('当前暴跌体制，多头止损盘连锁触发——等待崩盘尾声出现"连续绿K止跌"信号，此前不轻易做多。')
    elif 'BEAR_TREND' in regime:
        out.append('空头趋势延续，每次反弹都是新的做空机会——结构上更低高点未被突破，反弹参与意义有限。')
    elif 'BEAR_EARLY' in regime:
        out.append('早期熊市：散户仍幻想反弹，机构却在减仓——顺势偏空，等反弹高点做空优先于逆势抄底。')
    elif 'BEAR_RECOVERY' in regime:
        out.append('熊市技术性反弹，历史上此类反弹平均持续2-5天——可轻仓参与，不宜重仓持单过夜，趋势未逆转。')
    elif 'CHOP_HIGH' in regime:
        out.append('高位震荡整理，成交量萎缩时向下突破概率历史上偏高——放量突破前，任何方向都需谨慎。')
    elif 'CHOP' in regime:
        out.append('中位震荡整理，多空都在测试对方耐心——没有有效突破之前，短线做波段优于方向性押注。')
    elif 'BULL_TREND' in regime:
        out.append('牛市主升浪进行中，趋势是最大的朋友——回踩10EMA或关键支撑是绝佳做多机会，不要空仓踏空。')
    elif 'BULL_RECOVERY' in regime:
        out.append('牛市回调修正，洗盘而非转势——关键支撑不破，回调都是上车机会。')
    else:
        out.append(f'体制{regime}，市场方向不明，等待明确信号后行动。')

    # 2. 情绪矛盾分析
    if fg <= 25 and ls >= 65:
        out.append(f'情绪矛盾：F&G={fg}恐慌，但{ls:.0f}%散户仍持多——"嘴上说怕手上不动"，筹码未出清，下跌仍未结束。')
    elif fg <= 20 and ls <= 40:
        out.append(f'极度恐慌+空头拥挤（F&G={fg}，仅{ls:.0f}%多头）——历史上常见阶段底部特征，等止跌K线确认。')
    elif fg >= 70 and ls >= 72:
        out.append(f'贪婪泛滥（F&G={fg}，{ls:.0f}%做多）——市场充分乐观，聪明钱在派发，此时追多是接盘。')
    elif fg <= 40 and ls >= 60:
        out.append(f'F&G={fg}恐惧，但{ls:.0f}%散户还在持多——下跌力度不够，散户未被彻底打趴，后续仍有下行空间。')
    elif 40 < fg <= 60:
        out.append(f'F&G={fg}情绪中性，市场处于无偏向的中间地带——等待情绪极值（<25或>75）出现时入场效率更高。')
    else:
        levels = ['极度恐惧','恐惧','中性','贪婪','极度贪婪']
        lv = levels[min(4, fg//20)]
        out.append(f'F&G={fg}（{lv}），情绪参考价值有限，需结合价格结构判断方向。')

    # 3. 大小户博弈
    diff = top - ls
    if diff < -12:
        out.append(f'机构多头（{top:.0f}%）低于散户（{ls:.0f}%）——机构偏空而散户偏多，跟机构方向历史胜率更高。')
    elif diff > 12:
        out.append(f'机构多头（{top:.0f}%）高于散户（{ls:.0f}%）——大户布局多头而散户悲观，聪明钱方向值得跟随。')
    else:
        out.append(f'机构（{top:.0f}%多）与散户（{ls:.0f}%多）站位接近，博弈均衡，暂无明显大小户分歧信号。')

    # 4. OI/FR异常时加
    if abs(oi_chg) >= 1.0:
        direction = '增加' if oi_chg > 0 else '减少'
        out.append(f'OI{direction}{abs(oi_chg):.1f}%——{"新仓入场，方向待验证" if oi_chg>0 else "仓位撤退，价格若反弹可能是空头平仓推动"}。')
    if abs(funding) >= 0.0005:
        payer = '多头' if funding > 0 else '空头'
        out.append(f'资金费率{funding*100:+.4f}%，{payer}持续付费——长期维持会触发强制平仓压力。')

    # 4b. Kronos 方向研判（有数据时插入）
    if kronos:
        btc_k = kronos.get('BTCUSDT', {})
        eth_k = kronos.get('ETHUSDT', {})
        btc_dir  = btc_k.get('direction','')
        btc_conf = float(btc_k.get('confidence', 0))
        eth_dir  = eth_k.get('direction','')
        eth_conf = float(eth_k.get('confidence', 0))
        if btc_dir and btc_conf >= 0.65:
            dir_cn = '看跌' if btc_dir == 'DOWN' else '看涨'
            conf_pct = int(btc_conf * 100)
            if eth_dir == btc_dir and eth_conf >= 0.65:
                out.append(f'量化方向引擎：BTC+ETH均{dir_cn}（置信{conf_pct}%）——两大权重标的同向，短线方向偏{"空" if btc_dir=="DOWN" else "多"}的概率更高。')
            else:
                out.append(f'量化方向引擎：BTC{dir_cn}（置信{conf_pct}%），{"ETH方向存在分歧" if eth_dir and eth_dir!=btc_dir else "ETH置信度不足"}——信号未完全共振，需等价格结构确认。')

    # 5a. 历史学习结论（来自 news_learning_engine）
    from datetime import datetime as _dt, timezone as _tz
    _bj_now_h = (_dt.now(_tz.utc).hour + 8) % 24
    _cur_band  = f'{(_bj_now_h // 4) * 4:02d}'
    if _cur_band in _weak_hrs:
        out.append(f'⚠️ 历史数据：当前时段（北京{_bj_now_h:02d}时）信号胜率偏低，建议降低仓位或等待更优时段。')
    # 当前体制是否有对应的学习结论
    _rd_stats = _learning.get('regime_dir_stats', {})
    _cur_key_short = f'{regime}+SHORT'
    _cur_key_long  = f'{regime}+LONG'
    for _rk in [_cur_key_short, _cur_key_long]:
        _st = _rd_stats.get(_rk, {})
        if _st.get('total', 0) >= 5 and _st.get('wr', 0) >= 0.80:
            _d_cn = '做空' if '+SHORT' in _rk else '做多'
            out.append(f'历史验证：当前体制{_d_cn}胜率{_st["wr"]:.0%}（{_st["total"]}次），属于系统优势区间。')
            break

    # 5. 策略结论
    if 'BEAR' in regime and fg <= 40 and ls >= 58:
        out.append('综合判断：偏空体制+散户未降仓，等反弹入场空单优于立即追空，耐心比勇气更值钱。')
    elif 'CHOP' in regime:
        out.append('震荡市最佳策略：降低仓位、缩短持仓时间、等突破选方向——确认后加仓，不在拉锯区间重仓押注。')
    elif 'BULL_TREND' in regime:
        out.append('趋势市：持有多单设移动止盈，每次回踩是加仓机会而非减仓时机。')
    elif 'BEAR_RECOVERY' in regime:
        out.append('反弹行情：明确止盈位轻仓参与，不贪婪——趋势未逆转前这只是借来的涨幅。')

    return out


# ═══════════════════════════════════════════════════════════════════
# P1-A  开盘快报帖  format_morning()
# ─ 每日BJ 08:00自动发帖，简洁100字内，建立账号活跃感
# ═══════════════════════════════════════════════════════════════════
def format_morning(data: dict) -> str:
    """每日开盘快报 — 姓赵不宣IP标准格式，100字左右，高频发帖保持活跃"""
    from datetime import datetime, timezone, timedelta
    BJ = timezone(timedelta(hours=8))
    bj_now   = datetime.now(BJ)
    regime   = data.get('regime', 'UNKNOWN')
    fg       = int(data.get('fg', 25))
    btc      = float(data.get('btc_price', 0))
    eth      = float(data.get('eth_price', 0))
    ls       = float(data.get('ls_long', 50))
    nav      = float(data.get('nav', 0))
    pos_cnt  = int(data.get('pos_cnt', 0))

    REGIME_CN = {
        'BEAR_TREND':    '🐻 空头趋势',
        'BEAR_RECOVERY': '⚠️ 熊市反弹',
        'BEAR_EARLY':    '📉 初期下跌',
        'CHOP_MID':      '↔️ 中位震荡',
        'BULL_TREND':    '🐂 牛市趋势',
        'BULL_PEAK':     '🔔 顶部区域',
    }
    regime_cn = REGIME_CN.get(regime, regime)

    FG_LABEL = {range(0,15):'极度恐慌💀', range(15,30):'恐慌😨',
                range(30,45):'偏空😟', range(45,55):'中性😐',
                range(55,70):'贪婪😏', range(70,101):'极度贪婪🤑'}
    fg_label = next((v for k,v in FG_LABEL.items() if fg in k), '未知')

    # 体制决策建议
    decision = {
        'BEAR_TREND':    '反弹即空，顺势而为',
        'BEAR_RECOVERY': '轻仓参与反弹，严守止盈',
        'BEAR_EARLY':    '防守为主，等结构确认',
        'CHOP_MID':      '降仓等待，突破再追',
        'BULL_TREND':    '持多移动止盈，回踩加仓',
        'BULL_PEAK':     '减仓锁利，警惕高位',
    }.get(regime, '观望为主')

    lines = [
        f'📊 梵天开盘快报｜{bj_now.strftime("%m/%d")} 08:00 BJ',
        f'',
        f'▌今日体制：{regime_cn}',
    ]
    if btc > 0:
        lines.append(f'▌BTC ${btc:,.0f}  ETH ${eth:,.2f}')
    lines += [
        f'▌FGI {fg} {fg_label}  散户持多 {ls:.0f}%',
        f'',
        f'⚡ 梵天判断：{decision}',
    ]
    if pos_cnt > 0:
        lines.append(f'📌 当前持仓 {pos_cnt} 个，进行中')
    if ls >= 65:
        lines.append(f'⚠️ 散户{ls:.0f}%做多 — 多头过度拥挤，上方压力大')
    lines += [
        f'',
        f'有S1信号时将在下方推送详细研判 👇',
        f'',
        f'#梵天AI评分 #合约交易 #{regime_cn.split()[1] if len(regime_cn.split())>1 else "趋势分析"}',
    ]
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# P1-B  实盘复盘帖  format_recap()
# ─ 每日BJ 16:00自动发帖，读live_signal_log展示当日结算结果
# ═══════════════════════════════════════════════════════════════════
def format_recap(data: dict) -> str:
    """每日复盘帖 — 展示已结算信号的真实盈亏，建立可验证信誉"""
    from datetime import datetime, timezone, timedelta
    BJ = timezone(timedelta(hours=8))
    bj_now   = datetime.now(BJ)
    settled  = data.get('settled', [])   # 今日结算列表
    open_pos = data.get('open_pos', [])  # 持仓中

    if not settled and not open_pos:
        return ''   # 无数据，不发帖

    lines = [
        f'📋 梵天每日复盘｜{bj_now.strftime("%m/%d")} 16:00 BJ',
        f'',
    ]

    if settled:
        lines.append(f'▌今日结算（{len(settled)}笔）')
        win_cnt  = sum(1 for s in settled if s.get('result') in ('TP1','TP2'))
        lose_cnt = sum(1 for s in settled if s.get('result') == 'SL')
        to_cnt   = sum(1 for s in settled if s.get('result') == 'TIMEOUT')
        wr = win_cnt / len(settled) * 100 if settled else 0
        lines.append(f'  胜率 {wr:.0f}%  盈={win_cnt} 亏={lose_cnt} 超时={to_cnt}')
        for s in settled[:4]:  # 最多展示4条
            sym    = s.get('symbol','?').replace('USDT','')
            result = s.get('result','?')
            pnl    = float(s.get('pnl_pct', 0))
            icon   = '✅' if result in ('TP1','TP2') else ('❌' if result=='SL' else '⏱️')
            lines.append(f'  {icon} ${sym} {result}  {pnl:+.1f}%')
        lines.append('')

    if open_pos:
        lines.append(f'▌持仓中（{len(open_pos)}个）')
        for p in open_pos[:3]:
            sym  = p.get('symbol','?').replace('USDT','')
            pnl  = float(p.get('pnl_pct', 0))
            dist = float(p.get('dist_to_tp1', 0))
            lines.append(f'  📍 ${sym}  当前 {pnl:+.1f}%  距T1={dist:.1f}%')
        lines.append('')

    lines += [
        f'⚠️ 梵天AI信号仅供技术参考，不构成投资建议',
        f'',
        f'#梵天AI评分 #实盘透明 #合约交易',
    ]
    return '\n'.join(lines)

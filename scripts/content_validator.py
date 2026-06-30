#!/usr/bin/env python3
"""
content_validator.py — 发帖内容逻辑矛盾检测器
设计院 · 2026-05-28

【设计理念】
  帖子发出前的最后一道门。
  不检查禁词（ip_wrapper负责），不检查格式（post_gate负责）。
  只检查一件事：内容有没有自相矛盾。

【典型矛盾（历史案例）】
  Bug#1: 175分S1 + 「观望」= 高分信号却说等方向，逻辑矛盾
  Bug#2: 空单方向 + 止损在入场下方 = 点位结构错误
  Bug#3: 入场区间 > 止损价（空单）= 方向与点位冲突

【调用方式】
  from content_validator import validate_post, validate_signal
  ok, issues = validate_post(text)
  ok, issues = validate_signal(score, direction, entry, stop, tp1)
"""

import re
from typing import Tuple, List

# ──────────────────────────────────────────────────────
# 逻辑矛盾规则表
# ──────────────────────────────────────────────────────
CONTRADICTION_RULES = [
    {
        'id':    'C01',
        'name':  '高分信号+观望',
        'desc':  'S1/S2信号（评分≥120）显示「观望」或「等方向」是逻辑矛盾',
        'level': 'ERROR',
    },
    {
        'id':    'C02',
        'name':  '空单止损在入场下方',
        'desc':  '做空：止损应在入场上方，否则方向与点位冲突',
        'level': 'ERROR',
    },
    {
        'id':    'C03',
        'name':  '多单止损在入场上方',
        'desc':  '做多：止损应在入场下方，否则方向与点位冲突',
        'level': 'ERROR',
    },
    {
        'id':    'C04',
        'name':  '空单目标在入场上方',
        'desc':  '做空：目标应在入场下方，否则盈利方向错误',
        'level': 'ERROR',
    },
    {
        'id':    'C05',
        'name':  '多单目标在入场下方',
        'desc':  '做多：目标应在入场上方，否则盈利方向错误',
        'level': 'ERROR',
    },
    {
        'id':    'C06',
        'name':  '做空帖子含「做多」信号词',
        'desc':  '帖子方向为做空，但正文出现「做多▲」关键词',
        'level': 'WARN',
    },
    {
        'id':    'C07',
        'name':  'R:R过低',
        'desc':  'R:R < 1.0 不值得入场，不应发帖',
        'level': 'WARN',
    },
]


def validate_signal(
    score: float,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    rr: float = 0,
) -> Tuple[bool, List[dict]]:
    """
    验证单个信号的逻辑一致性。
    返回 (ok, issues)
    issues 每项: {'id', 'level', 'msg'}
    """
    issues = []
    d = str(direction or '').strip().upper()
    is_short = 'SHORT' in d or '空' in d
    is_long  = 'LONG'  in d or '多' in d
    is_neutral = not is_short and not is_long

    # C01: 高分+观望
    if score >= 120 and is_neutral:
        issues.append({
            'id':    'C01',
            'level': 'ERROR',
            'msg':   f'score={score:.0f}(≥120) 但方向={direction!r}「观望」，逻辑矛盾',
        })

    if entry > 0 and stop > 0 and tp1 > 0:
        if is_short:
            # 空单：stop > entry > tp1
            if stop < entry:
                issues.append({'id':'C02','level':'ERROR',
                    'msg': f'做空: 止损{stop} < 入场{entry}，方向与点位冲突'})
            if tp1 > entry:
                issues.append({'id':'C04','level':'ERROR',
                    'msg': f'做空: 目标{tp1} > 入场{entry}，目标方向错误'})
        elif is_long:
            # 多单：stop < entry < tp1
            if stop > entry:
                issues.append({'id':'C03','level':'ERROR',
                    'msg': f'做多: 止损{stop} > 入场{entry}，方向与点位冲突'})
            if tp1 < entry:
                issues.append({'id':'C05','level':'ERROR',
                    'msg': f'做多: 目标{tp1} < 入场{entry}，目标方向错误'})

    # C07: R:R
    if rr > 0 and rr < 1.0:
        issues.append({'id':'C07','level':'WARN',
            'msg': f'R:R={rr:.2f} < 1.0，风险回报不达标'})

    ok = not any(i['level'] == 'ERROR' for i in issues)
    return ok, issues


def validate_post(text: str) -> Tuple[bool, List[dict]]:
    """
    对生成的帖子文本做逻辑矛盾扫描。
    返回 (ok, issues)
    """
    issues = []

    # 提取所有信号块（评分 + 方向）
    # 匹配: 🔴 S1  $ETH ⏸ 观望  175分 / 🔴 S1  $ETH 做空▼  175分
    sig_pattern = re.compile(
        r'([🔴🟠⚪])\s+(S\d[\+]?)\s+\$(\w+)\s+(做空▼|做多▲|⏸\s*观望|观望)\s+(\d+)分',
        re.UNICODE
    )
    for m in sig_pattern.finditer(text):
        badge, level, sym, direction_str, score_str = m.groups()
        score = float(score_str)
        is_observe = '观望' in direction_str

        # C01: 高分+观望
        if score >= 120 and is_observe:
            issues.append({
                'id':    'C01',
                'level': 'ERROR',
                'msg':   f'${sym} score={score:.0f}(≥120) 但显示「观望」，逻辑矛盾 → 检查direction字段',
            })

    # 提取点位块并验证
    # 简单模式：找「做空▼」后的入场/止损/目标
    price_val = re.compile(r'\$([0-9,]+\.?[0-9]*)')

    short_blocks = list(re.finditer(r'做空▼.*?(?=做空▼|做多▲|━|$)', text, re.DOTALL))
    long_blocks  = list(re.finditer(r'做多▲.*?(?=做空▼|做多▲|━|$)', text, re.DOTALL))

    def extract_prices(block_text):
        vals = [float(v.replace(',','')) for v in price_val.findall(block_text)]
        return vals

    for blk in short_blocks:
        blk_text = blk.group(0)
        if '📍' in blk_text and '🛡' in blk_text and '🎯' in blk_text:
            # 解析入场/止损/目标
            entry_m = re.search(r'📍\s*\$?([\d,]+\.?\d*)', blk_text)
            stop_m  = re.search(r'🛡\s*\$?([\d,]+\.?\d*)', blk_text)
            tp_m    = re.search(r'🎯\s*\$?([\d,]+\.?\d*)', blk_text)
            if entry_m and stop_m and tp_m:
                entry = float(entry_m.group(1).replace(',',''))
                stop  = float(stop_m.group(1).replace(',',''))
                tp    = float(tp_m.group(1).replace(',',''))
                if stop < entry:
                    issues.append({'id':'C02','level':'ERROR',
                        'msg': f'做空点位: 止损{stop} < 入场{entry}，方向冲突'})
                if tp > entry:
                    issues.append({'id':'C04','level':'ERROR',
                        'msg': f'做空点位: 目标{tp} > 入场{entry}，目标方向错误'})

    for blk in long_blocks:
        blk_text = blk.group(0)
        if '📍' in blk_text and '🛡' in blk_text and '🎯' in blk_text:
            entry_m = re.search(r'📍\s*\$?([\d,]+\.?\d*)', blk_text)
            stop_m  = re.search(r'🛡\s*\$?([\d,]+\.?\d*)', blk_text)
            tp_m    = re.search(r'🎯\s*\$?([\d,]+\.?\d*)', blk_text)
            if entry_m and stop_m and tp_m:
                entry = float(entry_m.group(1).replace(',',''))
                stop  = float(stop_m.group(1).replace(',',''))
                tp    = float(tp_m.group(1).replace(',',''))
                if stop > entry:
                    issues.append({'id':'C03','level':'ERROR',
                        'msg': f'做多点位: 止损{stop} > 入场{entry}，方向冲突'})
                if tp < entry:
                    issues.append({'id':'C05','level':'ERROR',
                        'msg': f'做多点位: 目标{tp} < 入场{entry}，目标方向错误'})

    ok = not any(i['level'] == 'ERROR' for i in issues)
    return ok, issues


# ──────────────────────────────────────────────────────
# 自测
# ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=== validate_signal() 测试 ===")
    # 矛盾1：高分+观望
    ok, issues = validate_signal(175, 'NEUTRAL', 2034, 2106, 1893, 2.5)
    print(f"  175分+NEUTRAL: ok={ok} issues={[i['id'] for i in issues]}")
    assert not ok and any(i['id']=='C01' for i in issues), "C01未检测到"

    # 矛盾2：做空止损在下方
    ok, issues = validate_signal(150, 'SHORT', 2034, 1900, 1800, 2.0)
    print(f"  SHORT止损下方: ok={ok} issues={[i['id'] for i in issues]}")
    assert not ok and any(i['id']=='C02' for i in issues), "C02未检测到"

    # 正常空单
    ok, issues = validate_signal(175, 'SHORT', 2034, 2106, 1893, 2.5)
    print(f"  正常SHORT:     ok={ok} issues={[i['id'] for i in issues]}")
    assert ok, "正常信号误报"

    # 正常多单
    ok, issues = validate_signal(150, 'LONG', 74000, 72000, 78000, 2.0)
    print(f"  正常LONG:      ok={ok} issues={[i['id'] for i in issues]}")
    assert ok, "正常信号误报"

    print("\n=== validate_post() 文本扫描测试 ===")
    # 模拟矛盾帖子（175分+观望）
    bad_post = """📡 加密市场行情快讯
🔴 S1  $ETH ⏸ 观望  175分
   现价 $2,015  等方向明确后入场"""
    ok, issues = validate_post(bad_post)
    print(f"  矛盾帖子: ok={ok} issues={[i['id'] for i in issues]}")
    assert not ok and any(i['id']=='C01' for i in issues), "C01文本扫描未检测"

    # 正常帖子
    good_post = """📡 加密市场行情快讯
🔴 S1  $ETH 做空▼  175分
   现价 $2,015
   📍$2,034  🛡$2,106  🎯$1,893  R:R=2.5x"""
    ok, issues = validate_post(good_post)
    print(f"  正常帖子: ok={ok} issues={[i['id'] for i in issues]}")
    assert ok, "正常帖子误报"

    print("\n✅ 全部断言通过")

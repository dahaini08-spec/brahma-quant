#!/usr/bin/env python3
"""
DD1太医官·守护官 — 钉钉1格式全系统守卫
职责：在任何文本发出前，校验是否符合唯一正确格式
不合格 → 拦截 + 报告错误原因，绝不放行

格式标准（最高指令 2026-05-27 更新）：
    根据新浪财经公开数据 SYMBOL/USDT
    【SYMBOL】哆 ▲  （做多）
    【SYMBOL】箜 ▼  （做空）
      入场区: $xxx ~ $xxx ✅/📌
      保  护: $xxx (x.xx%)
      目标一: $xxx x.xx% R:R=x.xx
      目标二: $xxx 盈亏比 1:x.xx

    ⚠️ 仅供模拟参考 内部讨论学习
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 规则定义
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULES: List[Tuple[str, re.Pattern, str]] = [
    (
        "L1_HEADER",
        re.compile(r'^根据新浪财经公开数据\s+\S+/USDT', re.MULTILINE),
        "第1行必须是「根据新浪财经公开数据 SYMBOL/USDT」",
    ),
    (
        "L2_DIRECTION",
        re.compile(r'^【\S+】(哆\s*▲|箜\s*▼)', re.MULTILINE),
        "第2行必须是「【SYMBOL】哆 ▲」或「【SYMBOL】箜 ▼」，禁止其他汉字",
    ),
    (
        "L4_ENTRY",
        re.compile(r'入场区[:：]\s*\$[\d,\.]+ ~ \$[\d,\.]+', re.MULTILINE),
        "必须包含「入场区: $xxx ~ $xxx」",
    ),
    (
        "L5_STOP",
        re.compile(r'保\s{2}护[:：]\s*\$[\d,\.]+ \([\d\.]+%\)', re.MULTILINE),
        "必须包含「保  护: $xxx (x.xx%)」— '保'后两空格'护'，含止损%",
    ),
    (
        "L6_TP1",
        re.compile(r'目标一[:：].*R:R=[\d\.]+', re.MULTILINE),
        "目标一必须包含 R:R=x.xx",
    ),
    (
        "L7_TP2",
        re.compile(r'目标二[:：].*盈亏比\s+1:[\d\.]+', re.MULTILINE),
        "目标二必须包含「盈亏比 1:x.xx」",
    ),
    (
        "DISCLAIMER",
        re.compile(r'⚠️\s*仅供模拟参考\s+内部讨论学习'),
        "最后必须是「⚠️ 仅供模拟参考 内部讨论学习」",
    ),
    (
        "NO_OLD_FIELDS",
        re.compile(r'方\u3000\u3000向|止\u3000\u3000损|体\u3000\u3000制|评\s*分[:：]'),
        None,  # 反向规则：匹配到则报错
    ),
    (
        "NO_MARKDOWN",
        re.compile(r'(?m)^#{1,3} |^\*\*|^- \['),
        None,  # 反向规则
    ),
]


@dataclass
class GuardResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def report(self) -> str:
        if self.ok:
            return "✅ DD1守护官：格式校验通过"
        lines = ["❌ DD1守护官：格式不合格，拦截推送", ""]
        for i, e in enumerate(self.errors, 1):
            lines.append(f"  [{i}] {e}")
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  ⚠️ {w}")
        return "\n".join(lines)


def guard(text: str) -> GuardResult:
    """
    校验钉钉1文本格式。
    返回 GuardResult(ok=True) 表示通过，ok=False 表示拦截。
    """
    errors = []
    warnings = []

    for rule_id, pattern, msg in RULES:
        if msg is None:
            # 反向规则：匹配到 = 错误
            if pattern.search(text):
                if rule_id == "NO_OLD_FIELDS":
                    errors.append("禁止使用旧格式字段（方向/止损/体制/评分）")
                elif rule_id == "NO_MARKDOWN":
                    errors.append("禁止Markdown语法（#标题/粗体/列表）")
        else:
            # 正向规则：未匹配 = 错误
            if not pattern.search(text):
                errors.append(msg)

    # 额外校验：哆/箜汉字一致性
    has_duo = bool(re.search(r'哆\s*▲', text))
    has_kong = bool(re.search(r'箜\s*▼', text))
    if has_duo and has_kong:
        errors.append("同一条策略不能同时含哆▲和箜▼")
    if not has_duo and not has_kong:
        errors.append("方向汉字缺失：做多用「哆 ▲」，做空用「箜 ▼」")

    # 警告：价格精度
    if re.search(r'\$\d{4,}', text) and not re.search(r'\$[\d,]+\.', text):
        warnings.append("高价标的建议保留小数点（如$103.21）")

    return GuardResult(ok=len(errors) == 0, errors=errors, warnings=warnings)


def guard_and_raise(text: str) -> str:
    """校验通过返回原文，不通过抛出 ValueError"""
    result = guard(text)
    if not result.ok:
        raise ValueError(result.report())
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI 快速校验
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    import sys

    # 从标准输入读取待校验文本
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        # 内置自测
        CORRECT = """根据新浪财经公开数据 CL/USDT
【CL】箜 ▼
  当前价: $103.21
  入场区: $103.50 ~ $104.20 ✅现价附近
  保  护: $105.50 (1.25%)
  目标一: $101.00 2.13% R:R=1.70
  目标二: $99.00 盈亏比 1:3.84
  RSI 1H=54 4H=61 日线=48 接近2周高点，MACD收缩

⚠️ 仅供模拟参考 内部讨论学习"""

        WRONG = """根据新浪财经公开数据 CL/USDT
🔴 CL-SHORT-001
方　　向：做空 ▼
当　前　价：$103
止　　损：$105
目标：$101 / $99
体　　制：NEUTRAL

⚠️ 仅供模拟参考 内部讨论学习"""

        print("=== 测试1：正确格式 ===")
        r1 = guard(CORRECT)
        print(r1.report())

        print("\n=== 测试2：错误格式 ===")
        r2 = guard(WRONG)
        print(r2.report())

    else:
        text = sys.stdin.read()
        result = guard(text)
        print(result.report())
        sys.exit(0 if result.ok else 1)

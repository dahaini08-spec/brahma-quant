"""
grade_utils.py — 梵天全局grade解析工具
设计院封印 2026-07-22 苏摩111

问题根因：
  - brahma_core.py / brahma_scoring.py 写入 grade = '🔴神级'/'🟠极强' 等中文emoji字符串
  - brahma_engine.py 写入 grade = int(structure_grade)
  - signal_watcher.py / auto_executor.py 等下游用正则r'(\d+)'解析 → 中文grade=0 → 信号误杀
  - 这个问题已修复多次但反复出现，根因是没有统一解析入口

彻底修复方案：
  1. 本模块作为唯一grade解析入口（SSOT）
  2. 所有上游写入时同步写 grade_num 整数字段
  3. 所有下游读取时用 parse_grade() 统一解析
"""

import re as _re

# 中文/emoji grade → 数字映射表（权威定义）
GRADE_LABEL_MAP = {
    '神级': 95,
    '极强': 85,
    '强+': 78,
    '强':  72,
    '中等': 55,
    '放弃': 0,
    # 英文字母级别
    'S': 95,
    'A': 85,
    'B': 72,
    'C': 55,
    'X': 0,
}

# 反向映射：数字 → 中文label（用于格式化输出）
def grade_to_label(grade_num: int) -> str:
    if grade_num >= 90: return '🔴神级'
    if grade_num >= 80: return '🟠极强'
    if grade_num >= 75: return '🟡强+'
    if grade_num >= 70: return '🟡强'
    if grade_num >= 50: return '🔵中等'
    return '⚫放弃'


def parse_grade(grade_val, structure_grade=0, effective_grade=0) -> int:
    """
    统一grade解析函数 — 所有模块的唯一入口
    
    优先级：
    1. grade_val 是 int/float → 直接用
    2. grade_val 是字符串 → 先映射表，再正则，再fallback
    3. 结果为0 → 尝试 effective_grade → structure_grade
    
    Args:
        grade_val: 原始grade值（int/float/str）
        structure_grade: 结构grade兜底（int）
        effective_grade: 体制感知grade兜底（float）
    
    Returns:
        int: 0-100的grade数值
    """
    # 已是数字
    if isinstance(grade_val, (int, float)):
        n = int(grade_val)
    elif isinstance(grade_val, str) and grade_val.strip():
        n = 0
        # 映射表优先（按key长度倒序，避免'强'匹配到'极强'）
        for kw in sorted(GRADE_LABEL_MAP.keys(), key=len, reverse=True):
            if kw in grade_val:
                n = GRADE_LABEL_MAP[kw]
                break
        # 映射失败 → 正则提数字
        if n == 0:
            m = _re.search(r'(\d+)', grade_val)
            n = int(m.group(1)) if m else 0
    else:
        n = 0
    
    # 兜底：effective_grade > structure_grade
    if n == 0 and effective_grade:
        n = int(effective_grade)
    if n == 0 and structure_grade:
        n = int(structure_grade)
    
    return max(0, min(100, n))


def enrich_signal_grade(signal_dict: dict) -> dict:
    """
    给信号字典注入 grade_num 字段（整数），不修改原始 grade 字段。
    在信号写入 live_signal_log.jsonl 之前调用。
    
    Args:
        signal_dict: 信号字典（in-place修改）
    Returns:
        signal_dict（同一对象）
    """
    grade_num = parse_grade(
        signal_dict.get('grade', 0),
        structure_grade=int(signal_dict.get('structure_grade', 0) or 0),
        effective_grade=float(signal_dict.get('effective_grade', 0) or 0),
    )
    signal_dict['grade_num'] = grade_num
    return signal_dict

#!/usr/bin/env python3
# STATUS: AUXILIARY — 独立工具模块，不接入主链路 [设计院 2026-07-26]
"""
brahma_constitutional_test.py — 梵天宪法守卫单元测试
设计院六方联合封印 2026-07-16 苏摩111批准

职责：
  - 每次修改核心文件后自动回归验证
  - 防止修复复发（死穴逻辑/积分/体制/仓位）
  - 被 brahma_smoke_test.py / post-commit hook 调用

运行: python3 brahma_constitutional_test.py
"""

import sys, ast, re, os
from pathlib import Path

BASE  = Path(__file__).parent
ROOT  = BASE.parent
BRAIN = BASE

PASS = []
FAIL = []

def check(name: str, cond: bool, detail: str = ''):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f'{name}: {detail}')

# ══════════════════════════════════════════════════════════════════
# A. 语法完整性
# ══════════════════════════════════════════════════════════════════
for fname in ['brahma_core.py','brahma_scoring.py','brahma_analysis_runner.py',
              'position_sizer.py','timing_filter.py','brahma_bus.py']:
    fpath = BRAIN / fname
    try:
        ast.parse(fpath.read_text())
        check(f'syntax:{fname}', True)
    except SyntaxError as e:
        check(f'syntax:{fname}', False, str(e))

# ══════════════════════════════════════════════════════════════════
# B. P0-1: 死穴封禁必须同步置零 _score_gate_ok
# ══════════════════════════════════════════════════════════════════
core_src = (BRAIN / 'brahma_core.py').read_text()
# P0-5 检查时需要包含 block_c（regime_label 已迁移至此）
_block_c_src = (BRAIN / 'brahma_core_block_c.py').read_text() if (BRAIN / 'brahma_core_block_c.py').exists() else ''
dead_blocks = core_src.count('_score_gate_ok = False  # [P0-1')
check('P0-1:死穴3处_score_gate_ok', dead_blocks == 3,
      f'期望3处，实际{dead_blocks}处，_valid=False可能被L3742覆盖')

# ══════════════════════════════════════════════════════════════════
# C. P0-2: 死穴后乘数节点必须有 _in_dead_zone 标志
# ══════════════════════════════════════════════════════════════════
sc_src = (BRAIN / 'brahma_scoring.py').read_text()
check('P0-2:_in_dead_zone标志存在', '_in_dead_zone' in sc_src,
      '死穴乘数后奖励层缺少_in_dead_zone门控，末尾+46分可推升死穴信号')

# ══════════════════════════════════════════════════════════════════
# D. P0-3: N16豁免必须是if/elif/else三分支
# ══════════════════════════════════════════════════════════════════
check('P0-3:N16豁免三分支', 'elif 155 <= score < 165:' in sc_src,
      'N16豁免if/else结构导致豁免后仍执行惩罚')

# ══════════════════════════════════════════════════════════════════
# E. P0-4: regime读取必须有顶层兜底
# ══════════════════════════════════════════════════════════════════
rn_src = (BRAIN / 'brahma_analysis_runner.py').read_text()
check('P0-4:双路径regime兜底', "or str(_rf.get('regime'" in rn_src,
      'BEAR_RECOVERY阈值120修正因params.regime为空而失效')

# ══════════════════════════════════════════════════════════════════
# F. P0-5: regime_label必须在使用前定义
# ══════════════════════════════════════════════════════════════════
check('P0-5:regime_label定义', 'regime_label = str(ms.get' in core_src or 'regime_label = str(ms.get' in _block_c_src,
      'confluence_score()中regime_label NameError导致online_bayes永久静默')

# ══════════════════════════════════════════════════════════════════
# G. P0-6: flush_stale不得实例化新对象
# ══════════════════════════════════════════════════════════════════
bus_src = (BRAIN / 'brahma_bus.py').read_text()
flush_body = bus_src.split('def flush_stale')[1].split('\ndef ')[0] if 'def flush_stale' in bus_src else ''
check('P0-6:flush_stale不实例化', 'BrahmaBus()' not in flush_body,
      'flush_stale内部实例化新BrahmaBus，全局缓存永远不清理')

# ══════════════════════════════════════════════════════════════════
# H. P0-7: max字段必须=175
# ══════════════════════════════════════════════════════════════════
check('P0-7:max=175', "'max':        175," in sc_src,
      'max=150与实际天花板175矛盾，归一化超界')

# ══════════════════════════════════════════════════════════════════
# I. P1-1: _br_grade必须是数值
# ══════════════════════════════════════════════════════════════════
check('P1-1:br_grade数值化', '_br_grade = float' in core_src,
      '_br_grade读取grade字符串，>=85比较TypeError导致BEAR_RECOVERY_SHORT永久封禁')

# ══════════════════════════════════════════════════════════════════
# J. P1-2: _apply_calib必须接入至少1个维度
# ══════════════════════════════════════════════════════════════════
check('P1-2:calib至少接入1维', "_apply_calib('s1_trend'" in sc_src,
      '_apply_calib定义但从未调用，在线学习权重对评分零影响')

# ══════════════════════════════════════════════════════════════════
# K. P1-3: s18不得直接覆盖score
# ══════════════════════════════════════════════════════════════════
check('P1-3:s18使用+=', 'score += s18  # [P1-3' in sc_src,
      'score=_cal_score绕过s18上限，breakdown无法对账')

# ══════════════════════════════════════════════════════════════════
# L. P1-5: BTC单边下行豁免通道必须实现
# ══════════════════════════════════════════════════════════════════
tf_src = (BRAIN / 'timing_filter.py').read_text()
check('P1-5:_check_bearish_4h_streak实现', '_check_bearish_4h_streak' in tf_src,
      '注释有但代码无，BEAR阶段WAIT门控失灵')

# ══════════════════════════════════════════════════════════════════
# M. P1-6: BEAR_RECOVERY SHORT s6不得加分
# ══════════════════════════════════════════════════════════════════
check('P1-6:s6无BEAR_RECOVERY加分_精确', "('CHOP_HIGH', 'BULL_PEAK'): s6 += 5" in sc_src,
      'BEAR_RECOVERY做空s6+5矛盾未修复')

# ══════════════════════════════════════════════════════════════════
# N. 积分天花板一致性: 所有min(score,N)的N必须=175
# ══════════════════════════════════════════════════════════════════
bad_caps = re.findall(r'min\(score\s*\+\s*\w+,\s*(\d+)\)', sc_src)
wrong_caps = [c for c in bad_caps if int(c) != 175]
check('积分天花板全部=175', len(wrong_caps) == 0,
      f'存在非175天花板: {set(wrong_caps)}')

# ══════════════════════════════════════════════════════════════════
# 输出结果
# ══════════════════════════════════════════════════════════════════
total = len(PASS) + len(FAIL)
print(f'\n🏛️ 梵天宪法守卫测试 — {len(PASS)}/{total} 通过\n')
for p in PASS:
    print(f'  ✅ {p}')
if FAIL:
    print()
    for f in FAIL:
        print(f'  ❌ {f}')
    print(f'\n⚠️  {len(FAIL)}项失败，请修复后重新提交')
    sys.exit(1)
else:
    print('\n🟢 所有宪法检查通过')
    sys.exit(0)

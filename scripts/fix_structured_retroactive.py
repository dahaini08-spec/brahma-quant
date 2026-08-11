#!/usr/bin/env python3
"""
fix_structured_retroactive.py — 追溯修复brahma_structured.jsonl历史字段
[设计院 2026-08-11 模拟验证]

问题: regime/direction存在metrics嵌套里，顶层缺失
修复: 把metrics.regime/direction提升到顶层，不改动metrics内容
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / 'data'
SRC  = DATA / 'brahma_structured.jsonl'
BAK  = DATA / 'brahma_structured.jsonl.bak_fix20260811'

if not SRC.exists():
    print("文件不存在"); exit(1)

lines = [json.loads(l) for l in SRC.read_text().strip().split('\n') if l.strip()]
print(f"原始: {len(lines)}条")

# 备份
import shutil
shutil.copy(SRC, BAK)
print(f"备份 → {BAK}")

fixed = 0
new_lines = []
for l in lines:
    m = l.get('metrics', {}) or {}
    # 顶层缺regime时，从metrics提升
    if not l.get('regime') and m.get('regime'):
        l['regime']    = m['regime']
        l['direction'] = m.get('direction', '')
        fixed += 1
    new_lines.append(l)

SRC.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in new_lines) + '\n')
print(f"修复: {fixed}条 | 总计: {len(new_lines)}条")

# 验证
has_regime = sum(1 for l in new_lines if l.get('regime'))
print(f"修复后 regime有值: {has_regime}/{len(new_lines)}")

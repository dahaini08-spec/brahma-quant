#!/usr/bin/env python3
"""[P4-TA 2026-10-01] analyze() 候选拆分块的耦合度分析器。
只读分析：对 brahma_core.analyze 内每个顶层(4空格缩进)语句段，
统计 load/store 名字，判定外泄变量（块内定义、块后被引用）与外源变量（块外定义、块内引用）。
输出紧凑表：行区间 / 大小 / 外泄 / 外源。接入位置：scripts/p4_coupling.py
"""
import ast, sys

SRC = 'brahma_brain/brahma_core.py'
src = open(SRC).read()
tree = ast.parse(src)

# 定位 analyze 函数
fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'analyze':
        fn = node
        break
if fn is None:
    print('analyze not found'); sys.exit(1)

body = fn.body
lines = src.splitlines()

def names_of(node, mode):
    """mode='load'|'store'"""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            if mode == 'load' and isinstance(n.ctx, ast.Load): out.add(n.id)
            if mode == 'store' and isinstance(n.ctx, (ast.Store, ast.AugStore)): out.add(n.id)
        elif isinstance(n, ast.AugAssign) and mode == 'store':
            if isinstance(n.target, ast.Name): out.add(n.target.id)
    return out

# 预计算每个顶层语句的 load/store
segs = []
for st in body:
    if not hasattr(st, 'lineno'): continue
    L0, L1 = st.lineno, getattr(st, 'end_lineno', st.lineno)
    if L1 - L0 < 8: continue  # 只关心≥8行的段
    segs.append({'l0': L0, 'l1': L1, 'st': st, 'load': names_of(st, 'load'), 'store': names_of(st, 'store')})

# 函数参数与顶层try外定义
args = {a.arg for a in fn.args.args}
print(f'analyze: L{body[0].lineno}-L{body[-1].end_lineno}, 参数: {sorted(args)[:8]}')
print(f'≥8行段数: {len(segs)}')
print(f'{"区间":>14} {"行数":>5} {"外泄":<28} {"外源数":>4}')
for i, s in enumerate(segs):
    # 外泄: 本段store的、且后续段load的名字
    leak = set()
    for s2 in segs[i+1:]:
        leak |= (s['store'] & s2['load'])
    # 外源: 本段load但之前段/参数没store过、且不是内置/模块名
    defined = set(args)
    for s2 in segs[:i]:
        defined |= s2['store']
    extern = s['load'] - defined - leak
    # 过滤明显是import进来的模块名与全局
    extern = {n for n in extern if not n.startswith('_e') and len(n) > 2}
    mark = ' <-- 候选' if (len(leak) <= 2 and len(extern) <= 3) else ''
    print(f'L{s["l0"]}-{s["l1"]:>5} {s["l1"]-s["l0"]+1:>5} {",".join(sorted(leak))[:26]:<28} {len(extern):>4}{mark}')

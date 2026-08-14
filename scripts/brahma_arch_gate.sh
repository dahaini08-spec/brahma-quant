#!/bin/bash
# brahma_arch_gate.sh — 梵天代码架构守门脚本（Sentrux替代方案）
# [设计院封印 2026-08-14 苏摩111]
# 用法:
#   bash scripts/brahma_arch_gate.sh save   # 改代码前保存基线
#   bash scripts/brahma_arch_gate.sh check  # 改代码后对比
# 集成: git pre-commit hook 自动触发

set -e
BASELINE_FILE=".arch_baseline.json"
TARGET_DIR="brahma_brain"
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

compute_metrics() {
    python3 - << 'PYEOF'
import os, json, ast
from pathlib import Path

metrics = {}
total_lines = 0
total_funcs = 0
total_classes = 0
complex_funcs = []

for pyf in sorted(Path("brahma_brain").glob("*.py")):
    try:
        src = pyf.read_text()
        tree = ast.parse(src)
        lines = len(src.splitlines())
        funcs = len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)])
        classes = len([n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)])
        total_lines += lines
        total_funcs += funcs
        total_classes += classes
        if lines > 500:
            complex_funcs.append(f"{pyf.name}({lines}L)")
    except Exception:
        pass

# radon复杂度
try:
    import subprocess
    r = subprocess.run(
        ["python3","-m","radon","cc","brahma_brain/","-a","-nc"],
        capture_output=True, text=True
    )
    avg_cc_line = [l for l in r.stdout.splitlines() if "Average complexity" in l]
    avg_cc = float(avg_cc_line[0].split("(")[1].rstrip(")")) if avg_cc_line else 0
except:
    avg_cc = 0

result = {
    "total_lines": total_lines,
    "total_funcs": total_funcs,
    "total_classes": total_classes,
    "avg_complexity": round(avg_cc, 2),
    "large_modules": complex_funcs[:5],
    "module_count": len(list(Path("brahma_brain").glob("*.py")))
}
print(json.dumps(result))
PYEOF
}

case "${1:-check}" in
  save)
    echo -e "${YELLOW}[arch_gate] 保存基线...${NC}"
    compute_metrics > "$BASELINE_FILE"
    cat "$BASELINE_FILE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'  总行数: {d[\"total_lines\"]}')
print(f'  函数数: {d[\"total_funcs\"]}')
print(f'  模块数: {d[\"module_count\"]}')
print(f'  平均复杂度: {d[\"avg_complexity\"]}')
print(f'  大模块: {d[\"large_modules\"]}')
"
    echo -e "${GREEN}✅ 基线已保存到 $BASELINE_FILE${NC}"
    ;;

  check)
    if [ ! -f "$BASELINE_FILE" ]; then
        echo -e "${YELLOW}⚠️ 无基线，先运行: bash scripts/brahma_arch_gate.sh save${NC}"
        exit 0
    fi
    echo -e "${YELLOW}[arch_gate] 对比架构质量...${NC}"
    CURRENT=$(compute_metrics)
    python3 - << PYEOF
import json, sys

baseline = json.load(open("$BASELINE_FILE"))
current = json.loads('''$CURRENT''')

print("\n[梵天架构守门] 对比报告")
print("="*50)
fields = [
    ("总行数",        "total_lines",    1000,  False),
    ("函数数",        "total_funcs",    50,    False),
    ("模块数",        "module_count",   3,     False),
    ("平均复杂度",    "avg_complexity", 0.5,   True),
]
issues = 0
for label, key, threshold, lower_better in fields:
    old = baseline.get(key, 0)
    new = current.get(key, 0)
    delta = new - old
    if lower_better:
        bad = delta > threshold
    else:
        bad = delta > threshold
    status = "🔴 退步" if bad else ("🟢 改善" if delta < 0 else "⚪ 无变化")
    print(f"  {label:<12}: {old} → {new} ({'+' if delta>=0 else ''}{delta})  {status}")
    if bad: issues += 1

print("="*50)
if issues == 0:
    print("✅ 架构质量未退步，可以提交")
    sys.exit(0)
else:
    print(f"⚠️ {issues} 项指标退步，请检查后再提交")
    sys.exit(1)
PYEOF
    ;;

  *)
    echo "用法: bash $0 [save|check]"
    ;;
esac

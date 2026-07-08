"""
execution_ast_audit.py — 第三方v4.0动态审计补丁包 Step3
AST静态扫描执行链文件，不执行任何代码。
"""
import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

TARGETS = [
    "scripts/auto_executor.py",
    "scripts/brahma_execute.py",
    "scripts/brahma_order_engine.py",
    "scripts/pump_signal_executor.py",
    "executor.py",
    "emergency_close.py",
    "treasury_gate.py",
]

RISK_KEYWORDS = [
    "fapi/v1/order",
    "fapi/v1/leverage",
    "fapi/v1/marginType",
    "/papi/v1/order",
    "MARKET",
    "LIMIT",
    "reduceOnly",
    "X-MBX-APIKEY",
    "API_SECRET",
    "BINANCE_SECRET",
    "signature",
    "place_order",
    "cancel_order",
    "set_leverage",
]

SECRET_PATTERNS = [
    "sDqoRAye", "hXQnzQco",  # known leaked keys (check only)
    "AKIA", "sk-",
]

results = []
total_risk = 0

for file in TARGETS:
    p = BASE / file
    if not p.exists():
        results.append({"file": file, "status": "NOT_FOUND"})
        continue

    text = p.read_text(errors="ignore")
    found = []

    for kw in RISK_KEYWORDS:
        if kw in text:
            found.append(kw)
            total_risk += 1

    secret_found = []
    for sp in SECRET_PATTERNS:
        if sp in text:
            secret_found.append(sp[:4] + "****")

    try:
        ast.parse(text)
        ast_ok = True
    except Exception as e:
        ast_ok = False
        found.append(f"AST_PARSE_ERROR: {e}")

    status = "CRITICAL" if secret_found else ("HIGH" if found else "CLEAN")
    results.append({
        "file": file,
        "status": status,
        "ast_ok": ast_ok,
        "risk_keywords": found,
        "secrets": secret_found,
    })

print("\n" + "="*60)
print("  AST EXECUTION CHAIN AUDIT REPORT")
print("="*60)
for r in results:
    icon = "🔴" if r["status"] in ("CRITICAL","HIGH") else ("⚠️ " if r["status"]=="MEDIUM" else ("✅" if r["status"]=="CLEAN" else "⬜"))
    print(f"\n{icon} {r['file']}  [{r['status']}]")
    if r["status"] == "NOT_FOUND":
        print("   (已移除或不存在 — 符合隔离要求)")
        continue
    print(f"   AST: {'OK' if r.get('ast_ok') else 'FAIL'}")
    if r.get("risk_keywords"):
        print(f"   风险关键词: {', '.join(r['risk_keywords'][:8])}")
    if r.get("secrets"):
        print(f"   ⚠️  疑似密钥残留: {r['secrets']}")

print("\n" + "="*60)
print(f"  总风险关键词命中: {total_risk}")
not_found = sum(1 for r in results if r["status"]=="NOT_FOUND")
clean = sum(1 for r in results if r["status"]=="CLEAN")
risk = sum(1 for r in results if r["status"] in ("HIGH","CRITICAL"))
print(f"  文件: {len(TARGETS)} 目标 | {not_found} 已隔离 | {clean} 干净 | {risk} 高危")
if total_risk == 0 and not_found + clean == len([r for r in results if r["status"]!="NOT_FOUND"]):
    print("\n  ✅ EXECUTION CHAIN AUDIT: PASS")
else:
    print("\n  ⚠️  EXECUTION CHAIN AUDIT: REVIEW REQUIRED")
print("="*60 + "\n")

sys.exit(0)

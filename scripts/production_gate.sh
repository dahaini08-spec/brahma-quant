#!/usr/bin/env bash
# scripts/production_gate.sh
# Brahma v6 Production Gate — Full-Auto Live Lite
# Phase 5 | 2026-07-09
set -e

BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"

PASS=0
FAIL=0

run_check() {
    local name="$1"; shift
    if "$@" > /tmp/brahma_gate_check.log 2>&1; then
        echo "  ✅ $name"
        PASS=$((PASS+1))
    else
        echo "  ❌ $name"
        cat /tmp/brahma_gate_check.log | head -10
        FAIL=$((FAIL+1))
    fi
}

echo "═══════════════════════════════════════════════════════"
echo "  Brahma v6 Production Gate — Full-Auto Live Lite"
echo "═══════════════════════════════════════════════════════"
echo ""

echo "▶ [1/5] Compile check..."
run_check "compileall brahma_v6" python3 -m compileall brahma_v6 -q
run_check "compileall tests"     python3 -m compileall tests -q

echo ""
echo "▶ [2/5] pyproject.toml..."
run_check "pyproject.toml" python3 -c "
import tomllib
with open('pyproject.toml','rb') as f: tomllib.load(f)
print('pyproject OK')
"

echo ""
echo "▶ [3/5] pytest full suite..."
run_check "pytest tests/" python3 -m pytest tests/ -q --tb=short

echo ""
echo "▶ [4/5] Import smoke..."
run_check "import smoke" python3 scripts/check_import_smoke.py

echo ""
echo "▶ [5/5] Live safety..."
run_check "live safety" python3 scripts/check_live_safety.py

echo ""
echo "═══════════════════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
    echo "  ✅ PRODUCTION GATE: PASS  ($PASS checks)"
    echo "  Ready to proceed to 10M simulation + Test Order"
    exit 0
else
    echo "  ❌ PRODUCTION GATE: FAIL  ($FAIL/$((PASS+FAIL)) failed)"
    exit 1
fi

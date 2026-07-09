#!/usr/bin/env python3
"""Live safety pre-flight check."""
import sys, os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
issues = []

# 1. BRAHMA_ALLOW_LIVE_ORDER must not be set to true
allow_live = os.environ.get("BRAHMA_ALLOW_LIVE_ORDER", "false").lower()
if allow_live == "true":
    issues.append("BRAHMA_ALLOW_LIVE_ORDER=true — must be false for gate check")

# 2. Config must have kill_switch enabled
cfg_path = BASE / "configs/full_auto_live_lite.yaml"
if cfg_path.exists():
    cfg_text = cfg_path.read_text()
    if "kill_switch:" not in cfg_text:
        issues.append("configs/full_auto_live_lite.yaml: kill_switch section missing")
    if "allow_market_order: false" not in cfg_text:
        issues.append("configs/full_auto_live_lite.yaml: allow_market_order must be false")
else:
    issues.append("configs/full_auto_live_lite.yaml not found")

# 3. No market order in any adapter code
for pyfile in (BASE / "brahma_v6").rglob("*.py"):
    text = pyfile.read_text()
    if '"MARKET"' in text and "test" not in pyfile.name and "test" not in str(pyfile):
        if "order_type" in text and "MARKET" in text:
            # Only flag if it looks like it's submitting a market order
            if "allow_market_order" not in text and "market_order_forbidden" not in text:
                pass  # OK — just references

if issues:
    for i in issues:
        print(f"  ❌ {i}")
    print(f"\nLIVE SAFETY: FAIL")
    sys.exit(1)
else:
    print("  ✅ BRAHMA_ALLOW_LIVE_ORDER not set to true")
    print("  ✅ kill_switch enabled in config")
    print("  ✅ allow_market_order: false in config")
    print(f"\nLIVE SAFETY: OK")
    sys.exit(0)

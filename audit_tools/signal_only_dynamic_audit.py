"""
signal_only_dynamic_audit.py — 第三方v4.0动态审计补丁包 Step4
只读行情 + 信号评分动态测试，完全禁止任何下单路径。
"""
import os
import sys
import json
import time
import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

# ── 1. 安装网络拦截 ──────────────────────────────────────────────
from audit_tools.no_trade_guard import install
install()

# ── 2. 强制清零 key ──────────────────────────────────────────────
for k in ("BINANCE_API_KEY", "BINANCE_SECRET", "BINANCE_API_SECRET"):
    os.environ[k] = ""

# ── 3. 输出目录 ──────────────────────────────────────────────────
OUT = BASE / "audit_outputs" / "signal_only_dynamic_audit.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DIRECTIONS = ["LONG", "SHORT"]

# ── 4. 尝试导入分析入口 ──────────────────────────────────────────
try:
    from brahma_brain.brahma_analysis_runner import run_analysis
    USE_RUNNER = True
    print("[IMPORT] Using brahma_analysis_runner.run_analysis ✅")
except ImportError:
    USE_RUNNER = False
    print("[IMPORT] brahma_analysis_runner not found, falling back to brahma_orchestrator")
    try:
        from brahma_brain.brahma_orchestrator import analyze as _analyze
        def run_analysis(symbol, direction="LONG", **kw):
            r = _analyze(symbol, signal_dir=direction, deep=False)
            return r
        print("[IMPORT] brahma_orchestrator.analyze ✅")
    except ImportError as e:
        print(f"[IMPORT FAIL] {e}")
        run_analysis = None

rows = []
passed = 0
failed = 0

print("\n" + "="*60)
print("  SIGNAL-ONLY DYNAMIC AUDIT  (NO TRADING)")
print("="*60)

for sym in SYMBOLS:
    for direction in DIRECTIONS:
        row = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbol": sym,
            "direction": direction,
            "mode": "SIGNAL_ONLY_DYNAMIC_AUDIT",
        }
        t0 = time.time()
        try:
            if run_analysis is None:
                raise RuntimeError("No analysis function available")
            result = run_analysis(sym, direction=direction) if USE_RUNNER else run_analysis(sym, direction=direction)
            elapsed = round(time.time() - t0, 3)
            row.update({
                "elapsed_sec": elapsed,
                "regime": result.get("regime") if isinstance(result, dict) else None,
                "grade": result.get("grade") if isinstance(result, dict) else None,
                "score": result.get("score") if isinstance(result, dict) else None,
                "blocked": result.get("blocked", False) if isinstance(result, dict) else None,
                "valid": result.get("valid_signal") if isinstance(result, dict) else None,
                "reason": result.get("reason") or result.get("block_reason") if isinstance(result, dict) else None,
                "ok": True,
            })
            passed += 1
            icon = "✅"
        except RuntimeError as e:
            if "NO_TRADE_GUARD" in str(e):
                row.update({"ok": False, "error_type": "BLOCKED_BY_GUARD", "error": str(e), "elapsed_sec": round(time.time()-t0,3)})
                failed += 1
                icon = "🔴 GUARD BLOCKED"
            else:
                row.update({"ok": False, "error_type": type(e).__name__, "error": str(e)[:200], "elapsed_sec": round(time.time()-t0,3)})
                failed += 1
                icon = "⚠️ "
        except Exception as e:
            row.update({"ok": False, "error_type": type(e).__name__, "error": str(e)[:200], "elapsed_sec": round(time.time()-t0,3)})
            failed += 1
            icon = "⚠️ "

        rows.append(row)
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        print(f"\n{icon} {sym} {direction}")
        print(f"   regime={row.get('regime')}  score={row.get('score')}  grade={row.get('grade')}")
        print(f"   blocked={row.get('blocked')}  valid={row.get('valid')}  elapsed={row.get('elapsed_sec')}s")
        if row.get("error"):
            print(f"   error: {row['error'][:100]}")

print("\n" + "="*60)
print(f"  结果: {passed} PASS / {failed} FAIL / {len(rows)} 总计")
print(f"  输出: {OUT}")
guard_blocks = sum(1 for r in rows if r.get("error_type") == "BLOCKED_BY_GUARD")
if guard_blocks:
    print(f"  🔴 {guard_blocks} 次下单请求被 NO_TRADE_GUARD 拦截！")
else:
    print("  ✅ 无下单请求泄漏")
print("="*60 + "\n")

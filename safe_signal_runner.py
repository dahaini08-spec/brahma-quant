"""
safe_signal_runner.py — 第三方v4.0动态审计补丁包 Step6
安全信号运行器：只读行情 + 梵天评分 + JSONL输出，完全隔离执行链。
"""
from __future__ import annotations
import os
import sys
import json
import time
import datetime
import argparse
from pathlib import Path
from typing import List, Dict, Optional

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# ── 安全闸 ──────────────────────────────────────────────────────
os.environ.update({
    "BRAHMA_SIGNAL_ONLY": "true",
    "BRAHMA_LIVE_TRADING_ENABLED": "false",
    "AGENT_LIVE_TRADING_ENABLED": "false",
    "PAPER_TRADING_DEFAULT": "true",
    "BRIDGE_DRY_RUN": "true",
    "BINANCE_API_KEY": "",
    "BINANCE_SECRET": "",
    "BINANCE_API_SECRET": "",
})

# ── 公开行情客户端 ────────────────────────────────────────────────
from safe_binance_public_client import get_ticker_price, get_ticker_24h, get_open_interest

# ── 导入分析入口 ─────────────────────────────────────────────────
_analyze_fn = None
_analyze_mode = "unavailable"

try:
    from brahma_brain.brahma_analysis_runner import run_analysis as _ra
    _analyze_fn = _ra
    _analyze_mode = "brahma_analysis_runner"
except ImportError:
    pass

if _analyze_fn is None:
    try:
        from brahma_brain.brahma_orchestrator import analyze as _oa
        _analyze_fn = lambda sym, direction="LONG", **kw: _oa(sym, signal_dir=direction, deep=False)
        _analyze_mode = "brahma_orchestrator"
    except ImportError:
        pass


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_DIRECTIONS = ["LONG", "SHORT"]

OUT_DIR = BASE / "audit_outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_signal_scan(
    symbols: List[str] = None,
    directions: List[str] = None,
    output_file: Optional[str] = None,
    min_score: float = 0,
) -> List[Dict]:
    """
    执行只读信号扫描，返回结果列表。
    不触发任何下单、不使用 API key。
    """
    symbols = symbols or DEFAULT_SYMBOLS
    directions = directions or DEFAULT_DIRECTIONS
    ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / (output_file or f"safe_signal_{ts_str}.jsonl")

    results = []
    print(f"\n{'='*60}")
    print(f"  SAFE SIGNAL RUNNER  [{_analyze_mode}]")
    print(f"  {len(symbols)} symbols × {len(directions)} directions")
    print(f"  Output: {out_path}")
    print(f"{'='*60}")

    for sym in symbols:
        # 先拉公开行情作为背景信息
        try:
            t24h = get_ticker_24h(sym)
            price = float(t24h["lastPrice"])
            change = float(t24h["priceChangePercent"])
            market_info = {"price": price, "change_pct": change}
        except Exception as e:
            market_info = {"price": None, "error": str(e)}

        for direction in directions:
            row: Dict = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "symbol": sym,
                "direction": direction,
                "market": market_info,
                "runner": _analyze_mode,
            }
            t0 = time.time()

            if _analyze_fn is None:
                row.update({"ok": False, "error": "No analysis function available"})
            else:
                try:
                    result = _analyze_fn(sym, direction=direction)
                    score = result.get("score", 0) if isinstance(result, dict) else 0
                    row.update({
                        "ok": True,
                        "elapsed_sec": round(time.time() - t0, 3),
                        "regime": result.get("regime") if isinstance(result, dict) else None,
                        "score": score,
                        "grade": result.get("grade") if isinstance(result, dict) else None,
                        "blocked": result.get("blocked", False) if isinstance(result, dict) else True,
                        "valid_signal": result.get("valid_signal", False) if isinstance(result, dict) else False,
                        "reason": result.get("reason") or result.get("block_reason") if isinstance(result, dict) else None,
                        "timing": result.get("timing_badge") if isinstance(result, dict) else None,
                    })
                except Exception as e:
                    row.update({"ok": False, "elapsed_sec": round(time.time()-t0, 3),
                                "error_type": type(e).__name__, "error": str(e)[:300]})

            results.append(row)
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

            score_val = row.get("score", 0) or 0
            valid = row.get("valid_signal", False)
            blocked = row.get("blocked", True)
            icon = "🚀" if (valid and not blocked) else ("⛔" if blocked else "📊")
            print(f"\n{icon} {sym} {direction}  score={score_val:.1f}  grade={row.get('grade')}  regime={row.get('regime')}")
            if valid and not blocked and score_val >= min_score:
                print(f"   ✅ VALID SIGNAL  timing={row.get('timing')}")
            elif blocked:
                print(f"   ⛔ BLOCKED  reason={str(row.get('reason',''))[:80]}")
            if row.get("error"):
                print(f"   ⚠️  {row['error'][:100]}")

    # 汇总
    valid_signals = [r for r in results if r.get("valid_signal") and not r.get("blocked") and (r.get("score") or 0) >= min_score]
    print(f"\n{'='*60}")
    print(f"  扫描完成: {len(results)} 组合 | {len(valid_signals)} 有效信号")
    if valid_signals:
        print("  有效信号列表:")
        for s in valid_signals:
            print(f"    {s['symbol']} {s['direction']}  score={s.get('score'):.1f}  regime={s.get('regime')}")
    print(f"  输出文件: {out_path}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Safe Signal Runner — signal-only, no trading")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--directions", nargs="+", default=DEFAULT_DIRECTIONS, choices=["LONG","SHORT"])
    parser.add_argument("--min-score", type=float, default=0)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    run_signal_scan(
        symbols=args.symbols,
        directions=args.directions,
        output_file=args.output,
        min_score=args.min_score,
    )

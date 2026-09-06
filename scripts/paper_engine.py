#!/usr/bin/env python3
"""
paper_engine.py — paper funnel on Brahma OS v7 gates.

analyze() -> analyze_to_signal -> evaluate_gates -> one-sided paper order.
Legacy dual-side / 100x path is removed on this branch.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
BRAIN = ROOT / "brahma_brain"
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BRAIN))

from brahma_os.config import load_settings
from brahma_os.paper_bridge import decide_from_analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s [engine] %(message)s")
_log = logging.getLogger(__name__)

SIGNAL_QUEUE = DATA / "signal_queue.jsonl"
PAPER_ORDERS = DATA / "paper_orders.jsonl"
PAPER_ACCOUNT = DATA / "paper_account.json"
ENGINE_LOG = DATA / "paper_engine_log.jsonl"
DEDUP_FILE = DATA / "paper_engine_dedup.json"

QUEUE_TTL_S = 3600 * 2
DEDUP_TTL_S = 3600 * 4
SETTINGS = load_settings(ROOT)


def _push(msg: str) -> None:
    try:
        import subprocess

        subprocess.Popen(
            [
                "openclaw",
                "message",
                "send",
                "--channel",
                "jarvis",
                "--to",
                "73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378",
                "--message",
                msg,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try:
            return json.loads(DEDUP_FILE.read_text())
        except Exception:
            return {}
    return {}


def _is_dedup(symbol: str, side: str) -> bool:
    return (time.time() - _load_dedup().get(f"{symbol}:{side}", 0)) < DEDUP_TTL_S


def _mark_dedup(symbol: str, side: str) -> None:
    d = _load_dedup()
    d[f"{symbol}:{side}"] = time.time()
    DEDUP_FILE.write_text(json.dumps(d))


def _get_nav() -> float:
    try:
        if PAPER_ACCOUNT.exists():
            return float(json.loads(PAPER_ACCOUNT.read_text()).get("nav_current", SETTINGS.start_nav))
    except Exception:
        pass
    return float(SETTINGS.start_nav)


def _count_open() -> int:
    if not PAPER_ORDERS.exists():
        return 0
    n = 0
    for line in PAPER_ORDERS.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            if json.loads(line).get("status") in ("FILLED", "PENDING"):
                n += 1
        except Exception:
            pass
    return n


def _symbol_exposure(symbol: str, nav: float) -> float:
    if nav <= 0 or not PAPER_ORDERS.exists():
        return 0.0
    used = 0.0
    for line in PAPER_ORDERS.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("symbol") != symbol or o.get("status") not in ("FILLED", "PENDING"):
            continue
        used += float(o.get("margin") or 0.0)
    return used / nav


def _gross_exposure(nav: float) -> float:
    if nav <= 0 or not PAPER_ORDERS.exists():
        return 0.0
    used = 0.0
    for line in PAPER_ORDERS.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("status") not in ("FILLED", "PENDING"):
            continue
        used += float(o.get("notional") or 0.0)
    return used / nav


def read_queue() -> list:
    if not SIGNAL_QUEUE.exists():
        return []
    now = time.time()
    seen: set[str] = set()
    fresh = []
    for line in SIGNAL_QUEUE.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            sig = json.loads(line)
        except Exception:
            continue
        sym = sig.get("symbol", "")
        if not sym or now - float(sig.get("ts", 0) or 0) > QUEUE_TTL_S or sym in seen:
            continue
        seen.add(sym)
        fresh.append(sig)
    _log.info("队列读取: %s个待处理信号", len(fresh))
    return fresh


def clear_queue(processed_symbols: set) -> None:
    if not SIGNAL_QUEUE.exists():
        return
    remaining = []
    for line in SIGNAL_QUEUE.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            sig = json.loads(line)
            if sig.get("symbol") not in processed_symbols:
                remaining.append(line)
        except Exception:
            remaining.append(line)
    SIGNAL_QUEUE.write_text("\n".join(remaining) + ("\n" if remaining else ""))


def _run_analyze(symbol: str) -> dict:
    from brahma_core import analyze
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(analyze, symbol)
        return fut.result(timeout=45)


def process_one(symbol: str, source: str = "queue") -> dict:
    result = {"symbol": symbol, "action": "SKIP", "reason": "", "source": source, "orders": [], "os": "v7"}
    nav = _get_nav()
    open_n = _count_open()
    if open_n >= SETTINGS.max_open_positions:
        result["reason"] = f"POS_LIMIT:{open_n}"
        return result

    try:
        raw = _run_analyze(symbol)
    except concurrent_timeout():
        result["reason"] = "analyze_timeout_45s"
        return result
    except Exception as exc:
        result["reason"] = f"analyze_fail: {exc}"
        return result

    if not isinstance(raw, dict):
        result["reason"] = "analyze_not_dict"
        return result
    raw.setdefault("symbol", symbol)
    result.update(
        {
            "score": raw.get("score"),
            "regime": raw.get("regime"),
            "direction": raw.get("direction") or raw.get("signal_dir"),
        }
    )

    decision = decide_from_analyze(
        raw,
        SETTINGS,
        nav=nav,
        open_positions=open_n,
        symbol_exposure=_symbol_exposure(symbol, nav),
        gross_exposure=_gross_exposure(nav),
        now_ts=time.time(),
        symbol=symbol,
    )
    if not decision.allow or decision.signal is None:
        result["reason"] = f"{decision.code}:{decision.reason}"
        return result

    sig = decision.signal
    if _is_dedup(sig.symbol, sig.side):
        result["reason"] = f"DEDUP:{sig.symbol}:{sig.side}"
        return result

    rec = {
        "id": f"PE7-{int(time.time())}-{sig.symbol}-{sig.side}",
        "signal_id": sig.signal_id,
        "symbol": sig.symbol,
        "side": sig.side,
        "entry": round(sig.entry_mid, 6),
        "entry_lo": sig.entry_lo,
        "entry_hi": sig.entry_hi,
        "sl": sig.stop,
        "tp": sig.target,
        "rr": round(sig.rr, 4),
        "qty": round(decision.qty, 6),
        "notional": round(decision.notional, 2),
        "margin": round(decision.margin, 2),
        "lev": decision.leverage,
        "nav": nav,
        "score": sig.score,
        "grade": sig.grade,
        "regime": sig.regime,
        "source": source,
        "gate": decision.code,
        "status": "PENDING",
        "created_at": int(time.time()),
        "filled_at": None,
        "fill_price": None,
        "close_price": None,
        "pnl": None,
    }
    PAPER_ORDERS.parent.mkdir(parents=True, exist_ok=True)
    with open(PAPER_ORDERS, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _mark_dedup(sig.symbol, sig.side)
    result["action"] = "PAPER_OPEN"
    result["orders"] = [rec]
    _log.info(
        "[OPEN-v7] %s %s @%s RR=%.2f score=%.1f grade=%.0f lev=%.1fx",
        sig.symbol,
        sig.side,
        rec["entry"],
        sig.rr,
        sig.score,
        sig.grade,
        decision.leverage,
    )
    return result


def concurrent_timeout():
    import concurrent.futures

    return concurrent.futures.TimeoutError


def settle_orders() -> list:
    import urllib.request

    if not PAPER_ORDERS.exists():
        return []
    lines = PAPER_ORDERS.read_text().strip().split("\n")
    updated = []
    settled = []
    ts = int(time.time())
    for line in lines:
        if not line:
            continue
        try:
            order = json.loads(line)
        except Exception:
            updated.append(line)
            continue
        status = order.get("status", "PENDING")
        try:
            tick = json.loads(
                urllib.request.urlopen(
                    f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={order['symbol']}",
                    timeout=3,
                ).read()
            )
            price = float(tick["price"])
        except Exception:
            updated.append(json.dumps(order))
            continue
        if status == "PENDING":
            entry = float(order["entry"])
            if (order["side"] == "LONG" and price <= entry * 1.005) or (
                order["side"] == "SHORT" and price >= entry * 0.995
            ):
                order["status"] = "FILLED"
                order["filled_at"] = ts
                order["fill_price"] = price
                _log.info("[FILLED] %s %s @$%s", order["symbol"], order["side"], price)
        elif status == "FILLED":
            fill = float(order.get("fill_price", order["entry"]))
            hit_tp = (order["side"] == "LONG" and price >= order["tp"]) or (
                order["side"] == "SHORT" and price <= order["tp"]
            )
            hit_sl = (order["side"] == "LONG" and price <= order["sl"]) or (
                order["side"] == "SHORT" and price >= order["sl"]
            )
            if hit_tp or hit_sl:
                close_px = order["tp"] if hit_tp else order["sl"]
                if order["side"] == "LONG":
                    pnl = (close_px - fill) / fill * order["notional"]
                else:
                    pnl = (fill - close_px) / fill * order["notional"]
                order.update(
                    {
                        "status": "CLOSED",
                        "close_price": close_px,
                        "close_at": ts,
                        "pnl": round(pnl, 2),
                        "close_reason": "TP" if hit_tp else "SL",
                    }
                )
                settled.append(order)
                _log.info("[CLOSED] %s %s PnL=$%.2f %s", order["symbol"], order["side"], pnl, order["close_reason"])
        updated.append(json.dumps(order))
    PAPER_ORDERS.write_text("\n".join(updated) + "\n")
    if settled:
        total_pnl = sum(o["pnl"] for o in settled)
        try:
            acc = json.loads(PAPER_ACCOUNT.read_text()) if PAPER_ACCOUNT.exists() else {}
            acc["nav_current"] = acc.get("nav_current", SETTINGS.start_nav) + total_pnl
            acc["realized_pnl"] = acc.get("realized_pnl", 0) + total_pnl
            acc["updated_at"] = ts
            PAPER_ACCOUNT.write_text(json.dumps(acc, indent=2))
        except Exception:
            pass
    return settled


def main() -> None:
    t0 = time.time()
    _log.info("=== paper_engine v7 env=%s lev<=%s ===", SETTINGS.env, SETTINGS.max_leverage)
    signals = read_queue()
    results = []
    processed: set[str] = set()
    opened_total = 0
    opened_list = []
    for item in signals:
        sym = item.get("symbol", "")
        if not sym:
            continue
        try:
            out = process_one(sym, item.get("source", "queue"))
            results.append(out)
            processed.add(sym)
            if out["action"] == "PAPER_OPEN":
                opened_total += len(out["orders"])
                for order in out["orders"]:
                    opened_list.append(f"{order['symbol']} {order['side']} @{order['entry']} RR={order['rr']}")
            else:
                _log.info("SKIP %s: %s", sym, out["reason"])
        except Exception as exc:
            _log.error("process_one %s: %s", sym, exc)
    settled = settle_orders()
    clear_queue(processed)
    elapsed = round(time.time() - t0, 1)
    ENGINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ENGINE_LOG, "a") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": int(time.time()),
                    "signals_in": len(signals),
                    "opened": opened_total,
                    "settled": len(settled),
                    "elapsed_s": elapsed,
                    "os": "v7",
                }
            )
            + "\n"
        )
    if opened_total or settled:
        lines = [f"paper_engine v7 | {datetime.now(timezone.utc).strftime('%H:%M UTC')}"]
        lines.extend(opened_list)
        for order in settled:
            lines.append(f"{order['symbol']} {order['side']} {order['close_reason']} PnL=${order['pnl']:+,.2f}")
        _push("\n".join(lines))
    print(f"完成: 信号={len(signals)} 开单={opened_total} 结算={len(settled)} 耗时={elapsed}s env={SETTINGS.env}")


if __name__ == "__main__":
    main()

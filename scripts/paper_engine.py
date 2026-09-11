#!/usr/bin/env python3
"""
paper_engine.py — paper funnel on Brahma OS v7 gates.

analyze() -> snapshot -> evaluate_gates -> one-sided paper order.
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
from brahma_os.snapshot import append_snapshot, signal_record
from brahma_os.settlement import SettlementEngine, SettlementRule
from brahma_os.ledger import EquityLedger
from brahma_os.costs import CostModel
from brahma_os.contracts import Fill

logging.basicConfig(level=logging.INFO, format="%(asctime)s [engine] %(message)s")
_log = logging.getLogger(__name__)

SIGNAL_QUEUE = DATA / "signal_queue.jsonl"
PAPER_ORDERS = DATA / "paper_orders.jsonl"
PAPER_ACCOUNT = DATA / "paper_account.json"
ENGINE_LOG = DATA / "paper_engine_log.jsonl"
DEDUP_FILE = DATA / "paper_engine_dedup.json"

QUEUE_TTL_S = 3600 * 2
DEDUP_TTL_S = 300  # 2026-09-09 苏摩111修复：4h→5min，防止同价位重复开单
SETTINGS = load_settings(ROOT)

# [2026-09-07 达摩院封印] 单一账本 + bar回放结算层
COST_MODEL = CostModel(SETTINGS)
LEDGER = EquityLedger(settings=SETTINGS, cost=COST_MODEL)
SETTLEMENT = SettlementEngine(SettlementRule(
    same_bar_priority=SETTINGS.same_bar_priority,
    ttl_hours=SETTINGS.signal_ttl_hours,
))
LEDGER_FILE = DATA / "paper_ledger.json"

# 仓位规则（v7保留，LEV已由P0停血封死5x）
SIZE_MAJOR = 0.05   # BTC/ETH
SIZE_ALT   = 0.03   # 其他
LEV_MAJOR  = 5   # [2026-09-07 P0停血] 100x→5x
LEV_ALT    = 5   # [2026-09-07 P0停血] 20x→5x
MAJOR_SYMS = {'BTCUSDT', 'ETHUSDT'}

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
                "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6",
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


def _is_dedup(symbol: str, side: str, entry: float = 0) -> bool:
    key = f"{symbol}:{side}:{round(entry, 4)}" if entry else f"{symbol}:{side}"
    return (time.time() - _load_dedup().get(key, 0)) < DEDUP_TTL_S


def _mark_dedup(symbol: str, side: str, entry: float = 0) -> None:
    d = _load_dedup()
    key = f"{symbol}:{side}:{round(entry, 4)}" if entry else f"{symbol}:{side}"
    d[key] = time.time()
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


def _snapshot_decision(symbol: str, source: str, raw: dict, decision) -> None:
    try:
        if decision.signal is not None:
            append_snapshot(
                SETTINGS,
                signal_record(
                    decision.signal,
                    gate=decision.code,
                    allow=decision.allow,
                    source=source,
                    reason=decision.reason,
                ),
            )
        else:
            append_snapshot(
                SETTINGS,
                {
                    "ts": time.time(),
                    "symbol": symbol,
                    "gate": decision.code,
                    "allow": False,
                    "reason": decision.reason,
                    "source": source,
                    "score": raw.get("score"),
                    "regime": raw.get("regime"),
                    "direction": raw.get("direction") or raw.get("signal_dir"),
                },
            )
    except Exception as exc:
        _log.warning("snapshot_fail %s: %s", symbol, exc)


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
    _snapshot_decision(symbol, source, raw, decision)
    if not decision.allow or decision.signal is None:
        result["reason"] = f"{decision.code}:{decision.reason}"
        return result

    sig = decision.signal
    if _is_dedup(sig.symbol, sig.side, sig.entry_mid):
        result["reason"] = f"DEDUP:{sig.symbol}:{sig.side}:{sig.entry_mid}"
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
    _mark_dedup(sig.symbol, sig.side, sig.entry_mid)
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


def _fetch_klines(symbol: str, since_ts: float, limit: int = 100) -> list:
    """拉取1H K线用于bar回放结算。返回 [(ts, high, low, close), ...]"""
    import urllib.request
    try:
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={symbol}&interval=1h&limit={limit}"
            f"&startTime={int(since_ts * 1000)}"
        )
        data = json.loads(urllib.request.urlopen(url, timeout=6).read())
        return [(float(k[0]) / 1000, float(k[2]), float(k[3]), float(k[4])) for k in data]
    except Exception:
        return []


def _get_ledger_nav() -> float:
    """从EquityLedger读NAV，回退到PAPER_ACCOUNT"""
    nav = LEDGER.nav()
    if nav != SETTINGS.start_nav or not PAPER_ACCOUNT.exists():
        return nav
    try:
        return float(json.loads(PAPER_ACCOUNT.read_text()).get("nav_current", SETTINGS.start_nav))
    except Exception:
        return SETTINGS.start_nav


def settle_orders() -> list:
    """[2026-09-07 达摩院封印] bar回放结算，接入SettlementEngine+EquityLedger
    替代最新价触价（旧逻辑会改写历史、不计费用）
    """
    if not PAPER_ORDERS.exists():
        return []
    lines = PAPER_ORDERS.read_text().strip().split("\n")
    updated = []
    settled = []
    now_ts = time.time()

    for line in lines:
        if not line:
            continue
        try:
            order = json.loads(line)
        except Exception:
            updated.append(line)
            continue

        status = order.get("status", "PENDING")

        # ── PENDING: 用当前价尝试成交 ──────────────────────────
        if status == "PENDING":
            import urllib.request
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
            entry = float(order["entry"])
            if (order["side"] == "LONG" and price <= entry * 1.005) or (
                order["side"] == "SHORT" and price >= entry * 0.995
            ):
                order["status"] = "FILLED"
                order["filled_at"] = int(now_ts)
                order["fill_price"] = price
                # 接入EquityLedger记录开仓费用
                try:
                    notional = float(order.get("notional", 0))
                    qty = float(order.get("qty", notional / price if price > 0 else 0))
                    fee = notional * SETTINGS.taker_fee_bps / 10000
                    slip = notional * SETTINGS.slippage_bps / 10000
                    fill_obj = Fill(
                        fill_id=order.get("id", f"fill-{int(now_ts)}"),
                        signal_id=order.get("signal_id", order.get("id", "")),
                        ts=now_ts, symbol=order["symbol"],
                        side=order["side"], qty=qty, price=price,
                        fee=fee, slippage=slip, role="ENTRY",
                    )
                    LEDGER.apply_entry(fill_obj, float(order["sl"]), float(order["tp"]))
                    order["ledger_pos_id"] = fill_obj.fill_id
                except Exception as _le:
                    _log.debug("ledger entry skipped: %s", _le)
                _log.info("[FILLED] %s %s @$%s", order["symbol"], order["side"], price)

        # ── FILLED: 用bar回放结算，不用最新价 ──────────────────
        elif status == "FILLED":
            filled_ts = float(order.get("filled_at", now_ts - 3600))
            bars = _fetch_klines(order["symbol"], filled_ts)
            result = SETTLEMENT.settle(
                side=order["side"],
                entry_ts=filled_ts,
                entry=float(order.get("fill_price", order["entry"])),
                stop=float(order["sl"]),
                target=float(order["tp"]),
                bars=bars,
            )
            if result is not None:
                fill_px = result.exit_price
                notional = float(order.get("notional", 0))
                fill_price = float(order.get("fill_price", order["entry"]))
                qty = float(order.get("qty", notional / fill_price if fill_price > 0 else 0))
                # 费用后PnL via CostModel
                fee_close = notional * SETTINGS.taker_fee_bps / 10000
                slip_close = notional * SETTINGS.slippage_bps / 10000
                hours_held = result.bars_held
                funding = COST_MODEL.funding(notional, hours_held)
                fees_total = (notional * SETTINGS.taker_fee_bps / 10000  # open
                              + fee_close + slip_close + funding)
                if order["side"] == "LONG":
                    gross = (fill_px - fill_price) / fill_price * notional
                else:
                    gross = (fill_price - fill_px) / fill_price * notional
                pnl = round(gross - fees_total, 2)

                order.update({
                    "status": "CLOSED",
                    "close_price": fill_px,
                    "close_at": int(result.exit_ts),
                    "pnl": pnl,
                    "close_reason": result.outcome,
                    "bars_held": result.bars_held,
                    "settlement_note": result.note,
                })
                settled.append(order)

                # EquityLedger记录平仓
                try:
                    pos_id = order.get("ledger_pos_id")
                    if pos_id and pos_id in LEDGER.positions:
                        exit_fill = Fill(
                            fill_id=f"exit-{int(result.exit_ts)}",
                            signal_id=order.get("signal_id", ""),
                            ts=result.exit_ts, symbol=order["symbol"],
                            side=order["side"], qty=qty, price=fill_px,
                            fee=fee_close, slippage=slip_close, role="STOP" if result.outcome == "LOSS" else "TARGET",
                        )
                        LEDGER.apply_exit(pos_id, exit_fill, hours_held, result.outcome)
                        _save_ledger_snapshot()
                except Exception as _le:
                    _log.debug("ledger exit skipped: %s", _le)

                _log.info("[CLOSED] %s %s PnL=$%.2f %s bars=%d",
                          order["symbol"], order["side"], pnl, result.outcome, result.bars_held)

        updated.append(json.dumps(order))

    PAPER_ORDERS.write_text("\n".join(updated) + "\n")

    if settled:
        # 同步NAV到PAPER_ACCOUNT（兼容旧读取方）
        total_pnl = sum(o["pnl"] for o in settled)
        try:
            acc = json.loads(PAPER_ACCOUNT.read_text()) if PAPER_ACCOUNT.exists() else {}
            acc["nav_current"] = _get_ledger_nav()
            acc["realized_pnl"] = acc.get("realized_pnl", 0) + total_pnl
            acc["updated_at"] = int(now_ts)
            PAPER_ACCOUNT.write_text(json.dumps(acc, indent=2))
        except Exception:
            pass

    return settled


def _save_ledger_snapshot() -> None:
    """把EquityLedger.snapshot()持久化到paper_ledger.json（唯一真账本）"""
    try:
        snap = LEDGER.snapshot()
        data = {
            "ts": time.time(),
            "nav": snap.nav,
            "peak": snap.peak,
            "max_drawdown": round(snap.max_drawdown, 6),
            "n_closed": snap.n_closed,
            "wins": snap.wins,
            "losses": snap.losses,
            "timeouts": snap.timeouts,
            "wr": round(snap.wr, 4),
            "ev_usd": round(snap.ev_usd, 4),
            "sharpe": snap.sharpe,
            "days": snap.days,
        }
        LEDGER_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        _log.debug("ledger snapshot save failed: %s", e)


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

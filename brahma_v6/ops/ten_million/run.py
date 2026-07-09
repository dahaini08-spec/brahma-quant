"""
brahma_v6/ops/ten_million/run.py
CLI entry point for 10M simulation
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BASE))

from brahma_v6.ops.ten_million.driver import SimDriver, FaultRates
from brahma_v6.ops.ten_million.invariant_checker import InvariantChecker
from brahma_v6.ops.ten_million.optimizer import PolicyOptimizer
from brahma_v6.ops.ten_million.report import SimReport


def main() -> int:
    parser = argparse.ArgumentParser(description="Brahma v6 10M Event Simulation")
    parser.add_argument("--events",             type=int,   default=10_000_000)
    parser.add_argument("--seed",               type=int,   default=42)
    parser.add_argument("--symbols",            type=str,   default="ETHUSDT")
    parser.add_argument("--leverage",           type=int,   default=5)
    parser.add_argument("--max-trades-per-day", type=int,   default=20)
    parser.add_argument("--daily-loss-pct",     type=float, default=0.10)
    parser.add_argument("--order-types",        type=str,   default="LIMIT,LIMIT_MAKER")
    parser.add_argument("--unknown-rate",       type=float, default=0.001)
    parser.add_argument("--reject-rate",        type=float, default=0.005)
    parser.add_argument("--partial-fill-rate",  type=float, default=0.08)
    parser.add_argument("--cancel-race-rate",   type=float, default=0.002)
    parser.add_argument("--ledger-failure-rate",type=float, default=0.0001)
    parser.add_argument("--adapter-mismatch-rate", type=float, default=0.00005)
    parser.add_argument("--websocket-gap-rate", type=float, default=0.0001)
    parser.add_argument("--kill-switch-rate",   type=float, default=0.00001)
    parser.add_argument("--policy-search",      type=str,   default="false")
    parser.add_argument("--output",             type=str,   default="reports/ten_million_event_report.md")
    parser.add_argument("--policy-output",      type=str,   default="reports/final_policy_search_report.md")
    args = parser.parse_args()

    print(f"🚀 Brahma v6 10M Simulation  events={args.events:,}  seed={args.seed}")

    fault_rates = FaultRates(
        unknown_rate        = args.unknown_rate,
        reject_rate         = args.reject_rate,
        partial_fill_rate   = args.partial_fill_rate,
        cancel_race_rate    = args.cancel_race_rate,
        ledger_failure_rate = args.ledger_failure_rate,
        adapter_mismatch_rate = args.adapter_mismatch_rate,
        websocket_gap_rate  = args.websocket_gap_rate,
        kill_switch_rate    = args.kill_switch_rate,
    )

    driver  = SimDriver(fault_rates=fault_rates, seed=args.seed)
    checker = InvariantChecker()

    print("  [1/3] Running simulation...", end="", flush=True)
    t0 = time.perf_counter()
    events = list(driver.generate(args.events))
    for ev in events:
        checker.check(ev)
    elapsed = time.perf_counter() - t0
    print(f" done ({elapsed:.2f}s, {args.events/elapsed:,.0f} ev/s)")

    # Policy search
    best_policy = None
    if args.policy_search.lower() == "true":
        print("  [2/3] Policy search...", end="", flush=True)
        optimizer = PolicyOptimizer()
        best_policy = optimizer.search(events[:min(len(events), 100_000)])
        print(f" done  best_score={best_policy.score:.3f}")
    else:
        print("  [2/3] Policy search skipped")

    # Report
    print("  [3/3] Generating reports...", end="", flush=True)
    report = SimReport(
        total_events   = args.events,
        elapsed_s      = elapsed,
        checker        = checker,
        best_policy    = best_policy,
        fault_rates    = fault_rates,
    )
    output_path = BASE / args.output
    policy_path = BASE / args.policy_output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report.write_event_report(output_path)
    if best_policy:
        report.write_policy_report(policy_path)
    print(" done")

    # Final verdict
    all_pass = checker.all_pass()
    print()
    report.print_summary()
    print()
    if all_pass:
        print("✅ SIMULATION PASS — ready for Production Gate")
        return 0
    else:
        print("❌ SIMULATION FAIL — invariant violations detected")
        return 1


if __name__ == "__main__":
    sys.exit(main())

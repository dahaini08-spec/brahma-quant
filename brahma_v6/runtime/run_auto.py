"""
brahma_v6/runtime/run_auto.py — Main entry point for FULL_AUTO_LIVE_LITE
Phase 5 | 2026-07-09

Reads env vars, initializes all components, runs the main loop.

Required env vars:
  BINANCE_API_KEY        — Binance API key
  BINANCE_API_SECRET     — Binance API secret
  BRAHMA_ALLOW_LIVE_ORDER — must be set to "1" for real orders (default: test mode)
"""
from __future__ import annotations
import os
import sys
import time
import signal
import logging
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("brahma.run_auto")


def main():
    from brahma_v6.adapters.binance_client import BinanceClient
    from brahma_v6.adapters.binance_filters import SymbolFilters
    from brahma_v6.adapters.live_binance_adapter import LiveBinanceAdapter, ModePolicyLive
    from brahma_v6.risk.kill_switch import KillSwitch
    from brahma_v6.risk.risk_kernel import RiskKernel, AccountState
    from brahma_v6.runtime.signal_consumer import SignalConsumer
    from brahma_v6.runtime.order_intent_factory import OrderIntentFactory
    from brahma_v6.runtime.order_pipeline import OrderPipeline
    from brahma_v6.ops.dlq import DeadLetterQueue

    # Load env vars
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    allow_live = os.environ.get("BRAHMA_ALLOW_LIVE_ORDER", "0") == "1"

    if not api_key or not api_secret:
        logger.error("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
        sys.exit(1)

    test_order = not allow_live
    logger.info(f"Starting FULL_AUTO_LIVE_LITE | test_order={test_order} | live_mode={allow_live}")

    # Components
    kill_switch = KillSwitch()
    dlq = DeadLetterQueue()

    client = BinanceClient(api_key=api_key, api_secret=api_secret, testnet=False)

    def filter_provider(symbol: str) -> SymbolFilters:
        return SymbolFilters.ethusdt_default()

    adapter = LiveBinanceAdapter(
        client=client,
        filter_provider=filter_provider,
        mode_policy=ModePolicyLive(),
        kill_switch=kill_switch,
        test_order=test_order,
    )

    risk_kernel = RiskKernel(
        kill_switch=kill_switch,
        symbol_allowlist=["ETHUSDT"],
        max_open_positions=1,
        max_open_orders=1,
        max_trades_per_day=20,
        max_daily_loss_pct=0.10,
        min_score=0.5,
        min_notional=5.0,
    )

    signal_consumer = SignalConsumer(
        min_score=0.5,
        symbol_allowlist=["ETHUSDT"],
    )

    intent_factory = OrderIntentFactory()

    pipeline = OrderPipeline(
        signal_consumer=signal_consumer,
        risk_kernel=risk_kernel,
        intent_factory=intent_factory,
        adapter=adapter,
        dlq=dlq,
    )

    # Graceful shutdown
    running = [True]

    def handle_signal(sig, frame):
        logger.info("Shutdown signal received")
        running[0] = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("FULL_AUTO_LIVE_LITE main loop started")

    # Main loop — reads from external signal source
    while running[0]:
        if kill_switch.is_active():
            logger.warning(f"Kill switch ACTIVE: {kill_switch.reason} — halting")
            break
        time.sleep(1)

    logger.info("FULL_AUTO_LIVE_LITE shutdown complete")
    return pipeline


if __name__ == "__main__":
    main()

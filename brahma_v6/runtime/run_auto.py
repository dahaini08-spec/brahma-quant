"""
brahma_v6/runtime/run_auto.py — FULL_AUTO_LIVE_LITE Main Entry Point
Phase 5+ | 2026-07-09 (接入梵天 live_signal_log)

Required env vars:
  BINANCE_API_KEY        — Binance API key
  BINANCE_API_SECRET     — Binance API secret
  BRAHMA_ALLOW_LIVE_ORDER — set to "1" for real orders (default: test mode)

Optional env vars:
  BRAHMA_SYMBOL_ALLOWLIST — comma-separated symbols (default: ETHUSDT,BTCUSDT)
  BRAHMA_MIN_SCORE_RAW    — minimum raw score threshold (default: 155)
  BRAHMA_MAX_POSITIONS    — max open positions (default: 1)
  BRAHMA_MAX_DAILY_LOSS   — max daily loss pct (default: 0.10)
  BRAHMA_POLL_INTERVAL    — signal poll interval seconds (default: 5)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import hmac
import hashlib
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE / "data" / "run_auto.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("brahma.run_auto")


# ─────────────────────────────────────────────
#  实时账户状态提供器
# ─────────────────────────────────────────────

class BinanceAccountStateProvider:
    """
    实时从 Binance fapi 拉取账户余额和持仓，构造 AccountState。
    带 TTL 缓存避免过频请求。
    """
    CACHE_TTL = 10.0   # seconds

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._key = api_key
        self._secret = api_secret
        self._cache: dict = {}
        self._cache_ts: float = 0.0

    def _signed_get(self, base_url: str, path: str, params: str = "") -> dict | list:
        ts = int(time.time() * 1000)
        query = f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
        sig = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{base_url}{path}?{query}&signature={sig}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": self._key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _fetch(self) -> dict:
        try:
            balances = self._signed_get("https://fapi.binance.com", "/fapi/v2/balance")
            usdt = next((b for b in balances if b["asset"] == "USDT"), {})
            nav = float(usdt.get("balance", 0))
            avail = float(usdt.get("availableBalance", 0))
            unpnl = float(usdt.get("crossUnPnl", 0))

            positions = self._signed_get("https://fapi.binance.com", "/fapi/v2/positionRisk")
            open_pos = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)

            return {
                "nav": nav,
                "daily_pnl": unpnl,
                "open_positions": open_pos,
                "open_orders": 0,   # 可按需拉取
                "trades_today": 0,
            }
        except Exception as e:
            logger.warning(f"[AccountState] 拉取失败: {e}")
            return self._cache or {"nav": 0.0, "daily_pnl": 0.0, "open_positions": 0, "open_orders": 0, "trades_today": 0}

    def get(self):
        from brahma_v6.risk.risk_kernel import AccountState
        now = time.time()
        if now - self._cache_ts > self.CACHE_TTL:
            self._cache = self._fetch()
            self._cache_ts = now
        d = self._cache
        return AccountState(
            nav=d.get("nav", 0.0),
            daily_pnl=d.get("daily_pnl", 0.0),
            open_positions=d.get("open_positions", 0),
            open_orders=d.get("open_orders", 0),
            trades_today=d.get("trades_today", 0),
        )


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    # ── 读取环境变量 ──
    api_key    = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    allow_live = os.environ.get("BRAHMA_ALLOW_LIVE_ORDER", "0") == "1"

    symbols_raw  = os.environ.get("BRAHMA_SYMBOL_ALLOWLIST", "ETHUSDT,BTCUSDT")
    symbol_allowlist = [s.strip() for s in symbols_raw.split(",") if s.strip()]
    min_score_raw    = float(os.environ.get("BRAHMA_MIN_SCORE_RAW", "155"))
    max_positions    = int(os.environ.get("BRAHMA_MAX_POSITIONS", "1"))
    max_daily_loss   = float(os.environ.get("BRAHMA_MAX_DAILY_LOSS", "0.10"))
    poll_interval    = float(os.environ.get("BRAHMA_POLL_INTERVAL", "5"))

    if not api_key or not api_secret:
        logger.error("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
        sys.exit(1)

    test_order = not allow_live
    mode_str   = "🟢 LIVE" if allow_live else "🧪 TEST (沙盒, 无真实成交)"

    logger.info("=" * 60)
    logger.info(f"梵天 FULL_AUTO_LIVE_LITE 启动")
    logger.info(f"模式:    {mode_str}")
    logger.info(f"标的:    {symbol_allowlist}")
    logger.info(f"最低分:  {min_score_raw}")
    logger.info(f"最大持仓:{max_positions}")
    logger.info(f"最大日亏:{max_daily_loss*100:.0f}%")
    logger.info(f"轮询间隔:{poll_interval}s")
    logger.info("=" * 60)

    # ── 初始化组件 ──
    from brahma_v6.adapters.binance_client import BinanceClient
    from brahma_v6.adapters.binance_filters import SymbolFilters
    from brahma_v6.adapters.live_binance_adapter import LiveBinanceAdapter, ModePolicyLive
    from brahma_v6.risk.kill_switch import KillSwitch
    from brahma_v6.risk.risk_kernel import RiskKernel
    from brahma_v6.runtime.signal_consumer import SignalConsumer
    from brahma_v6.runtime.order_intent_factory import OrderIntentFactory
    from brahma_v6.runtime.order_pipeline import OrderPipeline
    from brahma_v6.runtime.live_signal_reader import LiveSignalReader
    from brahma_v6.ops.dlq import DeadLetterQueue

    kill_switch = KillSwitch()
    dlq         = DeadLetterQueue()
    client      = BinanceClient(api_key=api_key, api_secret=api_secret, testnet=False)
    acc_provider = BinanceAccountStateProvider(api_key=api_key, api_secret=api_secret)

    def filter_provider(symbol: str) -> SymbolFilters:
        # 可扩展到其他 symbol
        if symbol == "ETHUSDT":
            return SymbolFilters.ethusdt_default()
        return SymbolFilters.ethusdt_default()   # fallback

    adapter = LiveBinanceAdapter(
        client=client,
        filter_provider=filter_provider,
        mode_policy=ModePolicyLive(),
        kill_switch=kill_switch,
        test_order=test_order,
    )

    risk_kernel = RiskKernel(
        kill_switch=kill_switch,
        symbol_allowlist=symbol_allowlist,
        max_open_positions=max_positions,
        max_open_orders=max_positions + 2,
        max_trades_per_day=20,
        max_daily_loss_pct=max_daily_loss,
        min_score=min_score_raw / 175.0,   # 归一化
        min_notional=5.0,
    )

    signal_consumer = SignalConsumer(
        min_score=min_score_raw / 175.0,
        symbol_allowlist=symbol_allowlist,
    )

    intent_factory = OrderIntentFactory()

    pipeline = OrderPipeline(
        signal_consumer=signal_consumer,
        risk_kernel=risk_kernel,
        intent_factory=intent_factory,
        adapter=adapter,
        account_state_provider=acc_provider.get,
        dlq=dlq,
    )

    reader = LiveSignalReader(
        poll_interval=poll_interval,
        min_score_raw=min_score_raw,
    )

    logger.info(f"[LiveSignalReader] 监听: {reader.log_path}")
    logger.info("[run_auto] 所有组件初始化完成，进入主循环")

    # ── Graceful shutdown ──
    running = [True]

    def handle_signal(sig, frame):
        logger.info(f"收到关闭信号 {sig}，准备退出...")
        running[0] = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT,  handle_signal)

    # ── 主循环 ──
    loop_count  = 0
    total_emits = 0
    total_submitted = 0

    while running[0]:
        if kill_switch.is_active():
            logger.warning(f"[Kill Switch] ACTIVE: {kill_switch.reason} — 停止交易")
            break

        # poll 新信号
        new_signals = reader.poll_once()

        for raw_sig in new_signals:
            total_emits += 1
            # ── 注入 quantity：基于 NAV 和梵天仓位计算器 ──
            if raw_sig.quantity <= 0 and raw_sig.price > 0:
                try:
                    from brahma_brain.position_sizer import get_position_pct
                    acc_snap = acc_provider.get()
                    nav_now = acc_snap.nav or 100.0
                    score_raw = raw_sig.score * 175.0
                    direction = "LONG" if raw_sig.side == "BUY" else "SHORT"
                    size_info = get_position_pct(
                        symbol=raw_sig.symbol,
                        score=score_raw,
                        direction=direction,
                        nav=nav_now,
                    )
                    usdt_size = size_info.get("usdt", nav_now * 0.01)  # fallback 1%
                    # 确保名义值 >= Binance 最小 $20 (加少量余量)
                    MIN_NOTIONAL_USDT = 22.0  # Binance 要求 $20，留 10% 余量
                    leverage = 5
                    # usdt_size 是保证金, 名义值 = usdt_size * leverage
                    notional = usdt_size * leverage
                    if notional < MIN_NOTIONAL_USDT:
                        usdt_size = MIN_NOTIONAL_USDT / leverage
                        logger.info(f"[SizeCalc] 名义值不足，提升至 ${MIN_NOTIONAL_USDT} 名义值")
                    qty_raw = (usdt_size * leverage) / raw_sig.price
                    # 最小精度 0.001 (ETH)
                    qty = round(max(qty_raw, 0.001), 3)
                    raw_sig.quantity = qty
                    logger.info(
                        f"[SizeCalc] {raw_sig.symbol} nav={nav_now:.2f} "
                        f"size_pct={size_info.get('pct','?')}% usdt={usdt_size:.2f} "
                        f"lev={leverage}x qty={qty}"
                    )
                except Exception as e:
                    # 兜底：1% NAV × 5x
                    acc_snap = acc_provider.get()
                    fallback_qty = round((acc_snap.nav * 0.01 * 5) / raw_sig.price, 3)
                    raw_sig.quantity = max(fallback_qty, 0.001)
                    logger.warning(f"[SizeCalc] 兜底 qty={raw_sig.quantity}: {e}")

            logger.info(
                f"[Pipeline] 处理信号 #{total_emits}: "
                f"{raw_sig.symbol} {raw_sig.side} score_raw={raw_sig.score*175:.1f} "
                f"regime={raw_sig.regime} price={raw_sig.price} qty={raw_sig.quantity}"
            )
            result = pipeline.process(raw_sig)
            logger.info(
                f"[Pipeline] 结果: stage={result.stage} "
                f"risk={result.risk_action} reason={result.risk_reason}"
            )
            if result.success():
                total_submitted += 1
                ev = result.adapter_event
                logger.info(
                    f"[ORDER] ✅ {'TEST_OK' if test_order else 'SUBMITTED'} "
                    f"{ev.side} {ev.quantity} {ev.symbol} @ {ev.price} "
                    f"status={ev.status}"
                )
            elif result.stage == "BLOCKED":
                logger.info(f"[Pipeline] ⛔ BLOCKED: {result.risk_reason}")
            elif result.stage == "FILTERED":
                logger.info(f"[Pipeline] 🔘 FILTERED (consumer gate)")
            elif result.stage == "ERROR":
                logger.error(f"[Pipeline] ❌ ERROR: {result.error}")

        # 每 60s 打印一次状态
        loop_count += 1
        if loop_count % int(60 / max(poll_interval, 1)) == 0:
            acc = acc_provider.get()
            reader_stats = reader.stats
            logger.info(
                f"[Status] NAV=${acc.nav:.2f} pos={acc.open_positions} | "
                f"信号: read={reader_stats['read']} emit={reader_stats['emitted']} skip={reader_stats['skipped']} | "
                f"下单: {total_submitted}"
            )

        time.sleep(poll_interval)

    logger.info("[run_auto] 退出完成")
    logger.info(f"[run_auto] 统计: 信号处理={total_emits} 下单={total_submitted} DLQ={dlq.size() if hasattr(dlq,'size') else '?'}")
    return pipeline


if __name__ == "__main__":
    main()

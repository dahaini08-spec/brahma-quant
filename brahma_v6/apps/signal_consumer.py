"""
brahma_v6/apps/signal_consumer.py
全自动信号消费管道 v1.0

设计院 2026-07-10 自主补全（v6.5断点修复）

架构：
  signal_bus.jsonl（梵天主系统/暴涨猎手写入）
         ↓
  SignalConsumer.run_once() — 读取pending有效信号
         ↓
  RiskKernel.evaluate() — 风控门控
         ↓
  PositionSizer.calc() — 仓位计算
         ↓
  ExecutionAdapter.submit() — Backtest/Paper/Live统一执行
         ↓
  TradeLedger.append() — 硬证据链落盘
         ↓
  EVBucketRegistry.update() — EV桶更新

运行方式：
  python -m brahma_v6.apps.signal_consumer --mode paper
  python -m brahma_v6.apps.signal_consumer --mode live --testnet
  python -m brahma_v6.apps.signal_consumer --mode backtest --dry-run
"""

import sys, os, json, time, argparse, logging
from pathlib import Path
from typing import Optional, Dict, Any

# ─── 路径设置 ────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

logger = logging.getLogger(__name__)

# ─── 导入核心模块 ─────────────────────────────────────────────────────────────
try:
    from brahma_v6.risk.risk_kernel import RiskKernel, AccountState, Signal as RiskSignal
    from brahma_v6.risk.kill_switch import KillSwitch
    _RISK_OK = True
except Exception as e:
    logger.warning(f'RiskKernel导入失败: {e}')
    _RISK_OK = False

try:
    from brahma_v6.portfolio.position_sizer import PositionSizer
    _SIZER_OK = True
except Exception as e:
    logger.warning(f'PositionSizer导入失败: {e}')
    _SIZER_OK = False

try:
    from brahma_v6.adapters.execution_adapter import PaperAdapter, BacktestAdapter
    from brahma_v6.adapters.live_binance_adapter import LiveBinanceAdapter
    _ADAPTER_OK = True
except Exception as e:
    logger.warning(f'ExecutionAdapter导入失败: {e}')
    _ADAPTER_OK = False

try:
    from brahma_v6.dharma2.trade_ledger import TradeLedger
    from brahma_v6.dharma2.models import TradeRecord
    _LEDGER_OK = True
except Exception as e:
    logger.warning(f'TradeLedger导入失败: {e}')
    _LEDGER_OK = False

try:
    from brahma_v6.dharma2.ev_bucket import EVBucketRegistry
    _EV_OK = True
except Exception as e:
    logger.warning(f'EVBucketRegistry导入失败: {e}')
    _EV_OK = False

# ─── 常量 ────────────────────────────────────────────────────────────────────
SIGNAL_BUS_PATH = _BASE / 'data' / 'signal_bus.jsonl'
SCORE_THRESHOLD = 155        # 最低有效分数
POLL_INTERVAL   = 30         # 轮询间隔（秒）
MAX_DAILY_LOSS  = 0.10       # 最大日亏损10%NAV
MAX_POSITION_PCT = 0.10      # 单标的最大10%NAV


class SignalConsumer:
    """
    全自动信号消费管道

    模式：
      backtest — 历史数据回测（BacktestAdapter）
      paper    — 实时数据模拟成交（PaperAdapter）
      live     — 真实执行（LiveBinanceAdapter）
    """

    def __init__(self,
                 mode: str = 'paper',
                 nav: float = 127.37,
                 testnet: bool = True,
                 dry_run: bool = False):
        self.mode = mode
        self.nav = nav
        self.dry_run = dry_run
        self._running = False

        # 初始化各模块
        self._init_modules(mode, nav, testnet)
        logger.info(f'[SignalConsumer] mode={mode} nav={nav:.2f} dry_run={dry_run}')

    def _init_modules(self, mode: str, nav: float, testnet: bool):
        """初始化风控/仓位/适配器/账本/EV桶"""

        # 风控
        if _RISK_OK:
            self._kill_switch = KillSwitch()
            self._risk = RiskKernel(
                kill_switch=self._kill_switch,
                max_daily_loss_pct=MAX_DAILY_LOSS,
            )
        else:
            self._risk = None

        # 仓位计算
        self._ev_registry = EVBucketRegistry() if _EV_OK else None
        self._sizer = PositionSizer(nav=nav, ev_registry=self._ev_registry) if _SIZER_OK else None

        # 执行适配器
        if _ADAPTER_OK:
            if mode == 'live':
                api_key = os.environ.get('BINANCE_API_KEY', '')
                api_secret = os.environ.get('BINANCE_API_SECRET', '')
                self._adapter = LiveBinanceAdapter(
                    api_key=api_key,
                    api_secret=api_secret,
                    test_order=testnet,
                )
            elif mode == 'paper':
                self._adapter = PaperAdapter()
            else:
                self._adapter = BacktestAdapter()
        else:
            self._adapter = None

        # 账本
        self._ledger = TradeLedger(
            storage_path=_BASE / 'data' / 'trade_ledger_v6.jsonl'
        ) if _LEDGER_OK else None

    # ─── 核心：处理单条信号 ──────────────────────────────────────────────────

    def process_signal(self, sig: Dict[str, Any]) -> dict:
        """
        处理单条信号

        Returns:
            {'action': 'submitted'|'skipped'|'blocked', 'reason': str}
        """
        symbol    = sig.get('symbol', '')
        score     = float(sig.get('score', 0))
        direction = sig.get('direction', 'LONG')
        regime    = sig.get('regime', 'UNKNOWN')
        entry_lo  = float(sig.get('entry_lo', 0))
        entry_hi  = float(sig.get('entry_hi', 0))
        sl        = float(sig.get('sl', 0))
        tp1       = float(sig.get('tp1', 0))
        signal_id = sig.get('signal_id', '')

        logger.info(f'[处理信号] {symbol} {direction} score={score:.0f} regime={regime}')

        # ── 1. 基础门控 ─────────────────────────────────────────────
        if score < SCORE_THRESHOLD:
            return {'action': 'skipped', 'reason': f'score={score:.0f}<{SCORE_THRESHOLD}'}

        if not sig.get('valid', False):
            return {'action': 'skipped', 'reason': 'valid=False'}

        entry_price = (entry_lo + entry_hi) / 2 if entry_lo > 0 else 0
        if entry_price <= 0 or sl <= 0:
            return {'action': 'skipped', 'reason': '入场价或止损价缺失'}

        # ── 2. 风控评估 ─────────────────────────────────────────────
        if self._risk is not None:
            account_state = AccountState(
                nav_usdt=self.nav,
                open_positions={},
                daily_realized_pnl=0.0,
            )
            risk_sig = RiskSignal(
                symbol=symbol,
                direction=direction,
                score=score,
                entry_price=entry_price,
                stop_loss=sl,
                regime=regime,
            )
            decision = self._risk.evaluate(risk_sig, account_state)
            if not decision.approved:
                logger.warning(f'[风控拒绝] {symbol}: {decision.reason}')
                return {'action': 'blocked', 'reason': decision.reason}

        # ── 3. 仓位计算 ─────────────────────────────────────────────
        if self._sizer is not None:
            size_result = self._sizer.calc(sig)
            if size_result['blocked']:
                return {'action': 'blocked', 'reason': size_result['reason']}
            size_usdt = size_result['usdt']
            size_pct  = size_result['pct']
        else:
            size_pct  = 5.0
            size_usdt = self.nav * size_pct / 100

        if size_usdt <= 0:
            return {'action': 'skipped', 'reason': '仓位金额为0'}

        # ── 4. dry_run模式：仅记录不执行 ────────────────────────────
        if self.dry_run:
            logger.info(f'[DRY RUN] 本应下单: {symbol} {direction} '
                        f'entry={entry_price:.4f} sl={sl:.4f} '
                        f'size={size_pct:.1f}%NAV=${size_usdt:.2f}')
            return {
                'action': 'dry_run',
                'reason': f'dry_run模式，实际不下单',
                'symbol': symbol,
                'direction': direction,
                'entry_price': entry_price,
                'size_usdt': size_usdt,
            }

        # ── 5. 执行下单 ──────────────────────────────────────────────
        if self._adapter is None:
            return {'action': 'skipped', 'reason': 'ExecutionAdapter未初始化'}

        try:
            from brahma_v6.execution.order_ticket import BrahmaOrderTicket
            ticket = BrahmaOrderTicket(
                symbol=symbol,
                side='BUY' if direction == 'LONG' else 'SELL',
                order_type='LIMIT',
                quantity_usdt=size_usdt,
                limit_price=entry_price,
                stop_loss=sl,
                take_profit=tp1,
                signal_id=signal_id,
                regime=regime,
                score=score,
            )
            event = self._adapter.submit(ticket)
            if event:
                logger.info(f'[下单成功] {symbol} {direction} event={event}')
                # 账本记录
                self._record_trade(sig, ticket, event, size_usdt, size_pct)
                return {'action': 'submitted', 'reason': '下单成功', 'event': str(event)}
            else:
                return {'action': 'skipped', 'reason': '适配器返回空事件'}

        except Exception as e:
            logger.error(f'[下单失败] {symbol}: {e}')
            return {'action': 'error', 'reason': str(e)}

    def _record_trade(self, sig, ticket, event, size_usdt, size_pct):
        """账本记录"""
        if self._ledger is None:
            return
        try:
            from brahma_v6.dharma2.models import TradeRecord
            record = TradeRecord(
                trade_id=sig.get('signal_id', ''),
                ticket_id=getattr(ticket, 'ticket_id', ''),
                symbol=sig.get('symbol', ''),
                direction=sig.get('direction', ''),
                regime=sig.get('regime', ''),
                score=float(sig.get('score', 0)),
                entry_price=float(sig.get('entry_lo', 0)),
                size_usdt=size_usdt,
                size_pct=size_pct,
                stop_loss=float(sig.get('sl', 0)),
                take_profit=float(sig.get('tp1', 0)),
                gross_pnl=0.0,
                net_pnl=0.0,
                status='open',
                source=sig.get('source', 'brahma'),
                ts_open=time.time(),
            )
            self._ledger.append(record)
        except Exception as e:
            logger.warning(f'账本记录失败: {e}')

    # ─── 批量处理 ────────────────────────────────────────────────────────────

    def run_once(self) -> list:
        """
        单次轮询：读取信号总线并处理所有pending有效信号

        Returns:
            处理结果列表
        """
        if not SIGNAL_BUS_PATH.exists():
            logger.debug('信号总线文件不存在')
            return []

        results = []
        lines = SIGNAL_BUS_PATH.read_text().strip().split('\n')

        for line in lines:
            if not line.strip():
                continue
            try:
                sig = json.loads(line)
            except json.JSONDecodeError:
                continue

            if sig.get('status') != 'pending':
                continue
            if not sig.get('valid', False):
                continue
            if float(sig.get('score', 0)) < SCORE_THRESHOLD:
                continue

            # 检查是否过期
            expires_at = sig.get('expires_at', '')
            if expires_at:
                try:
                    from datetime import datetime, timezone
                    exp = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if exp < datetime.now(timezone.utc):
                        continue
                except Exception:
                    pass

            result = self.process_signal(sig)
            results.append({'signal_id': sig.get('signal_id', ''), **result})

        return results

    def run(self, max_iter: int = -1):
        """
        持续轮询运行

        Args:
            max_iter: 最大迭代次数（-1=无限）
        """
        self._running = True
        iteration = 0
        logger.info(f'[SignalConsumer] 启动 mode={self.mode} poll_interval={POLL_INTERVAL}s')

        while self._running:
            if max_iter > 0 and iteration >= max_iter:
                break
            try:
                results = self.run_once()
                if results:
                    for r in results:
                        logger.info(f'[结果] {r}')
                else:
                    logger.debug('无新信号')
            except Exception as e:
                logger.error(f'[轮询错误] {e}')

            iteration += 1
            if self._running and (max_iter < 0 or iteration < max_iter):
                time.sleep(POLL_INTERVAL)

        logger.info('[SignalConsumer] 停止')

    def stop(self):
        self._running = False


# ─── CLI入口 ─────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%SZ',
    )

    parser = argparse.ArgumentParser(description='Brahma v6.5 全自动信号消费管道')
    parser.add_argument('--mode', choices=['backtest', 'paper', 'live'], default='paper')
    parser.add_argument('--nav', type=float, default=127.37, help='账户净值(USDT)')
    parser.add_argument('--testnet', action='store_true', default=True)
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='仅记录，不实际下单')
    parser.add_argument('--once', action='store_true', default=False,
                        help='只运行一次（用于测试）')
    args = parser.parse_args()

    consumer = SignalConsumer(
        mode=args.mode,
        nav=args.nav,
        testnet=args.testnet,
        dry_run=args.dry_run,
    )

    if args.once:
        results = consumer.run_once()
        print(f'处理完成: {len(results)}条信号')
        for r in results:
            print(f'  {r}')
    else:
        try:
            consumer.run()
        except KeyboardInterrupt:
            consumer.stop()
            print('\n[SignalConsumer] 已停止')


if __name__ == '__main__':
    main()

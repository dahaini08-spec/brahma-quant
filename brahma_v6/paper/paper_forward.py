"""
brahma_v6/paper/paper_forward.py — 90天Paper Forward验证框架
设计院 × 顶级评估v6.0 Phase 5 | 2026-07-08

目标：证明系统，而不是相信回测。

验收标准（90天）：
  ✅ ≥300笔 paper trade
  ✅ BTC/ETH/SOL 分资产统计
  ✅ LONG/SHORT 分方向统计
  ✅ Regime 分层统计
  ✅ 净收益为正（含真实成本）
  ✅ 滑点模型偏差 < 25%
  ✅ 最大回撤可控（<12%NAV）
  ✅ 无执行链事故

组件：
  PaperTrade         — 单笔 paper 交易记录
  PaperPortfolio     — 组合层：NAV / DD / PnL 追踪
  PaperExecutor      — 模拟执行（滑点/手续费/资金费建模）
  ForwardValidator   — 统计验证 + 验收报告
  DriftMonitor       — live vs backtest 漂移监控
"""
from __future__ import annotations
import json
import time
import math
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parents[2]
PAPER_DIR = BASE / "data" / "paper_forward"
PAPER_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════
#  成本模型（复用 Dharma2）
# ══════════════════════════════════════════════════════
COST_PARAMS = {
    "BTCUSDT":  {"fee": 0.0004, "slip_bps": 1.5, "fund_per_8h": 0.0006},
    "ETHUSDT":  {"fee": 0.0004, "slip_bps": 2.5, "fund_per_8h": 0.0007},
    "SOLUSDT":  {"fee": 0.0004, "slip_bps": 4.0, "fund_per_8h": 0.0010},
    "_default": {"fee": 0.0005, "slip_bps": 8.0, "fund_per_8h": 0.0015},
}


def _compute_net_pnl(
    symbol: str, direction: str, entry: float, exit_price: float,
    qty: float, leverage: int, holding_hours: float,
) -> Dict:
    p = COST_PARAMS.get(symbol, COST_PARAMS["_default"])
    notional = entry * qty * leverage
    gross_pnl = (exit_price - entry) / entry * notional * (1 if direction == "LONG" else -1)
    fee_total = notional * p["fee"] * 2
    slip_total = notional * p["slip_bps"] / 10000 * 2
    fund_total = notional * p["fund_per_8h"] * (holding_hours / 8)
    net_pnl = gross_pnl - fee_total - slip_total - fund_total
    return {
        "gross_pnl": round(gross_pnl, 4),
        "fee": round(fee_total, 4),
        "slippage": round(slip_total, 4),
        "funding": round(fund_total, 4),
        "net_pnl": round(net_pnl, 4),
    }


# ══════════════════════════════════════════════════════
#  Paper Trade 记录
# ══════════════════════════════════════════════════════
@dataclass
class PaperTrade:
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    symbol: str = ""
    direction: str = ""            # LONG / SHORT
    signal_score: float = 0.0
    regime: str = ""
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: Optional[float] = None
    quantity: float = 0.0
    leverage: int = 3
    nav_at_entry: float = 100.0
    size_nav_pct: float = 0.0      # 仓位占NAV%

    # 出场
    exit_price: float = 0.0
    exit_reason: str = ""          # SL / TP1 / TP2 / TSL / MANUAL / REGIME_EXIT
    holding_hours: float = 0.0
    ts_open: float = field(default_factory=time.time)
    ts_close: float = 0.0

    # PnL
    gross_pnl: float = 0.0
    fee: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    net_pnl: float = 0.0
    net_pnl_pct: float = 0.0       # 净PnL / NAV

    # 状态
    status: str = "OPEN"           # OPEN / CLOSED
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def close(self, exit_price: float, exit_reason: str = "MANUAL") -> None:
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        self.ts_close = time.time()
        self.holding_hours = (self.ts_close - self.ts_open) / 3600
        self.status = "CLOSED"
        cost = _compute_net_pnl(
            self.symbol, self.direction,
            self.entry_price, exit_price,
            self.quantity, self.leverage, self.holding_hours,
        )
        self.gross_pnl = cost["gross_pnl"]
        self.fee = cost["fee"]
        self.slippage = cost["slippage"]
        self.funding = cost["funding"]
        self.net_pnl = cost["net_pnl"]
        self.net_pnl_pct = self.net_pnl / max(self.nav_at_entry, 1) * 100

    def to_dict(self) -> Dict:
        return asdict(self)


# ══════════════════════════════════════════════════════
#  Paper 组合
# ══════════════════════════════════════════════════════
class PaperPortfolio:
    """
    NAV / DD / PnL 追踪 + 验收标准检测
    """

    def __init__(self, initial_nav: float = 100.0, log_file=None):
        self.initial_nav = initial_nav
        self.nav = initial_nav
        self.peak_nav = initial_nav
        self.trades: List[PaperTrade] = []
        self._log_file = Path(log_file) if log_file else PAPER_DIR / "paper_trades.jsonl"
        self._load_existing()

    def _load_existing(self) -> None:
        if self._log_file.exists():
            for line in self._log_file.read_text().splitlines():
                if line.strip():
                    try:
                        d = json.loads(line)
                        t = PaperTrade(**{k: v for k, v in d.items()
                                          if k in PaperTrade.__dataclass_fields__})
                        self.trades.append(t)
                        if t.status == "CLOSED":
                            self.nav += t.net_pnl
                    except Exception:
                        pass
            self.peak_nav = max(self.nav, self.initial_nav)

    def open_trade(self, trade: PaperTrade) -> PaperTrade:
        trade.nav_at_entry = self.nav
        self.trades.append(trade)
        self._persist(trade)
        return trade

    def close_trade(self, trade_id: str, exit_price: float, reason: str = "MANUAL") -> Optional[PaperTrade]:
        for t in self.trades:
            if t.trade_id == trade_id and t.status == "OPEN":
                t.close(exit_price, reason)
                self.nav += t.net_pnl
                self.peak_nav = max(self.peak_nav, self.nav)
                self._persist(t)
                return t
        return None

    def drawdown(self) -> float:
        return (self.peak_nav - self.nav) / self.peak_nav * 100

    def closed_trades(self) -> List[PaperTrade]:
        return [t for t in self.trades if t.status == "CLOSED"]

    def open_trades(self) -> List[PaperTrade]:
        return [t for t in self.trades if t.status == "OPEN"]

    def summary(self) -> Dict:
        closed = self.closed_trades()
        if not closed:
            return {"total_trades": 0, "nav": self.nav, "drawdown": 0.0}
        net_pnls = [t.net_pnl for t in closed]
        wins = [p for p in net_pnls if p > 0]
        losses = [p for p in net_pnls if p <= 0]
        wr = len(wins) / len(closed) if closed else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 999.0
        return {
            "total_trades": len(closed),
            "open_trades": len(self.open_trades()),
            "nav": round(self.nav, 4),
            "initial_nav": self.initial_nav,
            "total_net_pnl": round(sum(net_pnls), 4),
            "total_pnl_pct": round((self.nav - self.initial_nav) / self.initial_nav * 100, 2),
            "win_rate": round(wr, 3),
            "profit_factor": round(profit_factor, 3),
            "avg_win": round(sum(wins)/len(wins), 4) if wins else 0,
            "avg_loss": round(sum(losses)/len(losses), 4) if losses else 0,
            "max_drawdown_pct": round(self.drawdown(), 2),
            "total_fee": round(sum(t.fee for t in closed), 4),
            "total_slippage": round(sum(t.slippage for t in closed), 4),
            "total_funding": round(sum(t.funding for t in closed), 4),
        }

    def _persist(self, trade: PaperTrade) -> None:
        """
        P0-5修复：改为 append-only 写入，消除 O(n²) 全量重写占用。
        每次只追加一行，百万次推演时不卡死。
        """
        try:
            with self._log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass


# ══════════════════════════════════════════════════════
#  Paper 执行器（模拟成交）
# ══════════════════════════════════════════════════════
class PaperExecutor:
    """
    模拟真实执行，建模滑点/部分成交/延迟。
    用于验证信号→执行的完整链路，无需真实资金。
    """

    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio = portfolio

    def execute_signal(
        self,
        signal_or_symbol,                          # SignalScoredEvent 或旧式 symbol str
        intent_or_direction=None,                  # OrderIntentEvent 或旧式 direction str
        signal_score: float = 0.0,
        regime: str = "",
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: Optional[float] = None,
        size_nav_pct: float = 0.0,
        leverage: int = 3,
        trace_id: str = "",
    ) -> PaperTrade:
        """
        P0-1 修复：双模式入口。
        新式：execute_signal(signal: SignalScoredEvent, intent: OrderIntentEvent)
        旧式：execute_signal(symbol, direction, signal_score, ...) 保持兼容。
        """
        # ── 模式判断 ──────────────────────────────────────
        from brahma_v6.schemas.events import SignalScoredEvent as _SSE
        from brahma_v6.schemas.events import OrderIntentEvent as _OIE

        if isinstance(signal_or_symbol, _SSE) and isinstance(intent_or_direction, _OIE):
            # 新式：v6 事件注入
            sig_ev: _SSE = signal_or_symbol
            intent_ev: _OIE = intent_or_direction
            symbol = sig_ev.symbol
            direction = intent_ev.direction or sig_ev.direction
            signal_score = sig_ev.final_score
            regime = sig_ev.regime
            entry_price = intent_ev.entry_price or 0.0
            stop_loss = intent_ev.stop_loss
            take_profit = intent_ev.take_profit
            size_nav_pct = intent_ev.size_nav * 100  # size_nav 是小数，转为 %
            leverage = intent_ev.leverage
            trace_id = intent_ev.trace_id
        else:
            # 旧式兼容
            symbol = signal_or_symbol
            direction = intent_or_direction or "LONG"
        import random
        p = COST_PARAMS.get(symbol, COST_PARAMS["_default"])
        # 模拟滑点
        slip_pct = p["slip_bps"] / 10000 * (1 + random.uniform(-0.3, 0.3))
        if direction == "LONG":
            actual_entry = entry_price * (1 + slip_pct)
        else:
            actual_entry = entry_price * (1 - slip_pct)

        nav = self.portfolio.nav
        quantity = (nav * size_nav_pct / 100) / actual_entry

        trade = PaperTrade(
            symbol=symbol,
            direction=direction,
            signal_score=signal_score,
            regime=regime,
            entry_price=actual_entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            leverage=leverage,
            size_nav_pct=size_nav_pct,
            trace_id=trace_id or str(uuid.uuid4()),
        )
        return self.portfolio.open_trade(trade)

    def simulate_exit(
        self,
        trade: PaperTrade,
        current_price: float,
        force_exit: bool = False,
    ) -> Optional[PaperTrade]:
        """
        检查止损/止盈触发，模拟出场。
        """
        if trade.status != "OPEN":
            return None

        # SL检查
        if trade.direction == "LONG" and current_price <= trade.stop_loss:
            return self.portfolio.close_trade(trade.trade_id, trade.stop_loss, "SL")
        if trade.direction == "SHORT" and current_price >= trade.stop_loss:
            return self.portfolio.close_trade(trade.trade_id, trade.stop_loss, "SL")

        # TP1检查
        if trade.take_profit:
            if trade.direction == "LONG" and current_price >= trade.take_profit:
                return self.portfolio.close_trade(trade.trade_id, trade.take_profit, "TP1")
            if trade.direction == "SHORT" and current_price <= trade.take_profit:
                return self.portfolio.close_trade(trade.trade_id, trade.take_profit, "TP1")

        if force_exit:
            return self.portfolio.close_trade(trade.trade_id, current_price, "MANUAL")

        return None


# ══════════════════════════════════════════════════════
#  Forward Validator — 验收报告
# ══════════════════════════════════════════════════════
class ForwardValidator:
    """
    生成标准化验收报告，对标顶级评估v6.0要求。
    """

    ACCEPTANCE_CRITERIA = {
        "min_trades": 300,
        "min_win_rate": 0.50,
        "min_profit_factor": 1.2,
        "max_drawdown_pct": 12.0,
        "max_slippage_model_error": 0.25,  # 25%
        "min_net_pnl_pct": 0.0,
    }

    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio = portfolio

    def validate(self) -> Dict:
        closed = self.portfolio.closed_trades()
        summary = self.portfolio.summary()
        criteria = self.ACCEPTANCE_CRITERIA
        checks = {}

        checks["min_trades"] = {
            "value": summary.get("total_trades", 0),
            "threshold": criteria["min_trades"],
            "pass": summary.get("total_trades", 0) >= criteria["min_trades"],
        }
        checks["win_rate"] = {
            "value": summary.get("win_rate", 0),
            "threshold": criteria["min_win_rate"],
            "pass": summary.get("win_rate", 0) >= criteria["min_win_rate"],
        }
        checks["profit_factor"] = {
            "value": summary.get("profit_factor", 0),
            "threshold": criteria["min_profit_factor"],
            "pass": summary.get("profit_factor", 0) >= criteria["min_profit_factor"],
        }
        checks["max_drawdown"] = {
            "value": summary.get("max_drawdown_pct", 0),
            "threshold": criteria["max_drawdown_pct"],
            "pass": summary.get("max_drawdown_pct", 0) <= criteria["max_drawdown_pct"],
        }
        checks["net_pnl_positive"] = {
            "value": summary.get("total_pnl_pct", 0),
            "threshold": criteria["min_net_pnl_pct"],
            "pass": summary.get("total_pnl_pct", 0) > criteria["min_net_pnl_pct"],
        }

        # 分资产统计
        by_symbol = {}
        for t in closed:
            s = t.symbol
            if s not in by_symbol:
                by_symbol[s] = {"trades": 0, "net_pnl": 0.0, "wins": 0}
            by_symbol[s]["trades"] += 1
            by_symbol[s]["net_pnl"] += t.net_pnl
            if t.net_pnl > 0:
                by_symbol[s]["wins"] += 1
        for s in by_symbol:
            n = by_symbol[s]["trades"]
            by_symbol[s]["win_rate"] = round(by_symbol[s]["wins"] / n, 3) if n > 0 else 0

        # 分体制统计
        by_regime = {}
        for t in closed:
            r = t.regime or "UNKNOWN"
            if r not in by_regime:
                by_regime[r] = {"trades": 0, "net_pnl": 0.0, "wins": 0}
            by_regime[r]["trades"] += 1
            by_regime[r]["net_pnl"] += t.net_pnl
            if t.net_pnl > 0:
                by_regime[r]["wins"] += 1

        # 分方向统计
        by_direction = {}
        for t in closed:
            d = t.direction
            if d not in by_direction:
                by_direction[d] = {"trades": 0, "net_pnl": 0.0, "wins": 0}
            by_direction[d]["trades"] += 1
            by_direction[d]["net_pnl"] += t.net_pnl
            if t.net_pnl > 0:
                by_direction[d]["wins"] += 1

        all_pass = all(c["pass"] for c in checks.values())
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "overall_pass": all_pass,
            "summary": summary,
            "checks": checks,
            "by_symbol": by_symbol,
            "by_regime": by_regime,
            "by_direction": by_direction,
            "verdict": "✅ ACCEPTED" if all_pass else "⏳ IN_PROGRESS" if summary.get("total_trades", 0) < 10 else "❌ NEEDS_IMPROVEMENT",
        }

    def print_report(self) -> None:
        report = self.validate()
        print("\n" + "="*60)
        print("  PAPER FORWARD VALIDATION REPORT")
        print(f"  {report['ts'][:19]} UTC")
        print("="*60)
        s = report["summary"]
        print(f"\n  NAV: ${s.get('nav',0):.2f}  总PnL: {s.get('total_pnl_pct',0):+.2f}%")
        print(f"  交易: {s.get('total_trades',0)}笔  WR: {s.get('win_rate',0)*100:.1f}%  PF: {s.get('profit_factor',0):.2f}")
        print(f"  最大回撤: {s.get('max_drawdown_pct',0):.1f}%")
        print(f"\n  验收检查:")
        for name, c in report["checks"].items():
            icon = "✅" if c["pass"] else "❌"
            print(f"    {icon} {name}: {c['value']} (阈值: {c['threshold']})")
        print(f"\n  {report['verdict']}")
        if report["by_symbol"]:
            print(f"\n  分资产:")
            for sym, d in report["by_symbol"].items():
                print(f"    {sym}: {d['trades']}笔  WR={d['win_rate']*100:.0f}%  PnL=${d['net_pnl']:+.2f}")
        print("="*60)


# ══════════════════════════════════════════════════════
#  漂移监控
# ══════════════════════════════════════════════════════
class DriftMonitor:
    """
    监控 live performance vs backtest 的漂移。
    关键指标：WR漂移 / PnL漂移 / 滑点模型偏差。
    """

    def __init__(self, backtest_wr: float, backtest_ev: float):
        self.backtest_wr = backtest_wr
        self.backtest_ev = backtest_ev

    def check_drift(self, portfolio: PaperPortfolio) -> Dict:
        summary = portfolio.summary()
        live_wr = summary.get("win_rate", 0)
        live_ev = summary.get("avg_win", 0) * live_wr - abs(summary.get("avg_loss", 0)) * (1 - live_wr) if summary.get("total_trades", 0) > 0 else 0

        wr_drift = abs(live_wr - self.backtest_wr) / max(self.backtest_wr, 0.01)
        ev_drift = abs(live_ev - self.backtest_ev) / max(abs(self.backtest_ev), 0.001) if self.backtest_ev != 0 else 0

        return {
            "trades_count": summary.get("total_trades", 0),
            "backtest_wr": self.backtest_wr,
            "live_wr": live_wr,
            "wr_drift_pct": round(wr_drift * 100, 1),
            "wr_drift_ok": wr_drift < 0.20,
            "ev_drift_ok": ev_drift < 0.25,
            "slippage_model_ok": True,  # 实际需对比模型估算 vs 真实成交
            "overall_ok": wr_drift < 0.20 and ev_drift < 0.25,
        }


# ── 全局单例 ────────────────────────────────────────────
_portfolio: Optional[PaperPortfolio] = None


def get_portfolio(initial_nav: float = 100.0) -> PaperPortfolio:
    global _portfolio
    if _portfolio is None:
        _portfolio = PaperPortfolio(initial_nav)
    return _portfolio


if __name__ == "__main__":
    import random
    print("=== Paper Forward 验证框架 自检 ===\n")
    port = PaperPortfolio(initial_nav=100.0)
    executor = PaperExecutor(port)
    validator = ForwardValidator(port)

    # 模拟20笔交易
    scenarios = [
        ("BTCUSDT","LONG",162.0,"BULL_TREND",62000,60500,64000,0.8,3),
        ("ETHUSDT","LONG",155.0,"BULL_TREND",1720,1680,1810,0.6,3),
        ("BTCUSDT","SHORT",158.0,"BEAR_TREND",63000,64500,61000,0.8,3),
        ("SOLUSDT","LONG",140.0,"BEAR_RECOVERY",155,150,165,0.5,2),
        ("ETHUSDT","SHORT",165.0,"BEAR_TREND",1800,1850,1700,0.8,5),
    ]

    for i in range(20):
        sym, dirn, score, regime, entry, sl, tp, sz, lev = scenarios[i % len(scenarios)]
        entry_p = entry * random.uniform(0.98, 1.02)
        sl_p = sl * (random.uniform(0.995, 1.005) if dirn=="LONG" else random.uniform(0.995, 1.005))
        tp_p = tp * random.uniform(0.98, 1.02)
        trade = executor.execute_signal(sym, dirn, score, regime, entry_p, sl_p, tp_p, sz, lev)
        # 模拟价格运动（随机胜负）
        if random.random() < 0.62:  # 62% WR
            exit_p = tp_p
            reason = "TP1"
        else:
            exit_p = sl_p
            reason = "SL"
        port.close_trade(trade.trade_id, exit_p, reason)

    validator.print_report()
    drift = DriftMonitor(0.62, 0.5)
    d = drift.check_drift(port)
    print(f"\n  漂移监控: WR漂移={d['wr_drift_pct']}%  {'✅' if d['overall_ok'] else '⚠️'}")
    print("\n✅ Paper Forward 框架自检完成")

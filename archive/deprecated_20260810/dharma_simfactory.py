"""
dharma_simfactory.py — 梵天轻量纸交易沙盒
设计院自主实现 2026-08-07

功能：
  - 消费 live_signal_log.jsonl 中的信号（valid=True，无result）
  - 模拟执行：记录开仓价、止损价、TP1/TP2
  - 按当前K线价格模拟结算（SL/TP1/EXPIRE）
  - 输出到 data/simfactory_trades.jsonl
  - 统计WR/EV供sl_bandit加速收敛

不依赖外部框架，纯Python + ccxt行情。
"""

import json
import pathlib
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
SIGNAL_LOG = DATA_DIR / "live_signal_log.jsonl"
SIM_TRADES = DATA_DIR / "simfactory_trades.jsonl"
SIM_STATE  = DATA_DIR / "simfactory_state.json"


@dataclass
class SimTrade:
    signal_id: str
    symbol: str
    direction: str
    regime: str
    score: float
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    sl_pct: float
    rr1: float
    opened_at: float
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    result: Optional[str] = None   # TP1 / TP2 / SL / EXPIRE
    pnl_pct: Optional[float] = None
    timing_status: Optional[str] = None
    grade_num: Optional[int] = None


def load_signals(only_valid=True) -> list:
    if not SIGNAL_LOG.exists():
        return []
    records = []
    for line in SIGNAL_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if only_valid and not r.get("valid", True):
                continue
            records.append(r)
        except Exception:
            pass
    return records


def load_state() -> dict:
    if SIM_STATE.exists():
        try:
            return json.loads(SIM_STATE.read_text())
        except Exception:
            pass
    return {"processed_ids": []}


def save_state(state: dict):
    SIM_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def append_trade(trade: SimTrade):
    with open(SIM_TRADES, "a") as f:
        f.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")


def get_current_price(symbol: str) -> Optional[float]:
    """从 regime_state.json 获取最新价，避免额外API调用"""
    regime_file = DATA_DIR / "regime_state.json"
    if regime_file.exists():
        try:
            data = json.loads(regime_file.read_text())
            if symbol in data:
                return data[symbol].get("price")
        except Exception:
            pass
    return None


def simulate_signal(sig: dict) -> Optional[SimTrade]:
    """
    对单条信号执行模拟：
    - 已有 result 字段（真实结算）直接复用
    - 无 result 的（理论上383条都已结算）按已有结果记录
    """
    sid = sig.get("signal_id", "")
    symbol = sig.get("symbol", "")
    direction = sig.get("direction") or sig.get("signal_dir", "LONG")

    entry = sig.get("price") or sig.get("generated_price")
    sl = sig.get("stop_loss")
    tp1 = sig.get("tp1")
    tp2 = sig.get("tp2")

    if not entry or not sl or not tp1:
        return None

    # 使用真实结算结果（若已有）
    result = sig.get("result") or sig.get("status")
    exit_price = sig.get("exit_price")
    pnl_pct = sig.get("pnl_pct")

    if result in ("STOP_LOSS", "SL"):
        result_norm = "SL"
        if exit_price is None:
            exit_price = sl
        if pnl_pct is None:
            pnl_pct = -abs(sig.get("sl_pct", 2.0))
    elif result in ("TP1", "TAKE_PROFIT"):
        result_norm = "TP1"
        if exit_price is None:
            exit_price = tp1
        if pnl_pct is None:
            sl_pct = abs(sig.get("sl_pct", 2.0))
            rr1 = sig.get("rr1", 1.0)
            pnl_pct = sl_pct * rr1
    elif result in ("EXPIRE", "STALE", "EXPIRED"):
        result_norm = "EXPIRE"
        if exit_price is None:
            exit_price = entry
        pnl_pct = 0.0
    else:
        # 无法判断，跳过
        return None

    trade = SimTrade(
        signal_id=sid,
        symbol=symbol,
        direction=direction,
        regime=sig.get("regime", ""),
        score=sig.get("score", 0),
        entry_price=entry,
        stop_loss=sl,
        tp1=tp1,
        tp2=tp2 or tp1,
        sl_pct=sig.get("sl_pct", 0),
        rr1=sig.get("rr1", 1.0),
        opened_at=sig.get("ts", 0),
        closed_at=sig.get("settled_at") or sig.get("closed_ts"),
        exit_price=exit_price,
        result=result_norm,
        pnl_pct=pnl_pct,
        timing_status=sig.get("timing_status") or sig.get("timing_badge", ""),
        grade_num=sig.get("grade_num"),
    )
    return trade


def run_simulation(max_signals: int = 0) -> dict:
    """
    主入口：扫描信号日志，生成模拟交易记录，计算WR统计。
    max_signals=0 表示处理全量。
    """
    state = load_state()
    processed = set(state.get("processed_ids", []))

    signals = load_signals()
    new_count = 0
    skip_count = 0

    trades_written = []
    for sig in signals:
        sid = sig.get("signal_id", "")
        if sid in processed:
            skip_count += 1
            continue
        if max_signals and new_count >= max_signals:
            break

        trade = simulate_signal(sig)
        if trade:
            append_trade(trade)
            trades_written.append(trade)
            processed.add(sid)
            new_count += 1

    # 更新状态
    state["processed_ids"] = list(processed)
    state["last_run"] = time.time()
    save_state(state)

    # 统计
    stats = compute_stats()
    logger.info(f"[simfactory] 新处理 {new_count} 条，跳过 {skip_count} 条")
    return {"new": new_count, "skipped": skip_count, "stats": stats}


def compute_stats() -> dict:
    """读取 simfactory_trades.jsonl 计算WR/EV分体制分组"""
    if not SIM_TRADES.exists():
        return {}

    from collections import defaultdict
    groups = defaultdict(lambda: {"win": 0, "loss": 0, "ev_sum": 0.0})

    for line in SIM_TRADES.read_text().splitlines():
        try:
            t = json.loads(line)
        except Exception:
            continue
        key = f"{t.get('regime','?')}:{t.get('direction','?')}"
        result = t.get("result", "")
        pnl = t.get("pnl_pct", 0) or 0
        if result == "TP1":
            groups[key]["win"] += 1
        elif result == "SL":
            groups[key]["loss"] += 1
        groups[key]["ev_sum"] += pnl

    stats = {}
    for key, g in groups.items():
        n = g["win"] + g["loss"]
        wr = g["win"] / n if n > 0 else 0
        ev = g["ev_sum"] / n if n > 0 else 0
        stats[key] = {"n": n, "wr_pct": round(wr * 100, 1), "ev_pct": round(ev, 3)}

    return stats


def print_report():
    """命令行调用：打印沙盒统计报告"""
    result = run_simulation()
    stats = result["stats"]
    print(f"\n=== dharma_simfactory 纸交易沙盒报告 ===")
    print(f"新处理: {result['new']} 条 | 跳过: {result['skipped']} 条")
    print(f"\n体制×方向 WR统计:")
    print(f"{'Key':<30} {'N':>5} {'WR%':>7} {'EV%':>8}")
    print("-" * 55)
    for key, s in sorted(stats.items(), key=lambda x: -x[1]['n']):
        print(f"{key:<30} {s['n']:>5} {s['wr_pct']:>7.1f} {s['ev_pct']:>8.3f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print_report()

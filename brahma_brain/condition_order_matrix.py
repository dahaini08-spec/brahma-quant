"""
condition_order_matrix.py — 梵天2.0 Phase 3a · 条件单矩阵
设计院×达摩院 封印 2026-07-20

功能：
  预设触发条件，替代事后人工判断
  每次分析结尾输出「交易计划卡」
  包含 P0~P3 四级触发规则

触发级别：
  P0 生死线  → 价格触及强平价-15% → 立即减仓50%
  P1 止盈线  → 多单盈利达目标 → 自动锁利
  P2 调仓线  → 空/多比>2.0 → 减空至1:1
  P3 时间线  → 持仓超72H → 重新评估

VERSION = v1.0 · 2026-07-20
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

_STATE_PATH = Path(__file__).parent.parent / 'data' / 'condition_orders.json'

def _load():
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception: pass
    return {}

def _save(data):
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception: pass


def create_trade_plan(
    symbol: str,
    short_entry: float,
    long_entry: float,
    short_notional: float,
    long_notional: float,
    liq_price: float,
    leverage: int = 5,
    entry_ts: Optional[str] = None,
) -> dict:
    """
    建仓时生成「交易计划卡」，预设所有触发条件
    """
    now = entry_ts or datetime.now(timezone.utc).isoformat()

    # P0：强平警戒线（强平价上方15%缓冲 → 做多单价格跌至此处触发减仓）
    # 修复[2026-08-09]: 原来liq*0.85是强平价下方，触发条件>=永远成立
    # 正确：做多单liq在入场价下方，警戒线=liq*1.15（比强平价高15%），触发条件<=
    p0_price = liq_price * 1.15

    # P1：多单止盈目标（入场+8%×杠杆 = +40%保证金）
    p1_long_tp = long_entry * 1.40

    # P2：调仓线
    ratio = short_notional / long_notional if long_notional > 0 else 0
    p2_triggered = ratio > 2.0

    # P3：时间止损（72H后）
    try:
        entry_dt = datetime.fromisoformat(now.replace('Z', '+00:00'))
        p3_deadline = (entry_dt + timedelta(hours=72)).isoformat()
    except Exception:
        p3_deadline = "72H后"

    plan = {
        "symbol": symbol,
        "created_at": now,
        "short_entry": short_entry,
        "long_entry": long_entry,
        "short_notional": short_notional,
        "long_notional": long_notional,
        "liq_price": liq_price,
        "triggers": {
            "P0_生死线": {
                "condition": f"价格 ≤ {p0_price:.5f}（强平价{liq_price:.4f}的115%，上方15%缓冲）",
                "action": "立即减仓50%，不商量",
                "price": p0_price,
                "trigger_dir": "lte",  # 做多单：价格跌至警戒线触发
                "priority": 0,
            },
            "P1_多单止盈": {
                "condition": f"多单浮盈 ≥ 40%（入场价+40%={p1_long_tp:.5f}）",
                "action": "多单市价止盈，锁定利润",
                "price": p1_long_tp,
                "priority": 1,
            },
            "P2_调仓线": {
                "condition": f"空/多比 > 2.0（当前={ratio:.2f}:1）",
                "action": "减空单至与多单等量（1:1）",
                "triggered_now": p2_triggered,
                "priority": 2,
            },
            "P3_时间止损": {
                "condition": f"持仓超72H（截止 {p3_deadline[:16]}）",
                "action": "若未回本则全平，接受损失，重新布局",
                "deadline": p3_deadline,
                "priority": 3,
            },
        },
        "immediate_warnings": [],
    }

    # 立即警告
    if p2_triggered:
        plan["immediate_warnings"].append(
            f"⚠️ P2立即触发：空/多比={ratio:.2f}:1 > 2.0，建议立即减空"
        )

    # 保存
    all_plans = _load()
    all_plans[symbol] = plan
    _save(all_plans)

    return plan


def check_triggers(
    symbol: str,
    current_price: float,
    short_notional: float,
    long_notional: float,
    short_pnl: float,
    long_pnl: float,
) -> dict:
    """
    实时检查条件单是否触发
    返回已触发的条件列表和对应行动
    """
    result = {"triggered": [], "urgent": False, "summary": ""}
    try:
        all_plans = _load()
        plan = all_plans.get(symbol)
        if not plan:
            return result

        triggers = plan.get("triggers", {})
        fired = []

        # P0 检查（做多单：价格跌至警戒线 = 接近强平，立即减仓）
        p0 = triggers.get("P0_生死线", {})
        trigger_dir = p0.get("trigger_dir", "lte")  # 默认lte兼容旧数据
        p0_price_val = p0.get("price", 0)
        p0_hit = (trigger_dir == "lte" and current_price <= p0_price_val) or \
                 (trigger_dir == "gte" and current_price >= p0_price_val)
        if p0 and p0_price_val > 0 and p0_hit:
            fired.append({"name": "P0_生死线", "urgent": True,
                          "action": p0["action"],
                          "detail": f"当前价{current_price:.4f} {'≤' if trigger_dir=='lte' else '≥'} 警戒价{p0_price_val:.4f}"})

        # P1 检查
        p1 = triggers.get("P1_多单止盈", {})
        if p1 and current_price >= p1.get("price", 9e9):
            fired.append({"name": "P1_多单止盈", "urgent": False,
                          "action": p1["action"],
                          "detail": f"当前价{current_price:.4f} ≥ 止盈价{p1['price']:.4f}"})

        # P2 检查
        ratio = short_notional / long_notional if long_notional > 0 else 0
        if ratio > 2.0:
            fired.append({"name": "P2_调仓线", "urgent": ratio > 2.5,
                          "action": triggers.get("P2_调仓线", {}).get("action", "减空"),
                          "detail": f"空/多比={ratio:.2f}:1 > 2.0"})

        # P3 检查
        p3 = triggers.get("P3_时间止损", {})
        if p3:
            try:
                deadline = datetime.fromisoformat(p3["deadline"].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= deadline:
                    fired.append({"name": "P3_时间止损", "urgent": False,
                                  "action": p3["action"],
                                  "detail": f"持仓已超72H（截止{p3['deadline'][:16]}）"})
            except Exception: pass

        urgent = any(f["urgent"] for f in fired)
        summary = (
            f"🚨 {len(fired)}个条件触发！" if fired else "✅ 无条件触发，持仓正常"
        )

        result.update({"triggered": fired, "urgent": urgent, "summary": summary})
    except Exception as e:
        result["summary"] = f"[condition_order_matrix异常,不阻断] {e}"
    return result


def format_plan_card(plan: dict) -> str:
    """格式化交易计划卡（供分析报告末尾展示）"""
    if not plan:
        return ""
    try:
        lines = [
            f"\n┌─── 交易计划卡 · {plan['symbol']} ──────────────────────┐",
            f"│ 建立时间: {plan['created_at'][:16]}",
            f"│ 空单入场: {plan['short_entry']}  多单入场: {plan['long_entry']}",
        ]
        for name, t in plan.get("triggers", {}).items():
            triggered = "⚡已触发" if t.get("triggered_now") else ""
            lines.append(f"│ [{t['priority']}] {name}: {t['condition']} {triggered}")
            lines.append(f"│     → {t['action']}")
        for w in plan.get("immediate_warnings", []):
            lines.append(f"│ {w}")
        lines.append("└────────────────────────────────────────────────────┘")
        return "\n".join(lines)
    except Exception:
        return ""

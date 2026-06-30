"""
L1 Schema Sentinel — 状态文件契约守卫
实时校验 brahma_state.json 中每个字段的类型、范围、逻辑一致性
发现违规立刻记录到 nerve_alerts，不抛异常不中断主流程
"""
import json, pathlib, time
from typing import Any, Dict, List, Optional, Tuple

STATE_FILE = pathlib.Path(__file__).parent.parent / "data" / "brahma_state.json"
ALERTS_FILE = pathlib.Path(__file__).parent.parent / "data" / "nerve_alerts.jsonl"

# ─── 验证函数（必须在 FIELD_SCHEMA 之前定义） ────────────────────
# [R4-fix audit-2026-06-17 DEDUP] def _check_circuit_breaker(v: Any) -> bool:
# [R4-fix audit-2026-06-17 DEDUP]     """circuit_breaker必须有active(bool)字段"""
# [R4-fix audit-2026-06-17 DEDUP]     if not isinstance(v, dict): return False
# [R4-fix audit-2026-06-17 DEDUP]     if "active" not in v: return False
# [R4-fix audit-2026-06-17 DEDUP]     if not isinstance(v["active"], bool): return False
# [R4-fix audit-2026-06-17 DEDUP]     if v["active"] and not v.get("reason"): return False
# [R4-fix audit-2026-06-17 DEDUP]     return True
# [R4-fix audit-2026-06-17 DEDUP] 
# [R4-fix audit-2026-06-17 DEDUP] def _check_portfolio_risk(v: Any) -> bool:
# [R4-fix audit-2026-06-17 DEDUP]     if not isinstance(v, dict): return False
# [R4-fix audit-2026-06-17 DEDUP]     return "long_count" in v and "short_count" in v
# [R4-fix audit-2026-06-17 DEDUP] 
# ─── 字段契约 ────────────────────────────────────────────────────
def _check_ws_guardian(v: Any) -> bool:
    """ws_guardian必须有last_ping（pid在待机时可能缺失）"""
    if not isinstance(v, dict): return False
    return "last_ping" in v and v["last_ping"] is not None

# [R4-fix audit-2026-06-17 DEDUP] def _check_portfolio_risk(v: Any) -> bool:
# [R4-fix audit-2026-06-17 DEDUP]     """portfolio_risk必须有long_count/short_count"""
# [R4-fix audit-2026-06-17 DEDUP]     if not isinstance(v, dict): return False
# [R4-fix audit-2026-06-17 DEDUP]     return "long_count" in v and "short_count" in v
# [R4-fix audit-2026-06-17 DEDUP] 
FIELD_SCHEMA = {
    # field_name: (expected_type_or_types, validator_fn, description)
    "nav":              (float,  lambda v: 0 < v < 100000,    "NAV必须是正数"),
    "nav_verified":     (bool,   None,                         "nav_verified必须是bool"),
    "health":           (str,    lambda v: v in ("GREEN","YELLOW","RED"), "health枚举值非法"),
    "circuit_breaker":  (dict,   _check_circuit_breaker,       "circuit_breaker结构非法"),
    "scan_count":       (int,    lambda v: v >= 0,             "scan_count不能为负"),
    "trade_count":      (int,    lambda v: v >= 0,             "trade_count不能为负"),
    "positions":        (list,   None,                         "positions必须是list"),
    "ws_guardian":      (dict,   _check_ws_guardian,           "ws_guardian结构非法"),
    "signal_queue":     (list,   None,                         "signal_queue必须是list"),
    "portfolio_risk":   (dict,   _check_portfolio_risk,        "portfolio_risk结构非法"),
}

def _check_circuit_breaker(v: Any) -> bool:
    """circuit_breaker必须有active(bool)字段"""
    if not isinstance(v, dict): return False
    if "active" not in v: return False
    if not isinstance(v["active"], bool): return False
    # 如果active=True，must有reason
    if v["active"] and not v.get("reason"): return False
    return True

# ─── 位置契约：每个持仓字段 ─────────────────────────────────────
POSITION_SCHEMA = {
    "signal_id":   (str,   None),
    "symbol":      (str,   lambda v: len(v) >= 3),
    "direction":   (str,   lambda v: v in ("做多","做空","LONG","SHORT")),
    "qty":         (float, lambda v: v > 0),
    "entry_price": (float, lambda v: v > 0),
    "sl_price":    (float, lambda v: v > 0),
    "status":      (str,   lambda v: v in ("OPEN","CLOSED","PENDING","STOP_LOSS","TAKE_PROFIT")),
    "dry_run":     (bool,  None),
    # [v13.1 UP-021] v13.0 止损四层架构字段
    "primary_tf":  (str,   lambda v: v in ("4H","1D","1H","15m","1W")),
    "entry_tf":    (str,   lambda v: v in ("1H","15m","4H","5m")),
    "sl_basis":    (str,   None),
    "tp1_price":   (float, lambda v: v > 0),
    "tp2_price":   (float, lambda v: v > 0),
}

# ─── 核心逻辑 ─────────────────────────────────────────────────────
def _alert(level: str, field: str, issue: str, value: Any) -> Dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer": "L1_SCHEMA",
        "level": level,   # ERROR / WARN / INFO
        "field": field,
        "issue": issue,
        "value": str(value)[:120],
    }

def check_state(state: Dict) -> List[Dict]:
    alerts = []

    # 1. 顶层字段类型检查
    for field, spec in FIELD_SCHEMA.items():
        expected_type, validator, desc = spec
        if field not in state:
            alerts.append(_alert("WARN", field, f"字段缺失: {desc}", None))
            continue
        val = state[field]
        if not isinstance(val, expected_type):
            alerts.append(_alert("ERROR", field,
                f"类型错误: 期望{expected_type.__name__} 实际{type(val).__name__} — {desc}", val))
            continue
        if validator:
            try:
                ok = validator(val)
            except Exception as e:
                ok = False
            if not ok:
                alerts.append(_alert("ERROR", field, f"校验失败: {desc}", val))

    # 2. 逻辑一致性检查
    positions = state.get("positions", [])
    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    cb = state.get("circuit_breaker", {})
    if isinstance(cb, dict) and cb.get("active") and len(open_pos) > 0:
        # 熔断激活时不应开新仓（持仓继续是正常的）
        pass  # 允许，只记录信息
    
    # 3. 持仓字段完整性检查（只检查OPEN状态）
    for i, pos in enumerate(open_pos):
        for field, (expected_type, validator) in POSITION_SCHEMA.items():
            if field not in pos:
                alerts.append(_alert("WARN", f"positions[{i}].{field}",
                    f"OPEN持仓缺少字段: {pos.get('signal_id','?')}", None))
            else:
                val = pos[field]
                if not isinstance(val, (expected_type, int) if expected_type == float else expected_type):
                    # float字段接受int
                    if expected_type == float and isinstance(val, int):
                        pass
                    else:
                        alerts.append(_alert("WARN", f"positions[{i}].{field}",
                            f"类型错误: 期望{expected_type.__name__} 实际{type(val).__name__}", val))

    # 4. SL价格逻辑检查
    for i, pos in enumerate(open_pos):
        direction = pos.get("direction","")
        entry = pos.get("entry_price", 0)
        sl = pos.get("sl_price", 0)
        if entry > 0 and sl > 0:
            if direction in ("做多", "LONG") and sl >= entry:
                alerts.append(_alert("ERROR", f"positions[{i}].sl_price",
                    f"做多SL({sl}) >= 入场价({entry})，止损设置错误", pos.get("signal_id")))
            if direction in ("做空", "SHORT") and sl <= entry:
                alerts.append(_alert("ERROR", f"positions[{i}].sl_price",
                    f"做空SL({sl}) <= 入场价({entry})，止损设置错误", pos.get("signal_id")))

    return alerts

def run() -> List[Dict]:
    """读取状态文件并校验，返回告警列表"""
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception as e:
        alert = _alert("ERROR", "STATE_FILE", f"状态文件读取失败: {e}", str(STATE_FILE))
        _append_alerts([alert])
        return [alert]

    alerts = check_state(state)
    if alerts:
        _append_alerts(alerts)
    return alerts

def _append_alerts(alerts: List[Dict]):
    """追加告警到日志文件"""
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_FILE, "a") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    results = run()
    if results:
        print(f"[SCHEMA SENTINEL] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['field']}: {r['issue']}")
    else:
        print("[SCHEMA SENTINEL] 状态契约校验通过 ✓")

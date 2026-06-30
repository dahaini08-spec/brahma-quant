"""
L6 Reconciler — brahma_state vs Binance 实盘对账
这是神经系统最关键的一层：任何本地状态与实盘不一致都必须在这里发现

检查项：
  R1: brahma_state OPEN 持仓 在 Binance 无对应实盘持仓（幽灵持仓）
  R2: Binance 有实盘持仓 但 brahma_state 没有记录（漏单）
  R3: NAV 与 Binance 账户余额偏差超过阈值
  R4: brahma_state OPEN 持仓数量 vs Binance 实盘数量不匹配
"""
import json, time, pathlib, urllib.request, urllib.parse, hmac, hashlib
from typing import List, Dict, Optional

ROOT        = pathlib.Path(__file__).parent.parent
STATE_FILE  = ROOT / "data" / "brahma_state.json"
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

# 偏差阈值
NAV_DIFF_THRESHOLD_PCT = 5.0    # NAV差异超过5%告警
NAV_DIFF_THRESHOLD_ABS = 10.0   # 或绝对差异超过10 USDT告警


def _load_keys():
    try:
        import importlib.util, sys, os
        # config.py 在 trading-system/ 目录下
        conf_path = str(ROOT / "config.py")
        conf_path = os.path.abspath(conf_path)
        if os.path.exists(conf_path):
            spec = importlib.util.spec_from_file_location("trading_config", conf_path)
            mod  = importlib.util.module_from_spec(spec)
            old  = os.getcwd()
            os.chdir(os.path.dirname(conf_path))
            spec.loader.exec_module(mod)
            os.chdir(old)
            bk = getattr(mod, "binance_keys", None)
            if callable(bk):
                r = bk()
                if isinstance(r, (list, tuple)) and len(r) >= 2:
                    return r[0], r[1]
    except Exception:
        pass
    return "", ""


def _sign(params: dict, secret: str) -> str:
    qs = urllib.parse.urlencode(params)
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _fapi_get(path: str, params: dict = None) -> tuple:
    api_key, secret = _load_keys()
    if not api_key:
        return False, "no_api_key"
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p, secret)
    url = f"https://fapi.binance.com{path}?{urllib.parse.urlencode(p)}"
    try:
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key})
        with urllib.request.urlopen(req, timeout=8) as r:
            return True, json.loads(r.read())
    except Exception as e:
        return False, str(e)


def _alert(level: str, check: str, issue: str, data: str = "") -> Dict:
    return {
        "ts":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer":  "L6_RECONCILE",
        "level":  level,
        "check":  check,
        "issue":  issue,
        "data":   data[:300],
    }


def _append_alerts(alerts: List[Dict]):
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_FILE, "a") as f:
        for a in alerts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def get_binance_positions() -> Optional[Dict[str, float]]:
    """返回 {symbol: positionAmt} 只含非零持仓"""
    ok, data = _fapi_get("/fapi/v2/positionRisk")
    if not ok or not isinstance(data, list):
        return None
    return {
        p["symbol"]: float(p["positionAmt"])
        for p in data
        if abs(float(p.get("positionAmt", 0))) > 0
    }


def get_binance_nav() -> Optional[float]:
    """返回合约账户 totalWalletBalance"""
    ok, data = _fapi_get("/fapi/v2/account")
    if not ok or not isinstance(data, dict):
        return None
    try:
        return float(data.get("totalWalletBalance", 0))
    except Exception:
        return None


def run() -> List[Dict]:
    alerts = []

    # 读本地状态
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception as e:
        alerts.append(_alert("ERROR", "R0_STATE_READ", f"状态文件读取失败: {e}"))
        _append_alerts(alerts)
        return alerts

    # 合并 brahma_state + hunter_v2_state 的 OPEN 持仓（双文件真相来源）
    _hv2_path = ROOT / "data" / "hunter_v2_state.json"
    _hv2_positions = []
    if _hv2_path.exists():
        try:
            _hv2_state = json.loads(_hv2_path.read_text())
            _hv2_positions = _hv2_state.get("positions", [])
        except Exception:
            pass

    _all_local = list(state.get("positions", [])) + [
        p for p in _hv2_positions
        if not any(q.get("symbol") == p.get("symbol") for q in state.get("positions", []))
    ]

    local_open = {
        p["symbol"]: p
        for p in _all_local
        if p.get("status") == "OPEN"
    }

    # 查 Binance 实盘
    binance_pos = get_binance_positions()
    if binance_pos is None:
        alerts.append(_alert("WARN", "R0_API", "Binance 持仓查询失败，跳过对账", ""))
        _append_alerts(alerts)
        return alerts

    # R1: 本地 OPEN 但 Binance 无持仓（幽灵持仓）
    for sym, pos in local_open.items():
        if sym not in binance_pos:
            open_ts = pos.get("open_ts", "")[:16]
            alerts.append(_alert(
                "ERROR", "R1_GHOST_POSITION",
                f"{sym} 本地状态OPEN但Binance无实盘持仓（幽灵）",
                f"signal_id={pos.get('signal_id','')} open_ts={open_ts} entry={pos.get('entry_price')}",
            ))
            # 自动清理幽灵持仓（Binance已平仓但本地仍 OPEN）
            try:
                import time as _time
                _state_fresh = json.loads(STATE_FILE.read_text())
                _healed = False
                for _p in _state_fresh.get("positions", []):
                    if _p.get("symbol") == sym and _p.get("status") == "OPEN":
                        _p["status"] = "CLOSED"
                        _p["close_reason"] = "L6_auto_heal: Binance无实盘持仓"
                        _p["close_ts"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
                        _healed = True
                if _healed:
                    _state_fresh["version"] = _state_fresh.get("version", 0) + 1
                    STATE_FILE.write_text(json.dumps(_state_fresh, ensure_ascii=False, indent=2))
                    alerts[-1]["issue"] += "（已自动清理）"
            except Exception as _he:
                pass

    # R2: Binance 有持仓但本地没有记录（漏单）
    for sym, amt in binance_pos.items():
        if sym not in local_open:
            alerts.append(_alert(
                "ERROR", "R2_MISSING_POSITION",
                f"{sym} Binance有实盘持仓({amt})但brahma_state无OPEN记录（漏单）",
                f"positionAmt={amt}",
            ))

    # R4: 数量不匹配摘要
    n_local   = len(local_open)
    n_binance = len(binance_pos)
    if n_local != n_binance:
        alerts.append(_alert(
            "ERROR", "R4_COUNT_MISMATCH",
            f"持仓数量不一致: 本地{n_local}个 vs Binance{n_binance}个",
            f"local={list(local_open.keys())} binance={list(binance_pos.keys())}",
        ))

    # R3: NAV 偏差
    binance_nav = get_binance_nav()
    local_nav   = state.get("nav", 0)
    if binance_nav is not None and local_nav > 0:
        diff_abs = abs(binance_nav - local_nav)
        diff_pct = diff_abs / local_nav * 100
        if diff_abs > NAV_DIFF_THRESHOLD_ABS or diff_pct > NAV_DIFF_THRESHOLD_PCT:
            alerts.append(_alert(
                "WARN", "R3_NAV_DRIFT",
                f"NAV偏差: 本地{local_nav:.2f} vs Binance{binance_nav:.2f} (差{diff_abs:.2f} USDT / {diff_pct:.1f}%)",
                f"local_nav={local_nav} binance_nav={binance_nav}",
            ))

    if alerts:
        _append_alerts(alerts)
    return alerts


if __name__ == "__main__":
    results = run()
    if results:
        print(f"[RECONCILER] {len(results)} 个告警:")
        for r in results:
            print(f"  [{r['level']}] {r['check']}: {r['issue']}")
    else:
        print("[RECONCILER] 对账通过，本地状态与实盘一致 ✓")

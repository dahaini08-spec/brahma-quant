"""
key_sentinel.py — 太医官 · 凭证哨兵 L_KEY 层
定期检测所有 API Key / Webhook 有效性
集成到太医官 nerve_runner
"""
import time, json, os, sys
from pathlib import Path
from typing import List, Dict, Any

ROOT      = Path(__file__).parent.parent
ENV_FILE  = ROOT.parent / "alerts" / ".env"
# 测试结果缓存文件（避免每次都真实请求）
CACHE_FILE = ROOT / "data" / "key_sentinel_cache.json"

# 测试间隔（秒）
INTERVALS = {
    "binance":    300,    # 5分钟
    "dingtalk":  86400,  # 24小时（避免触发频率限制）
    "coinglass": 3600,   # 1小时
    "square":   43200,  # 12小时
}


def _load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items()
                if k.startswith(('BINANCE_', 'DINGTALK_'))})
    return env


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cache(cache: dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def _alert(level: str, field: str, issue: str, value: str = "") -> dict:
    return {
        "ts":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer": "L0_KEY",
        "level": level,
        "field": field,
        "issue": issue,
        "value": value,
    }


def _test_binance(api_key: str, secret: str) -> tuple:
    if not api_key or not secret:
        return False, "未配置"
    try:
        import hmac, hashlib, urllib.request, urllib.error
        ts  = int(time.time() * 1000)
        qs  = f"timestamp={ts}"
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"https://fapi.binance.com/fapi/v2/balance?{qs}&signature={sig}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if isinstance(data, list):
            return True, "ok"
        return False, f"响应异常"
    except urllib.error.HTTPError as e:
        return False, f"HTTP{e.code}"
    except Exception as e:
        return False, str(e)[:60]


def _test_square(key: str) -> tuple:
    """正确端点: /public/pgc/openApi/content/add
    220011=认证通过(内容空被拦) 220003=Key无效 000000=成功
    """
    if not key:
        return False, "未配置"
    SQUARE_URL = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    try:
        import urllib.request, urllib.error
        payload = json.dumps({"content": "", "images": []}).encode()
        req = urllib.request.Request(
            SQUARE_URL, data=payload,
            headers={"Content-Type": "application/json",
                     "X-Square-OpenAPI-Key": key,
                     "clienttype": "binanceSkill"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        code = data.get('code', '')
        return code in ('000000', '220011'), code
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            code = body.get('code', '?')
            if code in ('220011',):
                return True, 'ok'
            if code == '220003':
                return False, 'Key无效'
            if code == '220004':
                return False, 'Key已过期'
            return False, f"HTTP{e.code}/code={code}"
        except:
            return False, f"HTTP{e.code}"
    except Exception as e:
        return False, str(e)[:60]


def run_checks() -> List[Dict[str, Any]]:
    """运行所有 key 检测，返回告警列表"""
    alerts = []
    env    = _load_env()
    cache  = _load_cache()
    now    = time.time()
    updated = False

    # ── Binance ──────────────────────────────────────────────────
    binance_last = cache.get("binance_last_check", 0)
    if now - binance_last > INTERVALS["binance"]:
        api_key = env.get("BINANCE_API_KEY", "")
        secret  = env.get("BINANCE_SECRET", "")
        ok, msg = _test_binance(api_key, secret)
        cache["binance_ok"]         = ok
        cache["binance_last_check"] = now
        cache["binance_msg"]        = msg
        updated = True
    else:
        ok  = cache.get("binance_ok", True)
        msg = cache.get("binance_msg", "cached")

    if not ok:
        alerts.append(_alert("ERROR", "BINANCE_API_KEY",
                              f"Binance API Key 失效({msg})，交易中断",
                              f"last_check={time.strftime('%H:%M:%S', time.gmtime(cache.get('binance_last_check',0)))}"))
    elif not env.get("BINANCE_API_KEY"):
        alerts.append(_alert("ERROR", "BINANCE_API_KEY", "Binance API Key 未配置"))

    # ── Coinglass ─────────────────────────────────────────────────
    cg_last = cache.get("coinglass_last_check", 0)
    if now - cg_last > INTERVALS.get("coinglass", 3600):
        cg_key = env.get("COINGLASS_API_KEY", "")
        ok_cg, msg_cg = False, "未配置"
        if cg_key:
            try:
                import urllib.request
                # v2: coinglassSecret header
                req2 = urllib.request.Request(
                    "https://open-api.coinglass.com/public/v2/funding?symbol=BTC",
                    headers={"coinglassSecret": cg_key}
                )
                with urllib.request.urlopen(req2, timeout=8) as r:
                    d2 = json.loads(r.read())
                v2_ok = str(d2.get("code", "")) == "0"
                # v3: CG-API-KEY header (修复 2026-05-17)
                req3 = urllib.request.Request(
                    "https://open-api-v3.coinglass.com/api/futures/openInterest/ohlc-aggregated-history?symbol=BTC&interval=h4&limit=1",
                    headers={"CG-API-KEY": cg_key}
                )
                with urllib.request.urlopen(req3, timeout=8) as r:
                    d3 = json.loads(r.read())
                v3_ok = str(d3.get("code", "")) == "0"
                ok_cg  = v2_ok and v3_ok
                msg_cg = f"v2={'ok' if v2_ok else 'fail'} v3={'ok' if v3_ok else 'fail'}" if ok_cg else f"v2={v2_ok} v3={v3_ok}"
                if ok_cg: msg_cg = "ok"
            except Exception as e:
                msg_cg = str(e)[:60]
        cache["coinglass_ok"]         = ok_cg
        cache["coinglass_last_check"] = now
        cache["coinglass_msg"]        = msg_cg
        updated = True
    else:
        ok_cg  = cache.get("coinglass_ok", True)
        msg_cg = cache.get("coinglass_msg", "cached")

    if not ok_cg and env.get("COINGLASS_API_KEY"):
        alerts.append(_alert("WARN", "COINGLASS_API_KEY",
                              f"Coinglass Key 异常({msg_cg})，OI数据降级"))

    # ── Square ───────────────────────────────────────────────────
    sq_last = cache.get("square_last_check", 0)
    if now - sq_last > INTERVALS["square"]:
        # 读 square/config.py
        sq_keys = []
        try:
            sq_cfg_path = ROOT.parent / "scripts" / "square" / "config.py"
            import importlib.util, re, ast
            content = sq_cfg_path.read_text() if sq_cfg_path.exists() else ''
            m = re.search(r'SQUARE_API_KEYS\s*=\s*(\[.*?\])', content, re.DOTALL)
            if m:
                sq_keys = ast.literal_eval(m.group(1))
        except Exception:
            pass

        sq_results = []
        for i, key in enumerate(sq_keys[:3]):
            ok_i, msg_i = _test_square(key)
            sq_results.append({"ok": ok_i, "msg": msg_i})

        cache["square_results"]     = sq_results
        cache["square_last_check"]  = now
        cache["square_keys_count"]  = len(sq_keys)
        updated = True
    else:
        sq_results = cache.get("square_results", [])

    sq_ok_count  = sum(1 for r in sq_results if r.get("ok"))
    sq_fail_count = sum(1 for r in sq_results if not r.get("ok") and r.get("msg","") in ('Key已过期','Key无效'))
    sq_total      = len(sq_results)
    if sq_total > 0 and sq_ok_count == 0:
        alerts.append(_alert("ERROR", "SQUARE_API_KEY",
                              f"Square 全部 {sq_total} 个 Key 失效，广场发帖中断",
                              "需立即更新 Key"))
    elif sq_fail_count > 0:
        alerts.append(_alert("WARN", "SQUARE_API_KEY",
                              f"Square {sq_fail_count}/{sq_total} 个 Key 已过期",
                              "建议尽快更新，剩余 Key 仍可发帖"))

    if updated:
        _save_cache(cache)

    return alerts


if __name__ == "__main__":
    print("🔑 机要官哨兵 · 太医院凭证检测")
    alerts = run_checks()
    if alerts:
        for a in alerts:
            icon = "🔴" if a["level"] == "ERROR" else "🟡"
            print(f"{icon} [{a['layer']}] {a['field']}: {a['issue']}")
    else:
        print("✅ 所有凭证正常")

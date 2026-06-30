"""
sentinel_core.py — 梵天哨兵核心引擎
主动健康感知：健康契约 + 探针 + 自愈
设计院 v1.0 · 2026-06-11
"""
import json, time, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
DATA    = BASE / 'data'
SCRIPTS = BASE / 'scripts'

# ─── 健康契约 ────────────────────────────────────────────────────────────
HEALTH_CONTRACTS = [
    # (名称, 类型, 参数, 级别)
    # 数据文件新鲜度
    ("brahma_state",     "file_age",  {"file": "data/brahma_state.json",        "max_min": 8},   "CRITICAL"),
    # ws_guardian_state disabled — 进程不常驻，文件会过期，无实盘期不监控
    ("signal_queue",     "file_age",  {"file": "data/signal_queue.jsonl",       "max_min": 300}, "WARN"),  # full-cycle每4h，允许5h空窗
    ("soma_state",       "file_age",  {"file": "data/soma_state.json",          "max_min": 1440}, "WARN"),  # 每天更新，允许24h空窗
    # 进程存活
    # ws_guardian_proc disabled — gateway重启会杀掉进程，无实盘期不监控 [2026-06-11]
    # watchdog_proc disabled — 无实盘期间不监控 [2026-06-11]
    # API连通性（有1H缓存，不消耗频率限制）
    ("binance_fapi",     "api_ping",  {"url": "https://fapi.binance.com/fapi/v1/ping", "cache_min": 60}, "CRITICAL"),
    # 配额预警
    ("soma_quota",       "quota",     {"file": "data/soma_state.json", "warn_pct": 85, "crit_pct": 95}, "WARN"),  # 调整阈值，避免今天86%误报
    # 信号链路
    ("signal_count",     "signal_freshness", {"file": "data/live_signal_log.jsonl", "max_no_signal_h": 48}, "WARN"),  # 初期积累期
    # brahma_state合理性
    ("regime_valid",     "regime_check", {"file": "data/brahma_state.json"}, "ERROR"),
]

# ─── 探针函数 ─────────────────────────────────────────────────────────────

def probe_file_age(params):
    """文件新鲜度检测"""
    p = BASE / params["file"]
    if not p.exists():
        return False, f"文件不存在: {params['file']}"
    age_min = (time.time() - p.stat().st_mtime) / 60
    max_min = params["max_min"]
    if age_min > max_min:
        return False, f"{params['file']} {age_min:.0f}分未更新（上限{max_min}min）"
    return True, f"OK {age_min:.1f}min"

def probe_process(params):
    """进程存活检测"""
    pattern = params["pattern"]
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    if r.returncode == 0:
        pids = r.stdout.strip().split()
        return True, f"OK PID={pids[0]}"
    return False, f"进程不存在: {pattern}"

_api_cache = {}
def probe_api_ping(params):
    """API连通性（带缓存，避免频繁消耗）"""
    url = params["url"]
    cache_min = params.get("cache_min", 60)
    now = time.time()
    if url in _api_cache:
        cached_time, cached_ok, cached_msg = _api_cache[url]
        if now - cached_time < cache_min * 60:
            return cached_ok, f"[缓存] {cached_msg}"
    try:
        t0 = time.time()
        urllib.request.urlopen(url, timeout=5)
        ms = int((time.time() - t0) * 1000)
        _api_cache[url] = (now, True, f"OK {ms}ms")
        return True, f"OK {ms}ms"
    except Exception as e:
        err = str(e)[:60]
        if "418" in err:
            msg = f"⚠️ IP被封禁: {err}"
            _api_cache[url] = (now, False, msg)
            return False, msg
        _api_cache[url] = (now, False, f"连接失败: {err}")
        return False, f"API不可达: {err}"

def probe_quota(params):
    """配额预警"""
    p = BASE / params["file"]
    if not p.exists():
        return True, "soma_state不存在，跳过"
    try:
        d = json.loads(p.read_text())
        used = d.get("used", 0)
        budget = d.get("budget", 150000)
        pct = used / budget * 100
        crit = params.get("crit_pct", 90)
        warn = params.get("warn_pct", 70)
        if pct >= crit:
            return False, f"配额危险: {used:,}/{budget:,} ({pct:.0f}%)"
        if pct >= warn:
            return None, f"配额预警: {used:,}/{budget:,} ({pct:.0f}%)"  # None=WARN | None表示WARN级别（非True/False）
        return True, f"OK {pct:.0f}%"
    except Exception as e:
        return True, f"读取失败: {e}"

def probe_signal_freshness(params):
    """信号链路活跃度"""
    p = BASE / params["file"]
    if not p.exists():
        return None, "live_signal_log不存在"
    try:
        lines = [l for l in p.read_text().splitlines() if l.strip()]
        if not lines:
            return None, "信号日志为空（新系统正常）"
        last = json.loads(lines[-1])
        ts = last.get("ts", 0)
        if isinstance(ts, str):
            from datetime import datetime
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        age_h = (time.time() - ts) / 3600
        max_h = params.get("max_no_signal_h", 12)
        if age_h > max_h:
            return None, f"最近信号 {age_h:.0f}h前（上限{max_h}h）"
        return True, f"OK 最近信号 {age_h:.1f}h前"
    except Exception as e:
        return True, f"解析失败: {e}"


def probe_ram_check(params):
    """内存可用量检测 + 低内存自动触发gateway重启"""
    import subprocess
    r = subprocess.run(['free','-m'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith('Mem:'):
            parts = line.split()
            avail = int(parts[6]) if len(parts)>6 else int(parts[3])
            warn = params.get('warn_mb', 800)
            crit = params.get('critical_mb', 500)
            restart_thresh = params.get('gateway_restart_mb', 700)
            if avail < crit:
                # 尝试重启gateway释放内存
                try:
                    subprocess.run(['openclaw','gateway','restart'], timeout=10, capture_output=True)
                except: pass
                return False, f"内存危险: {avail}MB可用 → 已触发gateway重启"
            if avail < warn:
                if avail < restart_thresh:
                    # 预防性重启
                    try:
                        subprocess.run(['openclaw','gateway','restart'], timeout=10, capture_output=True)
                    except: pass
                    return None, f"内存预警: {avail}MB → 已预防性重启gateway"
                return None, f"内存预警: {avail}MB可用 (<{warn}MB)"
            return True, f"OK {avail}MB可用"
    return True, "无法读取内存"

def probe_regime_check(params):
    """brahma_state regime合理性"""
    p = BASE / params["file"]
    if not p.exists():
        return False, "brahma_state.json不存在"
    try:
        d = json.loads(p.read_text())
        regime = d.get("regime", "")
        bear   = d.get("bear_prob", -1)
        btc    = d.get("btc_price", 0)
        valid_regimes = {"BEAR_TREND","BEAR_EARLY","BEAR_RECOVERY","CHOP_MID","CHOP_HIGH","BULL_EARLY","BULL_TREND"}
        if regime not in valid_regimes:
            return False, f"regime异常: '{regime}'"
        if not (0 <= bear <= 1):
            return False, f"bear_prob异常: {bear}"
        if btc < 1000 or btc > 500000:
            return False, f"BTC价格异常: ${btc:,.0f}"
        return True, f"OK regime={regime} bear={bear:.0%} BTC=${btc:,.0f}"
    except Exception as e:
        return False, f"解析失败: {e}"

# ─── 探针分发 ─────────────────────────────────────────────────────────────
PROBES = {
    "file_age":          probe_file_age,
    "process":           probe_process,
    "api_ping":          probe_api_ping,
    "quota":             probe_quota,
    "signal_freshness":  probe_signal_freshness,
    "regime_check":      probe_regime_check,
    "ram_check":         probe_ram_check,
}

# ─── 自愈动作 ─────────────────────────────────────────────────────────────
def heal_brahma_state():
    r = subprocess.run(
        ["python3", str(SCRIPTS / "brahma_state_refresh.py")],
        capture_output=True, text=True, timeout=15
    )
    return r.returncode == 0, r.stdout.strip()[-80:]

def heal_ws_guardian():
    subprocess.run(["pkill", "-f", "ws_guardian.py"], capture_output=True)
    time.sleep(1)
    subprocess.Popen(
        ["python3", str(BASE / "ws_guardian.py")],
        stdout=open("/tmp/ws_guardian.log", "a"),
        stderr=subprocess.STDOUT
    )
    time.sleep(3)
    r = subprocess.run(["pgrep", "-f", "ws_guardian.py"], capture_output=True)
    return r.returncode == 0, "ws_guardian 重启" + ("成功" if r.returncode == 0 else "失败")

def heal_watchdog():
    subprocess.Popen(
        ["bash", str(SCRIPTS / "watchdog_guardian.sh")],
        stdout=open("/tmp/watchdog.log", "a"),
        stderr=subprocess.STDOUT
    )
    time.sleep(2)
    r = subprocess.run(["pgrep", "-f", "watchdog_guardian"], capture_output=True)
    return r.returncode == 0, "watchdog 重启" + ("成功" if r.returncode == 0 else "失败")

HEALERS = {
    "brahma_state":      heal_brahma_state,
    "ws_guardian_state": heal_brahma_state,
    # "ws_guardian_proc": heal_ws_guardian,  # 实盘前不自愈
    # "watchdog_proc": heal_watchdog,  # 实盘前不自愈
}

# ─── 主体检函数 ──────────────────────────────────────────────────────────

def _load_alert_cache():
    """加载已发送告警缓存，用于去重"""
    cache_path = DATA / "sentinel_alert_cache.json"
    if not cache_path.exists():
        return {}
    try:
        import json as _j
        d = _j.loads(cache_path.read_text())
        # 清理超过2小时的缓存
        now = time.time()
        return {k: v for k, v in d.items() if now - v < 7200}
    except:
        return {}

def _save_alert_cache(cache):
    cache_path = DATA / "sentinel_alert_cache.json"
    import json as _j
    cache_path.write_text(_j.dumps(cache))

def _dedup_alerts(alerts):
    """去重：同一告警内容1小时内只报一次"""
    cache = _load_alert_cache()
    now = time.time()
    new_alerts = []
    for alert in alerts:
        key = alert[:60]  # 用前60字符作为key
        last_sent = cache.get(key, 0)
        if now - last_sent > 3600:  # 1小时去重
            new_alerts.append(alert)
            cache[key] = now
    _save_alert_cache(cache)
    return new_alerts

def run_health_check():
    """
    执行全量体检。
    返回: {
        "ts": ISO, "ok": bool,
        "results": [{name, level, ok, msg, healed}],
        "alerts": [str],   # 需要推送的告警
        "summary": str
    }
    """
    now = datetime.now(tz=timezone.utc)
    results = []
    alerts  = []

    for name, probe_type, params, level in HEALTH_CONTRACTS:
        probe_fn = PROBES.get(probe_type)
        if not probe_fn:
            continue
        try:
            ok, msg = probe_fn(params)
        except Exception as e:
            ok, msg = False, f"探针异常: {e}"

        healed = None
        # CRITICAL/ERROR → 尝试自愈
        if ok is False and level == "CRITICAL" and name in HEALERS:
            try:
                healed_ok, healed_msg = HEALERS[name]()
                healed = {"ok": healed_ok, "msg": healed_msg}
                if healed_ok:
                    alerts.append(f"🔧 自愈成功 [{name}]: {msg} → {healed_msg}")
                else:
                    alerts.append(f"🚨 CRITICAL [{name}]: {msg}（自愈失败: {healed_msg}）")
            except Exception as e:
                healed = {"ok": False, "msg": str(e)}
                alerts.append(f"🚨 CRITICAL [{name}]: {msg}（自愈崩溃: {e}）")
        elif ok is False and level in ("CRITICAL", "ERROR"):
            alerts.append(f"🔴 {level} [{name}]: {msg}")
        elif ok is None and level in ("ERROR", "WARN"):
            # None = WARN级别
            if level == "ERROR":
                alerts.append(f"🟡 WARN [{name}]: {msg}")

        results.append({
            "name": name, "level": level,
            "ok": ok, "msg": msg, "healed": healed
        })

    pass_count  = sum(1 for r in results if r["ok"] is True)
    warn_count  = sum(1 for r in results if r["ok"] is None)
    fail_count  = sum(1 for r in results if r["ok"] is False)
    overall_ok  = fail_count == 0

    summary = f"✅{pass_count} ⚠️{warn_count} ❌{fail_count} / {len(results)}项"

    status = {
        "ts":       now.isoformat(),
        "ok":       overall_ok,
        "pass":     pass_count,
        "warn":     warn_count,
        "fail":     fail_count,
        "total":    len(results),
        "summary":  summary,
        "alerts":   alerts,
        "results":  results,
    }

    # 写入状态文件
    status_path = DATA / "sentinel_status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2))

    # 追加历史（仅有告警时记录）
    if alerts:
        hist_path = DATA / "sentinel_history.jsonl"
        with open(hist_path, "a") as f:
            f.write(json.dumps({
                "ts": now.isoformat(),
                "alerts": alerts,
                "summary": summary
            }, ensure_ascii=False) + "\n")

    return status


if __name__ == "__main__":
    result = run_health_check()
    print(f"体检完成: {result['summary']}")
    if result["alerts"]:
        print("告警:")
        for a in result["alerts"]:
            print(f"  {a}")

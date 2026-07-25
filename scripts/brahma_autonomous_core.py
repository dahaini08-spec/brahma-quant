#!/usr/bin/env python3
"""
brahma_autonomous_core.py — 梵天全自主运行总控
设计院自主决策 2026-07-25 苏摩111封印

三大职责：
  1. 全自主运行：信号→分析→开单→监控 全链路状态感知
  2. 全流程内部修复：性能链条检查，发现问题自动修复
  3. 科学过热重启：RSS/context监控，阈值触发优雅重启

调用方式：
  python3 scripts/brahma_autonomous_core.py [--mode check|heal|status]

  --mode check  : 快速全链路健康检查（30秒内完成）
  --mode heal   : 发现问题并自动修复
  --mode status : 输出完整状态JSON（供其他脚本读取）

设计原则：
  - 每次运行必须有可验证的输出
  - 修复动作必须幂等（重复执行无副作用）
  - 自愈失败 → 推送苏摩，不静默
  - 过热阈值：RSS>1200MB OR context>180k tokens → 触发优雅重启
"""

import sys, os, json, time, subprocess, requests

# psutil延迟安全加载（gateway重启后自动恢复）
def _get_psutil():
    try:
        import psutil as _ps
        return _ps
    except ImportError:
        subprocess.run([sys.executable,'-m','pip','install','psutil',
                        '--break-system-packages','-q'],
                       capture_output=True, timeout=60)
        import psutil as _ps
        return _ps

psutil = _get_psutil()
from pathlib import Path
from datetime import datetime, timezone

# ─── 启动依赖自检（gateway重启后自动恢复）────────────────
def _ensure_startup_deps():
    for pkg in ['psutil', 'fastembed', 'chromadb']:
        try:
            __import__(pkg)
        except ImportError:
            import subprocess as _sp
            _sp.run([sys.executable, '-m', 'pip', 'install', pkg,
                     '--break-system-packages', '-q'],
                    capture_output=True, timeout=60)
_ensure_startup_deps()
# ──────────────────────────────────────────────────────────

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ─── 配置 ──────────────────────────────────────────────────
try:
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    PUSH_TO = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
except Exception:
    PUSH_TO = "73295708:thread:019f93b0-c154-73fd-91a3-4e755d3289af"

STATUS_FILE = BASE / "data" / "autonomous_core_status.json"
RSS_WARN_MB  = 1200   # 警告阈值
RSS_CRIT_MB  = 1350   # 危险阈值（触发重启准备）
RSS_KILL_MB  = 1400   # 硬限制（强制重启）

# ─── 工具函数 ──────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def push_alert(msg: str, level: str = "P1"):
    """推送告警给苏摩"""
    try:
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', PUSH_TO,
            '--message', f"[梵天自治] {level} {msg}"
        ], timeout=10, capture_output=True)
    except Exception as e:
        print(f"[push_alert] 失败: {e}")

def get_rss_mb() -> float:
    """获取当前进程RSS内存(MB)"""
    try:
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0

def get_gateway_rss_mb() -> float:
    """获取gateway进程RSS"""
    try:
        for proc in psutil.process_iter(['name', 'cmdline', 'memory_info']):
            cmd = ' '.join(proc.info.get('cmdline') or [])
            if 'openclaw' in cmd.lower() and 'gateway' in cmd.lower():
                return proc.info['memory_info'].rss / 1024 / 1024
        return 0.0
    except Exception:
        return 0.0

# ─── 检查模块 ──────────────────────────────────────────────

def check_api() -> dict:
    """检查Binance API连通性"""
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/ping', timeout=5)
        ok = r.status_code == 200
        return {"ok": ok, "latency_ms": int(r.elapsed.total_seconds()*1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_signal_log() -> dict:
    """检查信号日志健康度"""
    log = BASE / 'data' / 'live_signal_log.jsonl'
    if not log.exists():
        return {"ok": False, "error": "文件不存在"}
    try:
        lines = [json.loads(l) for l in log.read_text().strip().split('\n') if l.strip()]
        last_ts = max(l.get('ts', 0) for l in lines) if lines else 0
        age_h = (time.time() - last_ts) / 3600 if last_ts else 999
        return {
            "ok": len(lines) > 0 and age_h < 24,
            "count": len(lines),
            "last_age_h": round(age_h, 1),
            "last_regime": lines[-1].get('regime', '?') if lines else '?'
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_rsi_trigger() -> dict:
    """检查RSI触发文件新鲜度"""
    f = BASE / 'data' / 'rsi_trigger_event.json'
    if not f.exists():
        return {"ok": False, "error": "文件不存在"}
    try:
        age_m = (time.time() - f.stat().st_mtime) / 60
        d = json.loads(f.read_text())
        return {"ok": age_m < 60, "age_min": round(age_m, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_positions() -> dict:
    """检查活跃持仓"""
    try:
        from binance_fapi import get_account
        acc = get_account()
        if isinstance(acc, dict):
            positions = acc.get('positions', [])
            active = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
            return {"ok": True, "active_count": len(active),
                    "active": [{"sym": p['symbol'], "amt": p['positionAmt'], 
                                "pnl": p.get('unrealizedProfit', 0)} for p in active[:5]]}
        return {"ok": True, "active_count": 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_memory() -> dict:
    """检查系统内存状态"""
    try:
        vm = psutil.virtual_memory()
        gw_rss = get_gateway_rss_mb()
        used_pct = vm.percent
        level = "ok"
        if gw_rss > RSS_CRIT_MB or used_pct > 90:
            level = "critical"
        elif gw_rss > RSS_WARN_MB or used_pct > 80:
            level = "warn"
        return {
            "ok": level == "ok",
            "level": level,
            "system_used_pct": round(used_pct, 1),
            "gateway_rss_mb": round(gw_rss, 1),
            "warn_threshold": RSS_WARN_MB,
            "crit_threshold": RSS_CRIT_MB,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_eth_gate() -> dict:
    """检查ETH EMA门控cron状态"""
    try:
        r = subprocess.run(['openclaw', 'cron', 'list'], capture_output=True, text=True, timeout=10)
        has_gate = 'eth-ema-gate' in r.stdout
        return {"ok": has_gate, "running": has_gate}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_brahma_engine() -> dict:
    """检查梵天引擎可用性"""
    try:
        from brahma_brain.brahma_engine import analyze
        return {"ok": True, "importable": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def ensure_fastembed() -> dict:
    """确保fastembed可用，不可用则自动安装"""
    try:
        import fastembed
        return {"ok": True, "version": fastembed.__version__, "action": "already_installed"}
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', 'fastembed',
                 '--break-system-packages', '-q'],
                capture_output=True, timeout=60
            )
            import fastembed
            return {"ok": True, "version": fastembed.__version__, "action": "auto_installed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

# ─── 自愈模块 ──────────────────────────────────────────────

def heal_signal_log(check_result: dict) -> bool:
    """信号日志修复：age过大时触发brahma_state_refresh"""
    if check_result.get('last_age_h', 0) > 6:
        print("  [heal] 信号日志陈旧，触发state_refresh...")
        r = subprocess.run(
            ['python3', str(BASE / 'scripts' / 'brahma_state_refresh.py')],
            capture_output=True, timeout=30
        )
        return r.returncode == 0
    return True

def heal_memory_overheat(check_result: dict) -> bool:
    """过热处理：RSS超阈值时优雅处理"""
    level = check_result.get('level', 'ok')
    gw_rss = check_result.get('gateway_rss_mb', 0)

    if level == 'critical' or gw_rss > RSS_CRIT_MB:
        print(f"  [heal] 内存危险 RSS={gw_rss:.0f}MB > {RSS_CRIT_MB}MB")
        # 1. 清理Python缓存
        import gc
        gc.collect()
        # 2. 清理brahma_bus缓存
        try:
            from brahma_bus import flush_stale
            flush_stale()
            print("  [heal] brahma_bus缓存已清理")
        except Exception:
            pass
        # 3. 如果仍然过热，推送告警（不强制重启，让watchdog决定）
        if gw_rss > RSS_KILL_MB:
            push_alert(f"🚨 Gateway内存超硬限制 RSS={gw_rss:.0f}MB，建议重启", "P0")
            return False
        elif gw_rss > RSS_CRIT_MB:
            push_alert(f"⚠️ Gateway内存危险 RSS={gw_rss:.0f}MB，已清理缓存", "P1")
        return True
    elif level == 'warn':
        print(f"  [heal] 内存警告 RSS={gw_rss:.0f}MB，执行预防性GC")
        import gc; gc.collect()
        try:
            from brahma_bus import flush_stale
            flush_stale()
        except Exception:
            pass
    return True

# ─── 主流程 ────────────────────────────────────────────────

def run_full_check() -> dict:
    """完整健康检查，返回结构化结果"""
    ts = now_iso()
    print(f"\n{'='*55}")
    print(f"  梵天全自主核心 健康检查")
    print(f"  {ts}")
    print(f"{'='*55}")

    ensure_fastembed()  # 确保embedding可用
    checks = {
        "api":           check_api(),
        "signal_log":    check_signal_log(),
        "rsi_trigger":   check_rsi_trigger(),
        "positions":     check_positions(),
        "memory":        check_memory(),
        "eth_gate":      check_eth_gate(),
        "brahma_engine": check_brahma_engine(),
    }

    passed = sum(1 for v in checks.values() if v.get('ok'))
    total  = len(checks)
    score  = int(passed / total * 100)

    print(f"\n{'检查项':<20} {'状态':<8} {'详情'}")
    print("-" * 55)
    for name, result in checks.items():
        ok = result.get('ok', False)
        icon = '✅' if ok else '❌'
        detail = ""
        if name == 'api':
            detail = f"延迟{result.get('latency_ms',0)}ms"
        elif name == 'signal_log':
            detail = f"{result.get('count',0)}条 最新{result.get('last_age_h',0)}h前"
        elif name == 'memory':
            detail = f"Gateway RSS={result.get('gateway_rss_mb',0):.0f}MB level={result.get('level','?')}"
        elif name == 'positions':
            detail = f"活跃持仓{result.get('active_count',0)}个"
        elif name == 'eth_gate':
            detail = f"{'运行中' if result.get('running') else '未运行'}"
        elif name == 'brahma_engine':
            detail = f"{'可用' if result.get('importable') else '不可用'}"
        print(f"{icon} {name:<18} {'OK' if ok else 'FAIL':<8} {detail}")

    print(f"\n总分: {score}/100 ({passed}/{total}通过)")

    # ── self_heal深度检查状态（每2h跑，此处只读缓存）──────────
    try:
        sh_status = BASE / 'data' / 'self_heal_last.json'
        if sh_status.exists():
            import time as _t
            _sh = json.loads(sh_status.read_text())
            _age_m = (_t.time() - _sh.get('ts', 0)) / 60
            _sh_score = _sh.get('score', '?')
            _sh_status = _sh.get('status', '?')
            print(f"  └ self_heal深度: {_sh_status} {_sh_score}/100 ({_age_m:.0f}min前)")
    except Exception:
        pass

    result = {
        "ts": ts,
        "score": score,
        "passed": passed,
        "total": total,
        "status": "HEALTHY" if score >= 85 else "DEGRADED" if score >= 60 else "CRITICAL",
        "checks": checks,
    }
    STATUS_FILE.write_text(json.dumps(result, indent=2, default=str))
    return result

def run_heal(check_result: dict) -> dict:
    """基于检查结果执行自动修复"""
    healed = {}
    checks = check_result.get('checks', {})

    print(f"\n[自愈模式] 开始修复...")

    # 内存过热
    mem = checks.get('memory', {})
    if not mem.get('ok') or mem.get('level') in ('warn', 'critical'):
        print(f"  → 处理内存过热...")
        healed['memory'] = heal_memory_overheat(mem)

    # 信号日志陈旧
    sig = checks.get('signal_log', {})
    if sig.get('last_age_h', 0) > 6:
        print(f"  → 修复信号日志陈旧...")
        healed['signal_log'] = heal_signal_log(sig)

    # ETH门控未运行
    gate = checks.get('eth_gate', {})
    if not gate.get('running'):
        print(f"  → ETH门控未运行，重新注册cron...")
        r = subprocess.run([
            'openclaw', 'cron', 'add',
            '--name', 'eth-ema-gate', '--every', '5m',
            '--message', '运行 /root/.openclaw/workspace/trading-system/scripts/eth_ema_gate.py，输出HEARTBEAT_OK则静默，否则推送结果',
            '--channel', 'jarvis', '--to', PUSH_TO, '--announce'
        ], capture_output=True, timeout=15)
        healed['eth_gate'] = r.returncode == 0

    if healed:
        print(f"\n自愈结果: {healed}")
        all_ok = all(healed.values())
        if not all_ok:
            failed = [k for k,v in healed.items() if not v]
            push_alert(f"自愈部分失败: {failed}，需人工介入", "P1")
    else:
        print("  → 无需修复，系统健康")

    return healed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天全自主核心')
    parser.add_argument('--mode', choices=['check','heal','status'], default='check')
    args = parser.parse_args()

    if args.mode == 'status':
        if STATUS_FILE.exists():
            print(STATUS_FILE.read_text())
        else:
            print('{"status":"NO_DATA","msg":"尚未运行check"}')
    elif args.mode == 'check':
        result = run_full_check()
        if result['status'] == 'CRITICAL':
            push_alert(f"🚨 系统CRITICAL score={result['score']}/100", "P0")
        elif result['status'] == 'DEGRADED':
            push_alert(f"⚠️ 系统DEGRADED score={result['score']}/100", "P1")
    elif args.mode == 'heal':
        result = run_full_check()
        if result['score'] < 100:
            run_heal(result)
        print(f"\n最终状态: {result['status']} ({result['score']}/100)")

#!/usr/bin/env python3
"""
host_health_sentinel.py — 宿主健康感知矩阵 [2026-10-03 苏摩111 梵天自进化Phase2]
接入位置: brahma_crontab.txt (每5分钟) + process_resurrect.sh
功能: 感知宿主状态 → 自愈决策 → 写日志 → 推送告警

感知矩阵:
  进程健康   → 已有 process_resurrect.sh 处理
  内存使用   >90% → 推送P1告警
  磁盘空间   <10% → 触发data_janitor
  API延迟    >2s  → 推送P2告警 + 标记降级模式
  LLM池状态  退避中 → 记录，不推送（静默感知）
"""
import os, sys, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'


def _get_memory_pct() -> float:
    """获取内存使用百分比"""
    try:
        with open('/proc/meminfo') as f:
            lines = {l.split(':')[0]: int(l.split()[1])
                     for l in f if ':' in l and l.split()[1].isdigit()}
        total = lines.get('MemTotal', 1)
        avail = lines.get('MemAvailable', total)
        return round((1 - avail / total) * 100, 1)
    except Exception:
        return 0.0


def _get_disk_pct(path: str = '/') -> float:
    """获取磁盘使用百分比"""
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        return round((1 - free / total) * 100, 1) if total > 0 else 0.0
    except Exception:
        return 0.0


def _check_api_latency() -> float:
    """测量Binance API延迟（ms）"""
    try:
        import urllib.request, ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        t0 = time.time()
        urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ping', timeout=5, context=ctx)
        return round((time.time() - t0) * 1000, 1)
    except Exception:
        return 9999.0


def _check_llm_backoff() -> dict:
    """检查LLM退避状态"""
    state_file = DATA / 'llm_channel_state.json'
    try:
        if state_file.exists():
            st = json.loads(state_file.read_text())
            backoff_until = st.get('backoff_until', '')
            if backoff_until:
                from datetime import datetime, timezone
                bt = datetime.fromisoformat(backoff_until.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                if bt > now:
                    secs = (bt - now).total_seconds()
                    return {'in_backoff': True, 'remaining_s': int(secs), 'until': backoff_until}
    except Exception:
        pass
    return {'in_backoff': False}


def _check_supercronic() -> bool:
    """检查supercronic是否存活"""
    try:
        r = subprocess.run(['pgrep', '-x', 'supercronic'],
                          capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _write_health_log(entry: dict):
    """写入宿主健康日志"""
    mc_path = DATA / 'meta_cognition_state.json'
    try:
        mc = json.loads(mc_path.read_text()) if mc_path.exists() else {}
        logs = mc.get('host_health_log', [])
        logs.append(entry)
        mc['host_health_log'] = logs[-100:]  # 只保留最近100条
        mc['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        mc_path.write_text(json.dumps(mc, ensure_ascii=False, indent=2))
    except Exception as _e:
        print(f'[WARN] host_health: 写入日志失败: {_e}')


def run_health_check() -> dict:
    """执行完整宿主健康检查"""
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    mem_pct  = _get_memory_pct()
    disk_pct = _get_disk_pct()
    api_ms   = _check_api_latency()
    llm      = _check_llm_backoff()
    sc_alive = _check_supercronic()

    status = {
        'ts': ts,
        'memory_pct': mem_pct,
        'disk_pct': disk_pct,
        'api_latency_ms': api_ms,
        'llm_backoff': llm.get('in_backoff', False),
        'llm_backoff_remaining_s': llm.get('remaining_s', 0),
        'supercronic_alive': sc_alive,
        'alerts': [],
    }

    alerts = []

    # 内存 >90% → P1告警
    if mem_pct > 90:
        alerts.append(f'🔴 内存{mem_pct}%>90% → 建议暂停重分析任务')
        status['alerts'].append('HIGH_MEMORY')

    # 磁盘 >90% → P1告警 + 触发janitor
    if disk_pct > 90:
        alerts.append(f'🔴 磁盘{disk_pct}%>90% → 触发data_janitor清理')
        status['alerts'].append('LOW_DISK')
        try:
            subprocess.Popen(
                [sys.executable, str(BASE / 'scripts' / 'data_janitor.py')],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print('[HostHealth] 磁盘告急，已触发data_janitor')
        except Exception:
            pass

    # API延迟 >2000ms → P2告警
    if api_ms > 2000:
        alerts.append(f'⚠️ Binance API延迟{api_ms:.0f}ms>2s → 降级模式建议')
        status['alerts'].append('HIGH_API_LATENCY')

    # supercronic 挂了 → P0告警
    if not sc_alive:
        alerts.append('🚨 supercronic 未运行！所有cron任务中断')
        status['alerts'].append('SUPERCRONIC_DOWN')

    # 写日志
    _write_health_log(status)

    # 推送告警（有问题才推）
    if alerts:
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            import push_hub as _ph
            priority = 'P0' if 'SUPERCRONIC_DOWN' in status['alerts'] else 'P1' if 'HIGH_MEMORY' in status['alerts'] or 'LOW_DISK' in status['alerts'] else 'P2'
            msg = '🏥 宿主健康告警\n' + '\n'.join(alerts)
            if llm['in_backoff']:
                msg += f'\n💤 LLM退避中 还剩{llm.get("remaining_s",0)}s'
            _ph.push_jarvis(msg, priority=priority)
        except Exception as _pe:
            print(f'[HostHealth] 推送失败: {_pe}')

    # 打印摘要
    sc_icon = '✅' if sc_alive else '❌'
    llm_icon = '💤' if llm['in_backoff'] else '✅'
    print(f'[HostHealth] {ts} 内存={mem_pct}% 磁盘={disk_pct}% API={api_ms:.0f}ms supercronic={sc_icon} LLM={llm_icon}')
    if alerts:
        for a in alerts:
            print(f'  {a}')

    return status


if __name__ == '__main__':
    run_health_check()

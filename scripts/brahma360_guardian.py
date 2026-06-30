#!/usr/bin/env python3
"""
# ── 全局内存优化（工程师建议 P1）──
import gc as _gc_mod
import psutil as _psutil_mod
_gc_mod.enable()
_gc_mod.set_threshold(700, 10, 10)

def _check_and_gc():
    _gc_mod.collect()
    if _psutil_mod.virtual_memory().percent > 75:
        _gc_mod.collect(2)
# ─────────────────────────────────────
# brahma360_guardian.py — 梵天360守护系统 v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 设计院 2026-05-30 重构
"""

# ── signal_utils 标准读取（2026-06-02 设计院Bug修复）────────────────────
def _load_clean_signals(hours=None, min_score=0, valid_only=False, unsettled_only=False):
    """标准化信号读取：避免历史残留信号污染统计/广播。"""
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.join(_o.path.dirname(__file__)))
    try:
        from signal_utils import load_signals as _su
        return _su(hours=hours or 8760, min_score=min_score, valid_only=valid_only,
                   unsettled_only=unsettled_only)
    except Exception:
        from pathlib import Path as _P
        import json as _j
        _f = _P(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
        if not _f.exists(): return []
        _all = [_j.loads(l) for l in open(_f) if l.strip()]
        if valid_only: _all = [l for l in _all if l.get('valid')]
        if unsettled_only: _all = [l for l in _all if not l.get('settled')]
        return _all
# ────────────────────────────────────────────────────────────────────────


# 核心原则：梵天360是唯一指挥中心
#   - 所有防御机制通过360统一协调
#   - 指令总线集成：人工指令立即同步到所有子系统
#   - 自愈链：Gateway重启后自动拉起所有关键进程
#   - 虚报过滤：批量结算/重启中断不触发报警

# 职责分层：
#   L0 (每5分钟)  生命体征：进程/RAM/心跳/指令总线同步
#   L2 (每6小时)  深度诊断：PF趋势/评分校准/cron健康

import json, os, time, sys, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE    = Path(__file__).parent.parent
SCRIPTS = BASE / 'scripts'
DATA    = BASE / 'data'
CST     = timezone(timedelta(hours=8))

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(BASE / 'brahma_brain'))

def _check_stat_health() -> dict:
    """统计健康：WR/触发率是否正常（每次L0都检查，不只是L2）"""
    try:
        log_path = DATA / 'live_signal_log.jsonl'
        if not log_path.exists():
            return {'ok': True, 'warn': ''}
        with open(log_path) as f:
            logs = [json.loads(l) for l in f if l.strip()]

        # 最近7日真实结算
        cutoff = time.time() - 7 * 86400
        recent = []
        for l in logs:
            ts_str = l.get('ts', '')
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(ts_str.replace('Z','+00:00')).timestamp()
            except: continue
            if ts >= cutoff and l.get('outcome') in ('TP1','SL'):
                recent.append(l)

        if len(recent) < 5:
            return {'ok': True, 'warn': '', 'note': f'近7日结算{len(recent)}条，样本不足'}

        tp = sum(1 for l in recent if l['outcome']=='TP1')
        wr = tp / len(recent)

        # WR连续下滑预警（低于40%）
        if wr < 0.40:
            return {'ok': False, 'warn': f'统计预警: 近7日WR={wr:.0%}({len(recent)}条)，低于40%警戒线'}

        return {'ok': True, 'warn': '', 'note': f'近7日WR={wr:.0%}(n={len(recent)})'}
    except Exception as e:
        return {'ok': True, 'warn': '', 'note': str(e)}


def _now_cst(): return datetime.now(CST).strftime('%Y-%m-%d %H:%M CST')

def _ts_age(ts) -> int:
    try: return int(time.time() - float(ts))
    except: return 99999


# ═══════════════════════════════════════════════════════════
# 核心改进1: 指令总线同步
# ═══════════════════════════════════════════════════════════

def sync_command_register() -> dict:
    """
    读取指令总线，返回当前所有激活指令。
    所有子系统的行为都从这里开始。
    """
    try:
        from command_register import _load, _is_active
        reg = _load()
        now = time.time()
        active = {k: v for k, v in reg.items() if _is_active(v, now)}
        return active
    except:
        return {}


# ═══════════════════════════════════════════════════════════
# 核心改进2: 自愈链
# ═══════════════════════════════════════════════════════════

def heal_ws_guardian() -> str:
    """pgrep双重确认+自动重启 [v17.2 自愈增强]"""
    WD  = str(BASE)
    LOG = str(BASE / 'logs' / 'ws_guardian.log')
    alive = subprocess.run(['pgrep','-f','python3.*ws_guardian.py'], capture_output=True).returncode == 0
    if alive:
        return '✅ ws_guardian 进程存活'
    try:
        subprocess.Popen(f'nohup python3 {WD}/ws_guardian.py >> {LOG} 2>&1 &', shell=True, cwd=WD)
        return '🔄 ws_guardian 宕机 → 已自动重启'
    except Exception as e:
        return f'❌ ws_guardian重启失败: {e}'


def heal_watchdog() -> str:
    """检测watchdog_guardian，宕机自动重启 [v17.2 新增]"""
    WD  = str(BASE)
    LOG = str(BASE / 'logs' / 'watchdog.log')
    alive = subprocess.run(['pgrep','-f','watchdog_guardian.sh'], capture_output=True).returncode == 0
    if alive:
        return '✅ watchdog 进程存活'
    try:
        subprocess.Popen(f'nohup bash {WD}/scripts/watchdog_guardian.sh >> {LOG} 2>&1 &', shell=True, cwd=WD)
        return '🔄 watchdog 宕机 → 已自动重启'
    except Exception as e:
        return f'❌ watchdog重启失败: {e}'


def post_restart_integrity_check() -> str:
    """Gateway重启后完整性自检：T01(brahma_analyze) + T04(dry_run)
    只在 /tmp/brahma360_last_restart.ts 记录的重启时间后30s~5min内触发一次。"""
    FLAG = Path('/tmp/brahma360_restart_checked.flag')
    RESTART_TS = Path('/tmp/brahma_gateway_restart.ts')

    # 没有重启记录 → 跳过
    if not RESTART_TS.exists():
        return ''

    try:
        restart_ts = float(RESTART_TS.read_text().strip())
    except:
        return ''

    elapsed = time.time() - restart_ts

    # 重启后30s~5min内，且未检测过
    if not (30 <= elapsed <= 300):
        if elapsed > 300 and FLAG.exists():
            FLAG.unlink(missing_ok=True)   # 超5分钟，清除flag供下次使用
        return ''

    if FLAG.exists():
        return ''  # 已检测过

    # 执行T01轻量检测
    try:
        r = subprocess.run(
            ['python3', 'brahma_analyze.py', 'BTCUSDT', '--json'],
            capture_output=True, text=True, timeout=30, cwd=str(BASE)
        )
        ok = 'score' in r.stdout
        FLAG.touch()
        RESTART_TS.unlink(missing_ok=True)
        status = '✅ 重启后自检PASS (brahma_analyze正常)' if ok else '⚠️ 重启后自检: brahma_analyze异常'
        return status
    except Exception as e:
        return f'⚠️ 重启后自检异常: {e}'


def heal_circuit_breaker(active_cmds: dict) -> str:
    """
    熔断器自愈：
    1. 如果有人工覆盖指令 → 自动解除误触发熔断
    2. 检查是否是废数据触发 → 自动解除
    """
    try:
        from account_circuit_breaker import _load_cb, _save_cb, _get_nav

        # 有人工指令 → 确保熔断已解除
        if 'circuit_breaker' in active_cmds or 'ALL' in active_cmds:
            cb = _load_cb()
            changed = False
            if cb.get('l1'): cb['l1'] = False; changed = True
            if cb.get('l2'): cb['l2'] = False; changed = True
            if changed:
                _save_cb(cb)
                return '✅ 按指令总线自动解除熔断'
            return '✅ 指令覆盖中，熔断锁定解除'

        # 自动校验：回撤<5%且有误触发标记 → 自动解除
        from account_circuit_breaker import auto_reset_on_restart
        auto_reset_on_restart()

        cb = _load_cb()
        if cb.get('l1') or cb.get('l2') or cb.get('l3'):
            return f'⚠️ 熔断激活 L1={cb.get("l1")} L2={cb.get("l2")} L3={cb.get("l3")}'
        return '✅ 正常'
    except Exception as e:
        return f'? ({e})'


# ═══════════════════════════════════════════════════════════
# 核心改进3: 虚报过滤
# ═══════════════════════════════════════════════════════════

def check_cron_health() -> dict:
    """
    cron健康检查，过滤掉「Gateway重启中断」的假error
    只报告脚本本身失败的真实error
    """
    try:
        result = subprocess.run(['openclaw', 'cron', 'list'],
                                capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split('\n')
        errors = []
        for l in lines:
            if 'error' not in l: continue
            # 只统计真实错误（不是重启中断）
            cid = l.split()[0] if l.split() else ''
            if not cid or len(cid) < 10: continue
            # 快速查最近一次run原因
            try:
                r = subprocess.run(['openclaw', 'cron', 'runs', '--id', cid],
                                   capture_output=True, text=True, timeout=5)
                run_data = json.loads(r.stdout)
                last = run_data.get('entries', [{}])[0]
                err_msg = last.get('error', '')
                # Gateway重启中断 → 过滤
                if 'gateway restart' in err_msg.lower() or 'draining' in err_msg.lower():
                    continue
                name = l.split()[1] if len(l.split()) > 1 else cid[:8]
                errors.append(f'{name}: {err_msg[:50]}')
            except:
                pass

        return {'real_errors': errors, 'ok': len(errors) == 0,
                'warn': f'真实cron错误: {errors}' if errors else ''}
    except Exception as e:
        return {'ok': True, 'warn': '', 'real_errors': [], 'note': str(e)}


# ═══════════════════════════════════════════════════════════
# L0 主逻辑
# ═══════════════════════════════════════════════════════════

def run():
    now = time.time()
    issues = []

    # 第一步：同步指令总线
    active_cmds = sync_command_register()

    # 第二步：自愈链
    ws_status     = heal_ws_guardian()
    wd_status     = heal_watchdog()
    cb_status     = heal_circuit_breaker(active_cmds)

    # 第二步+: Gateway重启后完整性自检（30s~5min窗口触发一次）
    restart_check = post_restart_integrity_check()
    if restart_check:
        print(f'[360自检] {restart_check}')
        if '⚠️' in restart_check:
            issues.append(f'重启自检: {restart_check}')

    # 第三步：基础指标
    # RAM
    mem = open('/proc/meminfo').read().split('\n')
    total = int([l for l in mem if 'MemTotal' in l][0].split()[1]) // 1024
    avail = int([l for l in mem if 'MemAvailable' in l][0].split()[1]) // 1024
    used  = total - avail
    if avail < 300:
        issues.append(f'RAM危险: 可用{avail}MB < 300MB')

    # 持仓
    try:
        bs = json.loads((DATA / 'brahma_state.json').read_text())
        pos_count = len(bs.get('positions', []))
    except:
        pos_count = 0

    # 磁盘
    st = os.statvfs(str(BASE))
    free_gb = st.f_bavail * st.f_frsize / 1e9
    if free_gb < 2.0:
        issues.append(f'磁盘不足: {free_gb:.1f}GB')

    # ws_guardian 告警
    if '❌' in ws_status or ('⚠️' in ws_status and pos_count > 0):
        issues.append(f'ws_guardian: {ws_status}')

    # 熔断器告警（真实熔断，非误触发）
    if '⚠️ 熔断激活' in cb_status:
        issues.append(f'熔断器: {cb_status}')

    # ── 统计健康检查（核心：系统能不能赚钱）────────────────
    stat_health = _check_stat_health()
    if stat_health.get('warn'):
        issues.append(stat_health['warn'])

    # 写状态文件
    state = {
        'ts': int(now), 'at': _now_cst(),
        'ok': len(issues) == 0,
        'ram': f'{used}MB/{total}MB 可用{avail}MB',
        'ws': ws_status,
        'circuit_breaker': cb_status,
        'positions': pos_count,
        'disk': f'{free_gb:.1f}GB',
        'active_commands': list(active_cmds.keys()),
        'issues': issues,
    }
    tmp = '/tmp/brahma360_ok.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, '/tmp/brahma360_ok.json')

    # 输出
    if not issues:
        print('HEARTBEAT_OK')
        return state

    print(f'⚠️ 梵天360 L0告警 | {_now_cst()}')
    for i in issues:
        print(f'  🚨 {i}')
    print(f'  RAM={avail}MB可用 | 持仓={pos_count} | 磁盘={free_gb:.1f}GB')
    if active_cmds:
        print(f'  📋 活跃指令: {list(active_cmds.keys())}')
    return state


# ── 显式内存释放 ──
try:
    import gc as _gc
    _check_and_gc()
except Exception:
    pass

if __name__ == '__main__':
    run()
    # 集成自愈层：5类🚨故障检测+自愈+告警
    try:
        import brahma360_self_heal as _sh
        _sh.run()
    except Exception as _e:
        print(f'[self_heal] 执行异常: {_e}')

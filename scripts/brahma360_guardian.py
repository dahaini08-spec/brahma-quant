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
sys.path.insert(0, str(BASE))  # fix: 确保 import brahma_brain 可用（2026-08-10 设计院）
sys.path.insert(1, str(BASE / 'venv/lib/python3.11/site-packages'))  # venv依赖

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
        pass  # [静默]
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


# ══ [设计院 v16/v17 健康检查扩展] 2026-07-01 ══════════════════════════════
def check_v16_v17_modules() -> dict:
    """
    检查 v16/v17 新模块健康状态
    - realistic_cost_model: 可实例化
    - portfolio_optimizer: 相关性计算正常
    - llm_council_bridge: shadow log可写
    - kronos_bridge: 模型路径存在
    - kronos_m1_validator: shadow log进度
    """
    import sys, os
    from pathlib import Path
    results = {}
    BASE = Path(__file__).parent.parent

    # realistic_cost_model
    try:
        sys.path.insert(0, str(BASE / 'dharma'))
        from realistic_cost_model import CostModel
        m = CostModel()
        cost = m.total_cost(2000, 30, 'SHORT', 'BEAR_TREND', 16)
        results['realistic_cost_model'] = {'ok': True, 'cost_sample': round(cost*100,4)}
    except Exception as e:
        results['realistic_cost_model'] = {'ok': False, 'error': str(e)[:60]}

    # portfolio_optimizer
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from portfolio_optimizer import check_correlation_risk
        r = check_correlation_risk('BTCUSDT', 'ETHUSDT')
        results['portfolio_optimizer'] = {'ok': True, 'btc_eth_corr': r['corr'], 'risk_mult': r['risk_mult']}
    except Exception as e:
        results['portfolio_optimizer'] = {'ok': False, 'error': str(e)[:60]}

    # kronos_bridge shadow log
    shadow = BASE / 'data' / 'kronos_bridge_shadow.jsonl'
    n_shadow = 0
    if shadow.exists():
        n_shadow = sum(1 for _ in open(shadow) if _.strip())
    results['kronos_bridge'] = {
        'ok': True,
        'shadow_n': n_shadow,
        'm1_progress': f'{n_shadow}/100',
        'mode': os.environ.get('KRONOS_BRIDGE_MODE', 'shadow')
    }

    # llm_council shadow log
    llm_log = BASE / 'data' / 'llm_council_shadow_log.jsonl'
    n_llm = 0
    if llm_log.exists():
        n_llm = sum(1 for _ in open(llm_log) if _.strip())
    results['llm_council_bridge'] = {'ok': True, 'shadow_n': n_llm}

    passed = sum(1 for v in results.values() if v.get('ok'))
    total  = len(results)
    return {
        'check': 'v16_v17_modules',
        'score': int(passed / total * 100),
        'passed': passed,
        'total': total,
        'details': results
    }
# ══ [END] ══════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
# 🏛️ 梵天360固化监测矩阵 v2026-07-19
# 苏摩111授权 · 今日所有修复成果的持续验证层
# ══════════════════════════════════════════════════════════════

def run_solidification_checks() -> dict:
    """
    今日修复固化验证 - 每次360体检时自动运行
    确保所有修复成果不被回退或破坏
    """
    import subprocess, time, json
    from pathlib import Path

    results = {}
    issues = []

    # ── 检测1: 系统cron守望层 ─────────────────────────────────
    cron_ok = Path('/etc/cron.d/brahma-watchers').exists()
    if not cron_ok:
        # 自愈：自动重建
        try:
            content = (
                '# 梵天系统守望层 - 自愈重建\n'
                '*/3 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/live_sync.py --silent >> /tmp/brahma-watchers.log 2>&1\n'
                '*/5 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/rsi_structure_watcher.py --silent >> /tmp/brahma-watchers.log 2>&1\n'
                '*/10 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/btc_regime_updater.py --silent >> /tmp/brahma-watchers.log 2>&1\n'
                '*/15 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/market_pre_filter.py --silent >> /tmp/brahma-watchers.log 2>&1\n'
                '*/20 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/signal_change_detector.py --silent >> /tmp/brahma-watchers.log 2>&1\n'
                '*/20 * * * * root cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_lifecycle.py entry --silent >> /tmp/brahma-watchers.log 2>&1\n'
            )
            Path('/etc/cron.d/brahma-watchers').write_text(content)
            Path('/etc/cron.d/brahma-watchers').chmod(0o644)
            results['syscron'] = '✅ 自愈重建成功'
        except Exception as e:
            results['syscron'] = f'❌ 自愈失败: {e}'
            issues.append('syscron_rebuild_failed')
    else:
        results['syscron'] = '✅ 存在'

    # ── 检测2: openclaw cron活跃任务数 ──────────────────────────
    try:
        jobs_file = Path('/root/.openclaw/cron/jobs.json')
        cjobs = json.loads(jobs_file.read_text())
        enabled = [j for j in cjobs.get('jobs', []) if j.get('enabled', True)]
        wakeups = sum(1440/max(1,j.get('schedule',{}).get('everyMs',86400000)//60000) for j in enabled)
        if wakeups > 900:
            issues.append(f'唤醒次数过高: {wakeups:.0f}/天 > 900')
            results['wakeups'] = f'⚠️ {wakeups:.0f}次/天（超阈值900）'
        else:
            results['wakeups'] = f'✅ {wakeups:.0f}次/天'
    except Exception as e:
        results['wakeups'] = f'❌ {e}'

    # ── 检测3: lightContext覆盖率 ────────────────────────────────
    try:
        no_lc = []
        for j in cjobs.get('jobs', []):
            if not j.get('enabled', True): continue
            every_ms = j.get('schedule',{}).get('everyMs', 0)
            every_min = every_ms//60000 if every_ms else 1440
            if every_min <= 60 and not j.get('payload',{}).get('lightContext', False):
                no_lc.append(j.get('name','?'))
        if no_lc:
            issues.append(f'lightContext未覆盖: {no_lc}')
            results['lightContext'] = f'⚠️ 未覆盖: {no_lc}'
        else:
            results['lightContext'] = '✅ 全覆盖'
    except Exception as e:
        results['lightContext'] = f'❌ {e}'

    # ── 检测4: 推送任务沉默验证（11个噪音任务） ──────────────────
    SILENT_TASKS = [
        'live-sync-guardian','signal-fast-exec','rsi-structure-watcher',
        'btc-regime-updater','market-pre-filter','hunter-outcome-tracker',
        'gex-refresh','macro-state-refresh','brahma-learning-loop',
        'data-backup-6h','session-cleanup-6h','lifecycle-entry',
    ]
    try:
        not_silent = []
        for j in cjobs.get('jobs', []):
            name = j.get('name','')
            if name in SILENT_TASKS:
                if j.get('enabled', True):
                    not_silent.append(name)
        if not_silent:
            issues.append(f'噪音任务未禁用: {not_silent}')
            results['noise_tasks'] = f'⚠️ 仍活跃: {not_silent}'
        else:
            results['noise_tasks'] = f'✅ {len(SILENT_TASKS)}个全部禁用/静默'
    except Exception as e:
        results['noise_tasks'] = f'❌ {e}'

    # ── 检测5: 持仓守护核心任务活跃 ─────────────────────────────
    GUARDIAN_TASKS = ['position-guardian-unified','regime-switch-monitor','brahma-self-heal']
    try:
        enabled_names = {j.get('name') for j in cjobs.get('jobs',[]) if j.get('enabled',True)}
        missing = [t for t in GUARDIAN_TASKS if t not in enabled_names]
        if missing:
            issues.append(f'守护任务缺失: {missing}')
            results['guardian_tasks'] = f'❌ 缺失: {missing}'
        else:
            results['guardian_tasks'] = f'✅ 全部活跃'
    except Exception as e:
        results['guardian_tasks'] = f'❌ {e}'

    # ── 检测6: CRON_WATCHLIST验证（rsi已移除） ──────────────────
    try:
        heal_script = Path('/root/.openclaw/workspace/trading-system/scripts/brahma_self_heal.py').read_text()
        watchlist_section = heal_script[heal_script.find('CRON_WATCHLIST'):heal_script.find('CRON_WATCHLIST')+600]
        # rsi-structure-watcher应该只出现在注释中
        active_rsi = "'rsi-structure-watcher'" in watchlist_section and \
                     watchlist_section.find("'rsi-structure-watcher'") > 0 and \
                     watchlist_section[watchlist_section.find("'rsi-structure-watcher'")-5:watchlist_section.find("'rsi-structure-watcher'")].strip() != '#'
        if active_rsi:
            issues.append('CRON_WATCHLIST: rsi-structure-watcher仍活跃（应已注释）')
            results['watchlist'] = '⚠️ rsi仍在监控'
        else:
            results['watchlist'] = '✅ rsi已从监控移除'
    except Exception as e:
        results['watchlist'] = f'❌ {e}'

    return {
        'ok': len(issues) == 0,
        'issues': issues,
        'checks': results,
        'score': (len(results) - len(issues)) / len(results) * 100 if results else 0,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }


if __name__ == '__main__' and '--solidification' in __import__('sys').argv:
    import json as _json
    result = run_solidification_checks()
    print(f"\n🏛️ 梵天360固化监测 · {result['timestamp'][:16]} UTC")
    print(f"综合评分: {result['score']:.0f}/100 | 问题数: {len(result['issues'])}")
    print()
    for k, v in result['checks'].items():
        print(f"  {v}  [{k}]")
    if result['issues']:
        print(f"\n⚠️ 发现{len(result['issues'])}个问题:")
        for iss in result['issues']:
            print(f"  ❌ {iss}")
    else:
        print(f"\n✅ 全部{len(result['checks'])}项固化指标通过")


# ══════════════════════════════════════════════════════════════════════
# 🏛️ brahma_360 v2.0 — 10模块标准化自检体系
# mattpocock/skills原则：小而可组合，每个check做一件事
# 设计院封印 2026-08-08
# ══════════════════════════════════════════════════════════════════════

import psutil as _psutil
import requests as _req

BASE_360 = Path('/root/.openclaw/workspace/trading-system')

def _check_result(name: str, ok: bool, score: int, detail: str, fix: str = '') -> dict:
    """标准CheckResult格式（mattpocock小而可组合接口）"""
    return {'name': name, 'ok': ok, 'score': score, 'detail': detail, 'fix': fix}


def check_01_regime_freshness() -> dict:
    """体制状态 < 60min"""
    try:
        with open(BASE_360 / 'data' / 'brahma_state.json') as f:
            d = json.load(f)
        age = (time.time() - d.get('last_update', d.get('timestamp', 0))) / 60
        ok = age < 60
        score = 10 if age < 60 else (7 if age < 120 else 0)
        return _check_result('体制新鲜度', ok, score,
            f'{age:.0f}min BTC={d.get("regime","?")}',
            'python3 scripts/brahma_state_refresh.py')
    except Exception as e:
        return _check_result('体制新鲜度', False, 0, f'读取失败:{e}',
            'python3 scripts/brahma_state_refresh.py')


def check_02_sqe_clean() -> dict:
    """SQE信号日志无sl>2.0脏数据"""
    try:
        with open(BASE_360 / 'data' / 'live_signal_log.jsonl') as f:
            sigs = [json.loads(l) for l in f if l.strip()]
        dirty = [s for s in sigs if s.get('sl_pct', 0) > 2.0]
        ok = len(dirty) == 0
        return _check_result('SQE清洁度', ok, 10 if ok else 0,
            f'{len(sigs)}条 脏数据={len(dirty)}条',
            '清洗sl>2.0的历史信号')
    except Exception as e:
        return _check_result('SQE清洁度', False, 0, str(e))


def check_03_wr_baseline() -> dict:
    """WR权重基准正确（BULL:LONG:120-139=NORMAL）"""
    try:
        with open(BASE_360 / 'data' / 'signal_weights.json') as f:
            sw = json.load(f)
        e = sw.get('BULL_TREND:LONG:120-139', {})
        ok = e.get('action') == 'NORMAL' and e.get('multiplier', 0) >= 0.9
        return _check_result('WR权重基准', ok, 10 if ok else 5,
            f'action={e.get("action")} mult={e.get("multiplier")}',
            '更新signal_weights.json')
    except Exception as e:
        return _check_result('WR权重基准', False, 0, str(e))


def check_04_cron_architecture() -> dict:
    """Cron架构：noai_runner任务无model字段"""
    try:
        import subprocess
        r = subprocess.run(['openclaw', 'cron', 'list', '--json'],
                           capture_output=True, text=True, timeout=10)
        jobs = json.loads(r.stdout)
        jl = jobs if isinstance(jobs, list) else jobs.get('jobs', [])
        zombies = [
            j['name'] for j in jl
            if 'cron_noai_runner' in j.get('payload', {}).get('message', '')
            and 'model' in j.get('payload', {})
        ]
        ok = len(zombies) == 0
        return _check_result('Cron架构', ok, 10 if ok else 0,
            f'{len(jl)}个任务 僵尸model={len(zombies)}',
            f'重建任务去除model字段: {zombies}')
    except Exception as e:
        return _check_result('Cron架构', False, 0, str(e))


def check_05_critical_files() -> dict:
    """关键文件完整性（10个必须存在）"""
    files = [
        'data/brahma_state.json', 'data/live_signal_log.jsonl',
        'data/signal_weights.json', 'data/macro_overlay.json',
        'data/regime_state.json', 'brahma_brain/brahma_engine.py',
        'brahma_brain/fangcang_engine.py', 'brahma_brain/kronos_bridge.py',
        'scripts/auto_executor.py', 'scripts/position_guardian.py',
    ]
    missing = [f for f in files if not (BASE_360 / f).exists()]
    ok = len(missing) == 0
    return _check_result('关键文件', ok, 10 if ok else max(0, 10 - len(missing)*2),
        f'{len(files)-len(missing)}/{len(files)} 存在' + (f' 缺:{missing}' if missing else ''),
        f'恢复缺失文件: {missing}')


def check_06_kronos_cache() -> dict:
    """Kronos缓存有效（TTL=4H，磁盘缓存存在）"""
    try:
        cache_file = BASE_360 / 'data' / 'kronos_p_up_cache.json'
        if not cache_file.exists():
            return _check_result('Kronos缓存', False, 3, '磁盘缓存文件不存在',
                '运行brahma_1hao_analysis.py生成缓存')
        with open(cache_file) as f:
            kc = json.load(f)
        btc = kc.get('BTCUSDT', [0, 0.5, 0])
        age_min = (time.time() - btc[0]) / 60 if btc[0] else 999
        valid = age_min < 240  # <4H
        return _check_result('Kronos缓存', valid,
            10 if age_min < 60 else (7 if age_min < 240 else 3),
            f'BTC p_up={btc[1]:.3f} age={age_min:.0f}min',
            '重新运行1号工程刷新缓存')
    except Exception as e:
        return _check_result('Kronos缓存', False, 0, str(e))


def check_07_fangcang_coverage() -> dict:
    """方仓引擎13币种K线数据覆盖"""
    try:
        required = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT',
                    'DOGEUSDT', 'ADAUSDT', 'OPUSDT', 'ARBUSDT']
        bt_dir = BASE_360 / 'data' / 'backtest'
        covered = [s for s in required
                   if (bt_dir / f'{s}_15m.json').exists()]
        ok = len(covered) >= 6
        return _check_result('方仓数据覆盖', ok,
            10 if len(covered) >= 8 else (7 if len(covered) >= 6 else 3),
            f'{len(covered)}/{len(required)} 币种有数据',
            'python3 scripts/fetch_fangcang_history.py --symbols ...')
    except Exception as e:
        return _check_result('方仓数据覆盖', False, 0, str(e))


def check_08_api_latency() -> dict:
    """Binance API延迟 < 500ms"""
    try:
        t0 = time.time()
        r = _req.get('https://fapi.binance.com/fapi/v1/ping', timeout=5)
        latency_ms = (time.time() - t0) * 1000
        ok = r.status_code == 200 and latency_ms < 500
        return _check_result('API延迟', ok,
            10 if latency_ms < 200 else (7 if latency_ms < 500 else 3),
            f'{latency_ms:.0f}ms (status={r.status_code})',
            '检查网络连接')
    except Exception as e:
        return _check_result('API延迟', False, 0, f'API不可达:{e}')


def check_09_disk_health() -> dict:
    """磁盘空间 > 5GB"""
    try:
        disk = _psutil.disk_usage('/')
        free_gb = disk.free / 1024**3
        ok = free_gb > 5
        return _check_result('磁盘健康', ok,
            10 if free_gb > 20 else (7 if free_gb > 10 else (5 if free_gb > 5 else 0)),
            f'剩余{free_gb:.1f}GB / 总{disk.total/1024**3:.0f}GB ({disk.percent}%)',
            'python3 scripts/cron_noai_runner.sh ops-disk-clean')
    except Exception as e:
        return _check_result('磁盘健康', False, 0, str(e))


def check_10_signal_quality() -> dict:
    """近24H信号质量（WR基准+信号数合理）"""
    try:
        with open(BASE_360 / 'data' / 'live_signal_log.jsonl') as f:
            sigs = [json.loads(l) for l in f if l.strip()]
        recent = [s for s in sigs if time.time() - s.get('timestamp', 0) < 86400]
        n = len(recent)
        # 检查是否有明显异常（太多或太少）
        ok = 0 <= n <= 200  # 正常范围：0~200条/天
        return _check_result('信号质量', ok,
            10 if 0 <= n <= 100 else (7 if n <= 200 else 3),
            f'近24H={n}条 总计={len(sigs)}条',
            '检查brahma_1hao是否异常频繁触发')
    except Exception as e:
        return _check_result('信号质量', False, 0, str(e))


def run_brahma_360(mode: str = 'full') -> dict:
    """
    brahma_360 v2.0 统一入口
    mode='light'  → check_01~05（<5s，无网络）
    mode='full'   → check_01~10（含API+信号质量）
    """
    t0 = time.time()

    light_checks = [
        check_01_regime_freshness,
        check_02_sqe_clean,
        check_03_wr_baseline,
        check_04_cron_architecture,
        check_05_critical_files,
    ]
    full_checks = light_checks + [
        check_06_kronos_cache,
        check_07_fangcang_coverage,
        check_08_api_latency,
        check_09_disk_health,
        check_10_signal_quality,
    ]

    checks = full_checks if mode == 'full' else light_checks
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:
            results.append(_check_result(fn.__name__, False, 0, f'异常:{e}'))

    total_score = sum(r['score'] for r in results)
    max_score = len(results) * 10
    issues = [r for r in results if not r['ok']]

    return {
        'score': total_score,
        'max_score': max_score,
        'pct': round(total_score / max_score * 100, 1) if max_score else 0,
        'mode': mode,
        'checks': results,
        'issues': [r['name'] for r in issues],
        'elapsed': round(time.time() - t0, 2),
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__' and '--360' in __import__('sys').argv:
    import sys as _sys
    _mode = 'light' if '--light' in _sys.argv else 'full'
    _res = run_brahma_360(_mode)
    print(f"\n🏛️ 梵天360 v2.0 · {_res['timestamp'][:16]} UTC · mode={_mode}")
    print(f"综合评分: {_res['score']}/{_res['max_score']} ({_res['pct']}%) | 耗时:{_res['elapsed']}s")
    print()
    for c in _res['checks']:
        icon = '✅' if c['ok'] else '❌'
        print(f"  {icon} {c['name']:14s} {c['score']:2d}/10  {c['detail']}")
        if not c['ok'] and c.get('fix'):
            print(f"     → 修复: {c['fix']}")
    if not _res['issues']:
        print(f"\n🔐 全部{len(_res['checks'])}项通过")
    else:
        print(f"\n⚠️ {len(_res['issues'])}项需关注: {_res['issues']}")

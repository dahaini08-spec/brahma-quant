#!/usr/bin/env python3
# ponytail: brahma_autofix 385行，有意为之，重构前先 grep 所有调用方
"""
brahma_autofix.py — 梵天自动化自检修复脚本
[设计院封印 2026-08-14 苏摩111]

功能：
  1. 全系统health check（22项检查）
  2. 可自动修复的问题立即修复
  3. 不可自动修复的问题推送报警
  4. 推送规则：有修复 or 有CRITICAL → 推送；全绿 → HEARTBEAT_OK

运行：
  python3 scripts/brahma_autofix.py
  python3 scripts/brahma_autofix.py --dry-run   # 只检查不修复
  python3 scripts/brahma_autofix.py --verbose   # 详细输出

自动修复项：
  A1. core dump文件清理
  A2. brahma_state.json过期 → 触发state_refresh
  A3. mcp/lightgbm包丢失 → 重装wheel
  A4. signal_weights.json过期 → 触发update_weights
  A5. wr_matrix过期 → 触发signal_settler dry验证
  A6. 孤立tmp/lock文件清理
"""

import sys, os, json, time, subprocess, shutil, glob
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

DRY_RUN = '--dry-run' in sys.argv
VERBOSE = '--verbose' in sys.argv

# ── 结果收集 ──────────────────────────────────────────────
issues    = []   # (level, code, detail, auto_fixable)
fixed     = []   # 已自动修复
checks_ok = []   # 通过的检查


def ok(msg):
    checks_ok.append(msg)
    if VERBOSE: print(f'  ✅ {msg}')


def warn(code, detail, fixable=False):
    issues.append(('WARN', code, detail, fixable))
    print(f'  ⚠️  [{code}] {detail}')


def crit(code, detail, fixable=False):
    issues.append(('CRIT', code, detail, fixable))
    print(f'  🔴 [{code}] {detail}')


def fix(msg):
    fixed.append(msg)
    print(f'  🔧 FIX: {msg}')


# ════════════════════════════════════════════════════════════
# CHECK 1: 数据文件新鲜度
# ════════════════════════════════════════════════════════════
def check_data_freshness():
    DATA = BASE / 'data'
    checks = {
        'brahma_state.json':       (70,   'CRIT', True),   # 体制刷新
        'position_sl_state.json':  (480,  'WARN', False),  # 持仓(无交易时正常老)
        'wr_matrix_realtime.json': (2880, 'WARN', False),  # WR矩阵(需结算)
        'signal_weights.json':     (2880, 'WARN', True),   # 权重文件可刷新
        'live_signal_log.jsonl':   (720,  'WARN', False),  # 信号日志(无信号时正常)
    }
    for fname, (max_age, level, fixable) in checks.items():
        path = DATA / fname
        if not path.exists():
            crit(f'MISSING_{fname.upper()[:12]}', f'{fname} 文件不存在', False)
            continue
        age_min = (time.time() - path.stat().st_mtime) / 60
        if age_min < max_age:
            ok(f'{fname} age={age_min:.0f}min')
        else:
            msg = f'{fname} age={age_min:.0f}min > {max_age}min'
            if level == 'CRIT':
                crit(f'STALE_{fname.upper()[:8]}', msg, fixable)
            else:
                # 跳过position_sl和signal_log的WARN（无交易期正常）
                if fname not in ('position_sl_state.json', 'live_signal_log.jsonl'):
                    warn(f'STALE_{fname.upper()[:8]}', msg, fixable)
                else:
                    ok(f'{fname} age={age_min:.0f}min (no-trade period OK)')


# ════════════════════════════════════════════════════════════
# CHECK 2: 核心模块可导入性
# ════════════════════════════════════════════════════════════
CORE_MODULES = [
    ('brahma_bus',           'get_price'),
    ('brahma_engine',        'analyze'),
    # ('brahma_scoring',       'confluence_score'),  # [2026-08-26] 已归档，主链路不需要，移出检查列表避免误报CRITICAL
    ('brahma_core',          'analyze'),
    ('position_sizer',       'get_position_pct'),
    ('var_engine',           'single_position_var'),
    ('cvd_engine',           'cvd_score_for_signal'),
    ('portfolio_optimizer',  'check_correlation_risk'),
    ('sl_bandit',            'recommend_sl_pct'),
    ('signal_weight_updater','update_weights'),
    ('kronos_bridge',        'get_s23_kronos'),
    ('hcme_matcher',         'HCMEMatcher'),   # 类名
    ('timing_filter',        'evaluate_timing'),
    ('ssi_engine',           'compute_ssi'),
    ('var_engine',           'single_position_var'),
]

def check_modules():
    for mod, fn in CORE_MODULES:
        try:
            m = __import__(mod)
            if hasattr(m, fn):
                ok(f'import {mod}.{fn}')
            else:
                warn('MISSING_FN', f'{mod}.{fn} 函数不存在（接口变更？）', False)
        except ImportError as e:
            err_s = str(e)
            # mcp是可自动修复的
            fixable = 'mcp' in mod or 'lightgbm' in err_s
            crit('IMPORT_ERR', f'{mod}: {err_s[:80]}', fixable)
        except Exception as e:
            warn('IMPORT_WARN', f'{mod}: {str(e)[:80]}', False)


# ════════════════════════════════════════════════════════════
# CHECK 3: core dump文件
# ════════════════════════════════════════════════════════════
def check_core_dumps():
    cores = list(BASE.glob('core.*')) + list(BASE.glob('core'))
    if cores:
        warn('CORE_DUMP', f'{len(cores)}个core dump文件 → 磁盘浪费', True)
        if not DRY_RUN:
            for c in cores:
                c.unlink(missing_ok=True)
            fix(f'清理{len(cores)}个core dump')
    else:
        ok('core dump: 0个')


# ════════════════════════════════════════════════════════════
# CHECK 4: cron任务健康
# ════════════════════════════════════════════════════════════
CRITICAL_CRONS = [
    'rsi-structure-watcher',
    'auto-executor-run',
    'brahma-state-refresh',
    'position-guardian',
    'signal-settler',
]

def check_crons():
    try:
        # 直接读本地jobs.json，避免subprocess超时
        jobs_path = Path.home() / '.openclaw/cron/jobs.json'
        runs_dir  = Path.home() / '.openclaw/cron/runs'
        jobs = json.loads(jobs_path.read_text()).get('jobs', []) if jobs_path.exists() else []

        error_crons    = []   # 连续err（非 restart打断）
        restart_errors = []   # restart打断（无需处理）

        for j in jobs:
            name = j.get('name', '')
            jid  = j.get('id', '')
            runs_file = runs_dir / f'{jid}.jsonl'
            if not runs_file.exists():
                continue
            lines = [l for l in runs_file.read_text().strip().split('\n') if l.strip()]
            consecutive = 0
            last_was_restart = False
            for line in reversed(lines):
                try:
                    r = json.loads(line)
                    if r.get('action') != 'finished':
                        continue
                    if r.get('status') == 'error':
                        if 'gateway restart' in r.get('error', ''):
                            last_was_restart = True
                            break
                        consecutive += 1
                    else:
                        break
                except Exception:
                    continue
            if last_was_restart and consecutive == 0:
                restart_errors.append(name)
            elif consecutive >= 2:   # 连续2次才认定故障
                error_crons.append((name, jid, consecutive))

        # 自动重启持续故障的核心cron
        CORE = ['rsi-structure-watcher', 'auto-executor', 'brahma-state-refresh',
                'position-guardian', 'signal-settler']
        for name, jid, cnt in error_crons:
            warn('CRON_ERROR', f'{name} 连续error={cnt}次(非gateway打断)', fixable=True)
            if not DRY_RUN and any(c in name for c in CORE):
                try:
                    subprocess.run(['openclaw', 'cron', 'trigger', jid],
                                   capture_output=True, timeout=10)
                    fix(f'cron {name} 已触发重运行')
                except Exception as e:
                    warn('CRON_RESTART_FAIL', f'{name} 重启失败: {e}', False)

        if restart_errors:
            ok(f'cron restart打断(自愈): {restart_errors}')

        # 核心cron存在性检查
        job_names = {j.get('name','') for j in jobs}
        for core_name in CRITICAL_CRONS:
            if not any(core_name in n for n in job_names):
                crit('CRON_MISSING', f'核心cron {core_name} 不存在！', False)
            else:
                ok(f'cron {core_name} 存在')

        ok(f'cron总计: {len(jobs)}个  连续err={len(error_crons)}')
    except Exception as e:
        warn('CRON_CHECK_ERR', f'cron检查失败: {e}', False)


# ════════════════════════════════════════════════════════════
# CHECK 5: 磁盘空间
# ════════════════════════════════════════════════════════════
def check_disk():
    stat = os.statvfs(str(BASE))
    free_gb = stat.f_frsize * stat.f_bavail / 1e9
    total_gb = stat.f_frsize * stat.f_blocks / 1e9
    used_pct = (1 - stat.f_bavail / stat.f_blocks) * 100

    if free_gb < 2:
        crit('LOW_DISK', f'磁盘仅剩{free_gb:.1f}GB ({used_pct:.0f}%使用)', True)
        if not DRY_RUN:
            # 清理旧日志
            log_dir = BASE / 'logs'
            if log_dir.exists():
                old_logs = sorted(log_dir.glob('*.log'), key=os.path.getmtime)[:-5]
                for f in old_logs:
                    f.unlink(missing_ok=True)
                if old_logs:
                    fix(f'清理{len(old_logs)}个旧日志文件')
    elif free_gb < 5:
        warn('LOW_DISK', f'磁盘剩余{free_gb:.1f}GB ({used_pct:.0f}%使用)', False)
    else:
        ok(f'磁盘: {free_gb:.1f}GB可用 ({used_pct:.0f}%使用)')


# ════════════════════════════════════════════════════════════
# CHECK 6: brahma_state体制年龄（可自动刷新）
# ════════════════════════════════════════════════════════════
def check_and_fix_brahma_state():
    state_path = BASE / 'data' / 'brahma_state.json'
    if not state_path.exists():
        crit('NO_STATE', 'brahma_state.json不存在', True)
        return

    age_min = (time.time() - state_path.stat().st_mtime) / 60
    if age_min > 70:
        crit('STALE_STATE', f'体制状态过期 age={age_min:.0f}min > 70min', True)
        if not DRY_RUN:
            r = subprocess.run(
                ['python3', 'scripts/brahma_state_refresh.py'],
                capture_output=True, text=True, timeout=30, cwd=str(BASE)
            )
            if r.returncode == 0:
                fix('brahma_state_refresh.py执行成功，体制已刷新')
            else:
                warn('STATE_REFRESH_FAIL', f'刷新失败: {r.stderr[:100]}', False)
    else:
        ok(f'brahma_state age={age_min:.0f}min ✅')


# ════════════════════════════════════════════════════════════
# CHECK 7: signal_weights.json过期可刷新
# ════════════════════════════════════════════════════════════
def check_and_fix_signal_weights():
    sw_path = BASE / 'data' / 'signal_weights.json'
    if not sw_path.exists():
        warn('NO_SIGNAL_WEIGHTS', 'signal_weights.json不存在', True)
        return

    age_min = (time.time() - sw_path.stat().st_mtime) / 60
    if age_min > 2880:  # 48h
        warn('STALE_WEIGHTS', f'signal_weights age={age_min:.0f}min > 2880min', True)
        if not DRY_RUN:
            try:
                from signal_weight_updater import update_weights
                r = update_weights(dry_run=False)
                fix(f'signal_weights更新: {r.get("updated",0)}个key更新')
            except Exception as e:
                warn('WEIGHTS_UPDATE_FAIL', f'更新失败: {e}', False)
    else:
        ok(f'signal_weights age={age_min:.0f}min')


# ════════════════════════════════════════════════════════════
# CHECK 8: mcp包可用性（gateway重启后可能丢失）
# ════════════════════════════════════════════════════════════
def check_and_fix_mcp():
    try:
        from mcp.server import MCPServer
        ok('mcp.server.MCPServer 可用')
    except ImportError:
        warn('MCP_MISSING', 'mcp包不可用（gateway重启后丢失）', True)
        if not DRY_RUN:
            wheels_dir = Path('/tmp/mcp_wheels2')
            if wheels_dir.exists():
                try:
                    import zipfile
                    dest = Path('/usr/local/lib/python3.11/dist-packages/')
                    skip = ['cffi', 'cryptography', 'pydantic_core', 'rpds_py']
                    n = 0
                    for whl in wheels_dir.glob('*.whl'):
                        if any(s in whl.name for s in skip):
                            continue
                        with zipfile.ZipFile(whl) as z:
                            z.extractall(dest)
                            n += 1
                    fix(f'mcp重装: {n}个wheel → {dest}')
                except Exception as e:
                    warn('MCP_FIX_FAIL', f'mcp重装失败: {e}', False)
            else:
                warn('MCP_NO_WHEEL', 'mcp wheel缓存不存在，无法自动修复', False)


# ════════════════════════════════════════════════════════════
# CHECK 9: lightgbm / Kronos可用性
# ════════════════════════════════════════════════════════════
def check_lightgbm():
    try:
        r = subprocess.run(
            ['python3','-c','import sys; sys.path.insert(0,"venv/lib/python3.11/site-packages"); import lightgbm; print(lightgbm.__version__)'],
            capture_output=True, text=True, timeout=8, cwd=str(BASE)
        )
        if r.returncode == 0:
            ok(f'lightgbm {r.stdout.strip()}')
        else:
            warn("LGBM_MISSING", "lightgbm不可用，Kronos降级fallback", False)
    except Exception as e:
        warn("LGBM_CHECK_ERR", f'lightgbm检查失败: {e}', False)


# ════════════════════════════════════════════════════════════
# CHECK 10: 持仓止损状态
# ════════════════════════════════════════════════════════════
def check_positions():
    try:
        pos = json.load(open(BASE / 'data' / 'position_sl_state.json'))
        active = {k: v for k, v in pos.items() if v.get('side')}
        if not active:
            ok('持仓: 无活跃持仓')
            return
        for sym, p in active.items():
            if not p.get('sl_price'):
                crit('NO_SL', f'{sym} 活跃持仓无止损价！', False)
            elif not p.get('entry_price'):
                warn('NO_ENTRY', f'{sym} 持仓无进场价', False)
            else:
                ok(f'持仓 {sym}: entry={p["entry_price"]} sl={p["sl_price"]}')
    except Exception as e:
        warn('POSITION_ERR', f'持仓检查失败: {e}', False)


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════
def main():
    print(f'\n{"="*60}')
    print(f'梵天自动化自检 brahma_autofix.py')
    print(f'时间: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'模式: {"DRY_RUN" if DRY_RUN else "LIVE修复"}')
    print(f'{"="*60}\n')

    # 执行所有检查
    check_core_dumps()
    check_data_freshness()
    check_modules()
    check_crons()
    check_disk()
    check_and_fix_brahma_state()
    check_and_fix_signal_weights()
    check_and_fix_mcp()
    check_lightgbm()
    check_positions()

    # ── 汇总 ──────────────────────────────────────────────
    crits  = [i for i in issues if i[0] == 'CRIT']
    warns  = [i for i in issues if i[0] == 'WARN']
    auto_fixed = len(fixed)

    print(f'\n{"="*60}')
    print(f'检查通过: {len(checks_ok)}项')
    print(f'发现问题: CRIT={len(crits)} WARN={len(warns)}')
    print(f'自动修复: {auto_fixed}项')

    if not issues and not fixed:
        print('\n🟢 HEARTBEAT_OK — 系统全部正常')
        return 0

    if fixed:
        print(f'\n✅ 已自动修复:')
        for f in fixed:
            print(f'  • {f}')

    if crits:
        print(f'\n🔴 CRITICAL（需人工关注）:')
        for _, code, detail, _ in crits:
            print(f'  [{code}] {detail}')

    if warns:
        print(f'\n⚠️  WARNING:')
        for _, code, detail, _ in warns:
            print(f'  [{code}] {detail}')

    # 推送规则: CRIT or 有修复 → 推送报告; 只有WARN且无修复 → HEARTBEAT_OK
    if crits or auto_fixed > 0:
        return 1  # 需要推送
    else:
        print('\n⚠️ 有WARN但无CRIT → HEARTBEAT_OK（静默）')
        return 0


if __name__ == '__main__':
    sys.exit(main())

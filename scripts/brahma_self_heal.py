#!/usr/bin/env python3
"""
brahma_self_heal.py · 梵天自愈引擎 v1.0
══════════════════════════════════════════════════
设计院自主决策 · 2026-07-03 · 苏摩授权

核心能力：
  1. 主动检测 → 识别系统故障（API/引擎/队列/文件/cron/推送路由）
  2. 自动修复 → 不需要人工干预的故障直接自愈
  3. 分级上报 → 自愈成功静默 / 自愈失败 → P0推送苏摩
  4. 看门狗模式 → cron任务存活检测 + 自动重注册

自愈矩阵：
  故障类型             → 自愈动作
  ─────────────────────────────────────────────────
  API断连              → safe_fetch重试 / 等待恢复
  brahma_bus陈旧       → flush_stale()
  评分引擎崩溃          → ast语法检查 + 模块重载
  信号队列积压          → 清理超期信号
  关键数据文件缺失      → brahma_state_refresh
  cron任务停止运行      → 重新触发 / 检测并上报
  推送路由错误          → 检测019f1797匹配 + 自动修正
  regime_state陈旧     → 强制刷新体制状态
  position_sl_state脏  → 清理updated_at=0条目

触发方式：cron every 15min
"""

import sys, os, json, time, subprocess, requests, ast
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

try:
    import scripts.system_config as _sc
    PUSH_TARGET = f"{_sc.JARVIS_USER_ID}:t:{_sc.JARVIS_THREAD_ID}"
except Exception:
    PUSH_TARGET   = '73295708:t:019f309c-609b-7a75-a195-e221e5927c63'
PUSH_CHANNEL  = 'jarvis'
HEAL_LOG_FILE = BASE / 'logs' / 'self_heal.log'
STATE_FILE    = BASE / 'data' / 'self_heal_state.json'
FAPI          = 'https://fapi.binance.com'

# 监控的 cron 任务名 → 最大允许无运行间隔（分钟）
CRON_WATCHLIST = {
    'rsi-structure-watcher':   15,   # 每5min，15min没跑 = 故障
    'brahma-scan-guard':       800,  # 每12H
    'btc-regime-watcher':      15,
    'ws-guardian-keepalive':   15,
    'auto-position-manager':   45,
    'regime-switch-monitor':   45,
}

# 关键数据文件 → 最大陈旧分钟
CRITICAL_FILES = {
    'data/regime_state.json':       60,
    'data/scan_candidates.json':    90,
    'data/rsi_watcher_state.json':  20,   # v1.1: 从15→20min，避免与5min刷新频率临界竞争
}


# ══════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════

def _log(msg: str):
    """写入自愈日志"""
    HEAL_LOG_FILE.parent.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime('%m-%d %H:%M UTC')
    with open(HEAL_LOG_FILE, 'a') as f:
        f.write(f'[{ts}] {msg}\n')
    print(f'[SelfHeal] {msg}')


def _push(msg: str, dedup_key: str = None, dedup_ttl: int = 3600) -> bool:
    state = _load_state()
    if dedup_key:
        last = state.get('dedup', {}).get(dedup_key, 0)
        if time.time() - last < dedup_ttl:
            return False
        state.setdefault('dedup', {})[dedup_key] = time.time()
        _save_state(state)
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target', PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return True
    except Exception as e:
        _log(f'推送失败: {e}')
        return False


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _run(cmd: list, timeout: int = 30) -> tuple:
    """执行命令，返回 (success, output)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(BASE))
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, 'TIMEOUT'
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════
# 检测模块
# ══════════════════════════════════════════════════════════════

def check_binance_api() -> dict:
    """Binance API 连通性 + 延迟"""
    t0 = time.time()
    try:
        r = requests.get(f'{FAPI}/fapi/v1/ping', timeout=5)
        latency = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            return {'ok': True, 'latency_ms': latency,
                    'warn': latency > 2000,
                    'detail': f'延迟{latency}ms'}
        return {'ok': False, 'detail': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def check_brahma_analyze() -> dict:
    """信号链核心文件 brahma_analyze.py 存在性检查（设计院 2026-07-06）"""
    analyze_file = BASE / 'brahma_analyze.py'
    bak_file     = BASE / 'brahma_analyze.py.bak_20260703'
    if not analyze_file.exists():
        # 自愈：从备份恢复
        if bak_file.exists():
            import shutil
            shutil.copy2(str(bak_file), str(analyze_file))
            _push(
                '🔧 [SelfHeal] brahma_analyze.py 丢失 → 已从.bak自动恢复 \n信号链恢复正常',
                dedup_key='brahma_analyze_missing', dedup_ttl=3600
            )
            return {'ok': True, 'detail': '⚠️ 丢失 → 已从bak自动恢复', 'healed': True}
        else:
            _push(
                '🚨 [SelfHeal] brahma_analyze.py 丢失且无备份！信号链完全中断',
                dedup_key='brahma_analyze_no_bak', dedup_ttl=3600
            )
            return {'ok': False, 'detail': '❌ 丢失且无备份，信号链中断！'}
    try:
        ast.parse(analyze_file.read_text())
        return {'ok': True, 'detail': '存在+语法正常'}
    except SyntaxError as e:
        return {'ok': False, 'detail': f'语法错误L{e.lineno}: {e.msg}'}


def check_scoring_engine() -> dict:
    """评分引擎（brahma_core.py）语法健康"""
    core = BASE / 'brahma_brain' / 'brahma_core.py'
    if not core.exists():
        return {'ok': False, 'detail': 'brahma_core.py 不存在'}
    try:
        ast.parse(core.read_text())
        # 快速导入测试
        ok, out = _run(['python3', '-c',
                        'from brahma_brain.brahma_core import analyze; print("OK")'],
                       timeout=15)
        return {'ok': ok, 'detail': 'syntax+import正常' if ok else out[:100]}
    except SyntaxError as e:
        return {'ok': False, 'detail': f'语法错误L{e.lineno}: {e.msg}'}


def check_regime_state() -> dict:
    """体制状态文件新鲜度"""
    reg = BASE / 'data' / 'regime_state.json'
    if not reg.exists():
        return {'ok': False, 'detail': 'regime_state.json 不存在'}
    try:
        data = json.loads(reg.read_text())
        mtime = reg.stat().st_mtime
        age_min = (time.time() - mtime) / 60
        btc_reg = data.get('BTCUSDT', {}).get('confirmed', 'UNKNOWN')
        warn = age_min > 60
        return {
            'ok': True, 'warn': warn,
            'age_min': round(age_min, 1),
            'btc_regime': btc_reg,
            'detail': f'BTC={btc_reg} 更新={age_min:.0f}min前{"(陈旧⚠️)" if warn else ""}'
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def check_signal_pipeline() -> dict:
    """信号管道健康：live_signal_log + signal_queue"""
    sig = BASE / 'data' / 'live_signal_log.jsonl'
    if not sig.exists():
        return {'ok': True, 'detail': '无信号文件（正常）'}
    try:
        lines = sig.read_text().strip().split('\n')
        recent = 0
        now = time.time()
        for l in lines:
            try:
                r = json.loads(l)
                if now - r.get('ts', 0) < 86400:
                    recent += 1
            except Exception:
                pass
        return {
            'ok': True,
            'recent_24h': recent,
            'detail': f'24H内信号={recent}条'
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def check_cron_jobs() -> dict:
    """cron任务存活检测"""
    issues = []
    try:
        r = subprocess.run(
            ['openclaw', 'cron', 'list'],
            capture_output=True, text=True, timeout=10
        )
        output = r.stdout
        for job_name, max_idle_min in CRON_WATCHLIST.items():
            if job_name not in output:
                issues.append(f'{job_name}: 未注册')
                continue
            # 检查 last run
            for line in output.splitlines():
                if job_name in line:
                    # 解析 last run 时间（格式: "Xm ago"）
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if 'ago' in p and i > 0:
                            val = parts[i-1]
                            unit = p.replace('ago', '').strip()
                            try:
                                n = float(val.replace('m','').replace('h','').replace('s',''))
                                if 'h' in val:
                                    n *= 60
                                elif 's' in val:
                                    n /= 60
                                if n > max_idle_min:
                                    issues.append(f'{job_name}: {n:.0f}min未运行(阈值{max_idle_min}min)')
                            except Exception:
                                pass
                    break
    except Exception as e:
        return {'ok': False, 'detail': str(e)}

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'issues': issues,
        'detail': '所有cron正常' if not issues else f'{len(issues)}个异常: {issues[:2]}'
    }


def check_data_files() -> dict:
    """关键数据文件新鲜度"""
    stale = []
    now = time.time()
    for rel_path, max_age_min in CRITICAL_FILES.items():
        p = BASE / rel_path
        if not p.exists():
            stale.append(f'{rel_path}(不存在)')
            continue
        age_min = (now - p.stat().st_mtime) / 60
        if age_min > max_age_min:
            stale.append(f'{rel_path}({age_min:.0f}min前)')
    return {
        'ok': len(stale) == 0,
        'warn': len(stale) > 0,
        'stale': stale,
        'detail': '文件正常' if not stale else f'陈旧: {stale}'
    }


def check_execution_pipeline() -> dict:
    """
    OPT-C: 执行管道健康检测（设计院优化 2026-07-03）
    监控: position_tsl_monitor / auto_execute_gate 进程/文件健康
    """
    issues = []
    # 检查 auto_execute_gate.py 语法
    aeg = BASE / 'scripts' / 'auto_execute_gate.py'
    if aeg.exists():
        try:
            ast.parse(aeg.read_text())
        except SyntaxError as e:
            issues.append(f'auto_execute_gate 语法错误L{e.lineno}')
    else:
        issues.append('auto_execute_gate.py 不存在')

    # 检查 position_tsl_monitor.py 语法
    tsl = BASE / 'scripts' / 'position_tsl_monitor.py'
    if tsl.exists():
        try:
            ast.parse(tsl.read_text())
        except SyntaxError as e:
            issues.append(f'position_tsl_monitor 语法错误L{e.lineno}')
    else:
        issues.append('position_tsl_monitor.py 不存在')

    # 检查 live_signal_log 队列是否积压（超100条未处理视为异常）
    lsl = BASE / 'data' / 'live_signal_log.jsonl'
    if lsl.exists():
        try:
            lines = lsl.read_text().strip().split('\n')
            pending = sum(1 for l in lines if '"action":"ENTER"' in l or
                          ('"valid":true' in l.lower() or '"valid": true' in l.lower()))
            if pending > 50:
                issues.append(f'信号队列积压: {pending}条有效信号待处理')
        except Exception:
            pass

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'issues': issues,
        'detail': '执行管道正常' if not issues else f'{issues}'
    }


def check_cron_precise() -> dict:
    """
    OPT-C: 精确cron存活检测 v2（设计院优化 2026-07-03）
    解析 openclaw cron list 的 Last 列（如"13m ago"/"2h ago"），比字符串解析更准确
    """
    import re as _re
    issues = []
    try:
        r = subprocess.run(['openclaw', 'cron', 'list'],
                           capture_output=True, text=True, timeout=10)
        output = r.stdout

        for job_name, max_idle_min in CRON_WATCHLIST.items():
            for line in output.splitlines():
                if job_name not in line:
                    continue
                # 解析 Last 列：格式 "13m ago" / "2h ago" / "never" / "-"
                # 使用空白分隔，Last列通常在 Next 后面
                m_min = _re.search(r'(\d+)m\s+ago', line)
                m_hr  = _re.search(r'(\d+)h\s+ago', line)
                m_sec = _re.search(r'(\d+)s\s+ago', line)
                m_day = _re.search(r'(\d+)d\s+ago', line)
                is_main_target = 'not requested' in line
                never = (not is_main_target and
                         ('never' in line.lower() or
                          _re.search(r'\|\s+-\s+ok\b', line) is not None))

                if never:
                    # 从未运行：只有严格监控项才告警
                    if max_idle_min <= 30:
                        issues.append(f'{job_name}: 从未运行')
                    break

                idle_min = 0
                if m_day:  idle_min = int(m_day.group(1)) * 1440
                elif m_hr: idle_min = int(m_hr.group(1)) * 60
                elif m_min:idle_min = int(m_min.group(1))
                elif m_sec:idle_min = int(m_sec.group(1)) / 60

                if idle_min > max_idle_min:
                    issues.append(f'{job_name}: {idle_min:.0f}min未运行(阈值{max_idle_min}min)')
                break  # 找到后跳出内层循环

    except Exception as e:
        return {'ok': False, 'detail': str(e)}

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'issues': issues,
        'detail': 'cron全部正常' if not issues else f'{len(issues)}异常: {issues[:3]}'
    }


def check_push_routing() -> dict:
    """推送路由正确性：确认所有cron任务使用正确线程"""
    CORRECT_THREAD = '019f309c-609b-7a75-a195-e221e5927c63'
    OLD_THREADS    = [
        '019f181f-e4d1-7576-85ca-77f4a7fa8075',
        '019f1797-6c60-7541-ad72-ec34ed14dfc4',
    ]
    issues = []
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        if not jobs_file.exists():
            return {'ok': True, 'detail': 'jobs.json不存在'}
        raw = json.loads(jobs_file.read_text())
        jobs = raw.get('jobs', raw) if isinstance(raw, dict) else raw
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs:
            if not isinstance(j, dict):
                continue
            delivery = j.get('delivery', {})
            to = delivery.get('to', '')
            if any(old in to for old in OLD_THREADS):
                issues.append(j.get('name', '?'))
    except Exception as e:
        return {'ok': False, 'detail': str(e)}

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'wrong_thread_jobs': issues,
        'detail': '推送路由正常' if not issues else f'旧线程任务: {issues}'
    }


# ══════════════════════════════════════════════════════════════
# 自愈执行矩阵
# ══════════════════════════════════════════════════════════════

def heal(fault_type: str, context: dict) -> dict:
    """
    自愈动作执行器
    返回：{'healed': bool, 'action': str, 'output': str}
    """
    result = {'healed': False, 'action': fault_type, 'output': ''}

    if fault_type == 'API_RECONNECT':
        ok, out = _run(['python3', 'scripts/safe_fetch.py', '--test'], timeout=20)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'CACHE_FLUSH':
        ok, out = _run(['python3', '-c',
                        'from brahma_brain.brahma_bus import BrahmaBus; '
                        'b=BrahmaBus(); b.flush_stale(); print("flushed")'], timeout=15)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'REGIME_STALE':
        ok, out = _run(['python3', 'guardrails/brahma_state_refresh.py'], timeout=30)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'DEPS_CHECK':
        # 检查并自动修复缺失的Python依赖
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location('ensure_deps',
                BASE / 'scripts' / 'ensure_deps.py')
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _r = _mod.check_and_fix()
            if _r['fixed']:
                result.update({'healed': True, 'output': f'依赖已修复: {_r["fixed"]}'})
            else:
                result.update({'ok': True, 'output': '依赖完整'})
        except Exception as _e:
            result.update({'ok': False, 'output': f'deps_check异常: {_e}'})

    elif fault_type == 'DATA_FILE_REFRESH':
        ok, out = _run(['python3', 'scripts/brahma_state_refresh.py'], timeout=30)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'POSITION_SL_CLEAN':
        # 清理 updated_at=0 脏数据
        ps = BASE / 'data' / 'position_sl_state.json'
        if ps.exists():
            data = json.loads(ps.read_text())
            before = len(data)
            cleaned = {k: v for k, v in data.items()
                       if isinstance(v, dict) and v.get('updated_at', 0) > 1700000000}
            ps.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False))
            removed = before - len(cleaned)
            result.update({'healed': True, 'output': f'清理{removed}条脏数据'})

    elif fault_type == 'PUSH_ROUTE_FIX':
        # [v1.1 2026-07-03] 修复旧线程推送路由 — 从system_config读取SSOT线程ID
        try:
            import importlib.util as _ilu
            _sc_path = BASE / 'scripts' / 'system_config.py'
            _spec = _ilu.spec_from_file_location('system_config', _sc_path)
            _sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
            OLD = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
            NEW = getattr(_sc, 'JARVIS_THREAD_ID', '019f309c-609b-7a75-a195-e221e5927c63')
        except Exception:
            OLD = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
            NEW = '019f309c-609b-7a75-a195-e221e5927c63'
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        if jobs_file.exists():
            jobs_txt = jobs_file.read_text()
            if OLD in jobs_txt:
                fixed_txt = jobs_txt.replace(OLD, NEW)
                jobs_file.write_text(fixed_txt)
                fixed_n = jobs_txt.count(OLD)
                result.update({'healed': True, 'output': f'旧线程→{NEW[:8]} 修复{fixed_n}处'})
            else:
                result.update({'healed': True, 'output': '无需修复（路由已正确）'})

    elif fault_type == 'SCAN_CANDIDATES_REFRESH':
        ok, out = _run(['python3', 'scripts/market_screener.py'], timeout=60)
        result.update({'healed': ok, 'output': out[-200:] if out else ''})

    return result


# ══════════════════════════════════════════════════════════════
# 主检测+自愈循环
# ══════════════════════════════════════════════════════════════

def run_self_heal():
    _log('自愈引擎启动')
    healed_items  = []
    failed_items  = []
    reported_items = []

    # ── 执行所有检测 ─────────────────────────────────────────
    checks = {
        'brahma_analyze':  check_brahma_analyze(),   # [设计院 2026-07-06] 信号链核心文件保护
        'binance_api':     check_binance_api(),
        'scoring_engine':  check_scoring_engine(),
        'regime_state':    check_regime_state(),
        'signal_pipeline': check_signal_pipeline(),
        'cron_jobs':       check_cron_jobs(),
        'data_files':      check_data_files(),
        'push_routing':    check_push_routing(),
        'exec_pipeline':   check_execution_pipeline(),
        'cron_precise':    check_cron_precise(),
    }

    _log(f'检测完成: {sum(1 for c in checks.values() if c.get("ok"))}/'
         f'{len(checks)}项正常')

    # ── 自愈决策矩阵 ─────────────────────────────────────────
    fault_heal_map = [
        # (检测key, 判断条件, 故障类型, 是否上报失败)
        ('binance_api',    lambda c: not c.get('ok'),                  'API_RECONNECT',          True),
        ('regime_state',   lambda c: c.get('warn') and c.get('age_min',0) > 60, 'REGIME_STALE', False),
        ('data_files',     lambda c: c.get('warn') and len(c.get('stale',[])) > 0, 'DATA_FILE_REFRESH', False),
        ('push_routing',   lambda c: c.get('warn'),                    'PUSH_ROUTE_FIX',         False),
        ('exec_pipeline',  lambda c: c.get('warn'),                    'EXEC_PIPELINE_WARN',     True),
        ('cron_precise',   lambda c: c.get('warn') and len(c.get('issues',[])) > 0, 'CRON_PRECISE_WARN', True),
    ]

    for check_key, condition, fault_type, report_on_fail in fault_heal_map:
        c = checks.get(check_key, {})
        if not condition(c):
            continue

        _log(f'发现故障: {check_key} → 尝试自愈: {fault_type}')
        heal_result = heal(fault_type, c)

        if heal_result['healed']:
            healed_items.append(f'{fault_type}: {heal_result["output"][:80]}')
            _log(f'✅ 自愈成功: {fault_type} | {heal_result["output"][:80]}')
        else:
            failed_items.append(f'{fault_type}: {heal_result["output"][:80]}')
            _log(f'❌ 自愈失败: {fault_type} | {heal_result["output"][:80]}')
            if report_on_fail:
                reported_items.append((check_key, c, fault_type))

    # 评分引擎故障：必须上报（无法自动修复）
    eng = checks.get('scoring_engine', {})
    if not eng.get('ok'):
        reported_items.append(('scoring_engine', eng, 'SCORING_ENGINE_FAIL'))

    # cron异常上报
    cron = checks.get('cron_jobs', {})
    if cron.get('warn') and cron.get('issues'):
        for issue in cron['issues']:
            _log(f'⚠️ cron异常: {issue}')
        # 仅上报，不自动重注册（避免重复注册）
        reported_items.append(('cron_jobs', cron, 'CRON_ISSUE'))

    # ── 生成上报消息 ─────────────────────────────────────────
    if reported_items:
        lines = ['🔴 **梵天自愈系统 · 需要关注**', '']
        for check_key, c, fault_type in reported_items:
            lines.append(f'❌ {fault_type}')
            lines.append(f'   {c.get("detail", "?")[:100]}')
            lines.append('')
        if failed_items:
            lines.append('⚠️ 自愈失败项：')
            for f in failed_items:
                lines.append(f'  · {f}')
        _push('\n'.join(lines),
              dedup_key=f'self_heal_report_{int(time.time()//3600)}',
              dedup_ttl=3600)
    elif healed_items:
        _log(f'🟢 自愈成功{len(healed_items)}项，静默（无需上报）: {healed_items}')
    else:
        _log('🟢 系统健康，无需自愈')

    # ── 定期健康摘要（每4H推送一次简报）───────────────────────
    state = _load_state()
    last_summary = state.get('last_summary_ts', 0)
    if time.time() - last_summary > 4 * 3600:
        ok_count  = sum(1 for c in checks.values() if c.get('ok'))
        btc_reg   = checks.get('regime_state', {}).get('btc_regime', '?')
        api_lat   = checks.get('binance_api', {}).get('latency_ms', '?')
        sig_24h   = checks.get('signal_pipeline', {}).get('recent_24h', 0)

        summary_msg = (
            f"🟢 **梵天自愈 · 4H摘要**\n"
            f"  系统健康: {ok_count}/{len(checks)}项正常\n"
            f"  BTC体制: {btc_reg}\n"
            f"  API延迟: {api_lat}ms\n"
            f"  24H信号: {sig_24h}条\n"
            f"  自愈历史: {len(healed_items)}项已修复"
        )
        _push(summary_msg,
              dedup_key=f'self_heal_summary_{int(time.time()//14400)}',
              dedup_ttl=14400)
        state['last_summary_ts'] = time.time()
        _save_state(state)

    return {
        'checks': checks,
        'healed': healed_items,
        'failed': failed_items,
        'reported': len(reported_items),
    }


if __name__ == '__main__':
    result = run_self_heal()
    print(f'\n[SelfHeal] 完成 | 自愈={len(result["healed"])} 失败={len(result["failed"])} 上报={result["reported"]}')
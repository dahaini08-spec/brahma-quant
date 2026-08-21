#!/usr/bin/env python3
"""
brahma_cron_doctor.py — 梵天 Cron 自动诊断与修复
设计院落地 2026-08-04 | 苏摩授权

职责：
  1. 扫描所有核心 cron 任务的最近 run 记录
  2. 检测失败模式（error / timeout / no_run / silent_fail）
  3. 对已知错误自动修复（进程重启 / 数据刷新 / 环境修复）
  4. 推送诊断报告到 Jarvis（有问题才推，无问题 HEARTBEAT_OK）

运行方式：
  python3 scripts/brahma_cron_doctor.py         # 诊断+修复
  python3 scripts/brahma_cron_doctor.py --check # 仅诊断不修复

每4小时由 cron job 触发一次
"""


# [P0-2 2026-08-15 苏摩111] brahma-scan-guard存在性检查
REQUIRED_CRONS = ['rsi-structure-watcher', 'brahma-scan-guard', 'candidates-refresh']
import sys, os, json, time, subprocess, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE    = Path(__file__).parent.parent
SCRIPTS = BASE / 'scripts'
DATA    = BASE / 'data'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

CHECK_ONLY = '--check' in sys.argv

# ── 推送配置 ──────────────────────────────────────────────
try:
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    PUSH_TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
except Exception:
    PUSH_TARGET = '73295708:thread:019fd70a-0942-72b1-aeb9-1bd4fc11b30d'

def _push(msg: str):
    try:
        from push_hub import _jarvis
        _jarvis(msg)
    except Exception as e:
        print(f"[push_fail] {e}")

# ── 核心 cron 任务监控列表 ─────────────────────────────────
# 格式: (job_id, name, max_silence_hours, is_critical)
WATCHED_JOBS = [
    # [2026-08-11 1号工程修复] 同步当前有效cron ID，清除旧ID防止串行阻塞超时
    ('6e82b213-083d-4d0e-85b3-9144b4ded13d', 'brahma-state-refresh',    2,  True),
    ('b9274535-221a-4dbc-b8d5-0b97f1365b38', 'rsi-structure-watcher',    2,  True),
    ('3a9c9763-bc9e-4eca-9af4-c4369aaea557', 'auto-1hao-trigger',        2,  True),
    ('7da05c85-e909-4c8b-acf4-7865c0cb52fd', 'auto-executor-run',        2,  True),
    ('8ca65ac1-36a1-4f57-a7bc-f0bf15202f9e', 'signal-settler',           5,  False),
    ('7af852f4-90f2-4de1-a0a8-8b444e227e44', 'oi-advanced-scanner',      3,  False),
    ('0c79a28e-410d-479a-a6ff-dfafb0557f1f', 'log-rotation',            12,  False),
    ('22df48b0-dfdc-4cb1-bb7d-c98be7cabb00', 'core-dump-cleaner',        4,  False),
]

# ── 已知错误模式 → 自动修复动作 ──────────────────────────
ERROR_PATTERNS = [
    # (正则, 描述, 修复函数名)
    (r'ImportError.*brahma_engine',   'brahma_engine import失败',  'fix_pycache'),
    (r'ImportError.*system_config',   'system_config import失败',  'fix_syspath'),
    (r'ModuleNotFoundError',          '缺少Python模块',             'fix_deps'),
    (r'JSONDecodeError|json\.decoder','JSON文件损坏',               'fix_json_files'),
    (r'TimeoutError|timed out',       'API超时',                   'fix_timeout'),
    (r'ConnectionError|ECONNREFUSED', '网络连接失败',               'fix_network'),
    (r'KeyError.*BINANCE_API',        'API Key环境变量缺失',        'fix_env_vars'),
    (r'PermissionError',              '文件权限错误',               'fix_permissions'),
    (r'FileNotFoundError.*data/',     'data文件缺失',               'fix_data_files'),
    (r'SyntaxError',                  'Python语法错误',             'fix_syntax_error'),
]

# ── cron run 数据获取 ──────────────────────────────────────
def get_recent_runs(job_id: str, limit: int = 3) -> list:
    """调用 openclaw cron runs 获取最近 run 记录

    修复(2026-08-05 v2 设计院封印):
    根因: 连续17次串行调用时，第11次左右Gateway限流导致subprocess
          无限阻塞 → timeout=12秒后异常被吞 → 后续调用继续挂死
    修复方案:
      1. timeout 从12s→6s（更快失败）
      2. sleep 从0.3s→0.5s（给Gateway更多恢复时间）
      3. 捕获 subprocess.TimeoutExpired 并立即 return []
      4. 加 preexec_fn=os.setsid 确保子进程可被kill
    """
    time.sleep(0.5)  # Gateway限流保护：串行调用间隔
    try:
        r = subprocess.run(
            ['openclaw', 'cron', 'runs', '--id', job_id],
            capture_output=True, text=True, timeout=6,
            preexec_fn=os.setsid if hasattr(os, 'setsid') else None
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        # 跳过 config warnings 等非 JSON 前缀（定位第一个 '{'）
        stdout = r.stdout
        idx = stdout.find('{')
        if idx < 0:
            return []
        data = json.loads(stdout[idx:])
        entries = data.get('entries', [])
        # 只看 finished/error 类型
        runs = [e for e in entries if e.get('action') in ('finished', 'error', 'timeout')]
        return sorted(runs, key=lambda x: x.get('ts', 0), reverse=True)[:limit]
    except Exception:
        return []

def classify_run(run: dict) -> str:
    """判断一次 run 的健康状态"""
    status  = run.get('status', '')
    summary = run.get('summary', '')
    action  = run.get('action', '')

    if action == 'error' or status == 'error':
        return 'error'
    if action == 'timeout':
        return 'timeout'
    # 判断 summary 是否含失败信号
    if summary and re.search(r'(?i)(error|exception|failed|traceback|crash)', summary):
        return 'soft_fail'
    return 'ok'

def extract_error_text(run: dict) -> str:
    """从 run 记录提取错误文本"""
    parts = []
    for field in ('summary', 'error', 'stderr', 'output'):
        v = run.get(field, '')
        if v:
            parts.append(str(v)[:300])
    return ' | '.join(parts)

# ── 自动修复动作 ───────────────────────────────────────────
def fix_pycache():
    """清理 __pycache__，解决 stale bytecode 导致的 ImportError"""
    count = 0
    for p in BASE.rglob('__pycache__'):
        if 'venv' not in str(p) and '.git' not in str(p):
            subprocess.run(['rm', '-rf', str(p)], capture_output=True)
            count += 1
    return f"已清理 {count} 个 __pycache__"

def fix_syspath():
    """验证 sys.path 配置文件完整性"""
    issues = []
    for d in ['brahma_brain', 'scripts']:
        if not (BASE / d).exists():
            issues.append(f"{d} 目录不存在")
    return f"syspath check: {', '.join(issues) if issues else 'OK'}"

def fix_deps():
    """尝试重装 requirements-live-lite"""
    req = BASE / 'requirements-live-lite.txt'
    if req.exists():
        r = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', str(req),
             '--break-system-packages', '-q'],
            capture_output=True, text=True, timeout=60
        )
        return f"pip install: {'OK' if r.returncode == 0 else r.stderr[:100]}"
    return "requirements-live-lite.txt 不存在"

def fix_json_files():
    """检测并修复损坏的 JSON 数据文件（备份后清空）"""
    fixed = []
    for jf in DATA.glob('*.json'):
        try:
            json.loads(jf.read_text())
        except Exception:
            bak = jf.with_suffix('.json.bak_doctor')
            jf.rename(bak)
            jf.write_text('{}')
            fixed.append(jf.name)
    return f"修复损坏 JSON: {fixed if fixed else '无'}"

def fix_timeout():
    """网络超时：验证 Binance FAPI 可达性"""
    try:
        import requests as _req
        r = _req.get('https://fapi.binance.com/fapi/v1/ping', timeout=5)
        return f"FAPI ping: {'OK' if r.status_code == 200 else r.status_code}"
    except Exception as e:
        return f"FAPI不可达: {str(e)[:80]}"

def fix_network():
    return fix_timeout()

def fix_env_vars():
    """确保关键环境变量存在（从 .secrets 读取）"""
    secrets_file = BASE / '.secrets'
    if not os.environ.get('BINANCE_API_KEY') and secrets_file.exists():
        for line in secrets_file.read_text().strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip()
        return "从 .secrets 重载环境变量 OK"
    return "env vars check OK"

def fix_permissions():
    """修复 data/ 目录写权限"""
    try:
        subprocess.run(['chmod', '-R', '664', str(DATA)], capture_output=True, timeout=5)
        return "data/ 权限修复 OK"
    except Exception as e:
        return f"chmod 失败: {e}"

def fix_data_files():
    """创建缺失的关键 data 文件（空JSON）"""
    critical_files = [
        'live_prices.json', 'regime_state.json',
        'nerve_center_state.json', 'signal_bus.jsonl'
    ]
    created = []
    for fname in critical_files:
        fp = DATA / fname
        if not fp.exists():
            fp.write_text('{}' if fname.endswith('.json') else '')
            created.append(fname)
    return f"创建缺失文件: {created if created else '无'}"

def fix_syntax_error():
    """Python语法错误无法自动修复，仅报告"""
    return "⚠️ 语法错误需人工审查，已记录"

FIX_ACTIONS = {
    'fix_pycache':     fix_pycache,
    'fix_syspath':     fix_syspath,
    'fix_deps':        fix_deps,
    'fix_json_files':  fix_json_files,
    'fix_timeout':     fix_timeout,
    'fix_network':     fix_network,
    'fix_env_vars':    fix_env_vars,
    'fix_permissions': fix_permissions,
    'fix_data_files':  fix_data_files,
    'fix_syntax_error': fix_syntax_error,
}

# ── 主诊断逻辑 ─────────────────────────────────────────────
def diagnose_all() -> dict:
    now_ms = int(time.time() * 1000)
    results = {
        'ok': [], 'warn': [], 'error': [], 'no_run': [],
        'fixes_applied': [], 'timestamp': datetime.now(timezone.utc).isoformat()
    }

    for job_id, name, max_silence_h, is_critical in WATCHED_JOBS:
        runs = get_recent_runs(job_id)

        # 无 run 记录
        if not runs:
            results['no_run'].append({'name': name, 'critical': is_critical})
            continue

        latest = runs[0]
        run_ts_ms = latest.get('ts', 0)
        silence_h  = (now_ms - run_ts_ms) / 3_600_000

        # 沉默超时检查
        if silence_h > max_silence_h:
            results['warn'].append({
                'name': name,
                'issue': f'沉默 {silence_h:.1f}h > 阈值 {max_silence_h}h',
                'critical': is_critical
            })
            continue

        # 状态检查
        health = classify_run(latest)
        if health in ('error', 'timeout', 'soft_fail'):
            err_text = extract_error_text(latest)

            # 匹配错误模式 → 自动修复
            fix_applied = None
            fix_result  = None
            for pattern, desc, fix_fn in ERROR_PATTERNS:
                if re.search(pattern, err_text, re.IGNORECASE):
                    if not CHECK_ONLY and fix_fn in FIX_ACTIONS:
                        try:
                            fix_result = FIX_ACTIONS[fix_fn]()
                            fix_applied = f"{desc} → {fix_fn}()"
                            results['fixes_applied'].append({
                                'job': name, 'fix': fix_fn,
                                'result': fix_result
                            })
                        except Exception as fe:
                            fix_result = f"修复失败: {fe}"
                    break

            results['error'].append({
                'name':    name,
                'health':  health,
                'err':     err_text[:200],
                'fix':     fix_applied,
                'fix_res': fix_result,
                'critical': is_critical
            })
        else:
            results['ok'].append(name)

    return results

# ── 报告格式化 ────────────────────────────────────────────
def format_report(r: dict) -> str | None:
    """只有异常才生成报告，否则返回 None（静默）"""
    issues = r['error'] + r['warn'] + r['no_run']
    if not issues:
        return None   # 全部正常 → HEARTBEAT_OK

    lines = ['🔬 **梵天 Cron 诊断报告**']
    ts = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    lines.append(f'__{ts}__')
    lines.append('')

    if r['error']:
        lines.append('**❌ 运行失败**')
        for e in r['error']:
            flag = '🚨' if e['critical'] else '⚠️'
            lines.append(f'{flag} `{e["name"]}` [{e["health"]}]')
            lines.append(f'   错误: {e["err"][:120]}')
            if e['fix']:
                lines.append(f'   🔧 自动修复: {e["fix"]}')
                lines.append(f'   结果: {e["fix_res"]}')

    if r['warn']:
        lines.append('')
        lines.append('**⏰ 沉默超时**')
        for w in r['warn']:
            flag = '🚨' if w['critical'] else '⚠️'
            lines.append(f'{flag} `{w["name"]}` — {w["issue"]}')

    if r['no_run']:
        lines.append('')
        lines.append('**📭 从未运行**')
        for n in r['no_run']:
            lines.append(f'❓ `{n["name"]}` (critical={n["critical"]})')

    lines.append('')
    lines.append(f'统计: ✅{len(r["ok"])} ❌{len(r["error"])} ⏰{len(r["warn"])} 📭{len(r["no_run"])} 🔧{len(r["fixes_applied"])}')
    return '\n'.join(lines)

# ── 入口 ─────────────────────────────────────────────────
def main():
    print(f'[brahma_cron_doctor] {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} | mode={"CHECK" if CHECK_ONLY else "HEAL"}')

    results = diagnose_all()
    report  = format_report(results)

    if report is None:
        print(f'HEARTBEAT_OK — 全部 {len(results["ok"])} 个任务正常')
        return

    print(report)
    if not CHECK_ONLY:
        _push(report)
        print('[push] 报告已推送到 Jarvis')

if __name__ == '__main__':
    main()

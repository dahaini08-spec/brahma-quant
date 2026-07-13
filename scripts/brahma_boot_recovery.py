#!/usr/bin/env python3
"""
brahma_boot_recovery.py — 梵天重启容灾自恢复脚本 v1.0
设计院六方联合封印 2026-07-13 | 苏摩111授权

═══════════════════════════════════════════════════════════════
核心原则：
  宿主机/网关重启后，所有"idle"(无ID)的cron任务会丢失。
  本脚本作为"重启后第一个执行的任务"，负责：
    1. 检测所有关键cron是否存在
    2. 缺失的自动重新注册（含正确的 --to --channel delivery）
    3. 检测关键数据文件健康状态
    4. 推送恢复报告到 Jarvis

运行：
  python3 scripts/brahma_boot_recovery.py
  python3 scripts/brahma_boot_recovery.py --check-only  # 只检测不修复

重启后手动触发：
  cd /root/.openclaw/workspace/trading-system
  python3 scripts/brahma_boot_recovery.py
═══════════════════════════════════════════════════════════════
"""
import sys, os, json, subprocess, time, argparse
from pathlib import Path
from datetime import datetime, timezone

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
SCRIPTS= BASE / 'scripts'

# ── SSOT 推送配置 ──────────────────────────────────────────────────────
JARVIS_USER_ID  = "73295708"
MAIN_THREAD     = "019f309c-609b-7a75-a195-e221e5927c63"   # 主线程
SIGNAL_THREAD   = "019f443a-b891-70f1-8cb0-ed0c1e879db2"  # 信号线程
EXEC_THREAD     = "019f4448-76b9-7a64-b879-0c7e2a79a95c"  # 执行线程
REPORT_THREAD   = "019f15c9-e5a7-7551-afb8-50b2c7c46e10"  # 日报线程
SQUARE_THREAD   = "019f04e3-5d78-7cd0-b0fa-da25d2adb3ac"  # 广场线程

MAIN_TO  = f"{JARVIS_USER_ID}:thread:{MAIN_THREAD}"
SIG_TO   = f"{JARVIS_USER_ID}:thread:{SIGNAL_THREAD}"
EXEC_TO  = f"{JARVIS_USER_ID}:thread:{EXEC_THREAD}"
REP_TO   = f"{JARVIS_USER_ID}:thread:{REPORT_THREAD}"
SQ_TO    = f"{JARVIS_USER_ID}:thread:{SQUARE_THREAD}"

def now_utc(): return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def run(cmd: str) -> tuple[int, str]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()

def cron_exists(name: str) -> bool:
    """检查 cron 任务是否存在且有ID（已持久化）"""
    rc, out = run('openclaw cron list 2>/dev/null')
    for line in out.split('\n'):
        # 有ID的任务格式: <uuid>  <name>  ...
        if name in line:
            parts = line.split()
            if parts and len(parts[0]) == 36 and '-' in parts[0]:
                return True  # 有UUID = 持久化注册
    return False

def register_cron(name: str, schedule: str, message: str, to: str,
                  model: str = '', extra: str = '') -> tuple[bool, str]:
    """注册一个 cron 任务"""
    model_flag = f'--model "{model}"' if model else ''
    cmd = (
        f'openclaw cron add --name "{name}" {schedule} '
        f'--message \'{message}\' '
        f'--channel jarvis --to "{to}" --announce '
        f'{model_flag} {extra} 2>&1'
    )
    rc, out = run(cmd)
    return rc == 0, out[:200]

# ══════════════════════════════════════════════════════════════════════
# 关键 Cron 任务清单（重启后需恢复的任务）
# ══════════════════════════════════════════════════════════════════════
CRITICAL_CRONS = [
    # ── 自愈系统 ────────────────────────────────────────────────────
    {
        'name':     'brahma-self-heal',
        'schedule': '--every 30m',
        'to':       MAIN_TO,
        'model':    '',
        'message':  (
            'Run brahma system health check. '
            'Execute: cd /root/.openclaw/workspace/trading-system && '
            'python3 scripts/brahma_self_heal.py 2>&1 | tail -30. '
            'If any check fails, report the failed items. '
            'If all pass reply: HEARTBEAT_OK'
        ),
        'priority': 'P0',
        'desc':     '自愈引擎 每30分钟'
    },
    # ── 体制切换监控 ─────────────────────────────────────────────────
    {
        'name':     'regime-switch-monitor',
        'schedule': '--every 30m',
        'to':       SIG_TO,
        'model':    '',
        'message':  (
            'Check if BTC/ETH regime has switched. '
            'Run: cd /root/.openclaw/workspace/trading-system && '
            'python3 -c "import sys; sys.path.insert(0,\'brahma_brain\'); '
            'from brahma_brain.regime_state_machine import RegimeStateMachine; '
            'print(RegimeStateMachine().get_state(\'BTCUSDT\'))" 2>&1 | tail -5. '
            'Only announce if regime changed in last 30 minutes. Otherwise reply HEARTBEAT_OK'
        ),
        'priority': 'P0',
        'desc':     '体制切换监控 每30分钟'
    },
    # ── 广场早间帖 ──────────────────────────────────────────────────
    {
        'name':     '早间综合-Square',
        'schedule': '--at "cron 0 8 * * *" --timezone Asia/Shanghai',
        'to':       SQ_TO,
        'model':    'litellm/bedrock-claude-4-6-sonnet',
        'message':  (
            '发布币安广场早间综合帖。'
            '获取BTC/ETH当前价格和今日涨跌幅，'
            '结合梵天体制(BULL/BEAR/CHOP)，'
            '用中文写一篇300字左右的早间市场综合分析帖，'
            '风格：姓赵不宣KOL专业财经风，包含具体价格数据和今日关注重点。'
            '调用square_post工具发布到币安广场。'
        ),
        'priority': 'P0',
        'desc':     '广场早间帖 08:00 CST'
    },
    # ── 广场午盘快讯 ─────────────────────────────────────────────────
    {
        'name':     '⚡午盘快讯-Square',
        'schedule': '--at "cron 15 12 * * *" --timezone Asia/Shanghai',
        'to':       SQ_TO,
        'model':    'litellm/Qwen3.5-397B-A17B-SGLang',
        'message':  (
            '发布币安广场午盘快讯帖。'
            '获取BTC/ETH午盘价格，分析上午走势，'
            '用中文写150字左右的午盘快讯，'
            '包含关键支撑阻力位和下午策略方向。'
            '调用square_post工具发布。'
        ),
        'priority': 'P0',
        'desc':     '广场午盘帖 12:15 CST'
    },
    # ── 广场晚盘深度帖 ────────────────────────────────────────────────
    {
        'name':     '🌿晚盘深度帖-Square',
        'schedule': '--at "cron 15 20 * * *" --timezone Asia/Shanghai',
        'to':       SQ_TO,
        'model':    'litellm/bedrock-claude-4-6-sonnet',
        'message':  (
            '发布币安广场晚盘深度分析帖。'
            '获取BTC/ETH收盘趋势，'
            '用中文写400字左右的晚盘深度分析，'
            '包含今日体制总结、明日关键位、VIP策略方向。'
            '风格：专业量化交易员，数据驱动，调用square_post工具发布。'
        ),
        'priority': 'P0',
        'desc':     '广场晚盘帖 20:15 CST'
    },
    # ── 每日架构复盘 ─────────────────────────────────────────────────
    {
        'name':     'brahma-arch-review',
        'schedule': '--every 24h',
        'to':       REP_TO,
        'model':    '',
        'message':  (
            'Run daily brahma architecture review. '
            'Execute: cd /root/.openclaw/workspace/trading-system && '
            'python3 scripts/brahma_learning_loop.py 2>&1 | tail -20. '
            'Then summarize: total calibration records, WR by bucket, '
            'threshold suggestion, and top risk flags. Reply in Chinese.'
        ),
        'priority': 'P1',
        'desc':     '每日架构复盘'
    },
    # ── 6H信号监控 ──────────────────────────────────────────────────
    {
        'name':     'signal-watcher-6h',
        'schedule': '--every 6h',
        'to':       SIG_TO,
        'model':    '',
        'message':  (
            'Check brahma signal pipeline health every 6 hours. '
            'Run: cd /root/.openclaw/workspace/trading-system && '
            'tail -5 data/live_signal_log.jsonl && '
            'tail -3 data/auto_execute_log.jsonl. '
            'Report: last signal time, last execution, any BLOCKED reasons. '
            'Reply HEARTBEAT_OK if everything normal.'
        ),
        'priority': 'P1',
        'desc':     '6H信号监控'
    },
]

# ══════════════════════════════════════════════════════════════════════
# 关键数据文件健康检查
# ══════════════════════════════════════════════════════════════════════
CRITICAL_FILES = [
    {'path': DATA / 'regime_state.json',         'max_age_h': 2,  'name': '体制状态'},
    {'path': DATA / 'wuqu_positions.json',        'max_age_h': 2,  'name': '持仓快照'},
    {'path': DATA / 'live_signal_log.jsonl',      'max_age_h': 24, 'name': '信号日志'},
    {'path': DATA / 'auto_execute_log.jsonl',     'max_age_h': 48, 'name': '执行日志'},
    {'path': DATA / 'auto_learner_state.json',    'max_age_h': 48, 'name': '学习器状态'},
    {'path': DATA / 'live_performance_daily.json','max_age_h': 24, 'name': '持仓盈亏'},
    {'path': DATA / 'calibration_feedback.jsonl', 'max_age_h': 72, 'name': '信号校准日志'},
]


def check_files() -> list[dict]:
    """检查关键文件健康状态"""
    issues = []
    now = time.time()
    for fc in CRITICAL_FILES:
        p = Path(fc['path'])
        if not p.exists():
            issues.append({'name': fc['name'], 'status': 'MISSING', 'path': str(p)})
            continue
        age_h = (now - p.stat().st_mtime) / 3600
        if age_h > fc['max_age_h']:
            issues.append({
                'name': fc['name'], 'status': 'STALE',
                'age_h': round(age_h, 1), 'max_h': fc['max_age_h'],
                'path': str(p)
            })
    return issues


def run_recovery(check_only: bool = False) -> dict:
    """主恢复流程"""
    ts      = now_utc()
    fixed   = []
    skipped = []
    failed  = []

    print(f'[{ts}] brahma_boot_recovery 启动 check_only={check_only}')

    # ── 1. 检查并恢复 cron 任务 ────────────────────────────────────
    print('\n=== Cron 任务恢复检查 ===')
    for job in CRITICAL_CRONS:
        name = job['name']
        exists = cron_exists(name)
        if exists:
            print(f'  ✅ {name} 已存在，跳过')
            skipped.append(name)
            continue

        print(f'  🔴 {name} 缺失 ({job["priority"]}) → {"检查模式跳过" if check_only else "注册中..."}')

        if check_only:
            failed.append({'name': name, 'reason': '缺失(check-only模式)'})
            continue

        ok, msg = register_cron(
            name     = name,
            schedule = job['schedule'],
            message  = job['message'],
            to       = job['to'],
            model    = job.get('model', ''),
        )
        if ok:
            print(f'    ✅ 注册成功')
            fixed.append(name)
        else:
            print(f'    ❌ 注册失败: {msg[:100]}')
            failed.append({'name': name, 'reason': msg[:100]})

    # ── 2. 检查关键数据文件 ────────────────────────────────────────
    print('\n=== 数据文件健康检查 ===')
    file_issues = check_files()
    for issue in file_issues:
        status = issue['status']
        name   = issue['name']
        if status == 'MISSING':
            print(f'  🔴 {name}: 文件不存在 ({issue["path"]})')
        else:
            print(f'  🟡 {name}: 数据陈旧 {issue["age_h"]}h > {issue["max_h"]}h')

    # ── 3. 汇总报告 ────────────────────────────────────────────────
    summary = {
        'ts':          ts,
        'check_only':  check_only,
        'cron_fixed':  fixed,
        'cron_skip':   skipped,
        'cron_fail':   failed,
        'file_issues': file_issues,
        'status':      'OK' if not failed and not file_issues else 'PARTIAL',
    }

    print(f'\n=== 恢复结果 ===')
    print(f'  Cron修复: {len(fixed)}个  跳过: {len(skipped)}个  失败: {len(failed)}个')
    print(f'  文件问题: {len(file_issues)}个')
    print(f'  总状态: {summary["status"]}')

    # ── 4. 写入恢复日志 ────────────────────────────────────────────
    if not check_only:
        log_path = DATA / 'brahma_boot_recovery_log.jsonl'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(summary, ensure_ascii=False) + '\n')
        print(f'  日志已写入: {log_path}')

    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true', help='只检测不修复')
    args   = parser.parse_args()

    result = run_recovery(check_only=args.check_only)
    sys.exit(0 if result['status'] == 'OK' else 1)

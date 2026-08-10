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
soma_manager.py — 苏摩·梵天能量调度引擎 v1.0
设计院 2026-06-10

【命名由来】
  苏摩（Soma）= 印度神话中神界能量源泉
  积分 = 梵天系统运行的能量
  苏摩守护 = 确保能量不被浪费，核心任务永远有能量

【核心功能】
  1. 预算管理   每日积分上限，UTC 00:00自动重置
  2. 优先级调度 P0核心信号无限制，P4报告类低余额暂停
  3. 消耗记录   每次调用记录任务名+估算tokens
  4. 每日汇报   消耗排行 + 剩余百分比 + ROI参考

【优先级定义】
  P0 核心信号生成（trade_gateway / multi-scan）   → 无限制
  P1 Zone Watcher 重分析                         → 剩余>40%
  P2 猎手拉娜 / L2诊断                           → 剩余>30%
  P3 evening_brief / position_broadcast          → 剩余>20%
  P4 auto_learner / dharma_weekly / brahma360     → 剩余>10%
  P∞ exec:noai脚本                               → 永不限制（零消耗）

【调用方式（极简，一行接入）】
  from soma_manager import can_run, record_usage
  if not can_run(priority=2, task='猎手拉娜'): exit(0)
  # ... 任务逻辑 ...
  record_usage('猎手拉娜', tokens=3000)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_DIR        = Path(__file__).parent.parent
_STATE_FILE = _DIR / 'data' / 'soma_state.json'

# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════

DAILY_BUDGET = 300_000      # 每日token预算
P0_RESERVE   = 50_000       # P0保留区（前5万无条件给核心信号）

PRIORITY_THRESHOLD = {
    0: 0.0,    # P0 核心信号，无限制
    1: 0.40,   # P1 剩余>40%才运行
    2: 0.30,   # P2 剩余>30%才运行
    3: 0.20,   # P3 剩余>20%才运行
    4: 0.10,   # P4 剩余>10%才运行
}

# 各任务估算tokens（用于预测性判断）
TASK_COST_ESTIMATE = {
    'trade_gateway':      3000,
    'multi-scan':         2000,
    'zone_watcher':       2000,
    'brahma_analyze':     2000,
    '猎手拉娜':            3000,
    'brahma360_l2_diag':  1000,
    'evening_brief':      1000,
    'position_broadcast':  500,
    'dharma_weekly':      2000,
    'auto_learner':       3000,
    'brahma360_auditor':  1500,
    'signal_lifecycle':    500,
    'pipeline_bridge':     800,
    'oi_alert':            500,
    'event_trigger':       800,
    'live_signal_settler': 600,
    'milestone_monitor':   500,
    'sync_all':            400,
    'brahma_health':       600,
}

DEFAULT_TASK_COST = 1000   # 未知任务默认估算


# ══════════════════════════════════════════════════════════
# 状态管理
# ══════════════════════════════════════════════════════════

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            s = json.loads(_STATE_FILE.read_text())
            # 跨天重置
            if s.get('date') != _today_str():
                return _fresh_state()
            return s
    except Exception:
        pass
    return _fresh_state()


def _fresh_state() -> dict:
    return {
        'date':      _today_str(),
        'budget':    DAILY_BUDGET,
        'used':      0,
        'records':   [],   # [{ts, task, tokens, priority}]
        'resets':    0,
    }


def _save_state(s: dict):
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception as e:
        pass  # [静默]


# ══════════════════════════════════════════════════════════
# 核心接口
# ══════════════════════════════════════════════════════════

def get_remaining() -> int:
    """返回今日剩余tokens"""
    s = _load_state()
    return max(0, s['budget'] - s['used'])


def get_remaining_pct() -> float:
    """返回剩余百分比 0.0~1.0"""
    s = _load_state()
    return max(0.0, (s['budget'] - s['used']) / s['budget'])


def can_run(priority: int = 2, task: str = 'unknown') -> bool:
    """
    判断当前积分余量是否允许运行该优先级任务

    参数：
      priority : 0=核心 1=重要 2=标准 3=补充 4=低优先
      task     : 任务名称（用于日志）

    返回：
      True  = 可以运行
      False = 积分不足，跳过
    """
    # P0永远允许
    if priority == 0:
        return True

    remaining_pct = get_remaining_pct()
    threshold     = PRIORITY_THRESHOLD.get(priority, 0.20)

    if remaining_pct >= threshold:
        pass  # [静默]
        return True
    else:
        remaining = get_remaining()
        pass  # [静默]
        return False


def record_usage(task: str, tokens: int = 0, priority: int = 2):
    """
    记录一次AI调用的积分消耗

    参数：
      task     : 任务名称
      tokens   : 实际消耗tokens（0=使用估算值）
      priority : 任务优先级
    """
    if tokens <= 0:
        tokens = TASK_COST_ESTIMATE.get(task, DEFAULT_TASK_COST)

    s = _load_state()
    s['used'] += tokens
    s['records'].append({
        'ts':       int(time.time()),
        'task':     task,
        'tokens':   tokens,
        'priority': priority,
    })
    # 只保留最近100条记录（节省空间）
    if len(s['records']) > 100:
        s['records'] = s['records'][-100:]
    _save_state(s)

    remaining = s['budget'] - s['used']
    pct       = remaining / s['budget']
    pass  # [静默]

    # 低余额预警
    if pct < 0.15:
        pass  # [静默]


def daily_summary() -> dict:
    """每日消耗汇报"""
    s = _load_state()
    records  = s.get('records', [])
    used     = s.get('used', 0)
    budget   = s.get('budget', DAILY_BUDGET)
    remaining = max(0, budget - used)

    # 按任务统计
    task_stats = {}
    for r in records:
        t = r['task']
        task_stats[t] = task_stats.get(t, 0) + r['tokens']

    # 排行
    top_tasks = sorted(task_stats.items(), key=lambda x: x[1], reverse=True)[:5]

    summary = {
        'date':      s.get('date'),
        'budget':    budget,
        'used':      used,
        'remaining': remaining,
        'pct_used':  round(used / budget * 100, 1),
        'calls':     len(records),
        'top_tasks': top_tasks,
    }
    return summary


def format_summary(s: dict = None) -> str:
    if s is None:
        s = daily_summary()

    bar_used = '█' * int(s['pct_used'] / 5)
    bar_free = '░' * (20 - len(bar_used))
    status = '🟢' if s['pct_used'] < 60 else ('🟡' if s['pct_used'] < 80 else '🔴')

    top = '\n'.join(
        f"  {i+1}. {t}  {tok:,}tok"
        for i, (t, tok) in enumerate(s['top_tasks'])
    )

    return (
        f"苏摩·能量日报 {s['date']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{status} {bar_used}{bar_free} {s['pct_used']:.1f}%\n"
        f"已用: {s['used']:,} / {s['budget']:,} tokens\n"
        f"剩余: {s['remaining']:,} tokens  调用: {s['calls']}次\n"
        f"\n消耗TOP5：\n{top if top else '  暂无记录'}"
    )


def status() -> str:
    """快速状态一行输出"""
    pct = get_remaining_pct()
    rem = get_remaining()
    s = _load_state()
    label = '🟢充裕' if pct > 0.6 else ('🟡适中' if pct > 0.3 else '🔴告急')
    return f'[Soma] {label} 今日剩余={rem:,}tok({pct:.0%}) 已调用{len(s.get("records",[]))}次'


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'status':
        print(status())
    elif cmd == 'summary':
        print(format_summary())
    elif cmd == 'reset':
        _save_state(_fresh_state())
        pass  # [静默]
    elif cmd == 'test':
        print('=== 苏摩调度测试 ===')
        print(f'P0: {can_run(0, "trade_gateway")}')
        print(f'P2: {can_run(2, "猎手拉娜")}')
        print(f'P4: {can_run(4, "auto_learner")}')
        record_usage('trade_gateway', 2800, 0)
        record_usage('猎手拉娜', 3100, 2)
        print(format_summary())


# ══════════════════════════════════════════════════════════
# 苏摩·新任务审核门控（与 soma_ai_registry 联动）
# ══════════════════════════════════════════════════════════

def request_new_task(task_def: dict) -> str:
    """
    申请新增AI任务，返回审核卡片（推送给用户）
    用户回复111批准后，调用approve_new_task()激活

    task_def: {name, purpose, priority, freq, est_tokens, est_daily, cron_message}
    """
    try:
        import sys as _sys3
        _sys3.path.insert(0, str(_DIR / 'scripts'))
        from soma_ai_registry import submit_new_task
        result = submit_new_task(task_def)
        if result['ok']:
            return result['review']
        return f'[Soma] 提交失败: {result["reason"]}'
    except Exception as e:
        return f'[Soma] 审核系统异常: {e}'


def approve_new_task(task_name: str, code: str) -> str:
    """用户111批准后激活任务"""
    try:
        from soma_ai_registry import approve_task
        result = approve_task(task_name, code)
        return result.get('msg') or result.get('reason', '未知错误')
    except Exception as e:
        return f'[Soma] 审核系统异常: {e}'

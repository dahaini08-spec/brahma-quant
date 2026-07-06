#!/usr/bin/env python3
"""
soma_ai_registry.py — 苏摩·AI任务注册管理中心 v1.0
设计院 2026-06-10

【核心职责】
  1. 管理所有AI消耗任务的注册台账
  2. 新增任务必须经过苏摩审核 + 口令111确认才可激活
  3. 提供任务全景视图：状态/优先级/频率/预计消耗
  4. 任何新Cron AI任务在注册前以PENDING状态存在

【口令规则】
  新增AI任务 → 提交审核 → 展示预算影响 → 等待111确认 → 激活
  批准口令：111
  拒绝/取消：直接不回复，任务保持PENDING
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_DIR       = Path(__file__).parent.parent
_REG_FILE  = _DIR / 'data' / 'soma_ai_registry.json'

APPROVE_CODE = '111'

# ══════════════════════════════════════════════════════════
# 任务优先级定义
# ══════════════════════════════════════════════════════════
PRIORITY_LABELS = {
    0: 'P0·核心信号',
    1: 'P1·重要分析',
    2: 'P2·标准扫描',
    3: 'P3·补充报告',
    4: 'P4·低优先级',
}

# ══════════════════════════════════════════════════════════
# 初始化注册台账（现有AI任务）
# ══════════════════════════════════════════════════════════

INITIAL_REGISTRY = [
    {
        'id':          'e832c8a0',
        'name':        'multi-scan-BTC',
        'type':        'ai_cron',
        'priority':    0,
        'freq':        '每日2次(UTC 1,13)',
        'est_tokens':  2000,
        'est_daily':   4000,
        'purpose':     'BTC深度信号分析，核心信号生成',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '2682e44c',
        'name':        'multi-scan-ETH',
        'type':        'ai_cron',
        'priority':    0,
        'freq':        '每日2次(UTC 1,13)',
        'est_tokens':  2000,
        'est_daily':   4000,
        'purpose':     'ETH深度信号分析，核心信号生成',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '47b30b39',
        'name':        'multi-scan-SOL',
        'type':        'ai_cron',
        'priority':    0,
        'freq':        '每日2次(UTC 1,13)',
        'est_tokens':  2000,
        'est_daily':   4000,
        'purpose':     'SOL深度信号分析，核心信号生成',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '21f39d7e',
        'name':        'multi-scan-DOGE',
        'type':        'ai_cron',
        'priority':    0,
        'freq':        '每日2次(UTC 1,13)',
        'est_tokens':  2000,
        'est_daily':   4000,
        'purpose':     'DOGE深度信号分析，核心信号生成',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '609b88a6',
        'name':        'multi-scan-BNB',
        'type':        'ai_cron',
        'priority':    0,
        'freq':        '每日2次(UTC 1,13)',
        'est_tokens':  2000,
        'est_daily':   4000,
        'purpose':     'BNB深度信号分析，核心信号生成',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '4a6112d8',
        'name':        '猎手拉娜-4H',
        'type':        'ai_cron',
        'priority':    2,
        'freq':        '每日4次(UTC 5,9,13,17)',
        'est_tokens':  3000,
        'est_daily':   12000,
        'purpose':     '全市场扫描，发现非主力标的机会',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          'f3f69fbc',
        'name':        'signal-watcher-5m',
        'type':        'ai_cron',
        'priority':    1,
        'freq':        '每5分钟',
        'est_tokens':  500,
        'est_daily':   3000,
        'purpose':     '信号预警推送，贴近入场区通知',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          'efa63962',
        'name':        'auto-learner-daily',
        'type':        'ai_cron',
        'priority':    4,
        'freq':        '每日1次',
        'est_tokens':  3000,
        'est_daily':   3000,
        'purpose':     '自动学习历史结算信号，优化评分权重',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
    {
        'id':          '0fa322fb',
        'name':        'square-weekly-report',
        'type':        'ai_cron',
        'priority':    4,
        'freq':        '每周1次',
        'est_tokens':  2000,
        'est_daily':   286,
        'purpose':     '广场周报，社区运营内容',
        'status':      'ACTIVE',
        'approved_at': '2026-06-01',
        'approved_by': 'genesis',
    },
]


# ══════════════════════════════════════════════════════════
# 注册台账读写
# ══════════════════════════════════════════════════════════

def _load() -> list:
    if _REG_FILE.exists():
        try:
            return json.loads(_REG_FILE.read_text())
        except Exception:
            pass
    # 首次初始化
    _save(INITIAL_REGISTRY)
    return INITIAL_REGISTRY[:]


def _save(registry: list):
    _REG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REG_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


# ══════════════════════════════════════════════════════════
# 核心接口
# ══════════════════════════════════════════════════════════

def list_tasks(status_filter: str = None) -> list:
    """列出所有AI任务"""
    reg = _load()
    if status_filter:
        return [t for t in reg if t.get('status') == status_filter]
    return reg


def get_task(task_id: str) -> dict:
    reg = _load()
    return next((t for t in reg if t['id'] == task_id or t['name'] == task_id), None)


def submit_new_task(task_def: dict) -> dict:
    """
    提交新AI任务申请（进入PENDING审核状态）

    task_def必须包含：
      name, purpose, priority, freq, est_tokens, est_daily
    """
    reg = _load()
    # 检查重名
    if any(t['name'] == task_def.get('name') for t in reg):
        return {'ok': False, 'reason': f'任务名已存在: {task_def["name"]}'}

    new_task = {
        'id':          task_def.get('id', f'pending_{int(time.time())}'),
        'name':        task_def['name'],
        'type':        task_def.get('type', 'ai_cron'),
        'priority':    task_def.get('priority', 3),
        'freq':        task_def.get('freq', '未指定'),
        'est_tokens':  task_def.get('est_tokens', 1000),
        'est_daily':   task_def.get('est_daily', 1000),
        'purpose':     task_def.get('purpose', ''),
        'status':      'PENDING',
        'submitted_at': datetime.now(timezone.utc).isoformat()[:16],
        'approved_at': None,
        'approved_by': None,
        'cron_message': task_def.get('cron_message', ''),
    }
    reg.append(new_task)
    _save(reg)
    return {'ok': True, 'task': new_task, 'review': _review_card(new_task, reg)}


def approve_task(task_id: str, code: str) -> dict:
    """审核批准任务（口令111）"""
    if code != APPROVE_CODE:
        return {'ok': False, 'reason': f'口令错误，拒绝批准'}

    reg = _load()
    for t in reg:
        if t['id'] == task_id or t['name'] == task_id:
            if t['status'] != 'PENDING':
                return {'ok': False, 'reason': f'任务状态={t["status"]}，非PENDING'}
            t['status']      = 'ACTIVE'
            t['approved_at'] = datetime.now(timezone.utc).isoformat()[:16]
            t['approved_by'] = 'user_111'
            _save(reg)
            return {'ok': True, 'task': t,
                    'msg': f'✅ 苏摩批准：{t["name"]} 已激活'}
    return {'ok': False, 'reason': f'任务不存在: {task_id}'}


def reject_task(task_id: str) -> dict:
    """拒绝/删除PENDING任务"""
    reg = _load()
    before = len(reg)
    reg = [t for t in reg if not (
        (t['id'] == task_id or t['name'] == task_id) and t['status'] == 'PENDING'
    )]
    if len(reg) < before:
        _save(reg)
        return {'ok': True, 'msg': f'❌ 任务 {task_id} 已拒绝删除'}
    return {'ok': False, 'reason': '任务不存在或非PENDING状态'}


def suspend_task(task_id: str) -> dict:
    """暂停活跃任务"""
    reg = _load()
    for t in reg:
        if t['id'] == task_id or t['name'] == task_id:
            t['status'] = 'SUSPENDED'
            _save(reg)
            return {'ok': True, 'msg': f'⏸️ {t["name"]} 已暂停'}
    return {'ok': False, 'reason': '任务不存在'}


def resume_task(task_id: str, code: str) -> dict:
    """恢复暂停任务（也需要111）"""
    if code != APPROVE_CODE:
        return {'ok': False, 'reason': '口令错误'}
    reg = _load()
    for t in reg:
        if t['id'] == task_id or t['name'] == task_id:
            t['status'] = 'ACTIVE'
            _save(reg)
            return {'ok': True, 'msg': f'▶️ {t["name"]} 已恢复'}
    return {'ok': False, 'reason': '任务不存在'}


# ══════════════════════════════════════════════════════════
# 格式化输出
# ══════════════════════════════════════════════════════════

def _review_card(task: dict, all_tasks: list) -> str:
    """新任务审核卡片"""
    active = [t for t in all_tasks if t.get('status') == 'ACTIVE']
    current_daily = sum(t.get('est_daily', 0) for t in active)
    new_daily     = task.get('est_daily', 0)
    total_after   = current_daily + new_daily
    budget_pct    = total_after / 300000 * 100

    p_label = PRIORITY_LABELS.get(task['priority'], f'P{task["priority"]}')

    return f"""
🔔 苏摩·新任务审核申请
━━━━━━━━━━━━━━━━━━━
任务名:   {task['name']}
优先级:   {p_label}
频率:     {task['freq']}
用途:     {task['purpose']}
估算消耗: {task['est_tokens']:,} tokens/次
         {task['est_daily']:,} tokens/天

📊 预算影响：
  当前每日消耗: {current_daily:,} tokens ({current_daily/300000*100:.1f}%)
  新增后每日:   {total_after:,} tokens ({budget_pct:.1f}%)
  剩余预算:     {300000-total_after:,} tokens
━━━━━━━━━━━━━━━━━━━
回复 111 批准激活此任务"""


def format_registry() -> str:
    """完整注册台账视图"""
    reg = _load()
    active    = [t for t in reg if t['status'] == 'ACTIVE']
    pending   = [t for t in reg if t['status'] == 'PENDING']
    suspended = [t for t in reg if t['status'] == 'SUSPENDED']

    total_daily = sum(t.get('est_daily', 0) for t in active)
    budget_pct  = total_daily / 300000 * 100
    bar = '█' * int(budget_pct / 5) + '░' * (20 - int(budget_pct / 5))

    lines = [
        '苏摩·AI任务注册台账',
        '━━━━━━━━━━━━━━━━━━━',
        f'预算占用: {bar} {budget_pct:.1f}%',
        f'每日消耗: {total_daily:,} / 150,000 tokens',
        f'活跃: {len(active)}  待审: {len(pending)}  暂停: {len(suspended)}',
        '',
    ]

    # 按优先级分组显示
    for p in range(5):
        p_tasks = [t for t in active if t.get('priority') == p]
        if not p_tasks:
            continue
        lines.append(f'── {PRIORITY_LABELS[p]} ──')
        for t in p_tasks:
            lines.append(
                f'  ✅ {t["name"]:<25} {t["freq"]:<15} '
                f'{t["est_daily"]:>6,}tok/天'
            )

    if pending:
        lines.append('')
        lines.append('── ⏳ 待审核 ──')
        for t in pending:
            lines.append(f'  🔔 {t["name"]} | {t["purpose"]} | '
                         f'{t["est_daily"]:,}tok/天 | 回复111批准')

    if suspended:
        lines.append('')
        lines.append('── ⏸️ 已暂停 ──')
        for t in suspended:
            lines.append(f'  ⏸️ {t["name"]}')

    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'

    if cmd == 'list':
        print(format_registry())
    elif cmd == 'approve' and len(sys.argv) >= 4:
        result = approve_task(sys.argv[2], sys.argv[3])
        print(result['msg'] if result['ok'] else result['reason'])
    elif cmd == 'reject' and len(sys.argv) >= 3:
        result = reject_task(sys.argv[2])
        print(result.get('msg') or result.get('reason'))
    elif cmd == 'suspend' and len(sys.argv) >= 3:
        result = suspend_task(sys.argv[2])
        print(result.get('msg') or result.get('reason'))
    elif cmd == 'init':
        _save(INITIAL_REGISTRY)
        print(f'✅ 注册台账初始化完成，{len(INITIAL_REGISTRY)}个任务')

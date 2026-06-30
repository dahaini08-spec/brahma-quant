#!/usr/bin/env python3
"""
command_register.py — 梵天全局指令寄存器 v1.0
设计院 2026-05-30

解决问题：人工下达指令后，所有正在运行/即将运行的cron任务必须立即同步
原架构缺陷：各任务独立运行，不知道人工已下达「静默/取消/覆盖」指令

机制：
  所有任务启动前 → 先调用 check_override(domain)
  返回 SILENCED → 立即 HEARTBEAT_OK，不执行任何报警/交易
  返回 ACTIVE   → 正常执行

指令存储：/tmp/command_register.json（内存级，Gateway重启后清空=自动恢复）
持久存储：data/command_register_persist.json（跨重启有效，需人工清除）
"""
import json, time, os
from pathlib import Path

BASE = Path(__file__).parent.parent
REGISTER_FILE     = Path('/tmp/command_register.json')
PERSIST_FILE      = BASE / 'data/command_register_persist.json'

# 域定义
DOMAIN_CIRCUIT_BREAKER = 'circuit_breaker'
DOMAIN_TRADING         = 'trading'
DOMAIN_ALERTS          = 'alerts'
DOMAIN_ALL             = 'ALL'   # 全局静默


def _load() -> dict:
    reg = {}
    # 先读持久化（跨重启有效）
    try:
        if PERSIST_FILE.exists():
            reg.update(json.loads(PERSIST_FILE.read_text()))
    except: pass
    # 再读内存级（优先级更高）
    try:
        if REGISTER_FILE.exists():
            mem = json.loads(REGISTER_FILE.read_text())
            reg.update(mem)
    except: pass
    return reg


def _save(reg: dict, persist: bool = False):
    REGISTER_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    if persist:
        PERSIST_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2))


def check_override(domain: str) -> tuple:
    """
    检查指定域是否被人工覆盖
    返回 (silenced: bool, reason: str)

    所有任务第一行调用：
        from command_register import check_override, DOMAIN_CIRCUIT_BREAKER
        silenced, reason = check_override(DOMAIN_CIRCUIT_BREAKER)
        if silenced:
            print(f'HEARTBEAT_OK (指令覆盖: {reason})')
            sys.exit(0)
    """
    reg = _load()
    now = time.time()

    # 检查 ALL 全局静默
    all_entry = reg.get(DOMAIN_ALL)
    if all_entry and _is_active(all_entry, now):
        return True, f"全局静默: {all_entry.get('reason','')}"

    # 检查具体域
    entry = reg.get(domain)
    if entry and _is_active(entry, now):
        return True, f"{domain}覆盖: {entry.get('reason','')}"

    return False, ''


def _is_active(entry: dict, now: float) -> bool:
    if entry.get('action') not in ('SILENCE', 'DISABLE', 'HUMAN_OVERRIDE'):
        return False
    until = entry.get('until', 0)
    if until == -1:  # 永久（直到人工解除）
        return True
    return now < until


def set_override(domain: str, reason: str = 'human override',
                 duration_hours: float = 2.0, persist: bool = False):
    """
    设置覆盖指令。duration_hours=-1 表示永久（直到人工解除）
    """
    reg = _load()
    until = -1 if duration_hours < 0 else time.time() + duration_hours * 3600
    reg[domain] = {
        'action':  'HUMAN_OVERRIDE',
        'reason':  reason,
        'until':   until,
        'set_at':  time.time(),
    }
    _save(reg, persist=persist)
    exp = '永久' if until == -1 else f'{duration_hours}H后自动恢复'
    print(f'✅ [{domain}] 已设置覆盖 ({exp}): {reason}')


def clear_override(domain: str):
    """解除覆盖"""
    reg = _load()
    if domain in reg:
        del reg[domain]
        _save(reg)
        print(f'✅ [{domain}] 覆盖已解除')
    else:
        print(f'[{domain}] 无覆盖记录')


def clear_all():
    """解除所有覆盖"""
    _save({})
    if PERSIST_FILE.exists():
        PERSIST_FILE.write_text('{}')
    print('✅ 所有覆盖已解除')


def status():
    reg = _load()
    now = time.time()
    if not reg:
        print('指令寄存器: 空（所有任务正常运行）')
        return
    print('=== 指令寄存器状态 ===')
    for domain, entry in reg.items():
        active = _is_active(entry, now)
        until = entry.get('until', 0)
        if until == -1:
            exp = '永久'
        else:
            remaining = max(0, until - now)
            exp = f'剩余{remaining/3600:.1f}H'
        flag = '🔴 激活' if active else '✅ 已过期'
        print(f'  {flag} [{domain}] {entry.get("reason","")} ({exp})')


if __name__ == '__main__':
    import sys, argparse
    p = argparse.ArgumentParser(description='梵天全局指令寄存器')
    p.add_argument('--status',        action='store_true', help='查看状态')
    p.add_argument('--silence',       metavar='DOMAIN',    help='静默域')
    p.add_argument('--silence-all',   action='store_true', help='全局静默')
    p.add_argument('--clear',         metavar='DOMAIN',    help='解除域覆盖')
    p.add_argument('--clear-all',     action='store_true', help='解除所有覆盖')
    p.add_argument('--hours',         type=float, default=2.0)
    p.add_argument('--persist',       action='store_true', help='跨重启有效')
    p.add_argument('--reason',        default='human override')
    args = p.parse_args()

    if args.status:           status()
    elif args.silence_all:    set_override(DOMAIN_ALL,    args.reason, args.hours, args.persist)
    elif args.silence:        set_override(args.silence,  args.reason, args.hours, args.persist)
    elif args.clear_all:      clear_all()
    elif args.clear:          clear_override(args.clear)
    else:                     status()

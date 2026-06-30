#!/usr/bin/env python3
"""
clean_stale_signals.py — 结构失效信号清理器
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
清理逻辑（优先级顺序）：
  1. 价格已穿越止损  → 结构失效，立即清除
  2. 价格已大幅偏离入场区（>5%）→ 入场机会已消失
  3. 体制切换（原信号体制 ≠ 当前体制）→ 上下文失效
  4. 已 SUPERSEDED / EXPIRED / SENT(CHOP体制) → 陈旧

不使用硬编码时间，以结构判断为准。
"""
import os, sys, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent

def get_current_price(symbol: str) -> float:
    """从 brahma_cache 取最新价格"""
    try:
        ticker_file = BASE / 'data' / 'brahma_cache' / f'{symbol}_ticker.json'
        if ticker_file.exists():
            d = json.loads(ticker_file.read_text())
            return float(d.get('lastPrice') or d.get('price') or 0)
    except:
        pass
    return 0.0

def get_current_regime(symbol: str) -> str:
    """从 regime_state 取当前体制"""
    try:
        rs = json.loads((BASE / 'data' / 'regime_state.json').read_text())
        syms = rs.get('symbols', rs.get('regimes', {}))
        if isinstance(syms, dict):
            info = syms.get(symbol, {})
            if isinstance(info, dict):
                return info.get('regime', info.get('label', ''))
            return str(info)
    except:
        pass
    # fallback: brahma_state
    try:
        state = json.loads((BASE / 'data' / 'brahma_state.json').read_text())
        regime = state.get('regime', {})
        if isinstance(regime, dict):
            return regime.get('label', regime.get('regime', ''))
        return str(regime)
    except:
        return ''

def is_structure_failed(item: dict, current_price: float, current_regime: str) -> tuple[bool, str]:
    """
    返回 (True, 原因) 如果结构已失效
    """
    direction = item.get('direction') or item.get('signal_dir', '')
    entry_lo  = float(item.get('entry_lo') or 0)
    entry_hi  = float(item.get('entry_hi') or 0)
    stop_loss = float(item.get('stop_loss') or 0)
    signal_regime = item.get('regime', '')
    status = item.get('status', '')

    # 0. 已非PENDING状态（SUPERSEDED/EXPIRED/SENT）
    if status in ('SUPERSEDED', 'EXPIRED'):
        return True, f'状态={status}'
    
    if current_price <= 0:
        return False, ''  # 无价格数据，不清除

    # 1. 价格已穿越止损（结构最严重失效）
    if stop_loss > 0:
        if direction in ('LONG', 'long') and current_price < stop_loss:
            return True, f'价格${current_price:.4f} < 止损${stop_loss:.4f}，LONG结构失效'
        if direction in ('SHORT', 'short') and current_price > stop_loss:
            return True, f'价格${current_price:.4f} > 止损${stop_loss:.4f}，SHORT结构失效'

    # 2. 价格大幅偏离入场区（>5%）
    if entry_lo > 0 and entry_hi > 0:
        entry_mid = (entry_lo + entry_hi) / 2
        dev_pct = abs(current_price - entry_mid) / entry_mid * 100
        if dev_pct > 5.0:
            # 进一步判断：方向性偏离（不是"尚未到达"而是"已远离"）
            if direction in ('LONG', 'long') and current_price > entry_hi * 1.05:
                return True, f'价格${current_price:.4f} 已远超LONG入场区，错过机会（偏离{dev_pct:.1f}%）'
            if direction in ('SHORT', 'short') and current_price < entry_lo * 0.95:
                return True, f'价格${current_price:.4f} 已远低于SHORT入场区，错过机会（偏离{dev_pct:.1f}%）'

    # 3. 体制不一致（CHOP体制下的方向信号失效）
    if current_regime and signal_regime:
        chop_regimes = {'CHOP_MID', 'CHOP_LOW', 'CHOP_HIGH'}
        if current_regime in chop_regimes and signal_regime not in chop_regimes:
            return True, f'体制从{signal_regime}→{current_regime}，方向性结构失效'
        # 体制方向反转（BULL→BEAR 或 BEAR→BULL）
        if 'BULL' in signal_regime and 'BEAR' in current_regime:
            return True, f'体制反转：{signal_regime}→{current_regime}'
        if 'BEAR' in signal_regime and 'BULL' in current_regime:
            return True, f'体制反转：{signal_regime}→{current_regime}'

    return False, ''


def clean_dd1_pending(dry_run=False):
    pending_file = BASE / 'data' / 'dd1_pending.json'
    if not pending_file.exists():
        print('[DD1] dd1_pending.json 不存在')
        return 0

    q = json.loads(pending_file.read_text())
    kept, removed = [], []

    for item in q:
        meta = item.get('meta') or {}
        symbol = meta.get('symbol') or item.get('symbol', '')
        direction = meta.get('direction') or item.get('direction', '')
        signal_regime = meta.get('regime') or item.get('regime', '')

        # 补全字段（部分字段在meta里，部分在顶层）
        full_item = {
            'direction': direction,
            'entry_lo':  item.get('entry_lo') or meta.get('entry_lo', 0),
            'entry_hi':  item.get('entry_hi') or meta.get('entry_hi', 0),
            'stop_loss': item.get('stop_loss') or meta.get('stop_loss', 0),
            'regime':    signal_regime,
            'status':    item.get('status', 'PENDING'),
        }

        price = get_current_price(symbol) if symbol else 0
        regime = get_current_regime(symbol) if symbol else ''

        failed, reason = is_structure_failed(full_item, price, regime)
        if failed:
            removed.append((item.get('task_id','?'), symbol, direction, reason))
        else:
            kept.append(item)

    print(f'[DD1] 扫描 {len(q)} 条 | 结构失效 {len(removed)} 条 | 保留 {len(kept)} 条')
    for tid, sym, d, reason in removed:
        print(f'  清除 [{tid}] {sym} {d}: {reason}')

    if not dry_run and removed:
        pending_file.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
        print(f'[DD1] ✅ 已写入（保留{len(kept)}条）')

    return len(removed)


def clean_queue_state(dry_run=False):
    qs_file = BASE / 'data' / 'queue_state.json'
    if not qs_file.exists():
        print('[QS] queue_state.json 不存在')
        return 0

    qs = json.loads(qs_file.read_text())
    queue = qs.get('queue', [])
    kept, removed = [], []

    for item in queue:
        symbol = item.get('symbol', '')
        direction = item.get('signal_dir') or item.get('direction', '')
        signal_regime = item.get('regime', '')
        full_item = {
            'direction': direction,
            'entry_lo':  item.get('entry_lo', 0),
            'entry_hi':  item.get('entry_hi', 0),
            'stop_loss': item.get('stop_loss', 0),
            'regime':    signal_regime,
            'status':    'PENDING',
        }
        price = get_current_price(symbol) if symbol else 0
        regime = get_current_regime(symbol) if symbol else ''

        failed, reason = is_structure_failed(full_item, price, regime)
        if failed:
            removed.append((symbol, direction, signal_regime, reason))
        else:
            kept.append(item)

    print(f'[QS] 扫描 {len(queue)} 条 | 结构失效 {len(removed)} 条 | 保留 {len(kept)} 条')
    for sym, d, reg, reason in removed:
        print(f'  清除 {sym} {d} ({reg}): {reason}')

    if not dry_run and removed:
        qs['queue'] = kept
        qs_file.write_text(json.dumps(qs, indent=2, ensure_ascii=False))
        print(f'[QS] ✅ 已写入（保留{len(kept)}条）')

    return len(removed)


if __name__ == '__main__':
    dry = '--dry' in sys.argv
    if dry:
        print('=== DRY RUN 模式（不写入）===')
    print()
    n1 = clean_dd1_pending(dry_run=dry)
    print()
    n2 = clean_queue_state(dry_run=dry)
    print()
    print(f'总计清除: {n1+n2} 条结构失效信号')

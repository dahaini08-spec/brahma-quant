#!/usr/bin/env python3
"""
position_state_monitor.py — 梵天持仓状态监控
设计院 2026-07-20

功能：
  1. 对比 brahma_state.json vs Binance实际持仓
  2. 若 state 超过 2H 未更新 → 推送告警
  3. 若 state 持仓与实际不符 → 推送告警
  4. 持仓接近止损/止盈 → 推送预警

cron: 每小时运行一次
"""
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
STATE_FILE = BASE_DIR / 'brahma_brain' / 'brahma_state.json'
MAX_STATE_AGE_HOURS = 2

# 推送配置（从system_config读）
try:
    sys.path.insert(0, str(BASE_DIR / 'scripts'))
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    PUSH_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    PUSH_TARGET = None


def push_alert(msg: str):
    """推送告警到Jarvis"""
    if not PUSH_TARGET:
        print(f'[ALERT] {msg}')
        return
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', PUSH_TARGET,
             '--message', msg],
            timeout=10, capture_output=True
        )
    except Exception as e:
        print(f'推送失败: {e}')


def check_state_freshness():
    """检查 brahma_state.json 是否过期"""
    if not STATE_FILE.exists():
        push_alert('🚨 brahma_state.json 不存在！梵天状态文件丢失')
        return False

    with open(STATE_FILE) as f:
        state = json.load(f)

    last_update_str = state.get('last_update', '')
    if not last_update_str:
        push_alert('⚠️ brahma_state.json 无 last_update 字段，状态可能陈旧')
        return False

    try:
        last_update = datetime.fromisoformat(last_update_str.replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - last_update
        age_hours = age.total_seconds() / 3600

        if age_hours > MAX_STATE_AGE_HOURS:
            push_alert(
                f'⚠️ brahma_state.json 已 {age_hours:.1f}H 未更新\n'
                f'上次更新: {last_update_str}\n'
                f'→ ws_guardian 可能已停止运行，请检查'
            )
            return False
        else:
            print(f'✅ state 新鲜度正常 (更新于 {age_hours:.1f}H 前)')
            return True
    except Exception as e:
        push_alert(f'⚠️ brahma_state.json 时间解析失败: {e}')
        return False


def check_position_sync():
    """检查 brahma_state 持仓 vs Binance 实际持仓是否一致"""
    if not STATE_FILE.exists():
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    state_syms = set(state.get('wuqu_positions', []))

    # 获取实际持仓（通过binance-cli）
    try:
        result = subprocess.run(
            ['binance-cli', 'futures-usds', 'account-information-v3'],
            capture_output=True, text=True, timeout=15,
            cwd=str(BASE_DIR)
        )
        if result.returncode != 0:
            print(f'binance-cli 调用失败: {result.stderr[:100]}')
            return

        data = json.loads(result.stdout)
        actual_positions = {
            p['symbol'] for p in data.get('positions', [])
            if float(p.get('positionAmt', 0)) != 0
        }
    except Exception as e:
        print(f'获取实际持仓失败: {e}')
        return

    # 对比
    ghost_positions = state_syms - actual_positions    # state有但实际没有
    missing_positions = actual_positions - state_syms  # 实际有但state没记录

    if ghost_positions:
        push_alert(
            f'🚨 持仓状态不同步！\n'
            f'state记录但实际已平仓: {", ".join(ghost_positions)}\n'
            f'→ 需要手动同步 brahma_state.json'
        )

    if missing_positions:
        push_alert(
            f'⚠️ 发现未记录的实际持仓!\n'
            f'实际持仓但state未记录: {", ".join(missing_positions)}\n'
            f'→ 请确认是否为手动开仓'
        )

    if not ghost_positions and not missing_positions:
        print(f'✅ 持仓同步正常: {actual_positions or "无持仓"}')

    # 检查止损价格（空单上穿止损 / 多单下穿止损）
    for pos in state.get('positions', []):
        sym = pos.get('symbol')
        if sym not in actual_positions:
            continue
        sl = pos.get('stop_loss') or pos.get('sl_price')
        if not sl:
            continue

        # 获取当前价格
        try:
            r = subprocess.run(
                ['binance-cli', 'futures-usds', 'symbol-price-ticker', '--symbol', sym],
                capture_output=True, text=True, timeout=10, cwd=str(BASE_DIR)
            )
            price_data = json.loads(r.stdout)
            mark = float(price_data.get('price', 0))
            direction = pos.get('direction', 'LONG')

            sl_dist_pct = abs(mark - sl) / sl * 100
            if direction == 'SHORT' and mark > sl * 0.98:
                push_alert(
                    f'🚨 {sym} SHORT 接近止损!\n'
                    f'当前:{mark}  止损:{sl}  距离:{sl_dist_pct:.1f}%'
                )
            elif direction == 'LONG' and mark < sl * 1.02:
                push_alert(
                    f'🚨 {sym} LONG 接近止损!\n'
                    f'当前:{mark}  止损:{sl}  距离:{sl_dist_pct:.1f}%'
                )
        except Exception:
            pass


def main():
    print(f'[{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}] 持仓状态监控启动')
    check_state_freshness()
    check_position_sync()
    print('监控完成')


if __name__ == '__main__':
    main()

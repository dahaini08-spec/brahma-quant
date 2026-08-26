"""
drawdown_tracker.py — 梵天回撤保护协议
接入位置：scripts/auto_executor.py（信号执行前门控）
[封印 2026-08-26 苏摩111] Drawdown Protocol顶级量化风控标准

协议规则：
- 回撤 ≥5%NAV  → 降仓50%，暂停新信号3天
- 回撤 ≥10%NAV → 暂停所有自动执行，需苏摩111重启
- 回撤 ≥15%NAV → 系统级暂停，全面复盘后才能重启
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'drawdown_state.json'
NAV_FILE   = BASE / 'data' / 'nav_history.jsonl'

# ══ 协议阈值 ══
THRESHOLD_WARN    = 0.05   # 5%  → 降仓50%+暂停3天
THRESHOLD_HALT    = 0.10   # 10% → 暂停自动执行
THRESHOLD_LOCKOUT = 0.15   # 15% → 系统级锁定

# ══ 状态管理 ══
def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        'peak_nav': None,
        'current_nav': None,
        'drawdown_pct': 0.0,
        'status': 'NORMAL',      # NORMAL / WARN / HALT / LOCKOUT
        'halt_until': None,      # WARN暂停到期时间戳
        'halt_reason': '',
        'last_updated': None,
        'restart_required': False,
        'unlock_code': None,     # 苏摩111解锁码
    }

def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def _get_current_nav() -> float:
    """从binance_cli或持仓状态估算当前NAV"""
    try:
        import sys
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_bus import bus
        balance = bus.get_balance()
        if balance and balance.get('totalWalletBalance'):
            return float(balance['totalWalletBalance'])
    except Exception:
        pass

    # fallback: 读取历史NAV
    if NAV_FILE.exists():
        lines = NAV_FILE.read_text().strip().splitlines()
        if lines:
            last = json.loads(lines[-1])
            return float(last.get('nav', 0))
    return 0.0

def record_nav(nav: float):
    """记录NAV快照"""
    NAV_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': time.time(),
        'ts_iso': datetime.now(timezone.utc).isoformat(),
        'nav': nav,
    }
    with open(NAV_FILE, 'a') as f:
        f.write(json.dumps(record) + '\n')

def update_drawdown(current_nav: float = None) -> dict:
    """
    更新回撤状态，返回当前协议状态。
    每次信号执行前调用。
    """
    state = _load_state()

    if current_nav is None:
        current_nav = _get_current_nav()

    if current_nav <= 0:
        return state  # 无法获取NAV，跳过

    now = time.time()
    state['current_nav'] = current_nav
    state['last_updated'] = now

    # 更新峰值NAV
    if state['peak_nav'] is None or current_nav > state['peak_nav']:
        state['peak_nav'] = current_nav

    # 计算回撤
    peak = state['peak_nav']
    drawdown = (peak - current_nav) / peak if peak > 0 else 0.0
    state['drawdown_pct'] = round(drawdown * 100, 2)

    # WARN暂停到期检查
    if state['status'] == 'WARN' and state.get('halt_until'):
        if now > state['halt_until']:
            state['status'] = 'NORMAL'
            state['halt_until'] = None
            state['halt_reason'] = ''

    # 协议级别判断
    if drawdown >= THRESHOLD_LOCKOUT:
        if state['status'] != 'LOCKOUT':
            state['status'] = 'LOCKOUT'
            state['halt_reason'] = f'回撤{state["drawdown_pct"]}%≥15%，系统级锁定，需苏摩111全面复盘'
            state['restart_required'] = True
            _notify_lockout(state)

    elif drawdown >= THRESHOLD_HALT:
        if state['status'] not in ('LOCKOUT', 'HALT'):
            state['status'] = 'HALT'
            state['halt_reason'] = f'回撤{state["drawdown_pct"]}%≥10%，暂停自动执行，需苏摩111重启'
            state['restart_required'] = True
            _notify_halt(state)

    elif drawdown >= THRESHOLD_WARN:
        if state['status'] == 'NORMAL':
            state['status'] = 'WARN'
            state['halt_until'] = now + 3 * 86400  # 暂停3天
            state['halt_reason'] = f'回撤{state["drawdown_pct"]}%≥5%，降仓50%，暂停新信号3天'
            _notify_warn(state)

    else:
        if state['status'] == 'NORMAL':
            pass  # 正常，不更新
        # WARN/HALT/LOCKOUT状态由上面的到期检查处理

    _save_state(state)
    return state

def check_can_execute(signal: dict = None) -> tuple[bool, str]:
    """
    信号执行前检查，返回 (can_execute, reason)
    在 auto_executor 调用 place_order 前调用
    """
    state = _load_state()
    status = state.get('status', 'NORMAL')
    drawdown = state.get('drawdown_pct', 0)

    if status == 'LOCKOUT':
        return False, f'🚨 LOCKOUT: {state.get("halt_reason","回撤超15%")} | DD={drawdown}%'

    if status == 'HALT':
        return False, f'🛑 HALT: {state.get("halt_reason","回撤超10%")} | DD={drawdown}%'

    if status == 'WARN':
        halt_until = state.get('halt_until', 0)
        remaining = max(0, halt_until - time.time()) / 3600
        return False, f'⚠️ WARN: {state.get("halt_reason","回撤超5%")} | 剩余{remaining:.1f}h | DD={drawdown}%'

    return True, f'NORMAL | DD={drawdown}%'

def get_position_size_multiplier() -> float:
    """
    根据回撤状态返回仓位乘数
    WARN: 0.5（降仓50%）
    其他: 1.0
    """
    state = _load_state()
    status = state.get('status', 'NORMAL')
    if status == 'WARN':
        return 0.5
    return 1.0

def unlock_halt(code: str) -> bool:
    """
    苏摩111解锁：解除HALT/LOCKOUT状态
    code = '111' = 苏摩最高批准
    """
    if code != '111':
        return False
    state = _load_state()
    if state['status'] in ('WARN', 'HALT', 'LOCKOUT'):
        state['status'] = 'NORMAL'
        state['halt_reason'] = ''
        state['restart_required'] = False
        state['peak_nav'] = state.get('current_nav', state.get('peak_nav'))  # 重置峰值
        _save_state(state)
        return True
    return False

def get_status_report() -> str:
    """生成回撤状态报告"""
    state = _load_state()
    dd = state.get('drawdown_pct', 0)
    peak = state.get('peak_nav', 0)
    cur = state.get('current_nav', 0)
    status = state.get('status', 'NORMAL')

    icons = {'NORMAL': '🟢', 'WARN': '🟡', 'HALT': '🔴', 'LOCKOUT': '🚨'}
    icon = icons.get(status, '❓')

    lines = [
        f'{icon} Drawdown Protocol: {status}',
        f'  峰值NAV: ${peak:.2f}  当前: ${cur:.2f}  回撤: {dd:.1f}%',
        f'  阈值: WARN≥5% / HALT≥10% / LOCKOUT≥15%',
    ]
    if state.get('halt_reason'):
        lines.append(f'  原因: {state["halt_reason"]}')
    if state.get('halt_until'):
        remaining = max(0, state['halt_until'] - time.time()) / 3600
        lines.append(f'  恢复: {remaining:.1f}h后自动恢复（WARN级）')
    if state.get('restart_required'):
        lines.append('  🔑 需要苏摩111解锁：unlock_halt("111")')

    return '\n'.join(lines)

# ══ 通知函数 ══
def _notify_warn(state: dict):
    try:
        import subprocess
        msg = f'⚠️ 梵天Drawdown WARN\n回撤{state["drawdown_pct"]}%≥5%\n降仓50%，暂停新信号3天\n峰值NAV=${state["peak_nav"]:.2f}'
        subprocess.Popen(['openclaw', 'message', '--channel', 'jarvis', '--to',
                         '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
                         '--message', msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _notify_halt(state: dict):
    try:
        import subprocess
        msg = f'🛑 梵天Drawdown HALT\n回撤{state["drawdown_pct"]}%≥10%\n暂停所有自动执行\n需苏摩111重启\n峰值NAV=${state["peak_nav"]:.2f}'
        subprocess.Popen(['openclaw', 'message', '--channel', 'jarvis', '--to',
                         '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
                         '--message', msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _notify_lockout(state: dict):
    try:
        import subprocess
        msg = f'🚨 梵天Drawdown LOCKOUT\n回撤{state["drawdown_pct"]}%≥15%\n系统级锁定\n需苏摩111全面复盘后重启\n峰值NAV=${state["peak_nav"]:.2f}'
        subprocess.Popen(['openclaw', 'message', '--channel', 'jarvis', '--to',
                         '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
                         '--message', msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'unlock':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        if unlock_halt(code):
            print('✅ Drawdown Protocol 已解锁')
        else:
            print('❌ 解锁失败（需要111）')
    elif len(sys.argv) > 1 and sys.argv[1] == 'status':
        print(get_status_report())
    else:
        state = update_drawdown()
        print(get_status_report())

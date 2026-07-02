import os
#!/usr/bin/env python3
"""
bear_recovery_unlock_monitor.py · BEAR_RECOVERY 解封监控 v1.0
设计院 · 2026-06-24

任务：纯脚本监控 BTC/ETH BEAR_RECOVERY 解封条件
  - BTC：4H方向封锁解除 + valid=True
  - ETH：LSR多头占比 < 65% + score ≥ 130 + valid=True

触发时推送到当前对话 thread，不满足完全静默（exit 0）

苏摩宪法：纯脚本 → crond直接执行，禁止agentTurn包装
"""

import sys, os, json, subprocess, time, urllib.request, urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'bear_recovery_unlock_state.json')

PUSH_TARGET  = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
PUSH_CHANNEL = 'jarvis'

TARGETS = [
    {'symbol': 'BTCUSDT', 'dir': 'LONG'},
    {'symbol': 'ETHUSDT', 'dir': 'LONG'},
]

# ── 状态管理（去重推送 2h TTL）─────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def already_pushed(state, sym, cooldown=7200):
    ts = state.get(sym, {}).get('pushed_at', 0)
    return (time.time() - ts) < cooldown

def mark_pushed(state, sym):
    state[sym] = {'pushed_at': time.time()}

# ── 推送 ──────────────────────────────────────────────────
def push(msg):
    subprocess.run([
        'openclaw', 'message', 'send',
        '--channel', PUSH_CHANNEL,
        '--to', PUSH_TARGET,
        '--message', msg,
    ], capture_output=True, timeout=15)

# ── LSR 实时查询 ──────────────────────────────────────────
def get_lsr(symbol):
    try:
        url = f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1'
        data = json.loads(urllib.request.urlopen(url, timeout=6).read())
        return float(data[0]['longAccount']) * 100
    except Exception:
        return None

# ── 梵天信号查询（brahma_execute dry_run）────────────────
def get_brahma(symbol, direction):
    try:
        r = subprocess.run(
            ['python3', os.path.join(BASE_DIR, 'scripts', 'brahma_execute.py'), symbol, direction],
            capture_output=True, text=True, timeout=60, cwd=BASE_DIR
        )
        out = r.stdout + r.stderr

        import re
        result = {'symbol': symbol, 'direction': direction, 'raw': out}

        # valid
        m = re.search(r'valid=(\w+)', out)
        result['valid'] = m.group(1) == 'True' if m else False

        # score
        m = re.search(r'score=(-?\d+(?:\.\d+)?)\s+rr1', out)
        result['score'] = float(m.group(1)) if m else 0

        # 体制
        m = re.search(r'体制=(\w+)', out)
        result['regime'] = m.group(1) if m else ''

        # 封锁原因
        m = re.search(r'🛡️ 封锁 \S+ \w+: (.+)', out)
        result['block_reason'] = m.group(1).strip() if m else ''

        # 入场区
        m = re.search(r'入场区:\s*\$?([\d,.]+)\s*~\s*\$?([\d,.]+)', out)
        if m:
            result['entry_lo'] = m.group(1)
            result['entry_hi'] = m.group(2)

        # 止损/TP
        m = re.search(r'止损:\s*\$?([\d,.]+)', out)
        result['sl'] = m.group(1) if m else ''
        m = re.search(r'TP1:\s*\$?([\d,.]+)', out)
        result['tp1'] = m.group(1) if m else ''
        m = re.search(r'TP2:\s*\$?([\d,.]+)', out)
        result['tp2'] = m.group(1) if m else ''

        # RR
        m = re.search(r'rr1=([\d.]+)', out)
        result['rr1'] = float(m.group(1)) if m else 0

        return result
    except Exception as e:
        return {'symbol': symbol, 'direction': direction, 'valid': False, 'error': str(e)}

# ── 主逻辑 ────────────────────────────────────────────────
def main():
    state = load_state()
    alerts = []

    for t in TARGETS:
        sym = t['symbol']
        direction = t['dir']

        sig = get_brahma(sym, direction)
        regime = sig.get('regime', '')

        # 只关注 BEAR_RECOVERY 体制
        if 'BEAR_RECOVERY' not in regime:
            continue

        valid   = sig.get('valid', False)
        score   = sig.get('score', 0)
        block   = sig.get('block_reason', '')
        lsr     = get_lsr(sym)

        # ── BTC 解封条件 ──────────────────────────────────
        if sym == 'BTCUSDT':
            unlocked = valid and '4H方向' not in block
            if unlocked and not already_pushed(state, sym):
                msg = (
                    f'🟢 BTC BEAR_RECOVERY 解封！\n'
                    f'体制: BEAR_RECOVERY | valid=True\n'
                    f'score={score:.0f} | 4H方向封锁已解除\n'
                    f'入场区: ${sig.get("entry_lo","")} ~ ${sig.get("entry_hi","")}\n'
                    f'止损: ${sig.get("sl","")}  TP1: ${sig.get("tp1","")}  TP2: ${sig.get("tp2","")}\n'
                    f'RR={sig.get("rr1",0):.2f} | LSR多头={lsr:.1f}%'
                )
                alerts.append((sym, msg))

        # ── ETH 解封条件 ──────────────────────────────────
        elif sym == 'ETHUSDT':
            lsr_ok   = lsr is not None and lsr < 65.0
            score_ok = score >= 130
            unlocked = valid and lsr_ok and score_ok

            # 即使未完全解封，LSR已改善也发预警（每2h一次）
            if lsr is not None and lsr < 65.0 and not valid and not already_pushed(state, f'{sym}_lsr'):
                warn = (
                    f'⚠️ ETH LSR改善预警\n'
                    f'多头占比已降至 {lsr:.1f}%（目标<65%）\n'
                    f'当前score={score:.0f}（需≥130）valid={valid}\n'
                    f'体制: BEAR_RECOVERY | 继续监控中'
                )
                push(warn)
                mark_pushed(state, f'{sym}_lsr')
                save_state(state)

            if unlocked and not already_pushed(state, sym):
                msg = (
                    f'🟢 ETH BEAR_RECOVERY 解封！\n'
                    f'体制: BEAR_RECOVERY | valid=True\n'
                    f'score={score:.0f}≥130 | LSR多头={lsr:.1f}%<65%\n'
                    f'入场区: ${sig.get("entry_lo","")} ~ ${sig.get("entry_hi","")}\n'
                    f'止损: ${sig.get("sl","")}  TP1: ${sig.get("tp1","")}  TP2: ${sig.get("tp2","")}\n'
                    f'RR={sig.get("rr1",0):.2f}'
                )
                alerts.append((sym, msg))

    # 推送解封信号
    for sym, msg in alerts:
        push(msg)
        mark_pushed(state, sym)

    save_state(state)

    if not alerts:
        print('SILENT_OK')  # 无触发，静默退出

if __name__ == '__main__':
    main()

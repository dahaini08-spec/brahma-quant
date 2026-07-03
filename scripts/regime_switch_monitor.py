import os
#!/usr/bin/env python3
"""
regime_switch_monitor.py · 梵天体制切换监控 v3
[苏摩审查 v3 · 2026-06-18]

设计原则（苏摩三原则）：
  1. 关键触发：体制真正切换才推送，无变化完全静默（exit 0）
  2. 纯脚本：系统crond直接执行，零AI负担，禁止agentTurn包装
  3. 推送方式：openclaw message send --channel jarvis（直接CLI）

执行路径：crond → 本脚本 → [有切换] → openclaw message send
NO_AI_AGENT，NO_HEARTBEAT_OK，NO_LLM_CALL

【苏摩裁决 2026-06-18】
- WATCH_SYMS 锁定为无黑名单品种（v9系统黑名单：XRPUSDT/SOLUSDT/ADAUSDT排除）
- 任何修改 WATCH_SYMS 须经苏摩审批，不得加入系统黑名单品种
"""

import sys, os, json, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brahma_brain'))

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'regime_switch_state.json')
LOG_FILE   = os.path.join(BASE_DIR, 'data', 'regime_log.json')

# 推送目标（Jarvis 用户ID）
PUSH_TARGET = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
PUSH_CHANNEL = 'jarvis'

# ── 监控标的 ──────────────────────────────────────────────
# 苏摩锁定名单：只含系统黑名单外的主流标的
# 禁止加入：XRPUSDT(监管风险/黑名单) SOLUSDT(黑名单) ADAUSDT(黑名单)
WATCH_SYMS = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT', 'NEARUSDT',
]
# 系统黑名单（双重防护，任何来源写入时过滤）
_BLACKLIST = {'XRPUSDT', 'SOLUSDT', 'ADAUSDT', 'CHZUSDT'}

# ── 体制分类 ──────────────────────────────────────────────
HIGH_VALUE = {
    'BULL_TREND':      ('🚀 牛市趋势',   'LONG',  'WR=70.3% n=3046铁证 | S+级'),
    'BULL_EARLY':      ('🌱 牛市初期',   'LONG',  'WR=64.4% n=5396铁证 | S级'),
    'BEAR_TREND':      ('🔻 熊市趋势',   'SHORT', 'WR=71.8% n=2413铁证 | S+级'),
    'BEAR_EARLY':      ('⚡ 熊市初期',   'SHORT', 'WR=66.5% n=5896铁证 | S级'),
    'BULL_CORRECTION': ('📉 牛市回调',   'BOTH',  'WR=73.9%(SHORT) | A级'),
    'BEAR_RECOVERY':   ('🔄 熊市反弹',   'LONG',  'WR=72.5% 反直觉alpha'),
}
CHOP = {'CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW'}

# ── 工具函数 ──────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def append_log(entry):
    try:
        log = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE) as f:
                log = json.load(f)
        log.append(entry)
        with open(LOG_FILE, 'w') as f:
            json.dump(log[-500:], f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def get_regime(sym: str):
    """获取标的当前体制和阶段（纯本地计算）"""
    try:
        from market_state import analyze as ms
        r = ms(sym)
        return r.get('regime', 'UNKNOWN'), r.get('phase', '?')
    except Exception as e:
        return None, None

def push(msg: str):
    """通过 openclaw CLI 直接推送到 Jarvis（无AI）"""
    try:
        result = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target',  PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False

# ── 主逻辑 ────────────────────────────────────────────────
def main():
    prev = load_state()
    alerts = []
    now   = int(time.time())

    for sym in WATCH_SYMS:
        # 苏摩双重防护：黑名单品种跳过，无论来源
        if sym in _BLACKLIST:
            continue
        regime, phase = get_regime(sym)
        if not regime:
            continue

        old = prev.get(sym, {}).get('regime', 'UNKNOWN')
        prev[sym] = {'regime': regime, 'phase': phase,
                     'ts': now, 'prev': old}

        # 无变化 → 静默
        if old == regime or old == 'UNKNOWN':
            continue

        # ── Case 1: CHOP → 高价值（核心预警）────────────
        if old in CHOP and regime in HIGH_VALUE:
            label, direction, wr_hint = HIGH_VALUE[regime]
            alerts.append(
f"""🔔 梵天体制切换预警

{label}
{sym}: {old} → {regime}
主方向: {direction}
铁证: {wr_hint}
Phase: {phase}

⚡ 系统自动优先扫描 {sym} {direction} 方向
下轮 brahma-scan 15~30min 内触发""")
            append_log({'ts': now, 'sym': sym,
                        'from': old, 'to': regime, 'type': 'UPGRADE'})

        # ── Case 2: 高价值 → CHOP（降级预警）────────────
        elif old in HIGH_VALUE and regime in CHOP:
            alerts.append(
f"""⚠️ 梵天体制降级

{sym}: {old} → {regime}
系统进入震荡待机，停止开单
等待体制回归高价值区间""")
            append_log({'ts': now, 'sym': sym,
                        'from': old, 'to': regime, 'type': 'DOWNGRADE'})

        # ── Case 3: 高价值间切换（方向调整）─────────────
        elif old in HIGH_VALUE and regime in HIGH_VALUE:
            new_label, new_dir, new_wr = HIGH_VALUE[regime]
            alerts.append(
f"""🔄 梵天体制升级

{sym}: {old} → {regime} {new_label}
方向调整: {new_dir} | {new_wr}""")
            append_log({'ts': now, 'sym': sym,
                        'from': old, 'to': regime, 'type': 'SHIFT'})

    save_state(prev)

    # ── [P0修复 2026-07-03] 体制切换钉子：自动减仓/平仓 ──
    if alerts:
        for sym_alert in alerts:
            # 从告警中提取新体制
            import re as _re
            m = _re.search(r'(BULL_TREND|BEAR_TREND|BEAR_RECOVERY|CHOP_MID|CHOP_HIGH|BEAR_EARLY|BULL_EARLY|BULL_CORRECTION)', sym_alert)
            if m:
                new_regime = m.group(1)
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(BASE))
                    from scripts.regime_position_hook import apply_regime_hook
                    hook_actions = apply_regime_hook(new_regime, dry_run=False)
                    if hook_actions:
                        hook_report = '\n'.join(hook_actions)
                        push(f'🔧 体制钉子执行 ({new_regime}):\n{hook_report}')
                        print(f'[RegimeMonitor] 体制钉子: {len(hook_actions)}项操作')
                except Exception as _he:
                    print(f'[RegimeMonitor] 体制钉子失败: {_he}')
                break  # 只取第一个体制切换处理

    # 有预警才推送
    if alerts:
        full_msg = '\n\n─────────────────────\n\n'.join(alerts)
        push(full_msg)
        print(f"[RegimeMonitor] ✅ 推送 {len(alerts)} 条预警")
    else:
        # 纯静默，不输出，crond 日志也干净
        pass

if __name__ == '__main__':
    main()

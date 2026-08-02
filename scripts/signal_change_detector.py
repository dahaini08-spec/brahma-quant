#!/usr/bin/env python3
"""
signal_change_detector.py — 梵天信号变化检测器
设计院六方联合封印 2026-07-16 苏摩111批准

核心哲学：推送 = 有变化 + 达到阈值。无变化 = 静默。

变化定义（四维）：
  1. score 变化 ≥ 5分（5分桶消除噪音）
  2. action 变化：WATCH / ENTER_WATCH / ENTER / ENTER_FULL
  3. 方向变化：LONG ↔ SHORT
  4. 体制变化：BULL_TREND / BEAR_TREND / CHOP / BEAR_RECOVERY

状态持久化：data/signal_push_state.json
  { sym: { hash, score, action, regime, direction, ts, push_count } }
"""
import sys, os, json, time, hashlib
from pathlib import Path

BASE  = Path(__file__).parent.parent
BRAIN = BASE / 'brahma_brain'
DATA  = BASE / 'data'
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BASE))

STATE_FILE    = DATA / 'signal_push_state.json'
WATCHER_STATE = DATA / 'rsi_watcher_state.json'
SCORE_BUCKET  = 5        # 5分桶，消除噪音波动
MIN_SCORE     = 138      # 低于此分不推送
SAME_HASH_TTL = 86400    # 同哈希24H内不重复推送

SYMS = ['BTCUSDT', 'ETHUSDT']

# ── 系统配置 ──────────────────────────────────────────────────
try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
except Exception:
    JARVIS_USER_ID  = '73295708'
    JARVIS_THREAD_ID = '019fb612-d570-7f0b-89c5-2065284157e0'

JARVIS_TARGET = f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}'  # [2026-07-19] thread:→t: 修复推送路由


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def make_hash(score: float, action: str, regime: str, direction: str) -> str:
    """状态哈希：score5分桶 + action + regime大类 + direction"""
    score_bucket = int(score // SCORE_BUCKET) * SCORE_BUCKET
    # regime大类化（消除子标签差异）
    regime_major = (
        'BULL' if 'BULL' in regime.upper() else
        'BEAR_REC' if 'RECOVERY' in regime.upper() else
        'BEAR' if 'BEAR' in regime.upper() else
        'CHOP'
    )
    raw = f'{score_bucket}|{action}|{regime_major}|{direction}'
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def check_rsi_trigger() -> bool:
    """检查10min内是否有RSI守望触发事件"""
    try:
        ws = json.loads(WATCHER_STATE.read_text())
        now = time.time()
        for sym in SYMS:
            last = ws.get(f'{sym}_last_trigger', 0)
            if now - last < 600:  # 10min内
                return True
        return False
    except Exception:
        return True  # 读取失败时保守触发


def run_analysis_safe(sym: str) -> dict:
    """安全执行分析，异常返回空"""
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis
        return run_analysis(sym) or {}
    except Exception as e:
        return {'_error': str(e)}


def describe_change(old: dict, new_score: float, new_action: str,
                    new_regime: str, new_dir: str) -> list:
    """描述发生了什么变化（用于推送内容）"""
    changes = []
    if not old:
        changes.append('首次信号')
        return changes

    old_score  = old.get('score', 0)
    old_action = old.get('action', '')
    old_regime = old.get('regime', '')
    old_dir    = old.get('direction', '')

    score_diff = new_score - old_score
    if abs(score_diff) >= SCORE_BUCKET:
        arrow = '📈' if score_diff > 0 else '📉'
        changes.append(f'{arrow} 评分 {old_score:.0f}→{new_score:.0f} ({score_diff:+.0f})')

    # action级别映射
    action_level = {'SKIP': 0, 'WATCH': 1, 'ENTER_WATCH': 2, 'ENTER': 3, 'ENTER_FULL': 4}
    old_lv = action_level.get(old_action, 0)
    new_lv = action_level.get(new_action, 0)
    if old_lv != new_lv:
        arrow = '⬆️' if new_lv > old_lv else '⬇️'
        changes.append(f'{arrow} 信号强度 {old_action}→{new_action}')

    if old_dir and new_dir and old_dir != new_dir:
        changes.append(f'🔄 方向翻转 {old_dir}→{new_dir}')

    if old_regime and new_regime and old_regime.split('_')[0] != new_regime.split('_')[0]:
        changes.append(f'🌐 体制切换 {old_regime}→{new_regime}')

    return changes


def push_jarvis(msg: str):
    """推送到Jarvis"""
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--to', JARVIS_TARGET,
         '--channel', 'jarvis',
         '--message', msg],
        capture_output=True, text=True, timeout=15
    )


def main():
    now = time.time()

    # 1. 检查RSI触发事件
    if not check_rsi_trigger():
        print('[signal_change_detector] 无RSI触发事件，静默')
        return

    # 2. 加载上次推送状态
    state = load_state()

    pushed_any = False

    for sym in SYMS:
        # 3. 执行分析
        result = run_analysis_safe(sym)
        if result.get('_error'):
            print(f'[signal_change_detector] {sym} 分析失败: {result["_error"][:60]}')
            continue

        score     = float(result.get('score_final', result.get('score', 0)) or 0)
        action    = str(result.get('action', 'SKIP') or 'SKIP')
        regime    = str(result.get('regime', '?') or '?')
        direction = str(result.get('direction', result.get('signal_dir', '?')) or '?')
        valid     = bool(result.get('valid_signal', False))

        # 4. 计算状态哈希
        new_hash = make_hash(score, action, regime, direction)
        sym_state = state.get(sym, {})
        old_hash  = sym_state.get('hash', '')
        last_push = sym_state.get('ts', 0)

        # 5. 判断是否需要推送
        hash_changed = (new_hash != old_hash)
        cooldown_ok  = (now - last_push > SAME_HASH_TTL) if old_hash == new_hash else True
        score_ok     = (score >= MIN_SCORE)

        # 变化描述
        changes = describe_change(sym_state, score, action, regime, direction)

        # 推送条件：有变化 + score达标（或action升级）
        should_push = hash_changed and score_ok and valid and len(changes) > 0

        # action降级（score不达标但action从高变低）也要推送警告
        action_level = {'SKIP': 0, 'WATCH': 1, 'ENTER_WATCH': 2, 'ENTER': 3, 'ENTER_FULL': 4}
        old_lv = action_level.get(sym_state.get('action',''), 0)
        new_lv = action_level.get(action, 0)
        is_downgrade = hash_changed and new_lv < old_lv and old_lv >= 2

        print(f'[{sym}] score={score:.0f} action={action} valid={valid} '
              f'hash={new_hash} changed={hash_changed} push={should_push or is_downgrade}')

        if not (should_push or is_downgrade):
            # 更新状态但不推送
            state[sym] = {
                'hash': new_hash, 'score': score, 'action': action,
                'regime': regime, 'direction': direction, 'ts': last_push,
                'push_count': sym_state.get('push_count', 0)
            }
            continue

        # 6. 构建推送消息
        change_str = ' | '.join(changes) if changes else '状态更新'
        emoji = '🚨' if score >= 165 else ('🔥' if score >= 155 else ('⚠️' if score >= 138 else '📊'))

        if is_downgrade and not should_push:
            msg = (
                f'⬇️ {sym} 信号降级提醒\n'
                f'体制: {regime} | 方向: {direction}\n'
                f'评分: {score:.0f} | 信号强度: {action}\n'
                f'变化: {change_str}'
            )
        else:
            # 完整信号卡片
            entry_lo  = float(result.get('entry_lo', 0) or 0)
            entry_hi  = float(result.get('entry_hi', entry_lo) or entry_lo)
            stop_loss = float(result.get('stop_loss', 0) or 0)
            tp1       = float(result.get('tp1', 0) or 0)

            msg = (
                f'{emoji} 梵天信号卡片 · 结构变化推送\n'
                f'📅 {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}\n\n'
                f'【{sym}】{regime} {direction}\n'
                f'评分: {score:.0f} | 信号: {action}\n'
                f'变化: {change_str}\n'
            )
            if entry_lo > 0:
                msg += f'入场: {entry_lo:.4g}~{entry_hi:.4g}\n'
            if stop_loss > 0:
                msg += f'止损: {stop_loss:.4g}\n'
            if tp1 > 0:
                msg += f'目标: {tp1:.4g}\n'
            msg += f'\n[BRAHMA·变化驱动推送]'

        push_jarvis(msg)
        pushed_any = True
        print(f'[signal_change_detector] 已推送 {sym}: {change_str}')

        # 7. 更新状态
        state[sym] = {
            'hash': new_hash, 'score': score, 'action': action,
            'regime': regime, 'direction': direction,
            'ts': now, 'push_count': sym_state.get('push_count', 0) + 1,
            'last_change': change_str
        }

    save_state(state)

    if not pushed_any:
        print('[signal_change_detector] 无结构变化，静默')


if __name__ == '__main__':
    main()

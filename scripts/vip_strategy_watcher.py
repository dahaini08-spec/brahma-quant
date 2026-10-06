#!/usr/bin/env python3
"""
vip_strategy_watcher.py — VIP策略即时推送守望者
设计院封印 2026-10-06 苏摩111

解决问题：CHOP_MID体制下ENTER永远不触发 → VIP策略更新苏摩永远不知道

机制：
  1. 读 brahma_state_{sym}.json 提取策略核心参数
  2. 与上次推送状态 data/vip_push_state.json 对比
  3. 有显著变化（entry/sl/tp/direction任一超阈值）→ P1推送
  4. 超过4h未推送 → 兜底心跳推送（防止苏摩长期零消息）

触发阈值（滤噪）：
  - entry_lo/hi 变化 > 0.5%
  - sl 变化 > 0.5%
  - direction 切换（SHORT↔LONG）
  - tp1 变化 > 1%
  - 4h心跳兜底

接入位置：
  A. supercronic brahma_crontab.txt  */30 * * * *（独立巡检）
  B. brahma_manual_analysis.py 分析完成后 inline调用（实时感知）
"""
import json, sys, time
from pathlib import Path

try:
    import brahma_path_setup  # noqa
except ImportError:
    pass

BASE  = Path(__file__).parent.parent
DATA  = BASE / 'data'
SYMS  = ['BTC', 'ETH']

STATE_FILE = DATA / 'vip_push_state.json'

# 变化阈值
ENTRY_CHANGE_PCT  = 0.005   # 0.5%
SL_CHANGE_PCT     = 0.005   # 0.5%
TP_CHANGE_PCT     = 0.010   # 1.0%
HEARTBEAT_TTL     = 4 * 3600  # 4h兜底心跳


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except Exception as e:
        print(f'[vip_watcher] 状态保存失败: {e}', file=sys.stderr)


def load_brahma_state(sym: str) -> dict:
    """读取 brahma_state_{sym}.json"""
    try:
        p = DATA / f'brahma_state_{sym.lower()}.json'
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def extract_vip_params(sym: str, st: dict) -> dict:
    """
    从 brahma_state 提取VIP策略核心参数。
    优先读 trader_brain / confluence 结构化字段，
    兜底读 key_levels。
    """
    if not st:
        return {}

    price   = float(st.get('price', 0) or 0)
    regime  = str(st.get('regime', '') or '')
    signal_dir = str(st.get('signal_dir', '') or st.get('direction', '') or '')

    # trader_brain 最完整
    tb = st.get('trader_brain', {}) or {}
    conf = st.get('confluence', {}) or {}
    kl = st.get('key_levels', {}) or {}
    smc = st.get('smc', {}) or {}

    entry_lo = float(tb.get('entry_lo') or conf.get('entry_lo') or st.get('entry_lo') or 0)
    entry_hi = float(tb.get('entry_hi') or conf.get('entry_hi') or st.get('entry_hi') or 0)
    sl       = float(tb.get('sl') or conf.get('sl') or st.get('sl') or 0)
    tp1      = float(tb.get('tp1') or conf.get('tp1') or st.get('tp1') or 0)
    tp2      = float(tb.get('tp2') or conf.get('tp2') or 0)
    rr       = float(tb.get('rr') or conf.get('rr') or st.get('rr') or 0)
    action   = str(conf.get('action') or st.get('decision_action') or 'WATCH')
    score    = float(conf.get('total') or conf.get('score') or st.get('score_final') or 0)

    # 清算地图补充
    liq_short = float(kl.get('liq_short_5pct') or st.get('liq_short') or 0)
    liq_long  = float(kl.get('liq_long_5pct')  or st.get('liq_long')  or 0)

    return {
        'sym':       sym,
        'price':     price,
        'regime':    regime,
        'direction': signal_dir,
        'action':    action,
        'score':     score,
        'entry_lo':  entry_lo,
        'entry_hi':  entry_hi,
        'sl':        sl,
        'tp1':       tp1,
        'tp2':       tp2,
        'rr':        rr,
        'liq_short': liq_short,
        'liq_long':  liq_long,
        'ts':        float(st.get('price_ts') or st.get('_snapshot_written_epoch') or time.time()),
    }


def pct_change(old: float, new: float) -> float:
    if old == 0 or new == 0:
        return 0.0
    return abs(new - old) / abs(old)


def detect_changes(old: dict, new: dict) -> list:
    """检测VIP参数显著变化，返回变化描述列表"""
    changes = []

    if not old:
        changes.append('首次策略感知')
        return changes

    # 方向切换（最重要）
    if old.get('direction', '') and new.get('direction', '') and \
       old['direction'] != new['direction'] and \
       new['direction'] not in ('', 'NEUTRAL', '?'):
        changes.append(f"⚡ 方向切换 {old['direction']} → {new['direction']}")

    # 体制切换
    if old.get('regime', '') and new.get('regime', '') and \
       old['regime'] != new['regime']:
        changes.append(f"🔄 体制切换 {old['regime']} → {new['regime']}")

    # action级别变化
    action_level = {'SKIP': 0, 'WATCH': 1, 'ENTER_WATCH': 2,
                    'ENTER': 3, 'AMBUSCADE': 3, 'EXECUTE': 4, 'ENTER_FULL': 4}
    old_lv = action_level.get(old.get('action', ''), 0)
    new_lv = action_level.get(new.get('action', ''), 0)
    if new_lv > old_lv:
        changes.append(f"🚀 信号升级 {old.get('action','?')} → {new.get('action','?')}")
    elif new_lv < old_lv and old_lv >= 3:
        changes.append(f"⬇️ 信号降级 {old.get('action','?')} → {new.get('action','?')}")

    # 入场区变化
    if new.get('entry_lo', 0) > 0 and old.get('entry_lo', 0) > 0:
        if pct_change(old['entry_lo'], new['entry_lo']) > ENTRY_CHANGE_PCT:
            changes.append(
                f"📍 入场区更新 ${old['entry_lo']:,.1f}~${old.get('entry_hi',0):,.1f}"
                f" → ${new['entry_lo']:,.1f}~${new.get('entry_hi',0):,.1f}"
            )
    elif new.get('entry_lo', 0) > 0 and old.get('entry_lo', 0) == 0:
        changes.append(f"📍 入场区新出现 ${new['entry_lo']:,.1f}~${new.get('entry_hi',0):,.1f}")

    # SL变化
    if new.get('sl', 0) > 0 and old.get('sl', 0) > 0:
        if pct_change(old['sl'], new['sl']) > SL_CHANGE_PCT:
            changes.append(f"🛑 止损更新 ${old['sl']:,.1f} → ${new['sl']:,.1f}")

    # TP1变化
    if new.get('tp1', 0) > 0 and old.get('tp1', 0) > 0:
        if pct_change(old['tp1'], new['tp1']) > TP_CHANGE_PCT:
            changes.append(f"🎯 目标更新 TP1 ${old['tp1']:,.1f} → ${new['tp1']:,.1f}")

    # 分数显著变化（±10）
    score_diff = new.get('score', 0) - old.get('score', 0)
    if abs(score_diff) >= 10:
        arrow = '📈' if score_diff > 0 else '📉'
        changes.append(f"{arrow} 评分 {old.get('score',0):.0f}→{new.get('score',0):.0f} ({score_diff:+.0f})")

    return changes


def build_push_msg(sym: str, params: dict, changes: list, trigger: str) -> str:
    """构建推送消息"""
    p = params
    price_str  = f"${p['price']:,.1f}" if p.get('price') else '—'
    entry_str  = (f"${p['entry_lo']:,.1f}~${p['entry_hi']:,.1f}"
                  if p.get('entry_lo', 0) > 0 else '等待结构')
    sl_str     = f"${p['sl']:,.1f}" if p.get('sl', 0) > 0 else '—'
    tp1_str    = f"${p['tp1']:,.1f}" if p.get('tp1', 0) > 0 else '—'
    rr_str     = f"{p['rr']:.1f}" if p.get('rr', 0) > 0 else '—'
    dir_emoji  = '🔴' if p.get('direction') == 'SHORT' else '🟢' if p.get('direction') == 'LONG' else '⚪'
    change_str = '\n'.join(f"  {c}" for c in changes)

    lines = [
        f"🎯 梵天VIP策略更新 | {sym}/USDT {price_str}",
        f"体制: {p.get('regime','?')} | 评分: {p.get('score',0):.0f} | 动作: {p.get('action','?')}",
        f"",
        f"变化:",
        change_str,
        f"",
        f"当前策略:",
        f"  {dir_emoji} {p.get('direction','?')} 方向 | 入场区: {entry_str}",
        f"  止损: {sl_str} | 目标: {tp1_str} | RR: {rr_str}",
    ]

    if p.get('liq_short', 0) > 0:
        lines.append(f"  清算: 上墙${p['liq_short']:,.1f} / 下池${p.get('liq_long',0):,.1f}")

    lines += [
        f"",
        f"触发: {trigger}",
        f"[vip_strategy_watcher {time.strftime('%H:%M UTC')}]",
    ]

    return '\n'.join(lines)


def push(msg: str):
    """推送到Jarvis主线程"""
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import push_jarvis
        push_jarvis(msg, priority='P1')
        print(f'[vip_watcher] ✅ 已推送 ({len(msg)}字符)')
    except Exception as e:
        print(f'[vip_watcher] ❌ 推送失败: {e}', file=sys.stderr)


def run_sym(sym: str, state: dict, now: float) -> dict:
    """处理单个标的，返回更新后的该标的状态"""
    st = load_brahma_state(sym)
    if not st:
        print(f'[vip_watcher] {sym} brahma_state不存在，跳过')
        return state.get(sym, {})

    params  = extract_vip_params(sym, st)
    old_sym = state.get(sym, {})
    last_push_ts = old_sym.get('last_push_ts', 0)
    time_since   = now - last_push_ts

    # 检测变化
    changes = detect_changes(old_sym.get('params', {}), params)

    # 推送决策
    should_push = False
    trigger     = ''

    if changes:
        should_push = True
        trigger = '策略参数变化: ' + ' | '.join(changes[:3])

    elif time_since > HEARTBEAT_TTL:
        # 4h心跳：即使无变化也推一次，防苏摩长期零消息
        should_push = True
        trigger = f'4H心跳兜底 (上次推送{time_since/3600:.1f}h前)'
        changes = [f'定期确认 | 策略维持 {params.get("direction","?")} 方向']

    if should_push:
        msg = build_push_msg(sym, params, changes, trigger)
        push(msg)
        return {
            'params':       params,
            'last_push_ts': now,
            'last_trigger': trigger,
            'push_count':   old_sym.get('push_count', 0) + 1,
        }
    else:
        print(f'[vip_watcher] {sym} 无显著变化 (上次推:{time_since/60:.0f}min前)，静默')
        return old_sym


def main():
    now   = time.time()
    state = load_state()
    updated = False

    for sym in SYMS:
        new_sym_state = run_sym(sym, state, now)
        if new_sym_state.get('last_push_ts', 0) > state.get(sym, {}).get('last_push_ts', 0):
            state[sym] = new_sym_state
            updated = True
        elif sym not in state:
            state[sym] = new_sym_state

    if updated:
        save_state(state)


if __name__ == '__main__':
    main()

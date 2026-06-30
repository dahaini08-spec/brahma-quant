#!/usr/bin/env python3
"""
account_circuit_breaker.py — 账户级风控熔断 M12 v1.0
设计院 2026-05-29
# ── signal_utils 标准读取（2026-06-02）────────────────
def _load_clean_signals(hours=8, min_score=0, valid_only=False, unsettled_only=False):
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.dirname(__file__))
    try:
        from signal_utils import load_signals as _su
        return _su(hours=hours, min_score=min_score, valid_only=valid_only, unsettled_only=unsettled_only)
    except Exception:
        return []
# ────────────────────────────────────────────────────


三级熔断：
  L1 日亏损熔断：当日亏损 > 账户5% → 暂停所有新信号24H
  L2 连亏熔断：  连续3笔SL → 冷静期6H
  L3 回撤熔断：  从历史最高NAV回撤 > 20% → 只允许平仓

状态文件：/tmp/circuit_breaker.json
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
CB_FILE = Path('/tmp/circuit_breaker.json')
NAV_PEAK_FILE = BASE / 'data/nav_peak.json'
LOG_PATH = BASE / 'data/live_signal_log.jsonl'

# 熔断阈值
DAILY_LOSS_PCT   = 5.0   # 日亏5%触发L1
CONSEC_SL_COUNT  = 3     # 连续3笔SL触发L2
MAX_DRAWDOWN_PCT = 20.0  # 从峰值回撤20%触发L3

# 熔断冷静期（秒）
L1_COOLDOWN = 86400   # 24H
L2_COOLDOWN = 21600   # 6H
L3_COOLDOWN = 0       # L3无期限，需人工解除


def _load_cb() -> dict:
    try:
        if CB_FILE.exists():
            return json.loads(CB_FILE.read_text())
    except: pass
    return {'l1': None, 'l2': None, 'l3': False, 'nav_peak': 0}


def _save_cb(cb: dict):
    CB_FILE.write_text(json.dumps(cb, ensure_ascii=False, indent=2))


def _load_signals() -> list:
    logs = []
    if LOG_PATH.exists():
        with open(LOG_PATH) as f:
            for l in f:
                try: logs.append(json.loads(l.strip()))
                except: pass
    return logs


def _get_nav() -> float:
    try:
        bs = json.loads((BASE / 'data/brahma_state.json').read_text())
        return float(bs.get('nav', 0) or 0)
    except: return 0.0


def _notify(msg: str):
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        _jarvis(f'🚨 [熔断器] {msg}')
    except: pass


# ─── 检查是否需要触发熔断 ─────────────────────────────────────────

def check_and_update() -> dict:
    # ── 指令总线：人工覆盖时立即退出 ─────────────────────────
    try:
        import sys as _sys_cr; _sys_cr.path.insert(0, str(Path(__file__).parent))
        from command_register import check_override, DOMAIN_CIRCUIT_BREAKER
        _silenced, _reason = check_override(DOMAIN_CIRCUIT_BREAKER)
        if _silenced:
            return {'l1': False, 'l2': False, 'l3': False, 'blocked': False,
                    'reason': f'人工覆盖: {_reason}', 'overridden': True}
    except Exception: pass
    # ────────────────────────────────────────────────────────
    cb  = _load_cb()
    now = time.time()
    logs = _load_signals()
    nav  = _get_nav()

    # 更新NAV峰值
    peak = cb.get('nav_peak', 0) or 0
    if nav > peak:
        cb['nav_peak'] = nav
        peak = nav

    results = {'l1': False, 'l2': False, 'l3': False, 'blocked': False, 'reason': ''}

    # L3 回撤熔断（优先检查）
    if peak > 0 and nav > 0:
        drawdown = (peak - nav) / peak * 100
        if drawdown > MAX_DRAWDOWN_PCT:
            if not cb.get('l3'):
                cb['l3'] = True
                cb['l3_ts'] = now
                cb['l3_nav'] = nav
                cb['l3_peak'] = peak
                _save_cb(cb)
                _notify(f'L3回撤熔断！从峰值${peak:.2f}回撤{drawdown:.1f}%至${nav:.2f}，暂停所有开仓，需人工解除')
            results['l3'] = True
            results['blocked'] = True
            results['reason'] = f'L3回撤熔断({drawdown:.1f}%>={MAX_DRAWDOWN_PCT}%)'

    # L1 日亏损熔断 — 基于真实账户余额变化（不依赖信号数据库）
    # 每日00:00 UTC记录起始NAV，当日亏损 = 起始NAV - 当前NAV
    today_start_key = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cb_nav_history = cb.get('nav_history', {})
    if today_start_key not in cb_nav_history and nav > 0:
        cb_nav_history[today_start_key] = nav   # 记录今日起始NAV
        cb['nav_history'] = cb_nav_history
        _save_cb(cb)
    
    today_start_nav = cb_nav_history.get(today_start_key, nav)
    real_loss_pct = (today_start_nav - nav) / today_start_nav * 100 if today_start_nav > 0 else 0

    if real_loss_pct > DAILY_LOSS_PCT:
        if not cb.get('l1') or (now - (cb.get('l1_ts',0) or 0)) > L1_COOLDOWN:
            cb['l1'] = True
            cb['l1_ts'] = now
            _save_cb(cb)
            _notify(f'L1日亏熔断！今日真实亏损{real_loss_pct:.2f}%超过阈值{DAILY_LOSS_PCT}%，暂停24H')

    if cb.get('l1') and (now - (cb.get('l1_ts',0) or 0)) < L1_COOLDOWN:
        results['l1'] = True
        results['blocked'] = True
        remaining = L1_COOLDOWN - (now - cb.get('l1_ts', now))
        results['reason'] = f'L1日亏熔断（剩余{remaining/3600:.1f}H）'

    # L2 连亏熔断 — 去重：同一次settler批量结算不算「连续」
    # 用信号发出时间(ts)排序，同一时间窗口(10分钟内)的批量结算算1次
    raw_settled = [l for l in logs if l.get('settled') and l.get('outcome') in ('TP1','SL')]
    raw_settled.sort(key=lambda x: x.get('closed_ts',''))

    # 按10分钟窗口折叠：同窗口内有任意TP1则该窗口=TP1，全SL才算SL
    from collections import defaultdict
    windows = defaultdict(list)
    for s in raw_settled:
        ct = s.get('closed_ts','')
        try:
            t = datetime.fromisoformat(ct.replace('Z','+00:00'))
            # 10分钟窗口键
            wkey = t.strftime('%Y-%m-%dT%H:') + str(t.minute // 10)
        except:
            wkey = ct[:13]
        windows[wkey].append(s.get('outcome'))

    # 取最近10个窗口
    window_results = []
    for wkey in sorted(windows.keys())[-10:]:
        outcomes = windows[wkey]
        window_results.append('TP1' if 'TP1' in outcomes else 'SL')

    consec_sl = 0
    for outcome in reversed(window_results):
        if outcome == 'SL':
            consec_sl += 1
        else:
            break

    if consec_sl >= CONSEC_SL_COUNT:
        if not cb.get('l2') or (now - (cb.get('l2_ts',0) or 0)) > L2_COOLDOWN:
            cb['l2'] = True
            cb['l2_ts'] = now
            _save_cb(cb)
            _notify(f'L2连亏熔断！连续{consec_sl}个时间窗口止损，冷静期6H')

    if cb.get('l2') and (now - (cb.get('l2_ts',0) or 0)) < L2_COOLDOWN:
        results['l2'] = True
        if not results['blocked']:
            results['blocked'] = True
            remaining = L2_COOLDOWN - (now - cb.get('l2_ts', now))
            results['reason'] = f'L2连亏熔断({consec_sl}连亏，剩余{remaining/3600:.1f}H)'

    _save_cb(cb)
    return results


def is_blocked() -> tuple:
    """
    快速检查是否处于熔断状态
    返回 (blocked: bool, reason: str)
    """
    result = check_and_update()
    return result['blocked'], result.get('reason', '')


def reset_l1():
    """手工解除L1 + 写入指令总线覆盖2H，防止下次cron立即重触发"""
    cb = _load_cb()
    cb['l1'] = False
    _save_cb(cb)
    # 写入指令总线：2H内不重新触发L1
    try:
        import sys as _s; _s.path.insert(0, str(Path(__file__).parent))
        from command_register import set_override, DOMAIN_CIRCUIT_BREAKER
        set_override(DOMAIN_CIRCUIT_BREAKER, '人工解除L1', hours=2.0)
    except Exception: pass
    print("✅ L1熔断已解除（指令总线覆盖2H）")


def reset_l2():
    cb = _load_cb()
    cb['l2'] = False
    _save_cb(cb)
    try:
        import sys as _s; _s.path.insert(0, str(Path(__file__).parent))
        from command_register import set_override, DOMAIN_CIRCUIT_BREAKER
        set_override(DOMAIN_CIRCUIT_BREAKER, '人工解除L2', hours=2.0)
    except Exception: pass
    print("✅ L2熔断已解除（指令总线覆盖2H）")


def reset_l3():
    """L3需要人工确认才能解除"""
    cb = _load_cb()
    cb['l3'] = False
    _save_cb(cb)
    print("✅ L3回撤熔断已人工解除")


def _ts_to_unix(ts_str: str) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts_str.replace('Z','+00:00')).timestamp()
    except: return 0.0


def status() -> None:
    cb = _load_cb()
    now = time.time()
    nav = _get_nav()
    peak = cb.get('nav_peak', nav)
    drawdown = (peak - nav) / max(peak, 0.001) * 100 if peak else 0

    print(f"=== 熔断器状态 ===")
    print(f"NAV: ${nav:.2f}  峰值: ${peak:.2f}  回撤: {drawdown:.1f}%")

    l1_active = cb.get('l1') and (now - (cb.get('l1_ts',0) or 0)) < L1_COOLDOWN
    l2_active = cb.get('l2') and (now - (cb.get('l2_ts',0) or 0)) < L2_COOLDOWN
    l3_active = cb.get('l3', False)

    print(f"L1 日亏熔断: {'🔴 激活' if l1_active else '✅ 正常'}")
    print(f"L2 连亏熔断: {'🔴 激活' if l2_active else '✅ 正常'}")
    print(f"L3 回撤熔断: {'🔴 激活（需人工解除）' if l3_active else '✅ 正常'}")
    print(f"整体状态:   {'🛑 已熔断，禁止开仓' if (l1_active or l2_active or l3_active) else '✅ 正常运行'}")


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--status',   action='store_true')
    p.add_argument('--reset-l1', action='store_true')
    p.add_argument('--reset-l2', action='store_true')
    p.add_argument('--reset-l3', action='store_true')
    args = p.parse_args()

    if args.status:    status()
    elif getattr(args, 'reset_l1'): reset_l1()
    elif getattr(args, 'reset_l2'): reset_l2()
    elif getattr(args, 'reset_l3'): reset_l3()
    else:
        result = check_and_update()
        if result['blocked']:
            print(f"🛑 熔断激活: {result['reason']}")
            sys.exit(1)
        else:
            print("✅ 熔断器正常")


def auto_reset_on_restart() -> None:
    """
    Gateway重启后自动校验熔断状态。
    规则：L1/L2 是基于「结算数据」触发的，如果当前账户余额正常（NAV接近峰值）
    且真实亏损未超标 → 自动解除（防止废数据误触发的熔断持续跨重启存活）。
    L3（大回撤）不自动解除，必须人工确认。
    """
    cb = _load_cb()
    nav = _get_nav()
    peak = cb.get('nav_peak', nav) or nav

    # 如果当前NAV/峰值回撤 < 5%，说明没有发生真实大亏损
    # L1/L2 可能是废数据误触发，自动清除
    if nav > 0 and peak > 0:
        dd = (peak - nav) / peak * 100
        if dd < 5.0:
            changed = False
            if cb.get('l1'):
                cb['l1'] = False
                changed = True
                print('[CB-AutoReset] L1熔断自动解除（回撤<5%，非真实亏损触发）')
            if cb.get('l2'):
                cb['l2'] = False
                changed = True
                print('[CB-AutoReset] L2熔断自动解除（回撤<5%，非真实亏损触发）')
            if changed:
                _save_cb(cb)

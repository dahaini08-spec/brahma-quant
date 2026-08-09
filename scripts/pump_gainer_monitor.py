#!/usr/bin/env python3
"""
pump_gainer_monitor.py — 爆发后跟踪层（v2.0 角色重定位）
[v2.0 重定位 2026-08-09 苏摩111批准]

旧角色（v1.0 确认型）：「涨>15%才推送苏摩」= 追高通知
新角色（v2.0 跟踪型）：「暴涨猎手ALERT/FIRE后，跟踪该币是否已爆发」

设计哲学变更：
  暴涨猎手 = 预判层（底部蓄力→提前推送）
  gainer_monitor = 状态层（猎手信号发出后→跟踪是否进入爆发期）

工作流程：
  1. 读取暴涨猎手的 signal_push_record.json（有推送记录的币）
  2. 检测这些币是否已进入涨幅>10%的爆发期
  3. 若已爆发 → 写入 data/active_pumps.json + 推送「已起飞」标记
  4. 苏摩收到=「你之前布局的XX已经起飞了」，而非「有币在涨快追」

封印铁律：
  - 不再独立扫描全市场涨幅榜
  - 不推送「新入榜涨幅>15%」这类纯确认型信号
  - 所有推送必须对应暴涨猎手已有推送记录的币
"""
import sys, os
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for _p in [_BASE, os.path.join(_BASE, 'scripts')]:
    if _p not in sys.path: sys.path.insert(0, _p)

import json, time, requests, subprocess
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────
API           = 'https://fapi.binance.com'
ACTIVE_PUMPS  = Path(_BASE) / 'data' / 'active_pumps.json'
STATE_FILE    = Path(_BASE) / 'data' / 'gainer_monitor_state.json'
PUSH_RECORD   = Path(_BASE) / 'dharma' / 'pump_hunter' / 'signal_push_record.json'

# 爆发判定阈值（猎手推送后，价格涨超此值=已起飞）
LIFTOFF_THR   = 8.0    # 8% = 确认爆发
WATCH_THR     = 3.0    # 3% = 开始动了
DEDUP_SEC     = 8 * 3600  # 8H不重复推送同一币

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    JARVIS_TARGET = '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291'


def _load_push_record() -> dict:
    try:
        if PUSH_RECORD.exists():
            return json.loads(PUSH_RECORD.read_text())
    except Exception:
        pass
    return {}


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {'notified': {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _load_active_pumps() -> dict:
    try:
        if ACTIVE_PUMPS.exists():
            return json.loads(ACTIVE_PUMPS.read_text())
    except Exception:
        pass
    return {}


def _save_active_pumps(data: dict):
    ACTIVE_PUMPS.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PUMPS.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _send(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis', '-t', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


def main():
    now_ts = time.time()

    # 读取猎手已推送的币
    push_record = _load_push_record()
    if not push_record:
        print('HEARTBEAT_OK')
        return

    # 只跟踪最近12H内猎手推过的币
    recent_syms = [
        sym for sym, info in push_record.items()
        if isinstance(info, dict)
        and now_ts - info.get('last_push_ts', 0) < 12 * 3600
    ]

    if not recent_syms:
        print('HEARTBEAT_OK')
        return

    # 拉取这些币的当前行情
    try:
        tickers_raw = requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=10).json()
        tickers = {t['symbol']: t for t in tickers_raw if isinstance(t, dict)}
    except Exception as e:
        print(f'[gainer-monitor v2] ticker拉取失败: {e}')
        return

    state       = _load_state()
    notified    = state.get('notified', {})
    active_pumps = _load_active_pumps()
    pushed_any   = False
    liftoff_list = []
    watch_list   = []

    for sym in recent_syms:
        tick = tickers.get(sym)
        if not tick:
            continue

        push_info   = push_record[sym]
        push_price  = push_info.get('push_price', 0)
        push_ts     = push_info.get('last_push_ts', 0)
        push_score  = push_info.get('last_score', 0)
        cur_price   = float(tick.get('lastPrice', 0))
        chg_24h     = float(tick.get('priceChangePercent', 0))

        if push_price <= 0 or cur_price <= 0:
            continue

        # 自推送后涨幅（猎手推送后的实际收益）
        since_push_pct = (cur_price - push_price) / push_price * 100

        # 更新 active_pumps 状态
        active_pumps[sym] = {
            'push_ts':         push_ts,
            'push_price':      push_price,
            'push_score':      push_score,
            'cur_price':       cur_price,
            'since_push_pct':  round(since_push_pct, 2),
            'chg_24h':         round(chg_24h, 2),
            'updated_ts':      now_ts,
            'status':          'LIFTOFF' if since_push_pct >= LIFTOFF_THR else
                               'MOVING'  if since_push_pct >= WATCH_THR  else
                               'WAITING',
        }

        last_notify = notified.get(sym, 0)
        in_dedup    = (now_ts - last_notify) < DEDUP_SEC

        if since_push_pct >= LIFTOFF_THR and not in_dedup:
            liftoff_list.append({
                'sym': sym, 'since_push_pct': since_push_pct,
                'push_score': push_score, 'cur_price': cur_price,
                'push_price': push_price, 'chg_24h': chg_24h,
            })
            notified[sym] = now_ts

        elif WATCH_THR <= since_push_pct < LIFTOFF_THR and not in_dedup:
            watch_list.append({
                'sym': sym, 'since_push_pct': since_push_pct,
                'push_score': push_score, 'cur_price': cur_price,
            })

    _save_active_pumps(active_pumps)
    state['notified']  = notified
    state['last_run']  = now_ts
    _save_state(state)

    # 推送「已起飞」通知（猎手布局后真正爆发的）
    if liftoff_list:
        lines = [f'🚀 [猎手验证] {len(liftoff_list)}个预判币已起飞！', '']
        for e in sorted(liftoff_list, key=lambda x: -x['since_push_pct'])[:6]:
            lines.append(
                f'✅ {e["sym"]:15} 自布局+{e["since_push_pct"]:.1f}%  '
                f'(猎手评分:{e["push_score"]}  布局价:{e["push_price"]:.4g}→{e["cur_price"]:.4g})'
            )
        lines += ['', '📊 以上均为暴涨猎手预判后的实际爆发验证']
        _send('\n'.join(lines))
        pushed_any = True

    if pushed_any:
        print(f'[gainer-monitor v2] 推送: 起飞{len(liftoff_list)}个 观察{len(watch_list)}个')
    else:
        watching = sum(1 for v in active_pumps.values() if v.get('status') == 'WAITING')
        moving   = sum(1 for v in active_pumps.values() if v.get('status') == 'MOVING')
        print(f'HEARTBEAT_OK  (跟踪:{len(recent_syms)}个 待发动:{watching} 启动中:{moving})')


if __name__ == '__main__':
    main()

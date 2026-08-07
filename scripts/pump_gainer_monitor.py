#!/usr/bin/env python3
"""
pump_gainer_monitor.py — 合约涨幅榜实时监控
设计院封印 2026-08-07 苏摩111自主决策

核心逻辑：
  - 每1H扫描合约涨幅榜
  - 涨幅>15% 且 是「新入榜」(上次扫描时<10%) → 立即推送苏摩
  - 额外检测：涨幅>30%但OI还在暴增 → 推送「妖币持续发动」警报
  - 避免重复推送：同一币种6H内不重复

设计哲学：
  暴涨猎手负责「发现爆发前信号」，涨幅榜监控负责「告知苏摩现在谁在涨」
  两者互补，不互斥
"""
import sys, os
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for _p in [_BASE, os.path.join(_BASE, 'scripts')]:
    if _p not in sys.path: sys.path.insert(0, _p)

import json, time, requests, subprocess
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────
API          = 'https://fapi.binance.com'
STATE_FILE   = Path(_BASE) / 'data' / 'gainer_monitor_state.json'
DEDUP_SEC    = 6 * 3600        # 同一币种6H不重复
NEW_GAINER_THR = 15.0          # 新入榜阈值：涨幅>15%
PREV_THR       = 10.0          # 上次扫描时涨幅<10% → 判定为新入榜
CONTINUE_THR   = 30.0          # 持续发动阈值：>30%且OI仍在涨
MIN_VOL        = 5_000_000     # 最小成交量5M（过滤垃圾小币）
EXCLUDE        = {'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT'}
TOP_N          = 20            # 每次最多推送TOP20涨幅

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    JARVIS_TARGET = '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291'


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {'last_scan': {}, 'push_record': {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _send(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis', '-t', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


def _get_oi_change(sym: str) -> float:
    """获取OI近1H变化%"""
    try:
        hist = requests.get(
            f'{API}/futures/data/openInterestHist',
            params={'symbol': sym, 'period': '1h', 'limit': 4},
            timeout=4
        ).json()
        if isinstance(hist, list) and len(hist) >= 2:
            v0 = float(hist[0]['sumOpenInterestValue'])
            v1 = float(hist[-1]['sumOpenInterestValue'])
            return (v1 - v0) / v0 * 100 if v0 > 0 else 0
    except Exception:
        pass
    return 0.0


def main():
    state = _load_state()
    last_scan   = state.get('last_scan', {})    # {sym: chg_pct}
    push_record = state.get('push_record', {})  # {sym: push_ts}
    now_ts = time.time()

    # 拉取全量行情
    try:
        tickers = requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=10).json()
    except Exception as e:
        print(f'[gainer-monitor] ticker拉取失败: {e}')
        return

    # 过滤有效合约
    valid = [
        t for t in tickers
        if isinstance(t, dict)
        and t.get('symbol', '').endswith('USDT')
        and 'UP' not in t['symbol']
        and 'DOWN' not in t['symbol']
        and t['symbol'] not in EXCLUDE
        and float(t.get('quoteVolume', 0)) >= MIN_VOL
    ]

    # 按涨幅排序
    gainers = sorted(valid, key=lambda x: -float(x.get('priceChangePercent', 0)))

    new_entries   = []  # 新入榜：上次<10%，现在>15%
    continue_list = []  # 持续发动：>30%且OI仍涨

    for t in gainers[:TOP_N * 3]:  # 多扫一些以覆盖边界
        sym = t['symbol']
        chg = float(t.get('priceChangePercent', 0))
        vol = float(t.get('quoteVolume', 0))
        price = float(t.get('lastPrice', 0))

        prev_chg = last_scan.get(sym, 0.0)
        last_push = push_record.get(sym, 0)
        in_dedup  = (now_ts - last_push) < DEDUP_SEC

        # 新入榜检测
        if chg >= NEW_GAINER_THR and prev_chg < PREV_THR and not in_dedup:
            new_entries.append({
                'symbol': sym, 'chg': chg, 'vol': vol, 'price': price,
                'prev_chg': prev_chg
            })

        # 持续发动检测（涨幅>30%且OI仍暴增）
        if chg >= CONTINUE_THR and not in_dedup:
            oi_chg = _get_oi_change(sym)
            if oi_chg >= 20:
                continue_list.append({
                    'symbol': sym, 'chg': chg, 'vol': vol,
                    'price': price, 'oi_chg': oi_chg
                })

    # 更新本次扫描快照
    new_last_scan = {t['symbol']: float(t.get('priceChangePercent', 0)) for t in gainers[:200]}
    state['last_scan']   = new_last_scan
    state['last_ts']     = now_ts

    pushed_any = False

    # 推送新入榜
    if new_entries:
        lines = [f'🚀 [涨幅榜新入榜] {len(new_entries)}个妖币突然启动', '']
        for e in sorted(new_entries, key=lambda x: -x['chg'])[:8]:
            lines.append(
                f'📌 {e["symbol"]:15} +{e["chg"]:.1f}%  '
                f'vol={e["vol"]/1e6:.0f}M  '
                f'(前:{e["prev_chg"]:+.1f}%)'
            )
            push_record[e['symbol']] = now_ts
        lines.append('')
        lines.append('⚡ 以上为新启动妖币，注意跟踪入场机会')
        _send('\n'.join(lines))
        pushed_any = True

    # 推送持续发动
    if continue_list:
        lines = [f'🔥 [妖币持续发动] {len(continue_list)}个高涨幅+OI暴增', '']
        for e in sorted(continue_list, key=lambda x: -x['chg'])[:5]:
            lines.append(
                f'🔴 {e["symbol"]:15} +{e["chg"]:.1f}%  '
                f'OI+{e["oi_chg"]:.0f}%  '
                f'vol={e["vol"]/1e6:.0f}M'
            )
            push_record[e['symbol']] = now_ts
        lines.append('')
        lines.append('⚠️ 高涨幅+OI仍在暴增，轧空未结束，注意追涨风险')
        _send('\n'.join(lines))
        pushed_any = True

    state['push_record'] = push_record
    _save_state(state)

    if pushed_any:
        print(f'[gainer-monitor] 推送: 新入榜{len(new_entries)}个 持续发动{len(continue_list)}个')
    else:
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    main()

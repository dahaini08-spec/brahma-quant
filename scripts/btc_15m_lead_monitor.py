#!/usr/bin/env python3
"""
btc_15m_lead_monitor.py — BTC 15m 领先信号监控
设计院封印 2026-08-08 苏摩111自主决策

核心逻辑：
  独立监控 BTC 15m K线，不等待4H信号触发
  触发条件：BTC 15m 连续3根同向 + OI方向一致 → 立即推送
  价值：ETH信号比BTC慢15-30分钟，用BTC领先获得时间优势

运行方式：cron every 15m（与K线周期对齐）
"""
import sys, os, time, json, requests
from pathlib import Path
from datetime import datetime, timezone

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(_BASE / 'scripts'))

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    JARVIS_TARGET = '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291'

API = 'https://fapi.binance.com'
STATE_FILE = _BASE / 'data' / 'btc_15m_lead_state.json'
DEDUP_SEC  = 3600  # 同一方向1H内不重复推送


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {'last_push_ts': 0, 'last_direction': '', 'last_bar_ts': 0}


def _save_state(s: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


def _send(msg: str):
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis', '-t', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


def _get_oi_change(symbol: str, period: str = '15m', limit: int = 4) -> float:
    """OI近1小时变化%"""
    try:
        hist = requests.get(
            f'{API}/futures/data/openInterestHist',
            params={'symbol': symbol, 'period': '1h', 'limit': 3},
            timeout=5
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
    now_ts = time.time()

    # 拉取 BTC 最近 8 根 15m K线（含当前未完成的根）
    try:
        klines = requests.get(
            f'{API}/fapi/v1/klines',
            params={'symbol': 'BTCUSDT', 'interval': '15m', 'limit': 8},
            timeout=8
        ).json()
    except Exception as e:
        print(f'[btc-15m-lead] K线获取失败: {e}')
        return

    if not isinstance(klines, list) or len(klines) < 5:
        print('HEARTBEAT_OK')
        return

    # 取最近4根已完成K线（排除最后一根当前未完成）
    bars = klines[-5:-1]
    opens  = [float(b[1]) for b in bars]
    closes = [float(b[4]) for b in bars]
    vols   = [float(b[5]) for b in bars]
    bar_ts = int(bars[-1][0])  # 最后一根已完成K线的开盘时间

    # 去重：同一根K线不重复触发
    if bar_ts <= state.get('last_bar_ts', 0):
        print('HEARTBEAT_OK')
        return

    avg_vol = sum(vols) / len(vols) if vols else 1

    # 检测连续3根同向（取最后3根）
    last3_opens  = opens[-3:]
    last3_closes = closes[-3:]
    candle_dir = [('BULL' if c > o else 'BEAR') for o, c in zip(last3_opens, last3_closes)]

    consecutive_bull = all(d == 'BULL' for d in candle_dir)
    consecutive_bear = all(d == 'BEAR' for d in candle_dir)

    if not (consecutive_bull or consecutive_bear):
        state['last_bar_ts'] = bar_ts
        _save_state(state)
        print('HEARTBEAT_OK')
        return

    direction = 'LONG' if consecutive_bull else 'SHORT'

    # 去重：同方向1H内不重复
    if (direction == state.get('last_direction') and
            (now_ts - state.get('last_push_ts', 0)) < DEDUP_SEC):
        state['last_bar_ts'] = bar_ts
        _save_state(state)
        print('HEARTBEAT_OK')
        return

    # 验证 OI 方向一致
    oi_chg = _get_oi_change('BTCUSDT')
    oi_aligned = (direction == 'LONG' and oi_chg > 0) or \
                 (direction == 'SHORT' and oi_chg < 0)

    if not oi_aligned and abs(oi_chg) < 0.5:
        # OI中性时不拦截，但降低推送优先级
        oi_note = f'OI中性({oi_chg:+.2f}%)'
    elif not oi_aligned:
        # OI明确反向 → 可能是假突破，拦截
        print(f'[btc-15m-lead] {direction} 被OI反向拦截 (OI{oi_chg:+.2f}%)')
        state['last_bar_ts'] = bar_ts
        _save_state(state)
        print('HEARTBEAT_OK')
        return
    else:
        oi_note = f'OI{oi_chg:+.2f}% ✅'

    # 计算动能强度
    total_move = (closes[-1] - closes[-4]) / closes[-4] * 100 if closes[-4] > 0 else 0
    last_vol_ratio = vols[-1] / avg_vol

    icon = '🚀' if direction == 'LONG' else '🔻'
    price = closes[-1]
    dt_str = datetime.now(timezone.utc).strftime('%H:%M UTC')

    msg = f"""{icon} [BTC 15m领先信号] {direction} 动能启动

BTC ${price:,.0f} | {dt_str}
连续3根{'阳' if direction=='LONG' else '阴'}线 | 累计移动{total_move:+.2f}%
成交量: {last_vol_ratio:.1f}x均量 | {oi_note}

⚡ 含义: BTC动能先于ETH约15-30分钟
📌 关注: {'ETH/SOL跟涨机会，等待1H确认信号' if direction=='LONG' else 'ETH/SOL跟跌风险，注意多单止损'}"""

    _send(msg)

    state['last_push_ts'] = now_ts
    state['last_direction'] = direction
    state['last_bar_ts'] = bar_ts
    _save_state(state)

    print(f'[btc-15m-lead] 推送 {direction} 连续3根同向 OI={oi_chg:+.2f}% 移动{total_move:+.2f}%')


if __name__ == '__main__':
    main()

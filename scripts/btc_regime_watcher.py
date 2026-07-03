import os
#!/usr/bin/env python3
"""
btc_regime_watcher.py · 梵天P1升级版 · BTC价格关键位穿越监控 v2.0
[设计院重构 · 2026-06-30 · 苏摩裁决]

v2.0 核心变更（苏摩裁决：只推关键重要信息）：
  ❌ 删除：RSI阈值穿越推送（动能描述，非关键事件）
  ✅ 保留：EMA20_4H 多空分界穿越
  ✅ 新增：关键价格位穿越（50H高低点 = 真正的破位信号）
  ✅ 新增：动态关键位自动更新（每日滚动计算最新支撑/阻力）

触发条件（任一满足才推送）：
  1. BTC 价格穿越 EMA20_4H（多空主控线）
  2. BTC 价格突破 50H高点（突破信号 → 目标上行）
  3. BTC 价格跌破 50H低点（破位信号 → 目标下行）

无穿越：完全静默
无穿越 + 超30分钟：心跳写入 regime_state.json（防360误报）
"""

import sys, os, json, time, subprocess, requests

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'btc_regime_watcher_state.json')
CHECKER    = os.path.join(BASE_DIR, 'scripts', 'position_regime_checker.py')

FAPI = 'https://fapi.binance.com'

PUSH_TARGET  = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
PUSH_CHANNEL = 'jarvis'

# 心跳间隔：30分钟刷新 regime_state.json，防360误报"陈旧"
HEARTBEAT_INTERVAL = 30 * 60


def get_btc_data():
    """获取BTC现价 + EMA20_4H + 50H高低点"""
    try:
        # 1H K线（取60根，计算50H高低点）
        r1 = requests.get(f"{FAPI}/fapi/v1/klines",
                          params={'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 60},
                          timeout=10)
        kl_1h = r1.json()
        closes_1h = [float(k[4]) for k in kl_1h]
        highs_1h  = [float(k[2]) for k in kl_1h]
        lows_1h   = [float(k[3]) for k in kl_1h]

        price     = closes_1h[-1]
        high_50h  = round(max(highs_1h[-50:]), 2)
        low_50h   = round(min(lows_1h[-50:]), 2)

        # 4H K线 → EMA20
        r2 = requests.get(f"{FAPI}/fapi/v1/klines",
                          params={'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 30},
                          timeout=10)
        closes_4h = [float(k[4]) for k in r2.json()]
        k_factor  = 2 / (20 + 1)
        ema20_4h  = closes_4h[0]
        for v in closes_4h[1:]:
            ema20_4h = v * k_factor + ema20_4h * (1 - k_factor)
        ema20_4h = round(ema20_4h, 1)

        return price, ema20_4h, high_50h, low_50h

    except Exception:
        return None, None, None, None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            'price_above_ema20_4h': None,
            'price_above_high50h':  None,
            'price_above_low50h':   None,
            'last_trigger_ts':      0,
            'last_heartbeat_ts':    0,
            # 保存上次的关键位，用于推送时显示目标
            'last_high50h':         None,
            'last_low50h':          None,
            'last_ema20_4h':        None,
        }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def push_alert(msg):
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target',  PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def trigger_position_check():
    """触发持仓体制匹配检查"""
    try:
        checker_state = os.path.join(BASE_DIR, 'data', 'position_regime_state.json')
        if os.path.exists(checker_state):
            with open(checker_state) as f:
                s = json.load(f)
            s['last_alerts'] = {}
            with open(checker_state, 'w') as f:
                json.dump(s, f)
        result = subprocess.run(
            [sys.executable, CHECKER],
            capture_output=True, text=True, timeout=30,
            cwd=BASE_DIR
        )
        return result.returncode == 0
    except Exception:
        return False


def do_regime_heartbeat():
    """静默刷新 regime_state.json 时间戳，防360误报陈旧"""
    regime_file = os.path.join(BASE_DIR, 'data', 'regime_state.json')
    try:
        if not os.path.exists(regime_file):
            return False
        with open(regime_file, 'r') as f:
            data = json.load(f)
        data['_heartbeat_ts'] = int(time.time())
        tmp = regime_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, regime_file)
        return True
    except Exception as e:
        print(f'[Watcher] ⚠️ 心跳写入失败: {e}')
        return False


def calc_target(price, key_level, direction):
    """根据穿越方向估算下一目标位（ATR粗估 = 关键位的1.5%）"""
    atr_est = key_level * 0.015
    if direction == 'up':
        return round(key_level + atr_est * 1.5, 1)
    else:
        return round(key_level - atr_est * 1.5, 1)


def main():
    state = load_state()
    now   = int(time.time())

    price, ema20_4h, high_50h, low_50h = get_btc_data()
    if price is None:
        save_state(state)
        return

    prev_above_ema  = state.get('price_above_ema20_4h')
    prev_above_h50  = state.get('price_above_high50h')
    prev_above_l50  = state.get('price_above_low50h')

    # ── 防抖三重门控（设计院 2026-07-02 封印）──────────────
    # 问题根因：BTC在EMA附近震荡，每5分钟反复触发矛盾穿越信号
    # 修复：0.3%缓冲区 + 1H冷却期 + 2根K线收盘确认
    MIN_CROSS_PCT = 0.003   # 0.3%缓冲带
    COOLDOWN_SEC  = 3600    # 同方向1小时内不重复推送
    CONFIRM_BARS  = 2       # 需要连续2根已收盘1H K线确认

    # 缓冲区判断：偏离EMA超0.3%才识别为穿越
    ema_diff_pct = (price - ema20_4h) / ema20_4h
    if ema_diff_pct > MIN_CROSS_PCT:
        price_above_ema = True
    elif ema_diff_pct < -MIN_CROSS_PCT:
        price_above_ema = False
    else:
        price_above_ema = prev_above_ema  # 缓冲区内保持上次状态

    price_above_h50 = price > high_50h * (1 + MIN_CROSS_PCT)
    price_above_l50 = price > low_50h  * (1 - MIN_CROSS_PCT)

    last_ema_dir = state.get('last_ema_direction', None)
    last_ema_ts  = state.get('last_ema_trigger_ts', 0)

    def confirm_cross_bars(above_val, ema_val, bars=CONFIRM_BARS):
        """K线确认：连续N根已收盘1H K线均在EMA同侧"""
        try:
            import requests as _rq
            kc = _rq.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol':'BTCUSDT','interval':'1h','limit':bars+2},timeout=5).json()
            closed = [float(c[4]) for c in kc[:-1]][-bars:]
            buf = ema_val * MIN_CROSS_PCT * 0.5
            return all(c > ema_val + buf for c in closed) if above_val else all(c < ema_val - buf for c in closed)
        except Exception:
            return True

    triggered    = False
    alert_lines  = []

    # ── 1. EMA20_4H 穿越（三重门控）──────────────────────
    if prev_above_ema is not None and prev_above_ema != price_above_ema:
        cur_dir     = 'up' if price_above_ema else 'down'
        in_cooldown = (cur_dir == last_ema_dir) and ((now - last_ema_ts) < COOLDOWN_SEC)
        confirmed   = confirm_cross_bars(price_above_ema, ema20_4h)
        if in_cooldown:
            print(f'[Watcher] 冷却中({cur_dir}) {now-last_ema_ts:.0f}s/{COOLDOWN_SEC}s')
        elif not confirmed:
            print(f'[Watcher] 未确认 需{CONFIRM_BARS}根1H已收盘K线')
        else:
            triggered = True
            if price_above_ema:
                target = calc_target(price, ema20_4h, 'up')
                alert_lines.append(f"📈 BTC 突破 EMA20_4H ${ema20_4h:,.1f} → 目标 ${target:,.1f}")
            else:
                target = calc_target(price, ema20_4h, 'down')
                alert_lines.append(f"📉 BTC 跌破 EMA20_4H ${ema20_4h:,.1f} → 目标 ${target:,.1f}")
            state['last_ema_direction']  = cur_dir
            state['last_ema_trigger_ts'] = now
    # ── 2. 50H 高点突破（做多信号）───────────────────────
    if prev_above_h50 is not None and not prev_above_h50 and price_above_h50:
        triggered = True
        target = calc_target(price, high_50h, 'up')
        alert_lines.append(
            f"🚀 BTC 突破50H高点 ${high_50h:,.1f} → 目标 ${target:,.1f}"
        )

    # ── 3. 50H 低点跌破（破位信号）───────────────────────
    if prev_above_l50 is not None and prev_above_l50 and not price_above_l50:
        triggered = True
        target = calc_target(price, low_50h, 'down')
        alert_lines.append(
            f"💥 BTC 跌破50H低点 ${low_50h:,.1f} → 目标 ${target:,.1f}"
        )

    # ── 推送 ─────────────────────────────────────────────
    if triggered:
        summary = '\n'.join(alert_lines)
        msg = f"⚡ BTC关键位穿越\n{summary}\n现价 ${price:,.1f}"
        push_alert(msg)
        trigger_position_check()
        state['last_trigger_ts']   = now
        state['last_heartbeat_ts'] = now
        print(f"[Watcher] ✅ 触发 | {summary}")

    # ── 心跳写入（无穿越时每30m刷新时间戳）──────────────
    else:
        last_hb = state.get('last_heartbeat_ts', 0)
        if (now - last_hb) >= HEARTBEAT_INTERVAL:
            if do_regime_heartbeat():
                state['last_heartbeat_ts'] = now

    # 同步brahma_state.json SSOT（防止体制滖后超过20H）
    try:
        import importlib.util as _ilu
        _sp = os.path.join(BASE_DIR, 'scripts', 'sync_brahma_state.py')
        _spec = _ilu.spec_from_file_location('sync_brahma_state', _sp)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.sync()
    except Exception as _se:
        pass  # SSOT同步失败不影响主流程

    # 更新状态
    state['price_above_ema20_4h'] = price_above_ema
    state['price_above_high50h']  = price_above_h50
    state['price_above_low50h']   = price_above_l50
    state['last_high50h']  = high_50h
    state['last_low50h']   = low_50h
    state['last_ema20_4h'] = ema20_4h
    save_state(state)


if __name__ == '__main__':
    main()

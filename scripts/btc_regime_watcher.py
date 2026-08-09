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

try:
    from scripts.system_config import JARVIS_TARGET as _SSOT_TARGET, JARVIS_CHANNEL as _SSOT_CHANNEL
    PUSH_TARGET  = os.environ.get('JARVIS_TARGET', _SSOT_TARGET)
    PUSH_CHANNEL = _SSOT_CHANNEL
except Exception:
    PUSH_TARGET  = os.environ.get('JARVIS_TARGET', '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291')  # SSOT fallback
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


def get_eth_regime():
    """计算ETH实时体制 (EMA20_4H + RSI_1H)"""
    try:
        r4 = requests.get(f'{FAPI}/fapi/v1/klines',
            params={'symbol': 'ETHUSDT', 'interval': '4h', 'limit': 30}, timeout=8)
        c4 = [float(k[4]) for k in r4.json()]
        k_f = 2 / (20 + 1); ema20 = c4[0]
        for v in c4[1:]: ema20 = v * k_f + ema20 * (1 - k_f)
        price = c4[-1]
        r1 = requests.get(f'{FAPI}/fapi/v1/klines',
            params={'symbol': 'ETHUSDT', 'interval': '1h', 'limit': 20}, timeout=8)
        c1 = [float(k[4]) for k in r1.json()]
        gains  = [max(0, c1[i] - c1[i-1]) for i in range(1, len(c1))]
        losses = [max(0, c1[i-1] - c1[i]) for i in range(1, len(c1))]
        ag = sum(gains[-14:]) / 14; al = sum(losses[-14:]) / 14
        rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100
        phase = 'BULL' if price > ema20 else 'BEAR'
        if phase == 'BULL' and rsi > 55: return 'BULL_TREND'
        elif phase == 'BULL': return 'BULL_EARLY'
        elif phase == 'BEAR' and rsi < 45: return 'BEAR_TREND'
        else: return 'BEAR_RECOVERY'
    except Exception:
        return 'UNKNOWN'


REGIME_HISTORY_FILE = os.path.join(BASE_DIR, 'data', 'regime_history.json')

def _append_regime_history(old_regime: str, new_regime: str, trigger: str = ''):
    """[修复2 2026-07-18 苏摩111] 体制切换历史持久化
    每次体制变化追加到 data/regime_history.json
    """
    try:
        from datetime import datetime, timezone
        history = []
        if os.path.exists(REGIME_HISTORY_FILE):
            with open(REGIME_HISTORY_FILE) as f:
                history = json.load(f)
        if not isinstance(history, list):
            history = []
        history.append({
            'ts':          datetime.now(tz=timezone.utc).isoformat(),
            'prev_regime': old_regime,
            'regime':      new_regime,
            'trigger':     trigger,
        })
        # 保留最近200条
        if len(history) > 200:
            history = history[-200:]
        with open(REGIME_HISTORY_FILE, 'w') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    # [设计院 2026-07-06] 同步写入ETH体制
    state['eth_regime'] = get_eth_regime()
    state['eth_regime_updated_at'] = time.time()
    # [修复2 2026-07-18] 体制切换时写入历史
    _old = state.get('_prev_regime_for_history', state.get('regime', ''))
    _new = state.get('regime', '')
    if _old and _new and _old != _new:
        _append_regime_history(_old, _new, state.get('regime_trigger', ''))
    state['_prev_regime_for_history'] = _new
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    # ── [P2-B修复 2026-07-24 苏摩确认] 强制同步写入 regime_state.json ────────
    # brahma_engine读取的是 regime_state_machine.RegimeStateMachine
    # 其STATE_FILE = data/regime_state.json — 与本文件完全独立
    # 修复：每次运行后强制将当前体制写入 regime_state.json（SSOT桥接）
    try:
        import time as _time_sync
        _rsf = os.path.join(BASE_DIR, 'data', 'regime_state.json')
        _existing_rs = {}
        if os.path.exists(_rsf):
            with open(_rsf) as _f: _existing_rs = json.load(_f)
        _btc_rs = _existing_rs.get('BTCUSDT', {})
        _new_regime = state.get('regime', _btc_rs.get('confirmed', 'UNKNOWN'))
        if _new_regime and _new_regime != '?':
            # [防误判封印 2026-08-05] BULL_TREND/BEAR_TREND切换需双重RSI_4H确认
            # 防止1H短暂噪音触发CONFIRMED体制切换
            try:
                import requests as _rq_rb
                _kl = _rq_rb.get('https://fapi.binance.com/fapi/v1/klines',
                                  params={'symbol':'BTCUSDT','interval':'4h','limit':20},timeout=5).json()
                _c4h = [float(k[4]) for k in _kl]
                _d4h = [_c4h[i]-_c4h[i-1] for i in range(1,len(_c4h))]
                _g=[max(0,x) for x in _d4h]; _l=[max(0,-x) for x in _d4h]
                _ag=sum(_g[-14:])/14; _al=sum(_l[-14:])/14
                _rsi4h = 100-100/(1+_ag/_al) if _al>0 else 100
                # 门控：BULL_TREND需RSI_4H>52，BEAR_TREND需RSI_4H<45
                _regime_ok = True
                if _new_regime == 'BULL_TREND' and _rsi4h < 52:
                    _new_regime = 'BEAR_RECOVERY'  # 降级，RSI不足
                elif _new_regime == 'BEAR_TREND' and _rsi4h > 45:  # [2026-08-05 48→45收紧]
                    _new_regime = 'CHOP_MID'       # 降级，RSI未到空头区
            except Exception:
                pass  # 网络失败则不降级，保留原判断
            _btc_rs.update({
                'confirmed':      _new_regime,
                'confirmed_cn':   {'BEAR_TREND':'熊市趋势','BULL_TREND':'牛市趋势',
                                   'CHOP_MID':'震荡','BEAR_RECOVERY':'熊市反弹',
                                   'BEAR_EARLY':'熊市初期','BULL_EARLY':'牛市初期'}.get(_new_regime, _new_regime),
                'candidate':      None,
                'confirm_count':  3,   # 已确认
                'locked_until':   _time_sync.time() + 600,  # 10分钟防抖
                'confirmed_at':   _time_sync.time(),
                'switch_count_24h': _btc_rs.get('switch_count_24h', 0),
                'last_raw':       _new_regime,
                'synced_from':    'btc_regime_watcher',
            })
            _existing_rs['BTCUSDT'] = _btc_rs
            _tmp_rs = _rsf + '.tmp'
            with open(_tmp_rs, 'w') as _f: json.dump(_existing_rs, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp_rs, _rsf)
            # [regime_bus同步 2026-08-05] watcher写入同步到总线
            try:
                import sys as _sys_rb; _sys_rb.path.insert(0, os.path.join(BASE_DIR,'scripts'))
                from regime_bus import update as _rb_upd_w
                _rb_upd_w('BTCUSDT', _new_regime, 'CONFIRMED', 'btc_regime_watcher', score=0)
            except Exception: pass
    except Exception as _e_sync:
        pass  # 同步失败不影响主流程
    # ─────────────────────────────────────────────────────────────────────────


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
        pass  # [静默]
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
    MIN_CROSS_PCT   = 0.003   # 0.3%缓冲带
    COOLDOWN_SEC    = 3600    # 同方向1小时内不重复推送
    CONFIRM_BARS    = 2       # 需要连续2根已收盘1H K线确认
    # [防误判升级 2026-08-05 苏摩111封印]
    # 修复: 01:02 1H短暂下探触发BULL→BEAR误判
    # A. 体制切换反向冷却: BULL确立后N秒内禁止切BEAR（4H=14400s≈4根4H K线）
    SWITCH_LOCK_SEC = 14400   # 切换后4小时内禁止反向切换
    # B. BEAR_TREND需4H级别确认（非1H）
    BEAR_CONFIRM_4H = 2       # 需要连续2根已收盘4H K线在EMA下方
    # C. RSI_4H严格门控（原48→收紧到45）
    BEAR_RSI4H_MAX  = 45      # BEAR_TREND确认需RSI_4H<45（原48太宽松）

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

    def confirm_bear_4h(ema_val, bars=BEAR_CONFIRM_4H):
        """[防误判升级] BEAR确认需连续N根已收盘4H K线在EMA下方 + RSI_4H<BEAR_RSI4H_MAX"""
        try:
            import requests as _rq
            kc = _rq.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol':'BTCUSDT','interval':'4h','limit':bars+5},timeout=5).json()
            closed4h = [float(c[4]) for c in kc[:-1]][-bars:]  # 已收盘的4H
            buf = ema_val * MIN_CROSS_PCT
            bars_ok = all(c < ema_val - buf for c in closed4h)
            # RSI_4H严格门控
            d4h=[closed4h[i]-closed4h[i-1] for i in range(1,len(closed4h))]
            g=[max(0,x) for x in d4h]; l=[max(0,-x) for x in d4h]
            ag=sum(g)/len(g) if g else 0; al=sum(l)/len(l) if l else 1
            rsi4h = 100-100/(1+ag/al) if al>0 else 100
            rsi_ok = rsi4h < BEAR_RSI4H_MAX
            return bars_ok and rsi_ok
        except Exception:
            return False  # 失败时保守处理：不确认BEAR

    triggered    = False
    alert_lines  = []

    # ── 1. EMA20_4H 穿越（四重门控 2026-08-05升级）──────────────────────
    if prev_above_ema is not None and prev_above_ema != price_above_ema:
        cur_dir     = 'up' if price_above_ema else 'down'
        in_cooldown = (cur_dir == last_ema_dir) and ((now - last_ema_ts) < COOLDOWN_SEC)
        confirmed   = confirm_cross_bars(price_above_ema, ema20_4h)

        # [防误判门控A] 反向切换冷却：上次切换后SWITCH_LOCK_SEC内禁止反向
        last_switch_ts  = state.get('last_switch_ts', 0)
        last_switch_dir = state.get('last_switch_dir', None)
        in_switch_lock  = (last_switch_dir and cur_dir != last_switch_dir
                           and (now - last_switch_ts) < SWITCH_LOCK_SEC)

        # [防误判门控B] 向下穿越(BEAR候选)需额外4H确认
        if not price_above_ema:
            confirmed = confirmed and confirm_bear_4h(ema20_4h)

        if in_cooldown or in_switch_lock:
            pass  # [静默：冷却中]
        elif not confirmed:
            pass  # [静默：未通过K线确认]
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
            state['last_switch_ts']      = now   # 记录本次切换时间
            state['last_switch_dir']     = cur_dir
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
        pass  # [静默]

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

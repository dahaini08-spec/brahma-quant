#!/usr/bin/env python3
"""
regime_realtime_watcher.py — 实时体制感知层
设计院三方封印 2026-09-04 苏摩111

解决问题：体制标签滞后30~60分钟
机制：检测价格突破BBW / OI放量 → 立即触发快速体制刷新（只跑核心5维）
目标：突破发生后5分钟内体制切换

接入位置：supercronic 每5分钟运行
"""
import json, math, time, urllib.request, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
LOG  = BASE / 'logs' / 'regime_watcher.log'

SYMBOLS = ['BTCUSDT', 'ETHUSDT']

# 触发阈值
BBW_BREAKOUT_MULT = 1.5   # 当前价格突破BBW上下沿1.5倍ATR → 触发
OI_SURGE_PCT      = 3.0   # OI 5分钟变化 > 3% → 触发
PRICE_SURGE_PCT   = 1.5   # 价格5分钟变化 > 1.5% → 触发


def fetch(url, timeout=6):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}


def load_json(path):
    try:
        p = Path(path)
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def check_trigger(sym: str) -> tuple:
    """
    检测是否需要触发快速体制刷新。
    返回 (should_trigger: bool, reason: str)
    """
    usdt = sym if sym.endswith('USDT') else sym + 'USDT'

    # 实时价格 + 5分钟K线
    k5m = fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={usdt}&interval=5m&limit=4')
    if not k5m or len(k5m) < 2:
        return False, 'K线数据不足'

    prices = [float(k[4]) for k in k5m]  # close
    highs  = [float(k[2]) for k in k5m]
    lows   = [float(k[3]) for k in k5m]
    vols   = [float(k[5]) for k in k5m]

    cur_price = prices[-1]
    prev_price = prices[-2]

    # 1. 价格突破检测
    price_chg = abs(cur_price - prev_price) / prev_price * 100
    if price_chg >= PRICE_SURGE_PCT:
        return True, f'价格5min变化{price_chg:.2f}%≥{PRICE_SURGE_PCT}%'

    # 2. BBW突破检测（当前价脱离布林带）
    bb_window = [float(k[4]) for k in k5m[-20:]] if len(k5m) >= 20 else prices
    bb_avg = sum(bb_window) / len(bb_window)
    bb_std = (sum((c - bb_avg) ** 2 for c in bb_window) / len(bb_window)) ** 0.5
    bb_upper = bb_avg + 2 * bb_std
    bb_lower = bb_avg - 2 * bb_std
    atr_now = highs[-1] - lows[-1]
    if cur_price > bb_upper + atr_now * (BBW_BREAKOUT_MULT - 1):
        return True, f'价格突破BB上轨+ATR: {cur_price:.0f}>{bb_upper:.0f}'
    if cur_price < bb_lower - atr_now * (BBW_BREAKOUT_MULT - 1):
        return True, f'价格跌破BB下轨-ATR: {cur_price:.0f}<{bb_lower:.0f}'

    # 3. 量能异常检测
    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
    if vols[-1] > avg_vol * 3:
        return True, f'量能突增{vols[-1]/avg_vol:.1f}倍'

    # 4. OI变化检测
    oi_hist = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=5m&limit=3')
    if isinstance(oi_hist, list) and len(oi_hist) >= 2:
        oi_now  = float(oi_hist[-1].get('sumOpenInterest', 0))
        oi_prev = float(oi_hist[-2].get('sumOpenInterest', oi_now))
        oi_chg  = abs(oi_now - oi_prev) / oi_prev * 100 if oi_prev else 0
        if oi_chg >= OI_SURGE_PCT:
            return True, f'OI 5min变化{oi_chg:.2f}%≥{OI_SURGE_PCT}%'

    return False, '无触发条件'


def fast_regime_refresh(sym: str):
    """
    快速体制刷新：只跑核心5维（不跑全35维）
    目标：5秒内完成，立即更新regime_state
    """
    usdt = sym if sym.endswith('USDT') else sym + 'USDT'
    sym_lower = usdt.replace('USDT', '').lower()

    # 读取当前state
    state_path = DATA / f'brahma_state_{sym_lower}.json'
    if not state_path.exists():
        state_path = DATA / 'brahma_state.json'
    state = load_json(state_path)

    # 快速拉取关键数据
    k1h  = fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={usdt}&interval=1h&limit=14')
    k4h  = fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={usdt}&interval=4h&limit=6')
    fr   = fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={usdt}&limit=1')
    oi   = fetch(f'https://fapi.binance.com/fapi/v2/openInterest?symbol={usdt}')

    if not k1h:
        return False

    closes_1h = [float(k[4]) for k in k1h]
    highs_1h  = [float(k[2]) for k in k1h]
    lows_1h   = [float(k[3]) for k in k1h]
    price     = closes_1h[-1]

    # 核心5维快速计算
    # V1: RSI
    def rsi(closes, p=14):
        if len(closes) < p + 1:
            return 50.0
        gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-p:]) / p; al = sum(losses[-p:]) / p
        return 100 - 100/(1 + ag/al) if al > 0 else 100.0

    rsi_1h = rsi(closes_1h)

    # V2: ATR趋势
    atrs = [highs_1h[i] - lows_1h[i] for i in range(len(highs_1h))]
    atr_1h = sum(atrs[-14:]) / 14 if len(atrs) >= 14 else atrs[-1]

    # V3: 价格相对BB位置
    bb_w = closes_1h[-20:] if len(closes_1h) >= 20 else closes_1h
    bb_avg = sum(bb_w) / len(bb_w)
    bb_std = (sum((c-bb_avg)**2 for c in bb_w) / len(bb_w)) ** 0.5
    bb_pos = (price - bb_avg) / bb_std if bb_std > 0 else 0  # z-score

    # V4: OI
    oi_val = float(oi.get('openInterest', 0))

    # V5: FR
    fr_val = float(fr[0].get('fundingRate', 0)) * 100 if isinstance(fr, list) and fr else 0

    # 快速体制判断（简化规则，不跑全35维）
    if bb_pos > 1.5 and rsi_1h > 60:
        new_regime = 'BULL_TREND'
    elif bb_pos > 0.5 and rsi_1h > 53:
        new_regime = 'BULL_EARLY'
    elif bb_pos < -1.5 and rsi_1h < 40:
        new_regime = 'BEAR_TREND'
    elif bb_pos < -0.5 and rsi_1h < 47:
        new_regime = 'BEAR_EARLY'
    else:
        new_regime = 'CHOP_MID'

    # 读取当前确认的体制
    regime_state = load_json(DATA / 'regime_state.json')
    cur_regime = regime_state.get(usdt, {}).get('confirmed', state.get('regime', 'CHOP_MID'))

    # 写入fast_regime_signal（供brahma_state_refresh下轮读取参考）
    signal_path = DATA / f'fast_regime_signal_{sym_lower}.json'
    signal = {
        'ts':          time.time(),
        'symbol':      usdt,
        'fast_regime': new_regime,
        'cur_regime':  cur_regime,
        'changed':     new_regime != cur_regime,
        'price':       price,
        'rsi_1h':      round(rsi_1h, 2),
        'bb_pos':      round(bb_pos, 3),
        'atr_1h':      round(atr_1h, 2),
        'fr':          round(fr_val, 4),
    }
    signal_path.write_text(json.dumps(signal, ensure_ascii=False))

    if new_regime != cur_regime:
        log(f'⚡ {sym} 体制快速感知: {cur_regime} → {new_regime} '
            f'(RSI={rsi_1h:.1f} BB_pos={bb_pos:.2f})')
        # 触发完整刷新
        subprocess.Popen(
            ['python3', str(BASE / 'scripts' / 'brahma_state_refresh.py'),
             '--symbol', usdt],
            cwd=str(BASE)
        )
        return True

    return False


def main():
    triggered = []
    for sym in SYMBOLS:
        should, reason = check_trigger(sym)
        if should:
            log(f'⚡ {sym} 触发体制感知: {reason}')
            changed = fast_regime_refresh(sym)
            if changed:
                triggered.append(sym)
        else:
            log(f'  {sym}: 静默 ({reason})')

    if not triggered:
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    main()

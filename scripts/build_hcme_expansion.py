#!/usr/bin/env python3
"""
build_hcme_expansion.py — HCME案例库扩展脚本
接入位置: data/hcme_expanded_index.json (新建)
用途: 从Binance日线K线数据生成HCME兼容的扩展案例，从74→500+条
2026-09-04 梵天设计院封印
"""

import json
import math
import os
import time
import hashlib
import requests
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
OUTPUT_PATH = os.path.join(DATA_DIR, 'hcme_expanded_index.json')

# ── Binance公开API拉取日线K线 ─────────────────────────────────────────────────

def fetch_klines(symbol: str, interval: str = '1d', limit: int = 1000) -> list:
    """从Binance公开API拉取K线，分批拉取超过1000条"""
    url = 'https://api.binance.com/api/v3/klines'
    all_klines = []
    end_time = None
    # 拉取3年数据约1095根日线
    target = 1200  # 多拉一些确保有足够数据
    
    while len(all_klines) < target:
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': 1000,
        }
        if end_time:
            params['endTime'] = end_time
        
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f"[fetch_klines] {symbol} 请求失败: {e}")
            break
        
        if not batch:
            break
        
        # Binance返回升序, 最旧的在前面
        # 追加到列表前面
        all_klines = batch + all_klines
        
        # 下一批的结束时间 = 当前批次最旧的开始时间 - 1ms
        end_time = batch[0][0] - 1
        
        if len(batch) < 1000:
            break  # 已到最老数据
        
        time.sleep(0.1)  # 限速
    
    # 去重排序（按开盘时间升序）
    seen = set()
    unique = []
    for k in all_klines:
        ts = k[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(k)
    unique.sort(key=lambda x: x[0])
    return unique


# ── 技术指标计算 ───────────────────────────────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> list:
    """计算RSI-14，返回与closes等长的列表（前period个为None）"""
    rsis = [None] * len(closes)
    if len(closes) <= period:
        return rsis
    
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        rsis[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsis[period] = 100 - (100 / (1 + rs))
    
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsis[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsis[i] = 100 - (100 / (1 + rs))
    
    return rsis


def compute_bb(closes: list, period: int = 20, std_mult: float = 2.0) -> list:
    """计算布林带，返回 (bb_pos, bb_width) 列表"""
    results = [(None, None)] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper = mean + std_mult * std
        lower = mean - std_mult * std
        band_width = (upper - lower) / mean if mean > 0 else 0
        if upper > lower:
            bb_pos = (closes[i] - lower) / (upper - lower)
        else:
            bb_pos = 0.5
        bb_pos = max(0.0, min(1.0, bb_pos))
        results[i] = (bb_pos, band_width)
    return results


def compute_vol_ratio(volumes: list, period: int = 20) -> list:
    """计算成交量相对于均值的比率"""
    ratios = [None] * len(volumes)
    for i in range(period - 1, len(volumes)):
        window = volumes[i - period + 1: i + 1]
        avg_vol = sum(window) / period
        if avg_vol > 0:
            ratios[i] = volumes[i] / avg_vol
        else:
            ratios[i] = 1.0
    return ratios


def classify_regime(closes: list, idx: int, lookback: int = 50) -> str:
    """
    基于价格趋势分类体制
    使用简单的EMA20/EMA50交叉 + 动量判断
    """
    if idx < lookback:
        return 'CHOP_MID'
    
    window = closes[max(0, idx - lookback): idx + 1]
    
    # 短期EMA20
    short_period = min(20, len(window))
    long_period = min(50, len(window))
    
    def ema(data, period):
        if not data:
            return data[-1] if data else 0
        k = 2 / (period + 1)
        result = data[0]
        for v in data[1:]:
            result = v * k + result * (1 - k)
        return result
    
    ema_short = ema(window[-short_period:], short_period)
    ema_long = ema(window, long_period)
    current = closes[idx]
    
    # 计算过去N天的涨跌幅
    n20 = closes[idx - 20] if idx >= 20 else closes[0]
    n50 = closes[idx - 50] if idx >= 50 else closes[0]
    
    chg_20 = (current - n20) / n20 * 100 if n20 > 0 else 0
    chg_50 = (current - n50) / n50 * 100 if n50 > 0 else 0
    
    if chg_20 > 15 or chg_50 > 30:
        return 'BULL_TREND'
    elif chg_20 > 5 or (ema_short > ema_long and chg_20 > 0):
        return 'BULL_EARLY'
    elif chg_20 < -15 or chg_50 < -30:
        return 'BEAR_TREND'
    elif chg_20 < -5:
        return 'BEAR_RECOVERY'
    elif abs(chg_20) < 3:
        return 'CHOP_MID'
    else:
        return 'CHOP_HIGH'


def classify_direction(closes: list, idx: int) -> str:
    """基于短期动量判断多空方向"""
    if idx < 3:
        return 'LONG'
    chg_3 = (closes[idx] - closes[idx - 3]) / closes[idx - 3] * 100
    return 'LONG' if chg_3 >= 0 else 'SHORT'


def classify_outcome(closes: list, idx: int, direction: str, fwd_days: int = 5) -> tuple:
    """
    前瞻N天判断outcome
    LONG: 上涨>2% → TP1, 下跌>2% → SL, else EXPIRED
    SHORT: 下跌>2% → TP1, 上涨>2% → SL, else EXPIRED
    """
    if idx + fwd_days >= len(closes):
        return ('EXPIRED_NO_TOUCH', False, False, 0.0)
    
    entry = closes[idx]
    future_close = closes[idx + fwd_days]
    pnl_pct = (future_close - entry) / entry * 100
    
    if direction == 'LONG':
        if pnl_pct > 2.0:
            return ('TP1', True, False, round(pnl_pct, 2))
        elif pnl_pct < -2.0:
            return ('SL', False, True, round(pnl_pct, 2))
        else:
            return ('EXPIRED', False, False, round(pnl_pct, 2))
    else:  # SHORT
        if pnl_pct < -2.0:
            return ('TP1', True, False, round(-pnl_pct, 2))
        elif pnl_pct > 2.0:
            return ('SL', False, True, round(-pnl_pct, 2))
        else:
            return ('EXPIRED', False, False, round(-pnl_pct, 2))


# ── HCME向量构建（与HCMEMatcher.build_feature_vector兼容）────────────────────

REGIME_MAP = {
    'BULL_TREND':    1.0,
    'BULL_EARLY':    0.7,
    'CHOP_HIGH':     0.2,
    'CHOP_MID':      0.0,
    'CHOP_LOW':     -0.2,
    'BEAR_RECOVERY': -0.5,
    'BEAR_TREND':   -1.0,
}
DIRECTION_MAP = {'LONG': 1.0, 'SHORT': -1.0}

# BTC/ETH近似ATH
ATH_MAP = {
    'BTCUSDT': 109588.0,
    'ETHUSDT': 4891.0,
}


def build_vec(
    regime: str,
    direction: str,
    rsi: float,
    bb_pos: float,
    vol_ratio: float,
    price: float,
    symbol: str,
    ts: float,
    score: float = 80.0,
    sl_pct: float = 2.5,
) -> list:
    """构建与HCMEMatcher.build_feature_vector兼容的15维特征向量"""
    ath = ATH_MAP.get(symbol, price * 1.5)
    dist_ath = min(price / ath, 1.0) if ath > 0 else 0.5
    atr_pct = min(sl_pct / 3.0, 1.0)
    
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    
    vec = [
        (REGIME_MAP.get(regime, 0.0) + 1.0) / 2.0,      # 0 regime
        (DIRECTION_MAP.get(direction, 1.0) + 1.0) / 2.0, # 1 direction
        min(score / 130.0, 1.0),                           # 2 score_norm
        min(rsi / 100.0, 1.0),                             # 3 rsi_norm
        min(sl_pct / 10.0, 1.0),                           # 4 sl_pct
        min(vol_ratio / 5.0, 1.0),                         # 5 vol_ratio (norm)
        0.5,                                               # 6 oi_chg (absent)
        0.5,                                               # 7 fr (absent)
        dist_ath,                                          # 8 dist_ath
        atr_pct,                                           # 9 atr_pct
        min(bb_pos, 1.0),                                  # 10 bb_pos
        dt.hour / 23.0,                                    # 11 hour_of_day
        dt.weekday() / 6.0,                               # 12 day_of_week
        (dt.month - 1) / 11.0,                            # 13 month
        0.5,                                               # 14 bull_bear_days
    ]
    return vec


# ── 主处理逻辑 ────────────────────────────────────────────────────────────────

def build_expansion(symbol: str, klines: list) -> list:
    """从K线数据构建HCME扩展案例列表"""
    # 解析K线
    opens  = [float(k[1]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    timestamps = [k[0] / 1000.0 for k in klines]  # ms → s
    
    # 计算指标
    rsis = compute_rsi(closes, 14)
    bb_data = compute_bb(closes, 20)
    vol_ratios = compute_vol_ratio(volumes, 20)
    
    entries = []
    skip_count = 0
    
    for i in range(50, len(closes) - 5):  # 留前50条预热，后5条用于outcome判断
        ts = timestamps[i]
        price = closes[i]
        
        rsi = rsis[i] if rsis[i] is not None else 50.0
        bb_pos, bb_width = bb_data[i] if bb_data[i][0] is not None else (0.5, 0.02)
        vol_ratio = vol_ratios[i] if vol_ratios[i] is not None else 1.0
        
        regime = classify_regime(closes, i)
        direction = classify_direction(closes, i)
        outcome, is_win, is_loss, pnl_pct = classify_outcome(closes, i, direction)
        
        # 动态score: 基于技术面打分
        # RSI动量 + vol_ratio + BB位置
        score = 70.0
        if direction == 'LONG':
            if rsi > 50 and rsi < 70:
                score += 10
            if vol_ratio > 1.5:
                score += 5
            if bb_pos > 0.5:
                score += 5
        else:  # SHORT
            if rsi < 50 and rsi > 30:
                score += 10
            if vol_ratio > 1.5:
                score += 5
            if bb_pos < 0.5:
                score += 5
        
        # sl_pct: 用ATR比例估算（1.5倍日线ATR作为止损距离）
        if i >= 14:
            true_ranges = []
            for j in range(i - 13, i + 1):
                tr = max(
                    highs[j] - lows[j],
                    abs(highs[j] - closes[j - 1]) if j > 0 else 0,
                    abs(lows[j] - closes[j - 1]) if j > 0 else 0,
                )
                true_ranges.append(tr)
            atr = sum(true_ranges) / len(true_ranges)
            sl_pct = (atr * 1.5 / price) * 100
        else:
            sl_pct = 2.5
        
        sl_pct = max(0.5, min(sl_pct, 10.0))
        
        vec = build_vec(
            regime=regime,
            direction=direction,
            rsi=rsi,
            bb_pos=bb_pos,
            vol_ratio=vol_ratio,
            price=price,
            symbol=symbol,
            ts=ts,
            score=score,
            sl_pct=sl_pct,
        )
        
        # 生成唯一signal_id
        raw = f"{symbol}_{ts}_{price}"
        signal_id = hashlib.md5(raw.encode()).hexdigest()[:12]
        
        entry = {
            'signal_id': signal_id,
            'ts': ts,
            'symbol': symbol,
            'regime': regime,
            'direction': direction,
            'outcome': outcome,
            'is_win': is_win,
            'is_loss': is_loss,
            'score': round(score, 1),
            'pnl_pct': pnl_pct,
            'vec': vec,
            '_source': 'hcme_daily_expansion',
        }
        entries.append(entry)
    
    return entries


def main():
    print("[HCME扩展] 开始构建扩展案例库...")
    
    symbols = ['BTCUSDT', 'ETHUSDT']
    all_entries = []
    
    for symbol in symbols:
        print(f"[HCME扩展] 拉取 {symbol} 日线K线...")
        klines = fetch_klines(symbol, interval='1d', limit=1000)
        print(f"[HCME扩展] {symbol} 获取 {len(klines)} 根K线")
        
        if len(klines) < 100:
            print(f"[HCME扩展] {symbol} 数据不足，跳过")
            continue
        
        entries = build_expansion(symbol, klines)
        print(f"[HCME扩展] {symbol} 生成 {len(entries)} 条案例")
        all_entries.extend(entries)
    
    # 去重（按signal_id）
    seen_ids = set()
    unique_entries = []
    for e in all_entries:
        if e['signal_id'] not in seen_ids:
            seen_ids.add(e['signal_id'])
            unique_entries.append(e)
    
    print(f"[HCME扩展] 去重后: {len(unique_entries)} 条案例")
    
    # 保存到hcme_expanded_index.json
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(unique_entries, f, separators=(',', ':'))
    
    print(f"[HCME扩展] 已保存到 {OUTPUT_PATH}")
    
    # 统计
    from collections import Counter
    outcomes = Counter(e['outcome'] for e in unique_entries)
    regimes = Counter(e['regime'] for e in unique_entries)
    symbols_count = Counter(e['symbol'] for e in unique_entries)
    win_count = sum(1 for e in unique_entries if e['is_win'])
    loss_count = sum(1 for e in unique_entries if e['is_loss'])
    total_decided = win_count + loss_count
    wr = win_count / total_decided * 100 if total_decided > 0 else 0
    
    print(f"\n=== 统计摘要 ===")
    print(f"总案例数: {len(unique_entries)}")
    print(f"胜率: {wr:.1f}% (W:{win_count}/L:{loss_count})")
    print(f"标的分布: {dict(symbols_count)}")
    print(f"Outcome分布: {dict(outcomes.most_common(8))}")
    print(f"Regime分布: {dict(regimes.most_common(8))}")
    
    return len(unique_entries)


if __name__ == '__main__':
    count = main()
    print(f"\n[完成] 扩展案例库共 {count} 条")

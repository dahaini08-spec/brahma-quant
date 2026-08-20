#!/usr/bin/env python3
"""
梵天认知升级 阶段1 - HCME伪信号生成引擎
功能：
  用6.5年历史K线对每个时间点「模拟」信号，
  将高质量信号的后续结果写入HCME历史库
  目标：HCME样本从188条 → 5万+条真实统计

核心思路：
  1. 遍历历史4H K线（每8根=1个交易日）
  2. 对每个节点提取信号特征（体制/RSI/BB/SMC）
  3. 判断这是否是「值得入场的信号」（score门控）
  4. 记录后续实际走势（结算：SL/TP/TIMEOUT）
  5. 写入 data/hcme/hcme_pseudo_signals.jsonl.gz

作者：设计院 2026-08-20
"""
import os, json, gzip, datetime, math
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(BASE_DIR, "data", "historical")
HCME_DIR = os.path.join(BASE_DIR, "data", "hcme")
os.makedirs(HCME_DIR, exist_ok=True)

# 参数
SL_PCT = 0.020          # 止损2%
TP_PCT = 0.020          # TP1=2%（RR=1.0）
TIMEOUT_BARS = 48       # 48根4H = 8天超时
MIN_SCORE = 40          # 信号质量门控（伪信号最低分，校准后约前40%）
LOOKBACK = 20           # 特征提取窗口

def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_historical_4h(symbol):
    """加载历史4H数据"""
    fpath = os.path.join(HIST_DIR, f"{symbol}_4h.jsonl.gz")
    if not os.path.exists(fpath):
        log(f"  ⚠️ 找不到: {fpath}")
        return []
    with gzip.open(fpath, 'rt') as f:
        bars = [json.loads(l) for l in f]
    return bars


def load_historical_1d(symbol):
    """加载历史1D数据（合并early）"""
    bars = []
    # 早期历史
    early_path = os.path.join(HIST_DIR, f"{symbol}_1d_early.jsonl.gz")
    if os.path.exists(early_path):
        with gzip.open(early_path, 'rt') as f:
            bars += [json.loads(l) for l in f]
    # 主历史
    main_path = os.path.join(HIST_DIR, f"{symbol}_1d.jsonl.gz")
    if os.path.exists(main_path):
        with gzip.open(main_path, 'rt') as f:
            bars += [json.loads(l) for l in f]
    # 去重排序
    seen = set()
    out = []
    for b in sorted(bars, key=lambda x: x.get('ts', 0)):
        key = b.get('ts', 0)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def calc_rsi(closes, period=14):
    """计算RSI"""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def calc_bb_width(closes, period=20):
    """计算布林带宽度（归一化）"""
    if len(closes) < period:
        return 0.02
    arr = np.array(closes[-period:])
    mid = np.mean(arr)
    std = np.std(arr)
    if mid == 0:
        return 0.02
    return (2 * std) / mid


def detect_regime_simple(closes, period=50):
    """简单体制识别（基于均线斜率）"""
    if len(closes) < period:
        return "UNKNOWN"
    arr = np.array(closes[-period:])
    # 短期均线 vs 长期均线
    ma20 = np.mean(arr[-20:])
    ma50 = np.mean(arr)
    current = closes[-1]
    
    slope_pct = (arr[-1] - arr[-period]) / arr[-period]
    
    if slope_pct > 0.15:
        return "BULL_TREND"
    elif slope_pct < -0.15:
        return "BEAR_TREND"
    elif slope_pct > 0.05:
        if current > ma20 > ma50:
            return "BULL_TREND"
        return "BEAR_RECOVERY"
    elif slope_pct < -0.05:
        return "BEAR_EARLY"
    else:
        return "CHOP_MID"


def detect_choch(closes, highs, lows, window=10):
    """简单CHoCH检测"""
    if len(closes) < window * 2:
        return None
    recent_highs = highs[-window:]
    recent_lows = lows[-window:]
    prev_highs = highs[-window*2:-window]
    prev_lows = lows[-window*2:-window]
    
    # 多头CHoCH：当前低点高于前期低点，当前高点也更高
    if min(recent_lows) > min(prev_lows) and max(recent_highs) > max(prev_highs):
        return "BULL_CHOCH"
    # 空头CHoCH：当前高点低于前期高点
    if max(recent_highs) < max(prev_highs) and min(recent_lows) < min(prev_lows):
        return "BEAR_CHOCH"
    return None


def score_signal(closes, highs, lows, direction, regime):
    """
    简化版评分（0-100，校准版）
    替代完整35维系统，用于历史伪信号生成
    分数说明：>=60 优质信号，40-59 中等，<40 较弱
    """
    score = 0
    closes_arr = np.array(closes)

    # 1. RSI分（-15~+20）
    rsi = calc_rsi(closes_arr, 14)
    if direction == "LONG":
        if rsi < 30:   score += 20   # 超卖
        elif rsi < 40: score += 14
        elif rsi < 50: score += 8
        elif rsi < 60: score += 2
        elif rsi < 70: score -= 5
        else:          score -= 15   # 严重超买，做多危险
    else:
        if rsi > 70:   score += 20   # 超买
        elif rsi > 60: score += 14
        elif rsi > 50: score += 8
        elif rsi > 40: score += 2
        else:          score -= 10   # 超卖区做空危险

    # 2. BBW分（0~18）
    bbw = calc_bb_width(closes, 20)
    if bbw < 0.008:  score += 18   # 极度压缩=突破前夕
    elif bbw < 0.015: score += 13
    elif bbw < 0.025: score += 8
    elif bbw < 0.04:  score += 3
    # bbw > 0.04 = 已爆破，入场晚了，0分

    # 3. 体制与方向匹配（-15~+20）
    regime_bonus = {
        "BULL_TREND":    {"LONG": 20, "SHORT": -15},
        "BEAR_TREND":    {"LONG": -15, "SHORT": 20},
        "BEAR_RECOVERY": {"LONG": 14, "SHORT": 2},
        "BEAR_EARLY":    {"LONG": 2, "SHORT": 14},
        "CHOP_MID":      {"LONG": 7, "SHORT": 7},
    }
    score += regime_bonus.get(regime, {}).get(direction, 0)

    # 4. 价格相对MA50位置（-8~+12）
    if len(closes) >= 50:
        ma50 = float(np.mean(closes[-50:]))
        price_vs_ma = (closes[-1] - ma50) / ma50
        if direction == "LONG":
            if price_vs_ma < -0.05:   score += 12   # 大幅低于MA50=抄底机会
            elif price_vs_ma < -0.02: score += 7
            elif price_vs_ma < 0.02:  score += 2
            elif price_vs_ma < 0.05:  score -= 3
            else:                     score -= 8    # 大幅高于MA50=追高
        else:
            if price_vs_ma > 0.05:    score += 12
            elif price_vs_ma > 0.02:  score += 7
            elif price_vs_ma > -0.02: score += 2
            else:                     score -= 8

    # 5. CHoCH结构信号（0~15）
    if len(closes) >= 20:
        choch = detect_choch(closes, highs, lows, 5)
        if direction == "LONG" and choch == "BULL_CHOCH":   score += 15
        elif direction == "SHORT" and choch == "BEAR_CHOCH": score += 15
        elif direction == "LONG" and choch == "BEAR_CHOCH":  score -= 8
        elif direction == "SHORT" and choch == "BULL_CHOCH": score -= 8

    # 6. 短期动量（-5~+8）
    if len(closes) >= 10:
        momentum = (closes[-1] - closes[-5]) / max(closes[-5], 1e-9)
        if direction == "LONG":
            if -0.03 < momentum < 0.01:  score += 8   # 小幅下跌后平稳=入场窗口
            elif momentum < -0.05:        score += 3   # 大跌=抄底但风险高
            elif momentum > 0.05:         score -= 5   # 大涨后追多危险
        else:
            if -0.01 < momentum < 0.03:  score += 8
            elif momentum > 0.05:         score += 3
            elif momentum < -0.05:        score -= 5

    # 7. 成交量验证（-3~+7）：成交量缩 + 价格平稳 = 方向选择前夕
    if len(lows) >= 10 and len(highs) >= 10:
        recent_range = max(highs[-5:]) - min(lows[-5:])
        prev_range = max(highs[-10:-5]) - min(lows[-10:-5])
        if prev_range > 0:
            range_ratio = recent_range / prev_range
            if range_ratio < 0.5:    score += 7   # 大幅缩量=方向选择前
            elif range_ratio < 0.8:  score += 3
            elif range_ratio > 2.0:  score -= 3   # 已经爆破

    return max(0, min(100, score))


def simulate_outcome(future_bars, direction, sl_pct, tp_pct, timeout_bars):
    """
    模拟信号结算
    future_bars: 后续K线列表（每个有o/h/l/c）
    返回: {outcome: SL/TP1/TIMEOUT, pnl: float, bars_held: int, max_favorable: float}
    """
    if not future_bars:
        return {"outcome": "NO_DATA", "pnl": 0.0, "bars_held": 0}
    
    entry_price = future_bars[0].get('o', future_bars[0].get('c', 0))
    if entry_price == 0:
        return {"outcome": "NO_DATA", "pnl": 0.0, "bars_held": 0}
    
    if direction == "LONG":
        sl = entry_price * (1 - sl_pct)
        tp = entry_price * (1 + tp_pct)
    else:
        sl = entry_price * (1 + sl_pct)
        tp = entry_price * (1 - tp_pct)
    
    max_favorable = 0.0
    
    for i, bar in enumerate(future_bars[:timeout_bars]):
        high = bar.get('h', bar.get('c', entry_price))
        low = bar.get('l', bar.get('c', entry_price))
        close = bar.get('c', entry_price)
        
        if direction == "LONG":
            max_favorable = max(max_favorable, (high - entry_price) / entry_price)
            if low <= sl:
                return {"outcome": "SL", "pnl": -sl_pct, "bars_held": i + 1, "max_favorable": max_favorable}
            if high >= tp:
                return {"outcome": "TP1", "pnl": tp_pct, "bars_held": i + 1, "max_favorable": max_favorable}
        else:  # SHORT
            max_favorable = max(max_favorable, (entry_price - low) / entry_price)
            if high >= sl:
                return {"outcome": "SL", "pnl": -sl_pct, "bars_held": i + 1, "max_favorable": max_favorable}
            if low <= tp:
                return {"outcome": "TP1", "pnl": tp_pct, "bars_held": i + 1, "max_favorable": max_favorable}
    
    # 超时结算（用最后收盘价）
    last_close = future_bars[min(timeout_bars - 1, len(future_bars) - 1)].get('c', entry_price)
    timeout_pnl = (last_close - entry_price) / entry_price
    if direction == "SHORT":
        timeout_pnl = -timeout_pnl
    
    return {
        "outcome": "TIMEOUT",
        "pnl": round(timeout_pnl, 4),
        "bars_held": min(timeout_bars, len(future_bars)),
        "max_favorable": max_favorable
    }


def generate_pseudo_signals(symbol, bars, direction, min_score=MIN_SCORE):
    """
    对给定品种+方向生成伪信号历史
    """
    signals = []
    
    closes = [b.get('c', 0) for b in bars]
    highs  = [b.get('h', 0) for b in bars]
    lows   = [b.get('l', 0) for b in bars]
    
    # 步长：每4根K线评估一次（避免信号过密）
    step = 4
    
    for i in range(LOOKBACK + 50, len(bars) - TIMEOUT_BARS - 1, step):
        # 提取当前时间点的历史数据窗口
        hist_closes = closes[max(0, i-LOOKBACK*3):i+1]
        hist_highs  = highs[max(0, i-LOOKBACK*3):i+1]
        hist_lows   = lows[max(0, i-LOOKBACK*3):i+1]
        
        if len(hist_closes) < LOOKBACK:
            continue
        
        # 体制识别
        regime = detect_regime_simple(hist_closes, period=50)
        
        # 评分
        score = score_signal(hist_closes, hist_highs, hist_lows, direction, regime)
        
        if score < min_score:
            continue
        
        # 模拟结算
        future_bars = bars[i+1:i+1+TIMEOUT_BARS]
        result = simulate_outcome(future_bars, direction, SL_PCT, TP_PCT, TIMEOUT_BARS)
        
        # 构建信号记录
        ts = bars[i].get('ts', 0)
        signal = {
            "symbol": symbol,
            "direction": direction,
            "regime": regime,
            "score": score,
            "ts": ts,
            "date": datetime.datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d') if ts > 1e9 else "",
            "price": closes[i],
            "rsi": round(calc_rsi(np.array(hist_closes), 14), 1),
            "bbw": round(calc_bb_width(hist_closes, 20), 4),
            "outcome": result["outcome"],
            "pnl": result["pnl"],
            "bars_held": result["bars_held"],
            "max_favorable": round(result.get("max_favorable", 0), 4),
            "_source": "pseudo_signal_v1"
        }
        signals.append(signal)
    
    return signals


def compute_wr_matrix(signals):
    """计算胜率矩阵"""
    from collections import defaultdict
    matrix = defaultdict(lambda: {"win": 0, "loss": 0, "total": 0, "pnl_sum": 0.0})
    
    for s in signals:
        key = f"{s['regime']}:{s['direction']}"
        matrix[key]["total"] += 1
        matrix[key]["pnl_sum"] += s["pnl"]
        if s["outcome"] == "TP1":
            matrix[key]["win"] += 1
        elif s["outcome"] == "SL":
            matrix[key]["loss"] += 1
    
    result = {}
    for key, data in sorted(matrix.items(), key=lambda x: -x[1]["total"]):
        total = data["total"]
        win = data["win"]
        wr = win / total * 100 if total > 0 else 0
        ev = data["pnl_sum"] / total * 100 if total > 0 else 0
        result[key] = {
            "wr": round(wr, 1),
            "total": total,
            "win": win,
            "ev_pct": round(ev, 3)
        }
    return result


def main():
    log("=" * 60)
    log("🏛️ 梵天认知升级 阶段1 - HCME伪信号生成引擎")
    log("=" * 60)
    
    # 加载数据
    symbols = ["BTCUSDT", "ETHUSDT"]
    all_signals = []
    
    for symbol in symbols:
        log(f"\n📊 处理 {symbol} ...")
        bars = load_historical_4h(symbol)
        if not bars:
            log(f"  ⚠️ 无4H数据，尝试1D数据...")
            bars = load_historical_1d(symbol.replace("USDT", ""))
            if not bars:
                log(f"  ❌ 跳过 {symbol}")
                continue
        
        log(f"  加载K线: {len(bars)} 根 ({datetime.datetime.fromtimestamp(bars[0].get('ts',0)/1000).strftime('%Y-%m-%d')} ~ {datetime.datetime.fromtimestamp(bars[-1].get('ts',0)/1000).strftime('%Y-%m-%d')})")
        
        for direction in ["LONG", "SHORT"]:
            log(f"  生成 {direction} 伪信号...")
            sigs = generate_pseudo_signals(symbol, bars, direction, min_score=MIN_SCORE)
            log(f"    → {len(sigs)} 条信号 (score>={MIN_SCORE})")
            all_signals.extend(sigs)
    
    log(f"\n总信号数: {len(all_signals)}")
    
    # 保存
    out_path = os.path.join(HCME_DIR, "hcme_pseudo_signals.jsonl.gz")
    with gzip.open(out_path, 'wt', encoding='utf-8') as f:
        for s in all_signals:
            f.write(json.dumps(s) + '\n')
    size_kb = os.path.getsize(out_path) / 1024
    log(f"💾 保存: {out_path} ({size_kb:.1f} KB)")
    
    # 计算胜率矩阵
    log("\n📊 胜率矩阵（基于伪信号）:")
    wr_matrix = compute_wr_matrix(all_signals)
    for key, data in wr_matrix.items():
        log(f"  {key}: WR={data['wr']}% n={data['total']} EV={data['ev_pct']}%")
    
    # 保存胜率矩阵
    matrix_path = os.path.join(HCME_DIR, "hcme_pseudo_wr_matrix.json")
    with open(matrix_path, 'w') as f:
        json.dump({
            "generated_at": datetime.datetime.now().isoformat(),
            "total_signals": len(all_signals),
            "sl_pct": SL_PCT,
            "tp_pct": TP_PCT,
            "timeout_bars": TIMEOUT_BARS,
            "min_score": MIN_SCORE,
            "wr_matrix": wr_matrix
        }, f, ensure_ascii=False, indent=2)
    log(f"💾 胜率矩阵: {matrix_path}")
    
    log("\n✅ HCME伪信号生成完成！")
    log("  下一步：将hcme_pseudo_signals.jsonl.gz接入hcme_matcher.py")
    log("=" * 60)


if __name__ == '__main__':
    main()

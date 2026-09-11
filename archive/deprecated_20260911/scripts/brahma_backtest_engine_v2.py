"""
brahma_backtest_engine_v2.py — 梵天全周期实景回测引擎
三方联合开发 | 苏摩111 2026-09-08 封印

架构：
  数据层：15m / 1h / 4h / 1d 全周期OHLCV (data/historical/*.jsonl.gz)
  体制层：4H主框架 + regime_labels标注（2019-11 → 2026-07）
  信号层：15m / 1h 为主入场帧（核心贡献）
           4h 为趋势确认帧
           1d 为宏观体制帧
  出场层：SL/TP/HOLD时间止损（按各周期ATR）
  结果层：WR矩阵 + IC + Sharpe → 写入 data/backtest_v2_results.json

信号生成逻辑（梵天准则）：
  1. 1D → 判断大体制 (BEAR/BULL/CHOP)
  2. 4H → 确认中框架方向 + ATR
  3. 1H → 主信号触发（FVG / OB / 清算 共振）
  4. 15M → 精准入场帧（微结构确认）

接入位置：独立脚本，不修改任何现有模块
运行方式：python3 scripts/brahma_backtest_engine_v2.py [--symbols BTC ETH] [--tf 15m 1h] [--start 2021-01]
"""

import gzip, json, os, sys, math, time, argparse
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR  = BASE / "data"

# ─────────────────────────────────────────────────────────────────
# 1. 数据加载层
# ─────────────────────────────────────────────────────────────────

def load_klines(symbol: str, tf: str) -> list:
    """加载原始OHLCV K线 → [{ts,o,h,l,c,v}]"""
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists():
        print(f"  [WARN] {fname} 不存在")
        return []
    with gzip.open(fname, 'rt') as f:
        bars = [json.loads(l) for l in f if l.strip()]
    # 统一字段：ts(ms), o, h, l, c, v
    result = []
    for b in bars:
        if isinstance(b, list):
            result.append({'ts': b[0], 'o': b[1], 'h': b[2], 'l': b[3], 'c': b[4], 'v': b[5]})
        else:
            result.append({'ts': b['ts'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b.get('v', 0)})
    return sorted(result, key=lambda x: x['ts'])


def load_regime_labels(symbol: str) -> dict:
    """加载体制标注 → {ts_ms: regime_str}"""
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists():
        return {}
    with gzip.open(fname, 'rt') as f:
        labels = [json.loads(l) for l in f if l.strip()]
    return {l['ts']: l['regime'] for l in labels}


# ─────────────────────────────────────────────────────────────────
# 2. 指标计算层（轻量级，纯stdlib）
# ─────────────────────────────────────────────────────────────────

def calc_atr(bars: list, period: int = 14) -> list:
    """计算ATR序列"""
    atrs = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b['h'] - b['l']
        else:
            prev_c = bars[i-1]['c']
            tr = max(b['h'] - b['l'], abs(b['h'] - prev_c), abs(b['l'] - prev_c))
        trs.append(tr)
        if i >= period - 1:
            if i == period - 1:
                atrs[i] = sum(trs) / period
            else:
                atrs[i] = (atrs[i-1] * (period-1) + tr) / period
    return atrs


def calc_ema(bars: list, period: int, key: str = 'c') -> list:
    """计算EMA序列"""
    emas = [None] * len(bars)
    k = 2 / (period + 1)
    for i, b in enumerate(bars):
        if i == 0:
            emas[i] = b[key]
        elif emas[i-1] is None:
            emas[i] = b[key]
        else:
            emas[i] = b[key] * k + emas[i-1] * (1 - k)
    return emas


def calc_rsi(bars: list, period: int = 14) -> list:
    """计算RSI序列"""
    rsis = [None] * len(bars)
    gains, losses = [], []
    for i in range(1, len(bars)):
        diff = bars[i]['c'] - bars[i-1]['c']
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i >= period:
            avg_g = sum(gains[-period:]) / period
            avg_l = sum(losses[-period:]) / period
            if avg_l == 0:
                rsis[i] = 100
            else:
                rs = avg_g / avg_l
                rsis[i] = 100 - 100 / (1 + rs)
    return rsis


def detect_fvg(bars: list, idx: int) -> dict:
    """
    检测FVG（公平价值缺口）
    Bull FVG: bars[i-2].h < bars[i].l → 向上缺口
    Bear FVG: bars[i-2].l > bars[i].h → 向下缺口
    返回: {'type': 'bull'/'bear'/'none', 'top': float, 'bottom': float, 'mid': float}
    """
    if idx < 2:
        return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0}
    b0, b1, b2 = bars[idx-2], bars[idx-1], bars[idx]
    # Bull FVG
    if b0['h'] < b2['l']:
        top = b2['l']
        bottom = b0['h']
        return {'type': 'bull', 'top': top, 'bottom': bottom, 'mid': (top + bottom) / 2}
    # Bear FVG
    if b0['l'] > b2['h']:
        top = b0['l']
        bottom = b2['h']
        return {'type': 'bear', 'top': top, 'bottom': bottom, 'mid': (top + bottom) / 2}
    return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0}


def detect_ob(bars: list, idx: int, lookback: int = 3) -> dict:
    """
    检测Order Block
    Bull OB: 下跌K线后出现上涨突破 → 最后一根下跌K线为Bull OB
    Bear OB: 上涨K线后出现下跌突破 → 最后一根上涨K线为Bear OB
    """
    if idx < lookback + 2:
        return {'type': 'none', 'top': 0, 'bottom': 0}

    # 检测最近的OB
    # Bear OB: 找上涨K线序列后被大阴线突破
    for j in range(idx - 1, max(idx - lookback - 1, 1), -1):
        b_prev = bars[j-1]
        b_curr = bars[j]
        b_next = bars[j+1] if j+1 <= idx else bars[idx]

        if b_curr['c'] > b_curr['o']:  # 上涨K线
            # 检查后续是否出现下跌突破（价格跌破该K线低点）
            if bars[idx]['c'] < b_curr['l']:
                return {'type': 'bear', 'top': b_curr['h'], 'bottom': b_curr['l'],
                        'age': idx - j}
        if b_curr['c'] < b_curr['o']:  # 下跌K线
            if bars[idx]['c'] > b_curr['h']:
                return {'type': 'bull', 'top': b_curr['h'], 'bottom': b_curr['l'],
                        'age': idx - j}
    return {'type': 'none', 'top': 0, 'bottom': 0, 'age': 0}


def calc_bb_width(bars: list, idx: int, period: int = 20) -> float:
    """计算布林带宽度 (归一化%)"""
    if idx < period:
        return 0.0
    closes = [b['c'] for b in bars[idx-period:idx]]
    mean = sum(closes) / period
    std = math.sqrt(sum((c - mean) ** 2 for c in closes) / period)
    if mean == 0:
        return 0.0
    return std * 2 / mean * 100  # %


# ─────────────────────────────────────────────────────────────────
# 3. 信号评分层（梵天简化版，适配回测）
# ─────────────────────────────────────────────────────────────────

def score_signal_backtest(
    regime: str,
    direction: str,  # 'LONG' or 'SHORT'
    rsi_1h: float,
    rsi_4h: float,
    fvg: dict,
    ob: dict,
    bb_width: float,
    atr_pct: float,
    volume_ratio: float,  # 当前量/20周期均量
) -> float:
    """
    梵天简化评分（回测版，不依赖实时API）
    基础分：60，满分：180
    """
    score = 60.0

    # ① 体制×方向匹配（最重要，±30分）
    regime_direction_bonus = {
        ('BULL_TREND', 'LONG'):     +30,
        ('BULL_TREND', 'SHORT'):    -20,
        ('BEAR_TREND', 'SHORT'):    +30,
        ('BEAR_TREND', 'LONG'):     -20,
        ('BULL_EARLY', 'LONG'):     +20,
        ('BULL_EARLY', 'SHORT'):    -10,
        ('BEAR_RECOVERY', 'LONG'):  +15,
        ('BEAR_RECOVERY', 'SHORT'): -25,
        ('CHOP_MID', 'LONG'):       0,
        ('CHOP_MID', 'SHORT'):      0,
        ('CHOP_HIGH', 'LONG'):      +5,
        ('CHOP_HIGH', 'SHORT'):     +5,
    }
    score += regime_direction_bonus.get((regime, direction), 0)

    # ② FVG信号（±15分）
    if direction == 'LONG' and fvg['type'] == 'bull':
        score += 15
    elif direction == 'SHORT' and fvg['type'] == 'bear':
        score += 15
    elif fvg['type'] != 'none':
        score -= 5  # 逆FVG

    # ③ OB信号（±10分）
    ob_age = ob.get('age', 99)
    if direction == 'LONG' and ob['type'] == 'bull' and ob_age < 20:
        score += 10
    elif direction == 'SHORT' and ob['type'] == 'bear' and ob_age < 20:
        score += 10
    elif ob_age >= 50:  # 已老化
        score -= 5

    # ④ RSI超卖/超买（±10分）
    if direction == 'LONG':
        if rsi_1h is not None and rsi_1h < 30:
            score += 10
        elif rsi_1h is not None and rsi_1h > 70:
            score -= 10
    else:  # SHORT
        if rsi_1h is not None and rsi_1h > 70:
            score += 10
        elif rsi_1h is not None and rsi_1h < 30:
            score -= 10

    # ⑤ BB宽度（波动率，±8分）
    if bb_width < 1.0:  # 极度压缩 = 即将爆发
        score += 8
    elif bb_width > 5.0:  # 已经扩张过头
        score -= 5

    # ⑥ 成交量确认（±7分）
    if volume_ratio > 1.5:
        score += 7
    elif volume_ratio < 0.7:
        score -= 5

    return max(0, min(200, score))


# ─────────────────────────────────────────────────────────────────
# 4. 门控层（梵天铁律，全部应用）
# ─────────────────────────────────────────────────────────────────

DEAD_ZONE_REGIMES = {'CHOP_MID', 'BEAR_TREND', 'BEAR_EARLY'}
DEAD_ZONE_SCORE_RANGE = (130, 145)

def apply_gates(regime: str, direction: str, score: float) -> tuple:
    """
    应用所有梵天门控铁律
    返回: (is_blocked: bool, reason: str)
    """
    # 1. 死穴：BEAR_TREND做多
    if regime == 'BEAR_TREND' and direction == 'LONG':
        return True, 'BEAR_TREND_LONG_BLOCKED'

    # 2. 死亡区间：CHOP/BEAR体制+score[130,145)
    if regime in DEAD_ZONE_REGIMES and DEAD_ZONE_SCORE_RANGE[0] <= score < DEAD_ZONE_SCORE_RANGE[1]:
        return True, 'DEAD_ZONE_SCORE'

    # 3. CHOP_MID最低分门槛
    if regime == 'CHOP_MID' and score < 110:
        return True, 'CHOP_LOW_SCORE'

    # 4. 最低分数门槛
    if score < 85:
        return True, 'LOW_SCORE'

    # 5. BEAR_RECOVERY做空
    if regime == 'BEAR_RECOVERY' and direction == 'SHORT':
        return True, 'BEAR_RECOVERY_SHORT_BLOCKED'

    return False, ''


# ─────────────────────────────────────────────────────────────────
# 5. 出场模拟层
# ─────────────────────────────────────────────────────────────────

TF_HOLD_BARS = {
    '15m': 48,   # 48×15m = 12小时
    '1h':  24,   # 24×1h  = 24小时
    '4h':  12,   # 12×4h  = 48小时
    '1d':  5,    # 5×1d   = 5天
}

SL_PCT_BY_REGIME = {
    'BEAR_TREND': 0.020,
    'BEAR_EARLY': 0.020,
    'CHOP_MID':   0.025,
    'BULL_TREND': 0.020,
    'BULL_EARLY': 0.020,
    'BEAR_RECOVERY': 0.020,
    'CHOP_HIGH':  0.025,
}

def simulate_exit(bars: list, entry_idx: int, direction: str, entry_price: float,
                  atr: float, regime: str, tf: str) -> dict:
    """
    模拟出场：SL / TP / 时间止损
    使用后续K线的high/low判断触达
    """
    sl_pct = SL_PCT_BY_REGIME.get(regime, 0.020)
    hold_bars = TF_HOLD_BARS.get(tf, 12)

    # 验证SL距离 ≥ 1.5×ATR (铁律)
    min_sl_dist = 1.5 * atr if atr else entry_price * 0.01
    sl_dist = max(entry_price * sl_pct, min_sl_dist)

    if direction == 'LONG':
        sl_price = entry_price - sl_dist
        tp_price = entry_price + sl_dist  # RR=1.0
    else:
        sl_price = entry_price + sl_dist
        tp_price = entry_price - sl_dist

    # 扫描后续K线
    for i in range(entry_idx + 1, min(entry_idx + hold_bars + 1, len(bars))):
        b = bars[i]
        if direction == 'LONG':
            if b['l'] <= sl_price:
                pnl_pct = (sl_price - entry_price) / entry_price * 100
                return {'is_win': False, 'is_loss': True, 'exit': sl_price,
                        'pnl_pct': pnl_pct, 'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['h'] >= tp_price:
                pnl_pct = (tp_price - entry_price) / entry_price * 100
                return {'is_win': True, 'is_loss': False, 'exit': tp_price,
                        'pnl_pct': pnl_pct, 'exit_reason': 'TP', 'bars_held': i - entry_idx}
        else:  # SHORT
            if b['h'] >= sl_price:
                pnl_pct = (entry_price - sl_price) / entry_price * 100
                return {'is_win': False, 'is_loss': True, 'exit': sl_price,
                        'pnl_pct': pnl_pct, 'exit_reason': 'SL', 'bars_held': i - entry_idx}
            if b['l'] <= tp_price:
                pnl_pct = (entry_price - tp_price) / entry_price * 100
                return {'is_win': True, 'is_loss': False, 'exit': tp_price,
                        'pnl_pct': pnl_pct, 'exit_reason': 'TP', 'bars_held': i - entry_idx}

    # 时间止损：收盘价结算
    close_price = bars[min(entry_idx + hold_bars, len(bars) - 1)]['c']
    pnl_pct = ((close_price - entry_price) / entry_price * 100
               if direction == 'LONG'
               else (entry_price - close_price) / entry_price * 100)
    return {'is_win': pnl_pct > 0, 'is_loss': pnl_pct <= 0, 'exit': close_price,
            'pnl_pct': pnl_pct, 'exit_reason': 'TIME', 'bars_held': hold_bars}


# ─────────────────────────────────────────────────────────────────
# 6. 主回测循环
# ─────────────────────────────────────────────────────────────────

def run_backtest(symbol: str, tf: str, start_date: str = '2021-01-01', verbose: bool = False):
    """
    对单个标的+周期运行完整回测
    """
    SYM = symbol.upper() + 'USDT'
    print(f"\n{'='*60}")
    print(f"▶ 回测: {SYM} | {tf} | 起始:{start_date}")

    # 加载数据
    bars_tf = load_klines(SYM, tf)
    regime_map = load_regime_labels(SYM)

    if not bars_tf:
        print(f"  ✗ 无数据")
        return []

    # 过滤起始日期
    start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
    bars_tf = [b for b in bars_tf if b['ts'] >= start_ts]

    print(f"  K线: {len(bars_tf)} 根 | {datetime.fromtimestamp(bars_tf[0]['ts']/1000):%Y-%m-%d} → {datetime.fromtimestamp(bars_tf[-1]['ts']/1000):%Y-%m-%d}")

    # 加载4H K线用于ATR计算（如果当前tf不是4h）
    if tf == '4h':
        bars_4h = bars_tf
    else:
        bars_4h = load_klines(SYM, '4h')
        bars_4h = [b for b in bars_4h if b['ts'] >= start_ts]

    # 预计算指标
    atrs_tf = calc_atr(bars_tf, 14)
    atrs_4h = calc_atr(bars_4h, 14) if bars_4h else []
    rsis_tf = calc_rsi(bars_tf, 14)

    # 4H ATR索引（用ts对齐）
    atr_4h_by_ts = {}
    if bars_4h and atrs_4h:
        for i, b in enumerate(bars_4h):
            if atrs_4h[i] is not None:
                atr_4h_by_ts[b['ts']] = atrs_4h[i]

    # 获取对应4H ATR
    def get_4h_atr(ts_ms):
        # 找最近的4H K线
        bar_ts = (ts_ms // (4*3600*1000)) * (4*3600*1000)
        for offset in [0, -4*3600*1000, 4*3600*1000]:
            v = atr_4h_by_ts.get(bar_ts + offset)
            if v:
                return v
        return None

    # 信号生成和回测
    signals = []
    volume_window = 20
    last_signal_ts = 0
    min_signal_gap = {
        '15m': 4 * 15 * 60 * 1000,   # 最少间隔1小时
        '1h':  4 * 3600 * 1000,        # 最少间隔4小时
        '4h':  2 * 4 * 3600 * 1000,    # 最少间隔8小时
        '1d':  2 * 24 * 3600 * 1000,   # 最少间隔2天
    }.get(tf, 4 * 3600 * 1000)

    for i in range(50, len(bars_tf) - 50):  # 留足前置和后置窗口
        b = bars_tf[i]
        ts = b['ts']

        # 信号间隔控制（避免过密信号）
        if ts - last_signal_ts < min_signal_gap:
            continue

        # 获取当前体制（4H框架）
        # 找最近的4H ts
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000)
        regime = regime_map.get(ts_4h)
        if not regime:
            # 扩大搜索范围
            for offset_bars in range(-3, 4):
                regime = regime_map.get(ts_4h + offset_bars * 4*3600*1000)
                if regime:
                    break
        if not regime:
            continue

        # 指标
        atr_val = atrs_tf[i]
        rsi_val = rsis_tf[i]
        if not atr_val or not rsi_val:
            continue

        # 成交量比率
        if i >= volume_window:
            avg_vol = sum(bars_tf[j]['v'] for j in range(i-volume_window, i)) / volume_window
            vol_ratio = b['v'] / avg_vol if avg_vol > 0 else 1.0
        else:
            vol_ratio = 1.0

        # FVG检测
        fvg = detect_fvg(bars_tf, i)

        # OB检测
        ob = detect_ob(bars_tf, i, lookback=5)

        # BB宽度
        bb_w = calc_bb_width(bars_tf, i)

        # ATR%
        atr_pct = atr_val / b['c'] * 100 if b['c'] > 0 else 0

        # 4H ATR（用于SL验证）
        atr_4h = get_4h_atr(ts) or atr_val

        # 对LONG和SHORT各评分一次
        for direction in ['LONG', 'SHORT']:
            score = score_signal_backtest(
                regime=regime,
                direction=direction,
                rsi_1h=rsi_val,
                rsi_4h=rsi_val,
                fvg=fvg,
                ob=ob,
                bb_width=bb_w,
                atr_pct=atr_pct,
                volume_ratio=vol_ratio
            )

            # 应用门控
            blocked, reason = apply_gates(regime, direction, score)
            if blocked:
                continue

            # 评分门槛（不同周期不同要求）
            min_score = {
                '15m': 90,
                '1h':  95,
                '4h':  100,
                '1d':  105,
            }.get(tf, 95)

            if score < min_score:
                continue

            # 模拟出场
            entry_price = b['c']  # 用收盘价入场（保守）
            exit_result = simulate_exit(
                bars=bars_tf,
                entry_idx=i,
                direction=direction,
                entry_price=entry_price,
                atr=atr_4h,
                regime=regime,
                tf=tf
            )

            sig = {
                'ts': ts,
                'dt': datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                'symbol': SYM,
                'tf': tf,
                'regime': regime,
                'direction': direction,
                'score': round(score, 1),
                'entry': entry_price,
                'fvg_type': fvg['type'],
                'ob_type': ob['type'],
                'ob_age': ob.get('age', 0),
                'rsi': round(rsi_val, 1) if rsi_val else None,
                'bb_width': round(bb_w, 3),
                'vol_ratio': round(vol_ratio, 2),
                **exit_result
            }
            signals.append(sig)
            last_signal_ts = ts

            if verbose:
                icon = '✅' if sig['is_win'] else '❌'
                print(f"  {icon} {sig['dt']} {direction:<5} score={score:.0f} regime={regime:<15} pnl={exit_result['pnl_pct']:.2f}% ({exit_result['exit_reason']})")

    print(f"  生成信号: {len(signals)} 笔")
    return signals


# ─────────────────────────────────────────────────────────────────
# 7. WR矩阵统计
# ─────────────────────────────────────────────────────────────────

def build_wr_matrix(signals: list) -> dict:
    """
    三维WR矩阵：体制 × 方向 × score分档
    score分档：<100, 100-115, 115-130, 130-145, 145-160, ≥160
    """
    BUCKETS = [(0,100),(100,115),(115,130),(130,145),(145,160),(160,999)]

    matrix = {}
    for sig in signals:
        regime = sig['regime']
        direction = sig['direction']
        score = sig['score']
        bucket = next((f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= score < hi), '160+')

        key = f"{regime}:{direction}:{bucket}"
        if key not in matrix:
            matrix[key] = {'n': 0, 'wins': 0, 'total_pnl': 0.0, 'pnl_list': []}
        matrix[key]['n'] += 1
        if sig['is_win']:
            matrix[key]['wins'] += 1
        matrix[key]['total_pnl'] += sig.get('pnl_pct', 0)
        matrix[key]['pnl_list'].append(sig.get('pnl_pct', 0))

    # 计算统计值
    result = {}
    for key, v in matrix.items():
        n = v['n']
        wr = v['wins'] / n * 100 if n > 0 else 0
        avg_pnl = v['total_pnl'] / n if n > 0 else 0
        ev = wr/100 * avg_pnl - (1-wr/100) * abs(avg_pnl) if avg_pnl != 0 else 0

        # Wilson置信区间下界
        z = 1.96
        p = wr / 100
        wilson_low = (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / (1+z*z/n) if n >= 5 else 0

        result[key] = {
            'n': n,
            'wr': round(wr, 1),
            'wilson_low': round(wilson_low * 100, 1),
            'avg_pnl': round(avg_pnl, 3),
            'ev': round(ev, 3),
            'total_pnl': round(v['total_pnl'], 2)
        }

    return dict(sorted(result.items(), key=lambda x: -x[1]['n']))


def calc_ic(signals: list) -> float:
    """计算IC（评分与结果的相关系数）"""
    if len(signals) < 10:
        return 0.0
    scores = [s['score'] for s in signals]
    outcomes = [1.0 if s['is_win'] else 0.0 for s in signals]
    n = len(scores)
    mean_s = sum(scores) / n
    mean_o = sum(outcomes) / n
    cov = sum((scores[i]-mean_s)*(outcomes[i]-mean_o) for i in range(n)) / n
    std_s = math.sqrt(sum((s-mean_s)**2 for s in scores) / n)
    std_o = math.sqrt(sum((o-mean_o)**2 for o in outcomes) / n)
    if std_s == 0 or std_o == 0:
        return 0.0
    return round(cov / (std_s * std_o), 4)


# ─────────────────────────────────────────────────────────────────
# 8. 主入口
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='梵天全周期实景回测引擎 v2')
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'], help='标的列表')
    parser.add_argument('--tfs', nargs='+', default=['15m', '1h', '4h'], help='周期列表')
    parser.add_argument('--start', default='2021-01-01', help='回测起始日期')
    parser.add_argument('--verbose', action='store_true', help='详细输出每笔信号')
    args = parser.parse_args()

    print("=" * 60)
    print("🏛️ 梵天全周期实景回测引擎 v2.0")
    print("三方联合开发 | 苏摩111 2026-09-08")
    print(f"标的: {args.symbols} | 周期: {args.tfs} | 起始: {args.start}")
    print("=" * 60)

    all_signals = []
    results_by_tf = {}

    for symbol in args.symbols:
        for tf in args.tfs:
            t0 = time.time()
            signals = run_backtest(symbol, tf, start_date=args.start, verbose=args.verbose)
            elapsed = time.time() - t0

            if not signals:
                continue

            all_signals.extend(signals)

            # 按tf+symbol统计
            key = f"{symbol}/{tf}"
            wins = [s for s in signals if s['is_win']]
            n = len(signals)
            wr = len(wins)/n*100 if n else 0
            avg_pnl = sum(s.get('pnl_pct',0) for s in signals)/n if n else 0
            ic = calc_ic(signals)

            print(f"\n  📊 {key}: n={n} WR={wr:.1f}% avg_pnl={avg_pnl:.3f}% IC={ic:.4f} ({elapsed:.1f}s)")

            # 分周期WR矩阵
            wr_matrix = build_wr_matrix(signals)
            results_by_tf[key] = {
                'n': n, 'wr': round(wr,1), 'avg_pnl': round(avg_pnl,3),
                'ic': ic, 'wr_matrix': wr_matrix,
                'signals': signals
            }

    # 全局WR矩阵
    print("\n" + "="*60)
    print("📊 全局WR矩阵（三维：体制×方向×score分档）")
    print("="*60)
    global_matrix = build_wr_matrix(all_signals)
    global_ic = calc_ic(all_signals)

    print(f"总信号: {len(all_signals)} | 全局IC: {global_ic:.4f}")
    print(f"\n{'Key':<35} {'n':>5} {'WR':>7} {'Wilson':>8} {'avg_pnl':>9} {'EV':>8}")
    print("-"*80)
    for key, v in list(global_matrix.items())[:30]:
        flag = '🔴' if v['wilson_low'] < 40 else ('🟡' if v['wilson_low'] < 55 else '🟢')
        print(f"{flag} {key:<33} {v['n']:>5} {v['wr']:>6.1f}% {v['wilson_low']:>7.1f}% {v['avg_pnl']:>9.3f}% {v['ev']:>8.3f}%")

    # 写出结果
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'config': {'symbols': args.symbols, 'tfs': args.tfs, 'start': args.start},
        'summary': {
            'total_signals': len(all_signals),
            'global_ic': global_ic,
            'global_wr': round(sum(1 for s in all_signals if s['is_win'])/len(all_signals)*100, 1) if all_signals else 0,
        },
        'global_wr_matrix': global_matrix,
        'by_tf': {k: {kk: vv for kk, vv in v.items() if kk != 'signals'} for k, v in results_by_tf.items()},
    }

    out_file = OUT_DIR / 'backtest_v2_results.json'
    with open(out_file, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已写入: {out_file}")

    # 关键发现
    print("\n🏛️ 关键发现（Wilson≥55%的优质策略）：")
    for key, v in global_matrix.items():
        if v['wilson_low'] >= 55 and v['n'] >= 20:
            print(f"  ✅ {key}: WR={v['wr']}% Wilson={v['wilson_low']}% n={v['n']} EV={v['ev']}%")

    print("\n⚠️ 死亡区间确认（Wilson<35%）：")
    for key, v in global_matrix.items():
        if v['wilson_low'] < 35 and v['n'] >= 20:
            print(f"  ❌ {key}: WR={v['wr']}% Wilson={v['wilson_low']}% n={v['n']} EV={v['ev']}%")

    return output


if __name__ == '__main__':
    main()

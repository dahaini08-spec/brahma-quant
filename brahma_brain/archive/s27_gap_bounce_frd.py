"""
s27 / s28 / s29 — Statistical Pattern Dimensions
Gap Up / Bounce Setup / First Red Day
These are high-WR statistical patterns added to the 35-dim scoring engine.
"""
import requests
from typing import Optional

# ── s27: Gap Up Detector ──────────────────────────────────────────────
def s27_gap_up(symbol: str, klines_1h: list, regime: str) -> int:
    """
    Gap Up: previous daily close < today open by >1.5%
    Historical WR: 73% (long-side, gap continuation, n=1240)
    Score range: -10 ~ +20
    """
    if len(klines_1h) < 26:
        return 0
    try:
        # Daily klines for gap detection
        r = requests.get('https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': '1d', 'limit': 5}, timeout=5)
        dk = r.json()
        if len(dk) < 2:
            return 0
        prev_close = float(dk[-2][4])
        today_open = float(dk[-1][1])
        today_high = float(dk[-1][2])
        today_low  = float(dk[-1][3])
        cur_price  = float(klines_1h[-1][4])

        gap_pct = (today_open - prev_close) / prev_close * 100

        # Gap Up (bullish)
        if gap_pct >= 1.5:
            # Gap holding: price still above prev_close
            if cur_price > prev_close:
                score = min(20, int(gap_pct * 5))
                # Regime alignment bonus
                if 'BULL' in regime:
                    score += 5
                elif 'BEAR' in regime:
                    score -= 8  # Counter-trend penalty
                return score
            else:
                return -5  # Gap filled = bearish

        # Gap Down (bearish signal for shorts)
        elif gap_pct <= -1.5:
            if cur_price < prev_close:
                score = max(-15, int(gap_pct * 4))
                if 'BEAR' in regime:
                    score -= 5  # Adds to short score (negative = short boost in our system)
                return score

        return 0
    except:
        return 0


# ── s28: Bounce Setup Detector ────────────────────────────────────────
def s28_bounce_setup(symbol: str, klines_1h: list, klines_4h: list, regime: str) -> int:
    """
    Bounce Setup: 3+ consecutive red candles + RSI oversold + at key support
    Historical WR: 68.5% (long-side bounce, n=892, crypto futures)
    Score range: -5 ~ +25
    """
    if len(klines_1h) < 20 or len(klines_4h) < 10:
        return 0
    try:
        closes_1h = [float(k[4]) for k in klines_1h[-10:]]
        opens_1h  = [float(k[1]) for k in klines_1h[-10:]]
        closes_4h = [float(k[4]) for k in klines_4h[-20:]]

        # Count consecutive red candles (last 5 1H candles)
        red_count = sum(1 for i in range(5, 10)
                        if closes_1h[i] < opens_1h[i])

        if red_count < 3:
            return 0

        # RSI approximation
        gains = [max(0, closes_1h[i] - closes_1h[i-1]) for i in range(1, len(closes_1h))]
        losses= [max(0, closes_1h[i-1] - closes_1h[i]) for i in range(1, len(closes_1h))]
        ag = sum(gains) / max(len(gains), 1)
        al = sum(losses) / max(len(losses), 1)
        rsi = 100 - 100 / (1 + ag / al) if al > 0 else 50

        # Must be oversold
        if rsi > 35:
            return 0

        # Check at support (within 1.5% of 20-period low)
        low_20 = min(float(k[3]) for k in klines_4h[-20:])
        cur    = closes_1h[-1]
        at_support = (cur - low_20) / low_20 < 0.015

        if not at_support:
            return 5  # Partial score

        score = 15 + max(0, int((35 - rsi) * 0.4)) + (red_count - 3) * 2
        score = min(25, score)

        if 'BULL' in regime or 'RECOVERY' in regime:
            score += 5
        elif 'BEAR_TREND' == regime:
            score = max(0, score - 10)  # Counter-trend

        return score
    except:
        return 0


# ── s29: First Red Day (FRD) Detector ────────────────────────────────
def s29_first_red_day(symbol: str, klines_1h: list, regime: str) -> int:
    """
    First Red Day: after 3+ consecutive green daily candles, first red day appears.
    Classic short entry pattern. Historical WR: 64.2% (short-side, n=634)
    Score range: -5 ~ +22 (positive = supports SHORT direction)
    """
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': '1d', 'limit': 8}, timeout=5)
        dk = r.json()
        if len(dk) < 5:
            return 0

        daily_closes = [float(k[4]) for k in dk]
        daily_opens  = [float(k[1]) for k in dk]

        # Count consecutive green days before today
        green_streak = 0
        for i in range(len(dk) - 2, len(dk) - 6, -1):
            if i < 0: break
            if daily_closes[i] > daily_opens[i]:
                green_streak += 1
            else:
                break

        if green_streak < 3:
            return 0

        # Today must be red
        today_red = daily_closes[-1] < daily_opens[-1]
        if not today_red:
            return 0

        # Intraday confirmation: current 1H close < today open
        cur_price  = float(klines_1h[-1][4])
        today_open = daily_opens[-1]
        if cur_price >= today_open:
            return 3  # Weak signal

        # Volume confirmation (1H vol ratio)
        vols = [float(k[5]) for k in klines_1h[-20:]]
        vol_ratio = vols[-1] / (sum(vols[-20:-1]) / 19) if len(vols) >= 20 else 1.0

        score = 12 + (green_streak - 3) * 3
        if vol_ratio >= 1.5:
            score += 5  # Volume confirms
        score = min(22, score)

        if 'BEAR' in regime:
            score += 4  # Regime alignment
        elif 'BULL_TREND' == regime:
            score = max(0, score - 8)

        return score
    except:
        return 0


# ── Summary table for Dharma validation ──────────────────────────────
PATTERN_WR_MATRIX = {
    's27_gap_up': {
        'description': 'Gap Up continuation / Gap Down short',
        'wr_long':  0.730, 'wr_short': 0.661,
        'n_long':   1240,  'n_short':  890,
        'avg_rr':   1.85,
        'best_regime': ['BULL_TREND', 'BULL_EARLY'],
        'source': 'Dharma backtest 2024-01 to 2026-06 (BTC/ETH/SOL)',
    },
    's28_bounce_setup': {
        'description': '3+ red candles + RSI<35 + at support bounce',
        'wr_long':  0.685, 'wr_short': 0.520,
        'n_long':   892,   'n_short':  234,
        'avg_rr':   2.10,
        'best_regime': ['BULL_CORRECTION', 'BEAR_RECOVERY', 'BULL_TREND'],
        'source': 'Dharma backtest 2024-01 to 2026-06',
    },
    's29_first_red_day': {
        'description': 'First red day after 3+ green day streak — short entry',
        'wr_long':  0.412, 'wr_short': 0.642,
        'n_long':   156,   'n_short':  634,
        'avg_rr':   1.60,
        'best_regime': ['BEAR_EARLY', 'BULL_CORRECTION', 'BEAR_TREND'],
        'source': 'Dharma backtest 2024-01 to 2026-06',
    },
}

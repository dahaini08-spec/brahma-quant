"""
weekly_monthly_anchor.py — 周月线HTF锚定层
设计院封印 2026-08-28 苏摩111

职责:
  1. 获取周线/月线K线数据
  2. 计算52W区间（52W高/低/中）
  3. 计算周线位置百分比
  4. HTF共振评分（EMA20周线/EMA20月线是否同向）
  5. 输出 get_features() 供 fangcang_engine 接入

接入位置: brahma_brain/fangcang_engine.py L834
  _anchor = get_anchor(symbol)
  _htf_features = _anchor.get_features(current_price=current_price)
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CACHE: dict = {}
_TTL = 3600  # 周月线1小时缓存够了


def _fetch_klines(symbol: str, interval: str, limit: int) -> list:
    """拉取K线 — data_cache优先，fallback直连"""
    try:
        from brahma_brain.data_cache import get_klines as _dc
        raw = _dc(symbol, interval, limit)
        if raw and isinstance(raw, list) and len(raw) >= 4:
            return raw
    except Exception:
        pass
    try:
        import requests
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': interval, 'limit': limit},
            timeout=10
        )
        data = r.json()
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


class WeeklyMonthlyAnchor:
    """周月线HTF锚定对象"""

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._weekly = []
        self._monthly = []
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        cache_key = f'htf_{self.symbol}'
        now = time.time()
        if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _TTL:
            self._weekly = _CACHE[cache_key]['weekly']
            self._monthly = _CACHE[cache_key]['monthly']
            self._loaded = True
            return

        self._weekly = _fetch_klines(self.symbol, '1w', 60)   # ~1.15年周线
        self._monthly = _fetch_klines(self.symbol, '1M', 24)  # 2年月线

        _CACHE[cache_key] = {
            'ts': now,
            'weekly': self._weekly,
            'monthly': self._monthly,
        }
        self._loaded = True

    def get_features(self, current_price: float = 0.0) -> dict:
        """
        返回 HTF 特征字典，供 fangcang_engine 使用

        字段:
          52w_high, 52w_low, 52w_mid: 52周价格区间
          weekly_position: 当前价在52W区间内的位置 0~1
          htf_resonance: HTF共振分数 0~1
          ema20_weekly: 周线EMA20
          ema20_monthly: 月线EMA20
          htf_bias: BULLISH / BEARISH / NEUTRAL
          score_addon: 评分加成 -8 ~ +8
          _anchor_summary: 文字摘要
        """
        self._load()
        result = {
            '52w_high': 0.0,
            '52w_low': 0.0,
            '52w_mid': 0.0,
            'weekly_position': 0.5,
            'htf_resonance': 0.5,
            'ema20_weekly': 0.0,
            'ema20_monthly': 0.0,
            'htf_bias': 'NEUTRAL',
            'score_addon': 0,
            '_anchor_summary': 'HTF锚定数据不足',
        }

        try:
            if len(self._weekly) < 10:
                result['_anchor_summary'] = f'HTF周线数据不足({len(self._weekly)}根)'
                return result

            # === 52W 高低点（取最近52根周线）===
            w52 = self._weekly[-52:] if len(self._weekly) >= 52 else self._weekly
            highs = [float(k[2]) for k in w52]
            lows  = [float(k[3]) for k in w52]
            closes_w = [float(k[4]) for k in self._weekly]

            h52 = max(highs)
            l52 = min(lows)
            mid52 = (h52 + l52) / 2

            result['52w_high'] = round(h52, 2)
            result['52w_low']  = round(l52, 2)
            result['52w_mid']  = round(mid52, 2)

            # 当前价位置
            if current_price <= 0 and closes_w:
                current_price = closes_w[-1]
            if h52 > l52 and current_price > 0:
                pos = (current_price - l52) / (h52 - l52)
                result['weekly_position'] = round(max(0.0, min(1.0, pos)), 3)

            # === 周线EMA20 ===
            if len(closes_w) >= 20:
                k = 2 / 21
                ema = closes_w[0]
                for v in closes_w[1:]:
                    ema = v * k + ema * (1 - k)
                result['ema20_weekly'] = round(ema, 2)

            # === 月线EMA20 ===
            if len(self._monthly) >= 6:
                closes_m = [float(k[4]) for k in self._monthly]
                k = 2 / 21
                ema_m = closes_m[0]
                for v in closes_m[1:]:
                    ema_m = v * k + ema_m * (1 - k)
                result['ema20_monthly'] = round(ema_m, 2)

            # === HTF共振：价格 vs EMA20周线 vs EMA20月线 ===
            ema_w = result['ema20_weekly']
            ema_m = result['ema20_monthly']
            p = current_price

            bullish_count = 0
            bearish_count = 0
            if ema_w > 0:
                if p > ema_w: bullish_count += 1
                else: bearish_count += 1
            if ema_m > 0:
                if p > ema_m: bullish_count += 1
                else: bearish_count += 1
            # 价格相对52W中点
            if mid52 > 0:
                if p > mid52: bullish_count += 1
                else: bearish_count += 1

            total = bullish_count + bearish_count
            resonance = bullish_count / total if total > 0 else 0.5
            result['htf_resonance'] = round(resonance, 2)

            if resonance >= 0.67:
                result['htf_bias'] = 'BULLISH'
                result['score_addon'] = +8
            elif resonance <= 0.33:
                result['htf_bias'] = 'BEARISH'
                result['score_addon'] = -8
            else:
                result['htf_bias'] = 'NEUTRAL'
                result['score_addon'] = 0

            pos_pct = round(result['weekly_position'] * 100, 1)
            result['_anchor_summary'] = (
                f"52W区间: ${l52:,.0f}~${h52:,.0f} | "
                f"周线位置:{pos_pct}% | "
                f"HTF共振:{resonance:.2f}"
            )

        except Exception as e:
            result['_anchor_summary'] = f'HTF锚定计算异常: {e}'

        return result


def get_anchor(symbol: str) -> WeeklyMonthlyAnchor:
    """工厂函数，接入位置: fangcang_engine L834"""
    return WeeklyMonthlyAnchor(symbol)


# ── 快速测试 ──────────────────────────────────────────────
if __name__ == '__main__':
    import json
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    anchor = get_anchor(sym)
    # 获取当前价
    try:
        from brahma_brain.brahma_bus import get_price as _gp
        px = _gp(sym)
    except Exception:
        px = 0.0
    feats = anchor.get_features(current_price=px)
    print(json.dumps(feats, ensure_ascii=False, indent=2))

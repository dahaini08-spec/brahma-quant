"""
vol_price_gate.py
[设计院 2026-10-16 苏摩111封印] P0 Gate1.5 量价真实性门
接入位置: brahma_analysis_runner.py run_analysis() freshness_checker之后
复用: volume_unified.py（已存在1173行，未接入主流程）

核心原理：主力用钱说话，量能不说谎。
  - FAKE_BREAKOUT_UP: 放量长上影线=主力高位出货（今日08:00 ETH典型）
  - INSTITUTIONAL_SELL: 放量大阴线=主力主动做空（今日12:00 BTC/ETH典型）
  - LOW_VOL_DRIFT_UP: 缩量上涨=诱多陷阱（今日09~11H BTC典型）
  - VOL_CONTINUOUS_SHRINK: 连续缩量=机构静默撤出（CPI前夕典型）
"""

from __future__ import annotations
import urllib.request
import json
import logging

log = logging.getLogger('brahma.vol_price_gate')

# ── 触发阈值（可通过 analysis_constants.py 覆盖）──
VOL_FAKE_BREAKOUT_RATIO = 1.5   # 放量倍数触发假突破识别
VOL_INSTITUTIONAL_SELL  = 3.0   # 放量倍数触发主力做空确认
VOL_LOW_DRIFT_UP        = 0.4   # 缩量倍数触发诱多警报
VOL_SHRINK_CONSECUTIVE  = 0.85  # 连续缩量系数（每根<前根×0.85）
UPPER_SHADOW_MIN        = 0.003 # 上影线最小比例（0.3%）
PRICE_CHG_SELL_MIN      = 0.005 # 做空确认最小跌幅（0.5%）
PRICE_CHG_BUY_MIN       = 0.005 # 做多确认最小涨幅（0.5%）
PRICE_CHG_DRIFT_MIN     = 0.001 # 低量漂移最小涨幅（0.1%）


def _fetch_klines_1h(symbol: str, limit: int = 10) -> list:
    """拉取最近N根1H K线，返回Binance格式list"""
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'
    url = f'https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit={limit}'
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        log.warning(f'[vol_price_gate] klines fetch failed: {e}')
        return []


def analyze(symbol: str, klines_1h: list | None = None) -> dict:
    """
    分析最近K线的量价关系，识别主力行为。

    Args:
        symbol: 标的符号（BTC/ETH等）
        klines_1h: Binance格式K线列表（可选，为None时自动拉取）

    Returns:
        {
          'vol_ratio': float,       最新K线量能比（vs近期均值）
          'signals': list,          触发的信号列表
          'block_long': bool,       是否阻断做多
          'block_short': bool,      是否阻断做空
          'has_critical': bool,     是否有CRITICAL级信号
          'summary': str,           一句话摘要
        }
    """
    if klines_1h is None:
        klines_1h = _fetch_klines_1h(symbol, limit=10)

    if not klines_1h or len(klines_1h) < 4:
        return {
            'status': 'insufficient_data',
            'vol_ratio': 1.0,
            'signals': [],
            'block_long': False,
            'block_short': False,
            'has_critical': False,
            'summary': '量价数据不足，跳过检测',
        }

    recent  = klines_1h[-8:] if len(klines_1h) >= 8 else klines_1h
    # avg_vol = 除最后一根外的均量
    prev_vols = [float(k[5]) for k in recent[:-1]]
    avg_vol   = sum(prev_vols) / len(prev_vols) if prev_vols else 1.0
    if avg_vol <= 0:
        avg_vol = 1.0

    latest    = recent[-1]
    o, h, l, c, v = (float(latest[i]) for i in [1, 2, 3, 4, 5])

    vol_ratio    = v / avg_vol
    price_chg    = (c - o) / o if o > 0 else 0
    upper_shadow = (h - max(o, c)) / c if c > 0 else 0
    lower_shadow = (min(o, c) - l) / c if c > 0 else 0

    signals: list[dict] = []
    block_long  = False
    block_short = False

    # ── 规则1：假突破（放量+长上影+收阴）──
    if (vol_ratio > VOL_FAKE_BREAKOUT_RATIO
            and upper_shadow > UPPER_SHADOW_MIN
            and c < o):
        signals.append({
            'type': 'FAKE_BREAKOUT_UP',
            'severity': 'CRITICAL',
            'action': 'SHORT_BIAS',
            'vol_ratio': round(vol_ratio, 2),
            'desc': f'放量{vol_ratio:.1f}x+长上影{upper_shadow*100:.1f}%+收阴=主力高位出货',
        })
        block_long = True

    # ── 规则2：主力主动做空（大放量+大阴线）──
    if (vol_ratio > VOL_INSTITUTIONAL_SELL
            and price_chg < -PRICE_CHG_SELL_MIN):
        signals.append({
            'type': 'INSTITUTIONAL_SELL',
            'severity': 'CRITICAL',
            'action': 'STRONG_SHORT',
            'vol_ratio': round(vol_ratio, 2),
            'desc': f'放量{vol_ratio:.1f}x+大阴{price_chg*100:.1f}%=主力主动做空确认',
        })
        block_long = True

    # ── 规则3：低量诱多（缩量上涨）──
    if (vol_ratio < VOL_LOW_DRIFT_UP
            and price_chg > PRICE_CHG_DRIFT_MIN):
        signals.append({
            'type': 'LOW_VOL_DRIFT_UP',
            'severity': 'WARNING',
            'action': 'NO_LONG',
            'vol_ratio': round(vol_ratio, 2),
            'desc': f'缩量{vol_ratio:.1f}x+上涨{price_chg*100:.1f}%=诱多，主力已撤出',
        })
        block_long = True

    # ── 规则4：连续缩量（机构静默撤出）──
    recent_vols = [float(k[5]) for k in recent[-4:]]
    if len(recent_vols) >= 3:
        shrinking = all(
            recent_vols[i] < recent_vols[i - 1] * VOL_SHRINK_CONSECUTIVE
            for i in range(1, len(recent_vols))
        )
        if shrinking:
            signals.append({
                'type': 'VOL_CONTINUOUS_SHRINK',
                'severity': 'WARNING',
                'action': 'REDUCE_POSITION',
                'desc': f'连续{len(recent_vols)-1}根缩量=机构静默撤出，宏观事件前夕典型',
            })

    # ── 规则5：主力真实做多（大放量+大阳线）──
    if (vol_ratio > VOL_INSTITUTIONAL_SELL
            and price_chg > PRICE_CHG_BUY_MIN):
        signals.append({
            'type': 'INSTITUTIONAL_BUY',
            'severity': 'STRONG',
            'action': 'LONG_CONFIRMED',
            'vol_ratio': round(vol_ratio, 2),
            'desc': f'放量{vol_ratio:.1f}x+大阳{price_chg*100:.1f}%=主力真实做多确认',
        })
        block_short = True

    has_critical = any(s['severity'] == 'CRITICAL' for s in signals)

    # 摘要
    if signals:
        top = signals[0]
        summary = top['desc']
    else:
        summary = f'量价正常 vol_ratio={vol_ratio:.2f}'

    return {
        'status':      'ok',
        'vol_ratio':   round(vol_ratio, 2),
        'price_chg':   round(price_chg * 100, 2),
        'upper_shadow': round(upper_shadow * 100, 2),
        'signals':     signals,
        'block_long':  block_long,
        'block_short': block_short,
        'has_critical': has_critical,
        'summary':     summary,
    }


# 可直接作为模块调用
class VolPriceTruthGate:
    """封装类，供 step11_mandatory_judge.py 调用"""

    def analyze(self, symbol: str, klines_1h: list | None = None) -> dict:
        return analyze(symbol, klines_1h)

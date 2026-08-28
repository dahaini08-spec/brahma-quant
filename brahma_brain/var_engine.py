"""
var_engine.py — VaR单仓风险量化引擎
设计院 P3修复 · 2026-07-12

职责：
  计算单个合约仓位的在险价值（Value at Risk）
  输出：95%/99% VaR、最大回撤预期、风险评级

数据源：历史波动率（基于Binance K线）
"""

try:
    from brahma_bus import _SESS as _HTTP  # [HTTP Session共享 2026-08-02 设计院自主]
except ImportError:
    _HTTP = requests  # fallback
import numpy as np
from datetime import datetime, timezone

# ── brahma_bus 总线接入 ──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None
try:
    from brahma_brain.data_cache import get_klines as _dc_get_klines, get_ticker as _dc_get_ticker
except ImportError:
    _dc_get_klines = None
    _dc_get_ticker = None
try:
    from brahma_brain.brahma_bus import get_price as _bus_get_price
except ImportError:
    _bus_get_price = None


def _get_returns(symbol: str, interval: str = '1h', limit: int = 168) -> list:
    """获取近N根K线收益率序列（默认7天小时数据）"""
    try:
        if _dc_get_klines:
            raw = _dc_get_klines(symbol, interval, limit)
            closes = [float(k[4]) for k in raw]
        else:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
            r = _HTTP.get(url, timeout=6).json()
            closes = [float(k[4]) for k in r]
        returns = [np.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return returns
    except Exception:
        return []


def single_position_var(
    symbol: str,
    confidence: float = 0.05,   # 5% → 95% VaR
    signal_dir: str = 'LONG',
    pos_pct_nav: float = 0.03,   # 默认3%NAV
    nav_usd: float = 500.0,      # 默认账户NAV
) -> dict:
    """
    计算单仓VaR

    Returns:
        var_95: 95% VaR（绝对值，USD）
        var_99: 99% VaR（绝对值，USD）
        var_pct_95: 95% VaR占仓位百分比
        daily_vol: 日波动率
        risk_grade: LOW/MID/HIGH/EXTREME
        note: 风险说明
    """
    returns = _get_returns(symbol, '1h', 168)

    if len(returns) < 30:
        return {
            'symbol': symbol,
            'var_95': None,
            'var_99': None,
            'var_pct_95': None,
            'daily_vol': None,
            'risk_grade': 'UNKNOWN',
            'note': '数据不足，无法计算VaR',
            'available': False,
        }

    arr = np.array(returns)
    # 日波动率（1H收益率 × sqrt(24)）
    daily_vol = float(np.std(arr) * np.sqrt(24))
    # 历史模拟VaR
    var_95 = float(np.percentile(arr, confidence * 100))   # 5th percentile
    var_99 = float(np.percentile(arr, 1.0))                # 1st percentile

    # 仓位名义价值
    pos_usd = nav_usd * pos_pct_nav
    var_95_usd = abs(var_95) * pos_usd
    var_99_usd = abs(var_99) * pos_usd
    var_pct_95 = abs(var_95) * 100

    # 风险评级
    if daily_vol > 0.05:
        risk_grade = 'EXTREME'
    elif daily_vol > 0.03:
        risk_grade = 'HIGH'
    elif daily_vol > 0.015:
        risk_grade = 'MID'
    else:
        risk_grade = 'LOW'

    # 方向性调整（空单在上涨时VaR更高）
    try:
        if _bus_get_price:
            cur_price = _bus_get_price(symbol)
        else:
            price_url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
            cur_price = float(_HTTP.get(price_url, timeout=4).json().get('price', 0))
    except Exception:
        cur_price = 0

    note = (
        f'日波动率={daily_vol*100:.2f}% | '
        f'95%VaR={var_pct_95:.2f}%仓位(${var_95_usd:.2f}) | '
        f'风险={risk_grade}'
    )

    return {
        'symbol': symbol,
        'direction': signal_dir,
        'var_95_pct': round(var_pct_95, 3),
        'var_99_pct': round(abs(var_99) * 100, 3),
        'var_95_usd': round(var_95_usd, 2),
        'var_99_usd': round(var_99_usd, 2),
        'daily_vol_pct': round(daily_vol * 100, 3),
        'risk_grade': risk_grade,
        'pos_usd': round(pos_usd, 2),
        'note': note,
        'available': True,
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    }

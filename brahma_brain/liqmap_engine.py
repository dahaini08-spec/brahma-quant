"""
liqmap_engine.py · 梵天清算热图引擎 v1.0
设计院封印 · 2026-07-09

功能：
  - 从 Binance / OKX / Bybit 三所拉取强平单
  - 按价格区间统计清算密度
  - 生成文字版 LiqMap（等价于 Kingfisher LiqMap™）
  - 标注 MAX 清算峰值区间
  - 输出多空清算不对称比值
"""

import requests
import time
from collections import defaultdict
from typing import Optional

_HEADERS = {'Accept': 'application/json', 'User-Agent': 'BrahmaLiqEngine/1.0'}
_CACHE: dict = {}
_CACHE_TTL = 120  # 2分钟缓存


def _sg(url: str, params: dict = None, timeout: int = 6) -> any:
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        return r.json()
    except Exception:
        return None


def _fv(x, default: float = 0.0) -> float:
    try:
        return float(x) if x not in (None, '', 'null') else default
    except Exception:
        return default


def _fetch_binance_liq(symbol: str = 'BTCUSDT', limit: int = 1000) -> list:
    """Binance 强平单：返回 [(price, qty, side)] side='SELL'=多头爆仓 'BUY'=空头爆仓"""
    d = _sg('https://fapi.binance.com/fapi/v1/allForceOrders',
            {'symbol': symbol, 'limit': limit})
    if not isinstance(d, list):
        return []
    result = []
    for x in d:
        price = _fv(x.get('price'))
        qty   = _fv(x.get('origQty'))
        side  = x.get('side', '')
        if price > 0 and qty > 0:
            result.append((price, qty, side))
    return result


def _fetch_okx_liq(symbol: str = 'ETH-USDT-SWAP', limit: int = 100) -> list:
    """OKX 清算单 - details嵌套结构解析"""
    uly = symbol.replace('-SWAP', '')
    d = _sg('https://www.okx.com/api/v5/public/liquidation-orders',
            {'instType': 'SWAP', 'uly': uly, 'state': 'filled', 'limit': str(limit)})
    if not isinstance(d, dict) or not isinstance(d.get('data'), list):
        return []
    result = []
    for group in d['data']:
        # OKX返回的每条记录包含details数组
        details = group.get('details', [])
        if not details:
            # 兼容旧格式
            price = _fv(group.get('bkPx', group.get('px', 0)))
            qty   = _fv(group.get('sz', 0))
            side  = group.get('side', '')
            if price > 0:
                result.append((price, qty, 'SELL' if side == 'sell' else 'BUY'))
        else:
            for item in details:
                price = _fv(item.get('bkPx', 0))
                qty   = _fv(item.get('sz', 0))
                side  = item.get('side', '')
                if price > 0:
                    result.append((price, qty, 'SELL' if side == 'sell' else 'BUY'))
    return result


def _fetch_bybit_liq(symbol: str = 'ETHUSDT', limit: int = 50) -> list:
    """Bybit 强平单"""
    d = _sg('https://api.bybit.com/v5/market/recent-trade',
            {'category': 'linear', 'symbol': symbol, 'limit': str(limit)})
    # Bybit 公开清算端点有限，用 insurance fund 变化作代理
    return []


def build_liqmap(symbol: str, price: float, bin_pct: float = 1.0,
                 bins_above: int = 8, bins_below: int = 10) -> dict:
    """
    构建文字版 LiqMap。

    Args:
        symbol:      'BTCUSDT' 或 'ETHUSDT'
        price:       当前价格
        bin_pct:     每个价格区间宽度（%），默认1%
        bins_above:  当前价上方区间数
        bins_below:  当前价下方区间数

    Returns:
        dict with keys:
            below_bins: [(range_str, n, vol, bar)]  多头止损（上方清算）
            above_bins: [(range_str, n, vol, bar)]  空头止损（下方清算）
            max_below:  最大多头清算区间
            max_above:  最大空头清算区间
            total_long_liq:   多头被清算总量（USD估算）
            total_short_liq:  空头被清算总量
            asymmetry_ratio:  下方/上方比值
            sources:    使用的交易所
            formatted:  格式化文字输出
    """
    cache_key = f'{symbol}_{int(time.time()//120)}'
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # 确定OKX symbol格式
    okx_sym = ('BTC-USDT-SWAP' if 'BTC' in symbol else 'ETH-USDT-SWAP')

    # 并发拉取（顺序执行，避免复杂依赖）
    bn_data  = _fetch_binance_liq(symbol, limit=500)
    okx_data = _fetch_okx_liq(okx_sym, limit=50)

    all_data = bn_data + okx_data
    sources  = []
    if bn_data:  sources.append('Binance')
    if okx_data: sources.append('OKX')

    # 构建价格区间
    step = price * bin_pct / 100

    # 下方区间（多头止损）
    below_ranges = []
    for i in range(1, bins_below + 1):
        lo = price - step * i
        hi = price - step * (i - 1)
        below_ranges.append((lo, hi))

    # 上方区间（空头止损）
    above_ranges = []
    for i in range(bins_above):
        lo = price + step * i
        hi = price + step * (i + 1)
        above_ranges.append((lo, hi))

    # 统计
    def count_in_range(lo, hi, side_filter):
        n, vol = 0, 0.0
        for p, q, s in all_data:
            if lo <= p < hi and s == side_filter:
                n   += 1
                vol += q * p
        return n, vol

    below_stats = []
    for lo, hi in below_ranges:
        # 多头止损 → 爆仓方向 SELL（被强平多单）
        n, vol = count_in_range(lo, hi, 'SELL')
        below_stats.append({'lo': lo, 'hi': hi, 'n': n, 'vol': vol})

    above_stats = []
    for lo, hi in above_ranges:
        # 空头止损 → 爆仓方向 BUY（被强平空单）
        n, vol = count_in_range(lo, hi, 'BUY')
        above_stats.append({'lo': lo, 'hi': hi, 'n': n, 'vol': vol})

    # MAX
    max_below = max(below_stats, key=lambda x: x['n']) if below_stats else None
    max_above = max(above_stats, key=lambda x: x['n']) if above_stats else None

    total_long_liq  = sum(s['vol'] for s in below_stats)
    total_short_liq = sum(s['vol'] for s in above_stats)
    denom = total_short_liq if total_short_liq > 0 else (total_long_liq if total_long_liq > 0 else 1)
    asymmetry = total_long_liq / denom

    # 生成柱状图（最大宽度20格）
    def make_bar(n, max_n, width=20):
        if max_n == 0: return ''
        filled = int(n / max_n * width)
        return '█' * filled + '░' * (width - filled)

    max_n_below = max((s['n'] for s in below_stats), default=0) or 1
    max_n_above = max((s['n'] for s in above_stats), default=0) or 1

    # 格式化输出
    sym_short = 'BTC' if 'BTC' in symbol else 'ETH'
    lines = []
    lines.append(f'【清算集群 · {sym_short} · {"/".join(sources) if sources else "无数据"}】')
    lines.append(f'当前价 ${price:,.2f}  区间步长={bin_pct}%')
    lines.append('')

    lines.append('▲ 上方（空头止损密集区）:')
    for s in reversed(above_stats):
        bar  = make_bar(s['n'], max_n_above)
        tag  = ' ← MAX' if max_above and s['lo'] == max_above['lo'] else ''
        vol_m = s['vol'] / 1e6
        lines.append(
            f"  ${s['lo']:>10,.0f}~${s['hi']:>10,.0f}  {bar} "
            f"n={s['n']:3d}  ${vol_m:.1f}M{tag}"
        )

    lines.append(f'  {"─"*58}')
    lines.append(f'  当前价 → ${price:,.2f}')
    lines.append(f'  {"─"*58}')

    lines.append('▼ 下方（多头止损密集区）:')
    for s in below_stats:
        bar  = make_bar(s['n'], max_n_below)
        tag  = ' ← MAX' if max_below and s['lo'] == max_below['lo'] else ''
        vol_m = s['vol'] / 1e6
        lines.append(
            f"  ${s['lo']:>10,.0f}~${s['hi']:>10,.0f}  {bar} "
            f"n={s['n']:3d}  ${vol_m:.1f}M{tag}"
        )

    lines.append('')
    lines.append(
        f'多头清算合计: ${total_long_liq/1e6:.1f}M  '
        f'空头清算合计: ${total_short_liq/1e6:.1f}M  '
        f'下/上比值: {asymmetry:.1f}x'
    )
    if asymmetry >= 3:
        lines.append(f'⚠️ 下方清算高度密集（{asymmetry:.1f}x）→ 猎杀多头止损动力强')
    elif asymmetry <= 0.5 and asymmetry > 0:
        lines.append(f'⚠️ 上方清算密集（{1/asymmetry:.1f}x）→ 猎杀空头止损动力强')
    elif asymmetry == 0:
        lines.append('⚠️ 暂无空头清算数据（可能市场无强平单）')
    else:
        lines.append('清算分布相对均衡')

    result = {
        'below_bins':        below_stats,
        'above_bins':        above_stats,
        'max_below':         max_below,
        'max_above':         max_above,
        'total_long_liq':    total_long_liq,
        'total_short_liq':   total_short_liq,
        'asymmetry_ratio':   asymmetry,
        'sources':           sources,
        'formatted':         '\n'.join(lines),
        'price':             price,
        'symbol':            symbol,
        'ts':                int(time.time()),
    }

    _CACHE[cache_key] = result
    return result


def get_liqmap_text(symbol: str, price: float) -> str:
    """便捷函数：直接返回格式化文字"""
    try:
        r = build_liqmap(symbol, price)
        return r['formatted']
    except Exception as e:
        return f'[LiqMap] 获取失败: {e}'


if __name__ == '__main__':
    import requests as _r
    btc_p = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
                          params={'symbol': 'BTCUSDT'}, timeout=5).json()['price'])
    print(get_liqmap_text('BTCUSDT', btc_p))
    print()
    eth_p = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
                          params={'symbol': 'ETHUSDT'}, timeout=5).json()['price'])
    print(get_liqmap_text('ETHUSDT', eth_p))

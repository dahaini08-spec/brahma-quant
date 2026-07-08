"""
volume_profile.py — 成交量分布密度分析（Volume Profile）
设计院·达摩院 三院审核修复 2026-07-08

职责：
  1. 计算当前价格区间的历史成交密度
  2. 识别高密度支撑区 / 低密度空洞区
  3. 为 brahma_core s8 提供 VolProfile 评分

结论映射：
  密度 > 1.5x 均值 → 高密度支撑区 → 做多 +8 / 做空 -5
  密度 > 1.2x 均值 → 中密度支撑区 → 做多 +4 / 做空 -2
  密度 < 0.8x 均值 → 低密度空洞区 → 做多 -8（下跌加速风险）
  密度 < 0.6x 均值 → 极低密度空洞 → 做多 -15（踩踏风险）

数据源：Binance fapi/v1/klines（近96根1H K线，免费）
"""

import requests
import time
from typing import Tuple

_CACHE: dict = {}
_CACHE_TTL = 300  # 5分钟，1H K线数据更新慢


def _fetch_klines(symbol: str, limit: int = 96) -> list:
    """拉取近96根1H K线"""
    cache_key = f'vp_klines_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']
    try:
        url = f'https://fapi.binance.com/fapi/v1/klines'
        r = requests.get(url, params={'symbol': symbol, 'interval': '1h', 'limit': limit}, timeout=8)
        data = r.json()
        _CACHE[cache_key] = {'ts': now, 'data': data}
        return data
    except Exception:
        return []


def get_volume_profile(symbol: str, price: float, bin_width_pct: float = 0.5) -> dict:
    """
    计算当前价格区间的成交密度

    返回：
      density_ratio   : 当前区间密度 / 均值密度
      density_label   : HIGH_DENSITY / NORMAL / LOW_DENSITY / VOID
      score_adj       : 评分调整（做多视角）
      nearby_hvn      : 附近高密度价值区（HVN）
      nearby_lvn      : 附近低密度价值区（LVN）
      poc             : Point of Control（最高成交密度价位）
    """
    klines = _fetch_klines(symbol, 96)
    if not klines or price <= 0:
        return _empty(price)

    bin_size = price * bin_width_pct / 100
    price_bins: dict = {}
    all_volumes = []

    for k in klines:
        try:
            lo = float(k[3]); hi = float(k[2]); vol = float(k[5])
            # 将成交量分配到价格区间
            mid = (lo + hi) / 2
            bin_key = round(mid / bin_size) * bin_size
            price_bins[bin_key] = price_bins.get(bin_key, 0) + vol
            all_volumes.append(vol)
        except Exception:
            continue

    if not price_bins:
        return _empty(price)

    # 均值密度
    avg_density = sum(price_bins.values()) / len(price_bins)
    if avg_density <= 0:
        return _empty(price)

    # 当前价格区间密度
    cur_bin = round(price / bin_size) * bin_size
    cur_density = price_bins.get(cur_bin, 0)
    # 扩展±1档搜索（防止边界效应）
    for adj in [-bin_size, bin_size]:
        adj_bin = round((price + adj) / bin_size) * bin_size
        cur_density = max(cur_density, price_bins.get(adj_bin, 0))

    density_ratio = round(cur_density / avg_density, 2)

    # 分类
    if density_ratio >= 1.5:
        label = 'HIGH_DENSITY'
        score_adj_long = +8
        score_adj_desc = f'高密度筹码区{density_ratio:.1f}x→支撑强'
    elif density_ratio >= 1.2:
        label = 'NORMAL_HIGH'
        score_adj_long = +4
        score_adj_desc = f'中密度筹码区{density_ratio:.1f}x→支撑中等'
    elif density_ratio >= 0.8:
        label = 'NORMAL'
        score_adj_long = 0
        score_adj_desc = f'普通密度区{density_ratio:.1f}x→中性'
    elif density_ratio >= 0.6:
        label = 'LOW_DENSITY'
        score_adj_long = -8
        score_adj_desc = f'低密度空洞{density_ratio:.1f}x→支撑薄弱'
    else:
        label = 'VOID'
        score_adj_long = -15
        score_adj_desc = f'极低密度空洞{density_ratio:.1f}x→踩踏风险!'

    # POC（最高成交密度价位）
    poc_bin = max(price_bins, key=lambda k: price_bins[k])

    # 附近HVN（高密度区，>1.5x，价格±5%范围内）
    hvn_list = sorted(
        [(b, v) for b, v in price_bins.items()
         if v > avg_density * 1.5 and abs(b - price) / price < 0.05],
        key=lambda x: abs(x[0] - price)
    )
    nearby_hvn = [round(b, 2) for b, _ in hvn_list[:3]]

    # 附近LVN（低密度区，<0.6x，价格±5%范围内）
    lvn_list = sorted(
        [(b, v) for b, v in price_bins.items()
         if v < avg_density * 0.6 and abs(b - price) / price < 0.05],
        key=lambda x: abs(x[0] - price)
    )
    nearby_lvn = [round(b, 2) for b, _ in lvn_list[:3]]

    return {
        'density_ratio':  density_ratio,
        'density_label':  label,
        'score_adj_long': score_adj_long,
        'score_adj_short': -score_adj_long,  # 做空视角相反
        'desc':           score_adj_desc,
        'poc':            round(poc_bin, 2),
        'nearby_hvn':     nearby_hvn,
        'nearby_lvn':     nearby_lvn,
        'avg_density':    round(avg_density, 2),
        'cur_density':    round(cur_density, 2),
    }


def get_vp_score(symbol: str, price: float, signal_dir: str) -> Tuple[int, str]:
    """供 brahma_core s8 调用的评分接口"""
    try:
        vp = get_volume_profile(symbol, price)
        if signal_dir == 'LONG':
            score = vp['score_adj_long']
        else:
            score = vp['score_adj_short']
        desc = vp['desc']
        return score, desc
    except Exception:
        return 0, 'VolProfile N/A'


def _empty(price: float) -> dict:
    return {
        'density_ratio': 1.0, 'density_label': 'NORMAL',
        'score_adj_long': 0, 'score_adj_short': 0,
        'desc': 'VolProfile 数据不足',
        'poc': price, 'nearby_hvn': [], 'nearby_lvn': [],
        'avg_density': 0, 'cur_density': 0,
    }


if __name__ == '__main__':
    import requests as _r
    for sym in ['BTCUSDT', 'ETHUSDT']:
        px = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
                          params={'symbol': sym}, timeout=8).json()['price'])
        vp = get_volume_profile(sym, px)
        score_l, desc_l = get_vp_score(sym, px, 'LONG')
        print(f"{sym} ${px:.2f}")
        print(f"  密度: {vp['density_ratio']}x ({vp['density_label']})")
        print(f"  POC: ${vp['poc']:.2f}")
        print(f"  HVN: {vp['nearby_hvn']}")
        print(f"  LVN: {vp['nearby_lvn']}")
        print(f"  做多评分: {score_l:+d} | {desc_l}")
        print()

#!/usr/bin/env python3
"""
spot_strategy_runner.py — 现货策略专属分析器
设计院封印 2026-09-03 苏摩111

定位：拳头二·现货策略，独立于期货合约runner
时间框架：1D/1W（与期货1H/4H完全隔离）
止损逻辑：筹码密集区止损（不用ATR%杠杆SL）
输出：中线布局卡片（建仓区间/目标区/止损区/预期周期）

使用方式：
  python3 scripts/spot_strategy_runner.py --symbol BTC
  python3 scripts/spot_strategy_runner.py --symbol ETH
"""
import sys, json, time, argparse, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))


def get_klines_1d(symbol: str, limit: int = 90) -> list:
    """拉取日线K线"""
    try:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={symbol}&interval=1d&limit={limit}')
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
        return data
    except Exception as e:
        print(f'[spot] 1D klines失败: {e}')
        return []


def get_current_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0


def calc_vol_profile(klines: list, bins: int = 20) -> dict:
    """
    简易Volume Profile：把价格区间分bins，统计成交量分布
    返回：高密区/低密区/POC（最高成交量价格）

    修复说明（2026-09-04 苏摩111）：
    - BUG1: low_density用桶的最小值，可能是历史高价区的冷门桶 → 建仓区高于POC → 目标倒置
      修复：建仓区 = 当前价以下最近的低密支撑（从当前价向下找成交量最低桶）
    - BUG2: high_density按累积从低到高取，实际取到的是低价区 → tp2 = 低价 < entry
      修复：高密目标区 = 当前价以上的高阻力区（从当前价向上找成交量最高桶）
    """
    if not klines:
        return {}

    prices  = [float(k[4]) for k in klines]  # close
    volumes = [float(k[5]) for k in klines]   # volume
    lo, hi  = min(prices), max(prices)
    current = prices[-1]  # 最新收盘价作为当前价参考
    if hi == lo:
        return {}

    step = (hi - lo) / bins
    buckets = [0.0] * bins

    for p, v in zip(prices, volumes):
        idx = min(int((p - lo) / step), bins - 1)
        buckets[idx] += v

    # POC：全局成交量最大桶
    poc_idx   = buckets.index(max(buckets))
    poc_price = lo + (poc_idx + 0.5) * step

    # 建仓区：当前价以下的低密支撑
    # 从当前价对应桶向下扫，找成交量最低的桶（筹码空白区=支撑）
    cur_idx = min(int((current - lo) / step), bins - 1)
    below_buckets = [(i, buckets[i]) for i in range(cur_idx + 1)]  # 当前价及以下
    if below_buckets:
        entry_idx = min(below_buckets, key=lambda x: x[1])[0]
        low_price = lo + (entry_idx + 0.5) * step
    else:
        low_price = lo + 0.5 * step  # fallback

    # 目标区：当前价以上的高密阻力（聪明钱会在此减仓）
    above_buckets = [(i, buckets[i]) for i in range(cur_idx + 1, bins)]
    if above_buckets:
        # 取上方成交量最高的3个桶作为目标阻力
        sorted_above = sorted(above_buckets, key=lambda x: x[1], reverse=True)
        high_density = sorted([lo + (x[0] + 0.5) * step for x in sorted_above[:3]])
    else:
        high_density = [hi * 0.95, hi * 0.97, hi]

    return {
        'poc':          round(poc_price, 2),
        'high_density': [round(p, 2) for p in high_density],
        'low_density':  round(low_price, 2),
        'range_lo':     round(lo, 2),
        'range_hi':     round(hi, 2),
    }


def get_regime_1d(symbol: str) -> dict:
    """用brahma_core跑1D体制判断"""
    try:
        from brahma_brain import brahma_core
        result = brahma_core.analyze(symbol, signal_dir='LONG')
        return {
            'regime':  result.get('regime', 'UNKNOWN'),
            'score':   result.get('score', 0),
            'rsi_1d':  result.get('rsi_1d', 50),
        }
    except Exception as e:
        print(f'[spot] brahma_core失败: {e}')
        return {'regime': 'UNKNOWN', 'score': 0, 'rsi_1d': 50}


def build_spot_recommendation(symbol: str, price: float, regime: str,
                               score: float, rsi: float, vp: dict) -> dict:
    """
    根据体制+筹码分布生成现货策略
    """
    poc   = vp.get('poc', price)
    lo    = vp.get('range_lo', price * 0.85)
    hi    = vp.get('range_hi', price * 1.15)
    ldz   = vp.get('low_density', price * 0.92)   # 低密区（建仓区）
    hdz   = vp.get('high_density', [price * 1.1])  # 高密区（目标区）

    # 体制判断
    bullish_regimes = ['BULL_EARLY', 'BULL_TREND', 'BEAR_RECOVERY']
    bearish_regimes = ['BEAR_TREND', 'BEAR_EARLY']

    # 安全校验：确保 entry < tp1 < tp2（目标必须高于建仓）
    # 修复BUG3: 若poc<=ldz（POC低于建仓区），tp1改用上方第一阻力
    def _safe_tp(entry: float, tp_candidate: float, fallback_pct: float) -> float:
        """确保目标价高于入场价，否则用fallback"""
        if tp_candidate > entry * 1.02:  # 至少高于入场2%才算有效目标
            return tp_candidate
        return round(entry * (1 + fallback_pct), 2)

    if regime in bullish_regimes:
        bias     = 'LONG'
        nav_pct  = 5 if regime == 'BULL_EARLY' else (3 if regime == 'BULL_TREND' else 2)
        entry_lo = round(ldz * 0.99, 2)
        entry_hi = round(ldz * 1.01, 2)
        sl       = round(lo * 0.97, 2)         # 结构失效位（区间低点下3%）
        # tp1: 上方第一阻力（优先用高密区最低价，比POC更直观）
        tp1_raw  = hdz[0] if hdz else poc
        tp1      = _safe_tp(entry_hi, tp1_raw, 0.08)
        tp2      = _safe_tp(tp1, hdz[-1] if hdz else poc, 0.15)
        weeks    = '4~8'
        logic    = f'{regime}体制+筹码低密区建仓，目标上方高密阻力'
    elif regime in bearish_regimes:
        bias     = 'WATCH'
        nav_pct  = 0
        entry_lo = round(lo * 0.95, 2)
        entry_hi = round(lo * 0.97, 2)
        sl       = round(lo * 0.93, 2)
        tp1      = round(lo * 1.08, 2)  # 空仓观望，目标仅供参考
        tp2      = round(poc, 2)
        weeks    = '待体制切换'
        logic    = f'{regime}体制，空仓等待，关注{entry_lo:.0f}~{entry_hi:.0f}超跌区'
    else:  # CHOP_MID
        bias     = 'LIGHT'
        nav_pct  = 2
        entry_lo = round(ldz * 0.98, 2)
        entry_hi = round(ldz * 1.00, 2)
        sl       = round(lo * 0.96, 2)
        tp1_raw  = hdz[0] if hdz else poc
        tp1      = _safe_tp(entry_hi, tp1_raw, 0.06)
        tp2      = _safe_tp(tp1, hdz[-1] if hdz else hi * 0.95, 0.10)
        weeks    = '2~4'
        logic    = 'CHOP体制，轻仓试探，等突破方向确认'

    return {
        'symbol':    symbol,
        'price':     price,
        'regime':    regime,
        'bias':      bias,
        'entry_lo':  entry_lo,
        'entry_hi':  entry_hi,
        'sl':        sl,
        'tp1':       tp1,
        'tp2':       tp2,
        'nav_pct':   nav_pct,
        'weeks':     weeks,
        'logic':     logic,
        'rsi_1d':    rsi,
        'poc':       poc,
    }


def format_spot_card(rec: dict) -> str:
    """输出现货专属VIP卡片"""
    sym    = rec['symbol'].replace('USDT', '')
    bias   = rec['bias']
    emoji  = '🟢' if bias == 'LONG' else ('🟡' if bias == 'LIGHT' else '⚪')

    card = f"""
🌿 姓赵不宣 | {sym} 现货中线布局

{emoji} 建仓区间｜${rec['entry_lo']:,.2f}~${rec['entry_hi']:,.2f}
🎯 目标区｜${rec['tp1']:,.2f}→${rec['tp2']:,.2f}
🚫 止损区｜${rec['sl']:,.2f}（结构失效位）
⏰ 预期周期｜{rec['weeks']}周
仓位｜{rec['nav_pct']}%NAV（无杠杆）

⚠️ {rec['logic']}
📊 体制:{rec['regime']} RSI1D:{rec['rsi_1d']:.0f} POC:${rec['poc']:,.2f}

📊 梵天系统｜数据驱动｜不是建议"""
    return card.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTC', help='标的符号（BTC/ETH等）')
    parser.add_argument('--quiet',  action='store_true')
    args = parser.parse_args()

    symbol = args.symbol.upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    print(f'[spot] 现货策略分析: {symbol}')
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # 拉数据
    price  = get_current_price(symbol)
    klines = get_klines_1d(symbol, limit=90)
    vp     = calc_vol_profile(klines)
    regime_data = get_regime_1d(symbol)

    print(f'[spot] price={price:.2f} regime={regime_data["regime"]} '
          f'rsi1d={regime_data["rsi_1d"]:.1f} POC={vp.get("poc",0):.2f}')

    rec  = build_spot_recommendation(
        symbol, price,
        regime_data['regime'], regime_data['score'], regime_data['rsi_1d'],
        vp
    )
    card = format_spot_card(rec)

    print('\n' + '═'*50)
    print(f'  📊 梵天现货策略 | {symbol} | {ts}')
    print('═'*50)
    print(card)
    print('═'*50)

    # 写入缓存
    cache_file = BASE / 'data' / f'spot_strategy_{symbol}.json'
    cache_file.write_text(json.dumps({**rec, 'ts': ts, 'card': card}, indent=2))
    print(f'[spot] 策略已缓存: {cache_file.name}')


if __name__ == '__main__':
    main()

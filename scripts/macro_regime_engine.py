#!/usr/bin/env python3
"""
梵天宏观体制锚引擎 [封印 2026-10-26 苏摩111]
接入位置：brahma_state_refresh.py末尾 + brahma_deep_analysis.py enrich()
功能：周线/月线宏观体制判断，为所有短线信号提供顶层偏置
= 解决梵天在牛市回调中做空的系统性错误
"""
import json, time, urllib.request
from pathlib import Path

BASE       = Path(__file__).parent.parent
CACHE_PATH = BASE / 'data' / 'macro_regime_state.json'
CACHE_TTL  = 4 * 3600  # 4H缓存

def _fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read())
    except Exception:
        return []

def _ema(closes: list, period: int) -> float:
    if not closes: return 0
    e = closes[0]
    k = 2 / (period + 1)
    for c in closes[1:]: e = c * k + e * (1 - k)
    return round(e, 1)

def run_macro_regime(force: bool = False) -> dict:
    """
    计算宏观体制。
    周线层：EMA位置 + 52周位置 + 连续涨跌
    月线层：月线趋势 + 近期动量
    """
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if time.time() - cached.get('ts', 0) < CACHE_TTL:
                return cached
        except Exception:
            pass

    result = {'ts': time.time(), 'dt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    for sym in ['BTC', 'ETH']:
        ticker = sym + 'USDT'

        # 周线K线（52根）
        weekly = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={ticker}&interval=1w&limit=52')
        # 月线K线（12根）
        monthly= _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={ticker}&interval=1M&limit=12')
        # 当前价
        px_data= _fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={ticker}')
        px = float(px_data.get('price', 0)) if px_data else 0

        if not weekly or not px:
            result[sym] = {'macro_regime': 'UNKNOWN', 'error': 'no data'}
            continue

        w_closes = [float(k[4]) for k in weekly]
        w_highs  = [float(k[2]) for k in weekly]
        w_lows   = [float(k[3]) for k in weekly]

        # 52周高低位置
        hi52 = max(w_highs); lo52 = min(w_lows)
        pos52 = round((px - lo52) / (hi52 - lo52) * 100, 1) if hi52 > lo52 else 50

        # 周线EMA20
        w_ema20 = _ema(w_closes, 20)
        above_weekly_ema = px > w_ema20

        # 近8周涨跌方向
        recent8 = [(float(k[4]) - float(k[1])) / float(k[1]) * 100
                   for k in weekly[-8:]]
        bull_weeks = sum(1 for r in recent8 if r > 0)
        bear_weeks = sum(1 for r in recent8 if r < 0)
        consec_bear = 0
        for r in reversed(recent8):
            if r < 0: consec_bear += 1
            else: break

        # 月线动量
        m_chg = 0
        if monthly and len(monthly) >= 3:
            m3 = [float(k[4]) - float(k[1]) for k in monthly[-3:]]
            m_chg = sum(m3[-2:]) / 2  # 近2月平均涨跌

        # 宏观体制判断（6级别）
        if pos52 < 25 and not above_weekly_ema:
            macro_regime = 'BEAR_DEEP'
            bias_mult    = {'LONG': 0.3, 'SHORT': 1.5}
            desc = f'深熊区({pos52:.0f}%) 禁多'
        elif pos52 < 40 and not above_weekly_ema:
            macro_regime = 'BEAR_EARLY'
            bias_mult    = {'LONG': 0.5, 'SHORT': 1.3}
            desc = f'熊市初期({pos52:.0f}%) 偏空'
        elif pos52 < 50 and above_weekly_ema:
            macro_regime = 'BULL_EARLY'
            bias_mult    = {'LONG': 1.3, 'SHORT': 0.7}
            desc = f'牛市早期({pos52:.0f}%) 偏多'
        elif pos52 < 65 and above_weekly_ema:
            macro_regime = 'BULL_MID'
            bias_mult    = {'LONG': 1.2, 'SHORT': 0.8}
            desc = f'牛市中期({pos52:.0f}%) 多为主'
        elif pos52 < 80 and above_weekly_ema:
            macro_regime = 'BULL_LATE'
            bias_mult    = {'LONG': 1.0, 'SHORT': 1.0}
            desc = f'牛市高位({pos52:.0f}%) 双向谨慎'
        elif pos52 >= 80:
            macro_regime = 'DIST_TOP'
            bias_mult    = {'LONG': 0.7, 'SHORT': 1.2}
            desc = f'顶部分发({pos52:.0f}%) 偏空'
        else:
            macro_regime = 'CHOP_MACRO'
            bias_mult    = {'LONG': 1.0, 'SHORT': 1.0}
            desc = f'宏观震荡({pos52:.0f}%)'

        # 周线支撑/阻力
        weekly_support = min(w_lows[-8:])   # 近8周最低
        weekly_resist  = max(w_highs[-8:])  # 近8周最高
        near_support   = abs(px - weekly_support) / px < 0.015  # 在周线支撑1.5%内
        near_resist    = abs(px - weekly_resist)  / px < 0.015  # 在周线阻力1.5%内

        result[sym] = {
            'macro_regime':    macro_regime,
            'desc':            desc,
            'pos52':           pos52,
            'weekly_ema20':    w_ema20,
            'above_weekly_ema': above_weekly_ema,
            'weekly_support':  round(weekly_support, 1),
            'weekly_resist':   round(weekly_resist, 1),
            'near_support':    near_support,
            'near_resist':     near_resist,
            'consec_bear_weeks': consec_bear,
            'bull_weeks_8':    bull_weeks,
            'bear_weeks_8':    bear_weeks,
            'bias_mult':       bias_mult,
            'm_momentum':      round(m_chg, 2),
        }

    # ETH/BTC相对强弱
    if 'BTC' in result and 'ETH' in result:
        btc_w = _fetch('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1w&limit=5')
        eth_w = _fetch('https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=1w&limit=5')
        if btc_w and eth_w and len(btc_w)>=5 and len(eth_w)>=5:
            btc_4w=(float(btc_w[-1][4])-float(btc_w[0][1]))/float(btc_w[0][1])*100
            eth_4w=(float(eth_w[-1][4])-float(eth_w[0][1]))/float(eth_w[0][1])*100
            rel = round(eth_4w - btc_4w, 2)
            result['ETH_BTC_rel_4w'] = rel
            result['ETH_BTC_rel_signal'] = (
                'ETH_WEAK' if rel < -5 else
                'ETH_STRONG' if rel > 5 else
                'NEUTRAL'
            )

    # [接入 2026-10-27 苏摩111] weekly_monthly_anchor HTF锚定增强
    # 补充52W区间、HTF共振分数等字段，避免macro_regime重复拉取周线数据
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from weekly_monthly_anchor import get_anchor
        for sym in ['BTC', 'ETH']:
            if sym not in result: continue
            ticker = sym + 'USDT'
            px_now = float(result[sym].get('weekly_support', 0))  # 粗略用支撑位做占位
            try:
                anchor   = get_anchor(ticker)
                features = anchor.get_features(current_price=0)
                result[sym]['htf_resonance']   = round(features.get('htf_resonance', 0), 3)
                result[sym]['weekly_position'] = round(features.get('weekly_position', 0), 3)
                result[sym]['ema20_weekly_htf'] = features.get('ema20_weekly', result[sym].get('weekly_ema20', 0))
            except Exception:
                pass
    except Exception:
        pass

    CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    r = run_macro_regime(force=True)
    for sym in ['BTC', 'ETH']:
        d = r.get(sym, {})
        print(f"\n{sym} 宏观体制: {d.get('macro_regime')} — {d.get('desc')}")
        print(f"  52周位置={d.get('pos52')}% 周线EMA20=${d.get('weekly_ema20'):,.0f}")
        print(f"  周线支撑=${d.get('weekly_support'):,.0f} 阻力=${d.get('weekly_resist'):,.0f}")
        print(f"  近支撑: {'⚠️是' if d.get('near_support') else '否'} "
              f"近阻力: {'⚠️是' if d.get('near_resist') else '否'}")
        print(f"  连续阴线: {d.get('consec_bear_weeks')}周 多空比: {d.get('bull_weeks_8')}/{d.get('bear_weeks_8')}")
        print(f"  信号偏置: 多={d.get('bias_mult',{}).get('LONG',1):.1f}x 空={d.get('bias_mult',{}).get('SHORT',1):.1f}x")
    print(f"\nETH/BTC 4周相对强弱: {r.get('ETH_BTC_rel_4w',0):+.1f}% [{r.get('ETH_BTC_rel_signal')}]")

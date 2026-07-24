"""
brahma_macro_bottom.py
宏观底部判断独立模块 [Phase3 2026-07-24 设计院自主·苏摩确认]

设计原则：
  独立于梵天35维矩阵，专用于「宏观级别布局」判断
  1H体制信号≠宏观底部，此模块才是宏观底部的唯一判断入口
"""
import urllib.request, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent

def _get_closes(sym, interval, limit=20, futures=True):
    prefix = 'fapi.binance.com/fapi' if futures else 'api.binance.com/api'
    url = f'https://{prefix}/v1/klines?symbol={sym}&interval={interval}&limit={limit}'
    try:
        r = urllib.request.urlopen(url, timeout=6)
        return [float(k[4]) for k in json.loads(r.read())]
    except: return []

def _rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return round(100 - 100/(1 + ag/al), 1) if al else 100

def check_macro_bottom(symbol='BTCUSDT') -> dict:
    """
    五大指标宏观底部检查。
    返回 {status, score, signals, verdict, note}
    """
    signals = {}

    # 指标1: 周线RSI < 32（历史底部区间）
    closes_w = _get_closes(symbol, '1w', 30)
    rsi_w = _rsi(closes_w)
    signals['weekly_rsi'] = {
        'value': rsi_w,
        'threshold': 32,
        'passed': rsi_w < 32,
        'desc': f'周线RSI={rsi_w} (需<32，当前{"达标✅" if rsi_w < 32 else f"未达标❌，还需跌{rsi_w-32:.1f}点"})'
    }

    # 指标2: 多空比多头跌至40%以下（散户绝望）
    try:
        url = f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1'
        r = urllib.request.urlopen(url, timeout=5)
        ls = json.loads(r.read())
        long_pct = float(ls[0]['longAccount']) * 100 if ls else 50
    except: long_pct = 50
    signals['retail_capitulation'] = {
        'value': long_pct,
        'threshold': 40,
        'passed': long_pct < 40,
        'desc': f'多头比例={long_pct:.1f}% (需<40%，当前{"达标✅" if long_pct < 40 else "未达标❌"})'
    }

    # 指标3: 资金费率持续负值（空头付费阶段）
    try:
        url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=16'
        r = urllib.request.urlopen(url, timeout=5)
        frs = [float(x['fundingRate'])*100 for x in json.loads(r.read())]
        neg_count = sum(1 for f in frs if f < -0.005)
        fr_now = frs[-1] if frs else 0
    except: neg_count = 0; fr_now = 0
    signals['funding_rate'] = {
        'value': fr_now,
        'neg_count': neg_count,
        'threshold': 10,  # 16期中10期为深度负值
        'passed': neg_count >= 10,
        'desc': f'资金费率={fr_now:+.4f}% 深度负值期数={neg_count}/16 (需≥10，{"达标✅" if neg_count>=10 else "未达标❌"})'
    }

    # 指标4: OI大幅萎缩（空头清仓）
    try:
        url = f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=48'
        r = urllib.request.urlopen(url, timeout=5)
        oi_list = [float(x['sumOpenInterest']) for x in json.loads(r.read())]
        oi_peak = max(oi_list)
        oi_now  = oi_list[-1]
        oi_drop_pct = (oi_peak - oi_now) / oi_peak * 100
    except: oi_drop_pct = 0; oi_now = 0
    signals['oi_collapse'] = {
        'value': oi_drop_pct,
        'threshold': 20,  # 从峰值下降20%以上
        'passed': oi_drop_pct >= 20,
        'desc': f'OI从峰值下降={oi_drop_pct:.1f}% (需≥20%，{"达标✅" if oi_drop_pct>=20 else "未达标❌"})'
    }

    # 指标5: 矿工利润接近负值（投降信号）
    try:
        data = json.loads((BASE/'data/brahma_state.json').read_text())
        miner_profit = float(data.get('miner_profit_pct', 100))
    except: miner_profit = 100
    signals['miner_capitulation'] = {
        'value': miner_profit,
        'threshold': 10,  # 利润率低于10%
        'passed': miner_profit < 10,
        'desc': f'矿工利润率={miner_profit:.1f}% (需<10%，当前{"达标✅" if miner_profit<10 else "未达标❌"})'
    }

    # 综合评分
    passed = sum(1 for s in signals.values() if s['passed'])
    total  = len(signals)

    if passed >= 4:
        status  = 'MACRO_BOTTOM_CONFIRMED'
        verdict = '🟢 宏观底部确认 — 可以重仓布局'
    elif passed >= 3:
        status  = 'MACRO_BOTTOM_APPROACHING'
        verdict = '🟡 宏观底部临近 — 可以轻仓试探'
    elif passed >= 2:
        status  = 'MACRO_BOTTOM_POSSIBLE'
        verdict = '🟠 底部尚需观察 — 仅短线交易'
    else:
        status  = 'NOT_BOTTOM'
        verdict = '🔴 远未到底部 — 1H信号仅短线有效，勿宏观抄底'

    return {
        'symbol':  symbol,
        'status':  status,
        'score':   f'{passed}/{total}',
        'signals': signals,
        'verdict': verdict,
        'rsi_w':   rsi_w,
        'ts':      time.time(),
        'note':    '此模块是宏观底部的唯一判断入口，独立于梵天35维矩阵'
    }

if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    result = check_macro_bottom(sym)
    print(f'\n🏛️ brahma_macro_bottom · {sym}')
    print(f'状态: {result["status"]}')
    print(f'达标: {result["score"]}')
    print(f'裁定: {result["verdict"]}')
    print()
    for k, v in result['signals'].items():
        print(f'  {v["desc"]}')
    print(f'\n注: {result["note"]}')

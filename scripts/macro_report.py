#!/usr/bin/env python3
"""macro_report.py — 宏观守门直接API版（不用web_search）"""
import urllib.request, json, datetime

def run():
    try:
        eurusd = float(json.loads(urllib.request.urlopen(
            'https://api.binance.com/api/v3/ticker/price?symbol=EURUSDT',timeout=6).read())['price'])
        dxy = round(100.0/eurusd*1.0574, 1)
        dxy_note = '偏强⚠️加密承压' if dxy>104 else '偏弱✅加密友好' if dxy<100 else '中性'
    except:
        dxy = None; dxy_note = '获取失败'

    try:
        fg_raw = json.loads(urllib.request.urlopen(
            'https://api.alternative.me/fng/?limit=1',timeout=6).read())['data'][0]
        fg = int(fg_raw['value']); fg_label = fg_raw['value_classification']
        fg_note = '极度恐慌🔴' if fg<25 else '恐慌🟡' if fg<40 else '极度贪婪🔴' if fg>75 else '正常✅'
    except:
        fg = None; fg_label = ''; fg_note = '获取失败'

    try:
        btc = float(json.loads(urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT',timeout=6).read())['price'])
        eth = float(json.loads(urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT',timeout=6).read())['price'])
        prices = f'BTC=${btc:,.1f} | ETH=${eth:,.2f}'
    except:
        prices = '价格获取失败'

    # 危险级事件日历
    now = datetime.datetime.utcnow()
    danger = [(9,17,'FOMC'),(9,18,'FOMC记者会'),(10,29,'FOMC'),(12,17,'FOMC')]
    upcoming = [f'{n}(还有{(datetime.datetime(2026,m,d)-now).days}天)'
                for m,d,n in danger if 0<=(datetime.datetime(2026,m,d)-now).days<=3]

    risk = 0
    if dxy and dxy>104: risk+=1
    if fg and fg<25: risk+=1
    if upcoming: risk+=2
    conclusion = '🔴宏观风险高→降仓' if risk>=2 else '🟡轻压→正常' if risk==1 else '✅全力运行'

    now_str = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f'🌐 梵天宏观守门 | {now_str}',
        f'EUR/USD={eurusd:.5f} | DXY≈{dxy} {dxy_note}' if dxy else 'DXY: 获取失败',
        f'恐贪指数={fg} {fg_note}({fg_label})' if fg else '恐贪: 获取失败',
        prices,
        f'危险事件: {" | ".join(upcoming)}' if upcoming else '未来72H: 无危险级事件 ✅',
        f'宏观结论: {conclusion}',
    ]
    print('\n'.join(lines))

if __name__ == '__main__':
    run()

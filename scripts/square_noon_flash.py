#!/usr/bin/env python3
"""午盘快讯 - 纯脚本，0 AI tokens"""
import requests, json, os, time

def get_key():
    with open('/root/.openclaw/workspace/alerts/.env') as f:
        for line in f:
            if 'SQUARE_KEY_2' in line:
                return line.strip().split('=',1)[1]
    return None

def post(content, key):
    r = requests.post(
        'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add',
        headers={'X-Square-OpenAPI-Key': key, 'Content-Type': 'application/json', 'clienttype': 'binanceSkill'},
        json={'bodyTextOnly': content}, timeout=15
    )
    return r.json()

def main():
    btc = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5).json()
    eth = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT', timeout=5).json()
    lsr = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=1', timeout=5).json()
    fr  = requests.get('https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1', timeout=5).json()
    
    btc_p = float(btc['price'])
    eth_p = float(eth['price'])
    lsr_v = float(lsr[0]['longShortRatio'])
    fr_v  = float(fr[0]['fundingRate'])*100
    lsr_dir = "多头占优" if lsr_v > 1.2 else ("空头占优" if lsr_v < 0.8 else "多空均衡")

    content = f"""⚡ 午盘快讯 · {time.strftime('%m/%d %H:%M', time.gmtime())} UTC

BTC ${btc_p:,.0f}  |  ETH ${eth_p:,.0f}
多空比 {lsr_v:.2f} — {lsr_dir}
资金费率 {fr_v:+.4f}%

数据实时更新，持仓注意风控。

下午场关注什么？评论告诉我👇
#BTC #ETH #合约交易"""

    key = get_key()
    result = post(content, key)
    if result.get('code') == '000000':
        print(f"✅ 午盘快讯发布成功: https://www.binance.com/square/post/{result['data']['id']}")
    else:
        print(f"❌ 失败: {result}")

if __name__ == '__main__':
    main()

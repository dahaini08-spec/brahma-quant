#!/usr/bin/env python3
"""晚间互动钩子帖 - 纯脚本"""
import requests, json, time, random

def get_key():
    with open('/root/.openclaw/workspace/alerts/.env') as f:
        for line in f:
            if 'SQUARE_KEY_2' in line:
                return line.strip().split('=',1)[1]
    return None

HOOKS = [
    "今晚BTC会收在 ${price:.0f} 上方还是下方？\n👍 上方  |  💬 下方，说说你的判断",
    "今天你的仓位怎么样？\n👍 盈利  |  💬 亏损但我知道为什么  |  😂 别问",
    "你现在BTC是多单还是空单还是空仓？\n👍 多  |  💬 空  |  😴 空仓等机会",
    "今天最让你意外的是哪个币？评论区聊聊👇",
    "合约亏损90%的人，大多数是因为什么？\n👍 重仓  |  💬 频繁操作  |  😤 追涨杀跌",
]

def main():
    btc = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5).json()
    price = float(btc['price'])
    hook = random.choice(HOOKS).format(price=price)
    content = f"🌿 今晚问一个问题\n\n{hook}\n\n#BTC #合约交易 #币圈"
    key = get_key()
    r = requests.post(
        'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add',
        headers={'X-Square-OpenAPI-Key': key, 'Content-Type': 'application/json', 'clienttype': 'binanceSkill'},
        json={'bodyTextOnly': content}, timeout=15
    ).json()
    if r.get('code') == '000000':
        print(f"✅ 互动帖: https://www.binance.com/square/post/{r['data']['id']}")
    else:
        print(f"❌ {r}")

if __name__ == '__main__':
    main()

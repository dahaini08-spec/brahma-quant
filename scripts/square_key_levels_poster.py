#!/usr/bin/env python3
"""
square_key_levels_poster.py — 今日核心价位简短帖 [2026-10-06 苏摩111]
发布时间: UTC 06:00 (北京14:00，下午活跃高峰)
内容: BTC+ETH当日最关键支撑/压力位 + 互动钩子
格式: 3~5行，简洁，带问题引导评论
"""
try:
    import brahma_path_setup
except ImportError:
    pass

import json, ssl, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
CST  = timezone(timedelta(hours=8))

sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

from square_key_router import get_square_key as _get_sq_key
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()


def _post(content: str, key: str) -> dict:
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': key,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def main():
    latest = BASE / 'data' / 'auto_analysis_latest.json'
    if not latest.exists():
        print('[key_levels] auto_analysis_latest.json不存在'); sys.exit(0)

    d = json.loads(latest.read_text())
    signals = d.get('signals', {})
    btc = signals.get('BTC', {}) or {}
    eth = signals.get('ETH', {}) or {}

    def _fmt(v, fmt='{:,.0f}'): return fmt.format(float(v)) if v else '?'

    btc_price  = _fmt(btc.get('price', 0))
    btc_wall   = _fmt(btc.get('liq_short', 0))
    btc_pool   = _fmt(btc.get('liq_long', 0))
    eth_price  = _fmt(eth.get('price', 0))
    eth_wall   = _fmt(eth.get('liq_short', 0))
    eth_pool   = _fmt(eth.get('liq_long', 0))

    now_cst = datetime.now(CST).strftime('%m/%d %H:%M')

    content = (
        f"今天这两个位置盯紧 | {now_cst} CST\n\n"
        f"BTC ${btc_price}\n"
        f"  压力 ${btc_wall} | 支撑 ${btc_pool}\n\n"
        f"ETH ${eth_price}\n"
        f"  压力 ${eth_wall} | 支撑 ${eth_pool}\n\n"
        f"你觉得今天会先碰哪个方向? 评论区说说\n\n"
        f"关注我，每晚21:00直播+SMC教学\n"
        f"注册享20%手续费折扣 bsmkweb.cc/register?ref=XZBX666\n"
        f"🌿 姓赵不宣 | 仅供参考\n"
        f"$BTC $ETH #BTC #ETH #合约交易"
    )

    key = _get_sq_key('auto_post')
    r = _post(content, key)
    if r.get('code') == '000000' or 'data' in r:
        print(f'[key_levels] 发帖成功 ✅ {now_cst}')
    else:
        print(f'[key_levels] 失败: {r}')


if __name__ == '__main__':
    main()

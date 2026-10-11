#!/usr/bin/env python3
"""
square_forecast_poster.py — 存档预测帖 + 周日验证闭环
[设计院封印 2026-10-11 苏摩111]

运行模式：
  --mode archive   → 周一09:00北京时间：发本周判断存档帖（从brahma_state读价格）
  --mode verify    → 周日21:00北京时间：发上周预测对照帖

互动留存逻辑：
  读者周一看到预测，周日会主动回来对照 → 评论区自然复活 → 算法识别7天活跃帖持续推流

接入：brahma_crontab.txt
  0 1 * * 1  python3 scripts/square_forecast_poster.py --mode archive   # UTC01=北京09 周一
  0 13 * * 0 python3 scripts/square_forecast_poster.py --mode verify    # UTC13=北京21 周日
"""
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass

import json, sys, ssl, time, hashlib, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE   = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))
CST    = timezone(timedelta(hours=8))

DEDUP  = BASE / 'data' / 'square_post_dedup.json'
LOG    = BASE / 'data' / 'square_post_log.jsonl'
FORECAST_FILE = BASE / 'data' / 'weekly_forecast.json'   # 存档，供verify读取

from square_key_router import get_square_key as _gsk
SQUARE_KEY = _gsk('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ssl_ctx   = ssl.create_default_context()


def _get_prices() -> dict:
    """拉取BTC/ETH当前价格"""
    import urllib.request as _u, json as _j
    prices = {}
    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
            resp = _j.loads(_u.urlopen(url, timeout=5).read())
            prices[sym.replace('USDT', '')] = float(resp.get('price', 0))
        except Exception:
            prices[sym.replace('USDT', '')] = 0
    return prices


def _build_archive_post(prices: dict, week_str: str) -> str:
    btc = prices.get('BTC', 0)
    eth = prices.get('ETH', 0)
    btc_up = btc * 1.04 if btc else 0
    btc_dn = btc * 0.96 if btc else 0
    eth_up = eth * 1.04 if eth else 0
    eth_dn = eth * 0.96 if eth else 0

    return f"""本周判断存档 · {week_str}

每周一，我把这周的判断写下来存档。周日收盘后，我会回来对照——对了记录，错了复盘。

当前价格基准：
BTC ${btc:,.0f} | ETH ${eth:,.2f}

本周判断：
BTC：震荡区间 ${btc_dn:,.0f}~${btc_up:,.0f}
   突破上方 → 看多目标 ${btc*1.06:,.0f}
   跌破下方 → 看空目标 ${btc*0.94:,.0f}

ETH：震荡区间 ${eth_dn:,.2f}~${eth_up:,.2f}
   BTC带动下跟随，独立性弱，不单独判断

本周最大风险：
美联储相关数据 / BTC期货未平仓量集中到期 / 宏观情绪变化

这是我的判断，不是预测，更不是建议。
周日21:00我来公布对照结果。

你这周的判断是什么？评论说说——A（看涨突破）B（区间震荡）C（看跌下破）

关注我，每晚21:00直播+SMC教学
注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666
🌿 姓赵不宣 | 仅供参考"""


def _build_verify_post(forecast: dict, prices_now: dict, week_str: str) -> str:
    btc_pred_lo = forecast.get('btc_dn', 0)
    btc_pred_hi = forecast.get('btc_up', 0)
    btc_base    = forecast.get('btc', 0)
    btc_now     = prices_now.get('BTC', 0)
    eth_base    = forecast.get('eth', 0)
    eth_now     = prices_now.get('ETH', 0)

    btc_in_range = btc_pred_lo <= btc_now <= btc_pred_hi if btc_pred_lo and btc_pred_hi else None
    btc_chg = (btc_now - btc_base) / btc_base * 100 if btc_base else 0
    eth_chg = (eth_now - eth_base) / eth_base * 100 if eth_base else 0

    verdict = '✅ 区间内' if btc_in_range else ('📈 突破上方' if btc_now > btc_pred_hi else '📉 跌破下方')

    return f"""上周预测对照 · {week_str}

上周一存档的判断：
BTC区间 ${btc_pred_lo:,.0f}~${btc_pred_hi:,.0f}（基准 ${btc_base:,.0f}）

本周实际收盘：
BTC ${btc_now:,.0f}（{btc_chg:+.1f}%）{verdict}
ETH ${eth_now:,.2f}（{eth_chg:+.1f}%）

复盘结论：
{('判断正确——区间震荡走势与预测一致。' if btc_in_range else f'判断{"偏多" if btc_now > btc_pred_hi else "偏空"}——实际走出了{"更强的上涨" if btc_now > btc_pred_hi else "更深的下跌"}，说明看到的结构还不够清晰。')}

不管对错，最重要的是：
把判断写下来 → 回来对照 → 找到误判原因 → 下次更准。

你上周的判断对了吗？评论说说——A（对了）B（错了）C（我根本没做判断）

下周一09:00我会发新一轮存档。

关注我，每晚21:00直播+SMC教学
注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666
🌿 姓赵不宣 | 仅供参考"""


def _post(content: str) -> dict:
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload, headers={
        'X-Square-OpenAPI-Key': SQUARE_KEY,
        'Content-Type': 'application/json',
        'clienttype': 'binanceSkill',
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ssl_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def _log(post_type: str, content: str, resp: dict):
    try:
        entry = {'ts': time.time(), 'post_type': post_type,
                 'post_id': resp.get('data', {}).get('id'),
                 'chars': len(content), 'preview': content[:120]}
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception: pass


def run(mode: str = 'archive', dry_run: bool = False):
    now_cst = datetime.now(CST)
    week_str = now_cst.strftime('%Y-W%W')

    if mode == 'archive':
        prices = _get_prices()
        if not prices.get('BTC'):
            print('[forecast] 价格拉取失败，跳过')
            return
        content = _build_archive_post(prices, week_str)

        # 存档本周预测供verify读取
        FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        forecast_data = {
            'week': week_str,
            'ts': time.time(),
            'btc': prices.get('BTC', 0),
            'eth': prices.get('ETH', 0),
            'btc_up': prices.get('BTC', 0) * 1.04,
            'btc_dn': prices.get('BTC', 0) * 0.96,
        }
        FORECAST_FILE.write_text(json.dumps(forecast_data, ensure_ascii=False, indent=2))
        print(f'[forecast] 存档写入: {FORECAST_FILE}')

        print(f'[forecast:archive] ({len(content)}字)')
        print(content[:200] + '...')
        if dry_run:
            print('[forecast] DRY-RUN'); return
        resp = _post(content)
        if resp.get('code') == '000000':
            print(f'[forecast:archive] ✅ 发布成功')
            _log('forecast_archive', content, resp)
        else:
            print(f'[forecast:archive] 失败: {resp}')

    elif mode == 'verify':
        if not FORECAST_FILE.exists():
            print('[forecast:verify] 无存档数据，跳过')
            return
        forecast = json.loads(FORECAST_FILE.read_text())
        prices_now = _get_prices()
        if not prices_now.get('BTC'):
            print('[forecast:verify] 价格拉取失败，跳过')
            return
        content = _build_verify_post(forecast, prices_now, week_str)

        print(f'[forecast:verify] ({len(content)}字)')
        print(content[:200] + '...')
        if dry_run:
            print('[forecast] DRY-RUN'); return
        resp = _post(content)
        if resp.get('code') == '000000':
            print(f'[forecast:verify] ✅ 发布成功')
            _log('forecast_verify', content, resp)
        else:
            print(f'[forecast:verify] 失败: {resp}')
    else:
        print(f'[forecast] 未知mode: {mode}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['archive', 'verify'], default='archive')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(mode=args.mode, dry_run=args.dry_run)

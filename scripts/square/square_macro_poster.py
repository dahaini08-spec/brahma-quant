#!/usr/bin/env python3
"""
square_macro_poster.py — 宏观事件前瞻发帖 v1.0
[设计院封印 2026-09-11 苏摩111]

CPI/NFP/FOMC前1天自动发帖，蹭Trending Hashtag流量
每帖必有🌿姓赵不宣前缀+📊后缀
"""
import json, os, sys, time, hashlib, ssl, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))

SQUARE_KEY = os.environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27')
API_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
LOG_FILE = BASE / 'data' / 'square_post_log.jsonl'
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# 宏观事件日历（手动维护，也可从web_fetch获取）
MACRO_EVENTS = {
    'CPI': {'hashtag': '#CPIWatch', 'name': 'CPI公布'},
    'NFP': {'hashtag': '#NFPWatch', 'name': '非农就业数据'},
    'FOMC': {'hashtag': '#FOMCWatch', 'name': 'FOMC利率决议'},
    'PPI': {'hashtag': '#PPIWatch', 'name': 'PPI公布'},
}


def is_duplicate(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
        except:
            pass
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    return h in d


def mark_posted(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
        except:
            pass
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    d[h] = now
    DEDUP_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def post_to_square(content):
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        'X-Square-OpenAPI-Key': SQUARE_KEY,
        'Content-Type': 'application/json',
        'clienttype': 'binanceSkill',
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def log_post(content, resp):
    entry = {
        'ts': time.time(), 'post_type': 'macro',
        'post_id': resp.get('data', {}).get('id', 0) if isinstance(resp.get('data'), dict) else 0,
        'chars': len(content), 'preview': content[:200],
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def get_trending_hashtags():
    """从Binance Square获取当前热门标签"""
    try:
        import requests
        # Binance Square trends page
        resp = requests.get('https://www.binance.com/en/square', timeout=10, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        text = resp.text
        # 提取hashtag
        import re
        tags = re.findall(r'#(\w+)', text)
        # 去重
        seen = set()
        unique = []
        for t in tags:
            if t.lower() not in seen and len(t) > 2:
                seen.add(t.lower())
                unique.append(f'#{t}')
        return unique[:5]
    except:
        return ['#CPIWatch', '#BTC']


def run(event_type='CPI', dry_run=False):
    from square_template import build_macro_outlook, audit_post

    event = MACRO_EVENTS.get(event_type, {'hashtag': '#CryptoNews', 'name': event_type})
    event_time = (datetime.now(CST) + timedelta(days=1)).strftime('%m/%d %H:%M') + ' CST'

    # 获取Trending标签
    trending = get_trending_hashtags()
    hashtag = event['hashtag'] if event['hashtag'] in ' '.join(trending) else event['hashtag']

    # 拉BTC当前数据用于剧本
    try:
        import requests
        btc_ticker = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                                   params={'symbol': 'BTCUSDT'}, timeout=5).json()
        btc_price = float(btc_ticker.get('lastPrice', 77000))
    except:
        btc_price = 77000

    # 两种剧本（基于当前价位±3%）
    hot_script = f'BTC跌破$75,382支撑池 → 清算连环 → 测试$72,000'
    cool_script = f'BTC突破$78,458止损墙 → 轧空 → 测试$80,000'

    expectations = [
        f'市场预期CPI同比3.4% / 核心CPI 2.5%',
        f'当前加息概率70% / 恐贪指数68=Greed',
    ]

    action = (
        'CPI前减仓50% | SL加宽1.5倍 | 不在数据公布瞬间挂单\n'
        'CPI后等1H收线确认方向再入场'
    )

    content = build_macro_outlook(
        event_name=event['name'],
        event_time=event_time,
        expectations=expectations,
        hot_script=hot_script,
        cool_script=cool_script,
        btc_regime='CHOP_MID',
        btc_score=7,
        eth_hurst=0.426,
        oi_trend='LONG_UNWIND',
        action_advice=action,
        hashtag=hashtag,
    )

    ok, issues = audit_post(content)
    if not ok:
        print(f'审计失败: {issues}')
        print(content)
        return

    if is_duplicate(content):
        print('24h内重复，跳过')
        return

    print(f'准备发宏观帖 ({len(content)}字):')
    print(content)

    if dry_run:
        print('DRY-RUN')
        return

    resp = post_to_square(content)
    if 'error' in resp:
        print(f'发帖失败: {resp["error"]}')
    else:
        print('✅ 发布成功')
        mark_posted(content)
        log_post(content, resp)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', default='CPI')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(event_type=args.event, dry_run=args.dry_run)

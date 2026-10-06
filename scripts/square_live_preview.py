#!/usr/bin/env python3
"""
square_live_preview.py — 每日直播预告帖自动发布 [2026-10-03 苏摩111]
接入位置: cron 11:00 UTC (北京19:00，直播前2小时)
内容: 今晚21:00直播预告 + 当日核心问题（引导评论互动）
数据源: auto_analysis_latest.json (梵天分析结果)
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, sys, ssl, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))
DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
LATEST_FILE = BASE / 'data' / 'auto_analysis_latest.json'

from square_key_router import get_square_key as _get_sq_key
SQUARE_KEY = _get_sq_key('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()


def is_duplicate(content: str) -> bool:
    import hashlib
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        posts = d if isinstance(d, dict) and 'posts' not in d else d.get('posts', {})
        cutoff = time.time() - 86400
        if isinstance(posts, dict):
            return h in posts and float(posts[h]) > cutoff
    except Exception:
        pass
    return False


def mark_posted(content: str):
    import hashlib
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        if 'posts' not in d and isinstance(d, dict):
            d[h] = time.time()
        DEDUP_FILE.write_text(json.dumps(d))
    except Exception:
        pass


def build_live_preview() -> str:
    """生成直播预告帖，接入auto_analysis_latest数据"""
    date_str = datetime.now(CST).strftime('%m/%d')

    # 读取最新分析数据
    btc_price, eth_price = 0.0, 0.0
    btc_wall, eth_wall = 0.0, 0.0
    btc_bias, eth_bias = 'WATCH', 'WATCH'

    try:
        if LATEST_FILE.exists():
            d = json.loads(LATEST_FILE.read_text())
            output = d.get('output', '')
            # 从output提取关键价位（简单正则）
            import re
            btc_prices = re.findall(r'BTC.*?\$([0-9,]+)', output)
            eth_prices = re.findall(r'ETH.*?\$([0-9,]+)', output)
            if btc_prices:
                btc_price = float(btc_prices[0].replace(',', ''))
            if eth_prices:
                eth_price = float(eth_prices[0].replace(',', ''))
    except Exception:
        pass

    # 三个问题模板（轮换，避免重复）
    day_of_week = datetime.now(CST).weekday()
    questions = [
        'BTC能否有效突破上方压力区？还是会再次被打回来？',
        '现在散户在疯狂做多，但大户在悄悄减仓——你站哪边？',
        'OI数据显示空单在建仓，但价格没跌——这是陷阱还是机会？',
        'ETH相对BTC越来越弱，这是轮动信号还是ETH要补跌？',
        '今晚美盘开盘，外资进场方向会是多还是空？',
        'Hurst指数已到临界点，接下来是趋势启动还是继续震荡？',
        'FVG磁铁在上方，止损墙也在上方——主力到底想把价格带去哪里？',
    ]
    question = questions[day_of_week % len(questions)]

    btc_str = f'${btc_price:,.0f}' if btc_price > 0 else '实时价格'
    eth_str = f'${eth_price:,.0f}' if eth_price > 0 else '实时价格'

    # [2026-10-06 苏摩111] 强化互动钩子：明确征集答案，算法识别评论=更高推送权重
    lines = [
        f'今晚21:00 直播 | {date_str}',
        '',
        f'BTC {btc_str} | ETH {eth_str}',
        '',
        f'今晚要解答这个问题：',
        f'「{question}」',
        '',
        '评论区先说你的判断——多/空/观望？',
        '我会在直播里逐个点名回应。',
        '',
        '21:00 准时，不见不散。',
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 不是建议',
        '$BTC $ETH #直播预告 #BTC #合约交易',
    ]
    return '\n'.join(lines)


def post_to_square(content: str, dry_run: bool = False) -> bool:
    if dry_run:
        print('[DRY-RUN] 直播预告帖:')
        print(content)
        return True
    if is_duplicate(content):
        print('[live_preview] 今日已发直播预告，跳过')
        return False
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': SQUARE_KEY,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
        if resp.get('code') == '000000' or resp.get('success'):
            mark_posted(content)
            print(f'[live_preview] ✅ 直播预告发布成功')
            return True
        else:
            print(f'[live_preview] ❌ 发布失败: {resp}')
            return False
    except Exception as e:
        print(f'[live_preview] ❌ 异常: {e}')
        return False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='直播预告帖自动发布')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    content = build_live_preview()
    post_to_square(content, dry_run=args.dry_run)

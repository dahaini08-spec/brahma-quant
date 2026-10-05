#!/usr/bin/env python3
"""
square_opinion_poster.py — 每日「我的判断」观点帖 [2026-10-04 苏摩111 P1]
=========================================================================
定位: 与战场报告「等结构确认」的数据帖区分
     这条帖有明确观点——姓赵不宣今天的判断是什么
触发: cron 每日 15:00 UTC (北京23:00，收盘前最后判断)
内容: 当日BTC/ETH走势判断 + 个人观点 + 明日预判
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, sys, ssl, time, hashlib, urllib.request, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST   = timezone(timedelta(hours=8))
DEDUP = BASE / 'data' / 'square_post_dedup.json'
LOG   = BASE / 'data' / 'square_post_log.jsonl'
LATEST = BASE / 'data' / 'auto_analysis_latest.json'

from square_key_router import get_square_key as _gsk
SQUARE_KEY = _gsk('auto_post')  # KEY_0 姓赵不宣
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

_OPINION_OPENERS = [
    '说一下我今天的真实判断，不是系统输出，是我自己的看法。',
    '今天盘面走完了，说说我怎么看接下来。',
    '数据我每天发，今天说说我自己的判断——不一定对，但这是我的真实想法。',
    '系统每天给信号，但有些东西系统不说，我来说。',
    '今天不讲数据了，说说我的观点。',
]

_CLOSERS = [
    '这是我今天的判断。你怎么看？',
    '对不对明天见。欢迎评论说你的判断。',
    '市场会告诉我对错。你今天的判断是什么？',
    '我说了我的，你说说你的——评论区见。',
]

def load_analysis():
    try:
        d = json.loads(LATEST.read_text())
        out = d.get('output', '')
        import re
        result = {}
        for sym in ['BTC', 'ETH']:
            m = re.search(rf'梵天\d+维.+?\| {sym}.*?基准\$([0-9,]+)', out)
            price = float(m.group(1).replace(',','')) if m else 0
            regime_m = re.search(rf'梵天\d+维.+?\| {sym}.+?(CHOP_MID|BULL_TREND|BEAR_TREND|BEAR_RECOVERY|BEAR_EARLY)', out[out.find(f'| {sym}'):out.find(f'| {sym}')+3000] if f'| {sym}' in out else out)
            regime = regime_m.group(1) if regime_m else 'CHOP_MID'
            bias_m = re.search(r'交易员大脑[=：]\s*(SHORT|LONG|WATCH|WAIT)', out[out.find(f'| {sym}'):out.find(f'| {sym}')+3000] if f'| {sym}' in out else out)
            bias = bias_m.group(1) if bias_m else 'WATCH'
            result[sym] = {'price': price, 'regime': regime, 'bias': bias}
        return result
    except Exception as e:
        print(f'[opinion] 数据读取失败: {e}')
        return {}

def build_opinion_post(data: dict) -> str:
    btc = data.get('BTC', {})
    eth = data.get('ETH', {})
    date_s = datetime.now(CST).strftime('%m/%d')

    regime_cn = {
        'BULL_TREND': '牛市趋势', 'BEAR_TREND': '熊市趋势',
        'CHOP_MID': '震荡整理', 'BEAR_RECOVERY': '熊市反弹',
        'BEAR_EARLY': '熊市初期',
    }
    bias_cn = {'LONG': '看多', 'SHORT': '看空', 'WATCH': '观望', 'WAIT': '等待'}

    btc_r = regime_cn.get(btc.get('regime',''), '震荡')
    eth_r = regime_cn.get(eth.get('regime',''), '震荡')
    btc_b = bias_cn.get(btc.get('bias',''), '观望')
    eth_b = bias_cn.get(eth.get('bias',''), '观望')

    opener = random.choice(_OPINION_OPENERS)

    # 生成观点段
    if btc.get('bias') == 'SHORT' and eth.get('bias') == 'SHORT':
        opinion = (
            f'BTC和ETH今天都偏空。\n'
            f'我的判断是：空头占优，但不是暴跌的结构。'
            f'更像是主力在高位慢慢出货，价格会一级一级往下走，而不是一根大阴线砸下去。\n'
            f'做空的话，我会等反弹到止损墙附近，收阴确认再入——不抢跑。\n'
            f'明天继续观察OI是否继续下降，下降=空头确认；反弹=小心轧空。'
        )
    elif btc.get('bias') == 'LONG' and eth.get('bias') == 'LONG':
        opinion = (
            f'BTC和ETH今天都偏多。\n'
            f'我的判断是：多头结构在，但散户情绪已经偏乐观了，这时候追多要小心。\n'
            f'真正的机会在回调，不在追高。'
            f'等价格回踩支撑池，1H收阳确认再考虑入场。\n'
            f'明天关注量能，放量上涨=多头真实；缩量上涨=小心假突破。'
        )
    elif btc.get('bias') != eth.get('bias'):
        opinion = (
            f'BTC和ETH今天方向分歧——一个偏多，一个偏空。\n'
            f'我的判断是：这种分歧通常是趋势切换前的信号。'
            f'不是两个都做，而是哪个先出结构就跟哪个。\n'
            f'在分歧确认之前，我选择等待——分歧盘面里强行入场，盈亏比不合算。\n'
            f'明天看哪个先突破，跟突破方向走。'
        )
    else:
        opinion = (
            f'今天两个盘子都在震荡等待。\n'
            f'我的判断是：这种横盘是在酝酿方向，不是无聊的来回。'
            f'主力在用震荡清洗浮动筹码——清洗完才会选方向。\n'
            f'我现在的操作是：空仓等待，不在区间里博弈。'
            f'宁可错过，不抢跑。\n'
            f'明天关注BTC能否突破当前区间，突破哪边就跟哪边。'
        )

    closer = random.choice(_CLOSERS)

    lines = [
        f'我的判断 | {date_s}',
        '',
        opener,
        '',
        opinion,
        '',
        closer,
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 仅供参考',
        '$BTC $ETH #BTC #ETH #合约交易 #永续合约',
    ]
    return '\n'.join(lines)

def is_duplicate(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        return h in d and time.time() - d[h] < 86400
    except Exception: return False

def mark_posted(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d[h] = time.time()
        DEDUP.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception: pass  # dedup/文件读取失败，安全降级

def post_to_square(content):
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': SQUARE_KEY,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}

def run(dry_run=False):
    data = load_analysis()
    if not data:
        print('[opinion] 无数据，跳过')
        return

    content = build_opinion_post(data)
    if is_duplicate(content):
        print('[opinion] 今日已发，HEARTBEAT_OK')
        return

    print(f'[opinion] 今日判断 ({len(content)}字)')
    if dry_run:
        print(content)
        print('[opinion DRY-RUN] ✅')
        return

    resp = post_to_square(content)
    if resp.get('code') == '000000' or resp.get('success'):
        pid = resp.get('data', {}).get('id', '')
        print(f'[opinion] ✅ 发布成功 id={pid}')
        mark_posted(content)
        with open(LOG, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'post_type': 'opinion',
                'id': pid, 'chars': len(content), 'preview': content[:200],
            }, ensure_ascii=False) + '\n')
    else:
        print(f'[opinion] ❌ {resp}')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    run(dry_run=ap.parse_args().dry_run)

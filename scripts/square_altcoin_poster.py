#!/usr/bin/env python3
"""
square_altcoin_poster.py — KEY_1 蓝桉VS释怀鸟 | 山寨币暴动分析 [2026-10-04 苏摩111自主决策]
=========================================================================
定位: 完全独立选题，不改写KEY_0内容
内容: 山寨币异动 → 暴涨/暴跌逻辑拆解 → 高频交易视角
触发: cron */2h，扫24h涨跌幅榜，涨跌>15%且vol>500万U才发
账号: 蓝桉VS释怀鸟 (KEY_1)
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, sys, ssl, time, hashlib, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
import random

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))
FAPI = 'https://fapi.binance.com/fapi/v1'
DEDUP = BASE / 'data' / 'square_post_dedup.json'
LOG   = BASE / 'data' / 'square_post_log.jsonl'
COOLDOWN = BASE / 'data' / 'altcoin_cooldown.json'

from square_key_router import get_square_key as _gsk
SQUARE_KEY = _gsk('hot_poster')  # KEY_1 蓝桉
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

# 主流币排除（KEY_0已覆盖）
_MAJOR = {'BTC','ETH','BNB','SOL','XRP','ADA','DOGE','MATIC','AVAX','DOT','LINK','UNI'}

# 蓝桉人格：高频交易员，直接、具体、有数据
_OPENERS_UP = [
    '${sym}今天+{chg:.0f}%。先问自己：你是第几个知道这件事的？',
    '${sym} +{chg:.0f}%，追不追？我的答案直接告诉你。',
    '单日+{chg:.0f}%，${sym}这根K线你读懂了吗？',
    '${sym}拉了{chg:.0f}%。数据在这，自己判断。',
]
_OPENERS_DOWN = [
    '${sym}跌了{chg:.0f}%。这个跌法，有几种可能。',
    '${sym} {chg:.0f}%，有人在砸还是结构性下跌？',
    '单日{chg:.0f}%，${sym}这笔单你会怎么处理？',
]
_CLOSERS = [
    '我不预测，我只看数据说话。',
    '高频做的就是这种——数据说什么，我做什么。',
    '追不追，答案在数据里，不在感觉里。',
    '看完数据，你的判断是什么？',
]
_HOOKS = [
    '你会在这个位置进场吗？评论 A（会）B（等等再说）C（不碰）',
    '这个涨幅你追还是观望？评论告诉我',
    '如果是你，止损放哪？评论说说',
    '你觉得这波是主力拉盘还是真实需求？评论投票',
]

def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=8, context=_ctx).read())

def load_cooldown():
    try:
        if COOLDOWN.exists():
            return json.loads(COOLDOWN.read_text(encoding='utf-8'))
    except Exception: pass  # dedup读取失败，安全降级
    return {}

def save_cooldown(cd):
    tmp = COOLDOWN.with_suffix('.tmp')
    tmp.write_text(json.dumps(cd, ensure_ascii=False), encoding='utf-8')
    import os; os.replace(str(tmp), str(COOLDOWN))

def is_cool(sym, cd, hours=4):
    key = f'alt:{sym}'
    return key in cd and time.time() - cd[key] < hours * 3600

def mark_cool(sym, cd):
    cd[f'alt:{sym}'] = time.time()
    save_cooldown(cd)

def is_duplicate(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        if h in d and time.time() - d[h] < 86400:
            return True
    except Exception: pass  # dedup读取失败
    return False

def mark_posted(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d[h] = time.time()
        DEDUP.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception: pass  # dedup写入失败，非阻塞

def scan_altcoins():
    """扫24h异动榜，返回达标山寨币列表"""
    try:
        tickers = _fetch(f'{FAPI}/ticker/24hr')
    except Exception as e:
        print(f'[altcoin] 拉行情失败: {e}')
        return []
    candidates = []
    for t in tickers:
        sym = t.get('symbol','')
        if not sym.endswith('USDT'): continue
        base = sym.replace('USDT','')
        if base in _MAJOR: continue
        chg = float(t.get('priceChangePercent',0))
        vol = float(t.get('quoteVolume',0))
        if abs(chg) < 15 or vol < 5_000_000: continue
        candidates.append({
            'symbol': sym, 'base': base, 'chg': chg,
            'price': float(t.get('lastPrice',0)),
            'high': float(t.get('highPrice',0)),
            'low': float(t.get('lowPrice',0)),
            'vol': vol,
        })
    candidates.sort(key=lambda x: abs(x['chg']), reverse=True)
    return candidates[:2]  # 最多2条/次，间隔180s

def fetch_fr(sym):
    try:
        d = _fetch(f'{FAPI}/premiumIndex?symbol={sym}')
        return float(d.get('lastFundingRate', 0)) * 100
    except Exception: return 0.0

def build_altcoin_post(item):
    base = item['base']
    chg  = item['chg']
    price = item['price']
    high  = item['high']
    low   = item['low']
    vol   = item['vol'] / 1_000_000
    fr    = fetch_fr(item['symbol'])
    amp   = (high - low) / low * 100 if low > 0 else 0

    is_up = chg > 0
    opener_tmpl = random.choice(_OPENERS_UP if is_up else _OPENERS_DOWN)
    opener = opener_tmpl.replace('${sym}', f'${base}').replace('{chg:.0f}', f'{abs(chg):.0f}')

    # FR解读
    if fr > 0.1:
        fr_note = f'FR {fr:.4f}%，多头付费——方向拥挤，追多需谨慎。'
    elif fr < -0.1:
        fr_note = f'FR {fr:.4f}%负值，空头付费——可能有轧空动力。'
    else:
        fr_note = f'FR {fr:.4f}%，多空均衡，无明显方向偏移。'

    # 振幅解读
    amp_note = f'今日振幅{amp:.0f}%，成交额{vol:.0f}M U。'
    if vol < 10:
        amp_note += '池子浅，少量资金就能打出这个涨幅，出的时候未必有人接。'
    else:
        amp_note += '流动性尚可，不是纯操控盘。'

    # 操作判断
    if is_up and fr > 0.05:
        action = f'不追。+{chg:.0f}%追进去，你在给前面的人接盘。等回踩低点+FR回归再看。'
    elif is_up:
        action = f'+{chg:.0f}%后有回踩的可能。等回踩确认支撑再入，不追高。'
    elif not is_up and fr < -0.05:
        action = f'空头拥挤，小心轧空反弹。不追空，等反弹结构再做。'
    else:
        action = f'先观望，等方向明确。'

    lines = [
        opener,
        '',
        amp_note,
        fr_note,
        '',
        f'我的判断：{action}',
        '',
        random.choice(_CLOSERS),
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '💙 蓝桉VS释怀鸟 | 仅供参考',
        f'${base} #合约交易 #山寨币 #永续合约',
        '',
        random.choice(_HOOKS),
    ]
    return '\n'.join(lines)

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
    cd = load_cooldown()
    candidates = scan_altcoins()
    if not candidates:
        print('[altcoin] 无达标山寨币（涨跌<15%或成交<500万），HEARTBEAT_OK')
        return

    posted = 0
    for item in candidates:
        base = item['base']
        if is_cool(base, cd):
            print(f'[altcoin] {base} 4h内已发，跳过')
            continue

        content = build_altcoin_post(item)
        if is_duplicate(content):
            print(f'[altcoin] {base} 24h内重复，跳过')
            continue

        print(f'[altcoin] {base} {item["chg"]:+.1f}% vol={item["vol"]/1e6:.0f}M')
        if dry_run:
            print(content[:300])
            print('[altcoin DRY-RUN] ✅')
            continue

        resp = post_to_square(content)
        if resp.get('code') == '000000' or resp.get('success'):
            pid = resp.get('data', {}).get('id', '')
            print(f'[altcoin] ✅ 发布成功 {base} id={pid}')
            mark_cool(base, cd)
            mark_posted(content)
            with open(LOG, 'a') as f:
                f.write(json.dumps({
                    'ts': time.time(), 'post_type': 'altcoin',
                    'id': pid, 'chars': len(content),
                    'preview': content[:150],
                }, ensure_ascii=False) + '\n')
            posted += 1
            if len(candidates) > 1:
                time.sleep(180)  # 多条间隔180s
        else:
            print(f'[altcoin] ❌ 失败: {resp}')

    if posted == 0 and not dry_run:
        print('[altcoin] 无新帖发出，HEARTBEAT_OK')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    run(dry_run=ap.parse_args().dry_run)

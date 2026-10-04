#!/usr/bin/env python3
"""
square_macro_edu_poster.py — KEY_2 牛来PRO | 宏观教育 [2026-10-04 苏摩111自主决策]
=========================================================================
定位: 完全独立选题，不改写KEY_0/KEY_1内容
内容: 宏观事件解读 + 交易教育（美联储/CPI/美股/恐贪指数/大周期视角）
触发: cron 每日 02:00 UTC（北京10:00），固定时段一条
账号: 牛来PRO (KEY_2)

内容轮转（7主题，每天一个）:
  周一: 美联储政策与BTC关系
  周二: CPI数据怎么影响加密市场
  周三: 恐贪指数实战用法
  周四: 大周期体制判断（牛熊分界）
  周五: 仓位管理数学（凯利公式）
  周六: 止损心理学
  周日: 本周复盘 + 下周展望
"""
import json, sys, ssl, time, hashlib, urllib.request, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST   = timezone(timedelta(hours=8))
DEDUP = BASE / 'data' / 'square_post_dedup.json'
LOG   = BASE / 'data' / 'square_post_log.jsonl'

from square_key_router import get_square_key as _gsk
SQUARE_KEY = _gsk('extreme_alert')  # KEY_2 牛来PRO
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

# ── 7日内容模板 ──────────────────────────────────────────────────
_TOPICS = {
    0: {  # 周一
        'title': '美联储加息与BTC的真实关系',
        'body': (
            '很多人以为「加息=BTC跌」，但数据告诉你，这个逻辑有一半是错的。\n\n'
            '加息影响的是「流动性」，不是直接砸币价。\n\n'
            '真实链条：\n'
            '加息 → 美元融资成本上升 → 风险资产流动性收缩 → 机构减少加密配置\n\n'
            '但反过来也成立：\n'
            '加息预期见顶 → 市场提前定价宽松 → BTC先于降息上涨\n\n'
            '2022年加息周期里，BTC从$47,000跌到$15,000。\n'
            '2023年加息见顶后，BTC从$15,000涨回$40,000。\n\n'
            '结论：不是「加息就跌」，是「流动性拐点」才是真正的买卖信号。'
        ),
        'hook': '你现在判断流动性是收紧还是宽松？评论 A（收紧）B（宽松）C（不确定）',
    },
    1: {  # 周二
        'title': 'CPI数据来了，加密市场怎么反应？',
        'body': (
            'CPI高于预期 → 市场担心加息 → 风险资产承压\n'
            'CPI低于预期 → 市场预期宽松 → 风险资产上涨\n\n'
            '这是教科书逻辑，但实战里经常反着走。\n\n'
            '原因：市场提前定价。\n'
            'CPI发布前，主力已经根据预期建好仓位。\n'
            '数据出来，反而是「利好出尽」或「利空出尽」的时刻。\n\n'
            '高手操作：数据发布前减仓，数据发布后看反应方向再跟进。\n'
            '不在不确定性里押注，等方向确认再入场。\n\n'
            '这叫「不猜数据，只跟结构」。'
        ),
        'hook': 'CPI发布前你通常怎么操作？评论 A（减仓等）B（持仓不动）C（赌方向加仓）',
    },
    2: {  # 周三
        'title': '恐贪指数怎么用才是对的？',
        'body': (
            '恐贪指数 0-100：\n'
            '0-25 极度恐慌 → 历史上大多是买点\n'
            '75-100 极度贪婪 → 历史上大多是卖点\n\n'
            '但直接用这个逻辑亏钱的人多了去了。\n\n'
            '正确用法：\n'
            '极度恐慌 + BTC没有继续创新低 = 底部信号\n'
            '极度贪婪 + BTC涨幅已经超过上次高点50% = 顶部信号\n\n'
            '单独看恐贪指数没用，要结合「价格结构」。\n\n'
            '恐贪是情绪，价格结构是事实。\n'
            '用情绪做辅助，用结构做决策。'
        ),
        'hook': '你平时看恐贪指数吗？评论告诉我你怎么用它',
    },
    3: {  # 周四
        'title': '怎么判断现在是牛市还是熊市？',
        'body': (
            '很多人用感觉判断牛熊，结果总是判断错误。\n\n'
            '我用三个客观指标：\n\n'
            '1. 200周均线\n'
            'BTC价格在200周均线上方 = 牛市结构\n'
            '200周均线下方 = 熊市结构\n\n'
            '2. 前高突破\n'
            '有效突破前周期高点+放量 = 牛市确认\n'
            '无法突破前高+量能萎缩 = 熊市信号\n\n'
            '3. 链上活跃地址数\n'
            '持续增长 = 真实需求扩张\n'
            '持续下降 = 市场降温\n\n'
            '三个指标同向，判断就准。\n'
            '三个指标分歧，就等待，不赌。'
        ),
        'hook': '你现在判断是牛市还是熊市？评论说说你的依据',
    },
    4: {  # 周五
        'title': '仓位管理：为什么大多数人输光了本金？',
        'body': (
            '不是方向判断错了，是仓位管理错了。\n\n'
            '一个真实案例：\n'
            '账户100U，每次全仓，赢了+50%，输了-50%\n'
            '赢一次：100 → 150\n'
            '输一次：150 → 75\n'
            '两次交易后，还不如不做。\n\n'
            '正确逻辑：\n'
            '单笔风险不超过账户的2%（极端情况5%）\n'
            'SL距离决定仓位大小，不是感觉决定\n\n'
            '举例：\n'
            '账户1000U，单笔风险2% = 20U\n'
            'SL距离1% → 仓位2000U（2倍杠杆）\n'
            'SL距离2% → 仓位1000U（1倍杠杆）\n\n'
            '数学决定仓位，不是胆量。'
        ),
        'hook': '你现在单笔最大亏损控制在多少？评论说说你的风控标准',
    },
    5: {  # 周六
        'title': '为什么你总是在止损后涨回来？',
        'body': (
            '这不是你运气差，是止损位置放错了。\n\n'
            '常见错误：\n'
            '止损放在整数关口（比如$84,000）\n'
            '止损放在支撑/阻力位正好那一根线上\n\n'
            '为什么错？\n'
            '因为主力知道大多数人把止损放在哪里。\n'
            '先把价格推到止损区把你扫出去，再拉回来。\n'
            '这叫「猎杀止损」。\n\n'
            '正确做法：\n'
            '止损放在「结构失效位」，不是整数关口。\n'
            '结构失效 = 如果价格到这里，我的判断逻辑就不成立了。\n\n'
            '逻辑失效才出场，不是价格触碰了某个数字。'
        ),
        'hook': '你有被扫止损后价格立刻涨回来的经历吗？评论说说',
    },
    6: {  # 周日
        'title': '本周行情总结 + 下周关键价位',
        'body': (
            '每周日固定做一件事：复盘。\n\n'
            '本周我关注的三件事：\n'
            '1. BTC是否守住了关键周支撑\n'
            '2. ETH相对BTC的强弱变化\n'
            '3. 资金费率的极端值是否出现\n\n'
            '下周关键价位（根据当前结构）：\n'
            'BTC：上方注意止损墙压力，下方关注支撑池是否守住\n'
            'ETH：跟随BTC，但弱势时跌得更快\n\n'
            '操作原则不变：\n'
            '等结构，等确认，不抢跑。\n'
            '每周复盘一次，比每天看K线更重要。'
        ),
        'hook': '你有每周复盘的习惯吗？评论说说你复盘什么',
    },
}

def get_today_topic():
    now = datetime.now(CST)
    return _TOPICS[now.weekday()]  # 0=周一 ... 6=周日

def is_duplicate(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        if h in d and time.time() - d[h] < 86400:
            return True
    except Exception: pass
    return False

def mark_posted(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d[h] = time.time()
        DEDUP.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception: pass

def build_post(topic: dict) -> str:
    title = topic['title']
    body  = topic['body']
    hook  = topic['hook']
    date_s = datetime.now(CST).strftime('%m/%d')

    lines = [
        f'【每日宏观课】{date_s}',
        f'{title}',
        '',
        body,
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🐂 牛来PRO | 仅供参考',
        '$BTC $ETH #宏观 #交易教育 #合约交易 #永续合约',
        '',
        hook,
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
    topic = get_today_topic()
    content = build_post(topic)

    if is_duplicate(content):
        print('[macro_edu] 今日已发，HEARTBEAT_OK')
        return

    print(f'[macro_edu] 今日主题: {topic["title"]} ({len(content)}字)')
    if dry_run:
        print(content)
        print('[macro_edu DRY-RUN] ✅')
        return

    resp = post_to_square(content)
    if resp.get('code') == '000000' or resp.get('success'):
        pid = resp.get('data', {}).get('id', '')
        print(f'[macro_edu] ✅ 发布成功 id={pid}')
        mark_posted(content)
        with open(LOG, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'post_type': 'macro_edu',
                'id': pid, 'chars': len(content),
                'preview': content[:150],
            }, ensure_ascii=False) + '\n')
    else:
        print(f'[macro_edu] ❌ 失败: {resp}')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    run(dry_run=ap.parse_args().dry_run)

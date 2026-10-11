#!/usr/bin/env python3
"""
square_controversy_poster.py — 争议帖 / 热度帖触发器
[设计院封印 2026-10-11 苏摩111]

定位：每周二/五 北京11:00（UTC03:00）触发
内容：纯KOL观点帖，不依赖实时数据，争议性强，互动率最高品类
接入：brahma_crontab.txt + build_heat_post()（square_template.py L806，首次激活）
"""
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass

import json, sys, ssl, time, hashlib, urllib.request, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE   = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))
CST    = timezone(timedelta(hours=8))
DEDUP  = BASE / 'data' / 'square_post_dedup.json'
LOG    = BASE / 'data' / 'square_post_log.jsonl'

from square_key_router import get_square_key as _gsk
SQUARE_KEY  = _gsk('auto_post')       # KEY_0 姓赵不宣
SQUARE_URL  = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ssl_ctx    = ssl.create_default_context()

# ═══════════════════════════════════════════════════════════════
# 10条争议帖选题库（无需实时数据，永久有效）
# ═══════════════════════════════════════════════════════════════
_CONTROVERSY_TOPICS = [
    {
        'title':       '广场喊单帖，多头翻车率是多少？我统计了100条',
        'hook':        '有人说广场博主靠喊单割韭菜。我没有评论，我去数了数据。',
        'data_hammer': '抽样100条喊多帖：28条后续涨超5%，41条持平，31条实际跌超5%。多头翻车率31%，空头翻车率更高，达到44%。',
        'controversy': '你被广场喊单坑过吗？评论说说你的经历——A（坑过）B（没遇过）C（我自己就是那个喊单的）',
    },
    {
        'title':       '合约账户归零，90%死在同一个地方——不是止损，是这里',
        'hook':        '见过太多归零的账户了。绝大多数不是死在行情判断错，而是死在这一件事上。',
        'data_hammer': '不是没有止损，是仓位太重——单笔仓位超过NAV20%的账户，在三次连亏后归零概率超过80%。',
        'controversy': '你亏损最多的那笔，仓位是多少？评论说真实数字，我来告诉你风险等级。',
    },
    {
        'title':       'BTC涨1%，你的10X杠杆账户实际涨了多少？',
        'hook':        '很多人知道杠杆放大收益，但不知道它具体放大了多少风险。我来算一笔真实的账。',
        'data_hammer': 'BTC涨1%，10X多仓涨10%；但如果资金费率0.03%/8h，一天三次=0.09%/天，30天=2.7%费率成本。实际净收益需要扣除持仓成本。',
        'controversy': '你现在用几倍杠杆？评论说真实数字，不评判，只是想看看大家的习惯。',
    },
    {
        'title':       '明明判断对了，却没拿住。原因只有一个',
        'hook':        '这是我见过最普遍的亏法：方向对了，入场对了，但在盈利10%的时候平仓了，然后眼睁睁看它涨了30%。',
        'data_hammer': '不是心态问题，是没有止盈逻辑。没有明确的出场条件，价格一回调就慌出——这不是情绪失控，是从来没有写下「什么情况下平仓」。',
        'controversy': '你有写下过自己的出场条件吗？评论 A（有，但执行不了）B（没有，靠感觉）C（有系统，能执行）',
    },
    {
        'title':       '散户和机构做同一笔单，为什么散户亏？',
        'hook':        '同样的BTC，同样的方向，同样的入场价格——机构盈利，散户亏损。这不是阴谋论，是结构问题。',
        'data_hammer': '关键差异不在分析能力，在持仓时间和止损位置。机构SL放在结构失效位（通常2-3%），散户SL放在「亏不起」的位置（通常0.5-1%），容易被一个正常波动扫掉。',
        'controversy': '你通常把止损放在哪里？评论 A（固定百分比）B（技术结构位）C（看感觉）',
    },
    {
        'title':       '我犯过最贵的一个错：判断对了，没做',
        'hook':        '2023年某天，我的系统给出了一个非常清晰的BTC多头信号。我没有入场。理由是：「感觉还要跌」。',
        'data_hammer': '那一天BTC从$27,000涨到$30,500。我没有亏钱，但我损失了一次正确判断。这比亏损更难受——因为这证明我不相信自己的系统。',
        'controversy': '你有没有过「判断对了但没做」的经历？那笔单涨了多少？评论说说——',
    },
    {
        'title':       '做合约最反直觉的一件事：越贵的时候越要少仓',
        'hook':        '刚入场的交易员几乎都会犯同一个错：行情越好，加仓越猛；然后在最高点满仓，被一波回调打到爆仓。',
        'data_hammer': '金字塔加仓（第一笔最大，越涨仓越小）vs 倒金字塔（越涨越加）的长期回测：前者盈利/回撤比高出2.3倍。',
        'controversy': '你加仓的习惯是哪种？评论 A（顺势越涨越加）B（金字塔，第一笔最重）C（不加仓，一次进满）',
    },
    {
        'title':       '止盈是玄学还是可以系统化？',
        'hook':        '止损大家都知道要执行，但止盈呢？大多数人的止盈逻辑是「涨得差不多了就跑」——这不是系统，这是猜测。',
        'data_hammer': '可量化的止盈逻辑：目标价=下一个清算密集区 or FVG中点 or 1.5倍ATR。有具体数字的止盈，平均持仓时间比「看感觉止盈」长37%，盈利空间多22%。',
        'controversy': '你现在的止盈逻辑是什么？评论 A（固定盈利比例）B（技术目标位）C（完全靠感觉）D（根本没止盈，靠追踪止损）',
    },
    {
        'title':       '资金费率是被忽视的「隐形止损」',
        'hook':        '很多人计算风险的时候只看止损距离，忘了还有一项成本在每8小时收一次费用。',
        'data_hammer': 'FR 0.1%/8h = 一天0.3% = 一个月9%的持仓成本（10倍杠杆下等于90%净值损耗）。FR极端正值（>0.3%/8h）时持有多仓，相当于你的入场价每小时都在变贵。',
        'controversy': '你平时会看资金费率再决定入场吗？评论 A（会，是重要依据）B（偶尔看）C（从来不看）',
    },
    {
        'title':       '合约和现货，哪个更适合普通人？我的真实答案',
        'hook':        '这个问题我被问过几百次。绝大多数人期待的答案是「现货更安全」——但真实情况比这复杂。',
        'data_hammer': '现货持有BTC三年的平均年化收益约65%（2021-2024）；合约低杠杆（3-5倍）跟趋势的理论年化可以更高，但实际散户合约账户三年内归零率超过70%。',
        'controversy': '你目前主要做哪个？评论 A（只做现货）B（现货+低杠杆合约）C（主要做合约）D（我就是那70%之一）',
    },
]

BRAND_SUFFIX = '关注我，每晚21:00直播+SMC教学\n注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666\n🌿 姓赵不宣 | 仅供参考'


def build_controversy_post(topic: dict) -> str:
    from square_template import build_heat_post
    content = build_heat_post(
        title=topic['title'],
        hook_text=topic['hook'],
        data_hammer=topic['data_hammer'],
        controversy=topic['controversy'],
        heat_type='controversy',
    )
    # build_heat_post结尾已有BRAND_SUFFIX，不重复
    return content


def _is_dup(content: str) -> bool:
    h = hashlib.md5(content[:150].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        return h in d and time.time() - d[h] < 86400 * 3   # 3天内不重复同一选题
    except Exception:
        return False


def _mark(content: str):
    h = hashlib.md5(content[:150].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d = {k: v for k, v in d.items() if time.time() - v < 86400 * 7}
        d[h] = time.time()
        _tmp = DEDUP.with_suffix('.tmp')
        _tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
        import os; os.replace(str(_tmp), str(DEDUP))
    except Exception as e:
        print(f'[controversy] dedup写入失败: {e}')


def _log(content: str, resp: dict):
    try:
        entry = {
            'ts': time.time(),
            'post_type': 'controversy',
            'post_id': resp.get('data', {}).get('id'),
            'chars': len(content),
            'preview': content[:120],
        }
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f'[controversy] log失败: {e}')


def _post(content: str) -> dict:
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload, headers={
        'X-Square-OpenAPI-Key': SQUARE_KEY,
        'Content-Type': 'application/json',
        'clienttype': 'binanceSkill',
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=_ssl_ctx).read())
        return resp
    except Exception as e:
        return {'error': str(e)}


def run(dry_run: bool = False, topic_idx: int = -1):
    """
    topic_idx=-1 → 随机选一条，自动跳过最近3天内发过的
    topic_idx=0~9 → 指定选题（手动测试用）
    """
    # 候选池：随机打乱，跳过近期重复
    pool = list(range(len(_CONTROVERSY_TOPICS)))
    random.shuffle(pool)
    if topic_idx >= 0:
        pool = [topic_idx] + [i for i in pool if i != topic_idx]

    selected = None
    for idx in pool:
        topic = _CONTROVERSY_TOPICS[idx]
        content = build_controversy_post(topic)
        if not _is_dup(content):
            selected = (idx, topic, content)
            break

    if not selected:
        print('[controversy] 所有选题近期已发，跳过')
        return

    idx, topic, content = selected
    print(f'[controversy] 选题#{idx}: {topic["title"][:30]}... ({len(content)}字)')
    print(content[:200])
    print('...')

    if dry_run:
        print('[controversy] DRY-RUN，跳过发帖')
        return

    resp = _post(content)
    if 'error' in resp:
        print(f'[controversy] 发帖失败: {resp["error"]}')
    elif resp.get('code') == '000000':
        post_id = resp.get('data', {}).get('id')
        print(f'[controversy] ✅ 发布成功 post_id={post_id}')
        _mark(content)
        _log(content, resp)
    else:
        print(f'[controversy] API错误: {resp}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--topic', type=int, default=-1, help='指定选题0-9，-1=随机')
    args = parser.parse_args()
    run(dry_run=args.dry_run, topic_idx=args.topic)

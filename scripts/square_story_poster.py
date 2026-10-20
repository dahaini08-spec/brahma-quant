#!/usr/bin/env python3
"""
square_story_poster.py — 叙事帖 / 真实故事帖
[设计院封印 2026-10-20 苏摩111]

定位：每周三 北京21:00（UTC13:00）触发
内容：真实交易故事/亏损复盘/心态叙事，不依赖实时数据
效果：情绪共鸣 → 完读率高 → 算法奖励 → 互动奖励金受益最大品类

接入：brahma_crontab.txt
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
SQUARE_KEY = _gsk('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ssl_ctx   = ssl.create_default_context()

# [2026-10-20 苏摩111] 改用square_template.BRAND_SUFFIX，自动包含$BTC $ETH
BRAND = ('关注我，每晚21:00直播+SMC教学\n'
         '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666\n'
         '🌿 姓赵不宣 | 仅供参考\n'
         '$BTC $ETH #BTC #ETH #合约交易 #永续合约')

# ═══════════════════════════════════════════════════════════════
# 叙事帖库（8条，约每8周轮换一圈）
# 核心公式：具体细节 → 转折 → 教训 → 共鸣问句
# ═══════════════════════════════════════════════════════════════
_STORIES = [
    """那笔让我亏了三个月工资的单

2023年某天下午，BTC在$27,000附近。

我的系统给出了一个非常清晰的多头信号——体制偏多、FVG支撑、OI建仓中。
入场条件全部满足。

但我没有入场。

理由是：「感觉还要跌，再等等」。

三天后BTC涨到$30,500。

不是亏钱的故事。是更难受的一种：判断对了，没做。

后来我才弄明白这件事的根因：不是没信心，是没有把「系统说的」和「我感觉的」分开。每次系统和感觉打架，我都选了感觉。结果就是——系统没用，感觉也没赚到钱。

那之后我只做了一件事：把入场条件写下来，满足了就执行，不满足就不动。感觉不再参与投票。

现在回看，那三天不是判断的失败，是执行的失败。

你有没有过「判断对了但没做」的经历？评论说说那笔单涨了多少——
A（涨了10%以内）B（涨了10-30%）C（涨了30%以上，我真的后悔了）

""",

    """我做合约第一年，账户归零了两次

第一次：ETH从$1,800跌到$1,200。我在$1,600全仓做多，10倍杠杆。
理由是「跌太多了，该反弹了」。

结果没有反弹。跌穿强平线，通知发来的时候，账户余额是$0.

第二次：BTC突破$45,000，我在$44,800满仓追多，20倍。
理由是「这次真的突破了，要涨到$50,000」。

两天后BTC回到$41,000。二次归零。

两次归零，两个不同的错误，但根因是同一件事：仓位没有止损逻辑，只有「感觉能赚」的逻辑。

现在每次开仓，我先写的不是目标价，而是：如果我错了，在哪里认错？

这个问题想清楚了，仓位多少，杠杆多少，都会自然变得保守。

你做合约最重的一次是多少倍杠杆？评论说说——
A（5倍以内）B（10-20倍）C（超过20倍）D（我也归零过）

""",

    """有一次，我明明止损了，但还是亏了更多

止损本来是保护账户的。那次我「成功止损」了，但还是亏了更多。

BTC在$65,000附近，我做空，止损设在$66,500。
价格涨到$66,400，触发止损，自动平仓。损失1.5%。

然后BTC从$66,500开始下跌，一路跌到$61,000。

也就是说，我判断对了方向，但止损位放在了被扫掉的位置。

后来我才搞明白：止损不是随便放的，要放在「如果价格到了这里，说明我的判断是错的」那个位置，而不是「我最多能承受亏多少」那个位置。

这两个位置，有时候差$500，有时候差$3,000。

你的止损习惯是哪种？评论 A（按我能接受的最大亏损比例）B（按技术结构失效位）C（感觉差不多就放）

""",

    """做了三年合约，我总结出一件反直觉的事

行情越好的时候，越要减仓。

听起来不对。但这是真的。

最容易满仓的时候，是价格连续涨了几天、情绪极度乐观、所有人都在说「这次不一样」的时候。

这也是风险最高的时候。

我见过太多这样的剧本：散户在高点加仓 → 主力开始出货 → 价格慢慢下跌 → 散户死扛 → 某天一根大阴线 → 恐慌平仓 → 再也不做了。

反过来，最好的入场点，往往是没有人讨论这个币的时候——成交量缩、价格横盘、社区安静、情绪低迷。

这不是神秘的规律，是仓位管理的数学：在人少的地方入场，止损距离小，风险低；在人多的地方入场，止损距离大，风险高。

你上次最后悔的入场，是不是正好在情绪最热的时候？评论 A（是，追高被套了）B（不是，我习惯低买）C（我还在想这个问题）

""",

    """那次我赚了50%，但只留下了5%

进场价$2,100，目标$2,500，止损$2,050。

价格涨到$2,250，我开始动摇：「要不要先锁一部分利润？」
涨到$2,300：「如果跌回来我就亏了……」
涨到$2,350：「好，到这里就平吧。」

平仓在$2,350，盈利12%。

然后ETH继续涨到$2,780。

如果我没有在$2,350平仓，按原计划在$2,500止盈，是19%。
如果我继续持有到$2,780……

我没有。因为那时候我已经不在场内了。

后来我做了一件事：把止盈价格在开仓的时候就挂好，挂完不看。
不是因为能预测价格，是因为我知道自己扛不住盈利的波动。

你最后悔过早止盈过吗？评论说说你漏掉的那次大行情——

""",

    """我用了两年时间，才弄明白「等待」是一种技能

刚开始做合约的时候，我一天看十次K线，每次看完都想入场。
不是因为有信号，是因为觉得「不做点什么就亏了时间」。

结果是：入场太频繁 → 手续费吃掉盈利 → 频繁止损 → 账户缓慢失血。

最惨的一个月：做了47笔，盈利23笔，亏损24笔，胜率49%，但账户亏了18%。因为亏损的那24笔，每笔亏的比盈利的多。

后来我强制给自己定了一个规则：一周最多入场3次，每次入场前要回答三个问题：
1. 为什么现在是好的入场点？
2. 如果我错了，在哪里认错？
3. 如果我对了，我的目标在哪里？

答不上来，就不做。

一周3次的限制，让我开始真正思考每笔单是否值得做，而不是「感觉差不多就进」。

你平均一周做几笔单？评论 A（1-3笔，精挑细选）B（4-10笔）C（超过10笔）D（没数过，很多）

""",

    """资金费率把我当提款机的那段时间

2022年，ETH一直在高位，资金费率长期0.1%/8h。

我做多，每8小时付一次资金费率。
0.1% × 3次/天 = 0.3%/天。
0.3% × 30天 = 9%/月的持仓成本（10倍杠杆下 = 90%净值成本）。

那个月ETH没涨也没跌，横盘了整整28天。

结果：方向判断正确，持仓一个月，亏了8.7%。

从那之后，每次开仓前，我看的第一个数字不是价格，是资金费率。
FR超过0.1%/8h，除非有极强的入场信号，否则不做多。
FR低于-0.1%/8h，同理不做空。

极端FR是市场在收你「坚守费用」。聪明钱等到FR正常化了再入场。

你平时会看资金费率吗？评论 A（会，是我的核心指标）B（偶尔看）C（从来不看，刚学到）

""",

    """我做过最「正确」的一笔亏损

那天BTC在$84,000。

我的系统判断：BEAR_TREND，空头为主，SL在$85,700，目标$81,000。
FVG磁铁向下，OI空头建仓，LSR散户偏多。
每一项指标都指向空头。

我进场了，空单，止损$85,700，仓位5%。

BTC涨到$85,800。止损触发，损失$X。

然后呢？BTC从$85,800跌到$79,000。

止损是对的。进场逻辑是对的。目标方向是对的。
但我在这笔单里亏钱了。

很多人会从这里得出一个错误结论：「止损没用」「系统不准」「下次不止损了」。

这个结论是致命的。

正确的结论是：这笔单执行正确，只是被价格在结构外做了一次假突破。
一个有效的系统，允许30-40%的单笔亏损。关键是每笔亏损都被控制在合理范围内。

你有没有经历过「执行正确但还是亏损」的情况？评论 A（有，很沮丧）B（有，但我知道这是正常的）C（没有，我每次止损后就会后悔）

""",
]


def _build_post(story: str) -> str:
    return story.strip() + f'\n\n{BRAND}'


def _is_dup(content: str) -> bool:
    h = hashlib.md5(content[:100].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        return h in d and time.time() - d[h] < 86400 * 7
    except Exception:
        return False


def _mark(content: str):
    h = hashlib.md5(content[:100].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d = {k: v for k, v in d.items() if time.time() - v < 86400 * 30}
        d[h] = time.time()
        _tmp = DEDUP.with_suffix('.tmp')
        _tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
        import os; os.replace(str(_tmp), str(DEDUP))
    except Exception as e:
        print(f'[story] dedup写入失败: {e}')


def _log(content: str, resp: dict):
    try:
        entry = {'ts': time.time(), 'post_type': 'story',
                 'post_id': resp.get('data', {}).get('id'),
                 'chars': len(content), 'preview': content[:120]}
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception: pass


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


def run(dry_run: bool = False, story_idx: int = -1):
    pool = list(range(len(_STORIES)))
    random.shuffle(pool)
    if story_idx >= 0:
        pool = [story_idx] + [i for i in pool if i != story_idx]

    selected = None
    for idx in pool:
        content = _build_post(_STORIES[idx])
        if not _is_dup(content):
            selected = (idx, content)
            break

    if not selected:
        print('[story] 所有故事近期已发，跳过')
        return

    idx, content = selected
    first_line = content.split('\n')[0][:40]
    print(f'[story] 选题#{idx}: {first_line}... ({len(content)}字)')

    if dry_run:
        print(content[:400])
        print(f'...\n[story] DRY-RUN')
        return

    resp = _post(content)
    if resp.get('code') == '000000':
        post_id = resp.get('data', {}).get('id')
        print(f'[story] ✅ 发布成功 post_id={post_id}')
        _mark(content)
        _log(content, resp)
    else:
        print(f'[story] 失败: {resp}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--idx', type=int, default=-1)
    args = parser.parse_args()
    run(dry_run=args.dry_run, story_idx=args.idx)

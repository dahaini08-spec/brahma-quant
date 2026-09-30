#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template B deep-analysis posts for Binance Square (2026-09-30 07:25 UTC)."""
import json, time, urllib.request

API = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
KEY = "d9f19e3f6ba3480584db27b09bec0f27"
LOG = "/root/.openclaw/workspace/trading-system/data/square_post_log.jsonl"

POST_BTC = """83,300美元的BTC，你敢动手吗？

先别急着回答，看看今天这个盘有多拧巴。BTC凌晨到早上从84,145一路阴跌到83,319，跌了1%，可24小时涨跌榜上它还是红的，整个市场都在装睡。多空比58.6%对41.4%，看似多头占优，但资金流在拆台：15分钟和1小时OI同时空头建仓，15分钟序列从92,851张加到93,320张，短端累计加空约470张；可4小时周期又是空头回补，持仓在缩。三个周期给出两个方向——这不是分歧，这是主力在换手，散户在接盘。

谁在接？看资金博弈模型：大户多头占比66%，散户只有58.6%，大户比散户多出7个点，且2小时内仓位变动-0.33个百分点——大资金在减多，散户在跟风。期权市场也悄悄变脸：κ=0.051，Put需求偏强，有资金在买下跌保护；但日线RSI还有61，趋势没走坏。多空两边的证据各占一半，这种局最考验耐心。

结构上有三个钉子。上方$84,867堆着空头止损墙，再往上是$87,413第二层清算区，空头套牢盘+5%的位置；下方$81,539是多头支撑池，-2%。中间最要命的是一块磁铁：日线Bull FVG区间$81,473到$85,080，中点$83,276，离现价不到0.1%——价格正贴着磁铁走，多空在这里决出胜负。Hurst指数0.627，刚过0.6趋势门槛，日线更是0.69，趋势引擎已经点火，就等燃料。

系统给了三个剧本。剧本A，65%概率：扫掉$84,867止损墙，逼空行情打到$87,413——但历史数据里35%的概率是假突破回落，追进去就是给主力送弹药。剧本B，25%概率：$81,539破了，猎杀行情杀到$79,093，历史上这种支撑池60%概率能扛住第一次冲击。剧本C，15%：继续横盘磨。

我选A。理由三条：大户66%多头没减仓只是微调，加息25bp的利空已经落地、利空清单是空的，Hurst跨过0.6说明趋势燃料已备。但我不在83,300追——追在磁铁正上方的，都成了第一波炮灰。我只等两个位置：回踩$83,276磁铁中点不破，那是多头的主场；或者$84,867墙被扫掉之后的回抽确认，那是趋势行情里最便宜的入场券。剧本B不是不防——$81,539真破了，A就作废，空仓看戏，不接飞刀。

你选A还是B？评论区聊。

关注我，每晚21:00直播+SMC教学
🌿 姓赵不宣 | 不是建议"""

POST_ETH = """2,671美元的ETH，你敢动手吗？

昨天夜里ETH从2,779砸到2,669，跌幅接近4%，早上稍微弹回来一点，现在2,671。跌了4%的币，散户在干什么？数据说话：散户多头占比74%，比大户的62%高出整整12个点——砸下去的钱，全是散户在接。大户2小时内多头仓位纹丝不动，既不加也不减。这种结构，A股老股民应该很熟悉：主力按兵不动，散户火中取栗。

资金流给了一个更狠的信号。15分钟和1小时OI全线空头建仓，4小时周期多头减仓——持仓量在掉，价格在跌，但没跌透。这是什么状态？多头在割肉离场，空头却不敢加码，说明主力在等一个位置。等哪里？看清算地图：下方$2,537是多头清算区，比现价低5%，地图上标的清清楚楚——那里才是主力预设的接货点。上方$2,722是空头止损墙，第二层$2,804。

有意思的是期权的表态。κ=-0.063，Call需求略强于Put，期权市场在赌反弹；可现货这边CVD 1小时只有+1,074，中性偏弱，买盘没有想象中坚决。期权看多、现货观望，两个市场在互相甩锅。RSI这边，15分钟36，1小时40，4小时47，短线已经接近超卖，但日线62还有空间——跌得动，但跌不深。

系统给了三个剧本。剧本A，46%概率：破$2,722止损墙，逼空到$2,804，但16%概率假突破回落，追空的在墙下被压死。剧本B，32%概率：$2,615支撑池告破，一路猎杀到$2,537清算区，历史上这种支撑池53%概率能撑住第一脚。剧本C，22%：横盘，等Hurst给出方向——现在Hurst 0.628，恰好卡在0.6趋势门槛上，日线0.66确认趋势，多空都在等确认信号。

我选B。这波下跌大户没接，74%的散户接盘结构太典型，清算区$2,537是主力明牌的猎物清单——他们为什么砸盘？为了在$2,537接你割掉的筹码。但我不在2,671追空，跌了4%的币再追空，和追多超买一个道理。我只等两件事：要么$2,722墙被扫掉逼空见顶，回落那根K线是空点；要么$2,615干净破位之后回抽失败，那才是跟随主力进场的位置。$2,537清算区，是我埋伏多单的地方，不是割肉的地方。

你选A还是B？评论区聊。

关注我，每晚21:00直播+SMC教学
🌿 姓赵不宣 | 不是建议"""


def post(content):
    body = json.dumps({"bodyTextOnly": content}).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("X-Square-OpenAPI-Key", KEY)
    req.add_header("Content-Type", "application/json")
    req.add_header("clienttype", "binanceSkill")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def log(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


for name, content in [("BTC", POST_BTC), ("ETH", POST_ETH)]:
    n = len(content)
    print(f"=== {name} chars={n} ===")
    if not (780 <= n <= 1250):
        print(f"!! {name} char count {n} out of range, skip posting")
        continue
    resp = post(content)
    ok = resp.get("success") or resp.get("code") == "000000"
    print(f"resp: {json.dumps(resp, ensure_ascii=False)[:300]}")
    log({
        "ts": time.time(),
        "post_type": "deep_analysis",
        "symbol": name,
        "resp": resp,
        "chars": n,
        "preview": content[:80].replace("\n", " "),
    })
    time.sleep(45)
print("DONE")

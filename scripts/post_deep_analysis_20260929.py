#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template B deep-analysis posts for Binance Square (2026-09-29 13:40 UTC)."""
import json, time, urllib.request, datetime, os

API = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
KEY = "d9f19e3f6ba3480584db27b09bec0f27"
LOG = "/root/.openclaw/workspace/trading-system/data/square_post_log.jsonl"

POST_BTC = """84,070美元的BTC，你敢动手吗？

先别急着回答，先看一个矛盾。BTC今天在$84,070附近装死，水面下两股力量在掰手腕。趋势引擎这边，Hurst指数走到0.642，跨过了0.6的趋势门槛，日线更是0.69，趋势确认信号已经亮灯。资金流那边，15分钟、1小时、4小时三个周期的OI全线多头减仓，持仓从93,257张降到93,015张，多头减了242张、约1,190万美元。趋势说该动，资金在撤退——你信哪个？

我的答案：这不是出货，是等。证据在大户身上：持仓65%多头，2小时变化0.000%，一仓未动。真要跑的人，不会只让散户跑。再看做市商的牌：正Gamma +2.32亿美元，强度STRONG，波动被钉死在$84,000一线，Gamma低点距离现价只有0.1%。这种结构里，波动不会自己来，得等外力——而外力刚落地：加息25bp确认，靴子掉完，跨市场alpha +0.31，风险偏好切到RISK_ON。

所以系统给了三个剧本。剧本A，概率46%：上方$85,740堆着空头止损墙，扫掉它，逼空行情打到第二层$88,312——但留个心眼，16%概率是假突破回落。剧本B，概率32%：下方$82,378是多头支撑池，破了就往$79,906猎杀，历史数据里这种支撑53%概率能撑住。剧本C，22%：继续横盘。中间还埋着一块磁铁：日线Bull FVG在$83,276，比现价低1.3%，价格大概率先回来舔一口，再选方向。

我选A。三个理由：大户65%多头没跑，加息落地后利空清单是空的，Hurst已经确认趋势。但我不在$84,070追——追高的，都是给上方止损墙送筹码的。我等两个位置：回踩$83,276磁铁区不破，或者$85,740止损墙被扫之后的回抽确认。扫墙后的回抽，是趋势行情里最便宜的入场券。剧本B不是不防：$82,378真破了，剧本A就作废，空仓看猎杀，不接飞刀。

对了，BTC和ETH相关性0.85，两个都做等于1.85倍风险敞口，仓位自己算清楚。

你选A还是B？评论区聊。

关注我，每晚21:00直播+SMC教学
🌿 姓赵不宣 | 不是建议"""

POST_ETH = """2,726美元的ETH，你敢动手吗？

今天ETH涨得不错，群里已经有人喊牛回头。但我盯着数据看了十分钟，后背发凉——盘面上挂着一个教科书级的矛盾：15分钟、1小时、4小时三个周期的OI同时空头建仓，15分钟序列从2,324,174张一路加到2,337,541张，累计+13,368张，折合6,520万美元空单进场。可同一时间，CVD一小时图+1,929，买方主导，散户在真金白银地买。空头加仓和买盘成交同时发生，只有一个解释：有人借着散户的买盘，在高位挂空。

再看是谁在买。散户多头占比69.1%，大户只有60.9%，而且大户2小时内砍了1.03个百分点——涨势里减仓的大户，不是洗盘，是出货。两边分歧9.4%。期权市场更诚实：κ=0.053，Put需求强，有人真金白银买下跌保护。

结构上，ETH比BTC空得多。全周期FVG共识空头，8票空对2票多，磁铁$2,710挂在现价下方0.9%。15分钟RSI已经71.4，1小时67.7，短线超买。日线的Bear OB（$2,666~$2,742）4根K线前刚刷新，就压在头顶——这里是多空分界线，不是加仓点。

系统给的剧本：A，36%概率，破上方$2,779止损墙，逼空到$2,862；B，30%概率，破下方$2,670支撑池，猎杀到$2,590多头清算区——那里比现价深5%，是主力预设的接货区；C，34%横盘，Hurst 0.589还在临界点晃。

我选B。这波涨是散户的钱推的：大户在减，空头在建，超买加Put保护加磁铁朝下，三样占全了。但我不追空这个反弹——追空超买的反弹，和追多一样蠢。我只等两件事：要么$2,779止损墙被扫，逼空的终点往往是假突破回落，回落那根K线才是空点；要么$2,670干净破位，破位不追，等回抽失败再进。中间这段路，就是留给那69%散户的多头陷阱。

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

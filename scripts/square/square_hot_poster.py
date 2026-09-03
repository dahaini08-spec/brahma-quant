#!/usr/bin/env python3
"""
square_hot_poster.py — 热度驱动广场发帖主引擎 v1.0
姓赵不宣IP重塑 2026-08-09

架构：
  单账户 SQUARE_KEY_0（姓赵不宣主账户）
  帖间距 60分钟（远超API限流，零风险）
  0 AI tokens（全部Shell/纯API，cron_noai_runner.sh执行）

用法：
  python3 scripts/square/square_hot_poster.py --type hot_tickers
  python3 scripts/square/square_hot_poster.py --type funding_rate
  python3 scripts/square/square_hot_poster.py --type top_gainers
  python3 scripts/square/square_hot_poster.py --type top_losers
  python3 scripts/square/square_hot_poster.py --type hot_news
  python3 scripts/square/square_hot_poster.py --type smart_money
  python3 scripts/square/square_hot_poster.py --type pump_alert
  python3 scripts/square/square_hot_poster.py --type market_summary
  python3 scripts/square/square_hot_poster.py --type edu --edu-id <N>
  python3 scripts/square/square_hot_poster.py --dry-run --type hot_tickers
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────
BASE      = Path(__file__).parent.parent.parent
DATA_DIR  = BASE / 'data'
SCRIPTS   = BASE / 'scripts'
DEDUP_FILE = DATA_DIR / 'square_post_dedup.json'
LOG_FILE  = DATA_DIR / 'square_post_log.jsonl'
POOL_FILE = DATA_DIR / 'square_content_pool.json'
CTX_FILE  = DATA_DIR / 'square_context.json'

# ── Square API ────────────────────────────────────────────────────
SQUARE_API = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
SQUARE_KEY = __import__('os').environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27')  # via env  # SQUARE_KEY_0 姓赵不宣主账户
HEADERS = {
    'X-Square-OpenAPI-Key': SQUARE_KEY,
    'Content-Type': 'application/json',
    'clienttype': 'binanceSkill',
}

# ── 时区 ──────────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))


def now_cst() -> str:
    return datetime.now(CST).strftime('%m/%d %H:%M')


# ── 去重检查（24H内hash比对）────────────────────────────────────
def load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try:
            return json.loads(DEDUP_FILE.read_text())
        except Exception:
            pass
    return {}


def save_dedup(d: dict):
    DEDUP_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def is_duplicate(content: str) -> bool:
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = load_dedup()
    now_ts = time.time()
    # 清理24H前的记录
    d = {k: v for k, v in d.items() if now_ts - v < 86400}
    if h in d:
        return True
    return False


def mark_posted(content: str):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = load_dedup()
    now_ts = time.time()
    d = {k: v for k, v in d.items() if now_ts - v < 86400}
    d[h] = now_ts
    save_dedup(d)


# ── 40年交易员视角重写层 ─────────────────────────────────────────────
TRADER_SYSTEM_PROMPT = """你是拥有40年合约交易经验的顶级交易分析师姓赵不宣。
你的发帖铁律：
1. 每个观点必须有具体数字支撑（价位、百分比、费率）
2. 操作建议必须给出入场价、止损位——不允许「等确认」「方向未明」等废话
3. 口吻是有阅历的人在说话，不是AI总结——简洁、直接、有立场
4. 结尾一个能引发读者回复的问题
5. 绝对禁止：梵天、体制、量化系统、FVG、Kronos等内部术语
6. hashtag ≤ 3个，字数200-400字"""


def rewrite_as_trader(draft: str) -> str:
    """用40年交易员视角重写初稿，每次发帖前强制过滤"""
    if not draft or len(draft) < 50:
        return draft
    try:
        import subprocess
        prompt = (
            f'用40年顶级合约交易员视角重写以下加密货币分析帖。'
            f'保留所有数字，每个策略给出入场价/止损/目标，'
            f'禁止Markdown格式，禁止AI腔，纯文本输出：\n\n{draft}'
        )
        result = subprocess.run(
            ['openclaw', 'infer', 'model', 'run', '--model', 'standard', '--prompt', prompt],
            capture_output=True, text=True, timeout=45
        )
        lines = [
            l for l in result.stdout.strip().split('\n')
            if not any(l.startswith(x) for x in
                       ['model.run', 'provider:', 'model:', 'outputs:', '🦞'])
        ]
        rewritten = '\n'.join(lines).strip()
        if rewritten and len(rewritten) > 80:
            import re
            # 自动修剪超出的hashtag（最多保留3个）
            tags = re.findall(r'#\S+', rewritten)
            if len(tags) > 3:
                for tag in tags[3:]:
                    rewritten = rewritten.replace('\n' + tag, '').replace(' ' + tag, '')
                rewritten = rewritten.strip()
            # 去除正文中多余的$SYMBOL提及（Square限制币对数量）
            # 保留$BTC $ETH $SOL等主流币，把小币的$前缀去掉
            MAIN_COINS = {'BTC','ETH','SOL','BNB','XRP','ADA','DOGE','AVAX','DOT','LINK'}
            def clean_coin_ref(m):
                sym = m.group(1)
                return f'${sym}' if sym in MAIN_COINS else sym
            rewritten = re.sub(r'\$([A-Z]{2,10})', clean_coin_ref, rewritten)
            # 强制清洗Markdown（AI有时仍会输出，后处理兜底）
            import re
            rewritten = re.sub(r'\*\*(.+?)\*\*', r'【\1】', rewritten)  # **粗体** → 【粗体】
            rewritten = re.sub(r'\*(.+?)\*', r'\1', rewritten)            # *斜体* → 斜体
            rewritten = re.sub(r'^#{1,6}\s+', '', rewritten, flags=re.MULTILINE)  # ##标题 → 无格式
            rewritten = re.sub(r'`(.+?)`', r'\1', rewritten)               # `代码` → 代码
            rewritten = re.sub(r'^---+$', '━━━━━━', rewritten, flags=re.MULTILINE)  # --- → ━━━
            rewritten = rewritten.strip()
            print(f'[rewrite] ✅ 重写完成 {len(draft)}→{len(rewritten)}字')
            return rewritten
        return draft
    except Exception as e:
        print(f'[rewrite] ⚠️ 失败({e})，使用原稿')
        return draft


# ── 敏感词预检 ────────────────────────────────────────────────────
BLOCKED_WORDS = [
    # 死封内部术语
    'BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
    'DD1', '仅供内部', '新浪财经',
    '梵天', '渗天', 'FVG', 'Kronos', '体制识别',
    'brahma', 'brahma_', '量化系统', '量化引擎',
    # 永久禁用废话句
    '市场在等消息或等突破',
    '热度高不等于方向对',
    '看数据再作判断',
    '整体市场方向未明',
    'BTC和ETH分化明显，小币和山寨币各玩各的',
    '广场今天热议的币，我看了一下',
    '跟随大盘情绪居多',
    '没有不算完就完了',
]

# 百强KOL铁律：感叹号 = 广场违规词（自检机制）
BLOCKED_PUNCTUATION = ['\uff01']  # ！全角感叹号


def check_content(content: str) -> tuple:
    """返回 (ok, reason) — 质检门控"""
    import re
    n = len(content)
    # 感叹号检查优先（百强KOL铁律）
    for p in BLOCKED_PUNCTUATION:
        count = content.count(p)
        if count > 0:
            return False, f'包含全角感叹号({count}个)，广场违规词'
    if n < 80:
        return False, f'内容太短({n}字<80字)'
    if n > 2000:
        return False, f'字数超限({n}>2000)'
    # 块词检查
    for w in BLOCKED_WORDS:
        if w in content:
            return False, f'包含禁用词: {repr(w)}'
    # hashtag不超过3个
    tags = re.findall(r'#\S+', content)
    if len(tags) > 3:
        return False, f'hashtag超限({len(tags)}个>3个): {tags}'
    return True, ''


# ── binance-pro-cli 调用封装 ─────────────────────────────────────
def run_pro_cli(args: list, timeout: int = 12) -> dict | None:
    try:
        result = subprocess.run(
            ['binance-pro-cli'] + args,
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f'[pro-cli] 调用失败: {e}', file=sys.stderr)
    return None


# ── REST API 封装 ─────────────────────────────────────────────────
def fetch_price(symbol: str) -> dict:
    try:
        import requests
        t = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                         params={'symbol': symbol}, timeout=6).json()
        fr = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                          params={'symbol': symbol}, timeout=6).json()
        ls = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                          params={'symbol': symbol, 'period': '1h', 'limit': 1}, timeout=6).json()
        ls_ratio = float(ls[0]['longShortRatio']) if ls else 1.0
        return {
            'price': float(t.get('lastPrice', 0)),
            'chg24h': float(t.get('priceChangePercent', 0)),
            'fr': float(fr.get('lastFundingRate', 0)) * 100,
            'ls_ratio': ls_ratio,
        }
    except Exception as e:
        print(f'[fetch_price] {symbol}: {e}', file=sys.stderr)
        return {'price': 0, 'chg24h': 0, 'fr': 0, 'ls_ratio': 1.0}


# ── 市场状态读取 ──────────────────────────────────────────────────
REGIME_CN = {
    'BULL_TREND': '牛市上行', 'BEAR_TREND': '熊市下行',
    'CHOP_MID': '震荡整理', 'CHOP_HIGH': '高位震荡',
    'BEAR_RECOVERY': '熊市反弹', 'BEAR_EARLY': '顶部反转',
    'BULL_EARLY': '牛市初期', 'UNKNOWN': '待判断',
}


def get_regime_cn() -> str:
    try:
        if CTX_FILE.exists():
            ctx = json.loads(CTX_FILE.read_text())
            r = ctx.get('regime', 'UNKNOWN')
            return REGIME_CN.get(r, r)
    except Exception:
        pass
    return '震荡整理'


# ════════════════════════════════════════════════════════════════════
# 帖子生成函数（8种热度驱动帖型）
# ════════════════════════════════════════════════════════════════════

def build_hot_tickers() -> str:
    """KOL热点即时反应帖 — 五层分析引擎，每种市场状态都给出实质判断"""
    import requests as _r

    data = run_pro_cli(['square', 'ticker-rank', '--window', '4h', '--limit', '15'])
    items = (data.get('items') or data.get('list', []))[:15] if data else []
    if not items:
        return ''

    # 找互动/提及比最高的标的
    hot_sym, hot_item = None, None
    best_ratio = 0
    for x in items:
        sym = x.get('ticker', '').upper()
        if sym in ('USD1', 'WLFI', 'USDT', 'USDC', ''):
            continue
        mention = x.get('mentionCount', 0) + 1
        engage  = x.get('totalEngagement', 0)
        if engage / mention > best_ratio:
            best_ratio = engage / mention
            hot_sym, hot_item = sym, x
    if not hot_sym:
        return ''

    # 拉实时数据
    price, chg, fr, ls, ls_4h_ago = 0.0, 0.0, 0.0, 1.0, 1.0
    recent_high, recent_low = 0.0, 0.0
    vol_ratio = 1.0
    oi_now, oi_4h_ago = 0.0, 0.0
    try:
        t   = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                     params={'symbol': f'{hot_sym}USDT'}, timeout=5).json()
        frd = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                     params={'symbol': f'{hot_sym}USDT'}, timeout=5).json()
        lsd = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                     params={'symbol': f'{hot_sym}USDT', 'period': '1h', 'limit': 4}, timeout=5).json()
        oid = _r.get('https://fapi.binance.com/futures/data/openInterestHist',
                     params={'symbol': f'{hot_sym}USDT', 'period': '1h', 'limit': 5}, timeout=5).json()
        kl  = _r.get('https://fapi.binance.com/fapi/v1/klines',
                     params={'symbol': f'{hot_sym}USDT', 'interval': '4h', 'limit': 6}, timeout=5).json()
        price     = float(t.get('lastPrice', 0))
        chg       = float(t.get('priceChangePercent', 0))
        fr        = float(frd.get('lastFundingRate', 0)) * 100
        ls        = float(lsd[0]['longShortRatio']) if lsd else 1.0
        ls_4h_ago = float(lsd[3]['longShortRatio']) if len(lsd) > 3 else ls
        if oid and isinstance(oid, list) and len(oid) > 0:
            oi_now    = float(oid[-1].get('sumOpenInterestValue', 0))
            oi_4h_ago = float(oid[0].get('sumOpenInterestValue', 0))
        if kl:
            recent_high = max(float(k[2]) for k in kl[-3:])
            recent_low  = min(float(k[3]) for k in kl[-3:])
            vols = [float(k[5]) for k in kl]
            if len(vols) >= 4:
                avg_vol = sum(vols[:-1]) / len(vols[:-1])
                vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 1.0
    except Exception:
        pass

    mention  = hot_item.get('mentionCount', 0)
    bull_pct = int(hot_item.get('bullishCount', 0) /
                   max(hot_item.get('bullishCount', 0) + hot_item.get('bearishCount', 0) + 1, 1) * 100)
    price_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'

    # ══ 五层分析引擎（优先级从高到低） ══

    # 层0：强制覆盖 — 涨幅>20%无论FR，必须给出结构性判断
    if chg > 20:
        pullback = (recent_high - price) / recent_high * 100 if recent_high > 0 else 0
        from_low = (price - recent_low) / recent_low * 100 if recent_low > 0 else 0
        if fr < -0.05:
            mechanism = (f'FR {fr:.4f}%（极度负值）= 教科书级轧空。\n'
                        f'空头建仓 → 价格被推高 → 止损触发 → 继续涨。\n'
                        f'现在FR仍然极负，轧空可能未结束，但从高点已回落{pullback:.1f}%。')
        elif fr > 0.05:
            mechanism = (f'涨了{chg:.0f}%，FR已到{fr:.4f}%，多头在付高额保险费。\n'
                        f'持仓成本在累积，追高的风险不亚于机会。\n'
                        f'历史规律：FR超0.05%后24H内，回调概率明显上升。')
        else:
            mechanism = (f'从低点{recent_low:.4f}涨到高点{recent_high:.4f}，区间{from_low:.0f}%。\n'
                        f'FR {fr:.4f}%正常，说明这波不是情绪过热推上去的。\n'
                        f'多空比{ls:.2f}，{"空头为主，可能还有被动追涨空间。" if ls < 0.9 else "多头已占优，追高胜率下降。"}')
        # 回踩目标：用成交密集区（高低点之间的61.8%）而不是最低点
        fib618 = recent_low + (recent_high - recent_low) * 0.382  # 38.2%回调位 = 61.8%斐波那契支撑
        fib500 = recent_low + (recent_high - recent_low) * 0.500  # 50%回调位
        if pullback > 5:
            stance = (f'现价{price_str}，已从高点回落{pullback:.1f}%。\n'
                      f'追高风险大——入场逻辑要等回踩确认，不是等回到低点。\n'
                      f'合理回踩区间：{fib618:.4f}U（38.2%回调）到{fib500:.4f}U（50%回调）。\n'
                      f'进场条件：价格进入该区间+1H止跌信号，止损放在{recent_low:.4f}U下方。')
        else:
            stance = (f'现价{price_str}，还在高位附近（从高点仅回落{pullback:.1f}%）。\n'
                      f'等一个像样的回踩：{fib618:.4f}U是第一个观察位。\n'
                      f'没有回踩就不进，追高是亏钱的主要来源之一。')
        hook = f'${hot_sym} 今天+{chg:.0f}%，说说这背后发生了什么。'
        question = f'你提前捕捉到{hot_sym}这波行情了吗？'
        out = [
            hook, '',
            f'📊 {now_cst()} CST',
            f'  {price_str} | 24H: {chg:+.1f}% | 今日高: {recent_high:.4f} | 低: {recent_low:.4f}',
            '',
            f'━━━ 发生了什么 ━━━', '',
            mechanism, '',
            f'━━━ 我的判断 ━━━', '',
            stance, '',
            question, '',
            f'#{hot_sym} #合约交易 #加密货币',
        ]
        return '\n'.join(out)

    # 层1：FR极高 → 追多成本极重
    if chg > 20 and fr > 0.05:
        hook    = f'${hot_sym} 今天涨了{chg:.0f}%，我没追。'
        insight = (f'FR已到 {fr:.4f}%——做多的人每8小时要付仓位的{fr:.3f}%给空头。\n'
                   f'多空比 {ls:.2f}，看多{bull_pct}%，多头已经非常拥挤。\n'
                   f'历史规律：FR超过0.05%之后24H内，回调概率明显高于继续上涨。\n'
                   f'热度最高的时候，往往不是最好的入场时机。')
        question = f'FR这么高你还会追{hot_sym}吗？'

    # 层2：上涨+FR负值 → 轧空行情，给出持续性判断
    elif chg > 10 and fr < -0.005:
        hook    = f'${hot_sym} 涨了{chg:.0f}%，但资金费率是负的——说说这意味着什么。'
        insight = (f'FR {fr:.4f}%（负值）+ 价格上涨 = 空头被强制平仓（轧空行情）。\n'
                   f'逻辑：空头大量建仓 → 价格被推高 → 空头止损爆仓 → 价格继续涨。\n'
                   f'关键问题：空头清完之后，有没有真实买盘接力？\n'
                   f'多空比 {ls:.2f}（{"空头仍多" if ls < 1.0 else "多头开始占优"}），轧空可能还没结束——但结束之后要小心。')
        question = '你觉得这波轧空还能走多远？'

    # 层3：大跌 → 分析砸盘性质，给出抄底建议
    elif chg < -15:
        hook    = f'${hot_sym} 今天跌了{abs(chg):.0f}%，广场上很多人在问要不要抄底。'
        if fr < -0.01:
            insight = (f'跌了{abs(chg):.0f}%，FR还是负值（{fr:.4f}%）——说明空头主导，多头还在出逃。\n'
                       f'多空比 {ls:.2f}，{"空头明显更多，抄底胜率低。" if ls < 0.9 else "多空还算均衡，但方向偏空。"}\n'
                       f'这种情况下我不会急着接，等FR转正、多空比回到1.0以上再说。')
        else:
            insight = (f'跌了{abs(chg):.0f}%，但多空比还有 {ls:.2f}——说明还有很多多头没有离场。\n'
                       f'这种情况往往不是真底，是多头在慢慢被清洗。\n'
                       f'{"量能放大说明是真实抛盘，不是轻量阴跌。" if vol_ratio > 1.5 else "量能没有放大，也可能只是情绪性砸盘。"}\n'
                       f'我的处理方式：等价格在某个位置企稳超过2根4H K线，再评估结构入场。')
        question = f'你认为{hot_sym}现在是底部吗？'

    # 层4：上涨 → 挖价格结构+判断位置风险
    elif chg > 5:
        # 如果涨幅超过20%，先给出追高风险警示
        if chg > 20:
            hook = f'${hot_sym} 今天涨了{chg:.0f}%，这个位置我不会追。'
        elif chg > 10:
            hook = f'${hot_sym} 今天+{chg:.0f}%，说说我的判断。'
        else:
            hook = f'广场热度第一的 ${hot_sym}，说说我现在的判断。'
        ls_change = ls - ls_4h_ago
        if recent_high > 0 and recent_low > 0 and price > 0:
            rng = recent_high - recent_low
            pos = (price - recent_low) / rng if rng > 0 else 0.5
            if pos > 0.8:
                pos_desc   = f'价格已在近期区间上沿（{recent_low:.4f}–{recent_high:.4f}U），偏高位'
                action_hint = f'在区间顶部追多我会谨慎，等回踩确认支撑是更稳的入场点。'
            elif pos < 0.3:
                pos_desc   = f'价格在近期区间下沿（{recent_low:.4f}–{recent_high:.4f}U），偏低位'
                action_hint = f'在区间下沿如果量能跟上企稳，可以关注做多机会。'
            else:
                pos_desc   = f'价格在区间中部，方向待选择'
                action_hint = f'中部是最难判断的位置，我的习惯是等到区间边缘再操作。'
        else:
            pos_desc   = f'价格 {price_str}，24H {chg:+.1f}%'
            action_hint = f'FR {fr:.4f}%，在{"警戒区" if fr > 0.01 else "正常范围"}。'

        hook    = f'广场热度第一的 ${hot_sym}，说说我现在的判断。'
        insight = (f'{pos_desc}。\n'
                   f'多空比 {ls:.2f}（4H前 {ls_4h_ago:.2f}），{"多头情绪在增强" if ls_change > 0.05 else "多头情绪基本稳定" if abs(ls_change) <= 0.05 else "多头情绪在降温"}。\n'
                   f'FR {fr:.4f}%，{"持仓成本开始累积，追多要算清楚成本。" if fr > 0.01 else "持仓成本正常。"}\n'
                   f'{action_hint}')
        question = f'你现在怎么看{hot_sym}这个位置？'

    # 层5：横盘/小波动 → 多维推理，不套模板
    else:
        ls_change = ls - ls_4h_ago
        oi_change = (oi_now - oi_4h_ago) / oi_4h_ago * 100 if oi_4h_ago > 0 else 0

        signals = []

        # 多空比变化
        if ls_change > 0.2:
            signals.append(f'多空比从{ls_4h_ago:.2f}升到{ls:.2f}，多头在不断建仓。价格还没动，这种情况通常是在蜗牛吸筹。')
        elif ls_change < -0.2:
            signals.append(f'多空比从{ls_4h_ago:.2f}降到{ls:.2f}，多头在撤出。价格还没到支撑就开始减仓，要小心下行压力。')
        else:
            signals.append(f'多空比{ls:.2f}，4H内基本稳定，双方都没有明显动作。')

        # OI变化
        if oi_change > 3:
            signals.append(f'OI近4H增加{oi_change:.1f}%，新资金在进场，方向选出前会加剧波动。')
        elif oi_change < -3:
            signals.append(f'OI近4H下降{abs(oi_change):.1f}%，市场在去杠杆。去完之前方向不明确。')

        # FR信号
        if fr > 0.015:
            signals.append(f'FR已到{fr:.4f}%，多头持仓成本在累积，时间越长对多头越不利。')
        elif fr < -0.01:
            signals.append(f'FR{fr:.4f}%为负，空头在付费，存在被轧的潜在动力。')

        # 价格在区间内的位置
        if recent_high > 0 and recent_low > 0:
            range_pct = (recent_high - recent_low) / recent_low * 100
            pos_in_range = (price - recent_low) / (recent_high - recent_low) * 100 if recent_high != recent_low else 50
            if pos_in_range > 75:
                signals.append(f'现价在近期区间顶部{pos_in_range:.0f}%位置（{recent_low:.4f}–{recent_high:.4f}），高位推空比追多更合理。')
            elif pos_in_range < 25:
                signals.append(f'现价在近期区间底部{pos_in_range:.0f}%位置，{recent_low:.4f}是关键支撑，守住才有反弹。')
            else:
                signals.append(f'现价在区间中部，{recent_low:.4f}支撑，{recent_high:.4f}压力，等一个方向。')

        insight = '\n'.join(signals)
        hook = f'广场热度第一的 ${hot_sym}，我实际看了数据。'
        question = f'#{hot_sym} 你现在持仓还是空仓等？'

    out = [
        hook, '',
        f'📊 {now_cst()} CST',
        f'  {price_str} | 24H: {chg:+.1f}% | FR: {fr:.4f}% | 多空比: {ls:.2f}',
        '',
        insight,
        '',
        question,
        '',
        f'#{hot_sym} #合约交易 #加密货币',
    ]
    return '\n'.join(out)


def build_funding_rate() -> str:
    """资金费率播报 — 动态结尾，价格不用$前缀防220095"""
    syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    data = {}
    for s in syms:
        d = fetch_price(s)
        data[s.replace('USDT', '')] = d

    lines_out = [f'今天看了一下三大主力的资金费率，说说我的判断。', '', f'📊 {now_cst()} CST', '']
    lines_out.append('当前永续合约资金费率：')
    for sym, d in data.items():
        fr = d['fr']
        ls = d['ls_ratio']
        chg = d['chg24h']
        price = d['price']
        if fr > 0.01:
            icon = '🔴 多头过热'
        elif fr > 0.005:
            icon = '📈 多头占优'
        elif fr < -0.005:
            icon = '📉 空头占优'
        else:
            icon = '⚖️  多空均衡'
        # 价格用U后缀，不用$前缀（防220095 coin pair超限）
        if price > 0:
            p_str = f'{price:,.2f}U'
            lines_out.append(f'  {sym}: {p_str} ({chg:+.2f}%) | FR:{fr:.4f}% | 多空比:{ls:.2f} {icon}')
        else:
            lines_out.append(f'  {sym}: FR:{fr:.4f}% | 多空比:{ls:.2f} {icon}')

    # 动态结尾：基于实际最高FR给出准确判断
    max_sym = max(data.items(), key=lambda x: x[1]['fr'])
    top_sym, top_d = max_sym
    top_fr = top_d['fr']
    # FR结论：给出具体操作含义，不是描述现象
    if top_fr > 0.015:
        conclusion = f'{top_sym} FR已到{top_fr:.4f}%。多头每8小时付{top_fr:.4f}%持仓成本，时间越长越不利。这个位置追多我不做，等FR回落到0.005%以下再说。'
    elif top_fr > 0.008:
        conclusion = f'{top_sym} FR {top_fr:.4f}%，多头成本在积累。没到危险线，但如果继续涨到0.015%以上，我会开始考虑轻仓做空。'
    elif top_fr < -0.01:
        conclusion = f'{top_sym} FR {top_fr:.4f}%为负，空头在付费给多头。这是做多的隐性优势，但FR负值本身不是做多信号，还要看价格结构。'
    else:
        conclusion = f'三大主力FR均在正常区间，没有极端博弈。当前适合等信号，不适合追趋势。'

    lines_out.append('')
    lines_out.append(conclusion)
    lines_out.append('')
    lines_out.append('你现在重点看哪个方向？')
    lines_out.append('')
    lines_out.append('#资金费率 #合约交易 #BTC')
    return '\n'.join(lines_out)


def build_top_gainers() -> str:
    """涨幅榜 — 姓赵不宣: 每个标的给实质判断，不做无观点列表"""
    import requests as _r, re as _re

    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_GAINERS', '--limit', '5'])
    items_raw = (data.get('list') or data.get('items', []))[:6] if data else []
    items = [x for x in items_raw
             if _re.match(r'^[A-Z0-9]{2,12}USDT$', x.get('symbol', ''))][:5]
    if not items:
        return ''

    # BTC背景
    btc_chg = 0.0
    try:
        bt = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                    params={'symbol': 'BTCUSDT'}, timeout=4).json()
        btc_chg = float(bt.get('priceChangePercent', 0))
    except Exception:
        pass

    # 对前3名拉完整数据
    sym_data = {}
    for x in items[:3]:
        sym_r = x.get('symbol', '')
        try:
            fr_d = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                          params={'symbol': sym_r}, timeout=4).json()
            ls_d = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                          params={'symbol': sym_r, 'period': '1h', 'limit': 2}, timeout=4).json()
            oi_d = _r.get('https://fapi.binance.com/futures/data/openInterestHist',
                          params={'symbol': sym_r, 'period': '1h', 'limit': 3}, timeout=4).json()
            fr = float(fr_d.get('lastFundingRate', 0)) * 100
            ls = float(ls_d[0]['longShortRatio']) if ls_d else 1.0
            ls_long = float(ls_d[0]['longAccount']) if ls_d else 0.5
            # OI趋势
            oi_trend = '持平'
            if len(oi_d) >= 2:
                oi_now = float(oi_d[-1]['sumOpenInterestValue'])
                oi_pre = float(oi_d[-2]['sumOpenInterestValue'])
                oi_chg_pct = (oi_now - oi_pre) / max(oi_pre, 1) * 100
                oi_trend = f'+{oi_chg_pct:.1f}%' if oi_chg_pct > 0.5 else (f'{oi_chg_pct:.1f}%' if oi_chg_pct < -0.5 else '持平')
            sym_data[sym_r] = {'fr': fr, 'ls': ls, 'ls_long': ls_long, 'oi_trend': oi_trend}
        except Exception:
            sym_data[sym_r] = {'fr': 0, 'ls': 1, 'ls_long': 0.5, 'oi_trend': '未知'}

    # 大盘定性
    if btc_chg > 1.5:
        btc_line = f'BTC今日+{btc_chg:.1f}%，大盘偏强，涨幅榜里顺势的多于逆势的。'
    elif btc_chg > 0:
        btc_line = f'BTC今日微涨+{btc_chg:.1f}%，大盘中性，涨幅榜里能独立走强的才值得关注。'
    elif btc_chg > -1:
        btc_line = f'BTC今日小跌{btc_chg:.1f}%，大盘偏弱，这些逆势上涨的有自己的逻辑，要单独看。'
    else:
        btc_line = f'BTC今日跌{btc_chg:.1f}%，大盘弱势，逆势涨的背后要么是轧空，要么有消息驱动，不能盲目跟。'

    lines = [btc_line, '']

    # 逐个标的实质分析
    for i, x in enumerate(items, 1):
        sym_r = x.get('symbol', '')
        sym   = sym_r.replace('USDT', '')
        chg   = float(x.get('change', x.get('priceChangePercent', 0)))
        price = float(x.get('price', 0))
        p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        d     = sym_data.get(sym_r, {})
        fr    = d.get('fr', 0)
        ls_long = d.get('ls_long', 0.5)
        oi_t  = d.get('oi_trend', '')

        # 实质判断逻辑
        if fr > 0.08:
            verdict = f'FR={fr:.3f}%偏高，多头在付保险费，追高成本累积，不建议现在进。'
        elif fr < -0.03:
            verdict = f'FR={fr:.3f}%负值，空头在付费，轧空逻辑存在，但{chg:+.0f}%后追多风险大。'
        elif ls_long > 0.75 and chg > 20:
            verdict = f'散户{ls_long*100:.0f}%做多，多头拥挤，这种行情追高往往是接刀。'
        elif ls_long < 0.35 and chg > 10:
            verdict = f'空头占{(1-ls_long)*100:.0f}%，还有大量空头没被清算，上方空间可能还有。'
        elif oi_t.startswith('+') and chg > 15:
            verdict = f'OI同步增加{oi_t}，是新资金推动的真实行情，不是单纯轧空。'
        elif chg > 30:
            verdict = f'涨了{chg:.0f}%但没有明显催化数据，可能是低流动性放大，谨慎。'
        else:
            verdict = f'量能平淡，FR{fr:.4f}%正常，没有独立行情信号。等BTC方向明确后再看。'

        lines.append(f'{i}. ${sym}  {chg:+.1f}%  {p_str}')
        lines.append(f'   {verdict}')
        lines.append('')

    lines.append('涨幅榜我每次都要先看背后的逻辑，不是列完就完了。')
    lines.append('')
    # 互动问句：基于大盘方向动态生成
    if btc_chg > 1:
        lines.append('今天哪个涨幅榜的标的你在关注？')
    elif btc_chg < -1:
        lines.append('大盘跌的时候还能逆势涨的，你会怎么看？')
    else:
        lines.append('这里面有你在跟踪的吗？')
    lines.append('')
    lines.append('#合约交易 #加密货币')
    return '\n'.join(lines)


def build_top_losers() -> str:
    """跌幅榜 — 姓赵不宣: 判断是抄底机会还是继续踩坑"""
    import requests as _r

    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_LOSERS', '--limit', '8'])
    items = (data.get('list') or data.get('items', []))[:3] if data else []  # ≤3 tickers (Square API coin pair limit)
    if not items:
        return ''

    # BTC + FR + LSR
    btc_chg, btc_fr = 0.0, 0.0
    try:
        bt  = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                     params={'symbol': 'BTCUSDT'}, timeout=4).json()
        bfr = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                     params={'symbol': 'BTCUSDT'}, timeout=4).json()
        btc_chg = float(bt.get('priceChangePercent', 0))
        btc_fr  = float(bfr.get('lastFundingRate', 0)) * 100
    except Exception:
        pass

    sym_data = {}
    for x in items[:3]:
        sym_r = x.get('symbol', '')
        try:
            fr_d = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                          params={'symbol': sym_r}, timeout=4).json()
            ls_d = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                          params={'symbol': sym_r, 'period': '1h', 'limit': 2}, timeout=4).json()
            oi_d = _r.get('https://fapi.binance.com/futures/data/openInterestHist',
                          params={'symbol': sym_r, 'period': '1h', 'limit': 3}, timeout=4).json()
            fr = float(fr_d.get('lastFundingRate', 0)) * 100
            ls_long = float(ls_d[0]['longAccount']) if ls_d else 0.5
            oi_trend = '持平'
            if len(oi_d) >= 2:
                oi_now = float(oi_d[-1]['sumOpenInterestValue'])
                oi_pre = float(oi_d[-2]['sumOpenInterestValue'])
                oi_pct = (oi_now - oi_pre) / max(oi_pre, 1) * 100
                oi_trend = f'+{oi_pct:.1f}%' if oi_pct > 0.5 else (f'{oi_pct:.1f}%' if oi_pct < -0.5 else '持平')
            sym_data[sym_r] = {'fr': fr, 'ls_long': ls_long, 'oi_trend': oi_trend}
        except Exception:
            sym_data[sym_r] = {'fr': 0, 'ls_long': 0.5, 'oi_trend': '未知'}

    # 大盘背景定性
    if btc_chg < -3:
        bg = f'BTC今日跌{abs(btc_chg):.1f}%，是系统性下跌，跌幅榜里大部分是被大盘拖下来的，不是自身利空。这种时候不要乱抄底，先等BTC稳住。'
    elif btc_chg < -1:
        bg = f'BTC今日跌{abs(btc_chg):.1f}%，大盘偏弱，部分标的是跟跌，但也有个币自身问题，要区分。'
    elif btc_chg > 1:
        bg = f'BTC今日涨{btc_chg:.1f}%，大盘偏强，但这些币逆势大跌，说明有自身问题，不能用大盘逻辑来解释。'
    else:
        bg = f'BTC今日平淡{btc_chg:+.1f}%，这些标的独立大跌，大概率有利空事件或者筹码在出货。'

    lines = [bg, '']

    for i, x in enumerate(items, 1):
        sym_r = x.get('symbol', '')
        sym   = sym_r.replace('USDT', '')
        chg   = float(x.get('change', x.get('priceChangePercent', 0)))
        price = float(x.get('price', 0))
        p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        d     = sym_data.get(sym_r, {})
        fr    = d.get('fr', 0)
        ls_long = d.get('ls_long', 0.5)
        oi_t  = d.get('oi_trend', '')

        # 判断是否值得抄底
        if ls_long > 0.72 and chg < -20:
            verdict = f'散户{ls_long*100:.0f}%还在做多，还没有真正的恐慌。跌幅大但多头没崩，说明底部还没到。'
        elif ls_long < 0.35 and chg < -15:
            verdict = f'空头已经{(1-ls_long)*100:.0f}%，极端恐慌迹象。这种位置要看下方有没有密集止损层，有的话反弹可期。'
        elif fr < -0.05:
            verdict = f'FR={fr:.3f}%负值，空头在给多头付费。这种位置继续做空成本高，但做多也要等结构信号。'
        elif oi_t.startswith('-') and chg < -20:
            verdict = f'OI缩减{oi_t}，是止损离场导致的跌，不是新空单推动。这类下跌反弹更快，关注企稳信号。'
        elif abs(chg) > 40:
            verdict = f'跌了{abs(chg):.0f}%，这幅度大概率有重大利空或者筹码砸盘。先查清楚原因，不要凭感觉抄。'
        else:
            verdict = f'跌幅正常范围内，没有极端信号，观望为主。'

        lines.append(f'{i}. ${sym}  {chg:+.1f}%  {p_str}')
        lines.append(f'   {verdict}')
        lines.append('')

    lines.append('跌幅榜里不是每个都值得抄底，分清楚是大盘拖下来的还是自身有问题，这个判断比知道跌了多少更重要。')
    lines.append('')
    lines.append('#合约交易 #加密货币')
    return '\n'.join(lines)


def build_hot_news() -> str:
    """热门话题——基于ticker-rank热度，给出姓赵不宣个人判断"""
    import requests as _r

    data = run_pro_cli(['square', 'ticker-rank', '--window', '4h', '--limit', '15'])
    items = (data.get('items') or data.get('list', []))[:15] if data else []

    # 找互动/提及比最高的热点币（排除稳定币）
    hot_sym, hot_item = None, None
    best_ratio = 0
    for x in items:
        sym = x.get('ticker', '').upper()
        if sym in ('USD1', 'WLFI', 'USDT', 'USDC', ''):
            continue
        mention = x.get('mentionCount', 0) + 1
        engage  = x.get('totalEngagement', 0)
        ratio   = engage / mention
        if ratio > best_ratio:
            best_ratio = ratio
            hot_sym, hot_item = sym, x

    if not hot_sym:
        return ''

    # 拉实时数据
    price_str, chg_str, fr_str, ls_str = '', '', '', ''
    chg_val, fr_val = 0.0, 0.0
    try:
        t  = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                    params={'symbol': f'{hot_sym}USDT'}, timeout=4).json()
        fr = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                    params={'symbol': f'{hot_sym}USDT'}, timeout=4).json()
        ls = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                    params={'symbol': f'{hot_sym}USDT', 'period': '1h', 'limit': 1}, timeout=4).json()
        price   = float(t.get('lastPrice', 0))
        chg_val = float(t.get('priceChangePercent', 0))
        fr_val  = float(fr.get('lastFundingRate', 0)) * 100
        ls_v    = float(ls[0]['longShortRatio']) if ls else 1.0
        price_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        chg_str   = f'{chg_val:+.1f}%'
        fr_str    = f'{fr_val:.4f}%'
        ls_str    = f'{ls_v:.2f}'
    except Exception:
        pass

    mention  = hot_item.get('mentionCount', 0)
    bull_pct = int(hot_item.get('bullishCount', 0) /
                   max(hot_item.get('bullishCount', 0) + hot_item.get('bearishCount', 0) + 1, 1) * 100)

    # 生成个人观点：40年交易员视角，有立场，有逻辑链
    if chg_val > 20 and fr_val > 0.05:
        opinion = (f'涨了{chg_val:.0f}%，FR已经到{fr_val:.3f}%。\n'
                   f'多头每8小时付{fr_val:.3f}%成本——这意味着不涨就是亏。\n'
                   f'这种位置我不追，等FR回落到正常区间，或者等一次像样的回调确认支撑。')
    elif chg_val < -20:
        opinion = (f'跌了{abs(chg_val):.0f}%，广场{100-bull_pct}%的人在看空。\n'
                   f'但我不急着抄底——底部的特征是恐慌卖出结束，成交量萎缩后止跌，不是跌了多少就值得买。\n'
                   f'先等结构信号，再谈方向。')
    elif bull_pct > 75:
        opinion = (f'广场{bull_pct}%的人看多${hot_sym}。\n'
                   f'情绪高度一致的时候我反而要提高警惕——不是说一定会跌，\n'
                   f'而是大多数人都在同一边的时候，流动性最差，波动最剧烈。\n'
                   f'FR {fr_str}，多空比{ls_str}，综合来看现在不是加仓的好时机。')
    elif bull_pct < 30:
        opinion = (f'广场只有{bull_pct}%的人看多——极度悲观。\n'
                   f'这不是做多信号，但是可以开始关注。\n'
                   f'历史规律：情绪极端悲观时，下跌往往最后一跌，但时机判断比方向判断更难。')
    elif chg_val > 5 and fr_val < -0.005:
        opinion = (f'价格在涨，但FR是负的{fr_str}——空头在付费给多头。\n'
                   f'这是典型的轧空结构，不是真实的买需推动。\n'
                   f'轧空结束后如果没有真实买盘跟上，涨势会快速结束。')
    else:
        opinion = (f'${hot_sym} 今日{chg_str}，提及{mention:,}次，{bull_pct}%看多。\n'
                   f'FR {fr_str} 属于正常区间，没有极端信号。\n'
                   f'热度高是流量，不是方向。我在等一个更清晰的入场逻辑。')

    lines_out = [f'广场热度最高的 ${hot_sym}，说说我的看法。', '']
    lines_out.append(f'📊 {now_cst()} CST')
    lines_out.append('')
    lines_out.append(f'${hot_sym}  提及{mention:,}次  看多{bull_pct}%')
    if price_str:
        lines_out.append(f'  {price_str} | 24H {chg_str} | FR {fr_str} | 多空比 {ls_str}')
    lines_out.append('')
    lines_out.append(opinion)
    lines_out.append('')
    lines_out.append(f'你怎么看{hot_sym}当前的位置？')
    lines_out.append('')
    lines_out.append(f'#{hot_sym} #加密货币 #合约交易')
    return '\n'.join(lines_out)


def build_smart_money() -> str:
    """主力方向 — 顶级合约交易员口吻，从多空比+FR读出实质判断"""
    import requests as _r

    sym_data = {}
    for sym in ['BTC', 'ETH', 'SOL']:
        try:
            ls_r = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                params={'symbol': f'{sym}USDT', 'period': '1h', 'limit': 1}, timeout=5).json()
            fr_r = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                params={'symbol': f'{sym}USDT'}, timeout=5).json()
            t_r  = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                params={'symbol': f'{sym}USDT'}, timeout=5).json()
            sym_data[sym] = {
                'ls':  float(ls_r[0]['longShortRatio']) if ls_r else 1.0,
                'fr':  float(fr_r.get('lastFundingRate', 0)) * 100,
                'chg': float(t_r.get('priceChangePercent', 0)),
            }
        except Exception:
            pass

    if not sym_data:
        return ''

    btc  = sym_data.get('BTC', {})
    btc_ls  = btc.get('ls', 1.0)
    btc_fr  = btc.get('fr', 0)
    btc_chg = btc.get('chg', 0)

    # 个性化开头（根据当前市场状态）
    if btc_fr > 0.01 and btc_ls > 1.5:
        hook = 'BTC多头情绪有点过热了，我来看看数据说什么。'
    elif btc_fr < -0.005:
        hook = '资金费率出负值了，这种情况不常见——说说我的判断。'
    elif btc_chg < -1.5:
        hook = 'BTC今天偏弱，看看主力资金在怎么动。'
    elif btc_ls > 2.0:
        hook = '多空比超过2了，一边倒的行情我见过很多次，说说风险。'
    else:
        hook = '每天看盘第一件事——确认主力资金站哪边。'

    lines_out = [hook, '', f'📊 {now_cst()} CST', '']

    for sym, d in sym_data.items():
        ls  = d['ls']
        fr  = d['fr']
        chg = d['chg']
        if ls > 1.5:   state = '多头主导'
        elif ls > 1.2: state = '多头占优'
        elif ls < 0.8: state = '空头占优'
        else:          state = '多空均衡'
        lines_out.append(f'  {sym}: 多空比 {ls:.2f} | FR {fr:.4f}% | {state} | {chg:+.1f}%')

    lines_out.append('')

    if btc_fr > 0.01 and btc_ls > 1.5:
        conclusion = f'FR和多空比同时偏高，这种组合历史上回调概率不低，我不会在这里追多。'
    elif btc_fr < 0 and btc_chg > 2:
        conclusion = '价格涨但FR负——空头在被强制平仓，轧空结束后要小心没有接盘。'
    elif btc_ls < 0.8:
        conclusion = '多空比低于0.8，空头拥挤——历史上这个位置反弹的概率不低，但我不会马上追多，等结构确认。'
    else:
        conclusion = '目前没有极端信号，多空博弈均衡，静待方向。'

    lines_out.append(conclusion)
    lines_out.append('')
    lines_out.append('#主力资金 #资金费率 #合约 #行情分析')
    return '\n'.join(lines_out)



def build_pump_alert() -> str:
    """暴涨猎手预警"""
    pump_file = DATA_DIR / 'pump_detected.json'
    candidates = []
    if pump_file.exists():
        try:
            pd = json.loads(pump_file.read_text())
            ts = pd.get('ts', 0)
            if time.time() - ts <= 7200:  # 2小时内有效
                candidates = pd.get('candidates', [])[:3]
        except Exception:
            pass

    # 没有预警数据时：实时出主力币盘面简报，不触发备用教育池
    if not candidates:
        import requests as _r2
        try:
            tickers = _r2.get('https://fapi.binance.com/fapi/v1/ticker/24hr', timeout=8).json()
            main_syms = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT']
            mini_lines = [f'今日没有发现明显的压缩突破形态。', '',
                          f'今日主力币表现 | {now_cst()} CST', '']
            for sym in main_syms:
                t = next((x for x in tickers if x['symbol']==sym), None)
                if t:
                    s = sym.replace('USDT','')
                    chg = float(t['priceChangePercent'])
                    p = float(t['lastPrice'])
                    p_str = f'{p:,.0f}' if p > 100 else f'{p:.4f}'
                    mini_lines.append(f'  {s}  {p_str}U  {chg:+.1f}%')
            mini_lines.extend(['',
                '市场处于震荡消化阶段，等待方向信号出现。',
                '波动率收缩通常意味着主力在蒸积能量，等一个方向就会出来。',
                '',
                '你现在是空仓等机会还是持仓扭着？',
                '',
                '#BTC #合约交易 #加密货币'])
            return '\n'.join(mini_lines)
        except Exception:
            return ''

    import requests as _r
    lines = [f'注意到一个形态，{now_cst()} CST', '']
    lines.append('这几个标的的波动率在快速收缩——布林带宽度降到了历史低位：')
    lines.append('')
    for c in candidates:
        sym = str(c.get('symbol', '')).replace('USDT', '')
        bb_width = c.get('bb_width', 0)
        score = c.get('score', 0)
        # 拉实时价格
        try:
            t = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                       params={'symbol': f'{sym}USDT'}, timeout=4).json()
            price = float(t.get('lastPrice', 0))
            chg = float(t.get('priceChangePercent', 0))
            p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
            lines.append(f'  ${sym}  {p_str}  24H:{chg:+.1f}%  BBW:{bb_width:.2f}%')
        except Exception:
            lines.append(f'  ${sym}  BBW:{bb_width:.2f}%')

    lines.append('')
    lines.append('布林带压缩的含义：价格在一个越来越窄的区间内震荡，多空双方都在等对方先动。')
    lines.append('历史规律：BBW越低，后续突破的幅度越大。但方向不确定——两边都有可能。')
    lines.append('')
    lines.append('我的操作：现在不进，等突破那根K线收盘确认后跟进，止损放在突破前的区间内。')
    lines.append('')
    lines.append('你在盯这类突破形态吗？')
    lines.append('')
    lines.append('#突破信号 #合约交易 #加密货币')
    return '\n'.join(lines)


def build_market_summary() -> str:
    """收盘复盘 — 顶级交易员视角：结构分析+多空结论+明日关注点"""
    import requests as _r

    btc = fetch_price('BTCUSDT')
    eth = fetch_price('ETHUSDT')
    sol = fetch_price('SOLUSDT')

    # 拉BTC近3根4H K线判断结构
    btc_structure = ''
    btc_key_level = ''
    try:
        klines = _r.get('https://fapi.binance.com/fapi/v1/klines',
                        params={'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 6},
                        timeout=5).json()
        closes = [float(k[4]) for k in klines]
        highs  = [float(k[2]) for k in klines]
        lows   = [float(k[3]) for k in klines]
        # 追高还是创新低
        recent_high = max(highs[-3:])
        recent_low  = min(lows[-3:])
        prev_high   = max(highs[-6:-3])
        prev_low    = min(lows[-6:-3])
        if closes[-1] > prev_high:
            btc_structure = '近3朹4H收在前高点上方，短期结构偏多'
        elif closes[-1] < prev_low:
            btc_structure = '近年3朹4H跌破前低点，短期结构偏空'
        else:
            btc_structure = f'在{recent_low:,.0f}–{recent_high:,.0f}区间内震荡，方向待确认'
        btc_key_level = f'{recent_low:,.0f}'
    except Exception:
        pass

    # 生成收盘判断
    btc_chg = btc.get('chg24h', 0)
    eth_chg = eth.get('chg24h', 0)
    btc_fr  = btc.get('fr', 0)
    eth_fr  = eth.get('fr', 0)
    btc_ls  = btc.get('ls_ratio', 1.0)
    eth_ls  = eth.get('ls_ratio', 1.0)

    btc_price = btc.get('price', 0)
    # 计算关键价位（近期支撑/压力）
    support = recent_low if 'recent_low' in dir() else 0
    resist  = recent_high if 'recent_high' in dir() else 0

    if btc_chg < -1.5 and eth_chg < -1.5:
        mood = (f'BTC{btc_chg:+.1f}% ETH{eth_chg:+.1f}%，两大主力同步下跌。\n'
                f'这不是分化，是系统性下行。多头今天受伤了，FR{btc_fr:.4f}%说明还没到恐慌抛售的程度。\n'
                f'短期看：BTC能否守住{btc_key_level}U是关键，跌破这里止损会加速。')
        tomorrow = (f'我的操作：仓位降到30%以下等BTC企稳。{btc_key_level}U守住+成交量萎缩，'
                    f'才考虑轻仓试多，止损放在{btc_key_level}U下方1.5%。')
    elif btc_chg > 1 and eth_chg > 1:
        # 同向上涨：给出追涨的风险评估和实际操作数字
        eth_btc_stronger = eth_chg > btc_chg
        mood = (f'BTC{btc_chg:+.1f}% ETH{eth_chg:+.1f}%，主力同步上涨。\n'
                f'{"ETH跑赢BTC，资金在向山寨扩散，轮动行情启动信号。" if eth_btc_stronger else "BTC领涨，资金还在集中在主流，山寨跟涨但弹性弱。"}\n'
                f'FR{btc_fr:.4f}%，多头持仓成本{"在累积，追高要算清楚成本。" if btc_fr > 0.008 else "正常，没有过热。"}')
        if btc_price > 0:
            chase_risk_level = (btc_price * 1.005)
            tomorrow = (f'我的判断：{"现在追多不是最优时机，等回踩" + str(int(btc_price*0.99)) + "U附近确认支撑再进，止损放在" + str(int(btc_price*0.985)) + "U。" if btc_fr > 0.008 else "FR正常，可以轻仓持多，止损放在今日低点" + btc_key_level + "U下方。"}')
        else:
            tomorrow = f'关注BTC能否放量站稳，没有量能拉不动。'
    else:
        # 真正的分化或小幅震荡
        btc_dir = '微涨' if btc_chg > 0.3 else ('微跌' if btc_chg < -0.3 else '横盘')
        eth_dir = '微涨' if eth_chg > 0.3 else ('微跌' if eth_chg < -0.3 else '横盘')
        eth_btc_rel = '跑赢BTC' if eth_chg > btc_chg + 0.5 else ('跑输BTC' if eth_chg < btc_chg - 0.5 else '与BTC同步')
        mood = (f'BTC{btc_dir}({btc_chg:+.1f}%) ETH{eth_dir}({eth_chg:+.1f}%)，ETH{eth_btc_rel}。\n'
                f'{"ETH/BTC汇率上行，资金向山寨流动，留意轮动机会。" if eth_chg > btc_chg + 0.5 else "ETH/BTC汇率下行，资金仍在BTC，山寨季还没开始。" if eth_chg < btc_chg - 0.5 else "BTC和ETH同向小幅波动，市场整体在消化。"}\n'
                f'FR{btc_fr:.4f}%，没有极端信号，当前是等待而非行动的时机。')
        tomorrow = (f'BTC的{btc_key_level}U支撑和'
                    f'{int(float(btc_key_level.replace(",",""))*1.02) if btc_key_level else "上方压力位"}U压力是明天的核心观察位。'
                    f'没有突破前我保持轻仓或空仓。')

    lines = [f'今日收盘，说一下我的判断。', '']
    lines.append(f'📊 {now_cst()} CST')
    lines.append('')

    # 主力币数据
    if btc['price'] > 0:
        btc_fr_note = '正常' if abs(btc_fr) < 0.005 else ('多头偏热' if btc_fr > 0.01 else '空头占优' if btc_fr < -0.005 else '')
        lines.append(f'BTC {btc["price"]:,.0f}U ({btc_chg:+.1f}%) FR:{btc_fr:.4f}% 多空比:{btc_ls:.2f}')
    if eth['price'] > 0:
        lines.append(f'ETH {eth["price"]:,.1f}U ({eth_chg:+.1f}%) FR:{eth_fr:.4f}% 多空比:{eth_ls:.2f}')
    if sol['price'] > 0:
        lines.append(f'SOL {sol["price"]:,.2f}U ({sol["chg24h"]:+.1f}%)')

    # 4H结构判断
    if btc_structure:
        lines.append('')
        lines.append(f'BTC 4H结构：{btc_structure}。')

    lines.append('')
    lines.append(mood)
    lines.append('')
    lines.append(tomorrow)
    lines.append('')
    # 动态互动问句
    if btc_chg > 1:
        lines.append('今天涨了，你加仓了吗？')
    elif btc_chg < -1:
        lines.append('这个位置你会拉抢安全带吗？')
    else:
        lines.append('震荡行情，你现在是持币等的还是轻仓布局？')
    lines.append('')
    lines.append('#市场复盘 #BTC #合约交易')
    return '\n'.join(lines)


def build_education(edu_id: int = 0) -> str:
    """预制教育帖轮播"""
    if POOL_FILE.exists():
        try:
            pool = json.loads(POOL_FILE.read_text())
            posts = pool.get('education', [])
            if posts:
                idx = edu_id % len(posts)
                return posts[idx].replace('{NOW}', now_cst())
        except Exception:
            pass
    # fallback内置
    fallback = [
        f"""📚 SMC结构精讲 | {now_cst()} CST

什么是订单块(Order Block)？

OB = 机构建仓前最后一根反向K线。
• 看涨OB = 下跌段最后一根阴线（支撑）
• 看跌OB = 上涨段最后一根阳线（阻力）

核心逻辑：机构在OB累积头寸。
价格回踩OB = 二次建仓机会。我用OB找入场点，不懂机构在哪建仓就不知道止损放哪。

#SMC #技术分析 #OrderBlock #加密货币""",
        f"""⚠️ 风险管理铁律 | {now_cst()} CST

专业交易员与散户最大区别：

❌ 散户：先想利润，止损后悔恨
✅ 专业：先想止损，严格按计划执行

三条铁律：
1. 单笔风险≤账户2%
2. 止损是计划的一部分，不是失败
3. 连亏3笔，停下来复盘

这几条规则救了我很多次，纪律才是持续盈利的基础。

#风险管理 #交易心态 #加密货币 #交易技巧""",
        f"""📊 RSI进阶用法 | {now_cst()} CST

RSI不等于简单的超买超卖信号。

市场方向决定RSI的解读方式：

下行市场中：
• RSI>60做空 ✓（顺势）
• RSI<30接多 ✗（逆势接刀）

上行市场中：
• RSI50-60回调做多 ✓
• RSI>80追多 ✗（高位接盘）

同样的RSI，不同市场背景=完全不同含义。

#RSI #技术分析 #加密货币 #行情分析""",
        f"""🎯 如何设置止损 | {now_cst()} CST

止损不是随便放的。

正确止损逻辑：
• 做空止损 = 入场区上沿×(1+SL%)
• 做多止损 = 入场区下沿×(1-SL%)

SL%参考：
• 顺势方向：2.0%
• 逆势轻仓：2.5%

止损距离<1.5×ATR = 止损太近，会被扫
止损距离>3×ATR = 止损太远，亏太多

结构说话，不是感觉。

#止损 #风险管理 #技术分析 #加密货币""",
        f"""💡 什么时候不该进场 | {now_cst()} CST

比知道何时进场更重要的：
知道什么时候不进场。

以下情形我会主动跳过：
• 大趋势与当前方向相反
• 距离关键支撑/阻力<0.5%
• 即将到期日（月末周五）
• 市场情绪极度恐慌或贪婪

不出手也是一种赢法。
不追，不抢，等到合适的位置再动。

#交易纪律 #交易技巧 #加密货币 #风险管理""",
    ]
    return fallback[edu_id % len(fallback)]


# ════════════════════════════════════════════════════════════════════
# 发布函数
# ════════════════════════════════════════════════════════════════════

def post_to_square(content: str, dry_run: bool = False) -> bool:
    """发布到广场。返回True=成功"""
    import requests

    # ── 40年交易员视角重写层（发帖前必过）──
    if not dry_run:
        content = rewrite_as_trader(content)

    ok, reason = check_content(content)
    if not ok:
        print(f'[post] ❌ 内容检查失败: {reason}', file=sys.stderr)
        return False

    if is_duplicate(content):
        print('[post] ⚠️  24H内重复内容，跳过', file=sys.stderr)
        return False

    if dry_run:
        print('[DRY-RUN] 内容预览:')
        print('-' * 50)
        print(content)
        print('-' * 50)
        print(f'[DRY-RUN] 字数:{len(content)} 检查:✅')
        return True

    # [设计院封印 2026-08-21] 先占位再发送，防止API超时后重试导致重复发帖
    # 根因: 08-18两条完全相同的帖 = mark_posted只在成功后写入，超时重试时指纹还未写入
    mark_posted(content)  # 先占位

    try:
        resp = requests.post(
            SQUARE_API,
            headers=HEADERS,
            json={'bodyTextOnly': content},
            timeout=15,
        )
        result = resp.json()
        code = result.get('code', '')
        if code == '000000':
            post_id = result.get('data', {}).get('id', '') if result.get('data') else ''
            mark_posted(content)
            # 写日志
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'ts': time.time(),
                    'post_type': 'hot_poster',
                    'post_id': post_id,
                    'chars': len(content),
                    'preview': content[:300],
                }, ensure_ascii=False) + '\n')
            print(f'[post] ✅ 发布成功 id={post_id} chars={len(content)}')
            # 推送到苏摩主线程
            try:
                import subprocess as _sp
                _task_label = args_type if 'args_type' in dir() else 'Square帖子'
                _preview = content[:200].replace('"', '\"').replace('\n', ' ')
                _msg = f'📢 梵天发帖成功\n\n{_preview}...'
                _sp.run(['openclaw', 'message', 'send',
                         '--channel', 'jarvis',
                         '--to', '73295708:thread:01a03e25-a459-733e-a2ba-a56083050f26',
                         '--message', _msg], timeout=10, capture_output=True)
            except Exception as _pe:
                print(f'[post] ⚠️ 推送苏摩失败: {_pe}', file=sys.stderr)
            return True
        else:
            msg = result.get('message', result.get('msg', ''))
            print(f'[post] ❌ API错误 code={code} msg={msg}', file=sys.stderr)
            # 写blocked日志
            blocked_log = DATA_DIR / 'post_blocked_log.jsonl'
            with open(blocked_log, 'a') as f:
                f.write(json.dumps({'ts': time.time(), 'code': code, 'msg': msg,
                                    'preview': content[:60]}) + '\n')
            return False
    except Exception as e:
        print(f'[post] ❌ 请求异常: {e}', file=sys.stderr)
        return False


# ════════════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════════════

POST_BUILDERS = {
    'hot_tickers':    build_hot_tickers,
    'funding_rate':   build_funding_rate,
    'top_gainers':    build_top_gainers,
    'top_losers':     build_top_losers,
    'hot_news':       build_hot_news,
    'smart_money':    build_smart_money,
    'pump_alert':     build_pump_alert,
    'market_summary': build_market_summary,
}


def main():
    parser = argparse.ArgumentParser(description='广场热度驱动发帖主引擎')
    parser.add_argument('--type', required=True,
                        choices=list(POST_BUILDERS.keys()) + ['edu'],
                        help='帖子类型')
    parser.add_argument('--edu-id', type=int, default=0, help='教育帖序号(0-based)')
    parser.add_argument('--dry-run', action='store_true', help='仅预览不发布')
    parser.add_argument('--no-delay', action='store_true', help='跳过随机延迟（调试用）')
    args = parser.parse_args()

    # ── 升级1：随机延迟（防定时限流）──
    # 延迟移到内容生成之后，减少总耗时
    if not args.dry_run and not args.no_delay:
        import random
        delay = random.uniform(2, 15)  # 最多15秒（原60秒太长导致cron timeout）
        if delay > 5:
            print(f'[poster] 随机延迟 {delay:.0f}秒')
        time.sleep(delay)

    if args.type == 'edu':
        content = build_education(args.edu_id)
    else:
        builder = POST_BUILDERS[args.type]
        content = builder()

    if not content:
        # [设计院封印 2026-08-21 苏摩指令] 主任务无内容时启用备用内容池，不断更
        try:
            import json, random, time as _t
            _pool_path = Path(__file__).parent.parent.parent / 'data/square_backup_pool.json'
            _pool = json.loads(_pool_path.read_text())
            _posts = _pool.get('posts', [])
            # 过滤未发过的，过去24H内未使用的
            _log = Path(__file__).parent.parent.parent / 'data/square_post_log.jsonl'
            _sent_ids = set()
            if _log.exists():
                _cutoff = _t.time() - 86400 * 3  # 3天内用过的不再用
                for _l in _log.read_text().splitlines():
                    try:
                        _e = json.loads(_l)
                        if float(_e.get('ts', 0)) > _cutoff:
                            _sent_ids.add(_e.get('backup_id', ''))
                    except: pass
            _available = [p for p in _posts if p['id'] not in _sent_ids]
            if _available:
                _chosen = random.choice(_available)
                content = _chosen['body']
                print(f'[post] 📚 备用内容池兴布: {_chosen["id"]} ({_chosen["type"]})')
            else:
                print(f'[post] ⚠️  {args.type} 内容为空，备用内容池已用尽，跳过')
                sys.exit(0)
        except Exception as _be:
            print(f'[post] ⚠️  {args.type} 内容为空，备用内容池失败: {_be}，跳过')
            sys.exit(0)

    success = post_to_square(content, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
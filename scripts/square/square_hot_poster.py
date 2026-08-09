#!/usr/bin/env python3
"""
square_hot_poster.py — 热度驱动广场发帖主引擎 v1.0
设计院自主决策封印 2026-08-08 | 苏摩111批准

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
SQUARE_KEY = 'd9f19e3f6ba3480584db27b09bec0f27'  # SQUARE_KEY_0 姓赵不宣主账户
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


# ── 敏感词预检 ────────────────────────────────────────────────────
BLOCKED_WORDS = ['BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
                 'DD1', '仅供内部', '新浪财经']


def check_content(content: str) -> tuple:
    """返回 (ok, reason)"""
    n = len(content)
    if n < 30:
        return False, f'字数不足({n}<30)'
    if n > 500:
        return False, f'字数超限({n}>500)'
    for w in BLOCKED_WORDS:
        if w in content:
            return False, f'包含禁用词: {w}'
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


# ── 体制读取 ──────────────────────────────────────────────────────
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
    """热度币播报 — ticker-rank 4H TOP5"""
    data = run_pro_cli(['square', 'ticker-rank', '--window', '4h', '--limit', '8'])
    items = (data.get('items') or data.get('list', []))[:5] if data else []

    if not items:
        return ''

    # 拉取价格
    prices = {}
    for x in items[:3]:
        sym = x.get('ticker', '').upper()
        if sym in ('BTC', 'ETH', 'SOL', 'BNB'):
            try:
                import requests
                t = requests.get('https://api.binance.com/api/v3/ticker/price',
                                 params={'symbol': f'{sym}USDT'}, timeout=4).json()
                prices[sym] = float(t.get('price', 0))
            except Exception:
                pass

    regime_cn = get_regime_cn()
    lines = [f'🔥 广场热度币 | {now_cst()} CST', '']
    lines.append('过去4H社区最热话题：')
    for i, x in enumerate(items, 1):
        sym = x.get('ticker', '?')
        mention = x.get('mentionCount', 0)
        bull = x.get('bullishCount', 0)
        bear = x.get('bearishCount', 0)
        tot = bull + bear + 1
        bull_pct = int(bull / tot * 100)
        p = prices.get(sym.upper())
        # 修复220095: 价格用U后缀代替$前缀，$SYMBOL仅限TOP3
        price_str = f'  {p:,.2f}U' if p else ''
        ticker_str = f'${sym}' if i <= 3 else sym
        lines.append(f'  {i}. {ticker_str}{price_str} | 提及{mention:,}次 | 看多{bull_pct}%')

    lines.append('')
    lines.append(f'📊 当前体制：{regime_cn}')
    lines.append('热度就是资金方向——跟着流动性走。')
    lines.append('')
    lines.append('#热度榜 #加密货币 #量化 #梵天')
    return '\n'.join(lines)


def build_funding_rate() -> str:
    """资金费率 + 多空比播报"""
    syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    data = {}
    for s in syms:
        d = fetch_price(s)
        data[s.replace('USDT', '')] = d

    lines = [f'📊 资金费率速览 | {now_cst()} CST', '']
    lines.append('当前永续合约资金费率：')
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
        if price > 0:
            lines.append(f'  {sym}: ${price:,.2f} ({chg:+.2f}%) | FR:{fr:.4f}% | 多空比:{ls:.2f} {icon}')
        else:
            lines.append(f'  {sym}: FR:{fr:.4f}% | 多空比:{ls:.2f} {icon}')

    lines.append('')
    lines.append('资金费率>0.01%=多头过热，历史上常见回调。')
    lines.append('')
    lines.append('#资金费率 #合约交易 #量化 #BTC')
    return '\n'.join(lines)


def build_top_gainers() -> str:
    """合约涨幅榜 TOP5"""
    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_GAINERS', '--limit', '8'])
    items = (data.get('list') or data.get('items', []))[:5] if data else []

    if not items:
        return ''

    lines = [f'📈 合约涨幅榜 | {now_cst()} CST', '']
    lines.append('过去24H涨幅最大的合约：')
    for i, x in enumerate(items, 1):
        sym = x.get('symbol', '').replace('USDT', '')
        chg = float(x.get('change', x.get('priceChangePercent', 0)))
        price = float(x.get('price', 0))
        p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        # 修复220095: $SYMBOL仅前2名，其余纯文本
        sym_str = f'${sym}' if i <= 2 else sym
        lines.append(f'  {i}. {sym_str} {chg:+.2f}% | {p_str}')

    lines.append('')
    lines.append('涨幅榜≠买入信号。追高前确认体制和结构。')
    lines.append('')
    lines.append('#涨幅榜 #合约交易 #量化 #热点')
    return '\n'.join(lines)


def build_top_losers() -> str:
    """合约跌幅榜 TOP5"""
    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_LOSERS', '--limit', '8'])
    items = (data.get('list') or data.get('items', []))[:5] if data else []

    if not items:
        return ''

    lines = [f'📉 合约跌幅榜 | {now_cst()} CST', '']
    lines.append('过去24H跌幅最大的合约：')
    for i, x in enumerate(items, 1):
        sym = x.get('symbol', '').replace('USDT', '')
        chg = float(x.get('change', x.get('priceChangePercent', 0)))
        price = float(x.get('price', 0))
        p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        # 修复220095: $SYMBOL仅前2名
        sym_str = f'${sym}' if i <= 2 else sym
        lines.append(f'  {i}. {sym_str} {chg:+.2f}% | {p_str}')

    lines.append('')
    lines.append('跌幅榜≠做空信号。确认体制方向再入场。')
    lines.append('')
    lines.append('#跌幅榜 #合约交易 #量化 #风险')
    return '\n'.join(lines)


def build_hot_news() -> str:
    """热门话题 / 新闻追踪"""
    data = run_pro_cli(['square', 'hot', '--sort', 'HEAT', '--window', '4h', '--limit', '5'])
    items = (data.get('items') or [])[:3] if data else []

    if not items:
        return ''

    # 取热度最高帖的话题
    top = items[0]
    body = top.get('body', '')[:120].strip()
    tickers = [t.replace('USDT', '') for t in top.get('tickers', [])]
    hashtags = top.get('hashtagList', [])[:3]

    lines = [f'🌐 广场热门话题 | {now_cst()} CST', '']
    lines.append('社区当前最热讨论：')
    lines.append('')
    if body:
        lines.append(f'"{body}..."')
        lines.append('')
    if tickers:
        lines.append(f'相关标的: {" ".join("$"+t for t in tickers[:3])}')
    lines.append('')
    lines.append('热点追踪仅供参考，决策依赖系统信号和体制判断。')
    tags = ' '.join(hashtags) if hashtags else '#广场热点 #加密货币 #量化'
    lines.append('')
    lines.append(tags if '#' in tags else '#广场热点 #加密货币 #量化')
    return '\n'.join(lines)


def build_smart_money() -> str:
    """聪明钱资金流向"""
    # 用 arb-scan 获取多空比
    data = run_pro_cli(['workflow', 'arb-scan', '--symbols', 'BTC,ETH,SOL'])

    lines = [f'🐋 资金流向速览 | {now_cst()} CST', '']
    lines.append('主力资金当前方向：')

    if data and isinstance(data, dict):
        for sym in ['BTC', 'ETH', 'SOL']:
            v = data.get(sym, data.get(f'{sym}USDT', {}))
            if isinstance(v, dict) and 'long_short_ratio' in v:
                ls = float(v['long_short_ratio'])
                fr = float(v.get('latest_funding_rate', 0)) * 100
                icon = '📈' if ls > 1.2 else ('📉' if ls < 0.8 else '⚖️')
                lines.append(f'  {icon} {sym}: 多空比={ls:.2f} | FR={fr:.4f}%')
    else:
        lines.append('  数据获取中，请稍后查看...')

    lines.append('')
    lines.append('多空比>1.5=极端多头，资金费率>0.01%=多头过热。')
    lines.append('两者同时出现=高概率回调信号。')
    lines.append('')
    lines.append('#聪明钱 #资金流向 #多空比 #量化')
    return '\n'.join(lines)


def build_pump_alert() -> str:
    """暴涨猎手预警"""
    pump_file = DATA_DIR / 'pump_detected.json'
    if not pump_file.exists():
        return ''

    try:
        pd = json.loads(pump_file.read_text())
        # 检查时效性（2小时内）
        ts = pd.get('ts', 0)
        if time.time() - ts > 7200:
            return ''  # 数据过期，不发
        candidates = pd.get('candidates', [])[:3]
        if not candidates:
            return ''
    except Exception:
        return ''

    lines = [f'🚨 暴涨预警 | {now_cst()} CST', '']
    lines.append('梵天暴涨猎手检测到压缩形态：')
    for c in candidates:
        sym = str(c.get('symbol', '')).replace('USDT', '')
        score = c.get('score', 0)
        bb_width = c.get('bb_width', 0)
        lines.append(f'  🎯 ${sym} 评分:{score} | BB压缩:{bb_width:.2f}%')

    lines.append('')
    lines.append('压缩形态 = 能量积累，等方向突破再介入。')
    lines.append('切勿提前埋伏，等信号确认。')
    lines.append('')
    lines.append('#暴涨预警 #量化 #突破 #加密货币')
    return '\n'.join(lines)


def build_market_summary() -> str:
    """市场热度总结（每日收盘版）"""
    data = run_pro_cli(['square', 'ticker-rank', '--window', '24h', '--limit', '10'])
    items = (data.get('items') or data.get('list', []))[:5] if data else []

    btc = fetch_price('BTCUSDT')
    eth = fetch_price('ETHUSDT')
    regime_cn = get_regime_cn()

    lines = [f'🧠 今日市场热度总结 | {now_cst()} CST', '']
    lines.append(f'📊 体制：{regime_cn}')
    if btc['price'] > 0:
        lines.append(f'BTC ${btc["price"]:,.0f} ({btc["chg24h"]:+.2f}%) FR:{btc["fr"]:.4f}%')
    if eth['price'] > 0:
        lines.append(f'ETH ${eth["price"]:,.2f} ({eth["chg24h"]:+.2f}%) FR:{eth["fr"]:.4f}%')

    if items:
        lines.append('')
        lines.append('24H社区热度TOP3：')
        for i, x in enumerate(items[:3], 1):
            sym = x.get('ticker', '?')
            mention = x.get('mentionCount', 0)
            lines.append(f'  {i}. ${sym} 提及{mention:,}次')

    lines.append('')
    lines.append('数据说话，系统判断，风险自控。')
    lines.append('')
    lines.append('#市场总结 #BTC #量化 #加密货币')
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
价格回踩OB = 二次建仓机会。

不懂机构在哪建仓，就不知道止损放哪。

#SMC #量化 #技术分析 #OrderBlock""",
        f"""⚠️ 风险管理铁律 | {now_cst()} CST

专业交易员与散户最大区别：

❌ 散户：先想利润，止损后悔恨
✅ 专业：先想止损，严格按计划执行

三条铁律：
1. 单笔风险≤账户2%
2. 止损是计划的一部分，不是失败
3. 连亏3笔，停下来复盘

系统给信号，纪律保执行。

#风险管理 #量化 #交易心态""",
        f"""📊 RSI进阶用法 | {now_cst()} CST

RSI≠简单超买超卖信号。

关键：体制决定RSI的解读方式

熊市体制：
• RSI>60做空 ✓（顺势）
• RSI<30接多 ✗（逆势接刀）

牛市体制：
• RSI50-60回调做多 ✓
• RSI>80追多 ✗（高位接盘）

同样的RSI，不同体制=完全不同含义。

#RSI #量化 #技术指标 #梵天""",
        f"""🎯 如何设置止损 | {now_cst()} CST

止损不是随便放的。

正确止损逻辑：
• 做空止损 = 入场区上沿×(1+SL%)
• 做多止损 = 入场区下沿×(1-SL%)

SL%参考（按体制）：
• 顺势做空：2.0%
• 逆势轻多：2.5%

止损距离<1.5×ATR = 止损太近，会被扫
止损距离>3×ATR = 止损太远，亏太多

结构说话，不是感觉。

#止损 #风险管理 #量化 #梵天""",
        f"""💡 什么时候不该进场 | {now_cst()} CST

比知道何时进场更重要的：
知道什么时候不进场。

高分信号也要跳过的情形：
• 大级别体制与信号方向相反
• 距离关键支撑/阻力<0.5%
• 即将到期日（月末周五）
• 市场情绪极度恐慌或贪婪

系统不出手也是一种赢法。
不追，不抢，等系统给的位置。

#量化 #交易纪律 #梵天 #不进场""",
    ]
    return fallback[edu_id % len(fallback)]


# ════════════════════════════════════════════════════════════════════
# 发布函数
# ════════════════════════════════════════════════════════════════════

def post_to_square(content: str, dry_run: bool = False) -> bool:
    """发布到广场。返回True=成功"""
    import requests

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
                    'preview': content[:60],
                }, ensure_ascii=False) + '\n')
            print(f'[post] ✅ 发布成功 id={post_id} chars={len(content)}')
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
    parser = argparse.ArgumentParser(description='梵天广场热度驱动发帖主引擎')
    parser.add_argument('--type', required=True,
                        choices=list(POST_BUILDERS.keys()) + ['edu'],
                        help='帖子类型')
    parser.add_argument('--edu-id', type=int, default=0, help='教育帖序号(0-based)')
    parser.add_argument('--dry-run', action='store_true', help='仅预览不发布')
    args = parser.parse_args()

    if args.type == 'edu':
        content = build_education(args.edu_id)
    else:
        builder = POST_BUILDERS[args.type]
        content = builder()

    if not content:
        print(f'[post] ⚠️  {args.type} 内容为空（数据源无数据），跳过')
        sys.exit(0)

    success = post_to_square(content, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

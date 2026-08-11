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
    """KOL热点即时反应帖 — 找出最有爆点的币，给出反直觉判断"""
    import requests as _r

    # 拉取4H热度榜
    data = run_pro_cli(['square', 'ticker-rank', '--window', '4h', '--limit', '15'])
    items = (data.get('items') or data.get('list', []))[:15] if data else []
    if not items:
        return ''

    # 找出互动量最高 且 有合约数据的标的
    candidates = sorted(items, key=lambda x: x.get('totalEngagement', 0), reverse=True)
    hot_sym = None
    hot_data = {}
    for c in candidates:
        sym = c.get('ticker', '').upper()
        if not sym or sym in ('USD1', 'WLFI', 'USDT', 'USDC'):
            continue
        try:
            t = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                       params={'symbol': f'{sym}USDT'}, timeout=4).json()
            fr_r = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                          params={'symbol': f'{sym}USDT'}, timeout=4).json()
            ls_r = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                          params={'symbol': f'{sym}USDT', 'period': '1h', 'limit': 1}, timeout=4).json()
            price = float(t.get('lastPrice', 0))
            chg = float(t.get('priceChangePercent', 0))
            if price > 0:
                hot_sym = sym
                hot_data = {
                    'price': price,
                    'chg': chg,
                    'fr': float(fr_r.get('lastFundingRate', 0)) * 100,
                    'ls': float(ls_r[0]['longShortRatio']) if ls_r else 1.0,
                    'mention': c.get('mentionCount', 0),
                    'engage': c.get('totalEngagement', 0),
                    'bull_pct': int(c.get('bullishCount', 0) / max(c.get('bullishCount', 0) + c.get('bearishCount', 0) + 1, 1) * 100),
                }
                break
        except Exception:
            continue

    if not hot_sym or not hot_data:
        return ''

    price = hot_data['price']
    chg = hot_data['chg']
    fr = hot_data['fr']
    ls = hot_data['ls']
    mention = hot_data['mention']
    bull_pct = hot_data['bull_pct']

    # 价格格式
    price_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'

    # 生成反直觉观点
    if chg > 30 and fr > 0.05:
        hook = f'${hot_sym} 今天涨了{chg:.0f}%，我没追。'
        insight = f'资金费率已达 {fr:.4f}%——追多的成本在快速累积。\n历史规律：FR超过0.05%之后，通常24H内出现回调。'
        question = '这里你会追吗？'
    elif chg > 50 and fr < 0:
        hook = f'${hot_sym} 暴涨{chg:.0f}%，但空头比多头还多。'
        insight = f'资金费率 {fr:.4f}%（负值），说明这是空头止损触发的轧空行情。\n轧空结束后，没有新买盘接力，通常急跌开始。'
        question = '你觉得这波涨完了吗？'
    elif chg > 15:
        hook = f'${hot_sym} 涨{chg:.0f}%，广场讨论量{mention:,}次，情绪很热。'
        insight = f'多空比：{ls:.2f}，看多情绪占{bull_pct}%。\n资金费率：{fr:.4f}%。热度高不等于方向对，先看数据再说。'
        question = '你会在这里追进去吗？'
    elif chg < -15:
        hook = f'${hot_sym} 今天跌了{abs(chg):.0f}%，广场上一片恐慌。'
        insight = f'这种时候最容易做出冲动决定。\n数据：多空比 {ls:.2f}，FR {fr:.4f}%。\n跌幅大不等于可以抄底，方向没变之前我不会动。'
        question = '你觉得这里是底吗？'
    else:
        hook = f'广场热度第一的 ${hot_sym}，这些数据我最关注。'
        insight = f'提及{mention:,}次，看多{bull_pct}%——情绪偏一边的时候我反而要小心。\nFR {fr:.4f}%，多空比 {ls:.2f}，无极端信号。'
        question = '你怎么看这个位置？'

    lines = [
        hook, '',
        '📊 当前数据：',
        f'  价格: {price_str} | 24H: {chg:+.2f}%',
        f'  资金费率: {fr:.4f}% | 多空比: {ls:.2f}',
        f'  广场提及: {mention:,}次 | 看多情绪: {bull_pct}%',
        '',
        insight,
        '',
        question,
        '',
        f'#{hot_sym} #合约交易 #加密货币 #行情分析',
    ]
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
    lines.append('SOL资金费率已到警戒线，追多的朋友要注意了。')
    lines.append('')
    lines.append('#资金费率 #合约 #BTC #行情分析')
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
    lines.append('涨得猛不代表值得追，我会先看它涨的理由是什么。')
    lines.append('')
    lines.append('#涨幅榜 #合约 #加密货币 #今日行情')
    return '\n'.join(lines)


def build_top_losers() -> str:
    """合约跌幅榜 — 顶级交易员视角：利空分析+结构判断+操作建议"""
    import requests as _r

    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_LOSERS', '--limit', '8'])
    items = (data.get('list') or data.get('items', []))[:5] if data else []
    if not items:
        return ''

    # 拉主力币BTC结构判断跌幅背景
    btc_chg = 0.0
    btc_fr = 0.0
    btc_ls = 1.0
    try:
        bt = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                    params={'symbol': 'BTCUSDT'}, timeout=4).json()
        bfr = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                     params={'symbol': 'BTCUSDT'}, timeout=4).json()
        bls = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                     params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1}, timeout=4).json()
        btc_chg = float(bt.get('priceChangePercent', 0))
        btc_fr = float(bfr.get('lastFundingRate', 0)) * 100
        btc_ls = float(bls[0]['longShortRatio']) if bls else 1.0
    except Exception:
        pass

    # 对跌幅前2名拉OI和FR，判断是砸盘/轧多/恐慌
    sym_analysis = {}
    for x in items[:2]:
        sym = x.get('symbol', '')
        try:
            fr_r = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                          params={'symbol': sym}, timeout=4).json()
            oi_r = _r.get('https://fapi.binance.com/fapi/v1/openInterest',
                          params={'symbol': sym}, timeout=4).json()
            ls_r = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                          params={'symbol': sym, 'period': '1h', 'limit': 1}, timeout=4).json()
            sym_analysis[sym] = {
                'fr': float(fr_r.get('lastFundingRate', 0)) * 100,
                'oi': float(oi_r.get('openInterest', 0)) * float(x.get('price', 0)) / 1e6,
                'ls': float(ls_r[0]['longShortRatio']) if ls_r else 1.0,
            }
        except Exception:
            pass

    # 判断大背景
    if btc_chg < -2:
        background = f'BTC今日跌{abs(btc_chg):.1f}%，整体偏弱，跌幅榜几乎全是被大盘拖下来的。'
        btc_signal = '主因是大盘系统性下跌，不是个股利空。'
    elif btc_chg < 0:
        background = f'BTC今日小跌{abs(btc_chg):.1f}%，大盘承压。跌幅榜里有些是自身利空，要区分清楚。'
        btc_signal = '注意区分大盘带跌和个币利空。'
    else:
        background = f'BTC今日微涨，但这些币逆势大跌，说明有自身利空，要单独分析。'
        btc_signal = '大盘偏多但这些币逆势跌，大概率有利空事件或筹码砸盘。'

    lines = [f'今日跌幅榜，说说我的看法。', '']
    lines.append(f'📊 {now_cst()} CST | BTC {btc_chg:+.1f}%')
    lines.append('')
    lines.append('跌幅前5：')

    for i, x in enumerate(items, 1):
        sym_raw = x.get('symbol', '')
        sym = sym_raw.replace('USDT', '')
        chg = float(x.get('change', x.get('priceChangePercent', 0)))
        price = float(x.get('price', 0))
        p_str = f'{price:,.4f}U' if price < 1 else f'{price:,.2f}U'
        sym_str = f'${sym}' if i <= 2 else sym

        # 对前两名加结构判断
        if sym_raw in sym_analysis:
            a = sym_analysis[sym_raw]
            fr = a['fr']; oi_m = a['oi']; ls = a['ls']
            if fr < -0.01:
                note = f'  ← FR负值({fr:.3f}%)，多头被轧'
            elif oi_m > 50 and chg < -20:
                note = f'  ← OI大+急跌，可能继续'
            elif ls > 2.0:
                note = f'  ← 多空比{ls:.2f}，多头过度，回调合理'
            elif chg < -40:
                note = f'  ← 跌幅极端，小心反弹陷阱'
            else:
                note = ''
            lines.append(f'  {i}. {sym_str} {chg:+.1f}% | {p_str}{note}')
        else:
            lines.append(f'  {i}. {sym_str} {chg:+.1f}% | {p_str}')

    lines.append('')
    lines.append(background)
    lines.append('')

    # 操作建议（基于大盘方向）
    if btc_chg < -1.5:
        advice = '我的处理方式：大盘弱势时不抄底，等BTC稳住再看结构入场。'
    elif any(float(x.get('change', 0)) < -30 for x in items[:2]):
        advice = '跌超30%的别急着抄，先等量能萎缩、价格稳住，再谈入场。'
    else:
        advice = '跌幅榜的标的，先搞清楚是砸盘出货还是恐慌杀跌，逻辑不同，应对方式完全不一样。'

    lines.append(advice)
    lines.append('')
    lines.append('#合约交易 #技术分析 #加密货币 #行情分析')
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
    lines.append('看到热点我的第一反应是看数据，不是冲进去——等价格先反应，再说。')
    lines.append('')
    lines.append('#币圈热点 #加密货币 #BTC')
    return '\n'.join(lines)


def build_smart_money() -> str:
    """聪明钱资金流向"""
    # 用 arb-scan 获取多空比
    data = run_pro_cli(['workflow', 'arb-scan', '--symbols', 'BTC,ETH,SOL'])

    lines = [f'🐋 主力方向速览 | {now_cst()} CST', '']
    lines.append('我看盘的第一步，就是确认主力资金在哪边：')
    lines.append('')

    has_data = False
    if data and isinstance(data, dict):
        for sym in ['BTC', 'ETH', 'SOL']:
            v = data.get(sym, data.get(f'{sym}USDT', {}))
            if isinstance(v, dict) and 'long_short_ratio' in v:
                ls = float(v['long_short_ratio'])
                fr = float(v.get('latest_funding_rate', 0)) * 100
                if ls > 1.5:
                    icon = '📈 多头主导，情绪偏热'
                elif ls > 1.2:
                    icon = '📈 多头占优'
                elif ls < 0.8:
                    icon = '📉 空头占优'
                else:
                    icon = '⚖️  多空均衡'
                lines.append(f'  {sym}: 多空比={ls:.2f} | FR={fr:.4f}% | {icon}')
                has_data = True

    if not has_data:
        # fallback：拉实时数据
        import requests
        for sym in ['BTC', 'ETH', 'SOL']:
            try:
                ls_data = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                    params={'symbol': f'{sym}USDT', 'period': '1h', 'limit': 1}, timeout=5).json()
                fr_data = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                    params={'symbol': f'{sym}USDT'}, timeout=5).json()
                ls = float(ls_data[0]['longShortRatio']) if ls_data else 1.0
                fr = float(fr_data.get('lastFundingRate', 0)) * 100
                icon = '📈 多头占优' if ls > 1.2 else ('📉 空头占优' if ls < 0.8 else '⚖️  多空均衡')
                lines.append(f'  {sym}: 多空比={ls:.2f} | FR={fr:.4f}% | {icon}')
            except Exception:
                pass

    lines.append('')
    lines.append('多空比>1.5多头过热，结合资金费率一起看，两个都高的时候要小心追多。')
    lines.append('')
    lines.append('#主力资金 #资金费率 #合约 #行情分析')
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
    lines.append('注意到这几个币的波动率在收缩，历史上这种形态出现后通常会有方向性突破：')
    for c in candidates:
        sym = str(c.get('symbol', '')).replace('USDT', '')
        score = c.get('score', 0)
        bb_width = c.get('bb_width', 0)
        lines.append(f'  🎯 ${sym} 评分:{score} | BB压缩:{bb_width:.2f}%')

    lines.append('')
    lines.append('压缩形态 = 能量积累，等方向突破再介入。')
    lines.append('方向出来之前我不会动，等突破确认再说。')
    lines.append('')
    lines.append('#行情预警 #突破信号 #加密货币 #技术分析')
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

    if btc_chg < -1.5 and eth_chg < -1.5:
        mood = '两大主力币同步下滴，市场其实在用行动回答一个问题：多头到底有多少。'
        tomorrow = f'BTC {btc_key_level} 是今晚最关键的支撑，守不住的话我会进一步降低多头仓位。'
    elif btc_chg > 1.5 and eth_chg > 1.5:
        mood = '两大主力币同步上涨，多头情绪在换手。但FR还尚正常，还没到过热的位置。'
        tomorrow = f'BTC能否稳住并继续上攻，看量能。没量能拉不动。'
    else:
        mood = f'BTC和ETH分化明显，小币和山寨币各玩各的。整体市场方向未明。'
        tomorrow = f'BTC站不稳{btc_key_level}，我不会贸然加仓。'

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
    lines.append(f'明天开盘我会盯着看：{tomorrow}')
    lines.append('')
    lines.append('#市场复盘 #BTC #合约交易 #行情分析')
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
    parser = argparse.ArgumentParser(description='广场热度驱动发帖主引擎')
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

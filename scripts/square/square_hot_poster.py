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
SQUARE_KEY = __import__('os').environ.get('SQUARE_KEY_0', 'd9f19e3f...')  # via env  # SQUARE_KEY_0 姓赵不宣主账户
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

# 百强KOL铁律：感叹号 = 广场违规词（自检机制）
BLOCKED_PUNCTUATION = ['\uff01']  # ！全角感叹号


def check_content(content: str) -> tuple:
    """返回 (ok, reason)"""
    n = len(content)
    # 感叹号检查优先（百强KOL铁律）
    for p in BLOCKED_PUNCTUATION:
        count = content.count(p)
        if count > 0:
            return False, f'包含全角感叹号({count}个)，广场违规词'
    if n < 30:
        return False, f'字数不足({n}<30)'
    if n > 2000:
        return False, f'字数超限({n}>2000)'
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
    try:
        t   = _r.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
                     params={'symbol': f'{hot_sym}USDT'}, timeout=5).json()
        frd = _r.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                     params={'symbol': f'{hot_sym}USDT'}, timeout=5).json()
        lsd = _r.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                     params={'symbol': f'{hot_sym}USDT', 'period': '1h', 'limit': 4}, timeout=5).json()
        kl  = _r.get('https://fapi.binance.com/fapi/v1/klines',
                     params={'symbol': f'{hot_sym}USDT', 'interval': '4h', 'limit': 6}, timeout=5).json()
        price     = float(t.get('lastPrice', 0))
        chg       = float(t.get('priceChangePercent', 0))
        fr        = float(frd.get('lastFundingRate', 0)) * 100
        ls        = float(lsd[0]['longShortRatio']) if lsd else 1.0
        ls_4h_ago = float(lsd[3]['longShortRatio']) if len(lsd) > 3 else ls
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

    # 层1：暴涨+FR极高 → 追多成本极重，给出风险判断
    if chg > 20 and fr > 0.05:
        hook    = f'${hot_sym} 今天涨了{chg:.0f}%，我没追。'
        insight = (f'FR已到 {fr:.4f}%——做多的人每8小时要付仓位的{fr:.3f}%给空头。\n'
                   f'多空比 {ls:.2f}，看多{bull_pct}%，多头已经非常拥挤。\n'
                   f'历史规律：FR超过0.05%之后24H内，回调概率明显高于继续上涨。\n'
                   f'热度最高的时候，往往不是最好的入场时机。')
        question = f'FR这么高你还会追{hot_sym}吗？'

    # 层2：上涨+FR负值 → 轧空行情，给出持续性判断
    elif chg > 15 and fr < -0.005:
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

    # 层4：普通上涨 → 挖价格结构给出位置判断
    elif chg > 5:
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

    # 层5：横盘/小波动 → 从多空比变化读出未来方向
    else:
        ls_change = ls - ls_4h_ago
        hook    = f'广场今天热度最高的 ${hot_sym}，数据告诉我一件事。'
        if abs(ls_change) > 0.2:
            direction = f'{"多头快速增加" if ls_change > 0 else "多头在快速撤退"}'
            implication = (f'多空比从{ls_4h_ago:.2f}变化到{ls:.2f}——{direction}。\n'
                           f'{"价格还没动但多头在积累，可能是在等突破方向。" if ls_change > 0 and abs(chg) < 3 else "价格平稳但多头在撤，要小心下行风险。" if ls_change < 0 else "资金在流入，关注后续量能。"}')
        else:
            implication = (f'多空比 {ls:.2f}，4H内没有明显变化，市场在等消息或等突破。\n'
                           f'FR {fr:.4f}%，{"偏高，多头付出的成本在累积。" if fr > 0.008 else "正常，没有极端情绪。"}')

        if recent_high > 0 and recent_low > 0:
            insight = (f'{implication}\n'
                       f'近期价格区间 {recent_low:.4f}–{recent_high:.4f}U，突破哪边跟哪边。')
        else:
            insight = implication
        question = f'你认为{hot_sym}接下来会选择哪个方向？'

    out = [
        hook, '',
        f'📊 {now_cst()} CST',
        f'  {price_str} | 24H: {chg:+.1f}% | FR: {fr:.4f}% | 多空比: {ls:.2f}',
        '',
        insight,
        '',
        question,
        '',
        f'#{hot_sym} #合约交易 #加密货币 #行情分析',
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
    if top_fr > 0.01:
        conclusion = f'{top_sym}资金费率达{top_fr:.4f}%，多头持仓成本在累积，这个位置追多要谨慎。'
    elif top_fr > 0.007:
        conclusion = f'{top_sym}资金费率小幅偏高，还在正常范围，但持续上升的话我会开始警惕。'
    else:
        conclusion = '三个主力币资金费率均处于正常范围，没有极端信号，当前多空博弈较为均衡。'

    lines_out.append('')
    lines_out.append(conclusion)
    lines_out.append('')
    lines_out.append('#资金费率 #合约 #BTC #行情分析')
    return '\n'.join(lines_out)


def build_top_gainers() -> str:
    """涨幅榜 — 姓赵不宣: 每个标的给实质判断，不做无观点列表"""
    import requests as _r, re as _re

    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_GAINERS', '--limit', '8'])
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
            verdict = f'量能和资金面没有特别信号，跟随大盘情绪居多。'

        lines.append(f'{i}. ${sym}  {chg:+.1f}%  {p_str}')
        lines.append(f'   {verdict}')
        lines.append('')

    lines.append('涨幅榜我每次都要先看背后的逻辑，不是列完就完了。')
    lines.append('哪个有机会，我会单独出一篇分析。')
    lines.append('')
    lines.append('#合约交易 #加密货币 #量化')
    return '\n'.join(lines)


def build_top_losers() -> str:
    """跌幅榜 — 姓赵不宣: 判断是抄底机会还是继续踩坑"""
    import requests as _r

    data = run_pro_cli(['search', 'price-change', 'um', '--sort', 'TOP_LOSERS', '--limit', '8'])
    items = (data.get('list') or data.get('items', []))[:5] if data else []
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
    lines.append('#合约交易 #加密货币 #量化')
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

    # 生成个人观点
    if chg_val > 20 and fr_val > 0.05:
        opinion = f'涨了{chg_val:.0f}%，但FR已到{fr_val:.3f}%——这种位置我不会追，等回调再说。'
    elif chg_val < -20:
        opinion = f'跌了{abs(chg_val):.0f}%，很多人在讨论要不要抄底。我的判断：先搞清楚跌的原因，再决定要不要动。'
    elif bull_pct > 75:
        opinion = f'广场{bull_pct}%的人在看多——情绪偏向一边的时候我反而要小心。'
    else:
        opinion = '热度高不等于方向对，看数据再作判断。'

    lines_out = ['广场今天热议的币，我看了一下。', '']
    lines_out.append(f'📊 {now_cst()} CST')
    lines_out.append('')
    lines_out.append(f'${hot_sym} 提及{mention:,}次，看多{bull_pct}%。')
    if price_str:
        lines_out.append(f'现价 {price_str} | 24H {chg_str} | FR {fr_str} | 多空比 {ls_str}')
    lines_out.append('')
    lines_out.append(opinion)
    lines_out.append('')
    lines_out.append(f'#{hot_sym} #广场热点 #加密货币 #行情分析')
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
    parser.add_argument('--no-delay', action='store_true', help='跳过随机延迟（调试用）')
    args = parser.parse_args()

    # ── 升级1：随机延迟（防定时限流）──
    # 百强KOL铁律：「能手动发别定时，定时基本限流」
    # 在目标时间±15分钟内随机触发，模拟手动发帖行为
    if not args.dry_run and not args.no_delay:
        import random
        delay = random.uniform(0, 900)  # 0~15分钟随机延迟
        print(f'[poster] 随机延迟 {delay:.0f}秒 后发布（防限流）')
        time.sleep(delay)

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
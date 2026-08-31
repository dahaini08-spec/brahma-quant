#!/usr/bin/env python3
"""
square_extreme_alert.py — 极端行情自动捕捉 & 发帖
P2: 涨幅>50% 或 跌幅>30% 触发专项KOL帖
姓赵不宣IP · 梵天设计院 2026-08-31

用法:
  python3 scripts/square/square_extreme_alert.py
  python3 scripts/square/square_extreme_alert.py --dry-run

触发条件:
  涨幅 > 50%  → 轧空/暴涨分析帖
  跌幅 > 30%  → 暴跌/猎杀分析帖
  成交额异常  → 主力入场预警帖

冷却机制:
  同一币种 6 小时内只发一次
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

SQUARE_KEY = os.environ.get('SQUARE_KEY_0', '')
API_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
FAPI = 'https://fapi.binance.com/fapi/v1'
COOLDOWN_FILE = Path('/root/.openclaw/workspace/trading-system/data/extreme_alert_cooldown.json')
COOLDOWN_HOURS = 6

# ── 冷却管理 ────────────────────────────────────────────
def load_cooldown() -> dict:
    try:
        if COOLDOWN_FILE.exists():
            return json.loads(COOLDOWN_FILE.read_text())
    except Exception:
        pass
    return {}

def save_cooldown(cd: dict):
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_FILE.write_text(json.dumps(cd, ensure_ascii=False, indent=2))

def is_cool(sym: str, cd: dict) -> bool:
    ts = cd.get(sym, 0)
    return time.time() - ts > COOLDOWN_HOURS * 3600

def mark_sent(sym: str, cd: dict):
    cd[sym] = time.time()

# ── 数据获取 ─────────────────────────────────────────────
def get_extremes():
    """获取涨幅>50% 或 跌幅>30% 的标的"""
    data = requests.get(f'{FAPI}/ticker/24hr', timeout=10).json()
    extremes = []
    for d in data:
        sym = d.get('symbol', '')
        if not sym.endswith('USDT'):
            continue
        chg = float(d.get('priceChangePercent', 0))
        vol = float(d.get('quoteVolume', 0))
        price = float(d.get('lastPrice', 0))
        high = float(d.get('highPrice', 0))
        low = float(d.get('lowPrice', 0))
        if abs(chg) < 30 and chg < 50:
            continue
        if vol < 500_000:  # 过滤低流动性垃圾币（<50万U）
            continue
        extremes.append({
            'symbol': sym,
            'chg': chg,
            'price': price,
            'high': high,
            'low': low,
            'vol': vol,
        })
    return sorted(extremes, key=lambda x: abs(x['chg']), reverse=True)

def get_fr_ls(sym: str) -> tuple[float, float]:
    """获取资金费率和多空比"""
    fr, ls = 0.0, 1.0
    try:
        r = requests.get(f'{FAPI}/premiumIndex', params={'symbol': sym}, timeout=5).json()
        fr = float(r.get('lastFundingRate', 0)) * 100
    except Exception:
        pass
    try:
        r = requests.get(
            'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
            params={'symbol': sym, 'period': '1h', 'limit': 1},
            timeout=5
        ).json()
        if r:
            ls = float(r[0].get('longAccount', 0.5))
    except Exception:
        pass
    return fr, ls

# ── 内容生成 ─────────────────────────────────────────────
def build_pump_post(d: dict) -> str:
    sym = d['symbol'].replace('USDT', '')
    chg = d['chg']
    price = d['price']
    high = d['high']
    low = d['low']
    vol = d['vol']
    fr, ls = get_fr_ls(d['symbol'])

    now_str = datetime.now().strftime('%m/%d %H:%M')

    # 回落幅度
    pullback = (high - price) / high * 100 if high > 0 else 0

    # 判断类型
    if fr < -0.1:
        event_type = '轧空行情'
        mechanism = f'资金费率{fr:.3f}%（极度负值）= 空头大量建仓后被强制平仓\n空头平仓 → 推高价格 → 更多止损触发 → 继续上涨，教科书级轧空。'
        stance = '当前FR仍然极度负值，轧空可能未结束。但从高点已回落{:.1f}%，追高风险极大。'.format(pullback)
        question = '你提前捕捉到这种轧空信号了吗？'
    elif fr > 0.1:
        event_type = '多头过热行情'
        mechanism = f'资金费率{fr:.3f}%（极度正值）= 多头支付高额费用维持持仓\n短期动能强，但持仓成本在累积，需要警惕回调。'
        stance = '多头热情高涨时往往是风险最高的时候，我在这里不追多。'
        question = '这个位置你会持有还是减仓？'
    else:
        event_type = '异常拉升'
        mechanism = f'短时间内价格从{low:.4f}涨到{high:.4f}，涨幅{chg:.0f}%。\n成交额{vol/1e6:.0f}万U，有主力资金介入的痕迹。'
        stance = '涨幅{:.0f}%之后我会观察是否有放量回踩确认，不会第一时间追。'.format(chg)
        question = f'${sym} 你怎么看这波行情的持续性？'

    ls_pct = ls * 100
    content = f"""${sym} 今日+{chg:.0f}%，说说这背后发生了什么。

📊 {now_str} CST
  现价: ${price:.4f} | 今日高点: ${high:.4f} | 低点: ${low:.4f}
  24H成交额: {vol/1e6:.0f}万U | FR: {fr:.4f}% | 多头占比: {ls_pct:.0f}%

━━━ 这是{event_type} ━━━

{mechanism}

━━━ 我的判断 ━━━

{stance}

见过太多这种行情——进场时机比方向更重要。

{question}

#加密货币 #合约交易"""
    return content.strip()


def build_dump_post(d: dict) -> str:
    sym = d['symbol'].replace('USDT', '')
    chg = d['chg']
    price = d['price']
    high = d['high']
    low = d['low']
    vol = d['vol']
    fr, ls = get_fr_ls(d['symbol'])

    now_str = datetime.now().strftime('%m/%d %H:%M')
    rebound = (price - low) / low * 100 if low > 0 else 0

    if fr > 0.1:
        event_type = '多头被猎杀'
        mechanism = f'资金费率{fr:.3f}%（正值）= 大量多头被强制平仓\n多头止损 → 价格下砸 → 更多止损触发 → 继续下跌，主力收割多头流动性。'
        question = '这个位置你会抄底吗？'
    else:
        event_type = '暴跌清洗'
        mechanism = f'从高点{high:.4f}砸到{low:.4f}，跌幅{abs(chg):.0f}%。\n成交额{vol/1e6:.0f}万U，出现恐慌性抛售。'
        question = f'${sym} 你认为底部在哪里？'

    content = f"""${sym} 今日{chg:.0f}%，发生了什么？

📊 {now_str} CST
  现价: ${price:.4f} | 今日高点: ${high:.4f} | 低点: ${low:.4f}
  24H成交额: {vol/1e6:.0f}万U | FR: {fr:.4f}%

━━━ 这是{event_type} ━━━

{mechanism}

━━━ 我的判断 ━━━

已从低点反弹{rebound:.1f}%。短线情绪修复中，但没有结构确认前我不会进。
抄底要等：量能萎缩 + 小时级别结构止跌，两个条件同时满足。

{question}

#加密货币 #合约交易"""
    return content.strip()


# ── 发帖 ────────────────────────────────────────────────
def post_to_square(content: str, dry_run: bool = False) -> str | None:
    if dry_run:
        print('[DRY-RUN]', '-'*50)
        print(content)
        print('-'*50)
        print(f'字数:{len(content)}')
        return 'dry-run'

    if not SQUARE_KEY:
        print('[post] ❌ SQUARE_KEY_0未设置')
        return None

    try:
        r = requests.post(API_URL,
            headers={
                'X-Square-OpenAPI-Key': SQUARE_KEY,
                'Content-Type': 'application/json',
                'clienttype': 'binanceSkill'
            },
            json={'bodyTextOnly': content},
            timeout=10
        )
        data = r.json()
        if data.get('success'):
            pid = data['data']['id']
            print(f'[post] ✅ 发布成功 id={pid}')
            return str(pid)
        else:
            print(f'[post] ❌ {data.get("code")} {data.get("message")}')
            return None
    except Exception as e:
        print(f'[post] ❌ 异常: {e}')
        return None


# ── 主入口 ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print(f'[extreme_alert] 扫描极端行情... {datetime.now().strftime("%H:%M:%S")}')

    try:
        extremes = get_extremes()
    except Exception as e:
        print(f'[extreme_alert] ❌ 数据获取失败: {e}')
        return

    if not extremes:
        print('[extreme_alert] 无极端行情，HEARTBEAT_OK')
        return

    print(f'[extreme_alert] 发现 {len(extremes)} 个极端标的')
    for d in extremes[:3]:
        sym = d['symbol']
        chg = d['chg']
        vol = d['vol'] / 1e6
        print(f'  {sym}: {chg:+.1f}% 成交额={vol:.0f}万U')

    cd = load_cooldown()
    sent = 0

    for d in extremes[:3]:  # 最多处理前3个
        sym = d['symbol']
        if not is_cool(sym, cd):
            print(f'  [{sym}] 冷却中，跳过')
            continue

        chg = d['chg']
        if chg > 0:
            content = build_pump_post(d)
        else:
            content = build_dump_post(d)

        if not content:
            continue

        result = post_to_square(content, dry_run=args.dry_run)
        if result:
            mark_sent(sym, cd)
            sent += 1
            if not args.dry_run:
                time.sleep(3)  # 避免频率限制

    if not args.dry_run:
        save_cooldown(cd)

    if sent == 0:
        print('[extreme_alert] 无新帖发出（全部冷却中）HEARTBEAT_OK')
    else:
        print(f'[extreme_alert] 发出 {sent} 条极端行情帖')


if __name__ == '__main__':
    main()

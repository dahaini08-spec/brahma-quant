#!/usr/bin/env python3
"""
market_screener.py — 全市场纯脚本预筛器 v1.0
设计院封印 · 2026-06-19

功能：
  628个USDT永续合约 → 纯数学6维评分 → TOP 8候选
  0 Soma token消耗，全部为REST API调用（免费）

6维评分体系（满分100，SHORT导向）：
  维度1: 流动性   (0-20) 成交额对数分
  维度2: RSI位置  (0-20) 空头黄金区40-65
  维度3: 双TF趋势 (0-25) 1H+4H均线对齐
  维度4: 价格动量 (0-15) 24H跌幅适度
  维度5: OI变化   (-10~+10) 减仓信号加分
  维度6: 资金费率  (-5~+10) 空头付息加分

输出：data/scan_candidates.json
苏摩宪法：无agentTurn，不计入AI任务配额
"""
import sys, os, json, time, math, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
LOG  = BASE / 'logs'

os.chdir(str(BASE))

# ── 配置 ──────────────────────────────────────────
MIN_VOLUME_USD   = 100e6   # 最低成交额门槛 $100M
TOP_N            = 20      # [P1-E设计院 2026-07-03] 8→20扩大覆盖
SCORE_THRESHOLD  = 30      # [P1-D 2026-07-03] 40→30扩大候选覆盖
MAX_WORKERS      = 6       # 并发线程数（不超过10避免rate limit）
FAPI             = 'https://fapi.binance.com'

# 永久黑名单（极度不稳定 / 梵天无训练数据）
BLACKLIST = {
    'SOLUSDT',   # 梵天体制数据不足，暂不扫描（TODO：达摩院补充训练）
}

# 强制保留（主力标的，无论评分）
FORCE_INCLUDE = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'DOGEUSDT', 'XRPUSDT'}  # [P1-E 2026-07-03] 永久置顶主力币


def _fetch(url: str, retries: int = 2) -> object:
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == retries - 1:
                return None
            time.sleep(0.3)
    return None


def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _calc_ema(closes: list, period: int = 20) -> float:
    if not closes:
        return 0.0
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = ema * (1 - k) + c * k
    return ema


def _score_symbol(sym: str, ticker: dict) -> dict | None:
    """对单个标的进行6维评分，返回得分字典"""
    price  = float(ticker['lastPrice'])
    pct24h = float(ticker['priceChangePercent'])
    vol    = float(ticker['quoteVolume'])

    # ── 1H Klines（RSI + EMA20）──
    k1h = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=24')
    if not isinstance(k1h, list) or len(k1h) < 16:
        return None
    c1h = [float(k[4]) for k in k1h]
    rsi   = _calc_rsi(c1h)
    ema1h = _calc_ema(c1h, 20)
    bear1h = c1h[-1] < ema1h

    # ── 4H Klines（趋势方向）──
    k4h = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=4h&limit=8')
    bear4h = False
    if isinstance(k4h, list) and len(k4h) >= 6:
        c4h   = [float(k[4]) for k in k4h]
        ema4h = _calc_ema(c4h, 20)
        bear4h = c4h[-1] < ema4h

    # ── OI 变化（近3H）──
    oi_delta = 0.0
    oi_data = _fetch(f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=4')
    if isinstance(oi_data, list) and len(oi_data) >= 3:
        try:
            oi_new = float(oi_data[-1]['sumOpenInterest'])
            oi_old = float(oi_data[-3]['sumOpenInterest'])
            oi_delta = (oi_new - oi_old) / max(oi_old, 1) * 100
        except Exception:
            pass

    # ── 资金费率 ──
    fr = 0.0
    fr_data = _fetch(f'{FAPI}/fapi/v1/fundingRate?symbol={sym}&limit=1')
    if isinstance(fr_data, list) and fr_data:
        try:
            fr = float(fr_data[-1]['fundingRate']) * 100
        except Exception:
            pass

    # ════ 6维评分 ════
    score = 0
    detail = {}

    # 维度1: 流动性 (0-20)
    d1 = min(20, max(0, int(math.log10(max(vol / 1e8, 0.01)) * 10 + 10)))
    score += d1
    detail['liquidity'] = d1

    # ── [P1-D 2026-07-03] 体制感知双模式评分 ──
    # 读取当前主要体制
    try:
        import json as _json_ms
        _reg_f = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'data', 'regime_state.json')
        _reg_data = _json_ms.loads(open(_reg_f).read()) if os.path.exists(_reg_f) else {}
        _sym_reg = _reg_data.get(sym, {}).get('confirmed', 'BEAR_TREND') if isinstance(_reg_data.get(sym), dict) else 'BEAR_TREND'
    except Exception:
        _sym_reg = 'BEAR_TREND'

    _bull_mode = 'BULL' in _sym_reg  # BULL_TREND / BULL_EARLY

    # 维度2: RSI位置 (−5~+25) — 双模式
    if _bull_mode:
        # BULL模式：RSI 50~70 = 最佳多头区
        if 50 <= rsi <= 70:
            d2 = 25   # 黄金多头区 ← BULL特供
        elif 45 <= rsi < 50:
            d2 = 15
        elif rsi > 70:
            d2 = 10   # 超买回调等入场（不惩罚）
        elif 35 <= rsi < 45:
            d2 = 5
        else:
            d2 = 0
    else:
        # BEAR/CHOP模式：原始逻辑
        if 40 <= rsi <= 65:
            d2 = 20
        elif 35 <= rsi < 40 or 65 < rsi <= 70:
            d2 = 12
        elif rsi > 75:
            d2 = -5   # 超买，不做空
        elif rsi < 28:
            d2 = 5    # 超卖，风险大
        else:
            d2 = 8
    score += d2
    detail['rsi'] = d2

    # 维度3: 双TF趋势对齐 (0-25) — 双模式
    if _bull_mode:
        # BULL模式：多头排列加分
        bull1h = not bear1h  # 价格>EMA20_1H
        bull4h = not bear4h
        if bull4h and bull1h:
            d3 = 25   # 完美多头
        elif bull4h:
            d3 = 15
        elif bull1h:
            d3 = 8
        else:
            d3 = 0
    else:
        if bear4h and bear1h:
            d3 = 25
        elif bear4h:
            d3 = 15
        elif bear1h:
            d3 = 8
        else:
            d3 = 0
    score += d3
    detail['trend'] = d3

    # 维度4: 价格动量 (-10~+15) — 双模式
    if _bull_mode:
        # BULL模式：涨幅适中最佳，不惩罚强涨
        if 0 < pct24h <= 8:
            d4 = 15   # 有多头动能
        elif pct24h > 8:
            d4 = 8    # 强涨，不惩罚
        elif -3 < pct24h <= 0:
            d4 = 5    # 轻微回调，多头入场机会
        elif -8 < pct24h <= -3:
            d4 = 8    # 回调更深，买入机会增加
        else:
            d4 = 3
    else:
        if -10 < pct24h <= -1:
            d4 = 15
        elif -20 < pct24h <= -10:
            d4 = 6    # 过度下跌，反弹风险
        elif pct24h > 10:
            d4 = -10  # 强涨，不做空
        elif pct24h > 3:
            d4 = -5
        else:
            d4 = 5
    score += d4
    detail['momentum'] = d4

    # 维度5: OI变化 (−5~+10)
    if -3 < oi_delta <= -0.5:
        d5 = 10   # 多头去杠杆
    elif oi_delta > 2:
        d5 = -5   # OI大增+跌 = 新多入场，风险
    else:
        d5 = 0
    score += d5
    detail['oi_delta'] = d5

    # 维度6: 资金费率 (−5~+10)
    if fr < -0.001:
        d6 = 10   # 空头付息 = 主力看跌
    elif fr < 0:
        d6 = 5
    elif fr > 0.01:
        d6 = -5   # 极端多头付息
    else:
        d6 = 0
    score += d6
    detail['fr'] = d6

    trend_str = f'{"B" if bear4h else "U"}4H/{"B" if bear1h else "U"}1H'

    return {
        'symbol':    sym,
        'score':     score,
        'price':     price,
        'pct24h':    round(pct24h, 2),
        'vol_usd':   round(vol / 1e9, 3),
        'rsi':       rsi,
        'trend':     trend_str,
        'oi_delta':  round(oi_delta, 2),
        'fr':        round(fr, 4),
        'detail':    detail,
    }


def run(top_n: int = TOP_N) -> list:
    t0 = time.time()
    print(f'[Screener] 启动 · 目标=TOP {top_n} · 门槛=$100M', flush=True)

    # Step 1: 拉取全量ticker
    tickers = _fetch(f'{FAPI}/fapi/v1/ticker/24hr')
    if not isinstance(tickers, list):
        print('[Screener] ❌ 无法获取ticker数据')
        return []

    usdt_tickers = {
        t['symbol']: t for t in tickers
        if t['symbol'].endswith('USDT')
        and float(t['quoteVolume']) >= MIN_VOLUME_USD
        and t['symbol'] not in BLACKLIST
    }
    print(f'[Screener] 候选池: {len(usdt_tickers)}个 (>$100M, 黑名单已排除)', flush=True)

    # Step 2: 并发评分
    results = []
    errors  = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_score_symbol, sym, ticker): sym
            for sym, ticker in usdt_tickers.items()
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                r = future.result()
                if r is not None:
                    results.append(r)
            except Exception as e:
                errors += 1
                print(f'[Screener] ⚠️ {sym} 评分失败: {e}', flush=True)

    # Step 3: 排序 + 强制保留
    results.sort(key=lambda x: x['score'], reverse=True)

    # 强制包含BTCUSDT/ETHUSDT（主力，无论评分）
    force_syms = set()
    for sym in FORCE_INCLUDE:
        if sym in usdt_tickers:
            # 检查是否已在TOP N中
            top_syms = {r['symbol'] for r in results[:top_n]}
            if sym not in top_syms:
                # 找到它的位置并替换末位
                for r in results:
                    if r['symbol'] == sym:
                        results = [r] + [x for x in results if x['symbol'] != sym]
                        break
            force_syms.add(sym)

    top = results[:top_n]

    # Step 4: 打印摘要
    elapsed = round(time.time() - t0, 1)
    print(f'\n[Screener] TOP {top_n} 候选 ({elapsed}s, 错误={errors}):')
    print(f'  {"Symbol":<18} {"Score":>5} {"Price":>12} {"24H%":>7} {"RSI":>5} {"趋势":>7} {"OI%":>6} {"FR%":>8}')
    print(f'  {"-"*75}')
    for r in top:
        flag = '🔴' if r['score'] >= 55 else ('🟡' if r['score'] >= 40 else '⚪')
        forced = '★' if r['symbol'] in force_syms else ' '
        print(
            f'  {r["symbol"]:<18} {r["score"]:>5}分 {r["price"]:>12.4f} '
            f'{r["pct24h"]:>+7.2f}% {r["rsi"]:>5.1f} {r["trend"]:>7} '
            f'{r["oi_delta"]:>+6.1f}% {r["fr"]:>8.4f}% {flag}{forced}',
            flush=True
        )

    # Step 5: 写入候选文件
    output = {
        'ts':         time.time(),
        'generated':  time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        'total_scanned': len(usdt_tickers),
        'top_n':      top_n,
        'candidates': top,
        'full_scores': results[:20],   # 保存TOP20供复盘
    }
    out_path = DATA / 'scan_candidates.json'
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f'[Screener] ✅ 已写入 {out_path}', flush=True)

    # Step 5b: 暴涨检测 → pump_detected.json（供 pump_sector_relay.py 板块联动使用）
    # 设计院 2026-06-29 | 零AI消耗，纯REST数据
    PUMP_THRESHOLD = 30.0   # 24H涨幅≥30%视为暴涨
    pumped = [
        {'symbol': t['symbol'], 'pct24h': float(t['priceChangePercent']),
         'price': float(t['lastPrice']), 'volume': float(t['quoteVolume']),
         'ts': time.time()}
        for t in tickers
        if t['symbol'].endswith('USDT')
        and float(t['priceChangePercent']) >= PUMP_THRESHOLD
        and float(t['quoteVolume']) >= 20e6   # 最低$20M流动性
        and t['symbol'] not in BLACKLIST
    ]
    pumped.sort(key=lambda x: x['pct24h'], reverse=True)
    pump_out = {
        'ts': time.time(),
        'generated': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        'threshold_pct': PUMP_THRESHOLD,
        'count': len(pumped),
        'pumped': pumped[:20],   # TOP20暴涨标的
    }
    pump_path = DATA / 'pump_detected.json'
    pump_path.write_text(json.dumps(pump_out, ensure_ascii=False, indent=2))
    print(f'[Screener] 🔥 暴涨检测: {len(pumped)}个标的涨幅≥{PUMP_THRESHOLD}% → pump_detected.json', flush=True)

    # Step 6: 追加日志
    log_path = LOG / 'screener.log'
    with open(log_path, 'a') as f:
        summary = {
            'ts': time.time(),
            'elapsed': elapsed,
            'scanned': len(usdt_tickers),
            'top': [r['symbol'] for r in top],
            'scores': {r['symbol']: r['score'] for r in top},
        }
        f.write(json.dumps(summary) + '\n')

    return top


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='全市场纯脚本预筛器')
    ap.add_argument('--top', type=int, default=TOP_N, help=f'候选数量 (默认{TOP_N})')
    ap.add_argument('--min-vol', type=float, default=100, help='最低成交额 $M (默认100)')
    args = ap.parse_args()

    MIN_VOLUME_USD = args.min_vol * 1e6
    candidates = run(top_n=args.top)
    print(f'\n[Screener] 最终候选: {[r["symbol"] for r in candidates]}')

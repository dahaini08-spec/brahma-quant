#!/usr/bin/env python3
"""
ai_pro_screener.py — AI Pro选币层 → universal_asset_router候选输入
三方联合落地 矿脉5 | 设计院自主执行 2026-08-07

流程：
  Binance全市场成交量/涨跌幅异动扫描
  → 过滤规则（USDT永续合约、流动性充足、非稳定币）
  → Top20异动标的输出
  → 写入 data/ai_pro_candidates.json
  → brahma_scan_all读取候选列表 → 35维评分 → SQE门控

设计原则：
  不降低WR（SQE保障质量）
  只扩大机会池（从BTC/ETH 2个 → 全市场20个）
  数据完全来自Binance官方API（无第三方依赖）
"""

import json
import time
import requests
import argparse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / 'data'
CANDIDATES_FILE = DATA_DIR / 'ai_pro_candidates.json'

FAPI = 'https://fapi.binance.com'

# 排除列表（稳定币/指数/无意义合约）
EXCLUDE_SYMBOLS = {
    'USDCUSDT', 'BUSDUSDT', 'USDTUSDT', 'BTCDOMUSDT',
    'DEFIUSDT', 'ALTUSDT', 'BNXUSDT', 'COCOSUSDT',
}

# 最小流动性门槛（24H成交额 USD）
MIN_VOLUME_24H = 50_000_000  # 5000万美元


def fetch_all_tickers() -> list:
    """获取全市场永续合约24H行情"""
    try:
        resp = requests.get(f'{FAPI}/fapi/v1/ticker/24hr', timeout=10)
        tickers = resp.json()
        return [t for t in tickers
                if t.get('symbol', '').endswith('USDT')
                and t.get('symbol') not in EXCLUDE_SYMBOLS]
    except Exception as e:
        print(f"⚠️ 行情获取失败: {e}")
        return []


def fetch_oi_changes(symbols: list) -> dict:
    """批量获取OI变化（最近1H）"""
    oi_map = {}
    for sym in symbols[:30]:  # 只查Top30候选
        try:
            resp = requests.get(
                f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=3',
                timeout=5
            )
            data = resp.json()
            if len(data) >= 2:
                now_oi = float(data[-1].get('sumOpenInterestValue', 0))
                prev_oi = float(data[0].get('sumOpenInterestValue', 0))
                oi_map[sym] = round((now_oi - prev_oi) / prev_oi * 100, 2) if prev_oi else 0
        except Exception:
            oi_map[sym] = 0
    return oi_map


def score_candidate(ticker: dict, oi_change: float = 0) -> float:
    """
    候选标的异动评分
    成交额权重60% + 涨跌幅权重25% + OI变化15%
    """
    vol_24h   = float(ticker.get('quoteVolume', 0))
    chg_pct   = abs(float(ticker.get('priceChangePercent', 0)))
    vol_score = min(vol_24h / 500_000_000 * 60, 60)   # 5亿USD = 满分60
    chg_score = min(chg_pct / 10 * 25, 25)             # 10%涨跌 = 满分25
    oi_score  = min(abs(oi_change) / 5 * 15, 15)       # 5%OI变化 = 满分15
    return round(vol_score + chg_score + oi_score, 2)


def screen_candidates(top_n: int = 20) -> list:
    """扫描全市场，输出Top N异动候选"""
    print(f"[ai_pro_screener] 扫描全市场合约 {datetime.utcnow().strftime('%H:%M UTC')}")

    tickers = fetch_all_tickers()
    if not tickers:
        return []

    # 流动性过滤
    liquid = [t for t in tickers
              if float(t.get('quoteVolume', 0)) >= MIN_VOLUME_24H]
    print(f"  全市场: {len(tickers)}个 | 流动性充足: {len(liquid)}个")

    # 排除BTC/ETH（梵天已直接扫描）
    non_major = [t for t in liquid
                 if t['symbol'] not in ('BTCUSDT', 'ETHUSDT')]

    # 按成交额×涨跌幅排序，取Top50做OI查询
    pre_sorted = sorted(non_major,
                        key=lambda t: float(t.get('quoteVolume', 0)) * abs(float(t.get('priceChangePercent', 0))),
                        reverse=True)[:50]

    # 获取OI变化
    symbols_50 = [t['symbol'] for t in pre_sorted]
    oi_map = fetch_oi_changes(symbols_50)

    # 最终评分
    scored = []
    for t in pre_sorted:
        sym = t['symbol']
        oi_chg = oi_map.get(sym, 0)
        score = score_candidate(t, oi_chg)
        scored.append({
            'symbol':       sym,
            'price':        float(t.get('lastPrice', 0)),
            'change_pct':   round(float(t.get('priceChangePercent', 0)), 2),
            'volume_24h_m': round(float(t.get('quoteVolume', 0)) / 1_000_000, 1),
            'oi_change_1h': oi_chg,
            'candidate_score': score,
            'rank':         0,
        })

    # 取Top N
    top = sorted(scored, key=lambda x: x['candidate_score'], reverse=True)[:top_n]
    for i, c in enumerate(top):
        c['rank'] = i + 1

    return top


def write_candidates(candidates: list):
    """写入候选文件供brahma_scan_all读取"""
    output = {
        'ts':         time.time(),
        'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'count':      len(candidates),
        'candidates': candidates,
        'note':       'AI Pro选币层输出，供brahma_scan_all批量35维扫描',
    }
    with open(CANDIDATES_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top', type=int, default=20, help='候选标的数量（默认20）')
    parser.add_argument('--read', action='store_true', help='只读取当前候选列表')
    args = parser.parse_args()

    if args.read:
        if CANDIDATES_FILE.exists():
            with open(CANDIDATES_FILE) as f:
                data = json.load(f)
            print(f"候选列表 {data.get('updated_at')}，共{data['count']}个:")
            for c in data['candidates'][:10]:
                print(f"  #{c['rank']:>2} {c['symbol']:<15} "
                      f"chg={c['change_pct']:+.1f}% "
                      f"vol={c['volume_24h_m']:.0f}M "
                      f"score={c['candidate_score']:.1f}")
        else:
            print("⚠️ 无候选数据，请先运行扫描")
        return

    # 执行扫描
    candidates = screen_candidates(top_n=args.top)

    if not candidates:
        print("⚠️ 扫描失败或无候选标的")
        return

    write_candidates(candidates)

    print(f"[ai_pro_screener] ✅ Top{len(candidates)}候选标的:")
    for c in candidates[:10]:
        direction = '📈' if c['change_pct'] > 0 else '📉'
        print(f"  #{c['rank']:>2} {c['symbol']:<15} {direction} {c['change_pct']:+.1f}% "
              f"成交{c['volume_24h_m']:.0f}M$ OI{c['oi_change_1h']:+.1f}%")
    print(f"  → 写入 data/ai_pro_candidates.json")
    print(f"  → 待梵天35维扫描 → SQE门控 → 信号池")


if __name__ == '__main__':
    main()

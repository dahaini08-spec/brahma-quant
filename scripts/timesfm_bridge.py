#!/usr/bin/env python3
"""
scripts/timesfm_bridge.py — TimesFM-Lite → research_cache 写入桥接 v1.0
设计院 · 2026-06-21

职责：
  1. 对重点标的运行 TimesFM-Lite 预测
  2. 结果写入 data/research_cache/<SYM>_<DIR>.json
  3. brahma_core.py s_research 层自动读取（已有接口，零修改）

运行方式：
  python3 scripts/timesfm_bridge.py                    # 全量重点标的
  python3 scripts/timesfm_bridge.py --symbols BTC ETH  # 指定标的
  python3 scripts/timesfm_bridge.py --dry              # 仅打印，不写缓存

上限控制（STAR.md L0）：
  score 上限 8分（外部信号上限），体制死穴归零
  TTL = 3600s（1H刷新），CHOP体制不写入
"""

import sys, os, json, argparse, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent

# 导入 TimesFM-Lite
sys.path.insert(0, str(BASE / 'brahma_brain'))
from timesfm_lite import get_timesfm_score

CACHE_DIR = BASE / 'data' / 'research_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 重点标的（梵天主要交易对）
DEFAULT_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT',
    'ADAUSDT', 'AVAXUSDT', 'DOGEUSDT', 'NEARUSDT',
    'XRPUSDT', 'XLMUSDT',
]

DIRECTIONS = ['LONG', 'SHORT']


def get_klines(sym, interval='1h', limit=200):
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': sym, 'interval': interval, 'limit': limit}, timeout=6)
        return r.json() if r.status_code == 200 else []
    except:
        return []


def get_covariates(sym):
    """获取协变量：FR / LSR / OI变化 / RSI"""
    cov = {}
    try:
        r = requests.get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}', timeout=4)
        cov['funding_rate'] = float(r.json().get('lastFundingRate', 0))
    except: pass
    try:
        r = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
            params={'symbol': sym, 'period': '1h', 'limit': 2}, timeout=4)
        data = r.json()
        if data: cov['lsr'] = float(data[-1]['longShortRatio'])
    except: pass
    return cov


def get_regime(sym):
    try:
        rs = json.loads((BASE / 'data' / 'regime_state.json').read_text())
        return rs.get(sym, {}).get('confirmed', 'UNKNOWN')
    except:
        try:
            bs = json.loads((BASE / 'data' / 'brahma_state.json').read_text())
            return bs.get('regime_label') or bs.get('regime', 'UNKNOWN')
        except:
            return 'UNKNOWN'


def write_cache(sym, direction, score, meta, dry=False):
    """写入 research_cache，格式符合 external_signal.py 规范"""
    if 'CHOP' in meta.get('regime', ''):
        return  # CHOP体制不写入

    payload = {
        'score':   int(round(max(-8, min(8, score)))),  # 硬上限8分
        'sources': ['timesfm_lite'],
        'reason':  (
            f"TimesFM-Lite: p_dir={meta.get('p_direction',0.5):.2f} "
            f"conf={meta.get('confidence','?')} "
            f"pred={meta.get('pred_price',0):.2f} "
            f"Q10={meta.get('q10',0):.2f}~Q90={meta.get('q90',0):.2f}"
        ),
        'details': {
            'timesfm_lite': {
                'score':        score,
                'p_direction':  meta.get('p_direction'),
                'confidence':   meta.get('confidence'),
                'pred_price':   meta.get('pred_price'),
                'q10':          meta.get('q10'),
                'q25':          meta.get('q25'),
                'q75':          meta.get('q75'),
                'q90':          meta.get('q90'),
                'band_pct':     meta.get('band_pct'),
                'r_sq':         meta.get('r_sq'),
                'vol_h':        meta.get('vol_h'),
                'regime_coeff': meta.get('regime_coeff'),
            }
        },
        'ts':      time.time(),
        'expires': time.time() + 3600,
        'symbol':  sym,
        'direction': direction,
    }

    if dry:
        print(f"  [DRY] {sym}_{direction}: score={payload['score']} reason={payload['reason'][:60]}")
        return

    path = CACHE_DIR / f"{sym}_{direction}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def run(symbols, dry=False):
    now = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"🧠 TimesFM-Lite Bridge  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"{'标的':<14} {'方向':<7} {'score':>6} {'p_dir':>7} {'conf':>6} {'pred':>10} {'Q25~Q75'}")
    print(f"{'─'*75}")

    written = 0
    for sym in symbols:
        klines = get_klines(sym, '1h', 200)
        if not klines:
            print(f"  {sym}: ❌ K线获取失败")
            continue

        cov = get_covariates(sym)
        regime = get_regime(sym)

        for direction in DIRECTIONS:
            score, meta = get_timesfm_score(
                sym, direction, klines, regime,
                covariates=cov, horizon=8
            )
            if 'error' in meta:
                continue

            q25 = meta.get('q25', 0); q75 = meta.get('q75', 0)
            pred = meta.get('pred_price', 0)
            p_dir = meta.get('p_direction', 0.5)
            conf = meta.get('confidence', '?')
            cur = meta.get('cur_price', 0)

            score_str = f"{score:+.1f}"
            print(f"  {sym:<14} {direction:<7} {score_str:>6} "
                  f"{p_dir:>7.3f} {conf:>6} "
                  f"${pred:>9,.2f}  ${q25:,.2f}~${q75:,.2f}")

            write_cache(sym, direction, score, {**meta, 'regime': regime}, dry=dry)
            written += 1

    print(f"\n✅ 完成: {written}条缓存写入 → data/research_cache/")
    return written


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    syms = [s.upper() + 'USDT' if not s.endswith('USDT') else s.upper()
            for s in args.symbols]
    run(syms, dry=args.dry)

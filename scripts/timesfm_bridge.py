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
        klines = r.json() if r.status_code == 200 else []
        if not klines:
            return klines
        # [P0修复 2026-07-02] 用实时ticker价格覆盖最后一根未收盘K线的收盘价
        # 避免预测基准价格滞后（1H K线最后一根可能是59分钟前的价格）
        try:
            tr = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=4)
            if tr.status_code == 200:
                live_px = tr.json()['price']
                last = list(klines[-1])
                last[4] = live_px   # 覆盖收盘价 index=4
                last[2] = str(max(float(last[2]), float(live_px)))  # 更新最高价
                last[3] = str(min(float(last[3]), float(live_px)))  # 更新最低价
                klines[-1] = last
        except:
            pass  # 实时价格拉取失败时使用原始K线数据
        return klines
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

    # [P0修复 2026-07-03] 获取实时价作为cur_price，与pred_price明确区分
    try:
        _tr = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=4)
        cur_px = float(_tr.json()['price']) if _tr.status_code == 200 else meta.get('cur_price', 0)
    except:
        cur_px = meta.get('cur_price', 0)

    pred_px = meta.get('pred_price', 0)
    pred_chg = round((pred_px - cur_px) / cur_px * 100, 2) if cur_px > 0 else 0

    payload = {
        'score':   int(round(max(-8, min(8, score)))),  # 硬上限8分
        'sources': ['timesfm_lite'],
        'reason':  (
            f"TimesFM-Lite: p_dir={meta.get('p_direction',0.5):.2f} "
            f"conf={meta.get('confidence','?')} "
            f"cur={cur_px:.2f} pred={pred_px:.2f}({pred_chg:+.2f}%) "
            f"Q10={meta.get('q10',0):.2f}~Q90={meta.get('q90',0):.2f}"
        ),
        'details': {
            'timesfm_lite': {
                'score':        score,
                'p_direction':  meta.get('p_direction'),
                'confidence':   meta.get('confidence'),
                'cur_price':    cur_px,   # 实时价格（修复 2026-07-03）
                'pred_price':   meta.get('pred_price'),
                'pred_chg_pct': pred_chg,
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
    print(f"{'标的':<14} {'方向':<7} {'score':>6} {'p_dir':>7} {'conf':>6} {'当前价':>10} {'预测价(8H)':>12} {'Q25~Q75'}")
    print(f"{'─'*80}")

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
            # [修复 2026-07-03] 实时获取cur_price，不依赖meta缓存
            try:
                _tr2 = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}',timeout=4)
                cur = float(_tr2.json()['price']) if _tr2.status_code==200 else meta.get('cur_price',0)
            except:
                cur = meta.get('cur_price', 0)
            pred_chg = f"{(pred-cur)/cur*100:+.1f}%" if cur>0 else ""

            score_str = f"{score:+.1f}"
            print(f"  {sym:<14} {direction:<7} {score_str:>6} "
                  f"{p_dir:>7.3f} {conf:>6} "
                  f"${cur:>9,.2f}  ${pred:>9,.2f}({pred_chg})  ${q25:,.2f}~${q75:,.2f}")

            write_cache(sym, direction, score, {**meta, 'regime': regime}, dry=dry)
            written += 1

    # 设计院修复 2026-07-05: 只在有强信号时输出推送行，否则静默
    # 收集 score>=4 的强信号（外部信号满分8分，4分=置信度约0.6）
    strong = []
    for sym in syms:
        for d in ('LONG', 'SHORT'):
            cache_file = Path('data/research_cache') / f'{sym}_{d}.json'
            if cache_file.exists():
                try:
                    import json as _j
                    c = _j.loads(cache_file.read_text())
                    sc = float(c.get('score', 0) or 0)
                    p_d = float(c.get('meta', {}).get('p_direction', 0.5) or 0.5)
                    if sc >= 4 and p_d >= 0.60:
                        strong.append((sym, d, sc, p_d))
                except: pass

    if strong:
        strong.sort(key=lambda x: -x[2])
        parts = ' | '.join(f'{s} {d} +{sc:.1f}({pd:.3f})' for s, d, sc, pd in strong[:3])
        print(f'⏱️TimesFM强信号 | {parts}')
    else:
        print('HEARTBEAT_OK')

    return written


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    syms = [s.upper() + 'USDT' if not s.endswith('USDT') else s.upper()
            for s in args.symbols]
    run(syms, dry=args.dry)

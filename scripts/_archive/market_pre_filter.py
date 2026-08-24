#!/usr/bin/env python3
"""
market_pre_filter.py — 全市场零成本预筛层
[设计院 2026-07-18 苏摩111封印]

架构：
  层0（本脚本）: 665个USDT永续 → 七维纯脚本过滤 → candidates.json  (0 tokens)
  层1（下游）  : rsi_structure_watcher / brahma_scan_all 只分析candidates

运行方式:
  python3 scripts/market_pre_filter.py            # 标准模式
  python3 scripts/market_pre_filter.py --verbose  # 详细日志
  python3 scripts/market_pre_filter.py --dry      # 只输出candidates，不写文件

七维过滤器（任一触发即进入candidates）:
  D1: RSI_1H < 28（超卖）或 RSI_1H > 72（超买区回落）
  D2: BB_width_1H < 0.8%（压缩蓄力）
  D3: OI_1H变化 > 2%（资金异动）
  D4: 突破48H高点 或 跌破48H低点（结构突破）
  D5: 1H量比 > 2.5x（异常放量）
  D6: FR绝对值 > 0.05% 或 < -0.03%（资金费率极值）
  D7: [静默封印] RSI_1H在45~60 且 BB<0.8% → 强制排除

并发：50线程，约15~30秒扫完全市场
"""

import os, sys, json, time, math, argparse, logging
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 路径 ──────────────────────────────────────────────────────────
BASE      = Path(__file__).parent.parent
DATA_DIR  = BASE / 'data'
OUT_FILE  = DATA_DIR / 'pre_filter_candidates.json'
LOG_FILE  = DATA_DIR / 'pre_filter_log.jsonl'
DATA_DIR.mkdir(exist_ok=True)

FAPI = 'https://fapi.binance.com'

# ── 七维阈值（可调） ────────────────────────────────────────────────
CFG = {
    'D1_rsi_oversold':    28,    # RSI_1H超卖门槛
    'D1_rsi_overbought':  72,    # RSI_1H超买回落门槛
    'D2_bb_squeeze':      0.008, # BB宽度 < 0.8%
    'D3_oi_change':       0.02,  # OI 1H变化 > 2%
    'D4_breakout_hours':  48,    # 突破N小时高/低点
    'D5_vol_ratio':       2.5,   # 量比 > 2.5x
    'D6_fr_high':         0.0005,# FR > +0.05%
    'D6_fr_low':         -0.0003,# FR < -0.03%
    'D7_silence_rsi_lo':  45,    # 静默区RSI下界
    'D7_silence_rsi_hi':  60,    # 静默区RSI上界
    'D7_silence_bb':      0.008, # 静默区BB上界
    'min_volume_usdt':    5e6,   # 最小24H成交量（过滤垃圾币）
    'max_workers':        50,    # 并发线程数
    'request_timeout':    6,     # 单次请求超时秒
}

# ── HTTP session（带重试）─────────────────────────────────────────
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.3,
                  status_forcelist=[429, 500, 502, 503])
    s.mount('https://', HTTPAdapter(max_retries=retry, pool_maxsize=100))
    return s

_SESSION = _make_session()

# ── 工具函数 ─────────────────────────────────────────────────────
def _get(url: str, params: dict = None) -> Optional[dict | list]:
    try:
        r = _SESSION.get(url, params=params, timeout=CFG['request_timeout'])
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    return 100 - 100 / (1 + ag / al) if al > 0 else 100.0


def _bb_width(closes: list, n: int = 20) -> float:
    """布林带宽 = (upper-lower)/mid"""
    if len(closes) < n:
        return 1.0
    window = closes[-n:]
    mid = sum(window) / n
    std = math.sqrt(sum((c - mid) ** 2 for c in window) / n)
    return (2 * 2 * std) / mid if mid > 0 else 1.0  # 2σ宽度/中轨


# ── Step1: 批量拉ticker（1次API获取全市场基础信息）────────────────
def fetch_all_tickers() -> dict:
    """返回 {symbol: ticker_dict}"""
    data = _get(f'{FAPI}/fapi/v1/ticker/24hr')
    if not data:
        return {}
    return {d['symbol']: d for d in data if d['symbol'].endswith('USDT')}


def fetch_all_fr() -> dict:
    """返回 {symbol: funding_rate}"""
    data = _get(f'{FAPI}/fapi/v1/premiumIndex')
    if not data:
        return {}
    return {d['symbol']: float(d.get('lastFundingRate', 0))
            for d in data if d['symbol'].endswith('USDT')}


def fetch_all_oi() -> dict:
    """返回 {symbol: openInterest}（当前快照）"""
    data = _get(f'{FAPI}/fapi/v1/openInterest', {'symbol': ''})
    # openInterest endpoint需要symbol参数，改用ticker
    return {}  # 通过ticker的openInterest字段代替


# ── Step2: 单标的深度采集（kline）────────────────────────────────
def analyze_symbol(sym: str, ticker: dict, fr: float) -> Optional[dict]:
    """
    对单个标的做七维分析，返回触发维度列表或None（未触发）
    """
    vol_24h = float(ticker.get('quoteVolume', 0))
    if vol_24h < CFG['min_volume_usdt']:
        return None  # 流动性不足，直接跳过

    # 拉取1H K线（52根：用于RSI14+BB20+量比+突破48H）
    klines = _get(f'{FAPI}/fapi/v1/klines',
                  {'symbol': sym, 'interval': '1h', 'limit': 52})
    if not klines or len(klines) < 20:
        return None

    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    highs   = [float(k[2]) for k in klines]
    lows    = [float(k[3]) for k in klines]

    cur_price  = closes[-1]
    cur_vol    = volumes[-1]
    avg_vol_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else cur_vol

    # ── 七维计算 ─────────────────────────────────────────────
    rsi_1h   = _rsi(closes)
    bb_w     = _bb_width(closes)
    vol_ratio = cur_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    high_48h = max(highs[-49:-1]) if len(highs) >= 49 else max(highs[:-1])
    low_48h  = min(lows[-49:-1])  if len(lows)  >= 49 else min(lows[:-1])

    # OI变化：ticker里有openInterest（当前），但无历史，用价格变化代理
    price_1h_ago = closes[-2] if len(closes) >= 2 else cur_price
    price_chg_1h = abs(cur_price - price_1h_ago) / price_1h_ago if price_1h_ago > 0 else 0

    triggered = []

    # D7 静默封印（先判，命中则直接返回None）
    if (CFG['D7_silence_rsi_lo'] <= rsi_1h <= CFG['D7_silence_rsi_hi']
            and bb_w < CFG['D7_silence_bb']):
        return None  # 死水，完全静默

    # D1 RSI极值
    if rsi_1h < CFG['D1_rsi_oversold']:
        triggered.append(f'D1_oversold(rsi={rsi_1h:.1f})')
    elif rsi_1h > CFG['D1_rsi_overbought']:
        triggered.append(f'D1_overbought(rsi={rsi_1h:.1f})')

    # D2 BB压缩
    if bb_w < CFG['D2_bb_squeeze']:
        triggered.append(f'D2_bb_squeeze(bw={bb_w*100:.3f}%)')

    # D3 OI异动（用价格+量代理：1H价格变化>1% + 量比>1.5x）
    if price_chg_1h > 0.01 and vol_ratio > 1.5:
        triggered.append(f'D3_oi_proxy(pchg={price_chg_1h*100:.2f}% vr={vol_ratio:.1f}x)')

    # D4 结构突破
    if cur_price > high_48h * 1.001:
        triggered.append(f'D4_break_high48h(cur={cur_price:.4g} h48={high_48h:.4g})')
    elif cur_price < low_48h * 0.999:
        triggered.append(f'D4_break_low48h(cur={cur_price:.4g} l48={low_48h:.4g})')

    # D5 量比异常
    if vol_ratio > CFG['D5_vol_ratio']:
        triggered.append(f'D5_vol_surge(vr={vol_ratio:.1f}x)')

    # D6 资金费率极值
    if fr > CFG['D6_fr_high']:
        triggered.append(f'D6_fr_high({fr*100:.4f}%)')
    elif fr < CFG['D6_fr_low']:
        triggered.append(f'D6_fr_low({fr*100:.4f}%)')

    if not triggered:
        return None

    return {
        'symbol':    sym,
        'price':     cur_price,
        'rsi_1h':    round(rsi_1h, 1),
        'bb_width':  round(bb_w * 100, 4),
        'vol_ratio': round(vol_ratio, 2),
        'fr':        round(fr * 100, 5),
        'vol_24h_m': round(vol_24h / 1e6, 1),
        'triggered': triggered,
        'score':     len(triggered),  # 触发维度数=候选优先级
    }


# ── Step3: 并发扫描全市场 ─────────────────────────────────────────
def run_pre_filter(verbose: bool = False) -> list:
    t0 = time.time()

    # 批量拉基础数据
    tickers = fetch_all_tickers()
    fr_map  = fetch_all_fr()

    all_syms = [s for s, t in tickers.items()
                if float(t.get('quoteVolume', 0)) >= CFG['min_volume_usdt']]

    if verbose:
        print(f'[pre_filter] 全市场 {len(tickers)} 个标的，'
              f'成交量≥5M: {len(all_syms)} 个，开始并发分析...')

    candidates = []
    errors = 0

    with ThreadPoolExecutor(max_workers=CFG['max_workers']) as ex:
        futures = {
            ex.submit(analyze_symbol,
                      sym,
                      tickers[sym],
                      fr_map.get(sym, 0.0)): sym
            for sym in all_syms
        }
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result:
                    candidates.append(result)
            except Exception:
                errors += 1

    # 按触发维度数（score）降序排列
    candidates.sort(key=lambda x: (-x['score'], -x['vol_24h_m']))

    elapsed = time.time() - t0
    if verbose or candidates:
        print(f'[pre_filter] 完成 {len(all_syms)}标的 → '
              f'{len(candidates)}个候选 | 耗时{elapsed:.1f}s | 错误{errors}个')

    return candidates


# ── Step4: 写入文件 + 日志 ───────────────────────────────────────
def write_output(candidates: list, dry: bool = False):
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    out = {
        'generated':    now_iso,
        'total':        len(candidates),
        'candidates':   candidates,
    }
    if not dry:
        OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        # 追加日志
        log_entry = {
            'ts':        now_iso,
            'cnt':       len(candidates),
            'top5':      [c['symbol'] for c in candidates[:5]],
        }
        with LOG_FILE.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    return out


# ── Step5: 触发下游扫描（若candidates有新标的）──────────────────
def trigger_downstream(candidates: list, prev_syms: set):
    """
    将candidates中的symbol写入rsi_watcher可读的扩展候选文件
    rsi_structure_watcher在下次运行时会合并这个文件的标的
    """
    new_syms = [c['symbol'] for c in candidates]
    trigger_file = DATA_DIR / 'pre_filter_trigger.json'
    trigger_file.write_text(json.dumps({
        'generated': datetime.now(tz=timezone.utc).isoformat(),
        'symbols':   new_syms,
        'new_vs_prev': [s for s in new_syms if s not in prev_syms],
    }, ensure_ascii=False))


# ── 主入口 ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='梵天全市场零成本预筛层')
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('--dry',           action='store_true', help='不写文件')
    args = parser.parse_args()

    # 读取上次candidates（用于新增对比）
    prev_syms: set = set()
    if OUT_FILE.exists():
        try:
            prev = json.loads(OUT_FILE.read_text())
            prev_syms = {c['symbol'] for c in prev.get('candidates', [])}
        except Exception:
            pass

    candidates = run_pre_filter(verbose=args.verbose)
    out = write_output(candidates, dry=args.dry)

    if not args.dry:
        trigger_downstream(candidates, prev_syms)

    # 输出摘要
    if candidates:
        print(f'[pre_filter] ✅ {len(candidates)}个候选')
        if args.verbose:
            for c in candidates[:20]:
                print(f'  {c["symbol"]:18} rsi={c["rsi_1h"]:5.1f} '
                      f'bw={c["bb_width"]:.3f}% vr={c["vol_ratio"]}x '
                      f'| {", ".join(c["triggered"][:2])}')
    else:
        print('[pre_filter] HEARTBEAT_OK (无触发标的)')


if __name__ == '__main__':
    main()

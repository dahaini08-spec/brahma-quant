#!/usr/bin/env python3
# ponytail: ai_pro_screener 302行，有意为之，重构前先 grep 所有调用方
"""
ai_pro_screener.py — AI Pro选币层 → universal_asset_router候选输入
三方联合落地 矿脉5 | 设计院自主执行 2026-08-07
[预判型重构 2026-08-09 苏摩111批准]

流程：
  Binance全市场成交量/涨跌幅异动扫描
  → 过滤规则（USDT永续合约、流动性充足、非稳定币）
  → 「蓄力中的币」Top20候选输出（非「已涨的币」）
  → 写入 data/ai_pro_candidates.json
  → brahma_scan_all读取候选列表 → 35维评分 → SQE门控

设计原则（v2.0 预判型）：
  核心哲学变更：候选池 = 「底部蓄力中的币」而非「成交量已大/已涨的币」
  成交量/涨幅 降为流动性门槛，不作为评分维度
  评分维度全部来自「尚未发生的蓄力信号」：
    BBW压缩 / RSI低位 / 量能枯竭 / 距低点 / 负费率
  预期效果：brahma_scan_all --sector 候选质量大幅提升
  封印铁律：评分函数中「已发生的事」权重不得超过30%
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

# 最小流动性门槛（24H成交额 USD）— 仅作过滤，不计入评分
MIN_VOLUME_24H = 50_000_000  # 5000万美元

# ── 预判型评分阈值 [v2.0 2026-08-09] ─────────────────────────
BBW_TIGHT      = 15.0   # BB宽度 < 15% = 压缩蓄力（高分）
BBW_COMPRESS   = 25.0   # BB宽度 < 25% = 轻度压缩
RSI_OVERSOLD   = 30.0   # RSI < 30 = 超卖
RSI_LOW        = 40.0   # RSI < 40 = 低位
VOL_DRY        = 0.3    # 量比 < 0.3 = 极度枯竭
VOL_SHRINK     = 0.5    # 量比 < 0.5 = 萎缩
LOW_DIST_NEAR  = 10.0   # 距90日低点 < 10%
LOW_DIST_MID   = 20.0   # 距90日低点 < 20%


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


def fetch_funding_rates(symbols: list) -> dict:
    """批量获取资金费率"""
    fr_map = {}
    try:
        resp = requests.get(f'{FAPI}/fapi/v1/premiumIndex', timeout=8)
        data = resp.json()
        if isinstance(data, list):
            for d in data:
                sym = d.get('symbol', '')
                if sym in symbols:
                    fr_map[sym] = float(d.get('lastFundingRate', 0)) * 100
    except Exception:
        pass
    return fr_map


def score_candidate_v2(sym: str, ticker: dict, fr: float = 0) -> dict:
    """
    [v2.0 预判型] 候选标的蓄力评分
    封印铁律：评分维度全部来自「尚未发生的蓄力信号」
    
    维度权重：
      BBW压缩    = 40分（最重要：价格即将突破）
      RSI低位    = 25分（超卖蓄力）
      量能枯竭   = 15分（缩量整理完毕）
      距90日低点 = 10分（价格处于低位）
      负费率     = 10分（空头拥挤，逼空蓄力）
    
    成交量/涨幅：仅作流动性过滤门槛，不计入评分
    """
    score = 0
    notes = []

    try:
        # 获取1H K线（需BBW/RSI/量比）
        resp = requests.get(
            f'{FAPI}/fapi/v1/klines',
            params={'symbol': sym, 'interval': '1h', 'limit': 30},
            timeout=5
        )
        klines = resp.json()
        if not isinstance(klines, list) or len(klines) < 20:
            return {'score': 0, 'notes': ['数据不足'], 'bbw': 99, 'rsi': 50}

        closes = [float(k[4]) for k in klines]
        vols   = [float(k[7]) for k in klines]
        highs  = [float(k[2]) for k in klines]
        lows   = [float(k[3]) for k in klines]

        # ── 维度1: BBW压缩 (+40/+20) ──────────────────────────
        import statistics
        ma20  = sum(closes[-20:]) / 20
        std20 = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
        bbw   = std20 * 2 / ma20 * 100 if ma20 > 0 else 99

        if bbw < BBW_TIGHT:
            score += 40; notes.append(f'TIGHT({bbw:.1f}%)+40')
        elif bbw < BBW_COMPRESS:
            score += 20; notes.append(f'BB压缩({bbw:.1f}%)+20')

        # ── 维度2: RSI低位 (+25/+12) ──────────────────────────
        gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, 15)]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, 15)]
        ag = sum(gains) / 14; al = sum(losses) / 14
        rsi = 100 - (100 / (1 + ag / al)) if al > 0 else 50

        if rsi < RSI_OVERSOLD:
            score += 25; notes.append(f'RSI超卖({rsi:.0f})+25')
        elif rsi < RSI_LOW:
            score += 12; notes.append(f'RSI低位({rsi:.0f})+12')

        # ── 维度3: 量能枯竭 (+15/+8) ──────────────────────────
        avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else sum(vols[:-1]) / max(len(vols)-1, 1)
        cur_vol = vols[-1]
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

        if vol_ratio < VOL_DRY:
            score += 15; notes.append(f'量极度枯竭({vol_ratio:.2f}x)+15')
        elif vol_ratio < VOL_SHRINK:
            score += 8; notes.append(f'量萎缩({vol_ratio:.2f}x)+8')

        # ── 维度4: 距90日低点 (+10/+5) ─────────────────────────
        # 用日线获取90日低点
        try:
            dr = requests.get(
                f'{FAPI}/fapi/v1/klines',
                params={'symbol': sym, 'interval': '1d', 'limit': 90},
                timeout=4
            )
            dk = dr.json()
            if isinstance(dk, list) and len(dk) >= 10:
                dl = [float(k[3]) for k in dk]
                low90 = min(dl)
                dist_low = (closes[-1] / low90 - 1) * 100 if low90 > 0 else 99
                if dist_low < LOW_DIST_NEAR:
                    score += 10; notes.append(f'近90D低点({dist_low:.1f}%)+10')
                elif dist_low < LOW_DIST_MID:
                    score += 5; notes.append(f'偏低位({dist_low:.1f}%)+5')
        except Exception:
            dist_low = 99

        # ── 维度5: 负费率 (+10/+5) ─────────────────────────────
        if fr < -0.05:
            score += 10; notes.append(f'极端负费率({fr:.3f}%)+10')
        elif fr < -0.01:
            score += 5; notes.append(f'负费率({fr:.3f}%)+5')

    except Exception as e:
        return {'score': 0, 'notes': [f'err:{e}'], 'bbw': 99, 'rsi': 50}

    return {
        'score': round(score, 1),
        'notes': notes,
        'bbw':   round(bbw, 1) if 'bbw' in dir() else 99,
        'rsi':   round(rsi, 1) if 'rsi' in dir() else 50,
    }


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

    # 排除BTC/ETH/主流（梵天已直接扫描）
    non_major = [t for t in liquid
                 if t['symbol'] not in ('BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT')]

    # [v2.0] 过滤掉24H已大涨的币（>20%）— 蓄力候选不该是今日已爆发的
    pre_candidates = [
        t for t in non_major
        if abs(float(t.get('priceChangePercent', 0))) < 20.0
    ]

    # 按成交量排序取Top80做精评（流动性门槛）
    pre_sorted = sorted(pre_candidates,
                        key=lambda t: float(t.get('quoteVolume', 0)),
                        reverse=True)[:80]

    # 批量获取资金费率（0额外请求）
    symbols_80 = [t['symbol'] for t in pre_sorted]
    fr_map = fetch_funding_rates(symbols_80)

    # [v2.0] 预判型评分：对每个候选做BBW/RSI/量比/低点/FR评分
    scored = []
    for t in pre_sorted:
        sym = t['symbol']
        fr  = fr_map.get(sym, 0)
        result = score_candidate_v2(sym, t, fr)
        score  = result['score']
        if score <= 0:
            continue  # 无蓄力信号，跳过
        scored.append({
            'symbol':          sym,
            'price':           float(t.get('lastPrice', 0)),
            'change_pct':      round(float(t.get('priceChangePercent', 0)), 2),
            'volume_24h_m':    round(float(t.get('quoteVolume', 0)) / 1_000_000, 1),
            'funding_rate':    round(fr, 4),
            'bbw':             result.get('bbw', 99),
            'rsi':             result.get('rsi', 50),
            'squeeze_notes':   result.get('notes', []),
            'candidate_score': score,
            'rank':            0,
        })

    # 按蓄力评分排序，取TopN
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

    print(f"[ai_pro_screener v2.0 预判型] ✅ Top{len(candidates)}蓄力候选:")
    for c in candidates[:10]:
        notes_str = ' / '.join(c.get('squeeze_notes', [])[:2])
        print(f"  #{c['rank']:>2} {c['symbol']:<15} score={c['candidate_score']:.0f}  [{notes_str}]")
    print(f"  → 写入 data/ai_pro_candidates.json")
    print(f"  → 候选池已从[已涨的币]改为[底部蓄力的币]")
    print(f"  → 待梵天35维扫描 → SQE门控 → 信号池")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
recent_signal_match.py — 标的专属90天信号匹配引擎
P2改革 2026-09-12 苏摩111封印

替代HCME全局4565条案例库（相似度0.191=随机猜）
改为最近90天同标的实盘信号 → 5特征匹配 → 相似度>0.6才输出

5个关键特征：
  1. 体制 (regime)
  2. 方向 (direction)
  3. OI信号 (oi_signal)
  4. 清算距离 (liq_dist_pct)
  5. 大户分歧 (sm_divergence)

数据源：data/live_signal_log.jsonl（signal_settler写入的实盘信号）
输出：匹配案例 + WR + EV参考
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

DATA = Path(__file__).parent.parent / 'data'
SIGNAL_LOG = DATA / 'live_signal_log.jsonl'
WR_MATRIX = DATA / 'wr_matrix_live.json'

def load_recent_signals(symbol: str, days: int = 90) -> List[Dict]:
    """加载最近N天同标的已结算信号"""
    if not SIGNAL_LOG.exists():
        return []
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - days * 86400
    sigs = []
    for line in SIGNAL_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
        except Exception:
            continue
        ts = float(s.get('ts', 0))
        if ts < cutoff:
            continue
        sym = s.get('symbol', '')
        if sym.upper() != symbol.upper():
            continue
        if not s.get('outcome') or s.get('outcome') == 'pending':
            continue
        sigs.append(s)
    sigs.sort(key=lambda x: float(x.get('ts', 0)), reverse=True)
    return sigs

def extract_features(sig: Dict) -> Dict:
    """提取5个关键特征"""
    return {
        'regime': sig.get('regime', 'UNKNOWN'),
        'direction': sig.get('direction', 'NONE'),
        'oi_signal': sig.get('oi_signal', 'NO_DATA'),
        'liq_dist_pct': sig.get('liq_dist_pct', 0),
        'sm_divergence': sig.get('sm_divergence', 0),
    }

def extract_current_features(regime: str, direction: str, oi_signal: str,
                               liq_dist_pct: float, sm_divergence: float) -> Dict:
    """提取当前信号的特征"""
    return {
        'regime': regime,
        'direction': direction,
        'oi_signal': oi_signal,
        'liq_dist_pct': liq_dist_pct,
        'sm_divergence': sm_divergence,
    }

def calc_similarity(current: Dict, historical: Dict) -> float:
    """计算5特征相似度 (0~1)"""
    score = 0.0
    weights = {
        'regime': 0.25,
        'direction': 0.25,
        'oi_signal': 0.20,
        'liq_dist_pct': 0.15,
        'sm_divergence': 0.15,
    }
    # 精确匹配
    for k in ('regime', 'direction', 'oi_signal'):
        if current.get(k) == historical.get(k):
            score += weights[k]
        elif current.get(k, '').split('_')[0] == historical.get(k, '').split('_')[0]:
            score += weights[k] * 0.5  # 同前缀（BULL_TREND vs BULL_EARLY）
    # 数值匹配（容差10%）
    for k in ('liq_dist_pct', 'sm_divergence'):
        c = abs(current.get(k, 0))
        h = abs(historical.get(k, 0))
        if c == 0 and h == 0:
            score += weights[k]
        elif max(c, h) > 0:
            diff = abs(c - h) / max(c, h)
            if diff < 0.1:
                score += weights[k]
            elif diff < 0.3:
                score += weights[k] * 0.5
    return round(score, 3)

def match(symbol: str, regime: str, direction: str, oi_signal: str,
          liq_dist_pct: float = 0, sm_divergence: float = 0,
          min_similarity: float = 0.6, max_results: int = 5) -> Dict:
    """
    匹配最近90天同标的信号
    返回：匹配案例 + WR + EV参考
    """
    sigs = load_recent_signals(symbol)
    if not sigs:
        return {
            'matched': False,
            'reason': f'{symbol}最近90天无已结算信号',
            'total_signals': 0,
        }

    current = extract_current_features(regime, direction, oi_signal,
                                         liq_dist_pct, sm_divergence)

    matches = []
    for s in sigs:
        hist = extract_features(s)
        sim = calc_similarity(current, hist)
        if sim >= min_similarity:
            outcome = s.get('outcome', '')
            is_win = outcome in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT', 'WIN')
            pnl = s.get('pnl_pct', 0)
            matches.append({
                'similarity': sim,
                'date': s.get('ts', 0),
                'regime': hist['regime'],
                'direction': hist['direction'],
                'outcome': outcome,
                'win': is_win,
                'pnl_pct': pnl,
                'score': s.get('score', 0),
            })

    matches.sort(key=lambda x: x['similarity'], reverse=True)
    matches = matches[:max_results]

    if not matches:
        return {
            'matched': False,
            'reason': f'无相似度≥{min_similarity}的案例（{len(sigs)}条已结算信号中无匹配）',
            'total_signals': len(sigs),
            'best_similarity': max(
                (calc_similarity(current, extract_features(s)) for s in sigs),
                default=0
            ),
        }

    wins = sum(1 for m in matches if m['win'])
    total = len(matches)
    wr = wins / total if total else 0
    avg_pnl = sum(m['pnl_pct'] for m in matches) / total if total else 0

    return {
        'matched': True,
        'total_signals': len(sigs),
        'matched_count': total,
        'wr': round(wr * 100, 1),
        'avg_pnl': round(avg_pnl, 3),
        'ev': round(wr * avg_pnl if avg_pnl > 0 else -wr * abs(avg_pnl), 3),
        'matches': matches,
        'verdict': f'近90天{total}条相似(WR={wr*100:.0f}% PnL={avg_pnl:+.2f}%)' if total else '无匹配',
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    r = match(sym, 'BULL_TREND', 'LONG', 'SHORT_SQUEEZE', 0.06, -0.15)
    print(json.dumps(r, indent=2, ensure_ascii=False))

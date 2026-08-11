#!/usr/bin/env python3
"""
consensus_engine.py
设计院封印 2026-08-02 | 苏摩授权

四路信号共识投票引擎 — 消除各自为战
  输入: 主信号(brahma) + OI高级 + 暴涨猎手 + 体制
  输出: 统一方向共识 + 冲突告警

解决: OI看多 vs 主信号BEAR做空的割裂
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

def get_main_signal_bias() -> dict:
    """主信号最新方向"""
    try:
        lines = (DATA / 'live_signal_log.jsonl').read_text().strip().split('\n')
        for l in reversed(lines):
            d = json.loads(l)
            if d.get('symbol') in ('BTCUSDT', 'ETHUSDT') and d.get('score', 0):
                return {
                    'source': 'main_signal',
                    'symbol': d['symbol'],
                    'direction': d.get('direction', '?'),
                    'score': d.get('score', 0),
                    'regime': d.get('regime', '?'),
                    'ts': d.get('ts_iso', ''),
                }
    except:
        pass
    return {}

def get_oi_bias() -> dict:
    """OI高级最新信号方向"""
    try:
        lines = (DATA / 'oi_advanced_signals.jsonl').read_text().strip().split('\n')
        now = time.time()
        for l in reversed(lines):
            d = json.loads(l)
            pushed = d.get('pushed_at', '')
            # 只看12H内的OI信号
            if pushed:
                try:
                    ts = datetime.fromisoformat(pushed.replace('Z', '+00:00'))
                    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                    if age_h > 12:
                        continue
                except:
                    pass
            direction = d.get('direction_bias', 'LONG')
            watch_only = d.get('_watch_only', False)
            return {
                'source': 'oi_advanced',
                'symbol': d.get('symbol', '?'),
                'direction': direction,
                'score': d.get('oi_score', d.get('score', 0)),
                'watch_only': watch_only,
                'ts': pushed,
            }
    except:
        pass
    return {}

def get_regime_bias() -> dict:
    """体制方向（最权威）"""
    try:
        regime = json.loads((DATA / 'regime_state.json').read_text())
        btc_r = regime.get('BTCUSDT', {})
        confirmed = btc_r.get('confirmed', '?') if isinstance(btc_r, dict) else str(btc_r)
        if 'BEAR' in confirmed:
            direction = 'SHORT'
        elif 'BULL' in confirmed:
            direction = 'LONG'
        else:
            direction = 'NEUTRAL'
        return {
            'source': 'regime',
            'symbol': 'BTCUSDT',
            'direction': direction,
            'regime': confirmed,
            'weight': 3,  # 体制权重最高
        }
    except:
        return {}

def get_pump_hunter_bias() -> dict:
    """暴涨猎手最新信号"""
    try:
        log = BASE / 'dharma' / 'pump_hunter' / 'scan_log.jsonl'
        lines = log.read_text().strip().split('\n')
        now = time.time()
        for l in reversed(lines):
            d = json.loads(l)
            ts_str = d.get('ts', '')
            alerts = d.get('alerts', 0)
            if alerts and alerts > 0:
                return {
                    'source': 'pump_hunter',
                    'direction': 'LONG',  # 暴涨猎手永远是做多信号
                    'alerts': alerts,
                    'ts': ts_str,
                }
    except:
        pass
    return {'source': 'pump_hunter', 'direction': 'NONE', 'alerts': 0}

def run_consensus() -> dict:
    """
    四路投票 → 统一方向
    权重: 体制×3 > 主信号×2 > OI×1 > 暴涨猎手×1
    """
    signals = {
        'regime':       get_regime_bias(),
        'main_signal':  get_main_signal_bias(),
        'oi_advanced':  get_oi_bias(),
        'pump_hunter':  get_pump_hunter_bias(),
    }

    weights = {'regime': 3, 'main_signal': 2, 'oi_advanced': 1, 'pump_hunter': 1}

    score_long  = 0
    score_short = 0
    conflicts   = []

    for src, sig in signals.items():
        if not sig:
            continue
        w = weights.get(src, 1)
        direction = sig.get('direction', 'NONE')

        # OI信号：如果是WATCH_ONLY，权重归0
        if sig.get('watch_only'):
            direction = 'WATCH'

        if direction == 'SHORT':
            score_short += w
        elif direction == 'LONG':
            score_long += w

    total = score_long + score_short
    consensus_direction = 'SHORT' if score_short > score_long else ('LONG' if score_long > score_short else 'NEUTRAL')
    confidence = max(score_short, score_long) / max(total, 1)

    # 检测冲突
    regime_dir = signals.get('regime', {}).get('direction', '')
    oi_dir = signals.get('oi_advanced', {}).get('direction', '')
    if oi_dir and oi_dir not in ('NONE', 'WATCH', 'NEUTRAL') and oi_dir != regime_dir:
        conflicts.append(f'OI信号{oi_dir} vs 体制{regime_dir}')

    pump_alerts = signals.get('pump_hunter', {}).get('alerts', 0)
    if pump_alerts and pump_alerts > 0 and regime_dir == 'SHORT':
        conflicts.append(f'暴涨猎手触发({pump_alerts}个) vs 体制BEAR做空')

    result = {
        'consensus_direction': consensus_direction,
        'confidence': round(confidence, 2),
        'score_short': score_short,
        'score_long': score_long,
        'conflicts': conflicts,
        'signals': signals,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    # 写入状态
    (DATA / 'consensus_state.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    return result

if __name__ == '__main__':
    result = run_consensus()
    print(f'=== 梵天四路共识引擎 ===')
    print(f'共识方向: {result["consensus_direction"]} (置信度{result["confidence"]*100:.0f}%)')
    print(f'  做空票数: {result["score_short"]}  做多票数: {result["score_long"]}')
    print()
    for src, sig in result['signals'].items():
        if sig:
            direction = sig.get('direction', 'N/A')
            watch = ' ⚠️WATCH' if sig.get('watch_only') else ''
            print(f'  [{src:15s}] {direction}{watch}')
    if result['conflicts']:
        print()
        print('⚠️ 方向冲突告警:')
        for c in result['conflicts']:
            print(f'  ❌ {c}')
    else:
        print()
        print('✅ 无方向冲突，信号一致')

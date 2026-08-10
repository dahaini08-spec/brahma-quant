#!/usr/bin/env python3
"""
adaptive_threshold.py — 信号阈值自适应引擎
P1落地 [苏摩111 2026-07-19]

核心逻辑：
  基于历史信号分布、体制WR、近期表现，动态调整触发阈值
  避免固定阈值在不同市场环境下失效

自适应规则：
  1. 高质量信号稀缺（近7天score≥155 < 3个）→ 降低观察阈值至138
  2. 体制WR数据充分（n≥20）→ 按WR校准阈值
  3. 近期连续亏损（3笔以上）→ 提高阈值至160
  4. 体制CHOP_MID → 阈值上移+10（震荡期严格过滤）
  5. 季节性/宏观压力 → 引入临时修正
"""
import json, time, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE = Path(__file__).parent.parent
SIGNAL_LOG = BASE / 'data' / 'live_signal_log.jsonl'
THRESHOLD_FILE = BASE / 'data' / 'adaptive_threshold.json'

# 阈值边界
THRESHOLD_MIN = 130   # 最低不能低于130
THRESHOLD_MAX = 175   # 最高不能超过175
THRESHOLD_BASE = 155  # 基准阈值（MEMORY.md铁证）

def load_recent_signals(days: int = 30) -> list:
    """加载近N天信号"""
    cutoff = time.time() - days * 86400
    signals = []
    if not SIGNAL_LOG.exists():
        return []
    with open(SIGNAL_LOG) as f:
        for line in f:
            try:
                s = json.loads(line)
                ts = s.get('ts', 0)
                score = float(s.get('score_final', s.get('score', 0)) or 0)
                if ts > cutoff and score > 50:
                    signals.append(s)
            except: pass
    return signals

def compute_adaptive_threshold(regime: str = None) -> dict:
    """
    计算当前最优触发阈值
    返回: {threshold, observe_threshold, reason, confidence}
    """
    signals_7d = load_recent_signals(days=7)
    signals_30d = load_recent_signals(days=30)

    threshold = THRESHOLD_BASE
    observe = 138
    reasons = []
    adjustments = []

    # ── 规则1: 高分信号稀缺性检测 ─────────────────────────
    high_score_7d = [s for s in signals_7d if float(s.get('score_final', s.get('score',0)) or 0) >= 155]
    if len(high_score_7d) < 3:
        # 近7天高分信号太少，适当降低观察阈值
        observe = max(130, observe - 5)
        reasons.append(f"近7天高分信号仅{len(high_score_7d)}个，观察阈值降至{observe}")
        adjustments.append(-5)
    elif len(high_score_7d) > 15:
        # 信号太多，提高阈值
        threshold = min(THRESHOLD_MAX, threshold + 5)
        reasons.append(f"近7天高分信号{len(high_score_7d)}个，阈值提高至{threshold}")
        adjustments.append(+5)

    # ── 规则2: 体制专项调整 ────────────────────────────────
    regime_adj = {
        'CHOP_MID':      +10,   # 震荡期严格过滤
        'BULL_TREND':    0,     # 牛市基准
        'BEAR_TREND':    -5,    # 熊市空单机会多，稍降
        'BEAR_RECOVERY': -5,    # 反弹期多单机会
        'BEAR_EARLY':    +5,    # 熊市初期保守
        'BULL_EARLY':    +5,    # 牛市初期保守
    }
    if regime and regime in regime_adj:
        adj = regime_adj[regime]
        threshold = max(THRESHOLD_MIN, min(THRESHOLD_MAX, threshold + adj))
        if adj != 0:
            reasons.append(f"体制{regime}调整{'+' if adj>0 else ''}{adj}→{threshold}")
            adjustments.append(adj)

    # ── 规则3: 近期连续亏损检测 ────────────────────────────
    try:
        from expert_memory_manager import load_memory
        import sys as _sys; _sys.path.insert(0, str(BASE/'scripts'))
        mem = load_memory()
        recent_trades = mem.get('experts',{}).get('合约交易员',{}).get('recent_trades',[])
        if len(recent_trades) >= 3:
            last3 = recent_trades[-3:]
            losses = sum(1 for t in last3 if t.get('pnl',0) <= 0)
            if losses >= 3:
                threshold = min(THRESHOLD_MAX, threshold + 10)
                reasons.append(f"近3笔连续亏损，阈值提高+10→{threshold}")
                adjustments.append(+10)
            elif losses == 0:
                # 连赢3笔，可以稍微放松
                observe = max(THRESHOLD_MIN, observe - 3)
                reasons.append(f"近3笔连续盈利，观察阈值-3→{observe}")
    except:
        pass

    # ── 规则4: 分数分布校准 ────────────────────────────────
    if len(signals_30d) >= 30:
        scores_30d = [float(s.get('score_final', s.get('score',0)) or 0) for s in signals_30d]
        p75 = sorted(scores_30d)[int(len(scores_30d)*0.75)]
        p90 = sorted(scores_30d)[int(len(scores_30d)*0.90)]
        # 阈值应在p75~p90之间
        calibrated = max(THRESHOLD_MIN, min(THRESHOLD_MAX, int((p75 + p90) / 2)))
        if abs(calibrated - threshold) > 10:
            old = threshold
            threshold = calibrated
            reasons.append(f"分布校准: p75={p75:.0f} p90={p90:.0f} → 阈值校准至{threshold}")

    # 计算置信度（样本量）
    confidence = min(1.0, len(signals_30d) / 50)  # 50个样本=满置信

    result = {
        'threshold':         round(threshold),
        'observe_threshold': round(observe),
        'base_threshold':    THRESHOLD_BASE,
        'regime':            regime or 'UNKNOWN',
        'reasons':           reasons,
        'total_adj':         sum(adjustments),
        'confidence':        round(confidence, 2),
        'sample_7d':         len(signals_7d),
        'sample_30d':        len(signals_30d),
        'ts':                time.time(),
        'updated_at':        datetime.now(timezone.utc).isoformat()
    }

    # 持久化
    THRESHOLD_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result

def get_current_threshold(regime: str = None) -> int:
    """获取当前有效阈值（带缓存，1h更新一次）"""
    if THRESHOLD_FILE.exists():
        try:
            d = json.loads(THRESHOLD_FILE.read_text())
            # 缓存有效期1小时，且体制未变化
            if time.time() - d.get('ts', 0) < 3600:
                if regime is None or d.get('regime') == regime:
                    return d['threshold']
        except: pass
    result = compute_adaptive_threshold(regime)
    return result['threshold']

if __name__ == '__main__':
    import sys
    regime = sys.argv[1] if len(sys.argv) > 1 else None
    result = compute_adaptive_threshold(regime)
    print(f"\n🎯 信号阈值自适应计算结果")
    print(f"  当前体制:   {result['regime']}")
    print(f"  基准阈值:   {result['base_threshold']}")
    print(f"  自适应阈值: {result['threshold']} (信号推送)")
    print(f"  观察阈值:   {result['observe_threshold']} (观察候选)")
    print(f"  置信度:     {result['confidence']*100:.0f}% ({result['sample_30d']}个30天样本)")
    print(f"  调整理由:")
    for r in result['reasons']:
        print(f"    - {r}")
    if not result['reasons']:
        print(f"    - 无调整，使用基准阈值")

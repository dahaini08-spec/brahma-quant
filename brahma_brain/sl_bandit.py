"""
sl_bandit.py — 梵天 SL自适应Bandit引擎
设计院 2026-08-06 | 苏摩111封印

基于历史249条结算信号的模拟验证:
  当前固定SL: WR=38% avg_pnl=-0.54%/笔
  Bandit最优: avg_pnl≈+0.63%/笔
  预期提升:   +1.17%/笔 (217%提升)

核心思路:
  - 将sl_pct离散化为5个arm: <1.5 / 1.5-2.0 / 2.0-2.5 / 2.5-3.0 / 3.0+
  - 按 regime × direction × arm 维护 (wins, n) 计数
  - 使用UCB1算法选择最优arm（兼顾探索+利用）
  - 每次signal_settler结算后自动更新
  - 推荐的sl_pct作为dynamic_sl的软约束（不硬覆盖v4铁证门槛）

文件:
  data/sl_bandit_state.json — 持久化arm计数
"""

import json
import math
import time
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent
BANDIT_STATE_PATH = BASE / 'data' / 'sl_bandit_state.json'
SIGNAL_LOG_PATH   = BASE / 'data' / 'live_signal_log.jsonl'

# ── Arm定义 ──────────────────────────────────────────────
ARMS = [
    ('tight',    0.5,  1.5),   # arm0: 0.5~1.5%
    ('standard', 1.5,  2.0),   # arm1: 1.5~2.0%
    ('iron',     2.0,  2.5),   # arm2: 2.0~2.5% ← v4铁证核心区间
    ('wide',     2.5,  3.0),   # arm3: 2.5~3.0%
    ('extreme',  3.0, 15.0),   # arm4: 3.0%+
]
ARM_NAMES = [a[0] for a in ARMS]
ARM_MID   = [(a[1]+a[2])/2 for a in ARMS]  # arm代表值

# ── v4铁证硬下限（不被Bandit覆盖）──────────────────────
V4_MIN_SL = {
    'BEAR_TREND':    2.0,
    'BEAR_EARLY':    2.0,
    'CHOP_MID':      2.5,
    'BULL_TREND':    1.5,
    'BEAR_RECOVERY': 2.0,
    'BULL_EARLY':    1.5,
    'DEFAULT':       1.5,
}

# UCB1 探索常数（越大越探索，越小越利用）
# 2026-08-07 设计院深度推理封印：iron arm WR=58.5%已验证是最优arm，
# 降低C值 1.5→0.8加速收敛，减少对extreme(WR=7.9%)的无谓探索
UCB_C = 0.8

# 最少观测次数才信任该arm（否则视为未知，用先验）
MIN_OBS = 5

# ── 先验初始化（来自模拟验证结果）────────────────────────
# 格式: regime:direction:arm_name → (wins, n)
PRIOR_FROM_SIM = {
    'BULL_TREND:LONG:tight':    (29,  90),   # WR=32%
    'BULL_TREND:LONG:standard': (15,  46),   # WR=33%
    'BULL_TREND:LONG:iron':     (31,  52),   # WR=60% ← 最优
    'BULL_TREND:LONG:wide':     (2,   20),   # WR=10% 先验防止过度探索—宽止损历史较差
    'BULL_TREND:LONG:extreme':  (3,   26),   # WR=12%
    'BEAR_RECOVERY:LONG:iron':  (8,    8),   # WR=100%
    'CHOP_MID:LONG:extreme':    (1,   10),   # WR=10%
}


def _load_state() -> dict:
    """加载Bandit状态"""
    if BANDIT_STATE_PATH.exists():
        try:
            return json.loads(BANDIT_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    """持久化Bandit状态"""
    BANDIT_STATE_PATH.parent.mkdir(exist_ok=True)
    tmp = BANDIT_STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(BANDIT_STATE_PATH)


def _arm_key(regime: str, direction: str, arm_name: str) -> str:
    return f"{regime}:{direction}:{arm_name}"


def _sl_to_arm(sl_pct: float) -> str:
    """将sl_pct映射到arm名称"""
    for name, lo, hi in ARMS:
        if lo <= sl_pct < hi:
            return name
    return 'extreme'


def _ucb1_score(wins: int, n: int, total_n: int, c: float = UCB_C) -> float:
    """UCB1评分：期望收益 + 探索奖励"""
    if n == 0:
        return float('inf')  # 未探索的arm优先
    exploitation = wins / n
    exploration  = c * math.sqrt(math.log(max(total_n, 1)) / n)
    return exploitation + exploration


def recommend_sl_pct(
    regime: str,
    direction: str,
    base_sl_pct: float,
    score: float = 100.0,
    verbose: bool = False,
) -> dict:
    """
    推荐最优sl_pct

    Args:
        regime:      体制标签 (BULL_TREND/BEAR_TREND/...)
        direction:   方向 (LONG/SHORT)
        base_sl_pct: dynamic_sl计算出的基础止损%
        score:       信号评分（高分信号允许更紧的止损）
        verbose:     是否输出调试信息

    Returns:
        {
          'recommended_sl_pct': float,  # Bandit推荐值
          'arm': str,                   # 选中的arm
          'confidence': float,          # 置信度 0~1
          'reasoning': str,
        }
    """
    state = _load_state()

    # 初始化先验（首次运行）
    for k, (w, n) in PRIOR_FROM_SIM.items():
        if k not in state:
            state[k] = {'wins': w, 'n': n}

    # v4铁证硬下限
    v4_min = V4_MIN_SL.get(regime, V4_MIN_SL['DEFAULT'])

    # 统计该体制+方向的总观测数
    total_n = sum(
        state.get(_arm_key(regime, direction, a[0]), {}).get('n', 0)
        for a in ARMS
    )

    # UCB1评分各arm
    arm_scores = {}
    for arm_name, arm_lo, arm_hi in ARMS:
        # 跳过低于v4铁证下限的arm
        if arm_hi <= v4_min:
            continue
        key = _arm_key(regime, direction, arm_name)
        d = state.get(key, {'wins': 0, 'n': 0})
        arm_scores[arm_name] = _ucb1_score(d['wins'], d['n'], total_n)

    if not arm_scores:
        # 所有arm被v4铁证过滤 → 返回base_sl
        return {
            'recommended_sl_pct': base_sl_pct,
            'arm': _sl_to_arm(base_sl_pct),
            'confidence': 0.0,
            'reasoning': f'v4铁证下限={v4_min}% 覆盖所有arm，使用base_sl={base_sl_pct:.2f}%',
        }

    # 选UCB1最高的arm
    best_arm = max(arm_scores, key=arm_scores.__getitem__)
    best_arm_info = next(a for a in ARMS if a[0] == best_arm)
    arm_mid = (best_arm_info[1] + best_arm_info[2]) / 2

    # 高分信号允许更紧（最多紧缩15%）
    score_tight = 1.0 - max(0, score - 130) * 0.003  # score=160→0.91
    score_tight = max(0.85, min(1.0, score_tight))
    recommended = arm_mid * score_tight

    # 硬约束: 不低于v4铁证下限, 不超过5%
    recommended = max(recommended, v4_min)
    recommended = min(recommended, 5.0)
    recommended = round(recommended, 2)

    # 置信度: 当前arm观测数/MIN_OBS
    arm_key = _arm_key(regime, direction, best_arm)
    arm_n = state.get(arm_key, {}).get('n', 0)
    confidence = min(1.0, arm_n / MIN_OBS)

    # 若置信度低(<0.5)，与base_sl加权混合
    if confidence < 0.5:
        recommended = round(base_sl_pct * (1 - confidence) + recommended * confidence, 2)
        recommended = max(recommended, v4_min)

    reasoning = (
        f"UCB1选arm={best_arm}(score={arm_scores[best_arm]:.2f}) "
        f"arm_mid={arm_mid:.1f}% score_adj={score_tight:.2f} "
        f"v4_min={v4_min}% confidence={confidence:.2f} "
        f"→ recommended={recommended:.2f}%"
    )

    if verbose:
        print(f"[sl_bandit] {regime}:{direction}")
        for a, s_score in sorted(arm_scores.items(), key=lambda x: -x[1]):
            k = _arm_key(regime, direction, a)
            d = state.get(k, {'wins': 0, 'n': 0})
            wr = d['wins']/d['n']*100 if d['n'] else 0
            print(f"  {a:<10} UCB={s_score:.2f} WR={wr:.0f}% n={d['n']}")
        print(f"  → 推荐: {recommended:.2f}% ({reasoning})")

    return {
        'recommended_sl_pct': recommended,
        'arm': best_arm,
        'confidence': confidence,
        'reasoning': reasoning,
    }


def update_from_outcome(
    regime: str,
    direction: str,
    sl_pct: float,
    outcome: str,
    pnl_pct: float = 0.0,
) -> None:
    """
    根据信号结算结果更新Bandit状态

    Args:
        regime:    体制
        direction: 方向
        sl_pct:    实际使用的sl_pct
        outcome:   结算结果 (TP1/TP2/SL_HIT/EXPIRED_NO_TOUCH/...)
        pnl_pct:   实际PnL%
    """
    arm_name = _sl_to_arm(sl_pct)
    key = _arm_key(regime, direction, arm_name)

    state = _load_state()
    if key not in state:
        state[key] = {'wins': 0, 'n': 0}

    state[key]['n'] += 1

    # 判断胜负
    win = (
        outcome in ('TP1', 'TP2', 'WIN', 'TAKE_PROFIT') or
        (outcome == 'EXPIRED_NO_TOUCH' and pnl_pct > 0)
    )
    if win:
        state[key]['wins'] += 1

    state[key]['last_updated'] = time.time()
    _save_state(state)


def sync_from_signal_log() -> int:
    """
    从live_signal_log.jsonl批量同步结算数据到Bandit状态
    返回新同步的条数
    """
    state = _load_state()
    last_sync_ts = state.get('_last_sync_ts', 0)
    new_count = 0

    if not SIGNAL_LOG_PATH.exists():
        return 0

    lines = SIGNAL_LOG_PATH.read_text().strip().split('\n')
    for l in lines:
        try:
            s = json.loads(l)
            ts = s.get('ts', 0)
            if ts <= last_sync_ts:
                continue
            outcome = s.get('outcome', '')
            if not outcome:
                continue
            regime    = s.get('regime', 'BULL_TREND')
            direction = s.get('direction', 'LONG')
            sl_pct    = float(s.get('sl_pct', 0) or 0)
            pnl_pct   = float(s.get('pnl_pct') or s.get('pnl', 0) or 0)
            if sl_pct <= 0:
                continue
            update_from_outcome(regime, direction, sl_pct, outcome, pnl_pct)
            new_count += 1
        except Exception:
            pass

    # 更新同步时间戳
    if new_count > 0:
        state2 = _load_state()
        state2['_last_sync_ts'] = time.time()
        _save_state(state2)

    return new_count


def get_stats() -> dict:
    """返回当前Bandit统计摘要"""
    state = _load_state()
    summary = {}
    for key, v in state.items():
        if key.startswith('_'):
            continue
        n = v.get('n', 0)
        wins = v.get('wins', 0)
        wr = wins/n*100 if n > 0 else 0
        summary[key] = {'n': n, 'wins': wins, 'wr': round(wr, 1)}
    return summary


def sync_from_simfactory() -> int:
    """
    从 dharma_simfactory.py 生成的 simfactory_trades.jsonl
    批量喂给 Bandit，加速WR收敛（设计院 2026-08-07）
    """
    sim_path = BASE / 'data' / 'simfactory_trades.jsonl'
    if not sim_path.exists():
        return 0

    state = _load_state()
    seen_ids = set(state.get('_sim_seen_ids', []))
    new_count = 0

    for line in sim_path.read_text().splitlines():
        try:
            t = json.loads(line)
            sid = t.get('signal_id', '')
            if sid in seen_ids:
                continue
            result = t.get('result', '')
            if result not in ('TP1', 'SL'):
                continue
            outcome = 'WIN' if result == 'TP1' else 'LOSS'
            sl_pct  = float(t.get('sl_pct', 0) or 0)
            pnl_pct = float(t.get('pnl_pct', 0) or 0)
            if sl_pct <= 0:
                continue
            update_from_outcome(
                t.get('regime', 'BULL_TREND'),
                t.get('direction', 'LONG'),
                sl_pct, outcome, pnl_pct
            )
            seen_ids.add(sid)
            new_count += 1
        except Exception:
            pass

    if new_count > 0:
        state2 = _load_state()
        state2['_sim_seen_ids'] = list(seen_ids)
        _save_state(state2)

    return new_count


# ── 独立运行: 打印统计 + 同步 ────────────────────────────
if __name__ == '__main__':
    import sys

    if '--sync' in sys.argv:
        n = sync_from_signal_log()
        print(f"[sl_bandit] 从信号日志同步: {n}条新记录")
        n2 = sync_from_simfactory()
        print(f"[sl_bandit] 从 simfactory同步: {n2}条新记录")

    print("\n[sl_bandit] 当前Bandit状态:")
    stats = get_stats()
    if not stats:
        print("  (空，首次运行将使用先验数据)")
    else:
        for k, v in sorted(stats.items(), key=lambda x: -x[1]['n']):
            print(f"  {k:<40} n={v['n']:3} wins={v['wins']:3} WR={v['wr']:5.1f}%")

    print("\n[sl_bandit] 推荐示例:")
    for regime, direction in [('BULL_TREND','LONG'),('BEAR_RECOVERY','LONG'),('CHOP_MID','SHORT')]:
        r = recommend_sl_pct(regime, direction, base_sl_pct=2.0, score=140, verbose=True)
        print(f"  {regime}:{direction} → {r['recommended_sl_pct']:.2f}% (arm={r['arm']}, conf={r['confidence']:.2f})\n")

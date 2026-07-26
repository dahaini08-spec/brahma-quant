"""
SignalIntegrityGate — P0~P2 信号完整性门控
梵天设计院封印 2026-07-26 · 苏摩授权自主执行

P0: consensus + timing 互锁门控
P1: SL距离硬封禁（>5% → REJECT）
P2: 体制动态衰减（从峰值-3%开始）
"""
import time
from pathlib import Path
import json

# ── P0: 门控常量 ───────────────────────────────────────
# consensus包含BEAR且方向LONG → 拒绝
CONSENSUS_LONG_BLOCK  = {'BEAR', 'FULL_BEAR', 'STRONG_BEAR'}
CONSENSUS_SHORT_BLOCK = {'BULL', 'FULL_BULL', 'STRONG_BULL'}

# timing_status不允许ENTER的状态
TIMING_BLOCK_ACTIONS = {'ENTER', 'ENTER_FULL'}
TIMING_BLOCK_STATUS  = {'WAIT', 'STANDBY'}

# ── P1: SL距离上限 ─────────────────────────────────────
SL_PCT_HARD_CAP = 5.0  # 超过5%直接拒绝

# ── P2: 体制动态衰减 ────────────────────────────────────
REGIME_DECAY_THRESHOLD = -3.0   # 从峰值下跌超过3%开始衰减
REGIME_DECAY_FACTOR    = 3.0    # 衰减系数
REGIME_DECAY_FLOOR     = 0.40   # 最低保留40%权重
CONSECUTIVE_RED_PENALTY = 0.70  # 连续3根4H阴线额外×0.70

# 峰值追踪文件
_PEAK_STATE_FILE = Path(__file__).parent.parent / 'data' / '_regime_peak_state.json'


class SignalIntegrityGate:
    """
    P0~P2 信号完整性门控
    在brahma_engine最终 _valid 计算前调用
    返回 (pass: bool, reason: str)
    """

    @staticmethod
    def validate(
        direction: str,
        action: str,
        consensus: str,
        timing_status: str,
        sl_pct: float,
        regime: str,
        score: float,
    ) -> tuple:
        """
        Returns (True, '') if signal passes all gates
        Returns (False, reason) if rejected
        """
        consensus_upper = (consensus or '').upper().replace(' ', '_')
        timing_upper    = (timing_status or '').upper()
        direction_upper = (direction or '').upper()
        action_upper    = (action or '').upper()

        # ── P0-A: consensus与方向冲突 ──────────────────────
        if direction_upper == 'LONG' and consensus_upper in CONSENSUS_LONG_BLOCK:
            return False, f'[P0-A] consensus={consensus} 与 LONG 方向冲突 → REJECT'

        if direction_upper == 'SHORT' and consensus_upper in CONSENSUS_SHORT_BLOCK:
            return False, f'[P0-A] consensus={consensus} 与 SHORT 方向冲突 → REJECT'

        # ── P0-B: timing=WAIT/STANDBY 时禁止ENTER ──────────
        if timing_upper in TIMING_BLOCK_STATUS and action_upper in TIMING_BLOCK_ACTIONS:
            return False, f'[P0-B] timing_status={timing_status} 时禁止 {action} → REJECT'

        # ── P1: SL距离硬封禁 ────────────────────────────────
        if sl_pct > SL_PCT_HARD_CAP:
            return False, (
                f'[P1] SL距离={sl_pct:.2f}% 超过硬上限{SL_PCT_HARD_CAP}% → REJECT'
                f'（SL距离铁律：BEAR/BULL体制≤2.5%，绝对上限≤5%）'
            )

        return True, ''


def get_dynamic_regime_mult(
    regime: str,
    base_mult: float,
    symbol: str,
    current_price: float,
) -> tuple:
    """
    P2: 体制动态衰减
    Returns (adjusted_mult: float, note: str)
    """
    try:
        # 读取峰值状态
        state = {}
        if _PEAK_STATE_FILE.exists():
            state = json.loads(_PEAK_STATE_FILE.read_text())

        sym_state = state.get(symbol, {})
        peak_price = sym_state.get('peak_price', current_price)
        peak_ts    = sym_state.get('peak_ts', time.time())
        peak_regime = sym_state.get('peak_regime', regime)

        # 更新峰值（只在BULL体制下追踪峰值）
        if regime in ('BULL_TREND', 'BULL_EARLY') and current_price > peak_price:
            peak_price = current_price
            peak_ts    = time.time()
            peak_regime = regime
            state[symbol] = {
                'peak_price': peak_price,
                'peak_ts':    peak_ts,
                'peak_regime': peak_regime,
            }
            _PEAK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PEAK_STATE_FILE.write_text(json.dumps(state, indent=2))

        # 计算从峰值的跌幅
        if peak_price > 0:
            drop_pct = (current_price - peak_price) / peak_price * 100
        else:
            drop_pct = 0.0

        # 仅在BULL体制下应用衰减（BEAR/CHOP体制有自己的乘数）
        if regime not in ('BULL_TREND', 'BULL_EARLY'):
            return base_mult, ''

        if drop_pct >= REGIME_DECAY_THRESHOLD:
            # 价格未偏离峰值或在上涨，不衰减
            return base_mult, ''

        # 计算衰减
        decay_depth = abs(drop_pct - REGIME_DECAY_THRESHOLD) / 100
        decay_factor = 1.0 - decay_depth * REGIME_DECAY_FACTOR
        adjusted_mult = max(REGIME_DECAY_FLOOR, base_mult * decay_factor)

        note = (
            f'[P2] 体制动态衰减: {regime} 从峰值${peak_price:.2f}跌{drop_pct:.1f}% '
            f'→ mult {base_mult:.2f}→{adjusted_mult:.2f}'
        )
        return adjusted_mult, note

    except Exception as e:
        return base_mult, f'[P2] 衰减计算异常: {e}'


# ── 便捷函数供brahma_engine直接调用 ──────────────────────

def gate_check(cf: dict, params: dict, ms: dict) -> tuple:
    """
    主入口：从brahma_engine的cf/params/ms中提取字段，执行P0+P1检查
    Returns (pass: bool, reason: str)
    """
    direction     = ms.get('signal_dir', cf.get('signal_dir', ''))
    action        = cf.get('action', '')
    consensus     = cf.get('consensus', ms.get('consensus', ''))
    timing_status = cf.get('timing_status', ms.get('timing_status', ''))
    sl_pct        = float(params.get('sl_pct', 0) or 0)
    regime        = str(ms.get('regime', '') or '')
    score         = float(cf.get('score_final', 0) or 0)

    return SignalIntegrityGate.validate(
        direction, action, consensus, timing_status,
        sl_pct, regime, score
    )

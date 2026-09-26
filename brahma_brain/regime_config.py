"""
regime_config.py — 梵天体制×方向 SSOT（方向建议 + WR反哺override统一入口）
设计院 2026-08-24 从brahma_core.py提取封印
[Phase C 2026-09-13] 乘数定位变更: 不再乘score → 只作为仓位上限
[路线A 2026-09-26 苏摩111] 体制链审计终版裁决:
  - 删除三套死矩阵(DEFAULT/BTC/ETH) + ALTCOIN矩阵（9.20 P0改革后恒1.0死代码）
  - 宪法语义改为「方向建议」：只保留 <1.0 的降权铁证（BEAR做多/BULL做空等死穴侧）
  - WR反哺override统一由 get_regime_mult() 收口（新鲜优先→建议表降权兜底）
  - 新鲜度门: _updated_date != 今日UTC → override作废，走兜底（防wr引擎死亡后陈旧乘数永久生效）

职责: brahma_core / trade_gateway 的体制方向唯一乘数来源（SSOT）
调用方式:
    from regime_config import get_regime_mult_info
    _mult, _src = get_regime_mult_info(symbol, regime, signal_dir)

语义（路线A封印）:
    1. override新鲜（今日UTC写入）→ 用WR实盘值（9.25三方修复后的现行宪法）
    2. override缺失/过期 → 建议表降权值或1.0中性（只降权不放大；WR引擎死亡时绝不裸奔）
    3. 未知体制 → 0.85 保守降权
    放大（>1.0）只可能来自WR反哺实盘数据，永不来自静态建议表
"""

import json as _json
from pathlib import Path as _Path
from datetime import datetime as _dt, timezone as _tz
import sys

_OVERRIDE_FILE = _Path(__file__).parent.parent / 'data' / 'regime_mult_override.json'

# ── 方向建议表（宪法语义：死穴侧降权，只降不升）─────────────────────────────
# [词汇表收敛P2 2026-09-26 苏摩111] 18体制收敛→6体制：
# 保留 = watcher5体制(HMM4∪BREAKOUT) ∪ BULL_EARLY（SCORE_GATE消费方）；
# 删12行 = 无生产者体制（watcher5∪HMM4=6体制之外）。未知体制走 _FALLBACK_MULT=0.85。
# ⚠️ DIRECTION_GATE/SCORE_GATE 门控表不动（独立表，键覆盖不受本收敛影响）。
REGIME_DIRECTION_ADVICE = {
    'BEAR_TREND':     {'LONG': 0.35},            # LONG死穴 WR=44.6% n=225623
    'BEAR_EARLY':     {'LONG': 0.35},            # LONG降权 WR=50.4%
    'BEAR_RECOVERY':  {'SHORT': 0.35},           # SHORT高证据标准（DIRECTION_GATE needs_event）
    'BULL_TREND':     {'SHORT': 0.50},           # SHORT死穴 WR=48.2%
    'BULL_EARLY':     {'SHORT': 0.35},           # SHORT降权
    'CHOP_MID':       {'LONG': 0.50},            # SHORT 0.88解锁→中性1.0（WR=57.3%铁证）
}
# [词汇表收敛P2 2026-09-26 苏摩111] 12个无生产者体制行删除（watcher5∪HMM4=6体制）；
# 删除行: BULL_CORRECTION/BULL_PEAK/BULL_BREAK/BEAR_CRASH/CHOP/CHOP_HIGH/CHOP_LOW/
#         CHOP_RANGE_DISCOUNT/CHOP_RANGE_PREMIUM/MOMENTUM_BULL/MOMENTUM_STRONG/BREAKOUT
# 未知体制走 _FALLBACK_MULT=0.85

_FALLBACK_MULT = 0.85  # 未知体制，保守降权

# ── 方向准入表（体制重设计P1 2026-09-26 苏摩111）─────────────────────────────
# 语义: 「封禁」→「证据标准」。逆势方向不是禁手，是需要更硬的证据。
#   'open'            = 三票2/3即可（trader_brain现有投票语义）
#   'needs_consensus' = 需三票3/3全票（或事件/突破覆盖）才不降仓
#   'needs_event'     = 需事件驱动来源（_event_driven）才不降仓
# 表未列 = 'open'（含全部'CHOP'前缀体制：CHOP_HIGH/CHOP_LOW/CHOP_RANGE_*等）
DIRECTION_GATE = {
    'BULL_TREND':    {'SHORT': 'needs_consensus'},
    'BULL_EARLY':    {'SHORT': 'needs_consensus'},
    'BEAR_TREND':    {'LONG': 'needs_consensus'},
    'BEAR_EARLY':    {'LONG': 'needs_consensus'},
    'BEAR_RECOVERY': {'SHORT': 'needs_event'},   # 宪法语义落表执法（WR=0%铁证→事件标准）
    # CHOP_MID: 双向open（三票2/3），WR=57.3%铁证中性
}

# ── 许可层：体制差异化开仓门（体制重设计P1 2026-09-26 苏摩111）─────────────
# min_score = 该体制×方向的最低开仓score（signal层SELECTED分数）
# min_samples = 小样本执法阈值：实测n < min_samples → 半开门（mult×0.5+WATCH），不是硬拒
# 表未列 = {'min_score': 100}（现行MIN_SCORE_OPEN=100默认）
SCORE_GATE = {
    'BULL_TREND:LONG':  {'min_score': 100, 'min_samples': 0,  'note': '回测WR56.7% n=3655大样本'},
    'BULL_TREND:SHORT': {'min_score': 120, 'min_samples': 0,  'note': '逆势方向从严'},
    'BEAR_TREND:SHORT': {'min_score': 100, 'min_samples': 0,  'note': '顺势'},
    'BEAR_TREND:LONG':  {'min_score': 140, 'min_samples': 14, 'note': '死亡区间执法点，n<30→半开门，可被新样本推翻'},
    'CHOP_MID:LONG':    {'min_score': 110, 'min_samples': 0,  'note': '宪法CHOP禁单语义→110'},
    'CHOP_MID:SHORT':   {'min_score': 100, 'min_samples': 0,  'note': 'WR57.3%铁证中性'},
    # ── P1-2接线 2026-09-26 苏摩111：内联boost迁移（单源化，原brahma_core._DYNAMIC_THRESHOLD_BOOST）
    'BEAR_EARLY:LONG':  {'min_score': 118, 'min_samples': 0,  'note': '内联boost+18迁移(WR=50.4% n>6000)'},
    'BULL_EARLY:SHORT': {'min_score': 118, 'min_samples': 0,  'note': '内联boost+18迁移(WR=51.9% n>6000)'},
    'CHOP:LONG':        {'min_score': 108, 'min_samples': 0,  'note': '内联boost+8迁移(WR=56%)'},
    'CHOP_LOW:LONG':    {'min_score': 105, 'min_samples': 0,  'note': '内联boost+5迁移'},
    # ── TIER级锁（P1-3 STATIC_LOCK合并 2026-09-26 苏摩111）───────
    # locked=True = 动态WR反哺不许改写（原STATIC_LOCK语义），score门语义保留
    'CHOP_MID:LONG:155+':      {'min_score': 155, 'min_samples': 0, 'locked': True, 'note': '原STATIC_LOCK: 死穴永久封禁'},
    'BEAR_TREND:LONG:155+':    {'min_score': 155, 'min_samples': 0, 'locked': True, 'note': '原STATIC_LOCK: 逆势死亡区'},
    'BEAR_TREND:LONG:140-154': {'min_score': 140, 'min_samples': 0, 'locked': True, 'note': '原STATIC_LOCK: 逆势极危'},
    'BULL_TREND:LONG:140-154': {'min_score': 140, 'min_samples': 14, 'locked': True, 'note': 'WR=1.3% n=74(120~154实盘) SL≥3%触发'},
}
_SCORE_GATE_DEFAULT = {'min_score': 100, 'min_samples': 0, 'note': '默认门'}


def get_direction_gate(regime: str, direction: str) -> str:
    """方向准入（三层语义·方向层）：'open' | 'needs_consensus' | 'needs_event'
    regime/dir统一upper；'CHOP'前缀体制未列→'open'。"""
    r = (regime or '').upper()
    d = (direction or 'LONG').upper()
    return str(DIRECTION_GATE.get(r, {}).get(d, 'open'))


def get_score_gate(regime: str, direction: str, tier: str | None = None) -> dict:
    """许可层：返回{'min_score': int, 'min_samples': int, 'note': str, 'locked': bool}。
    表未列 = min_score 100默认（现行统一开仓门）。
    tier传入时先查 'REGIME:DIR:TIER' 锁键，未命中回落 'REGIME:DIR'。"""
    r = (regime or '').upper()
    d = (direction or 'LONG').upper()
    if tier:
        gate = SCORE_GATE.get(f'{r}:{d}:{str(tier).upper()}')
        if gate is not None:
            return dict(gate)
    return dict(SCORE_GATE.get(f'{r}:{d}', _SCORE_GATE_DEFAULT))


def get_gate_state(regime: str, direction: str) -> dict:
    """聚合层（P2哨兵挂点）：{direction_gate, score_gate, mult_info}。"""
    r = (regime or '').upper()
    d = (direction or 'LONG').upper()
    _mult, _src = get_regime_mult_info('', r, d)
    return {
        'direction_gate': get_direction_gate(r, d),
        'score_gate': get_score_gate(r, d),
        'mult_info': {'mult': _mult, 'source': _src},
    }


# ── WR反哺Override（P2 2026-09-03 苏摩111 / 9.25嵌套格式统一）───────────────
# 每日02:00由 scripts/wr_feedback_engine.py 原子写入 data/regime_mult_override.json
# 格式: {"BULL_TREND": {"LONG": 0.95}, "_updated_date": "2026-09-26", ...}
# 新鲜度门: _updated_date != 今日UTC → 视为过期，走建议表兜底
def _load_override_fresh() -> dict:
    """读取override，含新鲜度校验。返回: {REGIME: {DIR: mult}} 或 {}（过期/缺失/损坏）"""
    try:
        if not _OVERRIDE_FILE.exists():
            return {}
        data = _json.loads(_OVERRIDE_FILE.read_text())
        updated = data.get('_updated_date', '')
        today = _dt.now(_tz.utc).strftime('%Y-%m-%d')
        if updated != today:
            return {}
        # 只保留嵌套dict结构（平铺legacy键已在9.25被wr引擎清理，此处防御）
        return {k: v for k, v in data.items()
                if not k.startswith('_') and isinstance(v, dict)}
    except Exception as _e:
        print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        return {}


def get_regime_mult_info(symbol: str, regime: str, signal_dir: str) -> tuple:
    """
    返回 (mult, source)。source: override_fresh | advice_fallback | neutral | unknown_regime
    symbol保留在签名中兼容下游（WR反哺为全局矩阵，方仓标的专属化是后续P2）。
    """
    regime_upper = (regime or '').upper()
    direction = (signal_dir or 'LONG').upper()
    override = _load_override_fresh()
    ov_regime = override.get(regime_upper)
    if isinstance(ov_regime, dict):
        ov = ov_regime.get(direction)
        if isinstance(ov, (int, float)):
            return float(ov), 'override_fresh'
    dirs = REGIME_DIRECTION_ADVICE.get(regime_upper)
    if dirs is None:
        return _FALLBACK_MULT, 'unknown_regime'
    v = dirs.get(direction)
    if v is not None:
        return float(v), 'advice_fallback'
    return 1.0, 'neutral'


def get_regime_mult(symbol: str, regime: str, signal_dir: str) -> float:
    """统一入口（SSOT）：override新鲜 → WR实盘值；过期/缺失 → 建议表降权兜底"""
    return get_regime_mult_info(symbol, regime, signal_dir)[0]


# ── [2026-08-30 苏摩111] ETH订单流维度权重放大系数 ──────────────────────────
# 铁证：arXiv ETH订单流论文 — ETH盘口状态依赖性比BTC强，CVD信号更可靠
# BTC：订单流常见噪声+清算驱动，CVD可靠性偏低 → 乘数1.0（不变）
# ETH：多种盘口状态下订单流更有结构性 → 乘数1.3（增强30%）
ORDER_FLOW_MULT = {
    'BTCUSDT': 1.0,   # BTC: 清算驱动为主，订单流信号噪声大
    'ETHUSDT': 1.3,   # ETH: 盘口状态依赖更强，订单流更可靠（arXiv铁证）
    'DEFAULT': 1.0,   # 其他标的默认1.0
}

def get_order_flow_mult(symbol: str) -> float:
    """获取标的的订单流维度权重乘数"""
    sym_upper = str(symbol).upper()
    if 'ETH' in sym_upper:
        return ORDER_FLOW_MULT['ETHUSDT']
    elif 'BTC' in sym_upper:
        return ORDER_FLOW_MULT['BTCUSDT']
    return ORDER_FLOW_MULT['DEFAULT']

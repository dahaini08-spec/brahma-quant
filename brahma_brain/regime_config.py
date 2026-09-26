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
# 覆盖 market_state 全部14体制。放大侧（旧矩阵×1.1~1.6）已按9.20 P0宪法中性化=1.0。
REGIME_DIRECTION_ADVICE = {
    'BEAR_TREND':     {'LONG': 0.35},            # LONG死穴 WR=44.6% n=225623
    'BEAR_EARLY':     {'LONG': 0.35},            # LONG降权 WR=50.4%
    'BEAR_RECOVERY':  {'SHORT': 0.35},           # SHORT严禁（宪法+WR=0%封禁）
    'BULL_TREND':     {'SHORT': 0.50},           # SHORT死穴 WR=48.2%
    'BULL_EARLY':     {'SHORT': 0.35},           # SHORT降权
    'BULL_CORRECTION':{'LONG': 0.65},            # LONG样本不足降权
    'BULL_PEAK':      {'LONG': 0.75},
    'BULL_BREAK':     {'LONG': 0.75},
    'BEAR_CRASH':     {'SHORT': 0.90, 'LONG': 0.65},   # 极端体制两向降权
    'CHOP':           {'LONG': 0.50},
    'CHOP_HIGH':      {'SHORT': 0.80, 'LONG': 0.50},
    'CHOP_MID':       {'LONG': 0.50},            # SHORT 0.88解锁→中性1.0（WR=57.3%铁证）
    'CHOP_LOW':       {'SHORT': 0.88, 'LONG': 0.50},
    'CHOP_RANGE_DISCOUNT': {'SHORT': 0.50},
    'CHOP_RANGE_PREMIUM':  {'LONG': 0.35},
    'MOMENTUM_BULL':  {'SHORT': 0.50},           # 动量上行做空降权（无做多铁证，不放大）
    'MOMENTUM_STRONG':{'SHORT': 0.50},
    'BREAKOUT':       {},                        # 突破体制双向中性1.0
}
# 注意: 表中未列出的体制×方向 = 1.0中性（建议表只写降权侧，防宪法语义漂移）

_FALLBACK_MULT = 0.85  # 未知体制，保守降权


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

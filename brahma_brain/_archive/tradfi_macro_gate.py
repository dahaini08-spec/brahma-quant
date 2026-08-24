"""
tradfi_macro_gate.py — 美股代币宏观联动门控
[设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式

核心逻辑：macro_link 2.0x 权重的实际门控实现
- SPX日跌 > 1.5% → 所有美股代币信号 score-15
- QQQ跌破20均线 → LONG信号 score-20（严格门控）
- SPX连续3日上涨 → LONG信号 score+8
- DXY强劲上涨 > 0.5% → 科技股LONG信号 score-10（美元强→科技股压力）

数据来源：data/macro_state.json (由 macro_engine.py 定时写入)

调用方：brahma_core.py → compute_tradfi_macro_gate(symbol, direction, asset_type)
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_MACRO_STATE_PATH = Path(__file__).parent.parent / 'data' / 'macro_state.json'

# ─── 评分阈值 ────────────────────────────────────────────────────────────────────

_SPX_DROP_THRESHOLD    = -1.5   # SPX日跌幅 < -1.5% → 全系扣分
_SPX_DROP_PENALTY      = -15    # 扣分值
_QQQ_BELOW_MA20_PENALTY = -20   # QQQ跌破20均线时LONG扣分
_SPX_BULL_BONUS        = +8     # SPX连续上涨时LONG加分（保留，当前macro_state暂无连涨数据）
_DXY_STRONG_THRESHOLD  = 0.5    # DXY涨幅 > 0.5% → 科技股压力
_DXY_STRONG_PENALTY    = -10    # 科技股LONG扣分

# 科技股集合（DXY强对这些影响大）
_TECH_SYMBOLS = frozenset([
    'NVDAUSDT', 'AMDUSDT', 'TSMUSDT', 'MUUSDT', 'SNDKUSDT', 'SKHYNIXUSDT', 'SKHYUSDT',
    'TSLAUSDT', 'METAUSDT', 'MSFLUSDT', 'MSFUSDT', 'MSTRUSDT', 'GOOGLUSDT', 'GOOGUSDT',
    'AAPLUSDT', 'SOXLUSDT', 'QQQUSDT', 'SPCXUSDT',
])


def _load_macro_state() -> dict:
    """安全读取 macro_state.json，失败返回空字典"""
    try:
        with open(_MACRO_STATE_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        _log.debug(f'macro_gate: macro_state.json读取失败 {e}')
        return {}


def compute_tradfi_macro_gate(
    symbol: str,
    direction: str,  # 'LONG' or 'SHORT'
    asset_type: str = 'TRADFI_STOCK',
) -> dict:
    """
    计算宏观联动门控评分

    Args:
        symbol:     当前分析标的
        direction:  'LONG' 或 'SHORT'
        asset_type: 资产类型（仅TRADFI_STOCK执行门控）

    Returns:
        {
            'score': float,         # 宏观门控调整分（通常为负数）
            'rules_triggered': list,# 触发的规则名称列表
            'detail': str,          # 人类可读说明
            'spx_chg_1d': float,    # SPX当日涨跌
            'qqq_above_ma20': bool, # QQQ是否在MA20之上
        }
    """
    result = {
        'score': 0.0,
        'rules_triggered': [],
        'detail': '宏观正常',
        'spx_chg_1d': 0.0,
        'qqq_above_ma20': True,
    }

    if asset_type != 'TRADFI_STOCK':
        result['detail'] = '非TRADFI_STOCK，跳过宏观门控'
        return result

    macro = _load_macro_state()
    if not macro:
        result['detail'] = 'macro_state.json不可用，跳过宏观门控'
        return result

    total_adj = 0.0
    rules = []

    # ─── 规则1：SPX日跌 > 1.5% → 全系扣分 ─────────────────────────────────────
    spx = macro.get('spx', {})
    spx_chg = float(spx.get('chg_1d_pct', 0) or 0)
    result['spx_chg_1d'] = spx_chg

    if spx_chg < _SPX_DROP_THRESHOLD:
        total_adj += _SPX_DROP_PENALTY
        rules.append(f'SPX单日跌{spx_chg:.2f}%(<{_SPX_DROP_THRESHOLD}%) score{_SPX_DROP_PENALTY}')

    # ─── 规则2：QQQ跌破MA20 → LONG信号严格门控 ─────────────────────────────────
    qqq = macro.get('qqq', {})
    qqq_above_ma20 = bool(qqq.get('above_ma20', True))
    result['qqq_above_ma20'] = qqq_above_ma20

    if not qqq_above_ma20 and direction == 'LONG':
        total_adj += _QQQ_BELOW_MA20_PENALTY
        vs_ma20 = float(qqq.get('vs_ma20_pct', 0) or 0)
        rules.append(f'QQQ跌破MA20({vs_ma20:.1f}%) LONG受限 score{_QQQ_BELOW_MA20_PENALTY}')

    # ─── 规则3：DXY强势上涨 → 科技股LONG扣分 ───────────────────────────────────
    dxy = macro.get('dxy', {})
    dxy_chg = float(dxy.get('change', 0) or 0)

    if dxy_chg > _DXY_STRONG_THRESHOLD and direction == 'LONG' and symbol in _TECH_SYMBOLS:
        total_adj += _DXY_STRONG_PENALTY
        rules.append(f'DXY强势+{dxy_chg:.2f}%(>{_DXY_STRONG_THRESHOLD}%) 科技LONG受压 score{_DXY_STRONG_PENALTY}')

    # ─── 汇总 ────────────────────────────────────────────────────────────────────
    result['score'] = float(total_adj)
    result['rules_triggered'] = rules

    if rules:
        result['detail'] = ' | '.join(rules)
    else:
        result['detail'] = f'宏观正常(SPX={spx_chg:+.2f}% QQQ>MA20={qqq_above_ma20} DXY={dxy_chg:+.2f}%)'

    return result

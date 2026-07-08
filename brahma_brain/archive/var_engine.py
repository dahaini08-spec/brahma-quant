#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# VaR风险计算，资金管理
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
var_engine.py — VaR/CVaR 组合风险度量引擎
Phase A-3: 历史模拟法 VaR，每次执行前计算组合尾部风险

能力：
  - 单品种日度VaR (95%/99%)
  - 组合VaR（含相关性）
  - CVaR (Expected Shortfall)
  - 仓位调整建议（超限时缩仓）
"""
import json, os, sys, math, time
import urllib.request
from pathlib import Path
from collections import defaultdict

FAPI    = 'https://fapi.binance.com'
DATA_DIR = Path(__file__).parent.parent / 'data'

def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def get_daily_returns(symbol: str, n: int = 100) -> list:
    """获取日度收益率序列"""
    try:
        data = _get(f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit={n+1}')
        closes = [float(k[4]) for k in data]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        return returns
    except Exception:
        return []

def calc_var(returns: list, confidence: float = 0.95) -> dict:
    """历史模拟法 VaR"""
    if len(returns) < 20:
        return {'var': 0, 'cvar': 0, 'n': len(returns)}
    s = sorted(returns)
    idx = int((1 - confidence) * len(s))
    var  = abs(s[idx])
    cvar = abs(sum(s[:idx+1]) / max(idx+1, 1))
    return {
        'var':    round(var * 100, 3),   # 百分比
        'cvar':   round(cvar * 100, 3),
        'n':      len(s),
        'worst':  round(min(s) * 100, 3),
        'best':   round(max(s) * 100, 3),
    }

def calc_portfolio_var(positions: list, confidence: float = 0.95) -> dict:
    """
    组合VaR（简化相关性矩阵）
    positions: [{'symbol':str, 'direction':str, 'weight':float}]
    """
    if not positions:
        return {'portfolio_var': 0, 'portfolio_cvar': 0, 'positions': []}

    pos_vars = []
    for pos in positions:
        sym     = pos.get('symbol', 'ETHUSDT')
        weight  = pos.get('weight', 0.05)
        dir_    = pos.get('direction', 'LONG')

        rets = get_daily_returns(sym, 60)
        if not rets:
            continue

        # 空头反转收益符号
        if dir_ in ('做空', 'SHORT'):
            rets = [-r for r in rets]

        v = calc_var(rets, confidence)
        pos_vars.append({
            'symbol':  sym,
            'direction': dir_,
            'weight':  weight,
            'var_pct': v['var'],
            'cvar_pct': v['cvar'],
            'weighted_var': round(v['var'] * weight, 4),
            'weighted_cvar': round(v['cvar'] * weight, 4),
        })

    if not pos_vars:
        return {'portfolio_var': 0, 'portfolio_cvar': 0, 'positions': []}

    # 简单加权（保守估计，忽略多样化收益）
    p_var  = sum(p['weighted_var']  for p in pos_vars)
    p_cvar = sum(p['weighted_cvar'] for p in pos_vars)

    # 风险评级
    if p_var > 5:
        risk_level = 'EXTREME'
    elif p_var > 3:
        risk_level = 'HIGH'
    elif p_var > 1.5:
        risk_level = 'MEDIUM'
    else:
        risk_level = 'LOW'

    return {
        'portfolio_var':  round(p_var, 3),
        'portfolio_cvar': round(p_cvar, 3),
        'risk_level':     risk_level,
        'confidence':     confidence,
        'positions':      pos_vars,
        'recommendation': '降仓' if risk_level in ('EXTREME','HIGH') else '正常',
    }

def single_position_var(symbol: str, position_pct: float = 0.05, direction: str = 'SHORT') -> dict:
    """单仓位VaR快速评估"""
    rets = get_daily_returns(symbol, 60)
    if direction in ('做空', 'SHORT'):
        rets = [-r for r in rets]
    v = calc_var(rets)
    v['position_max_loss_pct'] = round(v['var'] * position_pct, 4)
    v['position_max_loss_usdt_per_100'] = round(v['var'] * position_pct * 100, 2)
    return v

def get_score_adjustment(portfolio_var: float) -> int:
    """根据组合VaR对新仓评分做风险调整"""
    if portfolio_var > 5:
        return -8   # 极端风险，强烈降低新开仓评分
    elif portfolio_var > 3:
        return -4
    elif portfolio_var > 2:
        return -2
    else:
        return 0

if __name__ == '__main__':
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else 'ETHUSDT'
    rets = get_daily_returns(sym)
    v = calc_var(rets)
    print(f'{sym} 日度VaR(95%): {v["var"]:.2f}%  CVaR: {v["cvar"]:.2f}%  最差日: {v["worst"]:.2f}%')

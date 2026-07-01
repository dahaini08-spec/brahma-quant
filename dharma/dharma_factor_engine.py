"""
达摩院因子引擎 v2.0
DharmaFactorEngine — 标准化落地层

读取 dharma/factor_weights.yaml，对状态为 'live' 的因子
根据传入的上下文字段计算调整分值，并应用到 brahma_core 的 score。

调用方式（brahma_core.py 已有 try/except 保护）：
    from dharma.dharma_factor_engine import apply_dharma_factors
    score, breakdown = apply_dharma_factors(score, ctx, breakdown)

ctx 字段（brahma_core.py 已传入）：
    rsi_1h, vol_ratio, bb_mid/upper/lower, price,
    rsi_bb_dual_long/short, triple_resonance_long/short,
    fvg_long/short, vol_rsi_optimal, has_fvg_long/short

返回：(new_score, updated_breakdown)
"""

import os
import yaml

_YAML_PATH = os.path.join(os.path.dirname(__file__), "factor_weights.yaml")
_CACHE: dict = {}
_CACHE_MTIME: float = 0.0


def _load_factors() -> dict:
    """加载 factor_weights.yaml，带 mtime 缓存"""
    global _CACHE, _CACHE_MTIME
    try:
        mtime = os.path.getmtime(_YAML_PATH)
        if mtime == _CACHE_MTIME and _CACHE:
            return _CACHE
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _CACHE = data or {}
        _CACHE_MTIME = mtime
        return _CACHE
    except Exception:
        return {}


def _apply_volume_factors(factors: dict, ctx: dict, adj: float, bd: dict):
    vol_ratio = float(ctx.get("vol_ratio", 1.0) or 1.0)
    for item in factors.get("volume", []):
        if item.get("status") != "live":
            continue
        fid = item["id"]
        score = float(item.get("score", 0))
        cond = item.get("condition", "")
        hit = False
        if fid == "VOL_EXTREME_HIGH" and vol_ratio > 2.0:
            hit = True
        elif fid == "VOL_HIGH" and 1.5 <= vol_ratio < 2.0:
            hit = True
        elif fid == "VOL_MEDIUM" and 1.2 <= vol_ratio < 1.5:
            hit = True
        elif fid == "VOL_LOW" and vol_ratio < 0.8:
            hit = True
        if hit:
            adj += score
            bd[fid] = score
    return adj, bd


def _apply_rsi_factors(factors: dict, ctx: dict, adj: float, bd: dict):
    rsi = float(ctx.get("rsi_1h", 50) or 50)
    direction = ctx.get("direction", "LONG")  # LONG or SHORT
    for item in factors.get("rsi", []):
        if item.get("status") != "live":
            continue
        fid = item["id"]
        score = float(item.get("score", 0))
        hit = False
        if fid == "RSI_OVERSOLD" and rsi < 30 and direction == "LONG":
            hit = True
        elif fid == "RSI_30_40" and 30 <= rsi < 40 and direction == "LONG":
            hit = True
        elif fid == "RSI_50_60" and 50 <= rsi < 60:
            hit = True
        elif fid == "RSI_60_70" and 60 <= rsi < 70:
            hit = True
        elif fid == "RSI_OVERBOUGHT" and rsi > 70 and direction == "SHORT":
            hit = True
        if hit:
            adj += score
            bd[fid] = score
    return adj, bd


def _apply_gate_factors(factors: dict, ctx: dict, adj: float, bd: dict):
    for item in factors.get("gates", []):
        if item.get("status") != "live":
            continue
        if item.get("action") != "SCORE_PENALTY":
            continue
        fid = item["id"]
        score = float(item.get("score", 0))
        hit = False
        if fid == "GATE_ATR_Q4":
            atr_pct = float(ctx.get("atr_pct", 0) or 0)
            meta = factors.get("meta", {})
            thresh = float(meta.get("atr_q4_thresh", 0.531))
            if atr_pct > thresh:
                hit = True
        elif fid == "GATE_SESSION_DEAD":
            utc_hour = int(ctx.get("utc_hour", 12) or 12)
            if utc_hour in (0, 22, 23):
                hit = True
        if hit:
            adj += score
            bd[fid] = score
    return adj, bd


def _apply_resonance_factors(factors: dict, ctx: dict, adj: float, bd: dict):
    """三重共振 / RSI_BB双重共振 / FVG方向性等复合因子"""
    triple_long = bool(ctx.get("triple_resonance_long", False))
    triple_short = bool(ctx.get("triple_resonance_short", False))
    rsi_bb_long = bool(ctx.get("rsi_bb_dual_long", False))
    rsi_bb_short = bool(ctx.get("rsi_bb_dual_short", False))
    has_fvg_long = bool(ctx.get("has_fvg_long", False))
    has_fvg_short = bool(ctx.get("has_fvg_short", False))
    direction = ctx.get("direction", "LONG")
    vol_rsi = bool(ctx.get("vol_rsi_optimal", False))

    # 三重共振 +8（6.5万+ 铁证）
    if triple_long and direction == "LONG":
        adj += 8
        bd["TRIPLE_RESONANCE_LONG"] = 8
    if triple_short and direction == "SHORT":
        adj += 8
        bd["TRIPLE_RESONANCE_SHORT"] = 8

    # RSI+BB双重共振 +5
    if rsi_bb_long and direction == "LONG":
        adj += 5
        bd["RSI_BB_DUAL_LONG"] = 5
    if rsi_bb_short and direction == "SHORT":
        adj += 5
        bd["RSI_BB_DUAL_SHORT"] = 5

    # FVG方向性加分 +3
    if has_fvg_long and direction == "LONG":
        adj += 3
        bd["FVG_DIRECTION_LONG"] = 3
    if has_fvg_short and direction == "SHORT":
        adj += 3
        bd["FVG_DIRECTION_SHORT"] = 3

    # VOL+RSI最优区间 +4
    if vol_rsi:
        adj += 4
        bd["VOL_RSI_OPTIMAL"] = 4

    return adj, bd


def apply_dharma_factors(score: float, ctx: dict, breakdown: dict) -> tuple:
    """
    主入口：对 brahma_core score 应用达摩院 live 因子。

    Args:
        score:     当前 confluence score（float）
        ctx:       brahma_core 传入的上下文字段 dict
        breakdown: 当前 breakdown dict（原地更新）

    Returns:
        (new_score, updated_breakdown)
    """
    try:
        factors = _load_factors()
        if not factors:
            return score, breakdown

        adj = 0.0
        bd = {}

        # Gate 层（live 惩罚）
        adj, bd = _apply_gate_factors(factors, ctx, adj, bd)

        # 量能因子
        adj, bd = _apply_volume_factors(factors, ctx, adj, bd)

        # RSI 因子
        adj, bd = _apply_rsi_factors(factors, ctx, adj, bd)

        # 复合共振因子
        adj, bd = _apply_resonance_factors(factors, ctx, adj, bd)

        if adj != 0:
            breakdown.update(bd)
            breakdown["dfe_total"] = round(adj, 2)
            new_score = score + adj
            return new_score, breakdown

        return score, breakdown

    except Exception:
        # 任何异常都不影响主流程
        return score, breakdown

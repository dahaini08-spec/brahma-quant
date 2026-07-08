"""
brahma_v6/risk/position_sizer.py — 10维仓位公式升级
设计院 P2 | 2026-07-08

position_nav =
  base_risk
  × score_quality
  × bucket_ev_multiplier
  × regime_multiplier
  × volatility_multiplier
  × liquidity_multiplier
  × funding_multiplier
  × correlation_multiplier
  × drawdown_multiplier
  × execution_quality_multiplier
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# ── 基准风险配置 ────────────────────────────────────────────
BASE_RISK = {
    "BTCUSDT":  {"base": 0.003, "max_nav": 0.015, "max_lev": 5},
    "ETHUSDT":  {"base": 0.003, "max_nav": 0.012, "max_lev": 5},
    "SOLUSDT":  {"base": 0.002, "max_nav": 0.008, "max_lev": 3},
    "BNBUSDT":  {"base": 0.002, "max_nav": 0.008, "max_lev": 3},
    "_meme":    {"base": 0.0005,"max_nav": 0.003, "max_lev": 2},
    "_default": {"base": 0.001, "max_nav": 0.005, "max_lev": 2},
}


@dataclass
class SizerContext:
    """10维仓位计算所需上下文"""
    symbol: str
    direction: str
    score: float
    regime: str
    bucket_ev_multiplier: float = 1.0   # 来自 EVBucketRegistry
    volatility_pct: float = 2.0          # 1H波动率 %（正常=2%，高=5%+）
    liquidity_depth_usdt: float = 500_000  # ±1% 盘口深度
    funding_rate_annual_pct: float = 10.0  # 年化 %
    correlated_exposure_nav: float = 0.0   # 当前相关敞口占NAV
    account_drawdown_pct: float = 0.0      # 当前回撤 %
    recent_fill_quality: float = 1.0       # 近期成交质量 0~1（滑点/预期比）


def compute_position_size(ctx: SizerContext) -> dict:
    """
    计算建议仓位，返回 {size_nav, leverage, multipliers}。
    """
    sym = ctx.symbol
    cfg = BASE_RISK.get(sym)
    if cfg is None:
        # 判断是否为 meme 类（简单启发）
        if any(x in sym for x in ("PEPE","DOGE","SHIB","FLOKI","BONK","MYXUSDT","PIPPIN")):
            cfg = BASE_RISK["_meme"]
        else:
            cfg = BASE_RISK["_default"]

    base = cfg["base"]

    # 1. score_quality
    s = ctx.score
    if s >= 170:   score_q = 1.20
    elif s >= 165: score_q = 1.10
    elif s >= 155: score_q = 1.00
    elif s >= 138: score_q = 0.75
    elif s >= 120: score_q = 0.50
    else:           score_q = 0.30

    # 2. bucket_ev_multiplier（外部注入）
    ev_mult = max(0.3, min(1.5, ctx.bucket_ev_multiplier))

    # 3. regime_multiplier
    regime_map = {
        "BULL_TREND":    {"LONG": 1.4, "SHORT": 0.2},
        "BEAR_RECOVERY": {"LONG": 1.2, "SHORT": 0.4},
        "BEAR_EARLY":    {"LONG": 0.7, "SHORT": 1.2},
        "CHOP_MID":      {"LONG": 0.6, "SHORT": 0.6},
        "BEAR_TREND":    {"LONG": 0.1, "SHORT": 1.4},
    }
    regime_mult = regime_map.get(ctx.regime, {}).get(ctx.direction, 0.5)

    # 4. volatility_multiplier（高波动降仓）
    v = ctx.volatility_pct
    if v <= 1.5:   vol_mult = 1.2
    elif v <= 2.5: vol_mult = 1.0
    elif v <= 4.0: vol_mult = 0.8
    elif v <= 6.0: vol_mult = 0.6
    else:           vol_mult = 0.4

    # 5. liquidity_multiplier
    depth = ctx.liquidity_depth_usdt
    ref = BASE_RISK.get(sym, BASE_RISK["_default"])
    min_depth = {"BTCUSDT": 500_000, "ETHUSDT": 300_000}.get(sym, 100_000)
    if depth >= min_depth * 5:     liq_mult = 1.1
    elif depth >= min_depth * 2:   liq_mult = 1.0
    elif depth >= min_depth:        liq_mult = 0.8
    elif depth >= min_depth * 0.5: liq_mult = 0.5
    else:                           liq_mult = 0.2

    # 6. funding_multiplier（高资金费降仓）
    fund = abs(ctx.funding_rate_annual_pct)
    # 同方向高资金费为成本
    if fund <= 30:    fund_mult = 1.0
    elif fund <= 80:  fund_mult = 0.9
    elif fund <= 150: fund_mult = 0.7
    else:              fund_mult = 0.4

    # 7. correlation_multiplier（相关敞口越高越降仓）
    corr = ctx.correlated_exposure_nav
    if corr <= 0.01:   corr_mult = 1.0
    elif corr <= 0.02: corr_mult = 0.8
    elif corr <= 0.03: corr_mult = 0.5
    else:               corr_mult = 0.2

    # 8. drawdown_multiplier（回撤越深越降仓）
    dd = ctx.account_drawdown_pct
    if dd <= 3:    dd_mult = 1.0
    elif dd <= 6:  dd_mult = 0.8
    elif dd <= 9:  dd_mult = 0.6
    elif dd <= 12: dd_mult = 0.4
    else:           dd_mult = 0.2

    # 9. execution_quality_multiplier
    exec_mult = max(0.5, min(1.1, ctx.recent_fill_quality))

    # ── 最终仓位 ──
    raw = (base * score_q * ev_mult * regime_mult * vol_mult
           * liq_mult * fund_mult * corr_mult * dd_mult * exec_mult)

    size_nav = round(min(raw, cfg["max_nav"]), 5)

    # 杠杆：根据score和regime自适应
    if ctx.score >= 165 and regime_mult >= 1.0:
        leverage = cfg["max_lev"]
    elif ctx.score >= 155:
        leverage = max(3, cfg["max_lev"] - 1)
    else:
        leverage = min(3, cfg["max_lev"])

    return {
        "size_nav":    size_nav,
        "leverage":    leverage,
        "multipliers": {
            "base":       base,
            "score_q":    round(score_q, 2),
            "ev_mult":    round(ev_mult, 2),
            "regime":     round(regime_mult, 2),
            "volatility": round(vol_mult, 2),
            "liquidity":  round(liq_mult, 2),
            "funding":    round(fund_mult, 2),
            "correlation":round(corr_mult, 2),
            "drawdown":   round(dd_mult, 2),
            "exec_qual":  round(exec_mult, 2),
        },
        "max_nav":  cfg["max_nav"],
        "raw_size": round(raw, 6),
        "capped":   raw > cfg["max_nav"],
    }


if __name__ == "__main__":
    ctx = SizerContext(
        symbol="BTCUSDT", direction="LONG",
        score=162.0, regime="BEAR_RECOVERY",
        bucket_ev_multiplier=1.15,
        volatility_pct=2.1,
        liquidity_depth_usdt=800_000,
        funding_rate_annual_pct=12.0,
        correlated_exposure_nav=0.005,
        account_drawdown_pct=1.5,
        recent_fill_quality=0.95,
    )
    result = compute_position_size(ctx)
    print("BTC LONG 162分 BEAR_RECOVERY:")
    print(f"  size_nav={result['size_nav']*100:.3f}%NAV  leverage={result['leverage']}x")
    print(f"  raw={result['raw_size']*100:.3f}% capped={result['capped']}")
    print(f"  multipliers={result['multipliers']}")
    print("✅ 10维仓位公式验证完成")

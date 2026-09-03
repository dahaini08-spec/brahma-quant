#!/usr/bin/env python3
"""
commodity_adapter.py — 梵天商品资产适配层
设计院封印 2026-09-02 苏摩111

用途：XAU/XAG等传统资产与加密评分体系的适配
核心原则：
  - 复用可复用的维度（SMC/GEX/OI/ATR）
  - 禁用不适用的维度（FR多空比/HCME加密情境库）
  - 补充商品专属信号（COT持仓/宏观相关性）
"""
import requests, time, json
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── 商品资产配置 ──────────────────────────────────────────────────
COMMODITY_CONFIG = {
    "XAU": {
        "name":        "黄金",
        "symbol":      "XAUUSDT",
        "type":        "precious_metal",
        "bucket_size": 5,           # GEX价格区间（$5档）
        "atr_mult":    1.5,         # SL最小倍数（与加密相同）
        "disabled_dims": [          # 禁用不适用的评分维度
            "fr_signal",            # 资金费率对黄金无意义
            "lsr_signal",           # 无官方多空比
            "hcme_match",           # 加密历史情境不适用
            "cross_fr_basis",       # 基差逻辑不同
        ],
        "macro_weight": 2.0,        # 宏观维度权重翻倍（黄金=宏观资产）
        "notes": "黄金受DXY/利率驱动，FR忽略，SMC/GEX/OI有效",
    },
    "XAG": {
        "name":        "白银",
        "symbol":      "XAGUSDT",
        "type":        "precious_metal",
        "bucket_size": 0.5,
        "atr_mult":    1.5,
        "disabled_dims": ["fr_signal", "lsr_signal", "hcme_match", "cross_fr_basis"],
        "macro_weight": 1.8,
        "notes":       "白银兼具工业属性，波动率高于黄金，跟随XAU方向",
    },
}

def is_commodity(symbol: str) -> bool:
    """判断是否为商品资产"""
    s = symbol.upper().replace("USDT","").replace("PERP","")
    return s in COMMODITY_CONFIG

def get_commodity_config(symbol: str) -> dict:
    """获取商品配置"""
    s = symbol.upper().replace("USDT","").replace("PERP","")
    return COMMODITY_CONFIG.get(s, {})

# ── COT持仓报告（CFTC，每周五更新）─────────────────────────────────
def get_cot_signal(symbol: str) -> dict:
    """
    解析CFTC COT报告替代LSR
    黄金COT：商业对冲空头增加 → 机构看跌 → 做多需谨慎
    返回: {signal: BEARISH/BULLISH/NEUTRAL, commercial_net: float, score_addon: int}
    """
    # CFTC提供免费CSV，但解析较重，用缓存文件
    cot_cache = BASE / "data" / "cot_cache.json"
    s = symbol.upper().replace("USDT","").replace("PERP","")

    if cot_cache.exists():
        age = time.time() - cot_cache.stat().st_mtime
        if age < 7 * 86400:   # 7天内有效（COT每周更新）
            try:
                d = json.loads(cot_cache.read_text())
                if s in d:
                    return d[s]
            except Exception:
                pass

    # 暂时返回中性（COT数据源需要额外配置）
    return {"signal": "NEUTRAL", "commercial_net": 0, "score_addon": 0, "source": "no_data"}

# ── 商品宏观信号 ──────────────────────────────────────────────────
def get_commodity_macro(symbol: str) -> dict:
    """
    黄金专属宏观信号：
    - DXY方向（负相关）
    - 实际利率（负相关）
    - 风险情绪
    """
    s = symbol.upper().replace("USDT","").replace("PERP","")
    if s not in ("XAU","XAG"):
        return {}

    result = {"score_addon": 0, "signals": []}
    try:
        # DXY代理：EUR/USD（DXY强=EUR/USD跌=XAU跌）
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol":"EURUSDT"}, timeout=5)
        d = r.json()
        eur_chg = float(d.get("priceChangePercent", 0))
        if eur_chg > 0.3:       # EUR涨=DXY跌=XAU利好
            result["score_addon"] += 3
            result["signals"].append(f"DXY走弱(EUR+{eur_chg:.2f}%) → XAU利好+3")
        elif eur_chg < -0.3:    # EUR跌=DXY涨=XAU利空
            result["score_addon"] -= 3
            result["signals"].append(f"DXY走强(EUR{eur_chg:.2f}%) → XAU利空-3")
    except Exception:
        pass

    try:
        # BTC作为风险情绪代理（BTC暴跌=避险需求升=XAU利好）
        r2 = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbol":"BTCUSDT"}, timeout=4)
        btc_chg = float(r2.json().get("priceChangePercent", 0))
        if btc_chg < -3.0:
            result["score_addon"] += 4
            result["signals"].append(f"BTC暴跌{btc_chg:.1f}% → 避险需求↑ XAU+4")
        elif btc_chg > 5.0:
            result["score_addon"] -= 2
            result["signals"].append(f"BTC大涨{btc_chg:.1f}% → 风险偏好↑ XAU-2")
    except Exception:
        pass

    return result

# ── 商品评分适配器 ────────────────────────────────────────────────
def adapt_score(symbol: str, raw_result: dict) -> dict:
    """
    对brahma_core.analyze()的结果进行商品适配：
    1. 清零禁用维度
    2. 加入商品宏观信号
    3. 加入COT信号
    """
    cfg = get_commodity_config(symbol)
    if not cfg:
        return raw_result

    result = dict(raw_result)
    adjustments = []

    # 清零禁用维度
    disabled = cfg.get("disabled_dims", [])
    for dim in disabled:
        if dim in result:
            adj = result[dim]
            if adj != 0:
                adjustments.append(f"禁用{dim}: {adj:+.0f}→0")
            result[dim] = 0

    # 加入宏观信号
    macro = get_commodity_macro(symbol)
    if macro.get("score_addon"):
        result["commodity_macro"] = macro["score_addon"]
        adjustments.append(f"商品宏观: {macro['score_addon']:+d}")
        for sig in macro.get("signals", []):
            adjustments.append(f"  └ {sig}")

    # 加入COT信号
    cot = get_cot_signal(symbol)
    if cot.get("score_addon"):
        result["cot_signal"] = cot["score_addon"]
        adjustments.append(f"COT持仓: {cot['score_addon']:+d} ({cot['signal']})")

    result["_commodity_adjustments"] = adjustments
    result["_is_commodity"] = True
    result["_commodity_name"] = cfg.get("name", symbol)

    return result

# ── 商品交易规则覆盖 ──────────────────────────────────────────────
COMMODITY_RULES = {
    "XAU": {
        # 黄金体制规则（比加密保守）
        "BULL_TREND":     {"long_ok": True,  "short_ok": False, "max_pos_pct": 5},
        "BEAR_TREND":     {"long_ok": False, "short_ok": True,  "max_pos_pct": 5},
        "CHOP_MID":       {"long_ok": False, "short_ok": False, "max_pos_pct": 0},
        "BULL_EARLY":     {"long_ok": True,  "short_ok": False, "max_pos_pct": 3},
        "BEAR_RECOVERY":  {"long_ok": True,  "short_ok": False, "max_pos_pct": 3},
        # 黄金SL规则
        "sl_pct_long":  0.015,   # 做多SL=1.5%（黄金波动率低）
        "sl_pct_short": 0.015,   # 做空SL=1.5%
        "min_sl_atr":   1.5,     # 最小1.5×ATR4H
    },
    "XAG": {
        "BULL_TREND":     {"long_ok": True,  "short_ok": False, "max_pos_pct": 3},
        "BEAR_TREND":     {"long_ok": False, "short_ok": True,  "max_pos_pct": 3},
        "CHOP_MID":       {"long_ok": False, "short_ok": False, "max_pos_pct": 0},
        "BULL_EARLY":     {"long_ok": True,  "short_ok": False, "max_pos_pct": 2},
        "BEAR_RECOVERY":  {"long_ok": True,  "short_ok": False, "max_pos_pct": 2},
        "sl_pct_long":  0.02,
        "sl_pct_short": 0.02,
        "min_sl_atr":   1.5,
    },
}

def get_trading_rules(symbol: str, regime: str) -> dict:
    """获取商品交易规则"""
    s = symbol.upper().replace("USDT","").replace("PERP","")
    rules = COMMODITY_RULES.get(s, {})
    regime_rule = rules.get(regime, {"long_ok": False, "short_ok": False, "max_pos_pct": 0})
    return {
        **regime_rule,
        "sl_pct_long":  rules.get("sl_pct_long",  0.02),
        "sl_pct_short": rules.get("sl_pct_short", 0.02),
        "min_sl_atr":   rules.get("min_sl_atr",   1.5),
    }

if __name__ == "__main__":
    print("=== 商品适配层测试 ===")
    for sym in ["XAUUSDT","XAGUSDT"]:
        cfg = get_commodity_config(sym)
        macro = get_commodity_macro(sym)
        print(f"\n{sym} ({cfg.get('name')}):")
        print(f"  禁用维度: {cfg.get('disabled_dims')}")
        print(f"  宏观addon: {macro.get('score_addon')} | {macro.get('signals')}")
        rules = get_trading_rules(sym, "BULL_TREND")
        print(f"  BULL_TREND规则: {rules}")


# [P2封印 2026-09-03 苏摩111] apply_commodity_filter = adapt_score别名
# 接入位置: brahma_brain/universal_asset_router.py route_and_score()
apply_commodity_filter = adapt_score


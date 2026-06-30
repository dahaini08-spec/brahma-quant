"""
s7_liq_config.py — s7维度清算墙加分配置
星枢引擎 · 设计院 2026-06-09

职责：集中管理 s7「清算/OI」维度中清算墙密度加分规则。
     修改此文件 = 调参，不触碰任何评分逻辑代码。

设计院裁定：
  ❌ 不改 grade（SMC结构质量，Bridge-Gate依赖）
  ✅ 只影响 s7 维度附加分（上限 +4，总维度上限 15）
  ✅ 不影响 enhanced_score（那是另一个维度）

数据来源优先级：
  1. ws_guardian !forceOrder@arr → 近1H滚动真实爆仓量（实时）
  2. Tardis CSV 月初历史 → liq_scanner（月度参考）
  3. 无数据 → 静默降级，s7 不加分
"""

# ── 清算墙密度等级阈值（单侧爆仓量，USD）──────────────────────
LIQ_DENSITY_BONUS: dict[str, float] = {
    "extreme": 4.0,   # > $50M 单侧清算：机构级别清算瀑布
    "strong":  3.0,   # $20M ~ $50M：明显清算墙被扫
    "medium":  2.0,   # $5M  ~ $20M：中等清算事件
    "weak":    1.0,   # $1M  ~ $5M ：轻微清算
}

LIQ_DENSITY_THRESHOLDS: dict[str, float] = {
    "extreme": 50_000_000,   # $50M
    "strong":  20_000_000,   # $20M
    "medium":   5_000_000,   # $5M
    "weak":     1_000_000,   # $1M（低于此值不加分）
}

# 双向极端爆仓惩罚（两侧总量 > 此值时扣分）
LIQ_CHAOS_THRESHOLD: float = 20_000_000   # $20M
LIQ_CHAOS_PENALTY:   float = -2.0

# 方向不对称比例门槛（单侧 > 对侧 × 此倍数才算方向确认）
LIQ_DIRECTION_RATIO: float = 1.5


def get_liq_bonus(side_usd: float) -> tuple[int, str]:
    """
    根据单侧爆仓量返回 (加分, 等级名称)
    side_usd: 方向一致侧的爆仓总量（USD）
    """
    for level in ("extreme", "strong", "medium", "weak"):
        if side_usd >= LIQ_DENSITY_THRESHOLDS[level]:
            return int(LIQ_DENSITY_BONUS[level]), level
    return 0, "none"

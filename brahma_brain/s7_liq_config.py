"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# s7清算配置常量
# LAST_REVIEW: 2026-07-11 | P0-B 流动性分级L1~L5接入 [苏摩111批准]
# ─────────────────────────────────────────────────────────────
s7_liq_config.py — s7维度清算墙加分配置
星枢引擎 · 设计院 2026-06-09
[P0-B 升级 苏摩111 2026-07-11] 流动性分级L1~L5差异化阈值

职责：集中管理 s7「清算/OI」维度中清算墙密度加分规则。
     修改此文件 = 调参，不触碰任何评分逻辑代码。

设计院裁定：
  ❌ 不改 grade（SMC结构质量，Bridge-Gate依赖）
  ✅ 只影响 s7 维度附加分（上限 +4，总维度上限 20）
  ✅ L1大币需要更大清算量才能同等加分（防止战略欠佳）
  ✅ L4/L5小币降低阈值（小币单品清算$1M就是极端事件）

数据来源优先级：
  1. ws_guardian !forceOrder@arr → 近1H滚动真实爆仓量（实时）
  2. Tardis CSV 月初历史 → liq_scanner（月度参考）
  3. 无数据 → 静默降级，s7 不加分
"""

# ── 流动性分级清算阈值（L1~L5差异化）─────────────────────────────
# 设计院铁证 [P0-B 2026-07-11]:
# L1(BTC/ETH)日均清算>$200M，$50M局面正常；
# L4小币日均清算<$5M，$1M就是清算潮——阈值需降低10x
# 统一加分层级不变，只调整触发下限
LIQ_DENSITY_THRESHOLDS_BY_TIER: dict[int, dict[str, float]] = {
    1: {  # BTC/ETH 主流
        "extreme": 80_000_000,   # $80M 机构级别清算瀑布
        "strong":  30_000_000,   # $30M
        "medium":  10_000_000,   # $10M
        "weak":     3_000_000,   # $3M
    },
    2: {  # SOL/BNB/XRP等次主流
        "extreme": 50_000_000,   # $50M
        "strong":  20_000_000,   # $20M
        "medium":   5_000_000,   # $5M
        "weak":     1_000_000,   # $1M
    },
    3: {  # DOGE/AVAX/LINK等中等
        "extreme": 20_000_000,   # $20M
        "strong":   8_000_000,   # $8M
        "medium":   2_000_000,   # $2M
        "weak":       500_000,   # $500K
    },
    4: {  # 中小币种
        "extreme":  5_000_000,   # $5M
        "strong":   2_000_000,   # $2M
        "medium":     500_000,   # $500K
        "weak":       100_000,   # $100K
    },
    5: {  # 超小币/姆币
        "extreme":  1_000_000,   # $1M
        "strong":     300_000,   # $300K
        "medium":     100_000,   # $100K
        "weak":        20_000,   # $20K
    },
}

# 向后兼容：原单一阈值表保留（默认L2层级，平衡断表）
LIQ_DENSITY_THRESHOLDS: dict[str, float] = LIQ_DENSITY_THRESHOLDS_BY_TIER[2]

LIQ_DENSITY_BONUS: dict[str, float] = {
    "extreme": 4.0,   # 机构级别清算瀑布
    "strong":  3.0,   # 明显清算墙被扫
    "medium":  2.0,   # 中等清算事件
    "weak":    1.0,   # 轻微清算
}

# 双向极端爆仓惩罚（两侧总量 > 此值时扣分）
LIQ_CHAOS_THRESHOLD: float = 20_000_000   # $20M
LIQ_CHAOS_PENALTY:   float = -2.0

# 方向不对称比例门槛（单侧 > 对侧 × 此倍数才算方向确认）
LIQ_DIRECTION_RATIO: float = 1.5


def get_liq_bonus(side_usd: float, symbol: str = '') -> tuple[int, str]:
    """
    根据单侧爆仓量和标的流动性层级返回 (加分, 等级名称)
    [P0-B 苏摩111 2026-07-11] 新增 symbol 参数实现L1~L5差异化
    side_usd: 方向一致侧的爆仓总量（USD）
    symbol:   合约名（用于获取流动性层级）
    """
    # 获取流动性层级
    tier = 2  # 默认L2
    if symbol:
        try:
            from brahma_brain.confluence_tf_weights import _get_tier
            tier = _get_tier(symbol)
        except Exception:
            pass

    thresholds = LIQ_DENSITY_THRESHOLDS_BY_TIER.get(
        tier, LIQ_DENSITY_THRESHOLDS_BY_TIER[3]
    )

    for level in ("extreme", "strong", "medium", "weak"):
        if side_usd >= thresholds[level]:
            return int(LIQ_DENSITY_BONUS[level]), level
    return 0, "none"

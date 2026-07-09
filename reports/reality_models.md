# reports/reality_models.md
# Reality Models — Phase 4A 封口报告
**生成时间：** 2026-07-09 UTC | **分支：** main

---

## 模块结构

| 文件 | 职责 |
|------|------|
| `brahma_v6/reality/models.py` | `RealityCost` 数据结构 + validate() |
| `brahma_v6/reality/fee_model.py` | Maker/Taker 手续费，双边 round-trip |
| `brahma_v6/reality/slippage_model.py` | 半价差 + 流动性冲击，bps 上限 |
| `brahma_v6/reality/funding_model.py` | 8H 资金费率，方向感知（可正可负） |
| `brahma_v6/reality/impact_model.py` | sqrt(notional/adv) 市场冲击模型 |
| `brahma_v6/reality/reality_engine.py` | 统一入口，组合四模型，输出 `PnLAttribution` |

---

## 接入路径

```python
from brahma_v6.reality.reality_engine import RealityEngine, MarketContext

engine = RealityEngine()
attr = engine.build_attribution(
    gross_pnl = gross_pnl,
    notional  = entry_price * quantity,
    context   = MarketContext(
        avg_daily_volume_usd = 500_000_000,
        funding_rate         = 0.0001,
        direction            = Direction.LONG,
        holding_hours        = 8.0,
    )
)
# attr.validate() 已内部调用，直接传入 TradeLedger.append()
```

---

## 默认参数（Binance USDS-M）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| Taker 费率 | 0.05% | 双边 round-trip |
| Maker 费率 | 0.02% | 双边 round-trip |
| 半价差 | 2 bps × 2 | 主流币 |
| 资金费率 | 0.01% / 8H | 温和市场 |
| 冲击系数 | 10 bps·sqrt | Almgren-Chriss 简化 |

---

## 测试结果

```
tests/test_reality_models.py  34/34 ✅
compileall exit 0
```

**验证覆盖：**
- fee cost reduces net_pnl ✅
- slippage cost reduces net_pnl ✅
- funding cost can be positive or negative ✅
- impact increases with notional/liquidity ratio ✅
- RealityCost.total_drag correct ✅
- engine.build_attribution() 输出通过 PnLAttribution.validate() ✅

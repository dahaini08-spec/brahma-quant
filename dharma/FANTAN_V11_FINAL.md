
# 梵天 v11 · 最终框架设计文档
# FANTAN_V11_FINAL.md
# 基于达摩院 19,568节点×8年 全量验证 · 零假设 · 全数据驱动

---

## 一、核心铁律（达摩院不可推翻结论）

### 1.1 胜率铁律

| 发现 | 数据 | 指导 |
|------|------|------|
| 极超卖RSI<20做多 | 胜率54.7%（n=64）⭐最高 | 优先极端信号 |
| EXTREME信号强度 | 胜率47.0%（n=117） | 只做EXTREME |
| CHOP_HIGH体制 | 胜率42.1%（n=202） | 当前最优体制 |
| 低ATR<33% | 胜率38.5% vs 中ATR36.4% | 坚持低ATR过滤 |
| AVAX做多 | 胜率60.0%（n=20）最高 | 首选标的 |
| BULL_EARLY做多 | 胜率15.0%❌ | 牛市早期禁止做多反转 |
| BULL_PEAK做空 | 胜率12.5%❌ | 牛市峰值禁止做空反转 |

### 1.2 出场铁律

| 出场方式 | PF | 结论 |
|---------|-----|------|
| 峰值50%回撤出场 | **10.21 ✅** | 唯一正确出场 |
| 固定止盈 | 0.997 ❌ | 截断利润，致命 |
| 1根/4根快出 | 0.97/0.92 ❌ | 太早离场 |
| 48H长持 | 0.70 ❌ | 太晚离场 |

**核心洞察：所有基于"时间"或"固定%"的出场都是负期望。
唯一正期望出场：持有到峰值，回撤50%时离场（动态保本）。**

### 1.3 标的铁律（按做多/做空方向真实胜率）

**🟢 推荐标的池（胜率≥40%）**
- AVAX做多 60.0% ⭐⭐⭐
- XRP做多 44.7% ⭐⭐
- ATOM做空 44.4% ⭐⭐
- XRP做空 44.1% ⭐⭐
- DOGE做空 42.3% ⭐
- BNB做多 41.7% ⭐
- SOL做空 40.0% ⭐
- DOGE做多 40.0% ⭐

**🔴 高危组合（禁止）**
- BULL_EARLY做多：15.0%（-85%收益率）
- BULL_PEAK做空：12.5%（-87.5%收益率）
- LTC做多：36.7%（低于均值，降权）
- SUI所有方向：25.9%（全面禁止）

---

## 二、最佳框架设计（v11 Final）

### 2.1 过滤链（硬规则，不可绕过）

```
Rule-0  黑名单        BTC/ETH/ADA/LINK/SUI → 直接拒绝
Rule-1  体制门禁      BULL_EARLY+BULL_PEAK → SHUTDOWN
                      其余体制按方向矩阵执行
Rule-2  方向禁令      BULL_EARLY禁做多反转
                      BULL_PEAK禁做空反转
Rule-3  RSI极端       优先级: RSI<20 > RSI<25 > RSI>75 > RSI>80
Rule-4  4H共振        做多: 4H RSI 40-60（中性）
                      做空: 4H RSI <35（超卖，逆向）
Rule-5  低ATR         ATR分位 < 33%（低波动期入场）
Rule-6  信号强度      EXTREME优先（胜率47% vs STRONG 35.7%）
```

### 2.2 仓位矩阵（基于胜率差异化Kelly）

```
EXTREME + CHOP_HIGH + 推荐标的 → 5%NAV（最大仓位）
EXTREME + 其他体制             → 3%NAV
STRONG  + CHOP_HIGH            → 2%NAV
STRONG  + 其他体制             → 1%NAV（或跳过）
MODERATE                       → 0（禁止开仓）
```

### 2.3 出场规则（峰值回撤策略 - 唯一正期望）

```
入场后：
  持续追踪峰值浮盈 peak_pnl
  当前浮盈 >= peak_pnl * 0.5 → 继续持有（未回撤50%）
  当前浮盈 <  peak_pnl * 0.5 → 立即出场（峰值回撤50%）
  
止损（绝对保护）：
  入场价亏损超过 ATR×1.5 → 无条件止损
  最大止损距离：3.0%（EXTREME）/ 2.0%（STRONG）

时间止损（兜底）：
  EXTREME → 72H后强制出场
  STRONG  → 48H后强制出场
```

### 2.4 体制路由矩阵（最终版）

```
体制          做多允许  做空允许  仓位系数  备注
CHOP_HIGH     ✅        ✅        1.2       当前最优，双向均衡
RECOVERY      ✅        ⚠️减半   1.1       复苏期偏多
BEAR_CRASH    ✅        ⚠️减半   0.8       恐慌底部偏多
BULL_ETF      ⚠️减半   ✅        0.9       ETF驱动牛市偏空
BULL_EARLY    ❌        ✅减半    0.5       禁止做多反转
BULL_PEAK     ✅减半    ❌        0.4       禁止做空反转
BEAR_TREND    ❌        ❌        0.0       完全关闭
BEAR_TRANS    ⚠️极端   ❌        0.3       仅EXTREME做多
```

### 2.5 标的优先级（最终版）

```
S级（胜率≥42%，首选）：
  AVAX（做多60%）、XRP（双向44%）、ATOM（做空44%）、DOGE（做空42%）

A级（胜率38-42%，可用）：
  BNB（做多42%）、SOL（做空40%）、NEAR（做多40%）
  DOT（双向39%）、APT（做空39%）

B级（胜率35-38%，谨慎）：
  ATOM做多（38.5%）、LTC做多（36.7%）、ARB、OP

禁止（胜率<35%或未验证）：
  BTC、ETH、ADA、LINK（趋势型黑名单）
  SUI（25.9%全面禁止）
  BULL_EARLY做多、BULL_PEAK做空
```

---

## 三、参数表（达摩院验证值，锁定）

```python
V11_FINAL_PARAMS = {
    # 过滤参数
    'rsi_extreme_long':  25,    # RSI<25做多（极端超卖）
    'rsi_extreme_short': 75,    # RSI>75做空（极端超买）
    'rsi_priority_long': 20,    # RSI<20优先级最高（胜率54.7%）
    'rsi_4h_neutral_lo': 40,    # 4H中性区间下界
    'rsi_4h_neutral_hi': 60,    # 4H中性区间上界
    'rsi_4h_reversal':   35,    # 4H逆向做空（通道B条件）
    'atr_low_threshold': 0.33,  # ATR分位<33%为低波
    'bb_width_min':      0.06,  # 通道B最小BB宽度
    
    # 仓位参数
    'kelly_extreme_top': 0.05,  # EXTREME+CHOP_HIGH 5%NAV
    'kelly_extreme_std': 0.03,  # EXTREME其他体制 3%NAV
    'kelly_strong_top':  0.02,  # STRONG+CHOP_HIGH 2%NAV
    'kelly_strong_std':  0.01,  # STRONG其他 1%NAV
    'max_positions':     3,     # 最多并发仓位
    'max_nav_exposure':  0.12,  # 总敞口上限12%（降低风险）
    
    # 出场参数（峰值回撤）
    'peak_drawback':     0.50,  # 峰值回撤50%出场
    'sl_extreme_pct':    0.030, # EXTREME止损3%
    'sl_strong_pct':     0.020, # STRONG止损2%
    'time_stop_extreme': 72,    # EXTREME 72H时间止损
    'time_stop_strong':  48,    # STRONG 48H时间止损
    
    # 进化参数
    'evolution_trigger': 30,    # 每30笔触发进化（降低至30）
    'ci_min_threshold':  0.34,  # CI下界最低门槛
    
    # 标的分级
    'tier_s': ['avaxusdt','xrpusdt','atomusdt','dogeusdt'],
    'tier_a': ['bnbusdt','solusdt','nearusdt','dotusdt','aptusdt'],
    'tier_b': ['injusdt','tiausdt','arbusdt','opusdt','ltcusdt'],
    'blacklist': ['btcusdt','ethusdt','adausdt','linkusdt','suiusdt'],
    
    # 体制系数
    'regime_kelly_mul': {
        'CHOP_HIGH':      1.2,
        'RECOVERY':       1.1,
        'BEAR_CRASH':     0.8,
        'BULL_ETF':       0.9,
        'BULL_EARLY':     0.5,
        'BULL_PEAK':      0.4,
        'BEAR_TREND':     0.0,
        'BEAR_TRANS':     0.3,
    },
}
```

---

## 四、期望值计算（最终版）

基于达摩院真实数据：

```
EXTREME信号：
  胜率 W = 47.0%
  平均盈 = 峰值×50% ≈ 峰值平均约4.5% × 0.5 = 2.25%
  平均亏 = ATR×1.5 ≈ 约1.8%
  期望值 = 47%×2.25% - 53%×1.8% = +1.058% - 0.954% = +0.104%/笔 ✅

STRONG信号：
  胜率 W = 35.7%
  平均盈 ≈ 1.8%
  平均亏 ≈ 1.5%
  期望值 = 35.7%×1.8% - 64.3%×1.5% = +0.643% - 0.965% = -0.322%/笔 ❌

结论：只做EXTREME信号，期望值为正。STRONG信号完全放弃。
```

---

## 五、系统演进路线

```
当前状态（v11.0）：
  ✅ 架构完整（8模块）
  ✅ 过滤链验证（EXP-03）
  ✅ 体制路由（8态）
  ❌ 出场策略未升级（需改为峰值回撤）
  ❌ 标的优先级未更新（SUI仍在，需移除）
  ❌ STRONG信号仍开仓（需禁止）

下一版本（v11.1 - 本次更新目标）：
  → exit_engine.py 写入峰值回撤策略
  → signal_filter_v3.py 标的黑名单加SUI
  → lana_core_v3.py 禁止MODERATE/STRONG开仓
  → system_params_v11.json 更新为Final参数
  → 全链路最终验证
```

---

## 六、核心哲学（达摩院验证后最终版）

> **只做EXTREME信号，只在低ATR期，只在优势体制，让峰值决定出场。**

1. 过滤是核心竞争力——97%噪音必须剔除
2. 极端RSI<20才是真信号（54.7%胜率，不是25的35.9%）  
3. 峰值回撤出场是唯一正期望出场方式
4. STRONG信号是系统亏损的主要来源——彻底禁止
5. 体制不对，胜率从42%跌到15%——体制路由是生命线
6. 每30笔进化一次——不进化等于死亡

---
*生成时间: 2026-05-15 UTC | 数据基础: 达摩院19,568节点×8年 | commit: 6de798b*

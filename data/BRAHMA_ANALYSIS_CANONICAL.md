# 梵天币种分析 · 标准模式固化文档
# 6方联合封印 · 2026-07-09 08:48 UTC
# 版本：v1.0

---

## 触发词 → 层级映射（唯一真相）

| 触发词 | 层级 | 输出 |
|--------|------|------|
| 梵天快讯 / 快速分析 | L1 | ≤300字快摘要 |
| 梵天分析 / 深度分析 / 分析BTC / 分析ETH / 梵天深度分析 | L2 | 6段完整报告 |
| 梵天35维 / 35维 | L2+ | L2 + 35维有效维度展开 |
| 六方联合 / 全能力分析 / 全系统分析 | L3 | 8段完整 + VIP自动推送 |
| vip策略 / 发送vip策略 / 今日布局 | VIP | 仅F款模板 + 推送Jarvis |

---

## L1 输出模板（快讯）

```
━━━━━━━━━━━━━━━━━━━━━━━━━
📊 梵天快讯 · {TIME} UTC
{SYM1} ${PRICE1} ({CHG1}%) | {SYM2} ${PRICE2} ({CHG2}%)
体制：{REGIME1} | {REGIME2}
信号：{SYM1} score={S1} valid={V1} | {SYM2} score={S2} valid={V2}
门控：{GATE_SUMMARY}
操作：{ONE_ACTION}
━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## L2 输出结构（6段固化）

[S1] 全局快照表格
[S2] 门控三层（P0B / StructureGate / TimingFilter）
[S3] 多维技术画像（RSI / BB / SMC / 量能 / 衍生品）
[S4] 关键价位速查
[S5] 合约交易计划（entry/sl/tp1/tp2/size/lev/rr）
[S6] 裁决（当前最优操作一句话）

---

## L2 标准模板

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ 梵天分析 · {SYM} | {TIME} UTC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[S1 快照]
价格 ${PRICE} | 体制 {REGIME} | score {SCORE} | grade {GRADE} | valid {VALID} | timing {TIMING}

[S2 门控]
P0B：{P0B_STATUS} · 价格${PRICE} vs EMA200${EMA200}（{P0B_GAP}%）
StructureGate：{SG_STATUS} · grade={GRADE}（{SG_LEVEL}）
TimingFilter：{TF_STATUS} · {TF_SCORE}分 · {TF_REASON}

[S3 技术画像]
RSI：15M={R15M} 1H={R1H} 4H={R4H} 1D={R1D}
BB：pos={BB_POS}（{BB_ZONE}） 上轨=${BB_UP} 中轨=${BB_MID}
SMC：{SMC_STRUCT} BOS=[{BOS}] CHoCH=[{CHOCH}] 摆高={SWING_HI} 摆低={SWING_LO}
量能：{VOL_LEVEL} · {VOL_NOTE}
衍生品：OI_chg={OI_CHG}% FR={FR} Basis={BASIS}% LSR={LSR}%

[S4 价位]
阻力：${R1} / ${R2} / ${R3}
入场⭐：${ENTRY_LO}~${ENTRY_HI}
止损：${SL}（-{SL_PCT}%）
目标：${TP1}（TP1 R:R={RR1}） / ${TP2}（TP2）

[S5 计划]
方向：{DIR} | 仓位：{POS}% NAV | 杠杆：{LEV}x | R:R：{RR}

[S6 裁决]
→ {SYM} {DIR} 等 {TRIGGER} 入场
   止损 ${SL}，TP ${TP1}/${TP2}，当前距入场区{GAP}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## L3 输出结构（8段固化）

[H1] 全局状态表（所有品种）
[H2] 六方深度推理（体制院/SMC院/量化院/衍生品院/宏观院/交易员）
[H3] 35维逐项（只展示有效维度，n/a跳过）
[H4] 三品种完整入场参数
[H5] 六方评分汇总表
[H6] 关键价位速查
[H7] 最终裁决
[H8] VIP策略F款 → 自动推送Jarvis线程

---

## 质量检查清单（每次输出前强制验证）

- [ ] 价格来源：实时引擎调用，非缓存
- [ ] 三门状态：P0B / StructureGate / TimingFilter 全部明确填写
- [ ] 入场区：来自 params.entry_lo / params.entry_hi（禁止人工估算）
- [ ] VIP体制标注：必须注明当前体制
- [ ] 决策闭环：末尾必须有"当前最优操作"一句话
- [ ] 禁止输出引擎日志行（[BrahmaBrain] 等）
- [ ] 禁止输出n/a维度（L3中跳过未返回模块）
- [ ] 禁止P0B封锁期间建议做多（违反梵天宪法）

---

## 禁止项（铁证封印）

1. 输出引擎日志（[BrahmaBrain] / [RSM] 等行）
2. n/a维度占篇幅（L3中有效维度才展开）
3. 模糊表述（"可以考虑" / "或许可以"）
4. P0B封锁期间建议同向（BEAR_TREND做多 / BULL_TREND做空除非精英通道）
5. 使用上次分析价格而非实时价格
6. 止损价格来源不明（必须来自系统dynamic_sl或params.stop_loss）

---

## 分析引擎调用标准

```python
# 标准调用方式（不可省略）
from brahma_brain.brahma_analysis_runner import run_analysis

btc = run_analysis('BTCUSDT')  # 每次分析必须实时调用
eth = run_analysis('ETHUSDT')

# 核心字段提取顺序
price      = r['price']                   # 实时价格
regime     = r['regime']                  # 体制
score      = r['score_final']             # 梵天评分
grade      = r['effective_grade']         # 有效grade
valid      = r['valid_signal']            # 是否有效信号
timing     = r['timing_status']           # TimingFilter状态
entry_lo   = r['params']['entry_lo']      # 入场区下沿
entry_hi   = r['params']['entry_hi']      # 入场区上沿
sl         = r['params']['stop_loss']     # 止损价
sl_pct     = r['params']['sl_pct']        # 止损幅度
tp1        = r['params']['tp1']           # 目标1
tp2        = r['params']['tp2']           # 目标2
rr1        = r['params']['rr1']           # R:R
pos_pct    = r['pos_pct_sizer']           # 仓位%
pos_level  = r['pos_level_sizer']         # 仓位级别
```

---

## 体制→策略映射（梵天宪法）

| 体制 | 主方向 | 默认SIZE | 默认LEV | 特殊限制 |
|------|--------|----------|---------|---------|
| BEAR_TREND | 空为主 | 5% | 5x | 做多须grade≥155且精英通道 |
| BEAR_EARLY | 空为主 | 3% | 5x | 做多须score≥140 |
| BEAR_RECOVERY | 多为主 | 2% | 2x | 7月上旬减半仓=1% |
| CHOP_MID | 不发策略 | 0 | — | score≥110→WATCH 0.5%NAV |
| BULL_TREND | 多为主 | 5% | 5x | 做空须grade≥120 |

---

## WR胜率参考（历史铁证）

| 体制 | 方向 | WR | n |
|------|------|----|---|
| BULL_TREND | LONG 15M | 70.5% | 1395 |
| BULL_TREND | LONG 1H | 70.2% | 1250 |
| BEAR_TREND | SHORT | 68.1% | 997 |
| BEAR_RECOVERY | LONG | 62.8% | 843 |

---
*最后更新：2026-07-09 08:48 UTC | 6方联合封印*

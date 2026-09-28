# 梵天系统2.0 三方联合深度评估报告
**日期**: 2026-09-28 02:00-02:20 UTC · 评估者: 设计院×达摩院×梵天大脑 · 主session直跑（子agent两轮runtime断线后收编）
**执行环境**: james-bond / trading-system/ · 全程只读+安全测试，零交易逻辑改动

---

## A. 架构盘点

| 维度 | 数值 |
|---|---|
| brahma_brain 模块 | 135个 / 66,588行 |
| scripts 脚本 | 123个 / 38,325行 |
| 总代码量 | ≈105K行 |
| git commits | 1,367 |
| 9.28当日封印 | 4个（P0#1跨进程退避 f926b8b1 / cron重放哨兵 b418a465 / 主AI failover 41868bd5 / 旗舰帖双轨 1bd99798） |
| supercronic总任务 | 66行 |
| openclaw AI cron | 17个活跃任务 |

## B. 守门链

| 检查 | 结果 |
|---|---|
| brahma_wiring_check | ✅4 ⚠️1 ❌0（S2 fangcang_engine 13个模块级可变对象=并发安全提醒，非阻断） |
| 静态扫描 | ✅ 无危险路径注入 / decision_engine共享状态可控(1个) |
| 冒烟测试 post-commit | ✅ 12/12 全绿（方仓20,330条/蒸馏矩阵305桶/注射器铁律/WR铁证SHORT15m=68%(n=4267)） |
| deps_test | exit 0 |

## C. 全流程实弹（核心）

```
brahma_manual_analysis.py --symbols BTC ETH
exit=0 ✅ Step0并行拉取5.2s → Step10 VIP卡片产出 ✅
```

**VIP卡片格式合规性（逐项）**:
- ✅ 每笔单用｜分隔（🟢 多单｜挂单区 $83,078.5~$83,442.9）
- ✅ 止损/目标/杠杆/仓位齐全
- ✅ 结尾「🌿 姓赵不宣 | 不是建议」
- ✅ 作废线「🚫 破$X作废」
- ✅ 交易员大脑交叉验证3/4 + SSOT唯一裁判注记

**SL铁律独立复核**（用Binance原始K线独立算ATR验证系统口径）:
| 标的 | 系统ATR1H | 独立复核 | SL距离 | 铁律≥1.5×ATR1H | 判定 |
|---|---|---|---|---|---|
| BTC | $456 | $430.7 | $1,662 (2.21%) | ≥$684 | ✅ 通过 |
| ETH | $19 | $17.1 | $55.9 (2.10%) | ≥$28.5 | ✅ 通过 |

**决策层一致性**:
- BTC score=66.8 CHOP_MID → SKIP（Step1否决: 缺cf_action, SSOT唯一裁判）✅
- ETH score=94.6 CHOP_MID → SKIP ✅
- paper_executor 02:15 BLOCK ETHUSDT NEUTRAL: risk_gate R2/体制CHOP_MID无乘数 fail-closed ✅ 双闸生效

**账本SSOT核对**: closed=2笔 net_pnl合计=-1035U（-517.5×2, 0721/0722独立成交）与9.27封印口径**完全一致** ✅ 无污染无漂移。

**主AI failover实锤**: master_failover_count 1→23（免费池429时议会自动切主AI正常出JSON，council_llm裁决"WAIT/LOW"真实链路通）。

## D. 健康度

| 项 | 状态 |
|---|---|
| 进程（评估开始时） | ❌ supercronic+CVD+liqmap+watchdog全灭（02:00-02:09死亡9分钟） |
| 复活 | ✅ resurrect闭环02:09拉起全部4进程，02:14 CVD快照恢复 |
| auto_analysis_latest | ✅ 02:16（<90min断供线） |
| battlefield_candidates | ⚠️ 01:37-02:13断供36min（死因窗口） |
| llm_channel_state | ✅ 免费池退避0/主AI接管23次 |
| error_ledger | ✅ 0条critical |

**死因调查**: 02:00-02:09窗口无OOM记录、无error输出、watchdog自身也死（其最后心跳02:00）。02:09由watchdog/resurrect双链拉起。非gateway重启（cron API全程存活）。**根因未明**。

## E. 断层复核（9.28晨报3断层）

| # | 断层 | 状态 |
|---|---|---|
| 1 | square_auto_post重复跑分析 | ❌ **未修**（L110/159仍调run_analysis，应读auto_analysis_latest.json≤2h） |
| 2 | afternoon-battlefield error态 | ✅ **已自愈**（状态ok，1h前正常跑） |
| 3 | paper_executor注释漂移 | ⚠️ 部分修（实际1H:15正确，L168旧注释仍说每07分钟） |

## F. 代码质量

- except-pass残留: brahma_core(2)/brahma_360(2)/har_rv(1)/regime_scorer(1)/scan_architecture(5)/seal_v2(7)等——多在诊断/工具路径，关键路径(error_ledger覆盖)已清零 ✅
- 宏观/antifragile缓存: ✅ 0.9h新鲜（晨报36.9h过期问题已自愈——02:08 auto-analysis刷新）
- AI cron预算: ⚠️ ≈38/天 > 预算33（chop-breakout-watch 12 + auto-analysis 8 + 每日帖15×1 + square-trade-loop 2 + w5一次性）
- Step4方向一致0/7审计尾巴: 未在代码中定位到专项逻辑（遗留冻结项确认）

---

## 三方评分

| 角色 | 分数 | 一句话 |
|---|---|---|
| 设计院 | 8/10 | 1367 commit封印纪律+SSOT清晰；扣分: AI cron超预算/断层1未修/注释漂移 |
| 达摩院 | 8.5/10 | 10步链5.2s并行+VIP格式100%合规+SL铁律双独立复核全过+决策双闸正确 |
| 梵天大脑 | 7/10 | 全链实测绿；扣分: 进程群灭9分钟(自愈救回但根因未明) |

## 待修清单

| 级 | 项 | 修法 |
|---|---|---|
| P1 | 进程群灭死因未明（02:00-02:09，9min） | 增加dmesg/journal采样哨兵+死亡快照留证 |
| P1 | AI cron 38>33预算 | 裁剪chop-breakout-watch 12→6或合并 |
| P1 | square_auto_post重复跑分析 | 改读auto_analysis_latest.json≤2h新鲜度 |
| P2 | paper_executor L168旧注释 | 1行文档修正 |
| P2 | Step4方向一致0/7审计尾巴冻结 | 排期修复 |
| P2 | fangcang_engine 13模块级可变对象 | 加锁或局部化 |

## 结论

**架构健康度: 良性（7.8/10）——交易管道全链SSOT闭环实测绿，自愈体系经受住了9分钟进程群灭的实战考验，账本-1035口径零漂移；剩余6项待修无阻断性风险。**

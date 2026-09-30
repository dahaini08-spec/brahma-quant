# Cron消费者声明清单 v1.0 定稿（2.0设计书§9待决项3）
> 2026-09-30 苏摩111 · 数据源：brahma_crontab.txt实测 + `openclaw cron list/runs`实拉 + 脚本grep逐项核验
> 状态：**待苏摩111裁定选项（§七）** · 相对08:07草案为修订版

## 0. 相对草案的5处修正

1. **行数口径**：76 = 67条cron + 9条env行（PYTHONDONTWRITEBYTECODE/SQUARE_KEY×3/OPENCLAW_API/BINANCE×2/DERIBIT×2）。**有效任务行=67**，草案把env行混进了任务数
2. **重大盲区**：草案只覆盖supercronic单层，漏掉**openclaw cron层14个AI会话任务**（全部announce→jarvis同线程）
3. **头号发现**：`process-resurrect-bridge` 5min一次 = **288次AI会话/天**，占openclaw层AI会话~94%，违反9.23「AI cron≤33次/天」铁律8.7倍。近24h实拉运行记录：50次里49次HEARTBEAT_OK（无活可干纯烧token）
4. **草案算术错误**：温和裁剪月省~420次 → 实算~630次/月
5. live-recap 9.29 15:31 error = 一次性Edit memory文件失败（9.28及之前全部ok），非系统性，不修

## 一、双层cron全景（实测）

| 层 | 数量 | 性质 |
|---|---|---|
| Layer1 supercronic（brahma_crontab.txt） | 67条cron = AI类20 + B线47 | 容器内进程级，含9条env行另计 |
| Layer2 openclaw jarvis cron | 14条 | AI会话类，独立于gateway进程树存活 |

## 二、Layer1 AI类20任务消费者声明（草案原表，下游已逐项复核）

| # | 任务 | 调度 | 输出/消费方 | 下游状态 | 建议动作 |
|---|---|---|---|---|---|
| 1 | battlefield_auto_analysis | 2h:15 | push + auto_analysis_latest.json | **实存6消费方**（dashboard×2/manual_analysis/square_auto_post/daily_review_llm/battlefield_intel）；trade_gateway in-process后确实0引用该json | 保留 |
| 2 | oi_watchlist_monitor | 2h:22 | push_jarvis | 独立推送 | 保留 |
| 3 | liq_heatmap | h:45 | push + data快照 | 独立推送 | 保留 |
| 4 | brahma_autocheck | 6h:50 | push_hub告警 | 独立哨兵 | 保留 |
| 5 | cron_health_board | 6h:58 | push_hub告警 | 独立哨兵 | 保留 |
| 6 | square_extreme_alert | 4h:33 | push_jarvis | 独立推送 | 保留 |
| 7-11 | square_hot_poster ×5 | 日5次 | Square API | 独立发帖 | 保留（内容引擎） |
| 12 | daily_review_llm | 16:00 | push + llm_channel_state.json | 复盘链主 | 保留 |
| 13 | brahma_screener | 12:00 | ai_pro_candidates.json | **下游实存**：spot_strategy_square/brahma_scan_all | 保留 |
| 14-15 | square_auto_post ×2 | 1:30/9:30 | Square API | 独立发帖 | 保留 |
| 16 | square_deep_post | 6h:17 | push + Square | 独立发帖 | 保留 |
| 17 | spot_strategy_square | 3:00 | Square API | 消费screener | 保留 |
| 18 | distill_calibrator | 周一3:57 | distill_calibrator_v1.json | shadow工件，smoke_test有消费 | 保留 |
| 19 | divergence_report | 15:50 | push + t2_ab_summary | T2对照盘 | 保留至10.12 |
| 20 | paper_daily_review | 15:30 | 复盘链 | B线复盘 | 保留 |

## 三、Layer2 openclaw 14任务声明（草案缺失层，本次新增）

| # | 任务 | 调度 | 用途 | 日均AI | 蓝图§4.4对齐 |
|---|---|---|---|---|---|
| 1 | process-resurrect-bridge | **5min** | supercronic死后的外部复活环 | **288** | 蓝图外 ⚠️ |
| 2 | auto-analysis | 3h | 分析数据广播 | 8 | ✓ |
| 3 | square-trade-loop | 12h | Square策略帖 | 2 | 蓝图外 |
| 4 | morning-battlefield | 01:00 | VIP晨报 | 1 | ✓ |
| 5 | afternoon-battlefield | 09:00 | VIP晚报 | 1 | ✓ |
| 6 | afternoon-deep-analysis | 07:00 | 深度帖×2（今晨985+947字） | 1 | 与蓝图「afternoon重复分析」字面冲突，实为旗舰产能 |
| 7 | noon-hot-pick | 04:00 | 热度帖 | 1 | 蓝图外 |
| 8 | evening-heat-post | 13:00 | 热度帖 | 1 | ✓ |
| 9 | blood-chip-morning | 06:00 | 带血筹码扫描 | 1 | ✓ |
| 10 | blood-chip-evening | 18:00 | 带血筹码扫描 | 1 | ✓ |
| 11 | smc-education-post | 06:30 | SMC教学帖 | 1 | ✓ |
| 12 | live-recap | 平日15:30 | 直播复盘帖 | ~0.7 | preview已死=合并完成，recap为幸存者 |
| 13 | weekly-deep-article | 周日14:00 | 深度文 | 0.14 | ✓ |
| 14 | brand-identity-post | 周一02:00 | 品牌帖 | 0.14 | ✓（已降周1） |

小计：**非bridge ≈ 19次/天（✓≤33铁律）；bridge独占288 → 合计~307**

## 四、头号发现：bridge 288次/天（§四必读）

**bridge价值**（9.23三方修复产物）：supercronic死了resurrect没人跑（看门狗死锁），openclaw cron独立于supercronic进程树，5min兜底拉起
**bridge成本**：288次/天 litellm/standard AI会话（每次带全套system prompt），保护的只是「supercronic单独死亡」这一窗口

**替代方案（B线化，代码已想好待批）**：
1. 新增 `scripts/watchdog_daemon.sh`：setsid脱离会话，`while true; do pgrep supercronic || setsid ./start_supercronic.sh; sleep 60; done`
2. `process_resurrect.sh` 托管名单加daemon → **互救闭环**：supercronic活→resurrect每1min救daemon；daemon活→60s救supercronic
3. bridge降频 5min→2h 做最外环（只兜「daemon+supercronic同死」场景，288→12次/天）

**收益**：省276次/天（~8,280/月AI会话）；复活延迟5min→60s（更快）；保护从1环变3环

## 五、蓝图§4.4对账（66→22行，AI 17→11）

- ✓ 已达成：brand-identity降周1 / chop-breakout-watch=0 / event-news已并入heat / live-preview已无
- ⚠️ 偏差：live-recap独立存在（合并语义可解释为幸存者，待确认直播档期）；afternoon-deep-analysis在产（旗舰内容vs蓝图砍单字面）；noon-hot-pick/square-trade-loop为蓝图外新增
- ⚠️ 「22行」未定义双层口径（Layer1单层 or 两层合并）→ 需苏摩裁定后才能算进度

## 六、草案待裁4项（数字修正版）

| 裁剪项 | 现→目标 | 日省AI |
|---|---|---|
| square_hot_poster ×5→×3 | 砍losers/summary或错峰 | 2 |
| liq_heatmap h→2h | 24→12 | 12 |
| oi_watchlist_monitor 2h→4h | 12→6 | 6 |
| square_deep_post 6h→8h | 4→3 | 1 |

合计~21/天 ≈ **630/月**（草案420系算错）

## 七、待批选项

- **A. 只治bridge（推荐）**：daemon落地+bridge降2h。零内容损失，省~8,280/月，复活更快
- **B. A+温和裁剪**：再加~630/月，内容产能小幅下降
- **C. 硬目标22行**：等10.12 T2对照盘结论后一并，先定义双层口径
- **D. 维持现状**：bridge持续烧standard配额，不推荐

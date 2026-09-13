# 声明 vs 证据对照表
<!-- 2026-09-13 审计止血 | 每行数字必须能 grep 到生成器 -->

## 1. 胜率（WR）

| 指标 | README声称 | 仓内证据 | 判定 |
|------|-----------|---------|------|
| Live WR | 62% (n=186) | wr_matrix.json: 47W/68L = 40.9% (n=124, 2026-09-05) | ❌ 偏差+21pp |
| OOS WR | 82.7% (n=121) | archive OOS: 46.6%, Sharpe −3.96, MDD 59% | ❌ 符号相反 |
| BULL_TREND LONG WR | 70.3% | wr_matrix.json: 33W/63L = 34.4% (n=96) | ❌ 偏差+36pp |
| BEAR_RECOVERY LONG WR | 100% (n=7) | wr_matrix.json: 7W/0L = 100% (n=7) | ⚠️ 样本不足 |
| BULL_EARLY LONG WR | 100% (n=5) | wr_matrix.json: 5W/0L = 100% (n=5, 42 expired) | ⚠️ 样本不足 |
| BEAR_RECOVERY SHORT WR | — | wr_matrix.json: 0W/3L = 0% (n=3) | ❌ 永久封禁已确认 |
| BEAR_EARLY SHORT WR | 66.5% (STAR) | v8矩阵: 38.9% (做多61%) | ❌ 方向写反 |

## 2. Pump Hunter

| 指标 | README声称 | 仓内证据 (hunter_win_rate.json) | 判定 |
|------|-----------|------|------|
| Hunter WR | 97.5% (TIGHT<15%, n=1600, 2yr) | 4h: 4.68% (n=342) / 8h: 10.64% (n=376) / 7d: 64.49% (n=107) | ❌ 不可比 |
| 97.5% 来源 | 无生成脚本 | 仓库内无任何脚本产出97.5%数字 | ❌ 无法复现 |
| 13H volume 100% | n=19 | STAR.md自定n<30禁止当结论 | ❌ 违反自定规则 |
| 信号总量 | — | 82,622次扫描 → 1,427次推送 | ⚠️ 选择偏差 |

## 3. 统计验证

| 指标 | README声称 | 仓内证据 | 判定 |
|------|-----------|---------|------|
| DSR | 22.64 (CPCV 15-path) | 无CPCV/DSR实现代码 | ❌ 不存在 |
| MC Sharpe P50 | 25.09 | bootstrap未乘杠杆，不复利 | ❌ 方法错误 |
| MC P50 | +1364% | 加法bootstrap，终点NAV非路径 | ❌ 方法错误 |
| MC Ruin | 0% (5×–25× leverage) | bootstrap_trades不用杠杆 | ❌ 设定出来的 |
| WF 19-fold | 全过 | 每窗n≈10，WR=100%窗仅十几笔 | ❌ 样本不足 |
| CPCV | ✅ | 零行CPCV代码 | ❌ 不存在 |
| 过拟合率 | 33.3% (<50%✅) | 阈值拍的，无可复现脚本 | ❌ 无效 |

## 4. 安全

| 问题 | 文件 | 修复 |
|------|------|------|
| 硬编码Binance密钥默认值 | scripts/whale_monitor.py, scripts/liq_heatmap.py | ✅ 改为空字符串+警告 |
| TLS验证关闭(CERT_NONE) | 11个文件 | ✅ 全改为CERT_REQUIRED |
| AI4TRADE_TOKEN明文 | TOOLS.md (私有) | ⚠️ 本地保留 |

## 5. 执行层

| 声明 | 实际 | 判定 |
|------|------|------|
| OMS已删除 | scripts/auto_executor.py (2299行) 仍在 | ❌ 残留 |
| 纸面系统运行 | 微秒级MANUAL平仓 | ❌ 非真实纸面 |
| risk_engine接入执行 | auto_executor.py 零引用risk_engine | ❌ 未接入 |
| 9层熔断 | circuit_breaker.py是软件熔断非交易闸门 | ❌ 名不对实 |
| Kronos AAAI 2026 | kronos_bridge.py返回(0.0, stub) | ❌ 死代码 |

## 6. 蒙特卡洛独立验证（审计师模拟）

| 情景 | 假设 | P50终点 | ruin概率 | 判定 |
|------|------|---------|---------|------|
| P 实盘账本 | WR=40.9%, 120笔 | 82万 | 25% | ⚠️ 中位亏损 |
| A 诚实数 | WR=51.8%, RR=1.3, 150笔 | 133万 | ~0% | 可接受 |
| D README面值 | WR=82.7%, RR=2, 风险2% | 3304万 | 0% | ❌ 不可能 |

---

**结论：** README的所有核心战绩数字无法从仓库结算文件复现。唯一可信的数字来源是 `data/wr_matrix.json`（40.9%主桶WR）和 `dharma/pump_hunter/hunter_win_rate.json`（4.7%短期WR）。

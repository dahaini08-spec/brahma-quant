# 梵天360 · 问题全景档案
<!-- 最后更新：2026-06-06 07:35 UTC -->
<!-- 用途：统一记录所有已知问题，方便后续集中改造 -->

---

## 🔴 P0 已修复

### [FIX-001] ws_guardian 进程泄漏（2026-06-06 07:14 UTC）
- **现象**：每分钟新增1个ws_guardian进程，累积到10+个
- **根因**：v13.2代码无论有无持仓都忽略SIGTERM → supervisor SIGKILL后误判崩溃 → 无限重启
- **修复**：
  - `ws_guardian.py v13.3`：无持仓时正常响应SIGTERM，sys.exit(0)
  - `supervisor.conf`：autorestart=unexpected + exitcodes=0（正常退出不重启）
- **验证**：进程数稳定=1，不再增长 ✅
- **commit**：526adf8

### [FIX-002] 国库官信号去重失效（2026-06-05）
- **现象**：同一signal_id被批准两次（DOGE两笔，相同信号ID）
- **根因**：去重依赖调用方传正确signal_id，调用方每次生成不同ID
- **修复**：`treasury_gate.py v2.0`  
  - 指纹=MD5(symbol+direction+round(entry_price,2))
  - 90min窗口内同指纹只批准一次
- **验证**：BEAR_RECOVERY拒绝测试通过 ✅
- **commit**：7d7d05c

### [FIX-003] 执行失败国库官不回滚（2026-06-05）
- **现象**：BNBUSDT下单失败（保证金不足），但国库官仍记录持仓OPEN
- **根因**：pipeline_execute未在下单失败时调用request_close
- **修复**：`pipeline_execute.py`  
  - `_approval = None` 全局初始化
  - 入场单失败后强制调用`treasury_gate.request_close(pos_id, 'EXEC_FAILED', 0)`
- **commit**：7d7d05c

### [FIX-004] 保证金未预检（2026-06-05）
- **现象**：NAV固定$1,000，实际余额$132，系统算出notional=$711被Binance拒绝
- **根因**：国库官未接实时余额，未在下单前检查可用余额
- **修复**：`treasury_gate.py v2.0`  
  - 关3b：下单前检查 available_balance × 0.85 安全系数
  - `nav_tracker.py`：每15min同步实时NAV
- **commit**：7d7d05c

### [FIX-005] signal_utils.py 语法错误（2026-06-06）
- **现象**：`load_broadcastable_signals`函数定义括号未闭合，commander读取信号报错
- **根因**：多次SSOT注释追加导致函数签名损坏
- **修复**：合并多个重复注释，修复括号

---

## 🟡 P1 待改造（下次统一修复）

### [TODO-001] pipeline_watch.json 字段为空
- **现象**：4个key存在（DOGEUSDT/BNBUSDT/LTCUSDT/SOLUSDT），但symbol/score/status字段全为None
- **根因**：信号写入时字段名不匹配（写入方用不同key名）
- **影响**：entry_confirm_cron读取到空数据，入场确认链路部分失效
- **方案**：统一pipeline_watch写入格式，标准化字段名
- **优先级**：下次合并改造时处理

### [TODO-002] ws_guardian平仓后不同步国库官
- **现象**：ws_guardian触发TP/SL平仓后，不调用treasury_gate.request_close()
- **根因**：ws_guardian与国库官解耦，平仓只更新brahma_state，不通知国库官
- **影响**：持仓平后国库官记录残留，最多15min延迟（靠nav_tracker对账补救）
- **方案**：在ws_guardian的TP/SL触发逻辑后加入treasury_gate.request_close调用
- **优先级**：有持仓时P0，当前空仓P1

### [TODO-003] binance_fapi.py 缺少 get_account_balance
- **现象**：nav_tracker调用get_account_balance报ImportError，降级到直接HTTP请求
- **根因**：binance_fapi.py未封装该函数
- **影响**：nav_tracker第一层降级失败，用第二层（直接API）补救，功能正常但不够干净
- **方案**：在binance_fapi.py添加get_account_balance函数
- **优先级**：低（已降级正常工作）

### [TODO-004] brahma_state.last_binance_sync 停留在2026-05-27
- **现象**：last_binance_sync=2026-05-27T14:02:39（10天未更新）
- **根因**：brahma_state_guardian_minimal只更新last_updated，不同步Binance仓位
- **影响**：brahma_state中的持仓数据可能不是最新（已被nav_tracker对账补救）
- **方案**：nav_tracker每次运行后更新last_binance_sync字段
- **优先级**：低（功能正常）

### [TODO-005] 四条开单路径并存，互不协调
- **现象**：lana/pipeline_execute/brahma_execute/entry_confirm_cron 四条路径都可以开单
- **根因**：历史迭代中多次添加新路径，未整合
- **影响**：可能叠加开仓（同币种被不同路径各开一单）
- **方案**：全部路由到brahma_commander，commander作为唯一入口
- **状态**：已完成commander框架，待各路径接入
- **优先级**：P0（自动开单前必须完成）

### [TODO-006] auto_execute_bridge.py 链路断开
- **现象**：entry_confirm_log有记录（DOGE/BNB确认），但auto_exec_log无记录
- **根因**：entry_confirm_cron写入确认后，没有触发auto_execute_bridge执行
- **影响**：自动执行链路最后一公里断路
- **方案**：entry_confirm_cron确认后主动调用auto_execute_bridge，或commander轮询确认队列
- **优先级**：P0（自动开单前必须完成）

### [TODO-007] Cron数量过多（当前24个）
- **现象**：24个cron互相不知晓，重复读同一数据
- **目标**：精简至8个（已停用3个）
- **待停用**：
  - 🚨信号仪表盘-30m（改事件驱动后停用）
  - on-demand-scan-30m（commander覆盖）
  - 高信心信号预警（commander覆盖）
  - entry-confirm-15m（commander覆盖）
- **优先级**：P2

### [TODO-008] ETH 失效OB降级机制缺失
- **现象**：ETH grade=19（<50），因OB过期导致结构评分极低
- **根因**：1H FVG/OB寿命短，被价格穿越后仍计入评分
- **方案**：structure_quality_engine.py 失效OB grade-30惩罚
- **优先级**：P2

---

## 🟢 P2 优化项（Phase 3计划）

### [OPT-001] BULL_TREND做多体系
- **现状**：系统只有做空体系（BEAR_EARLY/BEAR_TREND）
- **需求**：BULL_TREND体制下做多信号体系
- **前提**：武曲Paper 200条 + WR≥50%验证后

### [OPT-002] ETH止盈改革
- **方案**：50% TP1 + 50%移动止损（尾部利润最大化）
- **文件**：brahma_execute.py

### [OPT-003] BTC窄止损过滤器
- **方案**：sl% < 0.8% → 降权（入场区太近，噪音内止损）
- **铁证**：BTC ATR(4H)=$1,839，sl<$500属于噪音区

### [OPT-004] LTC+SOL BEAR_EARLY屏蔽
- **铁证**：LTC/SOL在BEAR_EARLY WR=0%，SL率极高
- **方案**：symbol_spec中LTC/SOL排除BEAR_EARLY方向

### [OPT-005] 熔断器与国库官完全集成
- **方案**：日亏>5% NAV → 国库官拒绝所有新申请
- **方案**：连续2SL → 国库官CAUTIOUS模式（仓位减半）

---

## 📊 系统当前评分（2026-06-06）

| 维度 | 评分 | 改造前→后 |
|---|---|---|
| 国库官可靠性 | 75/100 | 47→75（P0三修复） |
| 信号链路完整性 | 60/100 | 四条路径待统一 |
| 进程稳定性 | 85/100 | ws_guardian泄漏已修 |
| 数据一致性 | 70/100 | pipeline_watch待修 |
| 自动开单就绪度 | 45/100 | dry_run验证中 |
| **综合** | **67/100** | 目标：85/100 |

---

## 📋 改造优先级排序

```
本周（P0，自动开单前提）：
  [TODO-005] 四条路径统一到commander
  [TODO-006] auto_execute_bridge链路修通
  [TODO-001] pipeline_watch字段修复

下周（P1，系统可靠性）：
  [TODO-002] ws_guardian平仓后同步国库官
  [TODO-007] Cron精简（24→8）
  [TODO-003] binance_fapi补充函数

下下周（P2+自动开单上线）：
  dry_run 1周无异常 → auto_mode.flag手动开启
  [OPT-001~005] Phase 3优化项
```

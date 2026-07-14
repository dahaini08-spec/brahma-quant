# 梵天系统全局稳定性治理方案
# 设计院量化工程师深度推理封印 2026-07-14

## 当前系统负载（优化后）
总cron: 33个（删除position-guardian-5m后）
总频次: 51.5次/h（原63.5次/h，-19%）

## 三层治理框架

### 层1: 负载治理（过热防护）
已完成:
  ✅ lab-sl-monitor + clu-sl-monitor → position-guardian-unified（今日）
  ✅ position-guardian-5m → 删除（今日，与unified重叠）
  ✅ 全25个纯脚本任务 → lightContext=true（昨日MEMORY封印）

待治理（按优先级）:
  P1: signal-fast-exec(10min) 独立主动扫描 → 考虑并入rsi-structure-watcher事件触发
      当前: 10min固定扫描BTC/ETH = 6次/h独立AI调用
      优化: rsi_watcher事件触发时才执行，非事件时HEARTBEAT_OK
      效果: -4次/h（活跃市场），-6次/h（平静市场）

  P2: main-signal-watcher(1h) + signal-aggregator(1h) 读同一个log
      当前: 两个独立1h任务，功能高度相似
      优化: signal-aggregator 合并进 main-signal-watcher，间隔改为1.5h
      效果: -1次/h

  P3: brahma-self-heal(30min) + route-guardian(30min) 均为健康类
      优化: 合并为 system-guardian，间隔30min，一次完成健康+路由检查
      效果: -2次/h

### 层2: 止损架构（PM账户硬限制）
根因: API权限 enablePortfolioMarginTrading=false
解法路线图:
  方案A（推荐）: 开启Portfolio Margin Trading权限 → 直接挂papi条件单
    效果: 删除position-guardian-unified，止损延迟0ms
  方案B（当前）: position-guardian-unified 5min软止损
    风险: 极端行情5min滑点，已记录在宪法

### 层3: 信号链路（可靠性）
已完成:
  ✅ signal_bus.py sha8去重（今日）
  ✅ 学习器pnl NoneType修复（今日）
  ✅ 暴涨猎手sys.path修复（今日）

待治理:
  P1: 自动化学习回填 → 平仓后自动触发calibration_feedback写入
      当前: 需手动触发，84条历史数据今日才补写
      方案: 在signal_settler.py平仓结算时同步写入calibration_feedback

  P2: 信号重复推送防护 → push_hub.py dedup覆盖所有推送路径
      当前: 部分路径有dedup，signal_bus路径今日修复
      待检: main-signal-watcher和signal_aggregator是否会双推同信号

  P3: OI猎手持久化 → oi_hunter_log.jsonl目前空
      方案: oi_advanced_scanner.py扫描结果写入日志，供学习器消费

## 封口宪法新增规则（今日追加）
规则6: 每次新增cron前必须检查是否与现有cron功能重叠
       检查命令: 与现有cron的message关键词对比
规则7: 5min以内高频cron必须评估替代方案（事件触发优先于定时）
规则8: 同一功能不超过2个cron覆盖（防止信号链路重叠推送）

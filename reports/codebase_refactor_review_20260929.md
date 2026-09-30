# 梵天代码库架构审查报告（2026-09-29）

> 双视角：40年顶级合约交易员 × 资深架构师 | 只读审查，功能保持不变
> 产出方式：子代理v2审查 + 主代理独立验证回填（子代理因护栏禁写，本文件由主代理落盘）
> 证据标注：[自查]=主代理现场验证 / [子代理]=子代理取证未二次复核

## 一、架构总结

### 真实数据流（B线纸面盘）
```
43-cron(supercronic)
  → brahma_state_refresh(13,43每小时) → auto_signal_queue.json
  → paper_executor(*/15) → R闸风控 → 纸面开单
  → paper_ledger.py（SSOT账本）
  → paper_tp_monitor(*/5) → signal_settler(52 */6) → wr_feedback_engine(daily)
  → regime_config override → 仓位乘数
```

### SSOT 盘点
- **封印 SSOT 完好**：regime_config.py（ADVICE表 L26-34 / DIRECTION_GATE L57-63 / SCORE_GATE L66-83 / get_regime_mult_info L146）；brahma_core L283-360 只调用它，override 后门已封（L344 _wr_override 仅记录来源）。
- **重复状态源**：regime_state.json 被 19 个文件读、10+ 个脚本写/摸（btc_regime_watcher 强写 L177-190、regime_realtime_watcher 原子写、autopilot_l0_refresh 等）；方向映射双份（brahma_state_refresh.py:37 REGIME_DIRECTION vs regime_config.py REGIME_DIRECTION_ADVICE，语义靠人肉同步）。[子代理]
- **K线/价格拉取 10+ 处手写**：brahma_screener/bull_bear_engine/oi_advanced_scanner/spot_strategy_runner/regime_scorer… 绕过 data_cache.get_klines SSOT；paper_executor.py:34、paper_tp_monitor.py:38 各自裸 urllib 拉价。[子代理]
- **watchdog 冗余 5 套**：process_resurrect(1min) / process_guardian(10min) / watchdog_round(10min) / watchdog_l2(5min) / independent_watchdog(5min daemon)。[子代理]
- brahma_core.py 3268 行上帝模块；brahma_brain 137 个 py（top15 共 67k LOC）；scripts/ 186 项；push_hub.py 根目录 shim 20+ 调用方。trade_gateway.py:74-101 每信号 spawn python3 brahma_analyze.py 子进程（65s超时，进程级冷启动）。[子代理]

## 二、问题区域（按危害排序）

### P0-1 🔴 supercronic 双实例（每轮全部43任务双跑）
- 证据 [自查]：pgrep 实测 PID 2120149 + 2120151 同秒（13:12:52）拉起，同 brahma_crontab.txt。
- 根因 [自查]：两条启动路径竞态——start_supercronic.sh 用 PIDFILE 检查（尾段 nohup "$SCRON" "$CRONTAB" → echo $! > PIDFILE）；scripts/brahma_autostart.sh L41-44 只用 pgrep 检查后直接 nohup ./supercronic 绕过 PIDFILE。pgrep 与启动之间存在 TOCTOU 窗口，9/29 13:12 系统重启（PAUSED_20260927 解除）时两路径几乎同时触发。
- 危害：全部 cron 任务每轮双跑=重复推送+结算双写竞态（signal_settler/wr_feedback_engine 无锁，paper_executor/tp_monitor 有 paper_lock 幸免）；cron启动数 9/23=1045 → 9/28=3951（6天×3.8倍，双实例贡献~2x）。[子代理]

### P0-2 🔴 core dump 落地 4 个，清理 cron 永远够不到
- 证据 [自查]：trading-system/ 下 core.2003318 / core.2003319（06:30成对）、core.2098986（12:00）、core.2107641（12:30）；ls 显示各 696M（sparse，du 实占 111~126M，合计~475MB）。python 进程今日两轮段错误（06:30、12:00/12:30）。
- 根因 [自查]：brahma_crontab.txt:83 清理 cron glob 只扫 /tmp/core.* + /root/core.*，不含 trading-system/；core_pattern=/tmp/core.%p [子代理] 但工作目录下仍落地（子进程可能改了cwd），治本需 kernel core_pattern 指向 /tmp。

### P0-3 🟡 纸面账本闭环仍断（=已知积压 P2-1/P2-2 实锤加重）
- paper_positions.closed 2 条记录无 pnl_pct（None）→ stats win/loss 空；paper_ledger_log.jsonl 仅 5 事件；trade_ledger_v6.jsonl 0 行；wr_matrix_live.json 冻结于 09-24（total_settled=245）。[子代理]
- 进水端：live_signal_log.jsonl 1046 行，近7天 362 条，ENTER 仅 2 条（0.6%）——SCORE_GATE/RR1.5/体制过滤层层拦截，信号洪水但开单极少。[子代理]

### P1 可靠性
- regime_state.json 写者不收敛（10+ 写者）[子代理]
- K线/价格 fetch 不走 data_cache（限流+内存缓存旁路）[子代理]
- 5 套 watchdog 冗余 [子代理]
- ensure_deps.py 每15min spawn python3 -c "import mcp" 子进程，观测 42%/33% CPU 瞬时；纯文件检查即可。[子代理]
- 密钥卫生：brahma_crontab.txt 明文 SQUARE_KEY×3、OPENCLAW_API_KEY、BINANCE/DERIBIT。[子代理]

### P2 卫生
- brahma_core.py 拆层（体制链/奖励注入/门控）；paper_engine 双账本死代码（9/26已停）；docstring↔crontab 漂移（executor 注释 */40 vs 实际 */15）；陈旧 pickle（DOGE fangcang 停在 9/13）无 TTL 清理；data/ 241MB。[子代理]

## 三、性能 Ground Truth（先取数后评论）

- supercronic.log start/succeed 配对 18494 对：p50=0s / p90=2s / p99=22s / max=600s（600s=deep_post 顶满timeout）。process_resurrect.sh 单脚本 n=7490 次（每分钟，~0s）。[子代理]
- cron 启动数：9/23=1045 → 9/28=3951（6天×3.8倍）。[子代理]
- 信号：live_signal_log 1046 行；近7天 362 条；ENTER 2 条（0.6%）；BTC=149/ETH=138。[子代理]
- 纸面持仓：1 单 BTC LONG（09-29 09:08，source=p0_chain_verify）。[子代理]
- 缓存：data/ 241MB；fangcang 15m pickle BTC/ETH 各 24MB（13:13 新鲜）；DOGE pickle 停留 9/13（陈旧）。[子代理]

**交易员判断**：赚钱闭环（信号→开单→结算→WR反馈→乘数）目前断在两端——进水端信号几乎全被拦（362信号/2 ENTER），出水端 WR 矩阵冻结 9/24=系统拿旧地图打仗。性能瓶颈（p50=0s）不是主害；**正确性风险（双实例+账本断链）才是主害**。先修 P0-1，其余不阻塞赚钱验证。

## 四、重构策略（P0正确性 > P1可靠性 > P2卫生；功能不变）

| # | 项 | 改什么 | 工作量 | 风险 | 回滚 |
|---|-----|--------|--------|------|------|
| P0-1 | supercronic 单实例 | start_supercronic.sh 顶部加 flock 单例锁（exec 9>/tmp/brahma_scron.lock; flock -n 9 \|\| exit 0）；brahma_autostart.sh 直启路径改调 start_supercronic.sh；kill 多余实例 | S | 低 | kill 多余实例本身即回滚 |
| P0-2 | core dump | brahma_crontab.txt:83 glob 补 trading-system/core.*；治本：core_pattern → /tmp/core.%p（需宿主/容器权限）；删现有4个需批准 | S | 低 | 不适用（纯清理） |
| P0-3 | 账本口径 | closed 记录 backfill pnl_pct；统计改读 paper_ledger_log.jsonl（SSOT） | M | 中 | 保留旧字段兼容 |
| P1-① | regime_state 单写者 | 写者收敛到 regime_realtime_watcher | M | 中 | git revert |
| P1-② | fetch 收敛 | K线/价格全走 data_cache.get_klines | M | 中 | 逐文件回退 |
| P1-③ | watchdog 收敛 | 5→2（process_resurrect + independent_watchdog） | M | 中 | 恢复脚本即可 |
| P1-④ | ensure_deps 去子进程化 | python -c import 改纯文件存在性检查 | S | 低 | git revert |
| P1-⑤ | trade_gateway in-process | 不 spawn brahma_analyze 子进程，直接 import | M | 中 | 保留 spawn 开关 |
| P2 | 卫生 | brahma_core 拆层 / 删 paper_engine 死代码 / docstring 对齐 / 密钥出 crontab 入 .env / pickle TTL | L | 低 | 分批 |

## 五、改进代码草图（Top3，未落生产文件）

### P0-1 flock 单实例（start_supercronic.sh 顶部）
```bash
exec 9>/tmp/brahma_scron.lock
flock -n 9 || { echo "supercronic already held by another start path"; exit 0; }
```
同时 brahma_autostart.sh L41-44 改为：`bash start_supercronic.sh`（复用 PIDFILE+依赖恢复+BRAHMA_ENFORCE 导出）。

### P0-2 清理 glob 修复（brahma_crontab.txt:83 一行）
```bash
python3 -c "import os,glob; [os.remove(f) for f in glob.glob('/tmp/core.*')+glob.glob('/root/core.*')+glob.glob('/root/.openclaw/workspace/trading-system/core.*') if os.path.exists(f)]"
```
治本（宿主）：`echo '/tmp/core.%p' > /proc/sys/kernel/core_pattern`

### P0-3 账本 backfill（paper_ledger.py 尾部一次性迁移函数）
```python
def backfill_closed_pnl():
    for p in load_closed():
        if p.get("pnl_pct") is None and p.get("entry") and p.get("exit_price"):
            side = 1 if p["direction"] == "LONG" else -1
            p["pnl_pct"] = side * (p["exit_price"] / p["entry"] - 1) * p.get("leverage", 1)
            save_closed(p)
```
paper_daily_review 统计口径切换：以 paper_ledger_log.jsonl 为准。

## 六、附录证据索引

- 双实例：pgrep -af supercronic → 2120149+2120151（自查 13:38 UTC）
- core dump：ls/du trading-system/core.*（自查 13:38 UTC）
- 清理 glob：brahma_crontab.txt:83（自查）
- 启动竞态：start_supercronic.sh PIDFILE 段 + scripts/brahma_autostart.sh L41-44（自查）
- 其余数字（18494配对/分位数/信号量/持仓/缓存）：子代理 13:30 UTC 取数，未二次复核，标注[子代理]

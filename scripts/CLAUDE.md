# scripts/ 运维脚本手册
<!-- 2026-09-09 苏摩111 新建：从AGENTS.md下沉运维上下文 -->

---

## cron任务清单（62条）

### supercronic（46条，完全绕开OpenClaw）
查看 `brahma_crontab.txt` 获取完整列表。

**关键任务：**
- `brahma_analysis_runner.py` — 每30min，BTC+ETH分析
- `paper_engine.py` — 每5min，信号消费+paper order
- `auto_executor.py` — 每40min，score≥138自动执行
- `regime_realtime_watcher.py` — 每5min，体制监控
- `guardian_wrapper.sh` — 每10min，持仓守护
- `oi_watchlist_monitor.py` — 每2h，OI异动巡检
- `wr_feedback_engine.py` — 每天02:00，WR反哺
- `square_hot_poster.py` — 多时段，Square发帖

### OpenClaw cron（需AI判断的任务）
使用 `openclaw cron list` 查看。

---

## 启动顺序（服务器重启后）

```bash
# 1. supercronic（含libgomp初始化）
bash /root/.openclaw/workspace/trading-system/start_supercronic.sh

# 2. CVD采集器（setsid确保不被SIGTERM）
# supercronic @hourly 会自动拉起，无需手动

# 3. liqmap采集器
# supercronic @hourly 会自动拉起
```

---

## 数据文件地图

| 文件 | 用途 | 更新频率 |
|------|------|---------|
| `data/brahma_signals.db` | 信号SQLite库 | 每次分析 |
| `data/signal_queue.jsonl` | 待消费信号队列 | 信号源写入 |
| `data/paper_orders.json` | paper订单 | paper_engine |
| `data/signal_weights.json` | WR权重矩阵 | 每天02:00 |
| `data/brahma_state.json` | 系统状态 | 每30min |
| `data/analysis_cache_*.json` | 分析缓存 | analyze() |
| `data/cvd_realtime_*.json` | CVD实时数据 | 10s |
| `data/auto_executed_signals.json` | 已执行信号去重 | auto_executor |
| `data/oi_signal_log.jsonl` | OI信号日志 | oi_scanner |

---

## 常见修复路径

### CVD采集器挂了
```bash
# 检查数据新鲜度
python3 -c "import json,time; d=json.load(open('data/cvd_realtime_btcusdt.json')); print(f'age={round(time.time()-d.get("ts",0))}s')"
# supercronic @hourly 会自动拉起（setsid修复后）
```

### signal_queue 空壳信号
```bash
# 检查空壳率
python3 -c "import json; lines=[l for l in open('data/signal_queue.jsonl').readlines() if l.strip()]; empty=sum(1 for l in lines if 'null' in l and 'score' in l); print(f'{empty}/{len(lines)}空壳')"
```

### paper_engine 不出单
```bash
# 查日志
grep "OPEN-v7\|BLOCKED\|gate=" logs/syscron.log | tail -20
```

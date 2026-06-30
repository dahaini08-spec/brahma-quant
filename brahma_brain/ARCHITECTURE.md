# 梵天系统架构文档 v3.0
**设计院 · 2026-06-11**

## 核心原则（辩论裁定）
1. **梵天是唯一分析与执行系统** — 禁止任何绕过
2. **regime唯一来源** — market_state.analyze()为主，regime_scorer为概率补充
3. **信号生命周期完整** — 生成→expires_at→结算→退出
4. **大样本优先** — <500条不作系统决策依据

## 信号完整链路
```
brahma_analyze.py (入口)
  ↓ subprocess
brahma_core.analyze()
  ↓ market_state.analyze() → regime (权威)
  ↓ confluence_score() [3487行，待拆分]
  ↓ calc_trade_params()
  ↓ DharmaBridge.log_signal() → live_signal_log.jsonl
  
full_cycle_scanner.py [every 15m] ← 主扫描器
  ↓ trade_gateway.run()
  ↓ brahma_analyze → signal_selector
  
signal_watcher.py [every 30m] ← 监控+推送
live_signal_settler.py [every 2h] ← 结算
signal_health_check.py [every 30m] ← 自愈
```

## 体制判断（双轨已统一，2026-06-11）
```
market_state.analyze() → ms['regime']  ← brahma_core使用（主）
regime_scorer.score()  → 'regime'字段  ← 已补充，与market_state对齐
brahma_state.regime    ← 由brahma_state_refresh每5分钟写入
```

## 配置入口（单一）
```
config.py ← 唯一入口（集成system_config）
  ├── API Keys (from .env)
  ├── 蓝图参数 (from FANTAN_BLUEPRINT_V3.json)
  └── 路由+阈值 (from scripts/system_config.py)
```

## 执行引擎（待统一，Phase 3）
```
当前三套并存（历史遗留）：
  executor.py       ← 老系统主路径（仍活跃）
  hunter_executor.py ← 猎手系统
  brahma_execute.py  ← 人工确认直通道

目标：trade_gateway.run() 统一入口（下个月）
```

## 重构禁区（交易员否决权）
- ❌ 有效信号存活期禁止改confluence_score()
- ❌ BEAR_TREND体制禁止破坏性重构
- ✅ 非破坏性修复（字段补充/文档/删孤立文件）随时可做

## 待完成（Phase 2）
- [ ] brahma_core.py 拆分：calc_trade_params() → brahma_brain/params/
- [ ] ws_guardian.py 拆分：ws_core + position_monitor + execution_bridge
- [ ] 执行引擎统一：trade_gateway作为唯一入口
- [ ] 错误感知：关键路径except:pass替换（12处，分批）


## 执行层路径（已厘清，2026-06-11）

### 新系统主路径（trade_gateway → hunter_executor）
```
full_cycle_scanner.py [every 15m]
  ↓ trade_gateway.run(symbol)
    ↓ regime_scorer + brahma_analyze × 2 + signal_selector
    ↓ pre_trade_engine (五关门控)
    ↓ DharmaBridge.log_signal() → live_signal_log.jsonl
    ↓ dd1_confirm_gate → 人工CONFIRM
    ↓ hunter_executor.execute_open()
```

### 旧系统路径（executor.py，仅auto_poster.py使用）
```
auto_poster.py → executor.py.generate_strategy()
这是旧扣扳机层，Phase3合并到trade_gateway
```

### ws_guardian.py（持仓监控/TP-SL执行）
```
ws_guardian.py [常驻进程]
  ├── WebSocket价格流
  ├── position_monitor (TP1/TP2/SL检测)
  └── liq_flow (清算流s7维度)
Phase3拆分：ws_core + position_monitor + liq_flow_monitor
```

## 当前文件计数
```
brahma_brain/: 56个
scripts/:       53个
dharma/:        32个
根目录.py:       9个（brahma_analyze/config/dharma_data_bridge/
                     emergency_close/executor/treasury_gate/
                     tz_utils/ws_guardian/__init__）
```

## Phase 1C: Shadow Run方案（Phase 3 confluence拆分用）

当需要拆分confluence_score()时，不直接修改主路径，而是：

```python
# analyze()里并行运行新旧两套
old_score = confluence_score(...)  # 主路径，不动

try:
    from confluence.tech_analysis import score_tech
    from confluence.market_context import score_context
    new_score = score_tech(...) + score_context(...)
    diff = abs(new_score - old_score)
    # 记录差异到data/shadow_run_log.jsonl
    if diff > 5:
        log_shadow_divergence(symbol, old_score, new_score, diff)
except Exception:
    pass  # shadow run失败不影响主路径

return old_score  # 始终用旧路径结果
```

连续1000个信号diff < 2分 → 切换新路径（交易员零感知）

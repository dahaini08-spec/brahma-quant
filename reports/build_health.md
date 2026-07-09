# reports/build_health.md
# Brahma v6 — Build Health Report
**生成时间：** 2026-07-09 UTC  
**分支：** main  
**评估基线：** 公开 GitHub main（可验证状态）

---

## 工程封口状态

### compileall
```
python3 -m compileall brahma_v6 tests -q
# exit 0 — 无语法错误
```

### pytest 核心套件

| 测试文件 | 测试数 | 结果 |
|---------|--------|------|
| `test_order_state_matrix.py` | 97 | ✅ 全绿 |
| `test_trade_ledger.py` | 26 | ✅ 全绿 |
| `test_trade_ledger_v2.py` | 24 | ✅ 全绿 |
| `test_trade_ledger_append.py` | 8 | ✅ 全绿 |
| `test_million_run_simulator.py` | 11 | ✅ 全绿 |
| **合计** | **166** | ✅ **166/166** |

---

## 单一真相源（已封口）

### Order State Machine
- **文件：** `brahma_v6/execution/order_state.py`
- **13 态状态机：** `ALLOWED_TRANSITIONS` 为全系统唯一转移表
- **接受 `str | OrderState` 入参**，向下兼容
- `order_ticket.py` 兼容层从 `ALLOWED_TRANSITIONS` 动态派生，不维护独立副本

### TradeLedger 三道校验
- **文件：** `brahma_v6/dharma2/trade_ledger.py`
- **不重复定义 `TradeRecord`**，直接 import `brahma_v6.dharma2.models`
- `append()` 调用顺序不可绕过：
  1. `attribution.validate()` — PnL 数学守恒
  2. `_check_chain_integrity()` — 9 字段 + order_event_ids + quantity > 0
  3. `_check_duplicate()` — O(1) set 去重
  4. `_persist()` — 落盘失败 `raise LedgerWriteError`（不再 `pass`）
  5. `_records.append()` — 先落盘再写内存

---

## Phase 进度

| Phase | 状态 | 核心成果 | 测试 |
|-------|------|---------|------|
| Phase 0 | ✅ 封口 | 工程可运行性 | — |
| Phase 1 | ✅ 封口 | 13 态 Order State Machine | 97/97 |
| Phase 2 | ✅ 封口 | TradeRecord + TradeLedger 三道校验 | 58/58 |
| Phase 3 | ✅ 封口 | 10M 事件仿真验证 | 11/11 |

---

## 仿真验证摘要（Phase 3）

| 指标 | 结果 |
|------|------|
| 状态机转移总数 | 1,106,413 |
| 非法泄漏 | **0** |
| 状态机吞吐 | 982K transitions/s |
| 账本数学守恒率 | **100.00%** |
| PnL / 链路 / 重复 拒写率 | 100% |
| 账本吞吐 | 31K records/s |

---

## 综合评分（公开 main 可验证）

```
当前：7.8 / 10
条件：166/166 pytest 全绿 + compileall 通过 + 单一真相源封口
```

**下一跃迁（→ 8.3+）：** Phase 4 EV Bucket + Reality Models 落地

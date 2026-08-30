# brahma_brain/CLAUDE.md — 模块级工程规范
<!-- Boris五步闭环 · 2026-08-30 设计院自主封印 -->
<!-- 新人/新会话进入此模块，先读这个文件，再动代码 -->

---

## 🧭 核心入口链路（背熟）

```
苏摩输入「梵天分析BTC」
  ↓
brahma_full_report.run_full_analysis('BTCUSDT')   ← 唯一对外入口
  ↓
brahma_1hao_analysis.run_analysis()               ← 全量报告生成
  ↓
brahma_analysis_runner.run_analysis()             ← r对象（机器读取）
  ↓
brahma_core.analyze()                             ← 4353行，35维评分核心
  ↓
block_a(维度1-6) + block_b(维度7-10) + block_c(维度11-35)
  ↓
signal_quality_engine.evaluate()                  ← SQE 4道门控
  ↓
position_sizer.get_position_size()                ← 仓位计算
```

---

## 🚫 架构禁忌（违反即回滚）

1. **禁止在runner外新建裸HTTP分析调用** — 所有分析必须走 `run_full_analysis()`
2. **禁止新建独立分析文件** — 扩展现有文件，不新建孤岛模块
3. **禁止绕过SQE** — 信号必须经过 `signal_quality_engine.evaluate()`
4. **禁止修改regime_config.py以外的体制乘数** — SSOT唯一
5. **禁止用历史上下文价格当实时数据** — 必须调用 `brahma_bus.get_price()`

---

## ✅ 修改前必须执行

```bash
# 冒烟测试（必须全绿才算封印）
cd /root/.openclaw/workspace/trading-system
python3 brahma_brain/brahma_smoke_test_v2.py

# 语法检查
python3 -c "import ast; ast.parse(open('brahma_brain/brahma_core.py').read()); print('OK')"

# [Boris缺口2 2026-08-30] 先看最近改动，再动手——防止重复修已修过的bug
git log --oneline -20
# 关注: commit message里的「接入位置」和「根因」是最有价值的信息

# 架构守门（新增模块时验证接入位置）
grep -n "你的新模块名" brahma_brain/brahma_full_report.py brahma_brain/brahma_analysis_runner.py
```

---

## 📐 SSOT（单一真相来源）

| 内容 | 文件 | 禁止在别处修改 |
|------|------|--------------|
| 体制乘数矩阵 | `regime_config.py` | ✅ |
| 数学函数(RSI/EMA/ATR) | `math_utils.py` | ✅ |
| 信号队列写入 | `scripts/signal_queue_writer.py` | ✅ |
| 推送路由 | `scripts/system_config.py` | ✅ |
| 铁证WR规则 | `../../LESSONS.md` | 只增不减 |
| 试探性参数 | `../../PLAYBOOK.md` | 可回滚 |

---

## 🏛️ 封印文化

- **没有commit = 没有封印**
- commit message必须包含「接入位置：XXX」
- 新模块上线前必须grep验证：新模块名在runner/full_report中存在
- 冒烟测试未全绿 = 未完成

---

## 🔑 关键文件速查

| 文件 | 行数 | 职责 |
|------|------|------|
| `brahma_core.py` | 4353 | 35维评分引擎，唯一入口 |
| `brahma_full_report.py` | ~800 | 对外报告，ADAPTIVE v3.0 |
| `regime_config.py` | ~100 | 体制乘数SSOT |
| `signal_quality_engine.py` | ~200 | 4道门控 |
| `position_sizer.py` | ~500 | 仓位计算 |
| `brahma_smoke_test_v2.py` | ~300 | 冒烟测试 |
| `universal_asset_router.py` | ~380 | TradFi/加密 资产分类 |

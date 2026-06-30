# 🏯 梵天星枢引擎 · Tardis深度分析立项
**设计院 · 2026-06-09 | 版本 v1.0**

---

## 一、立项背景

梵天现有信号体系基于 REST/WS 实时数据，缺乏 L3 逐笔深度验证。
今日设计院评估发现：Tardis CSV 数据已下载（5个标的，2026-06-01），
DB 表已建（liquidations / derivative_ticks），但数据未加载（0行）。

现有资产：
- data/tardis/liq_csv/：BTC/ETH/SOL/DOGE/BNB 清算CSV（2026-06-01）
- data/tardis/tardis_cache.db：SQLite空库
- brahma_brain/tardis_liq_layer.py：清算墙分析引擎（接口已定义）
- scripts/tardis_pipeline.py：数据接入总线（未激活）

---

## 二、目标

### Phase1（本次，免费层）
用已有的免费 CSV 数据激活完整链路：
```
CSV → 加载 → SQLite → 清算墙分析 → brahma_analyze 入场区校准
```
- 激活 tardis_liq_layer.py 的 get_tardis_liq_walls()
- 在 brahma_analyze 里注入清算墙数据（新增 s20：清算墙维度）
- 验证清算位是否与历史信号入场区吻合

### Phase2（付费层，100条Paper后评估是否投入）
- Tardis API Key：解锁全量历史数据
- aggTrades / L3 orderbook 接入
- CVD（Cumulative Volume Delta）实时计算
- 影子验证（Paper信号 vs Tardis回放）

---

## 三、技术设计

```
数据流：
  Tardis CSV（已有）
      ↓
  tardis_pipeline.py --load
      ↓ INSERT INTO tardis_cache.db
  tardis_liq_layer.get_tardis_liq_walls(symbol)
      ↓ 返回清算墙快照
  brahma_brain/brahma_brain.py s20 引擎
      ↓ 清算墙评分注入（±10分）
  brahma_analyze.py 最终输出
```

**s20 评分逻辑（设计草案）：**
- 做空信号：入场区上方存在大空头清算墙（触发后反弹终止）→ +8分
- 做空信号：入场区下方存在大多头清算墙（踩踏加速）→ +5分
- 做空信号：无清算墙支撑 → 0分
- 做空信号：清算墙与信号方向矛盾 → -5分

---

## 四、执行计划

| 阶段 | 任务 | 预估 |
|------|------|------|
| P0 | 激活 tardis_pipeline.py --load，把CSV加载进SQLite | 1小时 |
| P0 | 验证 tardis_liq_layer.get_tardis_liq_walls() 返回正确数据 | 30分钟 |
| P1 | 新建 brahma_brain/tardis_engine.py（s20维度）| 2小时 |
| P1 | brahma_brain.py 注入 s20 | 30分钟 |
| P1 | 回归测试 + 评分对比（有/无Tardis）| 30分钟 |
| P2 | 付费层API Key评估（需100条Paper数据支撑）| TBD |

---

## 五、成功标准

- [ ] tardis_cache.db liquidations 表有数据
- [ ] get_tardis_liq_walls('BTCUSDT') 返回 available=True
- [ ] ETH SHORT 信号评分注入 s20 后变化可见
- [ ] 回归测试 ≥12/14 PASS
- [ ] 武曲Paper WR 在新维度下不低于历史82.5%

---

## 六、风险

- CSV 只有2026-06-01单日数据，样本偏少 → 仅作结构验证，不调整权重
- Tardis免费层限制：每月1日 → 需要付费Key才能有连续数据
- s20 权重需要至少50条Paper验证才能正式投入

---

*立项时间：2026-06-09*
*负责人：梵天设计院*
*前置条件：武曲Paper重置完成（今日已执行）*

# 梵天 v5.1 固化封印文档
<!-- 苏摩111封印 2026-07-19 04:23 UTC -->

## 核心框架：月之暗面五方案 × 梵天落地

### 永久文件路径（不得删除/重命名）
- `scripts/zero_cost_prescorer.py`   ← MuonClip类比，前置筛选层
- `scripts/regime_memory_7d.py`      ← KDA类比，中期体制记忆层
- `scripts/signal_history_scorer.py` ← Attention Residue类比，历史胜率引用层
- `data/regime_memory_7d.json`       ← 中期记忆数据（勿清空）

### brahma_engine.py 注入点（L3387~L3413）
```python
# ══ [v5.1 梵天历史引用层 + 中期记忆层] ══
# try/except完全隔离，主流程零风险
# 每次analyze()调用后自动执行
# 字段：v51_regime_adj / v51_history_adj / v51_reason
```

### 运行逻辑
1. pre_score < 50 → 跳过35维矩阵（zero_cost_prescorer）
2. 35维矩阵运行后 → regime_memory_7d 加减分（±5~12）
3. 35维矩阵运行后 → signal_history_scorer 加减分（±15最大）
4. 最终：score_final = raw_score + regime_adj + history_adj

### 当前状态（封印时）
- v51_regime_adj = +5.0（体制稳定7天0次切换）
- v51_history_adj = 0.0（样本不足，30天后生效）
- commit: c20f788

### 成熟时间线
- 7天后：regime_memory_7d体制切换记录完整
- 30天后：signal_history_scorer样本≥3条，历史胜率引用层生效
- 90天后：全量历史，梵天进化为「有记忆的系统」

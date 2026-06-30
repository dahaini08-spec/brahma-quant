# arch/evolution/ 进化日志规范

## 目录说明
每次系统进化（维度新增/参数调整/验证通过）在此目录记录一个 JSON 文件。

## 文件命名
`v{版本号}_{简短描述}.json`  例：`v22.0_gex_validation.json`

## JSON字段规范
```json
{
  "version":       "v22.0",
  "ts":            "ISO8601时间戳",
  "type":          "feature | param_tune | hotfix | rollback",
  "summary":       "一句话描述",
  "changes":       ["改动列表"],
  "metrics_before": {"key": val},
  "metrics_after":  {"key": val},
  "wuqu_wr_before": 0.825,
  "wuqu_wr_after":  null,
  "dsr":            0.95,
  "cpcv_oos_wr":    0.82,
  "commit":         "git hash",
  "approved_by":    "设计院 | 自动",
  "veto":           false
}
```

## 进化决策流程
1. 新逻辑提案
2. CPCV（15条路径）→ OOS WR
3. DSR.compare(sr_old, sr_new, n_trials=15)
4. Bootstrap MC → 资金曲线不恶化
5. 全部通过 → 写 JSON 记录 → 合并进化

## 里程碑触发
- 武曲Paper 50条：小进化（参数微调）
- 武曲Paper 100条：中进化（Meta-Labeler训练）
- 武曲Paper 200条：大进化（达摩院正式训练）

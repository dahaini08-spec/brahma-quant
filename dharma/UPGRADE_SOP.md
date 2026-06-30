# 梵天升级SOP手册 · 设计院标准
**版本 v1.0 | 2026-05-22**

---

## 核心原则

> **升级 = 代码修改 + 登记 + 验证 + 同步，缺一不可。**
> 只改代码不登记 = 升级不存在。

---

## 四层闭环架构

```
L1 升级时    → register_upgrade()    登记+评分
L2 commit后  → post-commit hook      自动同步Blueprint
L3 每6H CI  → dharma-ci              verify验证落地
L4 分析报告  → 引用 deep_eval_scores  实时评分
```

---

## 升级SOP（每次必须执行）

### 第一步：写代码
正常修改 .py 文件

### 第二步：登记升级（必须）
```bash
python3 dharma/upgrade_registry.py \
  --register "升级名称（简短清晰）" \
  --deltas signal_quality:+N execution:+N \
  --files 修改的文件.py \
  --notes "具体描述：做了什么，为什么，预期效果"
```

评分维度说明：
| 维度 | 何时加分 |
|------|---------|
| signal_quality | 信号逻辑、过滤器、达摩院验证 |
| execution | SL/TP/Kelly/下单逻辑 |
| perception | 数据源、指标计算、体制判断 |
| intelligence | ML模型、鲸鱼智能、多维融合 |
| stability | 容错、测试、成熟度、监控 |

### 第三步：git commit
```bash
git add -A && git commit -m "描述"
# post-commit hook 自动触发 score_sync
```

### 第四步：验证确认
```bash
python3 dharma/upgrade_registry.py --verify
python3 dharma/upgrade_registry.py --status
```

---

## 评分加分参考标准

| 升级类型 | 建议加分 |
|---------|---------|
| 关键 bug 修复（P0级）| +3~+8 |
| 核心算法优化（P1级）| +5~+10 |
| 新功能/新维度激活 | +4~+8 |
| 测试/成熟度提升 | +2~+5 |
| 达摩院节点验证 | +3~+6 |
| 文档/配置修复 | +1~+3 |

**原则：诚实评分，不虚报。每次+N 需有具体理由。**

---

## 常用命令

```bash
# 查看当前状态
python3 dharma/upgrade_registry.py --status

# 同步评分到Blueprint（手动触发）
python3 dharma/upgrade_registry.py --sync

# 验证历史升级是否真正落地
python3 dharma/upgrade_registry.py --verify

# 预览评分变化（不写入）
python3 dharma/upgrade_registry.py --sync --dry-run
```

---

## 何时评分会上涨

| 事件 | 自动/手动 |
|------|---------|
| 调用 register_upgrade() | 自动 |
| git commit（有核心文件变更）| 自动（hook） |
| 达摩院 CI 验证通过 | 手动登记 |
| 实盘50笔ML激活 | 手动登记 |

---

## 注意事项

1. **不要修改 UPGRADE_REGISTRY.json 的 current_scores** — 由程序维护
2. **每次会话开始先看 --status** — 了解系统当前真实水平
3. **评分不是目的** — 只是帮助追踪升级是否真正落地的工具
4. **执行层 35 低是正常的** — 需要实盘50笔后ML激活，时间换空间

---

## 文件位置

- 注册表：`dharma/UPGRADE_REGISTRY.json`
- 脚本：`dharma/upgrade_registry.py`
- git hook：`.git/hooks/post-commit`
- Blueprint：`FANTAN_BLUEPRINT_V3.json` → `deep_eval_scores`

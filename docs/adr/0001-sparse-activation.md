# ADR-0001: 稀疏激活 (Sparse Activation)

**Date**: 2026-09-13  
**Status**: Sealed (苏摩111)  
**Phase**: 0

## Context

梵天94维分析体系对所有维度全量计算，但回测发现：
- 不同体制下大部分维度是噪声（WR≈50%）
- CHOP_MID下s1/s2/s7/s10的IC接近0
- 全量计算浪费算力+引入噪声

## Decision

按体制选择性激活维度：
- `scoring_config.json` 定义6体制×2方向的active/sleep维度
- sleep维度归零（不计算不评分）
- active维度按权重加权

## Consequences

- ✅ 算力节省40%（sleep维度跳过）
- ✅ 噪声降低（无效维度不参与评分）
- ⚠️ 需要定期更新config（WR矩阵变化时）
- ⚠️ unset维度保持原样（未在config中声明的维度透传）

## Sources

- `data/scoring_config.json` — 稀疏激活配置
- `wr_matrix_v8_6y5.json` 47K条回测数据
- `sim_sparse_loo.py` LOO验证

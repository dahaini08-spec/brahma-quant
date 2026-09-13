# ADR-0002: DAG执行器 (DAG Executor)

**Date**: 2026-09-13  
**Status**: Sealed (苏摩111)  
**Phase**: 1

## Context

ADR-0001定义了稀疏激活配置，但实际执行还是走brahma_core.py硬编码逻辑，配置和执行脱节。

## Decision

创建轻量DAG执行器（<200行），在confluence_score的Block C之后接入：
1. 读取scoring_config做稀疏过滤
2. sleep维度归零 / active维度按权重加权
3. 重算总分 / 透传position_mult
4. 配置热更新（mtime检测，无需重启）

## Consequences

- ✅ 配置即执行（改config立即生效）
- ✅ 热更新（不需要重启服务）
- ✅ DAG后处理不改Block A/B/C内部逻辑
- ⚠️ 'all'关键字=全部sleep（BEAR_RECOVERY:SHORT使用）
- ⚠️ 未在config中声明的维度保持原样

## Sources

- `brahma_brain/dag_executor.py` — 193行DAG执行器
- `brahma_core.py` Block C后接入点

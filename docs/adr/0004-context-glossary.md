# ADR-0004: 共享术语表 + user/model分离

**Date**: 2026-09-13  
**Status**: Sealed (苏摩111)  
**Phase**: 3

## Context

1. 梵天94维分析中s1-s22、FVG、OB等术语没有统一定义文件，每次新AI代理接入要从头学
2. 所有模块都是model-invoked，没有"只能人类触发"的保护，AI可能在不该行动时自动行动
3. 架构决策记录混在MEMORY.md封印段落里，很难追溯

## Decision

1. 创建 `brahma_brain/CONTEXT.md` 定义所有领域术语（评分维度/SMC/体制/量化/果蝇架构/交易）
2. 在 `module_registry.json` 中添加 `invocation` 字段：
   - `user-invoked`: 执行交易/下单/发送推送 → 只能苏摩111触发
   - `model-invoked`: 分析/评分/健康检查 → AI可自动调用
3. 创建 `docs/adr/` 目录，每个重大架构决策一个ADR文件

## Consequences

- ✅ MCP外部代理读CONTEXT.md就能理解全部术语
- ✅ 交易执行模块有user-invoked保护
- ✅ 架构决策可追溯，不混在交易参数中
- ⚠️ 需要维护CONTEXT.md（新维度加入时更新）
- ⚠️ user-invoked模块列表需要人工维护

## Sources

- `brahma_brain/CONTEXT.md` — 领域术语表
- `data/module_registry.json` invocation字段
- `docs/adr/` — ADR目录

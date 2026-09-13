# ADR-0003: MCP暴露 (MCP Exposure)

**Date**: 2026-09-13  
**Status**: Sealed (苏摩111)  
**Phase**: 2

## Context

梵天分析只能通过内部脚本调用，外部AI代理无法接入。缺少标准化的工具接口。

## Decision

创建MCP服务器，把梵天分析封装为4个MCP工具，通过stdio协议暴露：
1. `brahma_analyze` — 全链路分析（94维+VIP卡片）
2. `brahma_score` — 轻量评分（~2s）
3. `brahma_config` — DAG稀疏激活配置
4. `brahma_health` — 27模块健康检查

## Consequences

- ✅ 任何支持MCP的客户端都能调用（Claude Desktop/Cursor/自定义脚本）
- ✅ stdio协议=本地调用，零网络开销
- ✅ 工具接口标准化，不需要自己写API
- ⚠️ brahma_analyze约90秒（需要拉实时数据）
- ⚠️ 依赖mcp 2.2.0+

## Sources

- `brahma_brain/brahma_mcp_server.py` — 152行MCP服务器
- `mcp` Python库 2.2.0

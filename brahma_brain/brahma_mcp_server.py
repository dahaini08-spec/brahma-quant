#!/usr/bin/env python3
"""
brahma_mcp_server.py — 梵天MCP服务器
[果蝇架构Phase 2] Langflow借鉴5：MCP暴露

把run_full_analysis封装为MCP工具，供外部AI代理调用。

启动：
    python3 brahma_brain/brahma_mcp_server.py

工具列表：
    - brahma_analyze: 运行梵天全链路分析（94维+VIP卡片）
    - brahma_score: 轻量评分（只返回confluence_score，~2s）
    - brahma_config: 查看DAG稀疏激活配置
    - brahma_health: 模块健康检查
"""
import sys
import os
import json
import asyncio

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, '..')
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

sys.dont_write_bytecode = True

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

server = Server("brahma-mcp")

# ── 工具定义 ──────────────────────────────────────────────────

_TOOLS = [
    types.Tool(
        name="brahma_analyze",
        description="运行梵天全链路分析（94维评分+SMC/FVG/OB+清算+OI+宏观+VIP卡片）。返回完整分析报告。",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "交易标的，如 BTCUSDT, ETHUSDT", "default": "BTCUSDT"},
                "mode": {"type": "string", "enum": ["auto", "hf", "spot", "dual"], "description": "分析模式", "default": "auto"}
            },
            "required": ["symbol"]
        }
    ),
    types.Tool(
        name="brahma_score",
        description="轻量评分：只返回confluence_score和体制，不跑完整报告。速度快(~2s)。",
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "交易标的", "default": "BTCUSDT"}},
            "required": ["symbol"]
        }
    ),
    types.Tool(
        name="brahma_config",
        description="查看DAG稀疏激活配置（scoring_config.json）",
        inputSchema={
            "type": "object",
            "properties": {"regime": {"type": "string", "description": "体制名（可选），不填则列出所有"}}
        }
    ),
    types.Tool(
        name="brahma_health",
        description="模块健康检查：27个模块存活+端口连通性",
        inputSchema={"type": "object", "properties": {}}
    ),
]

# ── 工具实现 ──────────────────────────────────────────────────

async def _on_list_tools(ctx, params):
    return types.ListToolsResult(tools=_TOOLS)

async def _on_call_tool(ctx, params):
    name = params.name
    args = params.arguments or {}
    try:
        if name == "brahma_analyze":
            text = await _tool_analyze(args)
        elif name == "brahma_score":
            text = await _tool_score(args)
        elif name == "brahma_config":
            text = await _tool_config(args)
        elif name == "brahma_health":
            text = await _tool_health(args)
        else:
            text = f"未知工具: {name}"
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
    except Exception as e:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"工具执行失败: {e}")], isError=True)

async def _tool_analyze(args: dict) -> str:
    symbol = args.get("symbol", "BTCUSDT").upper()
    mode = args.get("mode", "auto")
    from brahma_brain.brahma_full_report import run_full_analysis
    report, r = run_full_analysis(symbol, mode)
    summary = {
        "symbol": symbol, "score": r.get("score", r.get("total", 0)),
        "regime": r.get("regime", ""), "action": r.get("action", ""),
    }
    return f"## 梵天分析 {symbol}\n```json\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n```\n\n{report}"

async def _tool_score(args: dict) -> str:
    symbol = args.get("symbol", "BTCUSDT").upper()
    from brahma_brain.brahma_core import analyze
    result = analyze(symbol)
    summary = {
        "symbol": symbol, "score": result.get("score", 0),
        "regime": result.get("regime", ""), "action": result.get("action", ""),
        "grade": result.get("grade", ""),
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)

async def _tool_config(args: dict) -> str:
    regime = args.get("regime", "")
    from brahma_brain.dag_executor import _load_config
    cfg = _load_config()
    if regime:
        data = cfg.get(regime.upper(), {})
        return json.dumps(data, ensure_ascii=False, indent=2)
    lines = []
    for r in sorted(k for k in cfg if not k.startswith('_')):
        for d in ('LONG', 'SHORT'):
            c = cfg[r].get(d, {})
            lines.append(f"{r:16s} {d:5s} active={len(c.get('active_dims',[]))} sleep={len(c.get('sleep_dims',[]))} mult={c.get('position_mult',1.0)}")
    return '\n'.join(lines)

async def _tool_health(args: dict) -> str:
    from scripts.module_check import check_modules
    alive, missing, port_issues, alerts = check_modules()
    result = {"alive": len(alive), "missing": len(missing), "port_issues": len(port_issues), "alerts": alerts or "全部正常"}
    return json.dumps(result, ensure_ascii=False, indent=2)

# ── 注册+启动 ─────────────────────────────────────────────────

server.add_request_handler("tools/list", types.PaginatedRequestParams, _on_list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, _on_call_tool)

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
brahma_binance_mcp.py — 梵天 Binance MCP Server v1.0
[设计院封印 2026-08-13 苏摩111]

功能：
  把 brahma_bus 的数据能力包装为标准 MCP Server
  供未来 financial-services 架构的多Agent编排层调用

参考: anthropics/financial-services orchestrate.py 架构
     MCP Server 暴露为 http://localhost:9001/mcp (HTTP SSE)

工具列表：
  - get_klines(symbol, interval, limit) → K线数据
  - get_price(symbol) → 实时价格
  - get_funding_rate(symbol) → 资金费率
  - get_open_interest(symbol) → OI
  - get_orderbook(symbol, depth) → 订单簿
  - get_regime(symbol) → 当前体制（BULL/BEAR/CHOP）
  - get_signal_pool(limit) → 最新信号池
  - get_positions() → 当前持仓

状态：SCAFFOLD（骨架版，待mcp库安装后激活）
激活方式：pip install mcp && python3 brahma_brain/brahma_binance_mcp.py --port 9001
"""
import sys
import os
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── MCP Server 定义（需要 mcp 库）────────────────────────────────
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("[WARN] mcp库未安装，运行 pip install mcp 激活MCP Server")
    print("[INFO] 骨架已就绪，数据接口可独立测试")

# ── brahma_bus 数据层 ─────────────────────────────────────────────
try:
    from brahma_bus import get_klines, get_price, get_funding, get_oi
    BUS_AVAILABLE = True
except ImportError:
    BUS_AVAILABLE = False
    print("[WARN] brahma_bus不可用，使用mock数据")

    def get_klines(symbol, interval='1h', limit=100): return []
    def get_price(symbol): return 0.0
    def get_funding(symbol): return 0.0
    def get_oi(symbol): return 0.0


def _get_regime(symbol: str) -> dict:
    """读取梵天体制状态"""
    try:
        state_file = BASE / 'data' / 'brahma_state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            sym_state = state.get(symbol, state.get('BTC', {}))
            return {
                'symbol': symbol,
                'regime': sym_state.get('regime', 'UNKNOWN'),
                'score':  sym_state.get('score', 0),
                'age_min': sym_state.get('age_min', 0),
            }
    except Exception:
        pass
    return {'symbol': symbol, 'regime': 'UNKNOWN', 'score': 0}


def _get_signal_pool(limit: int = 10) -> list:
    """读取最新信号池"""
    try:
        sig_file = BASE / 'data' / 'live_signal_log.jsonl'
        if sig_file.exists():
            lines = sig_file.read_text().strip().splitlines()
            records = []
            for line in lines[-limit:]:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
            return records
    except Exception:
        pass
    return []


def _get_positions() -> dict:
    """读取当前持仓"""
    try:
        pos_file = BASE / 'data' / 'position_sl_state.json'
        if pos_file.exists():
            return json.loads(pos_file.read_text())
    except Exception:
        pass
    return {}


# ── 独立测试模式（无需mcp库）─────────────────────────────────────
def run_standalone_test():
    """验证所有数据接口可用性"""
    print("\n[梵天MCP骨架] 数据接口自检:")
    print(f"  brahma_bus: {'✅' if BUS_AVAILABLE else '❌ 未安装'}")

    test_symbol = 'BTCUSDT'
    try:
        price = get_price(test_symbol)
        print(f"  get_price({test_symbol}): {price}")
    except Exception as e:
        print(f"  get_price: ❌ {e}")

    try:
        regime = _get_regime(test_symbol)
        print(f"  get_regime({test_symbol}): {regime.get('regime','?')} score={regime.get('score',0)}")
    except Exception as e:
        print(f"  get_regime: ❌ {e}")

    try:
        pool = _get_signal_pool(3)
        print(f"  get_signal_pool: {len(pool)} 条")
    except Exception as e:
        print(f"  get_signal_pool: ❌ {e}")

    try:
        positions = _get_positions()
        print(f"  get_positions: {len(positions)} 个持仓")
    except Exception as e:
        print(f"  get_positions: ❌ {e}")

    print(f"\n[MCP Server] 激活方式:")
    print(f"  pip install mcp")
    print(f"  python3 brahma_brain/brahma_binance_mcp.py --serve --port 9001")
    print(f"  → 暴露为 http://localhost:9001/mcp")
    print(f"\n[agent.yaml] MCP配置:")
    print(f"  mcp_servers:")
    print(f"    - type: url")
    print(f"      name: brahma-data")
    print(f"      url: http://localhost:9001/mcp")


# ── MCP Server 模式 ───────────────────────────────────────────────
if MCP_AVAILABLE:
    app = Server("brahma-binance-mcp")

    @app.list_tools()
    async def list_tools():
        return [
            types.Tool(name="get_price",       description="获取实时价格",     inputSchema={"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}),
            types.Tool(name="get_klines",       description="获取K线数据",      inputSchema={"type":"object","properties":{"symbol":{"type":"string"},"interval":{"type":"string","default":"1h"},"limit":{"type":"integer","default":100}},"required":["symbol"]}),
            types.Tool(name="get_funding_rate", description="获取资金费率",     inputSchema={"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}),
            types.Tool(name="get_open_interest",description="获取OI",          inputSchema={"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}),
            types.Tool(name="get_regime",       description="获取梵天体制状态", inputSchema={"type":"object","properties":{"symbol":{"type":"string"}},"required":["symbol"]}),
            types.Tool(name="get_signal_pool",  description="获取最新信号池",   inputSchema={"type":"object","properties":{"limit":{"type":"integer","default":10}}}),
            types.Tool(name="get_positions",    description="获取当前持仓",     inputSchema={"type":"object","properties":{}}),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "get_price":
            result = get_price(arguments["symbol"])
        elif name == "get_klines":
            result = get_klines(arguments["symbol"], arguments.get("interval","1h"), arguments.get("limit",100))
        elif name == "get_funding_rate":
            result = get_funding(arguments["symbol"])
        elif name == "get_open_interest":
            result = get_oi(arguments["symbol"])
        elif name == "get_regime":
            result = _get_regime(arguments["symbol"])
        elif name == "get_signal_pool":
            result = _get_signal_pool(arguments.get("limit", 10))
        elif name == "get_positions":
            result = _get_positions()
        else:
            result = {"error": f"未知工具: {name}"}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true', help='启动MCP Server')
    parser.add_argument('--port', type=int, default=9001)
    args = parser.parse_args()

    if args.serve and MCP_AVAILABLE:
        import asyncio
        asyncio.run(stdio_server(app))
    else:
        run_standalone_test()

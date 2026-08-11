#!/usr/bin/env python3
"""
Brahma-Quant Quick Start
========================
用法:
    python examples/quick_start.py --symbol BTCUSDT
    python examples/quick_start.py --symbol ETHUSDT --full
    BRAHMA_SKIP_COUNCIL=1 python examples/quick_start.py --symbol BTCUSDT

说明:
    - 仅运行信号分析（不下真单）
    - 需要 .env 配置 BINANCE_API_KEY / BINANCE_SECRET
    - 设置 BRAHMA_SKIP_COUNCIL=1 可跳过 LLM 调用（离线也可跑）
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brahma_brain'))

def main():
    parser = argparse.ArgumentParser(description='Brahma-Quant 信号分析快速入口')
    parser.add_argument('--symbol',  default='BTCUSDT', help='交易对，如 BTCUSDT / ETHUSDT')
    parser.add_argument('--full',    action='store_true', help='输出完整分析报告（默认卡片格式）')
    parser.add_argument('--validate',action='store_true', help='仅验证模块可导入，不请求行情')
    args = parser.parse_args()

    # ── 依赖检查 ─────────────────────────────────────────────────────
    if args.validate:
        print('[quick_start] 验证模式：检查核心模块可导入性')
        modules = [
            'brahma_brain.brahma_analysis_runner',
            'brahma_brain.brahma_bus',
            'brahma_brain.brahma_health',
            'brahma_brain.market_state',
            'brahma_brain.universal_asset_router',
        ]
        ok = 0
        for m in modules:
            try:
                __import__(m)
                print(f'  ✅ {m}')
                ok += 1
            except Exception as e:
                print(f'  ❌ {m}: {e}')
        print(f'\n[quick_start] {ok}/{len(modules)} 模块可导入')
        sys.exit(0 if ok == len(modules) else 1)

    # ── 信号分析 ─────────────────────────────────────────────────────
    print(f'[quick_start] 分析 {args.symbol}，模式={"full" if args.full else "card"}')
    print('[quick_start] 提示：设置 BRAHMA_SKIP_COUNCIL=1 可跳过 LLM 调用')
    print()
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis
        mode = 'full' if args.full else 'card'
        result = run_analysis(args.symbol, output_format=mode)
        if isinstance(result, dict):
            score = result.get('score_final', result.get('score', 'N/A'))
            regime = result.get('regime', 'N/A')
            direction = result.get('signal_dir', result.get('direction', 'N/A'))
            valid = result.get('valid', False)
            print(f'  标的:    {args.symbol}')
            print(f'  体制:    {regime}')
            print(f'  方向:    {direction}')
            print(f'  评分:    {score}')
            print(f'  有效信号: {"✅" if valid else "❌"}')
        else:
            print(result)
    except ImportError as e:
        print(f'[quick_start] ❌ 模块导入失败: {e}')
        print('[quick_start] 请先安装依赖: pip install -e ".[dev,research]"')
        sys.exit(1)
    except Exception as e:
        print(f'[quick_start] ❌ 分析失败: {e}')
        print('[quick_start] 确认 .env 包含 BINANCE_API_KEY / BINANCE_SECRET')
        sys.exit(1)

if __name__ == '__main__':
    main()

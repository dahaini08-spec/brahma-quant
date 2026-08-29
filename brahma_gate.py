#!/usr/bin/env python3
"""
brahma_gate.py — 梵天分析唯一入口守门人
2026-08-29 苏摩111封印

用法：
  python3 brahma_gate.py BTCUSDT
  python3 brahma_gate.py ETHUSDT
  python3 brahma_gate.py BTCUSDT ETHUSDT  # 双标的

任何行情分析请求，必须且只能通过此文件。
禁止绕过此文件直接调用requests/API。
"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def analyze(symbol: str):
    from brahma_brain.brahma_full_report import run_full_analysis
    print(f'\n{"="*60}')
    print(f'梵天全能力分析 | {symbol} | {time.strftime("%Y-%m-%d %H:%M:%S CST", time.gmtime(time.time()+8*3600))}')
    print(f'{"="*60}\n')
    try:
        report, r = run_full_analysis(symbol)
        print(report)
        return r
    except Exception as e:
        print(f'[ERROR] {symbol} 分析失败: {e}')
        import traceback; traceback.print_exc()
        return None

if __name__ == '__main__':
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in symbols:
        analyze(sym.upper())

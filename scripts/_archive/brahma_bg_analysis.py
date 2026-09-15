#!/usr/bin/env python3
"""
brahma_bg_analysis.py — 后台分析运行器
用法: python3 scripts/brahma_bg_analysis.py BTCUSDT
结果写入 data/analysis_cache_<symbol>.json
"""
import sys, json, time
from pathlib import Path

def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    out_path = Path(__file__).parent.parent / 'data' / f'analysis_cache_{symbol}.json'
    
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    ts_start = time.time()
    try:
        from brahma_brain.brahma_full_report import run_full_analysis
        report, r = run_full_analysis(symbol)
        
        # 写结果
        cache = {
            'symbol': symbol,
            'ts': ts_start,
            'ts_done': time.time(),
            'elapsed': round(time.time() - ts_start, 1),
            'regime': r.get('regime'),
            'score_final': r.get('score_final'),
            'report_lines': len(report.split('\n')),
            'report': report,
            'r_keys': list(r.keys()),
            'status': 'ok'
        }
        out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        print(f'✅ {symbol} 分析完成 | regime={cache["regime"]} score={cache["score_final"]} 耗时{cache["elapsed"]}s')
        print(f'结果写入: {out_path}')
    except Exception as e:
        cache = {'symbol': symbol, 'ts': ts_start, 'status': 'error', 'error': str(e)}
        out_path.write_text(json.dumps(cache, ensure_ascii=False))
        print(f'❌ {symbol} 分析失败: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()

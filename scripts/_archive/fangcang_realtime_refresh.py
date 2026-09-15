#!/usr/bin/env python3
"""
fangcang_realtime_refresh.py — 刷新 fangcang_dharma_realtime.json
每4小时由 wrapper_4h.sh 调用
2026-08-29 苏摩111封印

功能：
  1. 扫描 data/fangcang_cases_*.json 所有标的的最新方仓状态
  2. 更新 data/fangcang_dharma_realtime.json
  3. 标记当前 BBW 压缩中的标的
"""
import sys, json, os, time, glob
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

DIR = Path(__file__).parent.parent
DATA_DIR = DIR / 'data'
OUTPUT_FILE = DATA_DIR / 'fangcang_dharma_realtime.json'

def refresh():
    now = time.time()
    new_cases = []
    updated_syms = 0

    # 扫描所有标的的方仓案例文件
    case_files = list(DATA_DIR.glob('fangcang_cases_*.json'))
    tradfi_file = DATA_DIR / 'fangcang_cases_tradfi.json'

    all_files = case_files
    if tradfi_file.exists():
        all_files.append(tradfi_file)

    for f in all_files:
        try:
            data = json.load(open(f))
            cases = data if isinstance(data, list) else data.get('cases', [])
            if not cases:
                continue

            # 只取最近30天的案例作为实时参考
            cutoff = now - 30 * 86400
            recent = [c for c in cases if float(c.get('ts_burst') or c.get('ts') or 0) > cutoff]
            if not recent:
                recent = cases[-5:]  # 至少取最后5条

            # 取最新一条作为快照
            latest = sorted(recent, key=lambda x: float(x.get('ts_burst') or x.get('ts') or 0), reverse=True)[0]
            sym = latest.get('symbol', f.stem.replace('fangcang_cases_','').upper() + 'USDT')

            snapshot = {
                'symbol': sym,
                'timeframe': latest.get('timeframe', '4h'),
                'ts_burst': latest.get('ts_burst') or latest.get('ts') or now,
                'direction': latest.get('breakout_direction') or latest.get('direction', '?'),
                'min_bb_width': latest.get('compress_bbw_min') or latest.get('min_bb_width') or 0,
                'squeeze_bars': latest.get('compress_bars') or latest.get('squeeze_bars') or 0,
                'rsi_at_burst': latest.get('rsi_at_end') or latest.get('rsi_at_burst') or 50,
                'is_genuine_breakout': latest.get('is_genuine_breakout', False),
                'brahma_regime': latest.get('regime_guess') or latest.get('brahma_regime') or '?',
                'score': latest.get('score') or 0,
                'sl_pct': latest.get('sl_pct') or 2.0,
                'rr': latest.get('rr') or 1.0,
                '_source': 'fangcang_realtime_refresh',
                '_refreshed_at': now,
                '_n_cases_total': len(cases),
            }
            new_cases.append(snapshot)
            updated_syms += 1
        except Exception as e:
            pass

    # 写入输出文件
    output = {
        'refreshed_at': datetime.utcnow().isoformat(),
        'total_syms': updated_syms,
        'cases': new_cases
    }
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'方仓实时库刷新完成: {updated_syms}个标的 → {OUTPUT_FILE}')
    return updated_syms

if __name__ == '__main__':
    refresh()

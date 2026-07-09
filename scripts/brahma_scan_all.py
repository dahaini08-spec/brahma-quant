#!/usr/bin/env python3
"""
brahma_scan_all.py — 梵天全量标的批量扫描器 v2.0
设计院 · 2026-06-12 | 升级 2026-06-19 (动态候选池)

功能：对标的列表逐一调用 trade_gateway，汇总新信号写入 multi_scan_log。
原则：扫描不封禁，结果交给 grade/score 门控自然过滤。
模式：
  默认 = FAST_SYMBOLS 8个固定标的
  --full = 全部18个标的
  --candidates = 读取 data/scan_candidates.json 动态候选（market_screener输出）
"""
import sys, os, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
os.chdir(str(BASE))
for p in [str(BASE), str(BASE/'scripts'), str(BASE/'brahma_brain')]:
    if p not in sys.path: sys.path.insert(0, p)

# ── 全量标的列表（来自 executor.py TIER_PARAMS）
ALL_SYMBOLS = [
    # flagship
    'BTCUSDT', 'ETHUSDT', 'DOGEUSDT',
    # mid
    'BNBUSDT', 'HYPEUSDT', 'NEARUSDT', 'TRUMPUSDT',
    '1000PEPEUSDT', 'TONUSDT', 'SNDKUSDT', 'CRCLUSDT',
    # small
    'WIFUSDT', 'LDOUSDT', 'JUPUSDT', 'RENDERUSDT',
    'AIOTUSDT', 'LAYERUSDT', 'POLUSDT',
]

# 轻量级：仅扫 flagship + mid（5个高流动性）满足信号需求
# 完整模式用 --full 参数
FAST_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'DOGEUSDT', 'NEARUSDT',
                'HYPEUSDT', 'TRUMPUSDT', '1000PEPEUSDT']

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--full',       action='store_true', help='扫描全部18个标的')
parser.add_argument('--candidates', action='store_true', help='读取market_screener动态候选')
parser.add_argument('--sector',     action='store_true', help='合并candidates+sector_candidates板块联动候选')
parser.add_argument('--dry',        action='store_true', help='只分析不推送')
args = parser.parse_args()

if args.sector:
    # [v2.0 板块联动模式] 合并 scan_candidates + sector_candidates
    # 设计院 2026-06-29 | 零AI额外消耗，自动发现暴涨板块低位标的
    symbols_set = set()
    source_info = []

    # 1. 加载market_screener候选
    cand_path = BASE / 'data' / 'scan_candidates.json'
    try:
        cand_data = json.loads(cand_path.read_text())
        cand_syms = [r['symbol'] for r in cand_data.get('candidates', [])]
        for s in cand_syms:
            symbols_set.add(s)
        source_info.append(f'screener({len(cand_syms)}个/{cand_data.get("generated","?")})')
        pass  # [静默]
    except Exception as e:
        pass  # [静默]

    # 2. 加载sector_candidates板块联动候选
    sector_path = BASE / 'data' / 'sector_candidates.json'
    sector_new = 0
    if sector_path.exists():
        try:
            sec_data = json.loads(sector_path.read_text())
            sec_syms = [r['symbol'] for r in sec_data.get('candidates', [])]
            # 板块联动候选最多取TOP6（避免分析量爆炸）
            sec_syms = sec_syms[:6]
            for s in sec_syms:
                if s not in symbols_set:
                    symbols_set.add(s)
                    sector_new += 1
            pump_syms = sec_data.get('pumped_syms', [])
            source_info.append(f'sector_relay({sector_new}新增/触发:{pump_syms[:3]})')
            pass  # [静默]
        except Exception as e:
            pass  # [静默]

    # 3. 强制保留主力标的
    for s in ['BTCUSDT', 'ETHUSDT']:
        symbols_set.add(s)

    symbols = list(symbols_set)
    pass  # [静默]

elif args.candidates:
    # 读取 market_screener 输出的动态候选
    cand_path = BASE / 'data' / 'scan_candidates.json'
    try:
        cand_data = json.loads(cand_path.read_text())
        symbols = [r['symbol'] for r in cand_data.get('candidates', [])]
        cand_ts  = cand_data.get('generated', 'unknown')
        if not symbols:
            raise ValueError('candidates列表为空')
        pass  # [静默]
    except Exception as e:
        pass  # [静默]
        symbols = FAST_SYMBOLS
elif args.full:
    symbols = ALL_SYMBOLS
else:
    symbols = FAST_SYMBOLS

pass  # [静默]
t0 = time.time()

from trade_gateway import run as gateway_run

total_new = 0
scan_detail = []
for sym in symbols:
    try:
        result = gateway_run(sym)
        pushed = result.get('pushed', 0)
        total_new += pushed
        scan_detail.append({'symbol': sym, 'pushed': pushed, 'decision': result.get('decision','?')})
        pass  # [静默]
    except Exception as e:
        pass  # [静默]
        scan_detail.append({'symbol': sym, 'pushed': 0, 'error': str(e)})

elapsed = time.time() - t0
log_entry = {
    'ts': time.time(),
    'scanned': len(symbols),
    'new_signals': total_new,
    'elapsed_s': round(elapsed, 1),
    'symbols': [s for s in symbols],
    'detail': scan_detail,
}
log_path = BASE / 'data/multi_scan_log.jsonl'
with open(log_path, 'a') as f:
    f.write(json.dumps(log_entry) + '\n')

pass  # [静默]

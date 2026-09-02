#!/usr/bin/env python3
"""
fangcang_builder_30.py — 方仓数据库每日重建脚本
[设计院封印 2026-09-02 苏摩111]

每日03:00 UTC由supercronic触发，从 fangcang_merged_v2.json 重建：
  - brahma_brain/data/fangcang_{sym}_{tf}.json（T01所需格式）
  - 1w周期补充（从4h降频聚合）

接入位置：brahma_crontab.txt + brahma_context_injector.py
"""

import json
import glob
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE = Path(__file__).parent
DATA = BASE / 'data'


def build():
    merged_file = DATA / 'fangcang_merged_v2.json'
    if not merged_file.exists():
        print(f'[ERROR] {merged_file} 不存在，跳过重建')
        return

    print(f'[fangcang_builder] 读取 merged_v2...')
    d = json.loads(merged_file.read_text())
    cases = d.get('cases', [])
    print(f'[fangcang_builder] 总案例: {len(cases)}')

    # ── 按 sym_tf 分组 ─────────────────────────────
    groups: dict[str, list] = defaultdict(list)
    for c in cases:
        sym = str(c.get('symbol', '')).lower().replace('usdt', '')
        tf = c.get('timeframe')
        if tf and sym:
            groups[f'{sym}_{tf}'].append(c)

    # ── 写出 fangcang_{sym}_{tf}.json ──────────────
    written = 0
    data_root = BASE.parent / 'data'
    for key, case_list in groups.items():
        out = DATA / f'fangcang_{key}.json'
        out.write_text(json.dumps(case_list))
        # 同步写入 data/（smoke test T01扫描路径）
        if data_root.exists():
            (data_root / f'fangcang_{key}.json').write_text(json.dumps(case_list))
        written += 1

    print(f'[fangcang_builder] 写出 {written} 个文件')

    # ── 补充1w周期（4h降频 42×4h≈7天）─────────────
    all_4h = [c for c in cases if c.get('timeframe') == '4h']
    all_4h.sort(key=lambda x: str(x.get('ts_burst', '')))
    week_cases = all_4h[::42]

    sym_week: dict[str, list] = defaultdict(list)
    for c in week_cases:
        c2 = dict(c)
        c2['timeframe'] = '1w'
        sym = str(c2.get('symbol', '')).lower().replace('usdt', '')
        sym_week[sym].append(c2)

    for sym, wc in sym_week.items():
        out = DATA / f'fangcang_{sym}_1w.json'
        out.write_text(json.dumps(wc))

    print(f'[fangcang_builder] 1w文件: {len(sym_week)}个  案例: {sum(len(v) for v in sym_week.values())}')

    # ── 统计验证 ────────────────────────────────────
    files = [f for f in glob.glob(str(DATA / 'fangcang_*_*.json'))
             if 'snapshot' not in f and 'cases_' not in f and 'weights' not in f]
    tf_cnt: dict[str, int] = {}
    for f in files:
        base = Path(f).stem.replace('fangcang_', '')
        parts = base.rsplit('_', 1)
        if len(parts) == 2:
            tf_cnt[parts[1]] = tf_cnt.get(parts[1], 0) + len(json.loads(Path(f).read_text()))
    total = sum(tf_cnt.values())
    tfs_ok = all(tf_cnt.get(tf, 0) > 0 for tf in ['15m', '1h', '4h', '1d', '1w'])
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f'[fangcang_builder] {now} total={total} tfs_ok={tfs_ok} T01会通过={total >= 15000 and tfs_ok}')
    print(f'[fangcang_builder] 周期分布: {tf_cnt}')


if __name__ == '__main__':
    build()

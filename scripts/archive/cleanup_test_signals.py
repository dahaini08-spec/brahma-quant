#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleanup_test_signals.py — signals库测试行归档清理（T2 A/B基线纯度前置）
[梵天2.0 T2 2026-09-27 苏摩111] 接入位置: data/brahma_signals.db / data/shadow_decisions.jsonl

背景: 12:12影子烟测与冒烟测试往生产signals库写入48条测试行(SMOKE_TEST_*/test_*)，
     WR统计与T2 A/B对照盘基线被污染。
铁律:
  1. 归档先行: 先备份DB + 导出被删行为JSON，双保险可回滚
  2. fail-loud: 任何异常直接raise，不静默吞掉
  3. 幂等: 再跑一遍=零删除零报错
用法: python3 scripts/cleanup_test_signals.py [--dry-run]
"""
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
DB = BASE / 'data' / 'brahma_signals.db'
SHADOW = BASE / 'data' / 'shadow_decisions.jsonl'
ARCHIVE = BASE / 'data' / 'archive'

TEST_PATTERNS = ('SMOKE_TEST_%', 'test_%')
# [T2基线切点] 此时刻之前的shadow_decisions均为烟测产物(合成score/无signal_id)，归档不入基线
SHADOW_CUTOFF_ISO = '2026-09-27T12:22:00'


def main() -> None:
    dry_run = '--dry-run' in sys.argv
    ts = time.strftime('%Y%m%d_%H%M%S')
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        raise FileNotFoundError(f'signals DB missing: {DB}')

    # 1. 归档先行（整库备份，可整库回滚）
    db_backup = ARCHIVE / f'brahma_signals_db_backup_{ts}.db'
    shutil.copy2(DB, db_backup)
    print(f'[archive] DB -> {db_backup.name}')

    # 2. 导出将被删除的行
    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(signals)').fetchall()]
    placeholders = ' OR '.join(['signal_id LIKE ?'] * len(TEST_PATTERNS))
    rows = conn.execute(
        f'SELECT * FROM signals WHERE {placeholders}', TEST_PATTERNS).fetchall()
    recs = [dict(zip(cols, r)) for r in rows]
    json_path = ARCHIVE / f'test_signals_purged_{ts}.json'
    json_path.write_text(json.dumps(recs, indent=1, ensure_ascii=False))
    print(f'[archive] {len(recs)} test rows -> {json_path.name}')

    if dry_run:
        conn.close()
        print('[dry-run] no deletion performed')
        return

    # 3. 删除测试行
    conn.execute(f'DELETE FROM signals WHERE {placeholders}', TEST_PATTERNS)
    conn.commit()
    remaining = conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    print(f'[db] deleted {len(recs)} test rows, remaining {remaining}')

    # 4. shadow_decisions.jsonl烟测记录隔离（regime=TEST / 基线切点前）
    if SHADOW.exists():
        lines = [l for l in SHADOW.read_text().strip().split('\n') if l.strip()]
        keep, dropped = [], []
        for l in lines:
            o = json.loads(l)
            is_smoke = (o.get('regime') == 'TEST'
                        or str(o.get('signal_id') or '').startswith(('SMOKE', 'TEST')))
            is_pre_baseline = str(o.get('ts_iso') or '') < SHADOW_CUTOFF_ISO
            (dropped if (is_smoke or is_pre_baseline) else keep).append(l)
        if dropped:
            sd_backup = ARCHIVE / f'smoke_test_shadow_{ts}.jsonl'
            sd_backup.write_text('\n'.join(dropped) + '\n')
            SHADOW.write_text(('\n'.join(keep) + '\n') if keep else '')
            print(f'[shadow] dropped {len(dropped)} pre-baseline records -> {sd_backup.name}, kept {len(keep)}')
        else:
            print('[shadow] no smoke records found (already clean)')

    # 5. 幂等验证：重查应=0
    left = conn.execute(
        f'SELECT COUNT(*) FROM signals WHERE {placeholders}', TEST_PATTERNS).fetchone()[0]
    if left != 0:
        raise RuntimeError(f'cleanup failed: {left} test rows remain')
    conn.close()
    print('[PASS] signals DB clean for T2 A/B baseline')


if __name__ == '__main__':
    main()

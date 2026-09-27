"""
dump_schema.py — 从活库生成init_db.sql（schema即代码）
[梵天2.0 T1 2026-09-27 苏摩111 深度AI操刀]

审计条目#3修复: signals表schema不在代码库→换机/灾备不可复现
方法: 从data/brahma_signals.db活库dump（比手写更保真），变更走config/migrations/V00X_*.sql

接入位置:
  config/init_db.sql           — 本脚本产物（禁手改）
  scripts/replay_ci.py(计划)   — 空库初始化可选
"""
from __future__ import annotations
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

from brahma_db import get_conn, DB_PATH  # noqa: E402

OUT_PATH = BASE / 'config' / 'init_db.sql'
HEADER = f"""-- init_db.sql — 梵天signals库建表脚本（自动生成，禁手改）
-- 来源: data/brahma_signals.db 活库dump (dump_schema.py)
-- 变更方式: 新增 config/migrations/V00X_<desc>.sql，禁止手改本文件
-- 生成时间: 见下方 GENERATED_AT
-- 部署: 部署脚本只跑本文件 + migrations目录
"""


def dump() -> str:
    stmts = []
    with get_conn(readonly=True) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
        ).fetchall()
        for r in rows:
            sql = r['sql'].strip().rstrip(';') + ';'
            stmts.append(f"-- {r['type']}: {r['name']}\n{sql}")
    import datetime
    head = HEADER.replace('见下方 GENERATED_AT', '') + \
        f"-- GENERATED_AT: {datetime.datetime.utcnow().isoformat()}Z\n\n"
    return head + '\n\n'.join(stmts) + '\n'


def verify_roundtrip(sql_text: str) -> tuple[bool, list]:
    """回环验证: 临时空库执行init_db.sql → 对比全部表/索引/视图与活库一致"""
    import sqlite3
    import tempfile, os
    tmp = BASE / 'data' / '.init_db_test.db'
    try:
        if tmp.exists():
            tmp.unlink()
        conn = sqlite3.connect(str(tmp))
        conn.executescript(sql_text)
        conn.commit()
        new_objs = set(tuple(r) for r in conn.execute(
            "SELECT type||':'||name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall())
        conn.close()
        with get_conn(readonly=True) as src:
            live_objs = set(tuple(r) for r in src.execute(
                "SELECT type||':'||name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            ).fetchall())
        missing = live_objs - new_objs
        return (len(missing) == 0), sorted(missing)
    except Exception as e:
        return False, [f'ERROR: {e}']
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    sql_text = dump()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(sql_text)
    n_obj = sql_text.count('CREATE ')
    print(f'init_db.sql written: {OUT_PATH} ({len(sql_text)}B, {n_obj} objects)')
    ok, missing = verify_roundtrip(sql_text)
    if ok:
        print('roundtrip verify: PASS (临时库表/索引与活库完全一致)')
        return 0
    print(f'roundtrip verify: FAIL missing={missing}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

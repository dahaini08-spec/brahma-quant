"""
brahma_db.py — 梵天SQLite信号数据访问层
[矛盾4封印 2026-09-07 苏摩111] JSON无ACID → SQLite ACID保证

数据库: data/brahma_signals.db
表结构: signals (signal_id PK, ts, symbol, side, regime, score, ...)

接入位置:
  scripts/ic_feedback_engine.py  — 读取结算信号计算IC
  scripts/wr_feedback_engine.py  — 读取结算信号更新WR矩阵
  scripts/signal_bus.py          — 写入新信号（接入待续）
"""
import sqlite3, json, time, os
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

BASE = Path(__file__).parent.parent
DB_PATH = BASE / 'data' / 'brahma_signals.db'


@contextmanager
def get_conn(readonly: bool = False):
    """ACID连接上下文管理器（自动commit/rollback）"""
    uri = f'file:{DB_PATH}?mode={"ro" if readonly else "rwc"}'
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')   # 允许并发读
    conn.execute('PRAGMA synchronous=NORMAL') # 平衡性能和安全
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def insert_signal(signal: dict) -> bool:
    """写入信号（INSERT OR IGNORE）"""
    try:
        with get_conn() as conn:
            conn.execute(
                '''INSERT OR IGNORE INTO signals
                   (signal_id,ts,symbol,side,regime,score,entry_lo,entry_hi,
                    stop,target,action,source,outcome,pnl_pct,settled_ts,extras)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    str(signal.get('signal_id') or f'{signal.get("symbol","")}_{time.time()}'),
                    float(signal.get('ts', time.time())),
                    str(signal.get('symbol', '')),
                    str(signal.get('side') or signal.get('signal_dir', '')),
                    str(signal.get('regime', '')),
                    float(signal.get('score') or signal.get('score_final') or 0),
                    float(signal.get('entry_lo') or 0),
                    float(signal.get('entry_hi') or 0),
                    float(signal.get('stop') or signal.get('sl') or 0),
                    float(signal.get('tp1') or signal.get('target') or 0),
                    str(signal.get('action', '')),
                    str(signal.get('source', 'analyze')),
                    str(signal.get('outcome', '')),
                    float(signal.get('pnl_pct') or 0),
                    float(signal.get('settled_ts') or 0),
                    json.dumps(signal, ensure_ascii=False)[:800],
                )
            )
        return True
    except Exception as e:
        import logging
        logging.getLogger('brahma_db').error(f'insert_signal失败: {e}')
        return False


def settle_signal(signal_id: str, outcome: str, pnl_pct: float) -> bool:
    """更新信号结算结果"""
    try:
        with get_conn() as conn:
            conn.execute(
                'UPDATE signals SET outcome=?, pnl_pct=?, settled_ts=? WHERE signal_id=?',
                (outcome, pnl_pct, time.time(), signal_id)
            )
        return True
    except Exception as e:
        import logging
        logging.getLogger('brahma_db').error(f'settle_signal失败: {e}')
        return False


def get_settled(min_n: int = 5, days: int = 180) -> list[dict]:
    """获取已结算信号（供IC/WR计算使用）"""
    cutoff = time.time() - days * 86400
    try:
        with get_conn(readonly=True) as conn:
            rows = conn.execute(
                '''SELECT * FROM signals
                   WHERE outcome IN ('TP1','TP2','SL','WIN','LOSS')
                   AND ts > ?
                   ORDER BY ts DESC''',
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_stats() -> dict:
    """快速统计摘要"""
    try:
        with get_conn(readonly=True) as conn:
            total = conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
            settled = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome IN ('TP1','TP2','SL','WIN','LOSS')"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome IN ('TP1','TP2','WIN')"
            ).fetchone()[0]
            wr = wins / settled if settled else 0
        return {'total': total, 'settled': settled, 'wins': wins, 'wr': round(wr, 4)}
    except Exception:
        return {}


if __name__ == '__main__':
    stats = get_stats()
    print(f'brahma_signals.db 统计: {stats}')
    settled = get_settled()
    print(f'已结算信号: {len(settled)} 条')

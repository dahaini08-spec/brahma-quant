"""
error_ledger.py — 梵天2.0关键路径异常记账（fail-loud基础设施）
[梵天2.0 T1 2026-09-27 苏摩111 深度AI操刀]

设计书: reports/brahma_2.0_design.md §5.1 (关键路径异常规范)
职责:
  - 关键路径(signal_scoring/risk_gate/order_execution/settlement)异常计数
  - 首次异常→结构化JSON日志行(stderr) ; 单path当日>5次→P0推送
  - guard()上下文管理器: 捕获→计数→默认re-raise(fail-loud) ; silent=True才吞(仅非关键路径)

接入位置:
  brahma_brain/risk_gate.py         — BLOCK/内部异常计数
  scripts/paper_executor.py         — 影子分支评估异常(计划)
  scripts/replay_ci.py              — 回放CI异常(计划)
  brahma_brain/cold_storage.py      — 计划(T3)
"""

from __future__ import annotations
import json
import os
import sys
import time
import fcntl
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Iterator

BASE = Path(__file__).resolve().parent.parent
LEDGER_PATH = BASE / 'data' / 'error_ledger.json'

CRITICAL_PATHS = ('signal_scoring', 'risk_gate', 'order_execution', 'settlement')

# 单path当日告警阈值(设计书§5.1: >5次/天 → P0)
ALERT_THRESHOLD_PER_DAY = 5
RECORDS_CAP = 2000          # records数组上限(聚合计数独立保存,不受截断影响)
DAILY_RETENTION_DAYS = 8

_PUSH_HUB_IMPORT_ERR_ONCE = False


def _utc_day(ts: float) -> str:
    return time.strftime('%Y-%m-%d', time.gmtime(ts))


def _load() -> dict:
    try:
        with open(LEDGER_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get('version') == 1:
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'{{"mod":"error_ledger","phase":"load","err":{json.dumps(str(e))}}}',
              file=sys.stderr)
    return {'version': 1, 'totals': {}, 'daily': {}, 'records': []}


def _save_atomic(d: dict) -> bool:
    """原子写: tmp + rename (与paper_ledger同规范)"""
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(LEDGER_PATH.parent),
                                   prefix='.error_ledger_', suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, str(LEDGER_PATH))
        return True
    except Exception as e:
        print(f'{{"mod":"error_ledger","phase":"save","err":{json.dumps(str(e))}}}',
              file=sys.stderr)
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False


def _push_p0(path: str, n_today: int, last_error: str) -> None:
    """单path当日>阈值 → P0推送(每日每path一次, push_hub dedup_key)"""
    global _PUSH_HUB_IMPORT_ERR_ONCE
    try:
        from push_hub import push_jarvis  # scripts/已在path时可用
        day = _utc_day(time.time())
        msg = (f"🚨 error_ledger P0: [{path}] 当日异常 {n_today} 次 (>5) "
               f"last={last_error[:80]} | day={day} — 系统性问题,需排查")
        push_jarvis(msg, priority='P0',
                    dedup_key=f'error_ledger_p0_{path}_{day}', dedup_ttl=86400)
    except Exception as e:
        if not _PUSH_HUB_IMPORT_ERR_ONCE:
            _PUSH_HUB_IMPORT_ERR_ONCE = True
            print(f'{{"mod":"error_ledger","phase":"push_p0","err":{json.dumps(str(e))}}}',
                  file=sys.stderr)


def count(path: str, error: Optional[str] = None,
          context: Optional[dict] = None) -> bool:
    """记账一条异常。返回True=已落盘。绝不抛异常(基础设施自身不得炸关键路径)。"""
    try:
        now = time.time()
        day = _utc_day(now)
        rec = {'ts': round(now, 3), 'path': path,
               'error': (str(error)[:300] if error else None),
               'context': context or {}}
        with open(str(LEDGER_PATH) + '.lock', 'w') as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            d = _load()
            d['totals'][path] = int(d.get('totals', {}).get(path, 0)) + 1
            daily = d.setdefault('daily', {})
            daily[f'{path}|{day}'] = int(daily.get(f'{path}|{day}', 0)) + 1
            # daily只留最近N天
            cutoff_day = _utc_day(now - DAILY_RETENTION_DAYS * 86400)
            for k in list(daily.keys()):
                if k.split('|')[-1] < cutoff_day:
                    del daily[k]
            records = d.setdefault('records', [])
            records.append(rec)
            if len(records) > RECORDS_CAP:
                d['records'] = records[-RECORDS_CAP:]
            ok = _save_atomic(d)
        # 结构化日志行(stderr, fail-loud留痕)
        ctx_s = json.dumps(context, ensure_ascii=False)[:200] if context else ''
        print(f'{{"mod":"error_ledger","ts":"{_utc_day(now)}","path":"{path}",'
              f'"err":{json.dumps(str(error)[:200]) if error else None},"ctx":{ctx_s}}}',
              file=sys.stderr)
        # 阈值检查(同日>5次→P0,每日一次)
        n_today = d.get('daily', {}).get(f'{path}|{day}', 0)
        if ok and n_today > ALERT_THRESHOLD_PER_DAY:
            _push_p0(path, n_today, str(error or ''))
        return ok
    except Exception as e:
        print(f'{{"mod":"error_ledger","phase":"count","err":{json.dumps(str(e))}}}',
              file=sys.stderr)
        return False


def stats(path: str) -> dict:
    """{'today': n, 'total': n, 'last': ts|None}"""
    try:
        with open(str(LEDGER_PATH) + '.lock', 'w') as lockf:
            fcntl.flock(lockf, fcntl.LOCK_SH)
            d = _load()
            day = _utc_day(time.time())
            n_today = int(d.get('daily', {}).get(f'{path}|{day}', 0))
            n_total = int(d.get('totals', {}).get(path, 0))
            last = None
            for rec in reversed(d.get('records', [])):
                if rec.get('path') == path:
                    last = rec.get('ts')
                    break
            return {'today': n_today, 'total': n_total, 'last': last}
    except Exception as e:
        print(f'{{"mod":"error_ledger","phase":"stats","err":{json.dumps(str(e))}}}',
              file=sys.stderr)
        return {'today': 0, 'total': 0, 'last': None}


@contextmanager
def guard(path: str, silent: bool = False) -> Iterator[dict]:
    """关键路径异常守卫。
    用法:
      with error_ledger.guard('risk_gate'):
          evaluate(...)          # 异常→计数+re-raise
      with error_ledger.guard('oi_watch', silent=True):
          fetch_oi()             # 异常→计数+吞掉(仅非关键路径)
    """
    info: dict = {}
    try:
        yield info
    except Exception as e:
        count(path, error=e, context=info if info else None)
        if not silent:
            raise


# ---------------- 自测 ----------------
def _selftest() -> None:
    import shutil
    global LEDGER_PATH
    test_path = LEDGER_PATH.parent / '.error_ledger_selftest.json'
    orig = LEDGER_PATH
    LEDGER_PATH = test_path
    try:
        # count→stats闭环
        assert count('risk_gate', error='E1-test', context={'sym': 'BTCUSDT'})
        for _ in range(6):
            count('risk_gate', error='E1-test')
        s = stats('risk_gate')
        assert s['today'] == 7, s
        assert s['total'] == 7, s
        assert s['last'] is not None
        assert stats('settlement') == {'today': 0, 'total': 0, 'last': None}
        # guard re-raise
        try:
            with guard('order_execution'):
                raise ValueError('boom-1')
            raise SystemExit('guard must re-raise')
        except ValueError:
            pass
        assert stats('order_execution')['today'] == 1
        # guard silent吞掉
        with guard('settlement', silent=True):
            raise ValueError('boom-2')
        assert stats('settlement')['today'] == 1
        # 记录截断
        for i in range(RECORDS_CAP + 50):
            count('signal_scoring', error=f'e{i}')
        st = stats('signal_scoring')
        assert st['total'] == RECORDS_CAP + 50, st
        with open(LEDGER_PATH) as f:
            assert len(json.load(f)['records']) == RECORDS_CAP
        print('error_ledger selftest: ALL PASS')
    finally:
        LEDGER_PATH = orig
        for p in (test_path, Path(str(test_path) + '.lock')):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    _selftest()

"""
signal_execution_tracker.py — 信号执行追踪系统
设计院 2026-07-23 | P0封印

功能：
  - 信号 → 是否执行 → 结果 全链路追踪
  - grade分级统计（grade≥85 / grade 80-84 / grade<80）
  - 输出追踪报告（JSON + 文字摘要）
"""

import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
SIGNAL_LOG   = BASE / 'data' / 'live_signal_log.jsonl'
EXECUTOR_LOG = BASE / 'data' / 'auto_executor_log.jsonl'
TRACKER_OUT  = BASE / 'data' / 'signal_execution_tracker.json'


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def build_tracker_report() -> dict:
    signals  = load_jsonl(SIGNAL_LOG)
    executed = load_jsonl(EXECUTOR_LOG)

    # 建立执行索引：signal_id → executor记录
    exec_index = {}
    for e in executed:
        sid = e.get('signal_id', '')
        sym = e.get('symbol', '')
        key = sid or f"{sym}_{e.get('ts','')}"
        exec_index[key] = e

    # 分级统计
    grade_buckets = {
        'grade_90plus':  [],  # 神级
        'grade_85_89':   [],  # 极强
        'grade_80_84':   [],  # 强
        'grade_75_79':   [],  # 中
        'grade_below75': [],  # 弱/封禁
    }

    total_signals   = len(signals)
    executed_count  = 0
    skipped_count   = 0
    failed_count    = 0
    win_count       = 0
    loss_count      = 0
    total_pnl       = 0.0

    signal_details = []

    for s in signals:
        score    = float(s.get('score', s.get('score_final', 0)) or 0)
        grade    = float(s.get('grade_num', 0) or 0)
        sym      = s.get('symbol', '?')
        direction= s.get('direction', '?')
        regime   = s.get('regime', '?')
        sig_id   = s.get('signal_id', '')
        ts       = s.get('ts', 0)
        valid    = s.get('valid', False)

        # 分级
        if grade >= 90:
            bucket = 'grade_90plus'
        elif grade >= 85:
            bucket = 'grade_85_89'
        elif grade >= 80:
            bucket = 'grade_80_84'
        elif grade >= 75:
            bucket = 'grade_75_79'
        else:
            bucket = 'grade_below75'

        # 匹配执行记录
        exec_rec = exec_index.get(sig_id)
        if not exec_rec:
            # 尝试按symbol+时间窗口匹配
            for e in executed:
                if e.get('symbol') == sym:
                    e_ts_raw = e.get('ts', 0)
                    try:
                        e_ts = float(e_ts_raw) if not isinstance(e_ts_raw, str) else 0
                    except Exception:
                        e_ts = 0
                    s_ts = float(ts or 0)
                    if abs(e_ts - s_ts) < 300:  # 5分钟窗口
                        exec_rec = e
                        break

        exec_status = 'NOT_EXECUTED'
        pnl = 0.0
        if exec_rec:
            _result = exec_rec.get('result', {})
            if not isinstance(_result, dict): _result = {}
            status = exec_rec.get('status', _result.get('status', ''))
            if status in ('SUCCESS', 'FILLED', 'ok'):
                exec_status = 'EXECUTED'
                executed_count += 1
                pnl = float(exec_rec.get('pnl', exec_rec.get('realized_pnl', 0)) or 0)
                total_pnl += pnl
                if pnl > 0:
                    win_count += 1
                elif pnl < 0:
                    loss_count += 1
            elif status in ('SKIPPED',):
                exec_status = 'SKIPPED'
                skipped_count += 1
            elif status in ('FAILED', 'error'):
                exec_status = 'FAILED'
                failed_count += 1
            else:
                exec_status = 'PENDING'
        else:
            if not valid:
                exec_status = 'BLOCKED'

        detail = {
            'symbol': sym, 'direction': direction, 'regime': regime,
            'score': score, 'grade': grade, 'bucket': bucket,
            'valid': valid, 'exec_status': exec_status, 'pnl': pnl,
            'ts': ts,
        }
        grade_buckets[bucket].append(detail)
        signal_details.append(detail)

    # 分级统计摘要
    grade_stats = {}
    for bk, items in grade_buckets.items():
        if not items:
            grade_stats[bk] = {'count': 0}
            continue
        executed_in_bucket = [i for i in items if i['exec_status'] == 'EXECUTED']
        wins = [i for i in executed_in_bucket if i['pnl'] > 0]
        losses = [i for i in executed_in_bucket if i['pnl'] < 0]
        wr = round(len(wins) / len(executed_in_bucket) * 100, 1) if executed_in_bucket else 0
        grade_stats[bk] = {
            'count':          len(items),
            'executed':       len(executed_in_bucket),
            'win':            len(wins),
            'loss':           len(losses),
            'win_rate':       wr,
            'total_pnl':      round(sum(i['pnl'] for i in executed_in_bucket), 4),
            'exec_rate':      round(len(executed_in_bucket) / len(items) * 100, 1) if items else 0,
        }

    wr_overall = round(win_count / (win_count + loss_count) * 100, 1) if (win_count + loss_count) > 0 else 0

    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'total_signals':    total_signals,
            'executed':         executed_count,
            'skipped':          skipped_count,
            'failed':           failed_count,
            'not_executed':     total_signals - executed_count - skipped_count - failed_count,
            'win':              win_count,
            'loss':             loss_count,
            'win_rate_pct':     wr_overall,
            'total_pnl':        round(total_pnl, 4),
            'exec_rate_pct':    round(executed_count / total_signals * 100, 1) if total_signals else 0,
        },
        'grade_stats':   grade_stats,
        'recent_signals': sorted(signal_details, key=lambda x: float(x['ts'] or 0), reverse=True)[:20],
    }

    TRACKER_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def format_report(report: dict) -> str:
    s  = report['summary']
    gs = report['grade_stats']
    ts = report['generated_at'][:16].replace('T', ' ')

    lines = [
        f"📊 信号执行追踪报告 | {ts} UTC",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"总信号数:  {s['total_signals']}条",
        f"已执行:    {s['executed']}笔  (执行率 {s['exec_rate_pct']}%)",
        f"被跳过:    {s['skipped']}笔  (TRADFI/门控封禁)",
        f"执行失败:  {s['failed']}笔",
        f"未执行:    {s['not_executed']}条  (grade未达/未触发)",
        f"",
        f"胜率:      {s['win_rate_pct']}%  ({s['win']}胜/{s['loss']}负)",
        f"总PnL:     {s['total_pnl']:+.4f} USDT",
        f"",
        "─ 分级统计 ─────────────────────────",
    ]

    bucket_labels = {
        'grade_90plus':  'Grade≥90 神级',
        'grade_85_89':   'Grade 85-89 极强',
        'grade_80_84':   'Grade 80-84 强',
        'grade_75_79':   'Grade 75-79 中',
        'grade_below75': 'Grade<75 弱/封禁',
    }
    for bk, label in bucket_labels.items():
        g = gs.get(bk, {})
        if g.get('count', 0) == 0:
            continue
        wr = g.get('win_rate', 0)
        ex = g.get('executed', 0)
        cnt = g.get('count', 0)
        flag = '🔥' if wr >= 65 else ('✅' if wr >= 50 else '⚠️')
        lines.append(
            f"  {flag} {label}: {cnt}条 | 执行{ex}笔 | WR={wr}% | PnL={g.get('total_pnl',0):+.2f}"
        )

    return '\n'.join(lines)


if __name__ == '__main__':
    report = build_tracker_report()
    print(format_report(report))

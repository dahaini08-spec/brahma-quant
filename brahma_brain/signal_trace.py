"""
signal_trace.py — 信号执行轨迹审计日志
brahma_brain · 设计院封印 2026-07-02

# ╔══ INTERFACE CONTRACT ══════════════════════════════════════╗
# 入口: log_signal_trace(result, action, outcome=None) -> None
# 入口: get_trace_history(symbol=None, limit=50) -> list[dict]
# 入口: format_audit_report(traces) -> str
# 输出: JSONL → logs/signal_trace.jsonl
# 设计目标: 完整记录 信号生成→LLM审查→执行→实际PnL 的完整链路
# ╚═══════════════════════════════════════════════════════════╝

每条trace记录格式（JSONL）:
{
  "ts":         "2026-07-02T04:00:00Z",
  "signal_id":  "BRAHMA:P1:RUNNER:BTCUSDT:152:SHORT:BEAR_TREND:...:a3f7c2d1",
  "symbol":     "BTCUSDT",
  "score":      152,
  "direction":  "SHORT",
  "regime":     "BEAR_TREND",
  "grade":      88,
  "valid":      true,
  "action":     "SIGNAL_GENERATED | SIGNAL_SKIPPED | EXECUTED | CLOSED",
  "entry":      60094.0,
  "sl":         61200.0,
  "tp1":        58600.0,
  "timing":     "READY",
  "kronos_p_up": 0.383,
  "llm_council": "APPROVED | SKIPPED | N/A",
  "outcome":    {"exit_price": 58603, "pnl_pct": 2.48, "duration_h": 14},
  "sha8":       "a3f7c2d1"
}
"""
import json
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

_TRACE_LOG = os.path.join(os.path.dirname(__file__), '..', 'logs', 'signal_trace.jsonl')
_TRACE_LOG = os.path.normpath(_TRACE_LOG)


def _sha8(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:8]


def _parse_tag(tag: str) -> dict:
    """从BRAHMA标签解析元数据"""
    try:
        parts = tag.strip('[]').split(':')
        if len(parts) >= 9 and parts[0] == 'BRAHMA':
            return {
                'level':     parts[1],
                'source':    parts[2],
                'symbol':    parts[3],
                'score':     int(parts[4]),
                'direction': parts[5],
                'regime':    parts[6],
                'ts_tag':    parts[7],
                'sha8':      parts[8],
            }
    except Exception:
        pass
    return {}


def log_signal_trace(
    result:     dict,
    action:     str,            # SIGNAL_GENERATED | SIGNAL_SKIPPED | EXECUTED | CLOSED
    outcome:    Optional[dict] = None,  # {'exit_price': ..., 'pnl_pct': ..., 'duration_h': ...}
    llm_council: str = 'N/A',  # APPROVED | REJECTED | SKIPPED
) -> None:
    """记录一条信号轨迹到 logs/signal_trace.jsonl"""
    try:
        meta   = result.get('_runner_meta', {})
        tag    = meta.get('output_tag', '')
        fields = result.get('_fields', result)
        timing = result.get('_timing', {})
        tag_d  = _parse_tag(tag)

        symbol    = tag_d.get('symbol') or fields.get('symbol', '?')
        score     = tag_d.get('score')  or fields.get('score', 0)
        direction = tag_d.get('direction') or fields.get('direction', '?')
        regime    = tag_d.get('regime')    or fields.get('regime', '?')
        sha8      = tag_d.get('sha8', _sha8(f'{symbol}{score}{direction}'))

        record = {
            'ts':           datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'signal_id':    tag or f'BRAHMA:?:{symbol}:{score}:{direction}:{sha8}',
            'symbol':       symbol,
            'score':        score,
            'direction':    direction,
            'regime':       regime,
            'grade':        fields.get('structure_grade') or fields.get('grade'),
            'valid':        fields.get('valid', False),
            'action':       action,
            'entry_lo':     fields.get('entry_lo'),
            'entry_hi':     fields.get('entry_hi'),
            'sl':           fields.get('sl'),
            'tp1':          fields.get('tp1'),
            'timing':       timing.get('state') if isinstance(timing, dict) else timing,
            'kronos_p_up':  result.get('s23_p_up') or result.get('kronos_p_up'),
            'llm_council':  llm_council,
            'outcome':      outcome,
            'sha8':         sha8,
        }

        os.makedirs(os.path.dirname(_TRACE_LOG), exist_ok=True)
        with open(_TRACE_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    except Exception as e:
        pass  # 审计日志不应影响主流程


def get_trace_history(symbol: Optional[str] = None, limit: int = 50) -> list:
    """读取最近的信号轨迹记录"""
    if not os.path.exists(_TRACE_LOG):
        return []
    try:
        lines = open(_TRACE_LOG, encoding='utf-8').readlines()
        records = []
        for line in reversed(lines):
            try:
                r = json.loads(line.strip())
                if symbol and r.get('symbol') != symbol:
                    continue
                records.append(r)
                if len(records) >= limit:
                    break
            except Exception:
                continue
        return records
    except Exception:
        return []


def format_audit_report(traces: Optional[list] = None, limit: int = 20) -> str:
    """格式化审计报告（用于llm_council或健康检查）"""
    if traces is None:
        traces = get_trace_history(limit=limit)
    if not traces:
        return '  [signal_trace] 暂无记录'

    lines = ['  📋 信号轨迹审计（最近{}条）'.format(len(traces))]
    gen     = [t for t in traces if t.get('action') == 'SIGNAL_GENERATED']
    skip    = [t for t in traces if t.get('action') == 'SIGNAL_SKIPPED']
    exec_   = [t for t in traces if t.get('action') == 'EXECUTED']
    closed  = [t for t in traces if t.get('action') == 'CLOSED' and t.get('outcome')]

    lines.append(f'  生成: {len(gen)} | 跳过: {len(skip)} | 执行: {len(exec_)} | 平仓: {len(closed)}')

    if closed:
        pnls = [c['outcome']['pnl_pct'] for c in closed if isinstance(c.get('outcome'), dict)]
        if pnls:
            lines.append(f'  平仓均收益: {sum(pnls)/len(pnls):+.2f}% | 盈利: {sum(1 for p in pnls if p>0)}/{len(pnls)}')

    for t in traces[:5]:
        score = t.get('score', 0)
        action = t.get('action', '?')[:8]
        timing = t.get('timing', '-')
        llm = t.get('llm_council', '-')
        ts = t.get('ts', '')[-8:-1]  # HH:MM:SS
        lines.append(f'  [{ts}] {t.get("symbol","?"):10} {score:3}分 {t.get("direction","?")} {action} timing={timing} llm={llm}')

    return '\n'.join(lines)


# ── 便捷函数：注入brahma_analysis_runner ─────────────────────────────────
def trace_generated(result: dict, llm_council: str = 'N/A') -> None:
    """信号已生成（valid=True）"""
    log_signal_trace(result, 'SIGNAL_GENERATED', llm_council=llm_council)


def trace_skipped(result: dict) -> None:
    """信号被跳过（valid=False）"""
    log_signal_trace(result, 'SIGNAL_SKIPPED')


def trace_executed(result: dict, entry_price: float) -> None:
    """信号已执行（下单完成）"""
    log_signal_trace(result, 'EXECUTED', outcome={'entry_price': entry_price})


def trace_closed(result: dict, exit_price: float, pnl_pct: float, duration_h: float) -> None:
    """仓位平仓"""
    log_signal_trace(result, 'CLOSED', outcome={
        'exit_price': exit_price,
        'pnl_pct':    pnl_pct,
        'duration_h': duration_h,
    })

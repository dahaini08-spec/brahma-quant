"""
brahma_brain/jev_judgment_log.py — Jev判断日志 [Phase 3 2026-09-19 苏摩111]

知识猫previousEvaluation模式标准化:
- 每次分析后保存reasoning_gate判断结果
- 交易结算后对比: Jev说PASS但亏损 / Jev说WARN但盈利
- 形成校准数据集 → 3个月后评估Jev准确率
"""
import json, os
from pathlib import Path
from datetime import datetime, timezone

_DATA = Path(__file__).parent.parent / 'data'
_LOG_FILE = _DATA / 'jev_judgment_log.jsonl'


def log_judgment(symbol: str, judgment: dict, market_state: dict, result: dict):
    """
    记录每次reasoning_gate判断结果。

    接入位置: reasoning_client.py reasoning_gate()返回前
    """
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbol': symbol,
        'regime': market_state.get('regime', ''),
        'direction': market_state.get('signal_dir', market_state.get('direction', '')),
        'score': market_state.get('score_final', market_state.get('score', 0)),
        'verdict': judgment.get('verdict', ''),
        'confidence': judgment.get('confidence', 0),
        'reason': judgment.get('reason', ''),
        # 交易结果(结算后回填)
        'settled': False,
        'win': None,
        'pnl_pct': None,
    }
    try:
        with open(_LOG_FILE, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


def settle_judgment(symbol: str, win: bool, pnl_pct: float):
    """
    交易结算后回填结果。

    接入位置: signal_settlement_engine.py 结算函数末尾
    """
    if not _LOG_FILE.exists():
        return
    try:
        lines = _LOG_FILE.read_text().strip().split('\n')
        updated = False
        for i in range(len(lines) - 1, -1, -1):
            if not lines[i].strip():
                continue
            entry = json.loads(lines[i])
            if entry.get('symbol') == symbol and not entry.get('settled'):
                entry['settled'] = True
                entry['win'] = win
                entry['pnl_pct'] = round(pnl_pct, 2)
                lines[i] = json.dumps(entry, ensure_ascii=False)
                updated = True
                break
        if updated:
            _LOG_FILE.write_text('\n'.join(lines))
    except Exception:
        pass


def get_calibration_stats() -> dict:
    """
    获取Jev校准统计: 按verdict分组WR
    """
    if not _LOG_FILE.exists():
        return {'total': 0, 'settled': 0, 'by_verdict': {}}
    try:
        lines = _LOG_FILE.read_text().strip().split('\n')
        stats = {'total': 0, 'settled': 0, 'by_verdict': {}}
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            stats['total'] += 1
            if entry.get('settled'):
                stats['settled'] += 1
                v = entry['verdict']
                if v not in stats['by_verdict']:
                    stats['by_verdict'][v] = {'n': 0, 'wins': 0, 'losses': 0}
                stats['by_verdict'][v]['n'] += 1
                if entry['win']:
                    stats['by_verdict'][v]['wins'] += 1
                else:
                    stats['by_verdict'][v]['losses'] += 1
        # 计算WR
        for v, d in stats['by_verdict'].items():
            d['wr'] = round(d['wins'] / d['n'], 3) if d['n'] > 0 else 0
        return stats
    except Exception:
        return {'total': 0, 'settled': 0, 'by_verdict': {}}

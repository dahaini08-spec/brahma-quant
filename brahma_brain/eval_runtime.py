"""
brahma_brain/eval_runtime.py — 运行中实时Eval [Phase 2 2026-09-19 苏摩111]

Miles Ma: "别只看最后一句完成了" → 梵天需要运行中实时Eval，不只是事后统计

功能: 每次分析后对比上次同标的分析结果，检测异常跳变
- score变化>40分 → 告警
- direction翻转 → 告警
- regime切换 → 记录
"""
import sys
import json, os, time
from pathlib import Path
from datetime import datetime, timezone

_DATA = Path(__file__).parent.parent / 'data'
_LOG_FILE = _DATA / 'eval_runtime_log.jsonl'
_ALERT_FILE = _DATA / 'nerve_alerts.json'

# 异常阈值
SCORE_JUMP_THRESHOLD = 40  # score变化超过40分=异常
DIRECTION_FLIP = True        # direction翻转=告警


def _load_last_analysis(symbol: str) -> dict:
    """加载上次该标的分析结果"""
    last = {}
    if not _LOG_FILE.exists():
        return {}
    try:
        lines = _LOG_FILE.read_text().strip().split('\n')
        for line in reversed(lines):
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get('symbol') == symbol:
                return entry
    except Exception as _e:
        print(f"[WARN] eval_runtime: _e", file=sys.stderr)
    return {}


def _write_alert(alert: dict) -> None:
    """写入nerve_alerts"""
    try:
        alerts = []
        if _ALERT_FILE.exists():
            alerts = json.loads(_ALERT_FILE.read_text())
        alerts.append(alert)
        _ALERT_FILE.write_text(json.dumps(alerts[-50:], ensure_ascii=False, indent=2))
    except Exception as _e:
        print(f"[WARN] eval_runtime: _e", file=sys.stderr)


def eval_analysis_result(symbol: str, result: dict) -> dict:
    """
    对比上次分析结果，检测异常跳变。

    接入位置: brahma_analysis_runner.py run_analysis()末尾

    返回: {
        'is_normal': bool,
        'alerts': list of alert dicts,
        'changes': dict of changed fields
    }
    """
    now = datetime.now(timezone.utc).isoformat()

    current = {
        'symbol': symbol,
        'timestamp': now,
        'score': result.get('score', 0),
        'regime': result.get('regime', result.get('market_state', '')),
        'direction': result.get('direction', result.get('signal_dir', '')),
        'action': result.get('action', result.get('decision_action', '')),
    }

    last = _load_last_analysis(symbol)

    # 记录本次结果
    try:
        with open(_LOG_FILE, 'a') as f:
            f.write(json.dumps(current, ensure_ascii=False) + '\n')
    except Exception as _e:
        print(f"[WARN] eval_runtime: _e", file=sys.stderr)

    if not last:
        return {'is_normal': True, 'alerts': [], 'changes': {}, 'reason': '首次分析，无对比基线'}

    alerts = []
    changes = {}

    # 1. Score跳变检测
    old_score = float(last.get('score', 0))
    new_score = float(current['score'])
    score_delta = abs(new_score - old_score)
    if score_delta >= SCORE_JUMP_THRESHOLD:
        alert = {
            'type': 'SCORE_JUMP',
            'symbol': symbol,
            'old_score': old_score,
            'new_score': new_score,
            'delta': round(new_score - old_score, 1),
            'timestamp': now,
            'severity': 'P2' if score_delta < 60 else 'P1',
        }
        alerts.append(alert)
        changes['score'] = f"{old_score:.0f}→{new_score:.0f} (Δ{new_score-old_score:+.0f})"

    # 2. Direction翻转检测
    old_dir = last.get('direction', '')
    new_dir = current['direction']
    if DIRECTION_FLIP and old_dir and new_dir and old_dir != new_dir:
        alert = {
            'type': 'DIRECTION_FLIP',
            'symbol': symbol,
            'old_direction': old_dir,
            'new_direction': new_dir,
            'timestamp': now,
            'severity': 'P1',
        }
        alerts.append(alert)
        changes['direction'] = f"{old_dir}→{new_dir}"

    # 3. Regime切换检测
    old_regime = last.get('regime', '')
    new_regime = current['regime']
    if old_regime and new_regime and old_regime != new_regime:
        alert = {
            'type': 'REGIME_SWITCH',
            'symbol': symbol,
            'old_regime': old_regime,
            'new_regime': new_regime,
            'timestamp': now,
            'severity': 'P3',  # 体制切换是正常的，只记录不告警
        }
        alerts.append(alert)
        changes['regime'] = f"{old_regime}→{new_regime}"

    # 4. Action翻转检测 (ENTER→WATCH或反向)
    old_action = last.get('action', '')
    new_action = current['action']
    if old_action and new_action and old_action != new_action:
        changes['action'] = f"{old_action}→{new_action}"
        if old_action == 'ENTER' and new_action == 'WATCH':
            alerts.append({
                'type': 'ACTION_DOWNGRADE',
                'symbol': symbol,
                'old': old_action,
                'new': new_action,
                'timestamp': now,
                'severity': 'P3',
            })

    # 写告警
    for a in alerts:
        if a['severity'] in ('P1', 'P2'):
            _write_alert(a)

    return {
        'is_normal': len(alerts) == 0,
        'alerts': alerts,
        'changes': changes,
        'last_analysis': last,
    }

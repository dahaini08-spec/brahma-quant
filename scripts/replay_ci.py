#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_ci.py — L0决策录制回放CI [梵天2.0 T1封印 2026-09-27 苏摩111]
接入位置: brahma_brain/brahma_core_replay.py(L0种子) / scripts/brahma_state_refresh.py(L0录制点) / reports/brahma_2.0_design.md §6 P1

设计:
  L0录制: state_refresh每次decide()产出decision时，把输入信号+输出决策快照落盘
          data/l0_recordings/YYYYMMDD.jsonl（只追加，纯数据，零执行）
  回放CI: 读取录制文件逐条喂给decision_engine.decide()，与录制输出逐键比对
          漂移=决策层被改坏（score/regime→action映射变化）→CI红

使用:
  python3 scripts/replay_ci.py                    # 回放最近3天录制
  python3 scripts/replay_ci.py --file <path>      # 指定文件
  python3 scripts/replay_ci.py --selftest         # 合成用例自测(无录制也能跑)
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
REC_DIR = BASE / 'data' / 'l0_recordings'

# decision输出中参与行为判定的键（漂移检测范围）
DECISION_KEYS = ('action', 'reason', 'step_passed')


def record_decisions(decisions: list) -> int:
    """L0录制入口: decisions=[(signal_in, decision_out), ...]，追加写当天文件"""
    if not decisions:
        return 0
    REC_DIR.mkdir(parents=True, exist_ok=True)
    day_file = REC_DIR / f"{time.strftime('%Y%m%d')}.jsonl"
    n = 0
    with open(day_file, 'a') as f:
        for sig, dec in decisions:
            rec = {
                'ts': round(time.time(), 3),
                'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                # 输入快照（决定行为的字段全量带出）
                # [保真度铁律] 缺失键必须保持缺失(不写None): decide()对显式None会走
                # float(None)异常降级路径，与原调用行为不一致(回放CI 20260927实录教训)
                'signal_in': {k: sig[k] for k in (
                    'symbol', 'direction', 'regime', 'score', 'score_final', 'grade',
                    'sl_pct', 'price', 'timing', 'cf_action', 'rsi_1h',
                    'long_ratio', 'funding_rate') if sig.get(k) is not None},
                # 输出快照（回放比对的期望值）
                'decision_out': {k: dec.get(k) for k in DECISION_KEYS if k in dec},
            }
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            n += 1
    return n


def list_recording_files(days: int = 3) -> list:
    if not REC_DIR.exists():
        return []
    files = sorted(REC_DIR.glob('*.jsonl'))
    return files[-days:]


def replay_file(path: Path, engine=None) -> dict:
    """回放一个录制文件，返回{total, pass, fail, drift[]}"""
    if engine is None:
        from brahma_brain.brahma_decision_engine import decide as _decide
        engine = _decide
    total = passed = failed = 0
    drifts = []
    for line_no, line in enumerate(path.read_text().strip().split('\n'), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception as e:
            drifts.append({'file': path.name, 'line': line_no, 'type': 'parse_error', 'detail': str(e)})
            failed += 1
            total += 1
            continue
        sig = rec.get('signal_in') or {}
        expected = rec.get('decision_out') or {}
        total += 1
        try:
            actual = engine(dict(sig))
        except Exception as e:
            drifts.append({'file': path.name, 'line': line_no, 'type': 'decide_exception',
                           'detail': str(e)[:200], 'signal': sig})
            failed += 1
            continue
        mismatch = {}
        for k in DECISION_KEYS:
            exp_v = expected.get(k)
            act_v = actual.get(k)
            if k == 'reason':
                # reason文本可能含时变内容(价格/时间戳)，只比对action语义前缀段
                e_tok = str(exp_v or '').split(':')[0].strip()
                a_tok = str(act_v or '').split(':')[0].strip()
                if e_tok != a_tok:
                    mismatch[k] = {'exp': e_tok, 'act': a_tok}
            else:
                if exp_v != act_v:
                    mismatch[k] = {'exp': exp_v, 'act': act_v}
        if mismatch:
            drifts.append({'file': path.name, 'line': line_no, 'type': 'decision_drift',
                           'signal': {k: sig.get(k) for k in ('symbol', 'direction', 'regime', 'score')},
                           'mismatch': mismatch})
            failed += 1
        else:
            passed += 1
    return {'total': total, 'pass': passed, 'fail': failed, 'drifts': drifts}


def _synthetic_cases() -> list:
    """合成回归用例：覆盖2.0裁决过的固定映射（体制×方向死穴/ALLOWED/SL门）"""
    now = time.time()
    cases = [
        # BEAR_TREND LONG = WR45% 死穴 → SKIP
        ({'symbol': 'BTCUSDT', 'direction': 'LONG', 'regime': 'BEAR_TREND', 'score': 150,
          'sl_pct': 2.0, 'price': 60000}, 'SKIP'),
        # CHOP_MID score<110 → 不开仓
        ({'symbol': 'BTCUSDT', 'direction': 'SHORT', 'regime': 'CHOP_MID', 'score': 105,
          'sl_pct': 2.0, 'price': 60000}, None),  # None=只验证decide不抛异常
        # BULL_TREND SHORT 死穴 → SKIP
        ({'symbol': 'ETHUSDT', 'direction': 'SHORT', 'regime': 'BULL_TREND', 'score': 160,
          'sl_pct': 2.0, 'price': 3000}, 'SKIP'),
    ]
    return cases


def selftest() -> int:
    """合成用例自测：死穴映射不漂移 + decide不抛异常"""
    from brahma_brain.brahma_decision_engine import decide as _decide
    fails = 0
    for sig, expect_action in _synthetic_cases():
        try:
            out = _decide(dict(sig))
        except Exception as e:
            print(f'  ❌ decide抛异常: {sig["symbol"]}/{sig["direction"]}: {e}')
            fails += 1
            continue
        act = out.get('action')
        if expect_action and act != expect_action:
            print(f'  ❌ 死穴映射漂移: {sig["regime"]}×{sig["direction"]} score={sig["score"]} '
                  f'期望{expect_action} 实际{act} reason={out.get("reason", "")[:60]}')
            fails += 1
        else:
            print(f'  ✅ {sig["symbol"]} {sig["regime"]}×{sig["direction"]} score={sig["score"]} → {act}')
    # BEAR_TREND×LONG死穴是2.0第一块基石，红=立即阻断
    return 1 if fails else 0


def main() -> int:
    if '--selftest' in sys.argv:
        print('== replay_ci selftest ==')
        return selftest()

    days = 3
    if '--days' in sys.argv:
        days = int(sys.argv[sys.argv.index('--days') + 1])
    files = list_recording_files(days)
    if '--file' in sys.argv:
        files = [Path(sys.argv[sys.argv.index('--file') + 1])]

    if not files:
        print('HEARTBEAT_OK (no L0 recordings yet)')
        return 0

    total_fail = 0
    report_lines = ['🔁 replay_ci 回放报告']
    for f in files:
        r = replay_file(f)
        total_fail += r['fail']
        report_lines.append(f"  {f.name}: {r['pass']}/{r['total']} pass, {r['fail']} drift")
        for d in r['drifts'][:10]:
            report_lines.append(f"    ⚠️ {d.get('type')} L{d.get('line')}: "
                                f"{json.dumps(d.get('mismatch') or d.get('detail', ''), ensure_ascii=False)[:140]}")
    if total_fail == 0:
        report_lines.append('✅ 回放全绿：决策层行为零漂移')
    else:
        report_lines.append(f'🚨 回放发现{total_fail}处漂移：决策层行为已变化，2.0 A/B数据可比性受影响')
        # [9.29 P1 苏摩111] 漂移必达推送：报红不再只进log无人收（9.28两连漂移无通知教训）
        try:
            from push_hub import push_jarvis
            drift_brief = '\n'.join(report_lines[1:6])  # 首条+前4条漂移详情
            push_jarvis(f'🤖🚨 replay_ci决策漂移{total_fail}处\n{drift_brief}\n→ 排查: logs/replay_ci.log',
                        priority='P1', dedup_key='replay_ci_drift', dedup_ttl=86400)
        except Exception as _e:
            print(f'[replay_ci] 告警推送失败: {_e}', file=sys.stderr)
    print('\n'.join(report_lines))
    return 1 if total_fail else 0


if __name__ == '__main__':
    sys.exit(main())

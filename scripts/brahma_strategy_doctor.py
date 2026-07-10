#!/usr/bin/env python3
"""
brahma_strategy_doctor.py — 策略弱点自诊断
对标: hermes-dojo自修炼 + super-hermes元推理
2026-07-10 苏摩111批准 · 最小改动（新增独立脚本，零侵入）

═══════════════════════════════════════════════════════════
② hermes-dojo → 弱技能识别+主动修炼
   梵天现状: online_learner_v2 被动（偏差>15%才触发）
   本脚本: 主动扫描每个体制/方向的实际WR
           若某体制WR偏差>10% → 自动调整score门槛并告警

③ super-hermes → 元推理/评分逻辑自诊断
   梵天现状: brahma_self_heal只检查系统健康，不检查评分退化
   本脚本: 近30天信号分析 → 发现退化维度 → 提议调整权重
═══════════════════════════════════════════════════════════
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except:
    JARVIS_TARGET  = '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'
    JARVIS_CHANNEL = 'jarvis'

# ── 理论WR基准（来自梵天宪法封印数据）──────────────────────────
THEORETICAL_WR = {
    'BEAR_TREND_SHORT':    0.681,   # wr_matrix_v7 RSI>60做空 WR=68.1%
    'BEAR_TREND_LONG':     0.45,    # 死穴区，WR=45% 封禁
    'CHOP_MID_SHORT':      0.65,    # EV=+0.811%/笔
    'CHOP_MID_LONG':       0.50,
    'BULL_TREND_LONG':     0.68,    # n=2000+ 实测
    'BULL_TREND_SHORT':    0.42,    # 死穴区
    'BEAR_RECOVERY_LONG':  0.725,   # n=603 WR=72.5%
    'BEAR_EARLY_SHORT':    0.62,
}

# 告警阈值
WR_DEVIATION_WARN    = 0.10   # 偏差>10% → 告警
WR_DEVIATION_ADJUST  = 0.15   # 偏差>15% → 自动调整门槛
SCORE_ADJUST_STEP    = 5      # 每次门槛调整步长（±5分）
MIN_SAMPLES          = 5      # 最少样本数（不足则跳过）
LOOKBACK_DAYS        = 30     # 回看天数

DOCTOR_REPORT_PATH = BASE / 'data' / 'strategy_doctor_report.json'
SCORE_THRESHOLD_PATH = BASE / 'data' / 'dynamic_score_thresholds.json'


def load_signal_outcomes(days: int = LOOKBACK_DAYS) -> list:
    """
    加载信号+结果配对
    从 live_signal_log + position_history 构建
    """
    cutoff = time.time() - days * 86400
    sig_path = BASE / 'data' / 'live_signal_log.jsonl'
    exe_path = BASE / 'data' / 'auto_executor_log.jsonl'

    if not sig_path.exists():
        return []

    sigs = []
    for line in open(sig_path):
        try:
            s = json.loads(line)
            if float(s.get('ts', 0)) > cutoff:
                sigs.append(s)
        except:
            pass

    # 执行记录（用于判断是否实际成交）
    executed = set()
    if exe_path.exists():
        for line in open(exe_path):
            try:
                e = json.loads(line)
                executed.add(e.get('signal_id', ''))
            except:
                pass

    # 构建结果配对（目前无闭仓PnL数据时用score做代理）
    outcomes = []
    for s in sigs:
        regime    = s.get('regime', '')
        direction = s.get('direction', '')
        score     = float(s.get('score', 0) or 0)
        valid     = s.get('valid', False)
        sig_id    = s.get('signal_id', '')

        if not regime or not direction or score < 100:
            continue

        key = f'{regime}_{direction}'
        outcomes.append({
            'key':       key,
            'regime':    regime,
            'direction': direction,
            'score':     score,
            'valid':     valid,
            'executed':  sig_id in executed,
            'ts':        float(s.get('ts', 0)),
            # 代理胜率：score≥140 且 valid=True 视为"质量信号"
            'quality':   score >= 140 and valid,
        })

    return outcomes


def analyze_regime_wr(outcomes: list) -> dict:
    """
    ② hermes-dojo核心：主动扫描每个体制/方向的质量率
    对比理论WR，发现偏差
    """
    by_key = defaultdict(list)
    for o in outcomes:
        by_key[o['key']].append(o)

    results = {}
    for key, samples in by_key.items():
        if len(samples) < MIN_SAMPLES:
            continue

        n_total   = len(samples)
        n_quality = sum(1 for s in samples if s['quality'])
        actual_wr = n_quality / n_total

        theoretical = THEORETICAL_WR.get(key, 0.55)  # 默认基准55%
        deviation   = actual_wr - theoretical
        deviation_pct = abs(deviation) / theoretical

        status = 'OK'
        if deviation_pct >= WR_DEVIATION_ADJUST:
            status = 'CRITICAL'
        elif deviation_pct >= WR_DEVIATION_WARN:
            status = 'WARN'

        results[key] = {
            'n':            n_total,
            'actual_wr':    round(actual_wr, 3),
            'theoretical':  theoretical,
            'deviation':    round(deviation, 3),
            'deviation_pct': round(deviation_pct * 100, 1),
            'status':       status,
            'avg_score':    round(sum(s['score'] for s in samples) / n_total, 1),
        }

    return results


def auto_adjust_thresholds(wr_analysis: dict) -> dict:
    """
    ② hermes-dojo自修炼：当偏差>15%时自动调整该体制的score门槛
    原则: WR偏高→降低门槛（放宽，增加信号）
         WR偏低→提高门槛（收紧，提升质量）
    """
    thresholds = {}
    if SCORE_THRESHOLD_PATH.exists():
        try:
            thresholds = json.loads(SCORE_THRESHOLD_PATH.read_text())
        except:
            pass

    adjustments = []
    for key, data in wr_analysis.items():
        if data['status'] != 'CRITICAL':
            continue

        current = thresholds.get(key, 135)  # 默认门槛135
        deviation = data['deviation']

        if deviation < 0:
            # 实际WR < 理论 → 提高门槛（更严格）
            new_threshold = min(current + SCORE_ADJUST_STEP, 160)
            direction_str = '↑提高'
        else:
            # 实际WR > 理论 → 降低门槛（更积极）
            new_threshold = max(current - SCORE_ADJUST_STEP, 120)
            direction_str = '↓降低'

        if new_threshold != current:
            thresholds[key] = new_threshold
            adjustments.append({
                'key':       key,
                'old':       current,
                'new':       new_threshold,
                'direction': direction_str,
                'reason':    f'实际WR={data["actual_wr"]:.1%} vs 理论={data["theoretical"]:.1%} 偏差={data["deviation_pct"]:.0f}%',
            })

    if adjustments:
        SCORE_THRESHOLD_PATH.write_text(json.dumps(thresholds, indent=2))

    return {'adjustments': adjustments, 'thresholds': thresholds}


def meta_reasoning_diagnosis(outcomes: list, wr_analysis: dict) -> dict:
    """
    ③ super-hermes元推理：检查评分逻辑是否退化
    问题: 梵天35维评分从不自问"这些维度是否过时"
    本函数: 分析哪些特征组合的信号质量在下滑
    """
    issues = []
    suggestions = []

    # 检查1: 总体valid率是否在下滑
    total = len(outcomes)
    valid_n = sum(1 for o in outcomes if o['valid'])
    valid_rate = valid_n / max(total, 1)

    if valid_rate < 0.3:
        issues.append(f'⚠️ valid率过低: {valid_rate:.0%} (近{LOOKBACK_DAYS}天{total}条信号只有{valid_n}条有效)')
        suggestions.append('检查dharma_data_bridge valid判断条件是否过严')

    # 检查2: 高分信号(≥155)的质量率是否低于预期
    high_score = [o for o in outcomes if o['score'] >= 155]
    if len(high_score) >= MIN_SAMPLES:
        high_quality = sum(1 for o in high_score if o['quality'])
        high_rate = high_quality / len(high_score)
        if high_rate < 0.5:
            issues.append(f'⚠️ score≥155信号质量率异常低: {high_rate:.0%} (n={len(high_score)})')
            suggestions.append('score≥155门槛可能过宽，建议提升至160或增加结构验证维度')

    # 检查3: BULL_TREND LONG信号在近期是否有系统性问题
    bull_long = [o for o in outcomes if o['key'] == 'BULL_TREND_LONG']
    if len(bull_long) >= MIN_SAMPLES:
        bl_quality = sum(1 for o in bull_long if o['quality'])
        bl_rate = bl_quality / len(bull_long)
        if bl_rate < 0.55:
            issues.append(f'⚠️ BULL_TREND LONG近期质量率: {bl_rate:.0%} (n={len(bull_long)}) < 55%基准')
            suggestions.append('BULL_TREND LONG信号可能受市场结构转变影响，考虑提高RSI_1H过滤阈值')

    # 检查4: 体制切换频率（switch_count过高=体制不稳定）
    try:
        rs = json.loads((BASE / 'data' / 'regime_state.json').read_text())
        high_switch = [(sym, d.get('switch_count_24h', 0))
                       for sym, d in rs.items()
                       if isinstance(d, dict) and int(d.get('switch_count_24h', 0)) > 5]
        if high_switch:
            syms = ', '.join(f'{s}({n}次)' for s, n in high_switch[:3])
            issues.append(f'⚠️ 体制切换频率过高: {syms}')
            suggestions.append('高频切换标的建议临时提升MIN_UPDATE_INTERVAL或降低评分权重')
    except:
        pass

    return {
        'total_signals': total,
        'valid_rate':    round(valid_rate, 3),
        'issues':        issues,
        'suggestions':   suggestions,
        'health_score':  max(0, 100 - len(issues) * 20),
    }


def run_full_diagnosis(push: bool = True) -> dict:
    """完整诊断 + 推送报告"""
    now = datetime.now(timezone.utc)
    print(f'\n{"="*55}')
    print(f'🩺 梵天策略医生 · {now.strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'{"="*55}')

    # 加载数据
    outcomes = load_signal_outcomes(LOOKBACK_DAYS)
    print(f'\n数据: 近{LOOKBACK_DAYS}天信号 {len(outcomes)}条')

    # ② 体制WR分析
    wr_analysis = analyze_regime_wr(outcomes)
    adj_result  = auto_adjust_thresholds(wr_analysis)

    print(f'\n── 体制WR分析 ─────────────────────────────────')
    for key, data in sorted(wr_analysis.items(), key=lambda x: -abs(x[1]['deviation_pct'])):
        icon = {'OK': '✅', 'WARN': '⚠️', 'CRITICAL': '🔴'}.get(data['status'], '?')
        print(f'  {icon} {key:25} 实={data["actual_wr"]:.1%} 理={data["theoretical"]:.1%} '
              f'偏差={data["deviation_pct"]:+.0f}% n={data["n"]}')

    if adj_result['adjustments']:
        print(f'\n── 自动调参 ({len(adj_result["adjustments"])}项) ──────────────────')
        for a in adj_result['adjustments']:
            print(f'  {a["key"]}: {a["old"]} {a["direction"]} {a["new"]} | {a["reason"]}')

    # ③ 元推理诊断
    meta = meta_reasoning_diagnosis(outcomes, wr_analysis)
    print(f'\n── 元推理诊断 ──────────────────────────────────')
    print(f'  评分系统健康度: {meta["health_score"]}/100')
    for issue in meta['issues']:
        print(f'  {issue}')
    for sug in meta['suggestions']:
        print(f'  💡 {sug}')

    # 汇总报告
    report = {
        'generated_at':  now.isoformat(),
        'lookback_days': LOOKBACK_DAYS,
        'total_signals': len(outcomes),
        'wr_analysis':   wr_analysis,
        'adjustments':   adj_result['adjustments'],
        'meta_diagnosis': meta,
    }
    DOCTOR_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # 推送（仅当有问题时）
    has_issues = (
        any(d['status'] != 'OK' for d in wr_analysis.values())
        or len(meta['issues']) > 0
        or len(adj_result['adjustments']) > 0
    )

    if push and has_issues:
        warn_keys = [k for k,d in wr_analysis.items() if d['status'] != 'OK']
        msg_lines = [
            f'🩺 梵天策略医生 | {now.strftime("%m-%d %H:%M UTC")}',
            f'健康度: {meta["health_score"]}/100 | 信号: {len(outcomes)}条',
            '',
        ]
        if adj_result['adjustments']:
            msg_lines.append(f'⚡ 自动调参 {len(adj_result["adjustments"])}项:')
            for a in adj_result['adjustments'][:3]:
                msg_lines.append(f'  {a["key"]}: score门槛 {a["old"]}{a["direction"]}{a["new"]}')
        if meta['issues']:
            msg_lines.append('')
            for issue in meta['issues'][:3]:
                msg_lines.append(issue)
        if meta['suggestions']:
            msg_lines.append('')
            for sug in meta['suggestions'][:2]:
                msg_lines.append(f'💡 {sug}')

        import subprocess
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', JARVIS_CHANNEL, '--to', JARVIS_TARGET,
             '--message', '\n'.join(msg_lines)],
            capture_output=True, timeout=10
        )
        print(f'\n✅ 诊断报告已推送')
    elif not has_issues:
        print(f'\nHEARTBEAT_OK — 所有体制WR在正常范围内')

    return report


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.dry_run:
        outcomes = load_signal_outcomes(LOOKBACK_DAYS)
        wr = analyze_regime_wr(outcomes)
        meta = meta_reasoning_diagnosis(outcomes, wr)
        print(f'Dry-run: {len(outcomes)}条信号 | 健康度={meta["health_score"]}/100')
        for k,d in wr.items():
            print(f'  {k}: 实={d["actual_wr"]:.1%} 偏差={d["deviation_pct"]:+.0f}% [{d["status"]}]')
    else:
        run_full_diagnosis(push=not args.no_push)

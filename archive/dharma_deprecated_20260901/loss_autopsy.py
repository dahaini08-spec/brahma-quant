"""
loss_autopsy.py · 失败信号自动解剖 v1.0
========================================
设计院 × 达摩院 · 2026-06-18

哲学：做错了改，不封禁。每次LOSS = 学习机会，不是封禁理由。

职责：
  每条 LOSS 信号结算后，自动分析「本次为什么失败」
  → 积累 failure_pattern.json
  → 达摩院定期分析 → 针对性调整权重（非封禁）

五大失败根因：
  1. REGIME_MISMATCH  — 体制判断延迟，入场时体制已切换
  2. GRADE_TRAP       — grade 70-80 危险陷阱（假结构）
  3. KEY_LEVEL_FAR    — 关键位 ob_dist>3%（D级，远离关键位入场）
  4. MACRO_HEADWIND   — 逆1D大趋势方向（宏观顺风/逆风）
  5. LOW_CONFLUENCE   — 多维度未共振（score 刚刚过门控，边缘信号）

调用方式：
  from dharma.loss_autopsy import autopsy
  result = autopsy(signal)   # signal 是 live_signals.jsonl 中的一条记录

输出写入：data/failure_pattern.jsonl（追加）
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "data"
FAILURE_LOG = DATA_DIR / "failure_pattern.jsonl"
REGIME_SWITCH_STATE = DATA_DIR / "regime_switch_state.json"


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def autopsy(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    对一条 LOSS 信号执行自动解剖分析

    Args:
        signal: live_signals.jsonl 中的完整信号记录

    Returns:
        {
          'signal_id': str,
          'primary_cause': str,      # 主要失败原因（最高权重）
          'causes': list,            # 所有检测到的失败原因
          'cause_scores': dict,      # 各原因置信度 0~1
          'recommendations': list,   # 针对性改进建议
          'lesson': str,             # 一句话教训
        }
    """
    if signal.get('outcome') not in ('LOSS', 'TIMEOUT_LOSS', 'SL', 'SL_BREACHED'):
        return {'skipped': True, 'reason': '非LOSS信号，跳过解剖'}

    causes = []
    cause_scores = {}

    # ── 检测1：体制错配 ──────────────────────────────────────────
    c1, s1 = _check_regime_mismatch(signal)
    if c1: causes.append(c1); cause_scores['REGIME_MISMATCH'] = s1

    # ── 检测2：grade 危险陷阱 ────────────────────────────────────
    c2, s2 = _check_grade_trap(signal)
    if c2: causes.append(c2); cause_scores['GRADE_TRAP'] = s2

    # ── 检测3：关键位过远 ────────────────────────────────────────
    c3, s3 = _check_key_level_far(signal)
    if c3: causes.append(c3); cause_scores['KEY_LEVEL_FAR'] = s3

    # ── 检测4：逆1D宏观趋势 ─────────────────────────────────────
    c4, s4 = _check_macro_headwind(signal)
    if c4: causes.append(c4); cause_scores['MACRO_HEADWIND'] = s4

    # ── 检测5：低共振边缘信号 ───────────────────────────────────
    c5, s5 = _check_low_confluence(signal)
    if c5: causes.append(c5); cause_scores['LOW_CONFLUENCE'] = s5

    # 主要原因（置信度最高的）
    primary = max(cause_scores, key=cause_scores.get) if cause_scores else 'UNKNOWN'
    primary_score = cause_scores.get(primary, 0)

    # 针对性建议
    recommendations = _generate_recommendations(cause_scores, signal)

    # 一句话教训
    lesson = _generate_lesson(primary, signal)

    result = {
        'signal_id':    signal.get('signal_id', signal.get('id', '?')),
        'symbol':       signal.get('symbol'),
        'direction':    signal.get('signal_dir', signal.get('direction')),
        'regime':       signal.get('regime'),
        'score':        signal.get('score', signal.get('confluence_score')),
        'grade':        signal.get('grade'),
        'outcome':      signal.get('outcome'),
        'pnl_pct':      signal.get('pnl_pct', signal.get('pnl')),
        'primary_cause': primary,
        'primary_conf':  round(primary_score, 2),
        'causes':        causes,
        'cause_scores':  {k: round(v, 2) for k, v in cause_scores.items()},
        'recommendations': recommendations,
        'lesson':        lesson,
        'analyzed_at':   datetime.now(timezone.utc).isoformat(),
    }

    _write_log(result)
    return result


# ══════════════════════════════════════════════════════════════
# 五大失败原因检测
# ══════════════════════════════════════════════════════════════

def _check_regime_mismatch(s: Dict) -> tuple:
    """检测1：体制判断延迟/错配"""
    regime = s.get('regime', '')
    direction = s.get('signal_dir', s.get('direction', ''))

    # 高风险组合（但不封禁，只是标记）
    HIGH_RISK = {
        ('BEAR_EARLY', 'LONG'), ('BULL_EARLY', 'SHORT'),
        ('CHOP_MID', 'LONG'),   ('CHOP_MID', 'SHORT'),
    }
    if (regime, direction) in HIGH_RISK:
        return (f'REGIME_MISMATCH: {regime}×{direction} 是低WR组合', 0.7)

    # 检查体制切换时间是否太近（信号生成时体制刚切换）
    try:
        rss = json.loads(REGIME_SWITCH_STATE.read_text())
        sym = s.get('symbol', '')
        sym_state = rss.get(sym, {})
        confirmed_at = sym_state.get('confirmed_at', 0)
        sig_time = s.get('created_at_ts', s.get('ts', 0)) or 0
        if sig_time and confirmed_at:
            # 如果信号在体制确认后1小时内生成，可能是「体制切换延迟」
            delta_h = (float(sig_time) - float(confirmed_at)) / 3600
            if 0 < delta_h < 1.0:
                return (f'REGIME_MISMATCH: 信号在体制确认后{delta_h:.1f}H内生成（体制可能还在稳定）', 0.5)
    except Exception:
        pass

    return (None, 0)


def _check_grade_trap(s: Dict) -> tuple:
    """检测2：grade 70-80 危险陷阱（数据证明WR=47%，比grade<50还差）"""
    _raw_grade = s.get('grade', 0) or 0
    # grade字段可能存为带emoji的字符串（如'🟡强'/'🟠极强'），需映射为数值
    _GRADE_MAP = {'神级': 100, '极强': 90, '🟠极强': 90, '强': 80, '🟡强': 80,
                  '次强': 70, '🟢次强': 70, '中': 60, '弱': 50, '极弱': 40}
    if isinstance(_raw_grade, str) and not _raw_grade.replace('.', '').replace('-', '').isdigit():
        _raw_grade = _GRADE_MAP.get(_raw_grade.strip(), 0)
    grade = float(_raw_grade)
    if 70 <= grade < 80:
        return (f'GRADE_TRAP: grade={grade:.0f} 处于危险段（70-80，WR=47%，假结构高发）', 0.8)
    if 50 <= grade < 60:
        return (f'GRADE_TRAP: grade={grade:.0f} 处于次危险段（50-60，WR=46.5%）', 0.6)
    return (None, 0)


def _check_key_level_far(s: Dict) -> tuple:
    """检测3：关键位过远（ob_dist > 3% = D级）"""
    ob_dist = float(s.get('ob_dist_pct', s.get('key_level_proximity', 99)) or 99)
    entry_src = s.get('entry_source', '')

    if ob_dist >= 3.0:
        return (f'KEY_LEVEL_FAR: ob_dist={ob_dist:.1f}% D级（>3%，远离OB入场，预期WR最低）', 0.75)
    if entry_src == 'FIB' and ob_dist >= 1.5:
        return (f'KEY_LEVEL_FAR: FIB入场+ob_dist={ob_dist:.1f}% 无有效OB支撑', 0.55)
    return (None, 0)


def _check_macro_headwind(s: Dict) -> tuple:
    """检测4：逆1D宏观趋势（顶级逆势做多/做空）"""
    # 暂时从信号字段推断（未来接入1D phase记录）
    regime = s.get('regime', '')
    direction = s.get('signal_dir', s.get('direction', ''))
    breakdown = s.get('breakdown', s.get('score_breakdown', {})) or {}

    # 如果有1D方向性惩罚记录
    if breakdown.get('_1d_direction_penalty'):
        penalty_str = breakdown['_1d_direction_penalty']
        return (f'MACRO_HEADWIND: {penalty_str}', 0.7)

    # 通过体制推断宏观逆势
    bear_regimes = {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY'}
    bull_regimes = {'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION'}
    if regime in bear_regimes and direction == 'LONG':
        return (f'MACRO_HEADWIND: 熊市体制({regime})中做多，宏观逆势', 0.65)
    if regime in bull_regimes and direction == 'SHORT':
        return (f'MACRO_HEADWIND: 牛市体制({regime})中做空，宏观逆势', 0.65)

    return (None, 0)


def _check_low_confluence(s: Dict) -> tuple:
    """检测5：低共振边缘信号（score刚过门控，多维度未充分共振）"""
    score = float(s.get('score', s.get('confluence_score', 0)) or 0)
    # 实盘门控约140，score在140~155之间是「边缘通过」
    if 100 <= score < 120:
        return (f'LOW_CONFLUENCE: score={score:.0f} 边缘通过门控（100-120区间，WR历史66.5%但不稳定）', 0.5)
    if score < 100:
        return (f'LOW_CONFLUENCE: score={score:.0f} 低于标准门控，属于试探性信号', 0.6)
    return (None, 0)


# ══════════════════════════════════════════════════════════════
# 建议生成
# ══════════════════════════════════════════════════════════════

def _generate_recommendations(cause_scores: Dict, s: Dict) -> List[str]:
    recs = []
    regime = s.get('regime', '')
    direction = s.get('signal_dir', s.get('direction', ''))

    if 'REGIME_MISMATCH' in cause_scores and cause_scores['REGIME_MISMATCH'] >= 0.6:
        recs.append(f'提升：{regime}×{direction} 动态门控已自动提高18分（v25.5），减少此类信号')

    if 'GRADE_TRAP' in cause_scores and cause_scores['GRADE_TRAP'] >= 0.7:
        recs.append('提升：grade 70-80 结构新鲜度验证（OB最近测试时间 + FVG填充比例）→ 待smc_engine升级')

    if 'KEY_LEVEL_FAR' in cause_scores and cause_scores['KEY_LEVEL_FAR'] >= 0.7:
        recs.append('提升：ob_dist>3% 时自动降分-10（入场位精度不足惩罚）→ 达摩院关键位验证积累')

    if 'MACRO_HEADWIND' in cause_scores and cause_scores['MACRO_HEADWIND'] >= 0.6:
        recs.append('提升：1D方向性门控已注入（v25.5），逆1D方向信号门控自动+12分')

    if 'LOW_CONFLUENCE' in cause_scores and cause_scores['LOW_CONFLUENCE'] >= 0.5:
        recs.append('提升：信号质量分级（S/A/B/C）待落地，边缘信号自动降仓位（非封禁）')

    if not recs:
        recs.append('持续积累实盘数据，达摩院下次训练时分析模式')

    return recs


def _generate_lesson(primary_cause: str, s: Dict) -> str:
    lessons = {
        'REGIME_MISMATCH': f"体制（{s.get('regime')}）是方向的基础，方向感与体制共振才是高质量信号",
        'GRADE_TRAP':       "grade 70-80 是假结构高发区——中等质量比低质量更危险，需更严格结构验证",
        'KEY_LEVEL_FAR':    "远离关键位入场 = 没有支撑的孤注一掷，OB/FVG精准区（<0.5%）才是入场锚",
        'MACRO_HEADWIND':   "逆1D大趋势方向是系统性亏损来源，需要1D+4H+1H三级协同才是真共振",
        'LOW_CONFLUENCE':   "边缘通过门控的信号是最大噪音来源，提升评分质量比降低门控更重要",
        'UNKNOWN':          "继续积累数据，模式尚不清晰",
    }
    return lessons.get(primary_cause, '持续学习，提升识别能力')


def _write_log(result: Dict):
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(FAILURE_LOG, 'a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 批量分析
# ══════════════════════════════════════════════════════════════

def analyze_all(min_recent: int = 50) -> Dict[str, Any]:
    """分析所有历史LOSS信号，输出系统性模式"""
    live_log = DATA_DIR.parent / 'dharma' / 'data' / 'live_signals.jsonl'
    signals = []
    if live_log.exists():
        with open(live_log) as f:
            for line in f:
                try: signals.append(json.loads(line))
                except: pass

    losses = [s for s in signals if s.get('outcome') in ('LOSS','TIMEOUT_LOSS')]
    print(f"分析 {len(losses)} 条LOSS信号...")

    pattern_count = {}
    for s in losses[-min_recent:]:
        result = autopsy(s)
        pc = result.get('primary_cause', 'UNKNOWN')
        pattern_count[pc] = pattern_count.get(pc, 0) + 1

    total = sum(pattern_count.values())
    print(f"\n=== 失败模式排行（最近{min_recent}条）===")
    for cause, cnt in sorted(pattern_count.items(), key=lambda x: -x[1]):
        pct = cnt/total*100 if total else 0
        print(f"  {cause:<22}: {cnt:3d}次 ({pct:.0f}%)")

    return {'pattern_count': pattern_count, 'total_analyzed': total}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='失败信号自动解剖')
    parser.add_argument('--all', action='store_true', help='分析所有历史LOSS')
    args = parser.parse_args()
    if args.all:
        analyze_all()
    else:
        print("用法: python3 dharma/loss_autopsy.py --all")

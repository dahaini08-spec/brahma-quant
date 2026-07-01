"""
key_level_validator.py · 达摩院关键位实盘验证模块 v1.0
======================================================
设计院 × 达摩院 · 2026-06-17

职责：
  所有关键位输出（OB/FVG/Swing/Entry/SL/TP）在生成后
  必须通过本模块统计验证，形成「关键位质量评分矩阵」

核心问题（本模块回答）：
  Q1: OB_4H 作为入场位，实际触发率和胜率如何？
  Q2: FVG 入场 vs OB 入场，哪个质量更高？
  Q3: 入场位距 OB 边沿越近，是否 WR 越高？
  Q4: swing_4h 作为 SL 基础，实际是否防住了？
  Q5: MTF 覆盖（4H>1H）的信号是否比纯1H质量更高？

数据流：
  brahma_core.calc_trade_params()
      ↓ 12个新字段（entry_source / ob_dist_pct / ob_top...）
  live_signals.jsonl（已有字段）
      ↓
  key_level_validator.analyze() ← 本模块
      ↓
  key_level_report.json（持久化统计）
      ↓
  dharma_runner Phase4（集成到训练流水线）

作者：设计院 · 达摩院 · 2026-06-17 v1.0
"""

import json
import time
import math
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

BASE_DIR   = Path(__file__).parent.parent
DHARMA_DIR = BASE_DIR / "dharma"
DATA_DIR   = DHARMA_DIR / "data"
REPORT_PATH = DATA_DIR / "key_level_report.json"
LIVE_LOG    = DATA_DIR / "live_signals.jsonl"

# ── 关键位质量门槛（达摩院标准）──────────────────────────────
OB_DIST_TIER = {
    'A': (0.0, 0.5),    # OB精准区间内，<0.5%
    'B': (0.5, 1.5),    # 靠近OB，0.5~1.5%
    'C': (1.5, 3.0),    # OB附近，1.5~3%
    'D': (3.0, 99.0),   # 远离OB或无OB
}

MIN_SAMPLES = 30  # 低于此样本量，统计结论标记 ⚠️ 参考级


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def analyze(min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """
    读取 live_signals.jsonl，生成关键位质量报告

    Returns:
        {
          'summary': {...},          # 总体摘要
          'entry_source_stats': ..., # 按入场来源（OB_4H/FVG/FIB）统计
          'ob_dist_tier_stats': ..., # 按 OB 距离分级统计
          'mtf_override_stats': ..., # MTF覆盖 vs 纯1H 对比
          'swing_sl_stats': ...,     # swing_4h SL 实际防守率
          'recommendations': [...],  # 设计院自动建议
          'generated_at': '...',
        }
    """
    signals = _load_signals()
    settled = [s for s in signals if s.get('outcome') in ('WIN', 'LOSS')]
    pending = [s for s in signals if s.get('outcome') not in ('WIN', 'LOSS')]

    report = {
        'total_signals':   len(signals),
        'settled_signals': len(settled),
        'pending_signals': len(pending),
        'has_kl_fields':   sum(1 for s in signals if s.get('entry_source')),
        'generated_at':    datetime.now(timezone.utc).isoformat(),
        'min_samples_threshold': min_samples,
    }

    # ── Q1/Q2: 按入场来源统计 WR ──────────────────────────────
    report['entry_source_stats'] = _stats_by_key(
        settled, 'entry_source',
        default='UNKNOWN', min_n=min_samples
    )

    # ── Q3: 按 OB 距离分级统计 ──────────────────────────────────
    # 先给每条信号打 ob_dist_tier 标签
    for s in settled:
        s['_ob_tier'] = _get_ob_tier(s.get('ob_dist_pct', 99))
    report['ob_dist_tier_stats'] = _stats_by_key(
        settled, '_ob_tier',
        default='D', min_n=min_samples,
        extra_fields=['ob_dist_pct']
    )

    # ── Q4: FVG vs 无FVG 对比 ────────────────────────────────────
    report['fvg_active_stats'] = _stats_by_key(
        settled, 'fvg_active',
        default=False, min_n=min_samples
    )

    # ── Q5: MTF 覆盖对比 ─────────────────────────────────────────
    report['mtf_override_stats'] = _stats_by_key(
        settled, 'mtf_override',
        default=False, min_n=min_samples
    )

    # ── swing SL 防守分析 ────────────────────────────────────────
    report['swing_sl_analysis'] = _swing_sl_analysis(settled, min_n=min_samples)

    # ── 关键位接近度 vs WR 分段 ──────────────────────────────────
    report['proximity_wr_curve'] = _proximity_wr_curve(settled, min_n=10)

    # ── 自动建议生成 ─────────────────────────────────────────────
    report['recommendations'] = _generate_recommendations(report, min_samples)

    # 持久化
    _save_report(report)
    return report


# ══════════════════════════════════════════════════════════════
# 内部函数
# ══════════════════════════════════════════════════════════════

def _load_signals() -> List[Dict]:
    if not LIVE_LOG.exists():
        return []
    signals = []
    with open(LIVE_LOG) as f:
        for line in f:
            try:
                s = json.loads(line)
                signals.append(s)
            except Exception:
                pass
    return signals


def _wr(entries: List[Dict]) -> Dict:
    """计算胜率统计"""
    wins   = sum(1 for e in entries if e.get('outcome') == 'WIN')
    losses = sum(1 for e in entries if e.get('outcome') == 'LOSS')
    n      = wins + losses
    if n == 0:
        return {'n': 0, 'wr': None, 'wins': 0, 'losses': 0, 'flag': '❌ 无数据'}
    wr = wins / n * 100
    pnl_vals = [e.get('pnl_pct', 0) for e in entries if e.get('pnl_pct') is not None]
    avg_pnl = round(sum(pnl_vals) / len(pnl_vals), 4) if pnl_vals else None
    flag = '🏆' if wr >= 65 and n >= 100 else ('✅' if wr >= 55 else ('⚠️' if n < MIN_SAMPLES else '❌'))
    return {
        'n':       n,
        'wr':      round(wr, 1),
        'wins':    wins,
        'losses':  losses,
        'avg_pnl': avg_pnl,
        'flag':    flag,
        'reliable': n >= MIN_SAMPLES,
    }


def _stats_by_key(settled: List[Dict], key: str,
                  default=None, min_n: int = 30,
                  extra_fields: List[str] = None) -> Dict:
    """按某字段分组计算WR"""
    groups: Dict[str, List] = {}
    for s in settled:
        v = s.get(key, default)
        v = str(v)  # 统一字符串键
        groups.setdefault(v, []).append(s)

    result = {}
    for grp, items in sorted(groups.items()):
        stat = _wr(items)
        if extra_fields:
            for ef in extra_fields:
                vals = [s.get(ef) for s in items if s.get(ef) is not None]
                stat[f'avg_{ef}'] = round(sum(vals)/len(vals), 3) if vals else None
        result[grp] = stat
    return result


def _get_ob_tier(ob_dist_pct: float) -> str:
    if ob_dist_pct is None:
        return 'D'
    for tier, (lo, hi) in OB_DIST_TIER.items():
        if lo <= ob_dist_pct < hi:
            return tier
    return 'D'


def _swing_sl_analysis(settled: List[Dict], min_n: int = 30) -> Dict:
    """
    分析 swing_4h 作为 SL 的实际防守效果
    关键指标：「入场后价格是否穿越了 swing 点再回来」
    目前版本：统计使用 swing_4h SL 的信号 WR vs 其他 SL 类型
    """
    sw_signals    = [s for s in settled if s.get('sl_basis', '').startswith('swing')]
    nonsw_signals = [s for s in settled if not s.get('sl_basis', '').startswith('swing')]
    return {
        'swing_4h_sl':    _wr(sw_signals),
        'other_sl':       _wr(nonsw_signals),
        'note': '达摩院 swing SL 实盘WR vs 其他SL类型对比'
    }


def _proximity_wr_curve(settled: List[Dict], min_n: int = 10) -> List[Dict]:
    """
    关键位接近度（ob_dist_pct）分段 WR 曲线
    用于验证「距OB越近WR越高」假设
    """
    buckets = [(0,0.5),(0.5,1),(1,2),(2,3),(3,5),(5,99)]
    result = []
    for lo, hi in buckets:
        items = [s for s in settled
                 if lo <= (s.get('ob_dist_pct') or s.get('key_level_proximity') or 99) < hi]
        stat = _wr(items)
        stat['ob_dist_range'] = f'{lo}~{hi}%'
        result.append(stat)
    return result


def _generate_recommendations(report: Dict, min_samples: int) -> List[str]:
    """基于统计结果生成达摩院自动建议"""
    recs = []
    es = report.get('entry_source_stats', {})

    # 入场来源对比
    ob4h_wr = es.get('OB_4H', {}).get('wr')
    fib_wr  = es.get('FIB', {}).get('wr')
    fvg_wr  = es.get('FVG', {}).get('wr')
    ob4h_n  = es.get('OB_4H', {}).get('n', 0)
    fib_n   = es.get('FIB', {}).get('n', 0)

    if ob4h_wr and fib_wr and ob4h_n >= min_samples and fib_n >= min_samples:
        if ob4h_wr > fib_wr + 5:
            recs.append(f"✅ OB_4H入场(WR={ob4h_wr}%) 显著优于 FIB入场(WR={fib_wr}%)，"
                        f"建议提高OB_4H触发率奖励 +2~3分")
        elif fib_wr > ob4h_wr + 5:
            recs.append(f"⚠️ FIB入场(WR={fib_wr}%) 优于 OB_4H入场(WR={ob4h_wr}%)，"
                        f"建议检查OB识别逻辑是否有误差")

    # OB 距离分级建议
    tier_stats = report.get('ob_dist_tier_stats', {})
    tier_a = tier_stats.get('A', {})
    tier_d = tier_stats.get('D', {})
    if tier_a.get('wr') and tier_d.get('wr') and tier_a.get('n', 0) >= min_samples:
        if tier_a['wr'] > tier_d.get('wr', 50) + 8:
            recs.append(f"✅ OB精准区(A级,<0.5%,WR={tier_a['wr']}%) >> "
                        f"远离OB(D级,WR={tier_d.get('wr','?')}%)，"
                        f"建议在OB距离<0.5%时额外加分 +3")

    # MTF 覆盖建议
    mtf = report.get('mtf_override_stats', {})
    mtf_true  = mtf.get('True', {})
    mtf_false = mtf.get('False', {})
    if (mtf_true.get('wr') and mtf_false.get('wr') and
        mtf_true.get('n', 0) >= min_samples and mtf_false.get('n', 0) >= min_samples):
        diff = mtf_true['wr'] - mtf_false['wr']
        if diff > 5:
            recs.append(f"✅ MTF覆盖信号WR={mtf_true['wr']}% 高于纯1H WR={mtf_false['wr']}% "
                        f"(+{diff:.1f}pp)，建议激活MTF覆盖加分 +2")
        elif diff < -5:
            recs.append(f"❌ MTF覆盖信号WR={mtf_true['wr']}% 低于纯1H WR={mtf_false['wr']}% "
                        f"({diff:.1f}pp)，建议检查4H OB识别精度")

    # FVG 建议
    fvg_stats = report.get('fvg_active_stats', {})
    fvg_true  = fvg_stats.get('True', {})
    fvg_false = fvg_stats.get('False', {})
    if fvg_true.get('n', 0) >= min_samples and fvg_false.get('n', 0) >= min_samples:
        if fvg_true.get('wr', 0) > fvg_false.get('wr', 0) + 5:
            recs.append(f"✅ FVG激活时WR={fvg_true['wr']}% 高于无FVG WR={fvg_false['wr']}%，"
                        f"建议维持FVG评分奖励")

    # 数据不足时的通用建议
    if report.get('has_kl_fields', 0) < 50:
        recs.append(f"⚠️ 关键位字段样本仅{report.get('has_kl_fields',0)}条，"
                    f"需积累至n≥{min_samples}条才有统计意义，当前仅供观察")

    if not recs:
        recs.append("ℹ️ 暂无显著发现，继续积累实盘数据")

    return recs


def _save_report(report: Dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )


def print_report(report: Dict = None):
    """打印人类可读报告"""
    if report is None:
        if REPORT_PATH.exists():
            report = json.loads(REPORT_PATH.read_text())
        else:
            report = analyze()

    print(f"\n{'='*60}")
    print(f"达摩院关键位实盘验证报告 · {report.get('generated_at','?')[:19]} UTC")
    print(f"{'='*60}")
    print(f"总信号: {report['total_signals']}  "
          f"已结算: {report['settled_signals']}  "
          f"含关键位字段: {report.get('has_kl_fields',0)}")

    print(f"\n── 入场来源WR对比 ──")
    for src, stat in report.get('entry_source_stats', {}).items():
        n = stat.get('n', 0)
        wr = stat.get('wr', 'N/A')
        flag = stat.get('flag', '')
        rel = '(铁证)' if stat.get('reliable') else f'(样本n={n}⚠️)'
        print(f"  {src:12s}: WR={wr}%  n={n}  {flag}  {rel}")

    print(f"\n── OB距离分级WR曲线 ──")
    for item in report.get('proximity_wr_curve', []):
        rng = item.get('ob_dist_range', '?')
        n = item.get('n', 0)
        wr = item.get('wr', 'N/A')
        flag = item.get('flag', '')
        print(f"  {rng:8s}: WR={wr}%  n={n}  {flag}")

    print(f"\n── MTF覆盖对比 ──")
    for k, stat in report.get('mtf_override_stats', {}).items():
        label = '4H覆盖' if k == 'True' else '纯1H'
        print(f"  {label}: WR={stat.get('wr','N/A')}%  n={stat.get('n',0)}  {stat.get('flag','')}")

    print(f"\n── 达摩院自动建议 ──")
    for rec in report.get('recommendations', []):
        print(f"  {rec}")
    print()


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='达摩院关键位实盘验证')
    parser.add_argument('--analyze', action='store_true', help='运行完整分析')
    parser.add_argument('--report',  action='store_true', help='打印最新报告')
    parser.add_argument('--min-samples', type=int, default=MIN_SAMPLES,
                        help=f'最小样本量门槛（默认{MIN_SAMPLES}）')
    args = parser.parse_args()

    if args.analyze or (not args.report):
        rep = analyze(min_samples=args.min_samples)
        print_report(rep)
    elif args.report:
        print_report()

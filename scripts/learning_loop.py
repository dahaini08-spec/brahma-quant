#!/usr/bin/env python3
"""
learning_loop.py — C-3 议会蒸馏闭环（backfiller + 周报 + 上下文包）
封印 2026-09-30 苏摩111

职责:
  1. backfill_shadow_verdicts(): 把纸面结算结果 (wuqu_paper_settled.jsonl) join 回
     llm_council_shadow_log.jsonl 的 outcome/verdict_correct 字段（仅写 verdict 非 null 行）
  2. weekly_report(): 8分向/中立样本统计 + 风险分级WR（0.9±/0.7±/0.5±）
  3. council_context(): 生成结构化教训包，注入 LLM 复盘 prompt（artifact，零API成本）
  4. council_package(): 输出当前包到 stdout

Join 键: symbol + signal_dir + score(±1.0) + open_ts距shadows≤90min
证据守则（P0-3教训）:
  - 无匹配 = null，不猜、不近似、不伪造
  - 8分向（score7-9/final_adj±2）为有效裁决样本，作为教训来源
  - 灰区（0.6-0.8）不输出教训，只累计

运行: cron每周一 03:50 UTC（独立低频任务）
接入位置: supercronic crontab; council_context() 供 daily_review_llm.py 引用
"""
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
SHADOW_LOG = DATA / 'llm_council_shadow_log.jsonl'
SETTLE_LOG = DATA / 'wuqu_paper_settled.jsonl'
REPORT     = DATA / 'distill_weekly_report.json'
PKG        = DATA / 'council_context_package.json'

SCORE_TOL = 1.0      # score 匹配容差
TIME_TOL  = 90 * 60  # 90分钟（秒）
STRONG_ADJ = 0.9     # |final_adj| ≥ 0.9 视为8分向
MILD_ADJ   = 0.7
GRAY_LO    = 0.6


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '')).timestamp()
    except Exception:
        return None


def _to_ts(v):
    """兼容 epoch float 与 ISO 字符串两种时间形态"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return _ts(v)


def load_shadow():
    recs = []
    if not SHADOW_LOG.exists():
        return recs
    for line in SHADOW_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    return recs


def load_settled():
    recs = []
    if not SETTLE_LOG.exists():
        return recs
    for line in SETTLE_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if d.get('signal_id') and d.get('result'):
                recs.append(d)
        except Exception:
            continue
    return recs


def backfill_shadow_verdicts() -> dict:
    """把纸面结算回填到 shadow log（正向闭环核心）"""
    shadow = load_shadow()
    settled = load_settled()

    # settled 索引: (symbol, dir) -> list
    idx = {}
    for w in settled:
        key = (w['symbol'], w.get('signal_dir', ''))
        idx.setdefault(key, []).append(w)

    updated = unmatched = already = no_verdict = 0
    for s in shadow:
        if not s.get('verdict'):
            no_verdict += 1
            continue
        if s.get('outcome') is not None:
            already += 1
            continue
        st = _ts(s.get('ts'))
        if st is None:
            unmatched += 1
            continue
        try:
            sc = float(s.get('score'))
        except (TypeError, ValueError):
            unmatched += 1
            continue
        hit = None
        for w in idx.get((s.get('symbol'), s.get('direction')), []):
            try:
                wsc = float(w.get('score'))
            except (TypeError, ValueError):
                continue
            if abs(wsc - sc) <= SCORE_TOL:
                wts = _to_ts(w.get('open_ts'))
                if wts and abs(wts - st) <= TIME_TOL:
                    hit = w
                    break
        if hit is None:
            unmatched += 1
            continue
        # 证据守则: 记录匹配与结论，但不捏造 verdict_correct 以外的字段
        s['outcome'] = hit['result']
        s['actual_pnl_pct'] = hit.get('pnl_pct')
        verdict = s.get('verdict', '')
        correct = None
        if verdict == 'RISK_LOW':
            correct = 1 if hit['result'] in ('WIN', 'TP1', 'TP2') else 0
        elif verdict in ('RISK_MED', 'RISK_HIGH'):
            correct = 0 if hit['result'] in ('WIN', 'TP1', 'TP2') else 1
        s['verdict_correct'] = correct
        s['match_method'] = 'sym+dir+score±1+90min'
        s['match_ts'] = datetime.now(timezone.utc).isoformat()
        updated += 1

    if updated:
        # 原子写
        tmp = SHADOW_LOG.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            for s in shadow:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        tmp.replace(SHADOW_LOG)

    return {
        'total_shadow': len(shadow),
        'with_verdict': len(shadow) - no_verdict,
        'backfilled_now': updated,
        'already_had': already,
        'unmatched': unmatched,
        'no_verdict_null': no_verdict,
    }


def weekly_report() -> dict:
    """8分向统计 + 灰区计数 + 风险分级WR"""
    shadow = load_shadow()
    strong, gray, total_v = [], 0, 0
    risk_wr = {'RISK_LOW': {'n': 0, 'w': 0}, 'RISK_MED': {'n': 0, 'w': 0}, 'RISK_HIGH': {'n': 0, 'w': 0}}
    for s in shadow:
        v = s.get('verdict')
        if not v:
            continue
        total_v += 1
        if v in risk_wr and s.get('outcome'):
            risk_wr[v]['n'] += 1
            if s['outcome'] in ('WIN', 'TP1', 'TP2'):
                risk_wr[v]['w'] += 1
        adj = s.get('final_adj') or 0
        a = abs(adj)
        if a >= STRONG_ADJ and s.get('verdict_correct') is not None:
            strong.append(s)
        elif GRAY_LO <= a < STRONG_ADJ:
            gray += 1
    acc = sum(1 for s in strong if s['verdict_correct'] == 1) if strong else 0
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'shadow_with_verdict': total_v,
        'strong_signals': len(strong),
        'strong_accuracy': round(acc / len(strong), 4) if strong else None,
        'gray_zone': gray,
        'risk_grade_wr': {
            k: {'n': v['n'], 'wr': round(v['w'] / v['n'], 4) if v['n'] else None}
            for k, v in risk_wr.items()
        },
    }


def council_context(limit=8) -> str:
    """生成结构化教训包文本（注入LLM复盘prompt，零API成本）"""
    if not PKG.exists():
        return ''
    try:
        pkg = json.loads(PKG.read_text())
    except Exception:
        return ''
    lessons = pkg.get('lessons', [])
    if not lessons:
        return ''
    lines = ['议会教训包（结构化蒸馏，供复盘参考）:']
    for l in lessons[:limit]:
        lines.append(f"- [{l['lesson_type']}] {l['lesson']} (证据: {l['evidence']})")
    return '\n'.join(lines)


def build_package(report: dict) -> dict:
    """从强信号样本构建结构化教训包"""
    shadow = load_shadow()
    lessons = []
    # 按symbol聚合强信号教训
    by_sym = {}
    for s in shadow:
        if s.get('verdict_correct') is None or (abs(s.get('final_adj') or 0) < STRONG_ADJ):
            continue
        sym = s.get('symbol', '?')
        by_sym.setdefault(sym, []).append(s)
    for sym, arr in by_sym.items():
        n = len(arr)
        ok = sum(1 for a in arr if a['verdict_correct'] == 1)
        if n < 2:
            continue
        wr = ok / n
        verdict = arr[0].get('verdict', '?')
        if wr >= 0.7:
            lesson = f"{sym} {verdict}裁决可信（{n}次样本WR={wr:.0%}），该风险分级可继续采纳"
        elif wr <= 0.4:
            lesson = f"{sym} {verdict}裁决不可信（{n}次样本WR={wr:.0%}），需降权或人工复核"
        else:
            lesson = f"{sym} {verdict}裁决中等（{n}次样本WR={wr:.0%}），保持观察"
        lessons.append({
            'lesson_type': 'verdict_calibration',
            'lesson': lesson,
            'evidence': f"n={n} wr={wr:.2f} window=正向backfill",
            'source': 'learning_loop',
        })
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'report': report,
        'lessons': lessons,
        'policy': {
            'min_strong_adj': STRONG_ADJ,
            'gray_zone_note': '灰区不计入教训，只累计',
            'evidence_rule': '无匹配=null，不猜不近似（P0-3教训）',
        },
    }


def main():
    args = sys.argv[1:]
    stats = backfill_shadow_verdicts()
    report = weekly_report()
    pkg = build_package(report)

    # 原子写
    tmp = PKG.with_suffix('.tmp')
    tmp.write_text(json.dumps(pkg, ensure_ascii=False, indent=2))
    tmp.replace(PKG)

    tmp = REPORT.with_suffix('.tmp')
    tmp.write_text(json.dumps({'backfill': stats, 'report': report}, ensure_ascii=False, indent=2))
    tmp.replace(REPORT)

    print(json.dumps({'backfill': stats, 'report': report}, ensure_ascii=False, indent=2))

    if '--package' in args:
        print('\n--- council_context_package.json ---')
        print(json.dumps(pkg, ensure_ascii=False, indent=2))
    if '--context' in args:
        print('\n--- council_context() ---')
        print(council_context())
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""
ic_tracker.py — 维度IC（信息系数）滚动追踪
设计院 2026-06-30 | 补齐与顶级量化差距路径

IC = 维度分数 与 最终方向结果 的Spearman秩相关系数
IC越高 = 该维度对方向预测越有效
IC接近0 = 该维度是噪音，应降权

当前状态：维度权重全部人工固定 → 目标：IC加权动态调整
进化路径：
  Phase1（当前）：记录每个维度的分数+结果，计算IC
  Phase2（50条后）：用IC自动调整维度权重
  Phase3（200条后）：XGBoost接管，维度作为特征
"""

from typing import Any

import json, os, time
import numpy as np
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
IC_STATE_PATH  = BASE / 'data' / 'ic_tracker_state.json'
LOG_PATH       = BASE / 'data' / 'live_signal_log.jsonl'

# 最小样本量：低于此值IC不可信
MIN_IC_SAMPLE = 20

# 维度名称映射（brahma_core中的breakdown key）
DIM_MAP = {
    'D01': 'trend_structure',
    'D02': 'momentum_4h',
    'D03': 'rsi_divergence',
    'D04': 'volume_confirm',
    'D05': 'vol_energy',
    'D06': 'order_flow',
    'D07': 'multi_tf_align',
    'D08': 'pattern_recognition',
    'D09': 'range_engine',
    'D10': 'macro_sentiment',
    'D11': 'whale_tracker',
    'D12': 'cross_market',
    'D13': 'microstructure',
    'D14': 'xgboost_ml',
    'D15': 'nlp_sentiment',
}

WIN_OUTCOMES  = ('TP1','TP2','TP3','WIN','HIT_TP1','HIT_TP2','MISS_WIN')
LOSS_OUTCOMES = ('SL','LOSS','SL_BREACHED','MISS_LOSS')


def load_signal_data() -> list[dict]:
    """读取有breakdown且有方向结果的信号"""
    records = []
    if not LOG_PATH.exists():
        return records
    for line in open(LOG_PATH):
        line = line.strip()
        if not line: continue
        try:
            s = json.loads(line)
        except Exception as _e: continue
        outcome = s.get('outcome','')
        if outcome not in WIN_OUTCOMES and outcome not in LOSS_OUTCOMES:
            continue
        bd = s.get('confluence',{}).get('breakdown',{})
        if not bd: bd = s.get('_breakdown', {})
        if not bd: bd = s.get('breakdown',{})
        if not bd: continue
        label = 1 if outcome in WIN_OUTCOMES else 0
        records.append({
            'signal_id': s.get('signal_id',''),
            'regime':    s.get('regime',''),
            'direction': s.get('direction','') or s.get('signal_dir',''),
            'label':     label,
            'score':     float(s.get('score',0) or 0),
            'breakdown': bd,
            'ts':        s.get('ts',0),
        })
    return records


def calc_ic(dim_scores: list, labels: list) -> float:
    """Spearman秩相关系数（IC）"""
    if len(dim_scores) < MIN_IC_SAMPLE:
        return None
    x = np.array(dim_scores, dtype=float)
    y = np.array(labels, dtype=float)
    # Spearman
    from scipy.stats import spearmanr
    try:
        corr, pval = spearmanr(x, y)
        return round(float(corr), 4) if not np.isnan(corr) else None
    except Exception:
        # 手算rank correlation
        def rank(a) -> Any:
            """rank"""
            tmp = sorted(enumerate(a), key=lambda x: x[1])
            r = np.zeros(len(a))
            for rank_i, (orig_i, _) in enumerate(tmp):
                r[orig_i] = rank_i + 1
            return r
        rx = rank(x); ry = rank(y)
        n = len(rx)
        d2 = np.sum((rx - ry)**2)
        return round(1 - 6*d2/(n*(n**2-1)), 4)


def compute_all_ic() -> dict:
    """
    计算所有维度的IC（按体制分组）
    返回：{regime: {dim_key: ic_value}}
    """
    records = load_signal_data()
    pass  # [静默]

    # 按体制分组
    by_regime = defaultdict(list)
    for r in records:
        by_regime[r['regime']].append(r)
    by_regime['ALL'] = records  # 全局IC

    ic_results = {}
    for regime, recs in by_regime.items():
        # 收集各维度分数
        dim_data = defaultdict(lambda: {'scores':[], 'labels':[]})
        for rec in recs:
            label = rec['label']
            for dim_key, val in rec['breakdown'].items():
                try:
                    # 清洗值：提取数字部分，处理中文/emoji/复合字符串
                    import re as _re_ic
                    _str_val = str(val)
                    # 提取第一个浮点数（正负均可）
                    _match = _re_ic.search(r'-?\d+\.?\d*', _str_val)
                    if _match:
                        score_val = float(_match.group())
                    else:
                        continue  # 无数字的值跳过
                    dim_data[dim_key]['scores'].append(score_val)
                    dim_data[dim_key]['labels'].append(label)
                except Exception as e:
                    import sys as _sys_ep; print(f"[ic_tracker] L129 dim_key={dim_key} val={val!r} err={e}", file=_sys_ep.stderr)
                    continue  # 跳过坏数据，不中断循环
        # 总分IC
        total_scores = [r['score'] for r in recs]
        total_labels = [r['label'] for r in recs]

        regime_ic = {'_total_n': len(recs)}
        if len(total_scores) >= MIN_IC_SAMPLE:
            regime_ic['_total_score_ic'] = calc_ic(total_scores, total_labels)

        for dim_key, data in dim_data.items():
            if len(data['scores']) >= MIN_IC_SAMPLE:
                ic = calc_ic(data['scores'], data['labels'])
                if ic is not None:
                    regime_ic[dim_key] = ic

        if len(regime_ic) > 1:
            ic_results[regime] = regime_ic

    return ic_results


def save_ic_state(ic_results: dict) -> Any:
    """保存ic state"""
    IC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'ic_by_regime': ic_results,
    }
    IC_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return state


def load_ic_state() -> dict:
    """加载ic state"""
    if not IC_STATE_PATH.exists():
        return {}
    try:
        return json.loads(IC_STATE_PATH.read_text())
    except Exception as _e: return {}


def get_dim_weight_adjust(dim_key: str, regime: str = 'ALL') -> float:
    """
    根据IC返回维度权重调整建议
    IC > 0.1  → 正向有效，权重+
    IC < -0.1 → 反向有效（反转），权重-
    |IC| < 0.1 → 噪音，权重削减50%
    """
    state = load_ic_state()
    ic_by_regime = state.get('ic_by_regime', {})

    ic = None
    for r in [regime, 'ALL']:
        if r in ic_by_regime and dim_key in ic_by_regime[r]:
            ic = ic_by_regime[r][dim_key]
            break

    if ic is None:
        return 1.0  # 无数据，保持原权重

    if ic > 0.15:   return 1.3   # 高IC，增权
    elif ic > 0.05: return 1.1   # 中IC，轻增
    elif ic < -0.15: return 0.5  # 负IC，强减权
    elif ic < -0.05: return 0.7  # 负IC，减权
    else:            return 0.8  # 噪音，轻减权


def report() -> str:
    """生成IC报告"""
    ic_results = compute_all_ic()
    save_ic_state(ic_results)

    lines = ['=== 维度IC报告 ===', '']
    for regime, ics in ic_results.items():
        n = ics.get('_total_n', 0)
        total_ic = ics.get('_total_score_ic', 'N/A')
        lines.append(f'【{regime}】 n={n} 总分IC={total_ic}')

        dim_ics = {k:v for k,v in ics.items()
                   if not k.startswith('_') and isinstance(v, float)}
        sorted_dims = sorted(dim_ics.items(), key=lambda x: -abs(x[1]))
        for dim, ic in sorted_dims[:10]:
            flag = '🔥有效' if abs(ic)>0.15 else ('⚠️弱' if abs(ic)>0.05 else '➖噪音')
            sign = '正向' if ic > 0 else '反向'
            lines.append(f'  {dim:<35} IC={ic:+.4f}  {sign} {flag}')
        lines.append('')
    return '\n'.join(lines)



# ── L3 IC过滤：learning_log教训周审门 [防谎P1 2026-09-28 苏摩111] ──────────
# AI教训（settler pending_ic）不是立刻进方仓记忆——等信号结算累积后，
# 用该批WR对应的真实成交结果算「教训与结果的IC一致性」：WR≥55%=APPROVE，<45%=REJECT。
# APPROVE教训才进SFT数据集（build_sft_dataset.py 只吃ic_verdict=APPROVE）。
IC_APPROVE_WR = 0.55
IC_REJECT_WR  = 0.45
IC_MIN_N      = 8  # 样本不足=继续PENDING（fail-closed，不许小样本污染）


def review_pending_lessons(lessons: list) -> int:
    """批量裁决pending_ic教训。输入learning_log条目列表（含wr/n_total）。
    更新ic_verdict: PENDING→APPROVE/REJECT。返回裁决数。
    规则（fail-closed）：n_total<IC_MIN_N→PENDING；wr≥APPROVE→APPROVE；wr≤REJECT→REJECT；中间→PENDING。
    """
    reviewed = 0
    for e in lessons:
        if e.get('ic_verdict') != 'PENDING':
            continue
        wr = e.get('wr', 0)
        n  = e.get('n_total', 0)
        if not isinstance(wr, (int, float)) or not isinstance(n, int) or n < IC_MIN_N:
            continue  # 样本不足=继续PENDING
        if wr >= IC_APPROVE_WR:
            e['ic_verdict'] = 'APPROVE'
            e['ic_reviewed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            reviewed += 1
        elif wr <= IC_REJECT_WR:
            e['ic_verdict'] = 'REJECT'
            e['ic_reviewed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            reviewed += 1
    return reviewed


def review_learning_log() -> dict:
    """对learning_log.jsonl跑周审门（幂等：只裁决PENDING）。"""
    from pathlib import Path as _P
    p = _P(__file__).parent.parent / 'data' / 'learning_log.jsonl'
    if not p.exists():
        return {'reviewed': 0, 'total': 0}
    entries = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    reviewed = review_pending_lessons(entries)
    # 原子写回（tmp+replace）
    tmp = p.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    tmp.replace(p)
    return {'reviewed': reviewed, 'total': len(entries)}


if __name__ == '__main__':
    print(report())

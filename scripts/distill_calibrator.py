#!/usr/bin/env python3
"""
distill_calibrator.py — C线校准蒸馏器（方案C：统计蒸馏）
封印 2026-09-30 苏摩111

职责:
  把历史大样本结局蒸馏成 P(win) 校准概率模型（纯numpy逻辑回归，零新增依赖）
  老师 = 结算账本（fangcang 4h 全家桶，目前唯一大样本带结局语料）
  学生 = 特征向量 -> P(genuine_breakout) 的逻辑回归

铁律:
  Shadow模式: 只产工件和日志，永不进交易决策链（A/B分离）
  升权需苏摩111二次批准；升权前只做shadow评分与离线评估
  live_signal_log 与结算库 overlap=0（P0-3账本闭环修复后，同一管线可重训live特征版）

数据:
  X = data/fangcang_{sym}_4h.json（44文件 ~7452条，挤压突破特征）
  y = is_genuine_breakout == 'True'（胜率代理标签）
  切分: 按ts_burst时序排序，前70%训练 / 后30%测试（无未来函数泄漏）

CLI:
  python3 scripts/distill_calibrator.py                # 训练+评估+落盘工件
  python3 scripts/distill_calibrator.py --score '{"squeeze_bars":12,...}'
  python3 scripts/distill_calibrator.py --info         # 只读现有工件

接入位置: 独立工件 data/distill_calibrator_v1.json（shadow，未接线）
"""
import json, glob, math, sys, time
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
OUT  = DATA / 'distill_calibrator_v1.json'

FEATURES = [
    'log_sq_bars',     # log1p(squeeze_bars)
    'min_bb_width',
    'rsi_c',           # 方向对称化RSI: (rsi-50)/50 * sgn
    'log_vol_peak',    # log1p(vol_ratio_peak)
    'burst_atr_mult',
    'is_up',
    'rsi_x_vol',       # 交互项
]

def _sgn(direction: str) -> int:
    return 1 if str(direction).upper() == 'UP' else -1

def _feat(r: dict):
    try:
        sq  = float(r['squeeze_bars'])
        bbw = float(r['min_bb_width'])
        rsi = float(r['rsi_at_burst'])
        vol = float(r['vol_ratio_peak'])
        am  = float(r['burst_atr_mult'])
        s   = _sgn(r.get('direction', 'UP'))
        rsi_c = (rsi - 50.0) / 50.0 * s
        return [
            math.log1p(max(sq, 0.0)),
            bbw,
            rsi_c,
            math.log1p(max(vol, 0.0)),
            am,
            1.0 if s > 0 else 0.0,
            rsi_c * math.log1p(max(vol, 0.0)),
        ]
    except (KeyError, TypeError, ValueError):
        return None

def _tskey(r: dict):
    # ts_burst 字符串排序（取前19字符避免时区偏移干扰）
    return str(r.get('ts_burst', ''))[:19]

def load_corpus():
    X, y, rows = [], [], []
    files_ok = files_skip = 0
    for f in sorted(glob.glob(str(DATA / 'fangcang_*_4h.json'))):
        try:
            recs = json.load(open(f))
        except Exception:
            files_skip += 1
            continue
        if not isinstance(recs, list):
            files_skip += 1
            continue
        ok = False
        for r in recs:
            lab = r.get('is_genuine_breakout')
            if lab in (None, 'None'):
                continue
            fx = _feat(r)
            if fx is None:
                continue
            X.append(fx)
            y.append(1 if str(lab) == 'True' else 0)
            rows.append(r)
            ok = True
        if ok:
            files_ok += 1
    return np.array(X, dtype=float), np.array(y, dtype=float), rows, files_ok, files_skip

def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))

def fit_logreg(X, y, l2=1e-3, lr=0.5, epochs=3000):
    n, d = X.shape
    w = np.zeros(d); b = 0.0
    for _ in range(epochs):
        p = sigmoid(X @ w + b)
        gw = X.T @ (p - y) / n + l2 * w
        gb = float(np.mean(p - y))
        w -= lr * gw
        b -= lr * gb
    return w, b

def auc(y, p):
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind='mergesort')
    ranks = np.empty(len(allv), dtype=float)
    ranks[order] = np.arange(1, len(allv) + 1)
    # 并列值取平均秩
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (ranks[order[i]] + ranks[order[j]]) / 2.0
        i = j + 1
    r_pos = ranks[:len(pos)]
    return float((r_pos.sum() - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)))

def brier(y, p):
    return float(np.mean((p - y) ** 2))

def calibration_buckets(y, p, n=8):
    out = []
    edges = np.linspace(0, 1, n + 1)
    for i in range(n):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < n - 1 else p <= edges[i + 1])
        if m.sum() == 0:
            continue
        out.append({
            'bucket': f'{edges[i]:.2f}-{edges[i+1]:.2f}',
            'n': int(m.sum()),
            'mean_p': round(float(p[m].mean()), 4),
            'actual_wr': round(float(y[m].mean()), 4),
            'gap': round(float(y[m].mean() - p[m].mean()), 4),
        })
    return out

def main():
    args = sys.argv[1:]
    if '--info' in args:
        if not OUT.exists():
            print(f'NO_ARTIFACT {OUT}')
            return 1
        art = json.load(open(OUT))
        print(json.dumps(art.get('metrics', {}), ensure_ascii=False, indent=2))
        print('features:', art.get('features'))
        print('trained_at:', art.get('trained_at_utc'))
        return 0

    X, y, rows, files_ok, files_skip = load_corpus()
    print(f'corpus: files_ok={files_ok} files_skip={files_skip} n={len(y)} win_rate={y.mean():.4f}')
    if len(y) < 200:
        print('FAIL: corpus too small')
        return 1

    # 时序切分（无未来函数）
    order = np.argsort([_tskey(r) for r in rows], kind='mergesort')
    X, y, rows = X[order], y[order], [rows[i] for i in order]
    cut = int(len(y) * 0.7)
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]

    # 标准化（只用训练集统计量）
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xtr_n = (Xtr - mu) / sd
    Xte_n = (Xte - mu) / sd

    w, b = fit_logreg(Xtr_n, ytr)

    ptr = sigmoid(Xtr_n @ w + b)
    pte = sigmoid(Xte_n @ w + b)

    metrics = {
        'n_total': int(len(y)),
        'n_train': int(len(ytr)),
        'n_test': int(len(yte)),
        'train_win_rate': round(float(ytr.mean()), 4),
        'test_win_rate': round(float(yte.mean()), 4),
        'train_auc': round(auc(ytr, ptr), 4),
        'test_auc': round(auc(yte, pte), 4),
        'test_brier': round(brier(yte, pte), 4),
        'test_logloss': round(float(np.mean(-(yte * np.log(np.clip(pte, 1e-9, 1)) + (1 - yte) * np.log(np.clip(1 - pte, 1e-9, 1))))), 4),
        'calibration_test': calibration_buckets(yte, pte),
    }

    art = {
        'version': 'v1',
        'model': 'logreg_numpy_shadow',
        'purpose': 'P(genuine_breakout) 校准蒸馏 — squeeze breakout 语料',
        'trained_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'features': FEATURES,
        'weights': [round(float(x), 6) for x in w],
        'bias': round(float(b), 6),
        'scaler_mean': [round(float(x), 6) for x in mu],
        'scaler_std': [round(float(x), 6) for x in sd],
        'corpus': {
            'source': 'data/fangcang_*_4h.json',
            'files': files_ok,
            'split': 'time 70/30 by ts_burst',
        },
        'metrics': metrics,
        'governance': {
            'mode': 'SHADOW_ONLY',
            'wired_into_decision_chain': False,
            'promotion_requires': '苏摩111 二次批准',
            'retrain_after': 'P0-3 账本闭环修复后接 live 特征版',
        },
    }
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(art, ensure_ascii=False, indent=2))
    tmp.replace(OUT)

    print('=== TEST METRICS ===')
    print(json.dumps({k: v for k, v in metrics.items() if k != 'calibration_test'}, ensure_ascii=False, indent=2))
    print('calibration buckets (test):')
    for c in metrics['calibration_test']:
        print(f"  {c['bucket']}: n={c['n']} mean_p={c['mean_p']} actual={c['actual_wr']} gap={c['gap']:+.3f}")
    print(f'artifact -> {OUT}')
    return 0

if __name__ == '__main__':
    sys.exit(main())

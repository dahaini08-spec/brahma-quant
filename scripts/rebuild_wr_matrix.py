#!/usr/bin/env python3
"""
rebuild_wr_matrix.py — 从live_signal_log重建wr_matrix_realtime.json
[设计院 2026-08-11 模拟验证]

问题: ev_feedback.py废弃导致n_win/avg_pnl_win统计断链
修复: 直接从live_signal_log.jsonl（186条完整结算）重建
"""
import json, time
from pathlib import Path
from collections import defaultdict

BASE  = Path(__file__).parent.parent
LSL   = BASE / 'data' / 'live_signal_log.jsonl'
WR_F  = BASE / 'data' / 'wr_matrix_realtime.json'
WR_BAK = BASE / 'data' / 'wr_matrix_realtime.json.bak_rebuild20260811'

WIN_SET     = {'TP1','TP2','WIN'}
LOSS_SET    = {'SL','LOSS'}
TIMEOUT_SET = {'TIMEOUT','EXPIRED_NO_TOUCH','EXPIRED'}

def score_bin(s: float) -> str:
    if s >= 160: return '160+'
    if s >= 155: return '155-159'
    if s >= 140: return '140-154'
    if s >= 120: return '120-139'
    return '<120'

lines = [json.loads(l) for l in LSL.read_text().strip().split('\n') if l.strip()]
print(f"live_signal_log: {len(lines)}条")

import shutil
if WR_F.exists():
    shutil.copy(WR_F, WR_BAK)
    print(f"备份 → {WR_BAK}")

matrix = defaultdict(lambda: {
    'n':0,'n_win':0,'n_loss':0,'n_timeout':0,'n_open':0,
    'settled':0, 'pnl_wins':[], 'pnl_losses':[]
})

for l in lines:
    r = l.get('regime','?')
    d = l.get('signal_dir') or l.get('direction','?')
    s = score_bin(float(l.get('score', 0) or 0))
    k = f'{r}:{d}:{s}'
    oc = l.get('outcome')
    
    m = matrix[k]
    m['regime']    = r
    m['direction'] = d
    m['score_bin'] = s
    m['n'] += 1
    
    if oc in WIN_SET:
        m['n_win']  += 1
        m['settled'] += 1
        pnl = float(l.get('pnl_pct', 2.0) or 2.0)
        m['pnl_wins'].append(pnl)
    elif oc in LOSS_SET:
        m['n_loss']  += 1
        m['settled'] += 1
        pnl = float(l.get('pnl_pct', -2.0) or -2.0)
        m['pnl_losses'].append(pnl)
    elif oc in TIMEOUT_SET:
        m['n_timeout'] += 1
    elif oc is None:
        m['n_open'] += 1

# 计算WR / avg_pnl / EV
result = {}
for k, m in matrix.items():
    w, l2 = m['n_win'], m['n_loss']
    wr  = w / (w + l2) if (w + l2) > 0 else 0.0
    avg_win  = sum(m['pnl_wins'])  / len(m['pnl_wins'])  if m['pnl_wins']  else 0.0
    avg_loss = sum(m['pnl_losses'])/ len(m['pnl_losses']) if m['pnl_losses'] else 0.0
    ev = wr * avg_win + (1 - wr) * avg_loss if (w + l2) > 0 else 0.0
    result[k] = {
        'regime':       m['regime'],
        'direction':    m['direction'],
        'score_bin':    m['score_bin'],
        'n':            m['n'],
        'n_win':        w,
        'n_loss':       l2,
        'n_timeout':    m['n_timeout'],
        'n_open':       m['n_open'],
        'settled':      m['settled'],
        'wr':           round(wr, 4),
        'avg_pnl_win':  round(avg_win, 4),
        'avg_pnl_loss': round(avg_loss, 4),
        'ev':           round(ev, 4),
        'rebuilt_at':   time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source':       'live_signal_log_rebuild_20260811',
    }

WR_F.write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(f"\n重建完成 → {WR_F}")
print(f"矩阵条目数: {len(result)}")
print()
print("=== 重建后WR矩阵 ===")
for k,v in sorted(result.items(), key=lambda x: -x[1]['n']):
    w,l2 = v['n_win'], v['n_loss']
    wr_s = f"{v['wr']:.0%}" if (w+l2)>0 else "N/A"
    ev_s = f"{v['ev']:+.3f}%" if (w+l2)>0 else "N/A"
    print(f"  {k:45s} n={v['n']:3d} settled={v['settled']:3d} W={w:2d} L={l2:2d} WR={wr_s:5s} EV={ev_s}")

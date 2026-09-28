#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
divergence_report.py — T2 A/B对照盘分歧报告 [梵天2.0 T2 2026-09-27 苏摩111]
接入位置: data/shadow_decisions.jsonl(2.0影子) / data/paper_orders.jsonl(1.0纸面) / reports/brahma_2.0_design.md §6 T2

设计: 同一信号队列，1.0照常纸面执行，2.0影子记录"本应如何"。
     每日双结算+分歧报告: 2.0拦了什么 / 1.0做了什么 / EV对比。

数据源:
  1.0执行: data/paper_orders.jsonl (OPEN/CLOSED订单)
  2.0影子: data/shadow_decisions.jsonl (risk_gate逐条verdict)
  账本: data/paper_ledger_log.jsonl (CLOSE事件net_pnl)

产出: 每日分歧摘要(stdout, 由cron推送) + data/t2_ab_summary.json 累计
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

SHADOW_FILE = BASE / 'data' / 'shadow_decisions.jsonl'
ORDERS_FILE = BASE / 'data' / 'paper_orders.jsonl'
LEDGER_FILE = BASE / 'data' / 'paper_ledger_log.jsonl'
SUMMARY_FILE = BASE / 'data' / 't2_ab_summary.json'

LOOKBACK_HOURS = 24


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def collect(day_cutoff: float) -> dict:
    """汇总回看窗口内的1.0执行与2.0影子评估"""
    orders = [o for o in _load_jsonl(ORDERS_FILE) if float(o.get('ts') or 0) >= day_cutoff]
    closes = [l for l in _load_jsonl(LEDGER_FILE)
              if l.get('ev') == 'CLOSE' and float(l.get('ts') or 0) >= day_cutoff]
    shadows = [s for s in _load_jsonl(SHADOW_FILE) if float(s.get('ts') or 0) >= day_cutoff]

    # 1.0统计
    opened_1_0 = [o for o in orders if o.get('status', '').startswith('OPEN')]
    net_1_0 = sum(float(c.get('net_pnl') or 0) for c in closes)

    # 2.0影子统计：verdict分布
    shadow_blocks = [s for s in shadows
                     if isinstance(s.get('risk_gate'), dict) and not s['risk_gate'].get('ok')]
    shadow_pass = [s for s in shadows
                   if isinstance(s.get('risk_gate'), dict) and s['risk_gate'].get('ok')]
    rule_counts = {}
    for s in shadow_blocks:
        r = s['risk_gate'].get('rule') or '?'
        rule_counts[r] = rule_counts.get(r, 0) + 1

    # 分歧对账：1.0开了但2.0会拦的（按symbol+side近似配对，60s窗口）
    disagreements = []
    for o in opened_1_0:
        for s in shadow_blocks:
            if (s.get('symbol') == o.get('symbol')
                    and str(s.get('side', '')).upper() == str(o.get('side', '')).upper()
                    and abs(float(s.get('ts') or 0) - float(o.get('ts') or 0)) < 3600):
                disagreements.append({
                    'symbol': o.get('symbol'), 'side': o.get('side'),
                    'ts_1_0': o.get('ts'), 'ts_2_0': s.get('ts'),
                    'rule': s['risk_gate'].get('rule'),
                    'reason': str(s['risk_gate'].get('reason', ''))[:80],
                    'net_1_0': o.get('net_pnl'),
                })
                break

    return {
        'window_hours': LOOKBACK_HOURS,
        # [P2口径分离 2026-09-28 苏摩111] R2铁证：同信号1.0语义EXPLETE vs 2.0语义WAIT_15M
        # 两套口径行为不同不是bug（12维终选语义>1.0决策语义），分口记分不混比
        'semantics': {
            '1_0': {'label': '1.0旧decide()口径', 'source': 'paper_orders.jsonl',
                    'note': '五步终审语义：action=ENTER直接开单'},
            '2_0': {'label': '2.0 D-10决策包口径', 'source': 'shadow_decisions.jsonl + decision_packages',
                    'note': 'D1-D7生命周期语义：同一信号可能WAIT_15M（更保守，口径差异非回归）'},
        },
        'n_1_0_open': len(opened_1_0),
        'n_1_0_close': len(closes),
        'net_1_0': round(net_1_0, 2),
        'n_2_0_shadow': len(shadows),
        'n_2_0_pass': len(shadow_pass),
        'n_2_0_block': len(shadow_blocks),
        'rule_counts': rule_counts,
        'n_disagreement': len(disagreements),
        'disagreements': disagreements[:10],
    }


def main() -> int:
    day_cutoff = time.time() - LOOKBACK_HOURS * 3600
    r = collect(day_cutoff)

    # 累计summary
    summary = {}
    if SUMMARY_FILE.exists():
        try:
            summary = json.loads(SUMMARY_FILE.read_text())
        except Exception:
            summary = {}
    summary['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    hist = summary.setdefault('daily', [])
    hist.append({'date': time.strftime('%Y-%m-%d', time.gmtime()),
                 'n_1_0_open': r['n_1_0_open'], 'net_1_0': r['net_1_0'],
                 'n_2_0_block': r['n_2_0_block'], 'rule_counts': r['rule_counts'],
                 'n_disagreement': r['n_disagreement']})
    summary['daily'] = hist[-30:]
    SUMMARY_FILE.write_text(json.dumps(summary, indent=1, ensure_ascii=False))

    # 无活动则静默（cron心跳规范）
    if r['n_1_0_open'] == 0 and r['n_2_0_shadow'] == 0 and r['n_1_0_close'] == 0:
        print('HEARTBEAT_OK')
        return 0

    lines = [
        f"🔬 梵天2.0 T2 A/B对照盘 · {time.strftime('%m-%d %H:%M', time.gmtime())} UTC",
        f"⚠️ 口径分离：1.0=旧decide()语义 / 2.0=D-10决策包语义（分歧≠回归，各自记分）",
        f"窗口{LOOKBACK_HOURS}h: 1.0开{r['n_1_0_open']}笔 平{r['n_1_0_close']}笔 净{r['net_1_0']:+.2f}U",
        f"2.0影子评估{r['n_2_0_shadow']}条: 放行{r['n_2_0_pass']} 拦截{r['n_2_0_block']}",
    ]
    if r['rule_counts']:
        rc = ' '.join(f'{k}×{v}' for k, v in sorted(r['rule_counts'].items()))
        lines.append(f"拦截分布: {rc}")
    if r['disagreements']:
        lines.append(f"⚠️ 分歧{r['n_disagreement']}处（1.0执行但2.0会拦）:")
        for d in r['disagreements'][:5]:
            pnl = d.get('net_1_0')
            pnl_s = f"{pnl:+.1f}U" if isinstance(pnl, (int, float)) else '持仓中'
            lines.append(f"  {d['symbol']} {d['side']} | R{d['rule']}:{d['reason'][:40]} | 1.0结果{pnl_s}")
    else:
        lines.append('✅ 无分歧：双引擎行为一致')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())

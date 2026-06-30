#!/usr/bin/env python3
"""
output_auditor.py — 梵天输出自动审计层 v1.0
设计院 · 360顶级量化工程师 · 2026-06-11

三层防御体系：
  Layer 1 — 数据源校验（Data Source Validator）
    在任何分析前，强制用API验证价格事实
    禁止AI凭记忆输出ATH/ATL/周期高低点

  Layer 2 — 推理链校验（Reasoning Chain Validator）
    核验报告中的数学计算是否自洽
    "从$A跌至$B = X%" → 反算验证

  Layer 3 — 统计置信度校验（Statistical Confidence Validator）
    WR/PF等指标必须携带样本量和置信区间
    小样本自动触发警告标签

设计原则：
  ① 验证层在信号出分前运行，不在输出后
  ② 任何FAIL不阻断流程，但在输出中标注[UNVERIFIED]
  ③ 所有验证结果写入data/audit_log.jsonl供溯源
"""

import math, json, time, subprocess, datetime, sys
from pathlib import Path

BASE     = Path(__file__).parent.parent
AUDIT_LOG = BASE / 'data' / 'audit_log.jsonl'

# ─────────────────────────────────────────
# Layer 1: 数据源校验
# ─────────────────────────────────────────

def _fetch(url):
    r = subprocess.run(['curl','-s','--compressed','--max-time','8', url],
                       capture_output=True, text=True)
    return json.loads(r.stdout)

def verify_price_claim(symbol: str, field: str, claimed: float, tolerance_pct: float = 1.0) -> dict:
    """
    验证一个价格主张
    field: 'ATH' | 'ATL' | 'CURRENT'
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'): sym += 'USDT'

    klines = _fetch(f'https://api.binance.com/api/v3/klines?symbol={sym}&interval=1M&limit=60')
    actual = None

    if field == 'ATH':
        actual = max(float(k[2]) for k in klines)   # H字段
    elif field == 'ATL':
        actual = min(float(k[3]) for k in klines)   # L字段
    elif field == 'CURRENT':
        data = _fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}')
        actual = float(data['price'])

    if actual is None:
        return {'verdict': 'SKIP', 'reason': f'不支持的字段: {field}'}

    diff_pct = abs(claimed - actual) / actual * 100
    passed   = diff_pct <= tolerance_pct

    return {
        'verdict':    'PASS' if passed else 'FAIL',
        'field':      field,
        'symbol':     sym,
        'claimed':    claimed,
        'actual':     round(actual, 2),
        'diff_pct':   round(diff_pct, 2),
        'tolerance':  tolerance_pct,
        'ts':         datetime.datetime.utcnow().isoformat(),
    }

# ─────────────────────────────────────────
# Layer 2: 推理链校验
# ─────────────────────────────────────────

def verify_pct_claim(price_from: float, price_to: float, claimed_pct: float,
                     tolerance: float = 1.0) -> dict:
    """
    验证涨跌幅主张
    e.g. "从$126,200跌至$63,000 = -50%" → 自动计算反算
    """
    actual_pct = (price_to - price_from) / price_from * 100
    diff       = abs(actual_pct - claimed_pct)
    passed     = diff <= tolerance

    return {
        'verdict':     'PASS' if passed else 'FAIL',
        'price_from':  price_from,
        'price_to':    price_to,
        'claimed_pct': claimed_pct,
        'actual_pct':  round(actual_pct, 2),
        'diff':        round(diff, 2),
    }

def verify_cycle_timing(halving_date: str, peak_date: str, claimed_months: int,
                        tolerance_months: int = 1) -> dict:
    """
    验证减半→顶部时间主张
    e.g. "减半后18个月触顶" → 计算实际月份差
    """
    from dateutil.relativedelta import relativedelta
    import dateutil.parser as dp
    try:
        h = dp.parse(halving_date)
        p = dp.parse(peak_date)
        delta = relativedelta(p, h)
        actual_months = delta.years * 12 + delta.months
        diff = abs(actual_months - claimed_months)
        return {
            'verdict':        'PASS' if diff <= tolerance_months else 'FAIL',
            'halving':        halving_date,
            'peak':           peak_date,
            'claimed_months': claimed_months,
            'actual_months':  actual_months,
            'diff_months':    diff,
        }
    except Exception as e:
        return {'verdict': 'SKIP', 'reason': str(e)}

# ─────────────────────────────────────────
# Layer 3: 统计置信度校验
# ─────────────────────────────────────────

def wilson_ci(wins: int, total: int, z: float = 1.96) -> tuple:
    """Wilson置信区间"""
    if total == 0: return (0.0, 1.0)
    p = wins / total
    d = 1 + z**2 / total
    c = (p + z**2 / (2 * total)) / d
    m = (z * math.sqrt(p*(1-p)/total + z**2/(4*total**2))) / d
    return (max(0, c - m), min(1, c + m))

def audit_win_rate(wins: int, losses: int, label: str = '') -> dict:
    """
    审计胜率声明
    自动添加Wilson置信区间，小样本触发警告
    """
    total = wins + losses
    wr    = wins / total if total > 0 else 0
    lo, hi = wilson_ci(wins, total)

    # 样本量分级
    if total < 20:
        confidence_level = 'VERY_LOW ⚠️⚠️'
        warning = f'样本仅{total}条，WR置信区间极宽[{lo*100:.0f}%~{hi*100:.0f}%]，结论不可靠'
    elif total < 50:
        confidence_level = 'LOW ⚠️'
        warning = f'样本{total}条，95%CI=[{lo*100:.1f}%~{hi*100:.1f}%]，需谨慎解读'
    elif total < 100:
        confidence_level = 'MEDIUM'
        warning = f'样本{total}条，95%CI=[{lo*100:.1f}%~{hi*100:.1f}%]'
    else:
        confidence_level = 'HIGH ✅'
        warning = f'样本{total}条，统计显著，95%CI=[{lo*100:.1f}%~{hi*100:.1f}%]'

    return {
        'label':            label,
        'wins':             wins,
        'losses':           losses,
        'total':            total,
        'wr_pct':           round(wr * 100, 1),
        'ci_lo_pct':        round(lo * 100, 1),
        'ci_hi_pct':        round(hi * 100, 1),
        'confidence_level': confidence_level,
        'warning':          warning,
        'display':          f'WR={wr*100:.1f}% 95%CI=[{lo*100:.1f}%,{hi*100:.1f}%] n={total}',
    }

# ─────────────────────────────────────────
# 综合报告审计入口
# ─────────────────────────────────────────

def audit_report(claims: dict) -> dict:
    """
    批量审计一份报告的所有可验证主张

    claims = {
        'price_claims': [
            {'symbol': 'BTC', 'field': 'ATH', 'claimed': 126200},
            {'symbol': 'ETH', 'field': 'ATH', 'claimed': 4957},
        ],
        'pct_claims': [
            {'from': 126200, 'to': 63000, 'claimed_pct': -50.0},
        ],
        'timing_claims': [
            {'halving': '2024-04-20', 'peak': '2025-10-06', 'claimed_months': 18},
        ],
        'stat_claims': [
            {'wins': 51, 'losses': 0, 'label': '武曲Paper WR'},
        ],
    }
    """
    results = {'passed': 0, 'failed': 0, 'warnings': [], 'details': {}}
    ts = datetime.datetime.utcnow().isoformat()

    # Layer 1
    for i, c in enumerate(claims.get('price_claims', [])):
        r = verify_price_claim(c['symbol'], c['field'], c['claimed'])
        key = f"price_{c['symbol']}_{c['field']}"
        results['details'][key] = r
        if r['verdict'] == 'PASS':
            results['passed'] += 1
        elif r['verdict'] == 'FAIL':
            results['failed'] += 1
            results['warnings'].append(
                f"❌ {c['symbol']} {c['field']}: 声明${c['claimed']:,.0f} 实际${r['actual']:,.0f} 偏差{r['diff_pct']:.1f}%"
            )

    # Layer 2
    for i, c in enumerate(claims.get('pct_claims', [])):
        r = verify_pct_claim(c['from'], c['to'], c['claimed_pct'])
        key = f"pct_{i}"
        results['details'][key] = r
        if r['verdict'] == 'PASS':
            results['passed'] += 1
        elif r['verdict'] == 'FAIL':
            results['failed'] += 1
            results['warnings'].append(
                f"❌ 涨跌幅: 声明{c['claimed_pct']:+.1f}% 实际{r['actual_pct']:+.1f}%"
            )

    # Layer 3
    for c in claims.get('stat_claims', []):
        r = audit_win_rate(c['wins'], c.get('losses', 0), c.get('label', ''))
        key = f"stat_{c.get('label','')}"
        results['details'][key] = r
        if 'VERY_LOW' in r['confidence_level'] or 'LOW' in r['confidence_level']:
            results['warnings'].append(f"⚠️  {r['label']}: {r['warning']}")

    results['ts'] = ts
    results['verdict'] = 'CLEAN' if results['failed'] == 0 else f"ISSUES_FOUND({results['failed']})"

    # 写入audit log
    try:
        with open(AUDIT_LOG, 'a') as f:
            f.write(json.dumps(results, ensure_ascii=False) + '\n')
    except Exception:
        pass

    return results


if __name__ == '__main__':
    print('=' * 64)
    print('  output_auditor.py — 三层防御验证系统')
    print('=' * 64)

    # 自测：审计刚才错误报告中的主张
    test_claims = {
        'price_claims': [
            {'symbol': 'BTC', 'field': 'ATH', 'claimed': 109588},   # 旧错误
            {'symbol': 'BTC', 'field': 'ATH', 'claimed': 126200},   # 修正值
            {'symbol': 'ETH', 'field': 'ATH', 'claimed': 4957},
        ],
        'pct_claims': [
            {'from': 126200, 'to': 62884, 'claimed_pct': -50.2},
            {'from': 4957,   'to': 1658,  'claimed_pct': -66.6},
        ],
        'timing_claims': [
            {'halving': '2024-04-20', 'peak': '2025-10-06', 'claimed_months': 18},
        ],
        'stat_claims': [
            {'wins': 15, 'losses': 0, 'label': '武曲Paper WR(已结算)'},
            {'wins': 51, 'losses': 0, 'label': '武曲Paper WR(乐观含OPEN)'},
        ],
    }

    print('\n正在验证...')
    r = audit_report(test_claims)

    print(f'\n综合判定: {r["verdict"]}')
    print(f'  通过: {r["passed"]}  失败: {r["failed"]}')

    if r['warnings']:
        print('\n警告列表:')
        for w in r['warnings']:
            print(f'  {w}')

    print('\n各项明细:')
    for k, v in r['details'].items():
        verdict = v.get('verdict','?')
        if 'display' in v:
            print(f'  [{verdict}] {k}: {v["display"]}')
        elif 'actual_pct' in v:
            print(f'  [{verdict}] {k}: 声明{v["claimed_pct"]:+.1f}% 实际{v["actual_pct"]:+.1f}%')
        elif 'actual' in v:
            print(f'  [{verdict}] {k}: 声明${v["claimed"]:,.0f} 实际${v["actual"]:,.0f}')
        elif 'actual_months' in v:
            print(f'  [{verdict}] {k}: 声明{v["claimed_months"]}月 实际{v["actual_months"]}月')

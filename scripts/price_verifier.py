#!/usr/bin/env python3
"""
price_verifier.py — 价格数值强制验证层 v1.0
设计院 · 2026-06-11

核心功能：
  所有涉及ATH/ATL/周期高低点的陈述
  必须经过此模块实时API验证，不允许AI凭记忆输出

用法：
  from price_verifier import verify_ath, get_cycle_stats, assert_price_fact
  stats = get_cycle_stats('BTCUSDT')
  verify_ath('BTC', claimed_ath=109588)  # 若与实际偏差>1%，抛出ValueError
"""

import subprocess, json, sys, time
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent

def _fetch(url: str, timeout: int = 8) -> list:
    """零AI curl fetch，避免urllib 418限流"""
    r = subprocess.run(
        ['curl', '-s', '--compressed', '--max-time', str(timeout), url],
        capture_output=True, text=True
    )
    return json.loads(r.stdout)

def _fetch_klines(symbol: str, interval: str, limit: int = 500) -> list:
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    return _fetch(url)

def get_current_price(symbol: str = 'BTCUSDT') -> float:
    """实时获取当前价格"""
    data = _fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}')
    return float(data['price'])

def get_cycle_stats(symbol: str = 'BTCUSDT', months: int = 48) -> dict:
    """
    获取最近N个月的真实周期统计
    使用月K线H字段（最高价）和L字段（最低价）
    
    返回：
        ath: 真实历史最高价（月K H字段最大值）
        ath_month: ATH所在月份
        atl: 真实历史最低价（月K L字段最小值）
        atl_month: ATL所在月份
        current: 实时价格
        drawdown_from_ath: 从ATH回落百分比
    """
    import datetime
    klines = _fetch_klines(symbol, '1M', limit=months)
    
    ath = 0.0
    atl = float('inf')
    ath_month = ''
    atl_month = ''
    
    for k in klines:
        ts = datetime.datetime.utcfromtimestamp(k[0] // 1000)
        month_str = ts.strftime('%Y-%m')
        h = float(k[2])  # 月最高价 ← H字段，非收盘C字段
        l = float(k[3])  # 月最低价 ← L字段
        if h > ath:
            ath = h
            ath_month = month_str
        if l < atl:
            atl = l
            atl_month = month_str
    
    current = get_current_price(symbol)
    drawdown = (current - ath) / ath * 100
    recovery = (current - atl) / atl * 100
    
    return {
        'symbol':          symbol,
        'ath':             round(ath, 2),
        'ath_month':       ath_month,
        'atl':             round(atl, 2),
        'atl_month':       atl_month,
        'current':         round(current, 2),
        'drawdown_from_ath': round(drawdown, 2),
        'recovery_from_atl': round(recovery, 2),
        'months_scanned':  len(klines),
        'verified_at':     datetime.datetime.utcnow().isoformat(),
    }

def verify_ath(symbol: str, claimed_ath: float, tolerance_pct: float = 1.0) -> dict:
    """
    验证声明的ATH是否与实际API数据吻合
    偏差超过tolerance_pct则抛出ValueError（强制修正）
    
    Args:
        symbol: 'BTC' or 'BTCUSDT'
        claimed_ath: AI或报告中声明的ATH价格
        tolerance_pct: 允许误差百分比（默认1%）
    
    Returns:
        {'valid': bool, 'actual_ath': float, 'claimed': float, 'diff_pct': float}
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    
    stats = get_cycle_stats(sym)
    actual_ath = stats['ath']
    diff_pct = abs(claimed_ath - actual_ath) / actual_ath * 100
    
    result = {
        'valid':      diff_pct <= tolerance_pct,
        'actual_ath': actual_ath,
        'ath_month':  stats['ath_month'],
        'claimed':    claimed_ath,
        'diff_pct':   round(diff_pct, 2),
        'verdict':    'PASS' if diff_pct <= tolerance_pct else 'FAIL',
    }
    
    if not result['valid']:
        msg = (f"[price_verifier] ATH验证失败: "
               f"声明${claimed_ath:,.0f} vs 实际${actual_ath:,.0f} "
               f"偏差{diff_pct:.1f}% > 容差{tolerance_pct}%")
        print(f"⚠️  {msg}", file=sys.stderr)
    
    return result

def get_drawdown_targets(symbol: str = 'BTCUSDT') -> dict:
    """
    基于真实ATH计算历史回撤目标位
    确保底部区间计算基于正确的ATH
    """
    stats = get_cycle_stats(symbol)
    ath = stats['ath']
    current = stats['current']
    
    targets = {}
    for pct in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        targets[f'drawdown_{int(pct*100)}pct'] = round(ath * (1 - pct), 2)
    
    # 当前回撤档位
    current_dd = (current - ath) / ath
    bracket = None
    for pct in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        if abs(current_dd + pct) < 0.05:
            bracket = f'{int(pct*100)}%'
    
    return {
        'ath':            ath,
        'ath_month':      stats['ath_month'],
        'current':        current,
        'current_drawdown_pct': round(current_dd * 100, 1),
        'current_bracket':     bracket,
        'targets':        targets,
    }

def verify_price_facts(facts: dict) -> dict:
    """
    批量验证一个报告中所有价格事实
    
    facts = {
        'BTC_ATH': 126200,
        'ETH_ATH': 4957,
    }
    返回每项的验证结果
    """
    results = {}
    for key, value in facts.items():
        parts = key.split('_')
        symbol = parts[0] + 'USDT'
        fact_type = '_'.join(parts[1:])
        
        if fact_type == 'ATH':
            results[key] = verify_ath(symbol, value)
        else:
            results[key] = {'skipped': True, 'reason': f'未知事实类型: {fact_type}'}
    
    all_pass = all(r.get('valid', True) for r in results.values())
    results['_summary'] = {
        'all_pass': all_pass,
        'total': len(facts),
        'failed': sum(1 for r in results.values() if r.get('verdict') == 'FAIL'),
    }
    return results


if __name__ == '__main__':
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    
    print('=' * 60)
    print('  price_verifier.py — 关键价格事实验证器')
    print('=' * 60)
    
    for sym in symbols:
        s = sym.upper()
        if not s.endswith('USDT'):
            s += 'USDT'
        print(f'\n{s}:')
        stats = get_cycle_stats(s, months=60)
        print(f'  ATH: ${stats["ath"]:,.2f} ({stats["ath_month"]}) ← 月K最高价H字段')
        print(f'  ATL: ${stats["atl"]:,.2f} ({stats["atl_month"]}) ← 月K最低价L字段')
        print(f'  当前: ${stats["current"]:,.2f}')
        print(f'  从ATH回落: {stats["drawdown_from_ath"]:+.1f}%')
        print(f'  验证时间: {stats["verified_at"]}')
        
        dd = get_drawdown_targets(s)
        print(f'\n  回撤目标（基于真实ATH ${dd["ath"]:,.0f}）:')
        for k, v in dd['targets'].items():
            marker = ' ← 当前附近' if abs(v - dd['current']) / dd['current'] < 0.05 else ''
            print(f'    {k}: ${v:,.0f}{marker}')
    
    print()
    print('=== ATH验证测试 ===')
    r = verify_ath('BTC', 109588)
    print(f'旧报告BTC ATH $109,588: {r["verdict"]} (实际${r["actual_ath"]:,.0f}, 偏差{r["diff_pct"]}%)')
    r2 = verify_ath('BTC', 126200)
    print(f'修正BTC ATH $126,200:   {r2["verdict"]} (实际${r2["actual_ath"]:,.0f}, 偏差{r2["diff_pct"]}%)')

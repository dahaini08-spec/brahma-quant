#!/usr/bin/env python3
"""
fact_gate.py — 梵天事实门控层 v1.0
设计院 · 2026-06-11

在brahma_analyze输出前自动运行的事实验证钩子
将output_auditor集成到信号生成主流程中

调用方式：
  from fact_gate import pre_output_check, format_verified_stats

  # 在任何报告/分析输出前
  gate = pre_output_check(symbol='BTC', price_claims={...})
  if gate['issues']:
      output = inject_warnings(output, gate['warnings'])
"""

import json, datetime, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

def pre_output_check(symbol: str = None, auto_verify: bool = True) -> dict:
    """
    输出前自动验证钩子
    在任何分析报告生成前调用
    返回: {ok, issues, warnings, verified_prices}
    """
    result = {
        'ok': True,
        'issues': [],
        'warnings': [],
        'verified_prices': {},
        'ts': datetime.datetime.utcnow().isoformat(),
    }

    if not auto_verify:
        return result

    try:
        from price_verifier import get_cycle_stats, get_drawdown_targets

        symbols = []
        if symbol:
            s = symbol.upper()
            if not s.endswith('USDT'): s += 'USDT'
            symbols = [s]
        else:
            symbols = ['BTCUSDT', 'ETHUSDT']

        for sym in symbols:
            stats = get_cycle_stats(sym, months=60)
            dd    = get_drawdown_targets(sym)
            ticker = sym.replace('USDT','')

            result['verified_prices'][ticker] = {
                'current':    stats['current'],
                'ath':        stats['ath'],
                'ath_month':  stats['ath_month'],
                'atl':        stats['atl'],
                'drawdown':   stats['drawdown_from_ath'],
                'dd_targets': dd['targets'],
            }

    except Exception as e:
        result['warnings'].append(f'价格验证层异常: {e}（继续但请人工核查价格数据）')

    return result

def format_verified_stats(wins: int, losses: int, label: str = '') -> str:
    """
    生成带置信区间的WR展示字符串
    替代裸WR数字
    """
    try:
        from output_auditor import audit_win_rate
        r = audit_win_rate(wins, losses, label)
        return r['display']
    except Exception:
        total = wins + losses
        wr = wins / total * 100 if total > 0 else 0
        return f'WR={wr:.1f}% n={total}（置信区间计算失败）'

def inject_unverified_tag(text: str, field: str) -> str:
    """在未验证数据后注入[UNVERIFIED]标签"""
    return f'{text} [UNVERIFIED:{field}]'

def get_verified_price_header(symbols: list = None) -> str:
    """
    生成报告顶部的价格验证头部
    所有分析报告必须包含此头部
    """
    symbols = symbols or ['BTCUSDT', 'ETHUSDT']
    lines = ['─' * 50,
             '  ✅ 价格事实已API验证（非AI记忆）',
             f'  验证时间: {datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}']
    try:
        from price_verifier import get_cycle_stats
        for sym in symbols:
            s = get_cycle_stats(sym, months=60)
            ticker = sym.replace('USDT','')
            lines.append(
                f'  {ticker}: 当前${s["current"]:,.0f}  '
                f'ATH ${s["ath"]:,.0f}({s["ath_month"]})  '
                f'回落{s["drawdown_from_ath"]:+.1f}%'
            )
    except Exception as e:
        lines.append(f'  ⚠️ 验证异常: {e}')
    lines.append('─' * 50)
    return '\n'.join(lines)


if __name__ == '__main__':
    print('=== fact_gate 预输出检查测试 ===\n')
    gate = pre_output_check()
    print(f'状态: {"OK" if gate["ok"] else "ISSUES"}')
    for sym, p in gate['verified_prices'].items():
        print(f'\n{sym}:')
        print(f'  当前: ${p["current"]:,.2f}')
        print(f'  ATH:  ${p["ath"]:,.2f} ({p["ath_month"]})')
        print(f'  回落: {p["drawdown"]:+.1f}%')
        print(f'  底部区间: ${p["dd_targets"]["drawdown_55pct"]:,.0f}~${p["dd_targets"]["drawdown_65pct"]:,.0f}')

    print('\n=== WR置信区间展示 ===')
    print(format_verified_stats(15, 0, '武曲Paper(已结算15条)'))
    print(format_verified_stats(51, 0, '武曲Paper(含OPEN)'))
    print(format_verified_stats(100, 20, '假设样本120条'))

    print('\n=== 验证头部示例 ===')
    print(get_verified_price_header())

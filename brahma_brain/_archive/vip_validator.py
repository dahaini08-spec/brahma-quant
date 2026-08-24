#!/usr/bin/env python3
"""
vip_validator.py — VIP策略发帖前实时校验层 v1.0
苏摩111封印 2026-08-15

三项铁律：
  P0: 价格量级验证（entry_lo 必须在当前价 ±50% 范围内）
  P1: VIP参数严格来自Engine（entry_lo/hi/sl/tp1 读自 r[...] 字段）
  P2: 妖币专项时效性门控（24H涨跌>50% → OI/LS必须实时重拉）

设计原则：
  fail-safe: 任何异常 → 返回警告，不阻断发帖，但标注 ⚠️
  零容忍：价格偏差 >50% → 强制拦截，策略标注 ❌INVALID
"""

from __future__ import annotations
import urllib.request
import json
import time
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# P0：价格量级验证
# ══════════════════════════════════════════════════════════════════════

def verify_price_scale(
    symbol: str,
    entry_lo: float,
    entry_hi: float,
    sl: float,
) -> dict:
    """
    验证 VIP 策略价格参数与实时价格是否量级一致。
    偏差 >50% → INVALID（强制拦截）
    偏差 10~50% → WARN（标注警告）
    偏差 <10% → OK

    返回:
    {
      'ok': bool,
      'warn': bool,
      'cur_price': float,
      'entry_lo': float,
      'deviation_pct': float,     # entry_lo 与当前价偏差%
      'sl_pct': float,             # SL距离%（从entry_lo计算）
      'sl_ok': bool,               # SL在1.0%~5.0%范围内
      'msg': str,
    }
    """
    result = {
        'ok': False, 'warn': False,
        'cur_price': 0.0, 'entry_lo': entry_lo,
        'deviation_pct': 0.0, 'sl_pct': 0.0,
        'sl_ok': False, 'msg': '',
    }

    try:
        # 实时拉取当前价格
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        resp = json.loads(urllib.request.urlopen(url, timeout=5).read())
        cur = float(resp['price'])
        result['cur_price'] = cur

        if cur <= 0 or entry_lo <= 0:
            result['msg'] = f'价格异常: cur={cur} entry_lo={entry_lo}'
            return result

        # 偏差计算
        dev = abs(entry_lo - cur) / cur * 100
        result['deviation_pct'] = round(dev, 2)

        if dev > 50:
            result['ok'] = False
            result['warn'] = False
            result['msg'] = (
                f'❌ INVALID: entry_lo={entry_lo} vs 实时${cur:.6f} '
                f'偏差{dev:.1f}%（超50%，策略无效）'
            )
            return result
        elif dev > 10:
            result['ok'] = True
            result['warn'] = True
            result['msg'] = f'⚠️ WARN: entry_lo偏差{dev:.1f}%（建议重新分析）'
        else:
            result['ok'] = True
            result['msg'] = f'✅ 价格验证: entry_lo={entry_lo} ≈ ${cur:.6f} 偏差{dev:.1f}%'

        # SL距离验证
        if sl > 0 and entry_lo > 0:
            sl_pct = abs(entry_lo - sl) / entry_lo * 100
            result['sl_pct'] = round(sl_pct, 2)
            result['sl_ok'] = 1.0 <= sl_pct <= 5.0
            if not result['sl_ok']:
                result['warn'] = True
                result['msg'] += f' | ⚠️ SL距离{sl_pct:.1f}%（需1.0%~5.0%）'
            else:
                result['msg'] += f' | SL距离{sl_pct:.1f}% ✅'

    except Exception as e:
        result['warn'] = True
        result['ok'] = True  # fail-safe: 验证失败不阻断，但标注
        result['msg'] = f'⚠️ 价格验证失败({e})，请人工核查'

    return result


# ══════════════════════════════════════════════════════════════════════
# P2：妖币时效性门控（实时重拉OI / 多空比）
# ══════════════════════════════════════════════════════════════════════

def verify_volatile_freshness(
    symbol: str,
    cached_oi_change: float,        # 分析时的OI变化%（如 +8.43）
    cached_long_pct: float,         # 分析时的多头占比%（如 49.6）
    chg_24h_pct: float,             # 24H涨跌幅%
    freshness_threshold_min: int = 5,  # 妖币数据有效期（分钟）
) -> dict:
    """
    妖币（24H涨跌>50%）专项：发帖前重新拉取OI和多空比，验证与分析时一致。

    返回:
    {
      'ok': bool,
      'is_volatile': bool,
      'oi_change_now': float,
      'oi_reversed': bool,          # OI方向是否已逆转
      'ls_long_pct_now': float,
      'ls_reversed': bool,          # 多空比是否显著变化(>15%)
      'msg': str,
    }
    """
    result = {
        'ok': True, 'is_volatile': False,
        'oi_change_now': 0.0, 'oi_reversed': False,
        'ls_long_pct_now': 0.0, 'ls_reversed': False,
        'msg': '',
    }

    is_volatile = abs(chg_24h_pct) > 50
    result['is_volatile'] = is_volatile

    if not is_volatile:
        result['msg'] = '非妖币，跳过时效性检查'
        return result

    msgs = []
    try:
        # 重拉OI（1H变化）
        oih = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/futures/data/openInterestHist'
            f'?symbol={symbol}&period=1h&limit=2', timeout=5
        ).read())
        if len(oih) >= 2:
            oi_now = (
                (float(oih[-1]['sumOpenInterestValue']) -
                 float(oih[0]['sumOpenInterestValue'])) /
                float(oih[0]['sumOpenInterestValue']) * 100
            )
            result['oi_change_now'] = round(oi_now, 2)
            # 方向逆转判断（正→负 或 负→正）
            oi_reversed = (cached_oi_change > 0 and oi_now < -1) or \
                          (cached_oi_change < 0 and oi_now > 1)
            result['oi_reversed'] = oi_reversed
            if oi_reversed:
                msgs.append(
                    f'⚠️ OI已逆转: 分析时{cached_oi_change:+.1f}% → 现在{oi_now:+.1f}%'
                    f'（做多燃料{"消失" if oi_now < 0 else "恢复"}）'
                )
                result['ok'] = False
            else:
                msgs.append(f'✅ OI方向一致: {oi_now:+.1f}%')
    except Exception as e:
        msgs.append(f'⚠️ OI实时拉取失败({e})')

    try:
        # 重拉多空比
        ls = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
            f'?symbol={symbol}&period=1h&limit=1', timeout=5
        ).read())
        long_now = float(ls[0]['longAccount']) * 100
        result['ls_long_pct_now'] = round(long_now, 1)
        # 显著变化（>15%）
        ls_delta = abs(long_now - cached_long_pct)
        ls_reversed = ls_delta > 15
        result['ls_reversed'] = ls_reversed
        if ls_reversed:
            msgs.append(
                f'⚠️ 多空比显著变化: 分析时多{cached_long_pct:.0f}% → 现在多{long_now:.1f}%'
                f'（变化{ls_delta:.0f}%，需重新评估）'
            )
            result['ok'] = False
        else:
            msgs.append(f'✅ 多空比稳定: 多{long_now:.1f}%（变化{ls_delta:.1f}%）')
    except Exception as e:
        msgs.append(f'⚠️ 多空比拉取失败({e})')

    result['msg'] = ' | '.join(msgs)
    return result


# ══════════════════════════════════════════════════════════════════════
# 主入口：VIP策略全量校验
# ══════════════════════════════════════════════════════════════════════

def validate_vip_strategy(
    symbol: str,
    direction: str,           # 'LONG' or 'SHORT'
    entry_lo: float,
    entry_hi: float,
    sl: float,
    tp1: float,
    chg_24h_pct: float = 0.0,
    cached_oi_change: float = 0.0,
    cached_long_pct: float = 50.0,
    source: str = 'engine',   # 'engine'（来自Engine字段）or 'ai'（AI自行推算）
) -> dict:
    """
    VIP策略发帖前全量校验。

    返回:
    {
      'valid': bool,            # True=可以发帖，False=必须拦截
      'warn': bool,             # True=可以发帖但有警告
      'source_ok': bool,        # 参数来源是否合规（engine）
      'price_check': dict,      # P0价格验证
      'freshness_check': dict,  # P2时效性验证（妖币）
      'summary': str,           # 一行汇总
      'vip_header': str,        # 在VIP策略顶部插入的校验标注
    }
    """
    result = {
        'valid': True, 'warn': False,
        'source_ok': source == 'engine',
        'price_check': {}, 'freshness_check': {},
        'summary': '', 'vip_header': '',
    }

    lines = []

    # P1：来源验证
    if source != 'engine':
        result['warn'] = True
        lines.append('⚠️ 参数来源: AI推算（非Engine输出，建议人工核查）')
    else:
        lines.append('✅ 参数来源: Engine输出')

    # P0：价格量级验证
    pc = verify_price_scale(symbol, entry_lo, entry_hi, sl)
    result['price_check'] = pc
    lines.append(pc['msg'])
    if not pc['ok']:
        result['valid'] = False
    if pc.get('warn'):
        result['warn'] = True

    # P2：妖币时效性验证
    fc = verify_volatile_freshness(
        symbol, cached_oi_change, cached_long_pct,
        chg_24h_pct
    )
    result['freshness_check'] = fc
    if fc['is_volatile']:
        lines.append(fc['msg'])
        if not fc['ok']:
            result['warn'] = True
            # 妖币OI逆转→降级为WARN（不强制拦截，但标注）
            lines.append('⚠️ 妖币行情数据已变化，策略参考价值降低')

    # 汇总
    status = '✅ 校验通过' if result['valid'] and not result['warn'] else \
             ('⚠️ 通过(有警告)' if result['valid'] else '❌ 校验失败，策略无效')
    result['summary'] = f'{status} | {symbol} {direction}'
    result['vip_header'] = '\n'.join(
        [f'【策略校验 {time.strftime("%H:%M UTC", time.gmtime())}】'] + lines
    )

    return result

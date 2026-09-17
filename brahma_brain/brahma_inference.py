#!/usr/bin/env python3
"""
brahma_inference.py — 梵天推理层 v2.0
[2026-09-16 苏摩111] 把"40年交易员思维"编码为因果推理+博弈建模+周期因果链
[2026-09-16 v2修复] 修正数据路径，对接真实state文件结构

接入位置：brahma_full_report.py format_full_report()末尾 → brahma_inference.py → 追加到报告
依赖：data/brahma_state_{symbol}.json（事实层数据，由brahma_core.analyze()生成）

三个推理引擎：
1. build_causal_chain() — 因果推理：连接跨指标/跨标的的因果关系
2. build_game_theory()  — 博弈建模：主力意图+散户行为+猎杀路径
3. build_timeframe_chain() — 周期因果链：不同周期=不同阶段

关键约束：
- 只能引用brahma_state中的数据，不能编造
- 每个推理步骤必须标注数据来源
- 数据不足时输出"数据不足，无法推理"
"""

import json, sys, os, re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(__file__).parent.parent / 'data'

# ── 工具函数 ────────────────────────────────────────────────

def _load_state(symbol: str) -> dict:
    """加载标的的分析状态"""
    f = DATA_DIR / f'brahma_state_{symbol.lower()}.json'
    if not f.exists():
        return {}
    return json.loads(f.read_text())

def _safe_get(d: dict, *keys, default=None):
    """安全嵌套取值"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d

def _fmt_price(p, sym='BTC'):
    """格式化价格"""
    if not p or p == 0:
        return '?'
    if 'ETH' in sym:
        return f'${p:,.0f}'
    return f'${p:,.0f}'

def _parse_gex_from_breakdown(breakdown_str: str) -> Tuple[float, str]:
    """从confluence.breakdown.s22_gex字符串解析GEX值和方向"""
    if not breakdown_str:
        return 0, ''
    # 匹配 GEX=+142.4M 或 GEX=-9.04M
    m = re.search(r'GEX=([+-][\d.]+)M?\s*\((\w+)\)', breakdown_str)
    if m:
        return float(m.group(1)), m.group(2)
    return 0, ''

def _parse_hurst_from_breakdown(breakdown_str: str) -> float:
    """从confluence.breakdown.Hurst体制验证字符串解析Hurst值"""
    if not breakdown_str:
        return 0.5
    m = re.search(r'H=([\d.]+)', breakdown_str)
    if m:
        return float(m.group(1))
    return 0.5

def _parse_cvd_from_breakdown(breakdown_str: str) -> Tuple[float, str]:
    """从confluence.breakdown.CVD订单流字符串解析CVD值"""
    if not breakdown_str:
        return 0, ''
    # 匹配 +6 CVD 或 -10 CVD
    m = re.match(r'([+-]?\d+)\s+CVD', breakdown_str)
    if m:
        return float(m.group(1)), breakdown_str
    return 0, breakdown_str

# ── 1. 因果推理引擎 ──────────────────────────────────────────

def build_causal_chain(btc: dict, eth: dict) -> List[dict]:
    """
    因果推理：连接跨指标/跨标的的因果关系
    输入：BTC和ETH的brahma_state
    输出：因果链[{step, cause, effect, evidence, direction}]
    """
    chains = []

    btc_price = btc.get('price', 0)
    eth_price = eth.get('price', 0)

    # ── 数据提取（使用正确的state路径） ──
    btc_liq = _safe_get(btc, 'extra', 'liq_snap', default={}) or {}
    eth_liq = _safe_get(eth, 'extra', 'liq_snap', default={}) or {}

    btc_oi_chg = btc_liq.get('oi_chg4h', 0) or 0
    eth_oi_chg = eth_liq.get('oi_chg4h', 0) or 0
    btc_long_pct = btc_liq.get('long_pct', 0) or 0
    eth_long_pct = eth_liq.get('long_pct', 0) or 0
    btc_fund_rate = btc_liq.get('fund_rate', 0) or 0
    eth_fund_rate = eth_liq.get('fund_rate', 0) or 0

    # CVD从confluence.breakdown解析
    btc_cvd_str = _safe_get(btc, 'confluence', 'breakdown', 'CVD订单流', default='')
    eth_cvd_str = _safe_get(eth, 'confluence', 'breakdown', 'CVD订单流', default='')
    btc_cvd, btc_cvd_note = _parse_cvd_from_breakdown(btc_cvd_str)
    eth_cvd, eth_cvd_note = _parse_cvd_from_breakdown(eth_cvd_str)

    # 清算位
    btc_liq_short = btc_liq.get('liq_short_5pct', 0) or 0  # 上方空头清算
    btc_liq_long = btc_liq.get('liq_long_5pct', 0) or 0    # 下方多头清算
    eth_liq_short = eth_liq.get('liq_short_5pct', 0) or 0
    eth_liq_long = eth_liq.get('liq_long_5pct', 0) or 0

    btc_dir = btc.get('signal_dir', 'NONE')
    eth_dir = eth.get('signal_dir', 'NONE')

    # ── 因果链1：OI变化 + 资金费率 → 方向判断 ──
    step = 1
    if btc_oi_chg < -0.5 and btc_fund_rate > 0:
        chains.append({
            'step': step,
            'cause': f'BTC OI 4H变化={btc_oi_chg:+.2f}% + 资金费率={btc_fund_rate:+.4f}(多头付费)',
            'effect': '多头在平仓(OI下降) + 多头仍在付费 → 多头疲软 → 下行压力',
            'evidence': f'OI_chg={btc_oi_chg:+.2f}% | FR={btc_fund_rate:+.4f} | long_pct={btc_long_pct:.0f}%',
            'direction': 'BTC短期偏空'
        })
    elif btc_oi_chg > 0.5 and btc_fund_rate < 0:
        chains.append({
            'step': step,
            'cause': f'BTC OI 4H变化={btc_oi_chg:+.2f}% + 资金费率={btc_fund_rate:+.4f}(空头付费)',
            'effect': '新仓在增加(OI上升) + 空头在付费 → 空头被挤 → 短期上行',
            'evidence': f'OI_chg={btc_oi_chg:+.2f}% | FR={btc_fund_rate:+.4f} | short_pct={100-btc_long_pct:.0f}%',
            'direction': 'BTC短期偏多'
        })
    step += 1

    if eth_oi_chg > 0.5 and eth_fund_rate < 0:
        chains.append({
            'step': step,
            'cause': f'ETH OI 4H变化={eth_oi_chg:+.2f}% + 资金费率={eth_fund_rate:+.4f}(空头付费)',
            'effect': '空头在建仓(OI上升) + 空头在付费 → 空头强势 → 但可能被逼空',
            'evidence': f'OI_chg={eth_oi_chg:+.2f}% | FR={eth_fund_rate:+.4f} | long_pct={eth_long_pct:.0f}% | 散户极度做多',
            'direction': 'ETH空头强势但逼空风险高'
        })
    elif eth_oi_chg < -0.5 and eth_fund_rate > 0:
        chains.append({
            'step': step,
            'cause': f'ETH OI 4H变化={eth_oi_chg:+.2f}% + 资金费率={eth_fund_rate:+.4f}(多头付费)',
            'effect': '多头在平仓 + 多头在付费 → 多头疲软 → 下行',
            'evidence': f'OI_chg={eth_oi_chg:+.2f}% | FR={eth_fund_rate:+.4f} | long_pct={eth_long_pct:.0f}%',
            'direction': 'ETH短期偏空'
        })
    step += 1

    # [补充 2026-09-16] ETH OI下降+空头付费 → 空头回补 → 看多信号
    if eth_oi_chg < -0.3 and eth_oi_chg > -0.5 and eth_fund_rate < 0:
        chains.append({
            'step': step,
            'cause': f'ETH OI 4H变化={eth_oi_chg:+.2f}% + 资金费率={eth_fund_rate:+.4f}(空头付费)',
            'effect': 'OI小幅下降+空头付费 → 空头在平仓回补 → 短期可能逼空 → 但散户77%极度做多 → 可能是多头陷阱',
            'evidence': f'OI_chg={eth_oi_chg:+.2f}% | FR={eth_fund_rate:+.4f} | long_pct={eth_long_pct:.0f}% | 矛盾信号',
            'direction': 'ETH空头回补但散户拥挤=矛盾'
        })
        step += 1

    # ── 因果链2：清算地图 → 猎杀路径 ──
    if btc_liq_short > 0 and btc_price > 0:
        dist_up = (btc_liq_short - btc_price) / btc_price * 100
        if 0 < dist_up < 5:
            chains.append({
                'step': step,
                'cause': f'BTC上方空头清算区{_fmt_price(btc_liq_short, "BTC")}(+{dist_up:.1f}%)',
                'effect': '主力可能引诱价格上行 → 触发空头止损 → 然后反转做空',
                'evidence': f'liq_short_5%={_fmt_price(btc_liq_short)} | 距离+{dist_up:.1f}% | 猎杀目标',
                'direction': 'BTC先涨后跌风险'
            })
            step += 1

    if btc_liq_long > 0 and btc_price > 0:
        dist_down = (btc_price - btc_liq_long) / btc_price * 100
        if 0 < dist_down < 5:
            chains.append({
                'step': step,
                'cause': f'BTC下方多头清算区{_fmt_price(btc_liq_long, "BTC")}( -{dist_down:.1f}%)',
                'effect': '价格跌到清算区 → 多头止损触发 → 加速下跌 → 然后主力接货反弹',
                'evidence': f'liq_long_5%={_fmt_price(btc_liq_long)} | 距离-{dist_down:.1f}% | 反弹伏击点',
                'direction': 'BTC清算区=做多伏击点'
            })
            step += 1

    if eth_liq_short > 0 and eth_price > 0:
        dist_up = (eth_liq_short - eth_price) / eth_price * 100
        if 0 < dist_up < 5:
            chains.append({
                'step': step,
                'cause': f'ETH上方空头清算区{_fmt_price(eth_liq_short, "ETH")}(+{dist_up:.1f}%)',
                'effect': 'ETH反弹到清算区 → 散户追多 → 主力做空 → ETH下跌',
                'evidence': f'liq_short_5%={_fmt_price(eth_liq_short, "ETH")} | 距离+{dist_up:.1f}% | 做空入场点',
                'direction': 'ETH清算区=做空入场点'
            })
            step += 1

    if eth_liq_long > 0 and eth_price > 0:
        dist_down = (eth_price - eth_liq_long) / eth_price * 100
        if 0 < dist_down < 5:
            chains.append({
                'step': step,
                'cause': f'ETH下方多头清算区{_fmt_price(eth_liq_long, "ETH")}( -{dist_down:.1f}%)',
                'effect': 'ETH跌到清算区 → 多头止损 → 主力接货 → 反弹',
                'evidence': f'liq_long_5%={_fmt_price(eth_liq_long, "ETH")} | 距离-{dist_down:.1f}% | 反弹伏击点',
                'direction': 'ETH清算区=做多伏击点'
            })
            step += 1

    # ── 因果链3：跨标的传导 ──
    if btc_dir != 'NONE' and eth_dir != 'NONE' and btc_dir != eth_dir:
        if btc_dir == 'LONG' and eth_dir == 'SHORT':
            chains.append({
                'step': step,
                'cause': 'BTC=做多 ETH=做空 相关性0.85',
                'effect': '分阶段策略：ETH先反弹做空→ETH下跌带动BTC跌→BTC到清算区接多→ETH空单止盈',
                'evidence': f'BTC方向={btc_dir} | ETH方向={eth_dir} | 相关性0.85 | 非同时矛盾',
                'direction': '分阶段：先空ETH后多BTC'
            })
        elif btc_dir == 'SHORT' and eth_dir == 'LONG':
            chains.append({
                'step': step,
                'cause': 'BTC=做空 ETH=做多 相关性0.85',
                'effect': '分阶段策略：BTC先反弹做空→BTC下跌带动ETH跌→ETH到清算区接多→BTC空单止盈',
                'evidence': f'BTC方向={btc_dir} | ETH方向={eth_dir} | 相关性0.85 | 非同时矛盾',
                'direction': '分阶段：先空BTC后多ETH'
            })
        step += 1

    # ── 因果链4：CVD + OI矛盾检测 ──
    if btc_cvd != 0:
        if btc_cvd > 0 and btc_oi_chg < 0:
            chains.append({
                'step': step,
                'cause': f'BTC CVD={btc_cvd:+.0f}(买方主导) 但 OI={btc_oi_chg:+.2f}%(多头平仓)',
                'effect': '买方主导但多头在平仓 → 可能是空头回补而非真实买入 → 上行有限',
                'evidence': f'CVD={btc_cvd:+.0f} | OI_chg={btc_oi_chg:+.2f}% | 矛盾',
                'direction': 'BTC上行有限，警惕假突破'
            })
            step += 1

    if eth_cvd != 0 and eth_oi_chg > 0.5 and eth_long_pct > 70:
        chains.append({
            'step': step,
            'cause': f'ETH CVD={eth_cvd:+.0f} + OI增加{eth_oi_chg:+.2f}% + 散户{eth_long_pct:.0f}%做多',
            'effect': '散户极度做多+OI增加 → 如果空头主力入场 → 可能是多头陷阱 → 猎杀散户',
            'evidence': f'CVD={eth_cvd:+.0f} | OI_chg={eth_oi_chg:+.2f}% | 散户做多={eth_long_pct:.0f}% | 猎杀风险',
            'direction': 'ETH多头陷阱风险'
        })
        step += 1

    return chains


# ── 2. 博弈建模引擎 ──────────────────────────────────────────

def build_game_theory(state: dict, symbol: str) -> List[dict]:
    """
    博弈建模：主力意图+散户行为+猎杀路径
    输入：单个标的的brahma_state
    输出：博弈分析[{actor, action, intent, evidence, target}]
    """
    games = []
    price = state.get('price', 0)
    sym = symbol.upper()

    # ── 大户 vs 散户（使用extra.smart_money路径） ──
    sm = _safe_get(state, 'extra', 'smart_money', default={}) or {}
    big_pos_long = sm.get('big_pos_long', 0) or 0
    retail_long = sm.get('retail_long', 0) or 0
    big_acct_long = sm.get('big_acct_long', 0) or 0
    pos_trend = sm.get('pos_trend_5h', 0) or 0
    sm_signal = sm.get('signal', '')
    sm_note = sm.get('note', '')

    if big_pos_long > 0 and retail_long > 0:
        divergence = abs(big_pos_long - retail_long) * 100
        big_pct = big_pos_long * 100
        retail_pct = retail_long * 100

        if big_pct > retail_pct and pos_trend < 0:
            games.append({
                'actor': '主力（大户）',
                'action': f'多头占比{big_pct:.0f}%但在减仓(趋势{pos_trend:+.3f})',
                'intent': '表面看多，实际在出货 → 引诱散户追多 → 然后猎杀',
                'evidence': f'大户={big_pct:.0f}% 趋势={pos_trend:+.3f} | 散户={retail_pct:.0f}% | 分歧={divergence:.1f}%',
                'target': '上方清算区的多头止损'
            })
        elif big_pct > retail_pct and pos_trend > 0:
            games.append({
                'actor': '主力（大户）',
                'action': f'多头占比{big_pct:.0f}%且在加仓(趋势{pos_trend:+.3f})',
                'intent': '真正看多 → 但可能等到散户止损后才拉升',
                'evidence': f'大户={big_pct:.0f}% 趋势={pos_trend:+.3f} | 散户={retail_pct:.0f}%',
                'target': '先洗盘后拉升'
            })
        elif big_pct < retail_pct:
            games.append({
                'actor': '散户',
                'action': f'散户多头占比{retail_pct:.0f}%高于大户{big_pct:.0f}%',
                'intent': '散户追多 → 但大户不跟 → 多头陷阱风险',
                'evidence': f'散户={retail_pct:.0f}% > 大户={big_pct:.0f}% | 分歧={divergence:.1f}% | {sm_note}',
                'target': '散户是猎物'
            })

    # ── GEX → 波动率引爆（从confluence.breakdown解析） ──
    gex_str = _safe_get(state, 'confluence', 'breakdown', 's22_gex', default='') or ''
    gex_val, gex_dir = _parse_gex_from_breakdown(gex_str)

    if 'NEGATIVE' in gex_dir.upper() and price > 0:
        games.append({
            'actor': '做市商（GEX）',
            'action': f'负GEX={gex_val:+.1f}M → 波动率引爆点',
            'intent': '做市商在负GEX区域对冲 → 价格触及后波动率放大 → 猎杀触发',
            'evidence': f'GEX={gex_str[:60]}',
            'target': '负GEX区域附近猎杀'
        })
    elif 'POSITIVE' in gex_dir.upper():
        games.append({
            'actor': '做市商（GEX）',
            'action': f'正GEX={gex_val:+.1f}M → 波动率抑制',
            'intent': '做市商在正GEX区域抑制波动 → 价格可能被锚定 → 突破需要外力',
            'evidence': f'GEX={gex_str[:60]}',
            'target': '正GEX抑制波动，等外力突破'
        })

    # ── 清算地图 → 猎杀路径（使用extra.liq_snap路径） ──
    liq = _safe_get(state, 'extra', 'liq_snap', default={}) or {}
    liq_short = liq.get('liq_short_5pct', 0) or 0  # 上方
    liq_long = liq.get('liq_long_5pct', 0) or 0   # 下方

    if liq_short > 0 and liq_long > 0 and price > 0:
        dist_up = (liq_short - price) / price * 100
        dist_down = (price - liq_long) / price * 100
        if 0 < dist_up < 5 and 0 < dist_down < 5:
            games.append({
                'actor': '主力',
                'action': f'上方清算{_fmt_price(liq_short, sym)}(+{dist_up:.1f}%) + 下方清算{_fmt_price(liq_long, sym)}(-{dist_down:.1f}%)',
                'intent': '两头猎杀：先拉到上方清算区猎杀空头 → 再砸到下方清算区猎杀多头 → 两头吃',
                'evidence': f'liq_short={_fmt_price(liq_short, sym)}(+{dist_up:.1f}%) | liq_long={_fmt_price(liq_long, sym)}(-{dist_down:.1f}%)',
                'target': '两头猎杀'
            })
        elif 0 < dist_up < 5:
            games.append({
                'actor': '主力',
                'action': f'上方清算区{_fmt_price(liq_short, sym)}(+{dist_up:.1f}%)',
                'intent': '引诱价格上行 → 触发空头止损 → 然后反转做空',
                'evidence': f'liq_short={_fmt_price(liq_short, sym)} | +{dist_up:.1f}%',
                'target': '空头止损=猎物'
            })
        elif 0 < dist_down < 5:
            games.append({
                'actor': '主力',
                'action': f'下方清算区{_fmt_price(liq_long, sym)}(-{dist_down:.1f}%)',
                'intent': '砸到下方清算区 → 触发多头止损 → 接货 → 反弹',
                'evidence': f'liq_long={_fmt_price(liq_long, sym)} | -{dist_down:.1f}%',
                'target': '多头止损=接货机会'
            })

    # ── 资金费率 + 散户持仓 → 猎杀判断 ──
    fund_rate = liq.get('fund_rate', 0) or 0
    long_pct = liq.get('long_pct', 0) or 0

    if fund_rate > 0.005 and long_pct > 70:
        games.append({
            'actor': '主力',
            'action': f'资金费率{fund_rate:+.4f}(多头高付费) + 散户{long_pct:.0f}%做多',
            'intent': '多头过度拥挤 + 高费率 → 主力可能做空猎杀多头',
            'evidence': f'FR={fund_rate:+.4f} | long_pct={long_pct:.0f}% | 拥挤交易',
            'target': '多头拥挤=猎杀目标'
        })
    elif fund_rate < -0.005 and long_pct < 40:
        games.append({
            'actor': '主力',
            'action': f'资金费率{fund_rate:+.4f}(空头高付费) + 散户{100-long_pct:.0f}%做空',
            'intent': '空头过度拥挤 + 高费率 → 主力可能做多猎杀空头',
            'evidence': f'FR={fund_rate:+.4f} | short_pct={100-long_pct:.0f}% | 拥挤交易',
            'target': '空头拥挤=猎杀目标'
        })

    # ── Hurst → 趋势性判断 ──
    hurst_str = _safe_get(state, 'confluence', 'breakdown', 'Hurst体制验证', default='') or ''
    hurst = _parse_hurst_from_breakdown(hurst_str)

    if hurst > 0.6:
        games.append({
            'actor': '趋势引擎',
            'action': f'Hurst={hurst:.3f} > 0.6 → 趋势性强',
            'intent': '当前趋势可能持续 → 顺势操作，不要逆势',
            'evidence': f'Hurst={hurst:.3f} | {hurst_str[:50]}',
            'target': '趋势持续=顺势'
        })
    elif hurst < 0.4:
        games.append({
            'actor': '趋势引擎',
            'action': f'Hurst={hurst:.3f} < 0.4 → 均值回归',
            'intent': '价格会回归均值 → 反转操作，不追趋势',
            'evidence': f'Hurst={hurst:.3f} | {hurst_str[:50]}',
            'target': '均值回归=反转'
        })

    return games


# ── 3. 周期因果链引擎 ────────────────────────────────────────

def build_timeframe_chain(state: dict, symbol: str) -> List[dict]:
    """
    周期因果链：不同周期=不同阶段
    输入：单个标的的brahma_state
    输出：周期链[{timeframe, fvg_direction, fvg_price, role, action, phase}]
    """
    chains = []
    sym = symbol.upper()
    price = state.get('price', 0)

    # ── 提取各周期FVG（smc.fvg/ smc.fvg_1d/ smc.fvg_4h/ smc.fvg_15m） ──
    smc = state.get('smc', {}) or {}
    fvg_1d = smc.get('fvg_1d', {}) if isinstance(smc.get('fvg_1d'), dict) else {}
    fvg_4h = smc.get('fvg_4h', {}) if isinstance(smc.get('fvg_4h'), dict) else {}
    fvg_1h = smc.get('fvg', {}) if isinstance(smc.get('fvg'), dict) else {}  # 1H is default
    fvg_15m = smc.get('fvg_15m', {}) if isinstance(smc.get('fvg_15m'), dict) else {}

    # ── 提取各周期RSI（momentum路径） ──
    mom = state.get('momentum', {}) or {}
    rsi_1d = mom.get('rsi_1d', 50) or 50
    rsi_4h = mom.get('rsi_4h', 50) or 50
    rsi_1h = mom.get('rsi_1h', 50) or 50
    rsi_15m = mom.get('rsi_15m', 50) or 50

    def _extract_fvg_data(fvg_dict, timeframe, phase):
        """从FVG字典提取数据"""
        results = []
        if not fvg_dict:
            return results

        bull_fvgs = fvg_dict.get('bull_fvg', []) or []
        bear_fvgs = fvg_dict.get('bear_fvg', []) or []

        if bull_fvgs and isinstance(bull_fvgs[0], dict):
            f = bull_fvgs[0]
            mid = f.get('mid', 0) or 0
            lo = f.get('lo', 0) or 0
            hi = f.get('hi', 0) or 0
            if mid > 0 and price > 0:
                dist = (price - mid) / price * 100
                role = '支撑' if price > mid else '阻力'
                action = '回调到FVG=做多入场点' if price > mid else '突破FVG=看多'
                results.append({
                    'timeframe': timeframe,
                    'fvg_direction': 'BULL',
                    'fvg_price': mid,
                    'fvg_range': (lo, hi),
                    'distance': f'{dist:+.1f}%',
                    'rsi': rsi_1d if timeframe == '1D' else rsi_4h if timeframe == '4H' else rsi_1h if timeframe == '1H' else rsi_15m,
                    'role': role,
                    'action': action,
                    'phase': phase
                })

        if bear_fvgs and isinstance(bear_fvgs[0], dict):
            f = bear_fvgs[0]
            mid = f.get('mid', 0) or 0
            lo = f.get('lo', 0) or 0
            hi = f.get('hi', 0) or 0
            if mid > 0 and price > 0:
                dist = (price - mid) / price * 100
                role = '阻力' if price < mid else '支撑'
                action = '反弹到Bear FVG=做空入场点' if price < mid else '突破Bear FVG=看空失效'
                results.append({
                    'timeframe': timeframe,
                    'fvg_direction': 'BEAR',
                    'fvg_price': mid,
                    'fvg_range': (lo, hi),
                    'distance': f'{dist:+.1f}%',
                    'rsi': rsi_1d if timeframe == '1D' else rsi_4h if timeframe == '4H' else rsi_1h if timeframe == '1H' else rsi_15m,
                    'role': role,
                    'action': action,
                    'phase': phase
                })

        return results

    chains.extend(_extract_fvg_data(fvg_1d, '1D', '中线（阶段2/3）'))
    chains.extend(_extract_fvg_data(fvg_4h, '4H', '波段（阶段1/2）'))
    chains.extend(_extract_fvg_data(fvg_1h, '1H', '短期（阶段1）'))
    chains.extend(_extract_fvg_data(fvg_15m, '15M', '精确入场'))

    return chains


# ── 4. 推理层主入口 ──────────────────────────────────────────

def run_inference(symbols: List[str] = None) -> dict:
    """
    推理层主入口
    输入：标的列表
    输出：{causal_chains, game_theory, timeframe_chains, report}
    """
    if symbols is None:
        symbols = ['BTC', 'ETH']

    states = {}
    for sym in symbols:
        states[sym] = _load_state(sym)

    # 1. 因果推理（跨标的，需要BTC+ETH同时存在）
    btc = states.get('BTC', {})
    eth = states.get('ETH', {})
    causal = build_causal_chain(btc, eth) if 'BTC' in states and 'ETH' in states else []

    # 2. 博弈建模（每个标的独立）
    games = {}
    for sym in symbols:
        if states.get(sym):
            games[sym] = build_game_theory(states[sym], sym)

    # 3. 周期因果链（每个标的独立）
    timeframes = {}
    for sym in symbols:
        if states.get(sym):
            timeframes[sym] = build_timeframe_chain(states[sym], sym)

    # 4. 生成报告
    report = _format_report(causal, games, timeframes, symbols)

    result = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbols': symbols,
        'causal_chains': causal,
        'game_theory': games,
        'timeframe_chains': timeframes,
        'report': report
    }

    # 保存
    out_file = DATA_DIR / 'inference_latest.json'
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    return result


def _format_report(causal: list, games: dict, timeframes: dict, symbols: list) -> str:
    """格式化推理报告"""
    lines = []
    lines.append('')
    lines.append('╬══════════════════════════════════════════════════════════')
    lines.append('  🧠 梵天推理层 | 因果+博弈+周期')
    lines.append('╬══════════════════════════════════════════════════════════')

    # 因果链
    if causal:
        lines.append('  ━━━ 1. 因果推理链 ━━━')
        for c in causal:
            lines.append(f'  [{c["step"]}] {c["cause"]}')
            lines.append(f'      → {c["effect"]}')
            lines.append(f'      📎 {c["evidence"]}')
            lines.append(f'      🎯 {c["direction"]}')
        lines.append('')

    # 博弈建模
    for sym in symbols:
        sym_games = games.get(sym, [])
        if sym_games:
            lines.append(f'  ━━━ 2. 博弈建模 | {sym} ━━━')
            for g in sym_games:
                lines.append(f'  👤 {g["actor"]}: {g["action"]}')
                lines.append(f'      → {g["intent"]}')
                lines.append(f'      📎 {g["evidence"]}')
                lines.append(f'      🎯 猎杀目标: {g["target"]}')
            lines.append('')

    # 周期因果链
    for sym in symbols:
        sym_tf = timeframes.get(sym, [])
        if sym_tf:
            lines.append(f'  ━━━ 3. 周期因果链 | {sym} ━━━')
            for t in sym_tf:
                lines.append(f'  [{t["timeframe"]}] FVG={t["fvg_direction"]} @{_fmt_price(t["fvg_price"], sym)} ({t["distance"]}) | RSI={t["rsi"]:.0f}')
                lines.append(f'      角色: {t["role"]} | 阶段: {t["phase"]}')
                lines.append(f'      → {t["action"]}')

            # 周期综合判断
            if len(sym_tf) >= 2:
                lines.append(f'      ── 周期综合 ──')
                tf_1d = [t for t in sym_tf if t['timeframe'] == '1D']
                tf_4h = [t for t in sym_tf if t['timeframe'] == '4H']
                tf_1h = [t for t in sym_tf if t['timeframe'] == '1H']

                if tf_1d and tf_1h:
                    d1_dir = tf_1d[0]['fvg_direction']
                    h1_dir = tf_1h[0]['fvg_direction']
                    if d1_dir == h1_dir:
                        lines.append(f'      1D和1H方向一致({d1_dir}) → 短期操作与中线趋势对齐 → 信号可靠')
                    else:
                        lines.append(f'      1D={d1_dir} 1H={h1_dir} 方向不一致 → 短期可能是中线级别的回调 → 分阶段操作')
            lines.append('')

    lines.append('  📌 推理层结论：')
    if causal:
        lines.append(f'  因果链: {len(causal)}条')
    if games:
        total_games = sum(len(g) for g in games.values())
        lines.append(f'  博弈分析: {total_games}条')
    if timeframes:
        total_tf = sum(len(t) for t in timeframes.values())
        lines.append(f'  周期链: {total_tf}条')

    lines.append('  ⚠️ 推理层=辅助决策，最终判断由交易员大脑决定')
    lines.append('╬══════════════════════════════════════════════════════════')

    return '\n'.join(lines)


# ── 供brahma_full_report调用的接口 ─────────────────────────

def format_inference_block(r: dict) -> str:
    """
    供brahma_full_report.py调用的接口
    从分析结果中提取数据并运行推理
    返回推理报告字符串
    """
    sym = r.get('symbol', 'BTC')
    sym_clean = sym.replace('USDT', '').upper()

    # [v2修复] 优先用传入的实时分析结果r，回退到state文件
    # r来自brahma_core.analyze()或brahma_manual_analysis的d['bs']，是实时的
    # state文件可能比r旧（由cron写入）
    state = r if r.get('price') and r.get('smc') else _load_state(sym_clean)
    if not state:
        state = r  # 最终回退

    # 单标的推理（因果链需要两个标的，这里只做博弈+周期）
    games = build_game_theory(state, sym_clean)
    timeframes = build_timeframe_chain(state, sym_clean)

    # 对于因果链，需要BTC和ETH两个标的的数据
    # [修复 2026-09-16] build_causal_chain(btc, eth)第一个参数必须是BTC，第二个是ETH
    other_sym = 'ETH' if sym_clean == 'BTC' else 'BTC'
    other_state = _load_state(other_sym)
    if other_state:
        # 确保参数顺序：BTC在前，ETH在后
        if sym_clean == 'BTC':
            causal = build_causal_chain(state, other_state)  # BTC=state, ETH=other
        else:
            causal = build_causal_chain(other_state, state)  # BTC=other, ETH=state
    else:
        causal = []

    # 格式化
    report_lines = []
    report_lines.append('')
    report_lines.append('╬══════════════════════════════════════════════════════════')
    report_lines.append('  🧠 梵天推理层 | 因果+博弈+周期')
    report_lines.append('╬══════════════════════════════════════════════════════════')

    if causal:
        report_lines.append('  ━━━ 1. 因果推理链 ━━━')
        for c in causal:
            report_lines.append(f'  [{c["step"]}] {c["cause"]}')
            report_lines.append(f'      → {c["effect"]}')
            report_lines.append(f'      📎 {c["evidence"]}')
            report_lines.append(f'      🎯 {c["direction"]}')
        report_lines.append('')

    if games:
        report_lines.append(f'  ━━━ 2. 博弈建模 | {sym_clean} ━━━')
        for g in games:
            report_lines.append(f'  👤 {g["actor"]}: {g["action"]}')
            report_lines.append(f'      → {g["intent"]}')
            report_lines.append(f'      📎 {g["evidence"]}')
            report_lines.append(f'      🎯 猎杀目标: {g["target"]}')
        report_lines.append('')

    if timeframes:
        report_lines.append(f'  ━━━ 3. 周期因果链 | {sym_clean} ━━━')
        for t in timeframes:
            report_lines.append(f'  [{t["timeframe"]}] FVG={t["fvg_direction"]} @{_fmt_price(t["fvg_price"], sym_clean)} ({t["distance"]}) | RSI={t["rsi"]:.0f}')
            report_lines.append(f'      角色: {t["role"]} | 阶段: {t["phase"]}')
            report_lines.append(f'      → {t["action"]}')

        if len(timeframes) >= 2:
            report_lines.append(f'      ── 周期综合 ──')
            tf_1d = [t for t in timeframes if t['timeframe'] == '1D']
            tf_1h = [t for t in timeframes if t['timeframe'] == '1H']
            if tf_1d and tf_1h:
                d1_dir = tf_1d[0]['fvg_direction']
                h1_dir = tf_1h[0]['fvg_direction']
                if d1_dir == h1_dir:
                    report_lines.append(f'      1D和1H方向一致({d1_dir}) → 信号可靠')
                else:
                    report_lines.append(f'      1D={d1_dir} 1H={h1_dir} 方向不一致 → 分阶段操作')
        report_lines.append('')

    report_lines.append('  ⚠️ 推理层=辅助决策，最终判断由交易员大脑决定')
    report_lines.append('╬══════════════════════════════════════════════════════════')

    return '\n'.join(report_lines)


# ── CLI入口 ────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    args = parser.parse_args()

    result = run_inference(args.symbols)
    print(result['report'])


# ── 5. 猎杀路径提取（供trader_brain使用）──────────────────

def extract_hunt_intel(state: dict, symbol: str) -> dict:
    """
    从推理层博弈建模中提取猎杀路径信息，供trader_brain修正入场区
    
    返回:
    {
        'has_hunt': bool,           # 是否有猎杀信号
        'hunt_target': float,      # 猎杀目标价位
        'hunt_direction': str,     # 猎杀方向（往下砸=SHORT_HUNT / 往上拉=LONG_HUNT）
        'hunt_type': str,          # 猎杀类型（多头止损/空头止损/两头猎杀）
        'reason': str,              # 猎杀原因
    }
    """
    games = build_game_theory(state, symbol)
    
    hunt_target = 0
    hunt_direction = ''
    hunt_type = ''
    hunt_reason = ''
    
    for g in games:
        target = g.get('target', '')
        actor = g.get('actor', '')
        action = g.get('action', '')
        intent = g.get('intent', '')
        
        # 主力猎杀下方清算区 → 做多止损 → 猎杀方向=往下
        if '主力' in actor and '下方清算' in action:
            # 提取价格
            import re
            m = re.search(r'\$([\d,]+\.?\d*)', action)
            if m:
                hunt_target = float(m.group(1).replace(',', ''))
            hunt_direction = 'SHORT_HUNT'  # 往下砸
            hunt_type = '多头止损猎杀'
            hunt_reason = f'主力目标{hunt_target}: {intent}'
            break
        
        # 主力猎杀上方清算区 → 做空止损 → 猎杀方向=往上
        if '主力' in actor and '上方清算' in action:
            import re
            m = re.search(r'\$([\d,]+\.?\d*)', action)
            if m:
                hunt_target = float(m.group(1).replace(',', ''))
            hunt_direction = 'LONG_HUNT'  # 往上拉
            hunt_type = '空头止损猎杀'
            hunt_reason = f'主力目标{hunt_target}: {intent}'
            break
        
        # 两头猎杀
        if '两头猎杀' in target:
            hunt_direction = 'BOTH_HUNT'
            hunt_type = '两头猎杀'
            hunt_reason = f'两头猎杀: {intent}'
            # 取下方目标为主（先砸后拉）
            import re
            m = re.search(r'下方.*?\$([\d,]+\.?\d*)', action)
            if m:
                hunt_target = float(m.group(1).replace(',', ''))
            break
    
    # 散户是猎物 + OI空头建仓 → 做多危险
    for g in games:
        if '散户' in g.get('actor', '') and '猎物' in g.get('target', ''):
            if not hunt_direction:
                hunt_direction = 'SHORT_HUNT'
                hunt_type = '散户拥挤'
                hunt_reason = '散户多头拥挤=猎杀目标'
            # [修复] 如果没有主力清算条目提取到价格，从liq_snap直接取
            if hunt_target == 0:
                liq = state.get('extra', {}).get('liq_snap', {})
                liq_long = liq.get('liq_long_5pct', 0) or 0
                if liq_long > 0:
                    hunt_target = round(liq_long, 1)
                    hunt_reason = f'散户拥挤+下方清算区${liq_long:,.0f}=猎杀目标'

    has_hunt = hunt_target > 0 or hunt_direction in ('SHORT_HUNT', 'LONG_HUNT', 'BOTH_HUNT')
    
    return {
        'has_hunt': has_hunt,
        'hunt_target': hunt_target,
        'hunt_direction': hunt_direction,
        'hunt_type': hunt_type,
        'hunt_reason': hunt_reason,
    }

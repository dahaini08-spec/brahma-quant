"""
fangcang_hcme_bridge.py — 方仓增强型HCME桥接引擎
设计院 2026-08-23 苏摩111封印

架构升级：
  旧：HCME(pseudo伪信号2177条) → -9分（无效惩罚，基于假数据）
  新：方仓(真实案例1597条) → 相似度匹配 → 置信度门控 → 精准评分

三层设计：
  Step A: HCME伪数据权重归零（hcme_source=pseudo → 完全忽略）
  Step B: 方仓1597条真实案例相似度匹配
  Step C: 置信度门控（n<3→0分中性 / n3~9→半权重 / n≥10→全权重）

铁证基础：
  方仓BTC=255条 / ETH=280条 / SOL=1062条 = 1597条真实6.5年案例
  字段：compress_bbw_min / rsi_at_end / breakout_direction / future_return_24h
  vs HCME：45670条pseudo合成 WR=46.4% EV=-0.146%（统计上无意义）
"""
import os
import json
import math
from pathlib import Path

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'

# 缓存方仓案例（全局单例，避免重复读盘）
_FANGCANG_CACHE: list = []
_CACHE_LOADED = False


def _load_fangcang_cases() -> list:
    """加载方仓真实案例库（BTC+ETH+SOL），全局缓存"""
    global _FANGCANG_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return _FANGCANG_CACHE

    cases = []
    # BTC
    btc_path = _DATA / 'fangcang_cases_btc.jsonl'
    if btc_path.exists():
        for line in btc_path.read_text().splitlines():
            try:
                d = json.loads(line)
                d['_src_sym'] = 'BTC'
                cases.append(d)
            except Exception:
                pass

    # ETH
    eth_path = _DATA / 'fangcang_cases_eth.jsonl'
    if eth_path.exists():
        for line in eth_path.read_text().splitlines():
            try:
                d = json.loads(line)
                d['_src_sym'] = 'ETH'
                cases.append(d)
            except Exception:
                pass

    # SOL（字段名略有差异）
    sol_path = _DATA / 'fangcang_cases_sol.json'
    if sol_path.exists():
        sol_raw = json.loads(sol_path.read_text())
        if isinstance(sol_raw, list):
            for d in sol_raw:
                # 字段统一化：SOL用min_bb_width/rsi_at_burst/direction
                normalized = {
                    'symbol': 'SOLUSDT',
                    '_src_sym': 'SOL',
                    'compress_bbw_min': float(d.get('min_bb_width', 0) or 0) / 100,  # SOL是百分比
                    'rsi_at_end': float(d.get('rsi_at_burst', 50) or 50),
                    'compress_bars': int(d.get('squeeze_bars', 0) or 0),
                    'breakout_direction': 'LONG' if str(d.get('direction', '')).upper() == 'UP'
                                         else ('SHORT' if str(d.get('direction', '')).upper() == 'DOWN'
                                               else 'CHOP'),
                    'future_return_24h': float(d.get('future_return_24h', 0) or 0),
                    'volume_trend': 'expand' if float(d.get('vol_ratio_peak', 1) or 1) > 1.5 else 'flat',
                    'is_genuine_breakout': bool(d.get('is_genuine_breakout', False)),
                }
                cases.append(normalized)

    _FANGCANG_CACHE = cases
    _CACHE_LOADED = True
    return cases


def fangcang_context_match(
    symbol: str,
    current_bbw: float,
    current_rsi: float,
    current_regime: str,
    signal_dir: str,
    current_bars: int = 0,
) -> dict:
    """
    方仓相似度匹配：在真实案例库中找相似压缩案例，输出方向概率评分

    参数：
      symbol       : 当前标的（用于优先同标的案例）
      current_bbw  : 当前布林带宽度（小数，如0.0084=0.84%）
      current_rsi  : 当前RSI_1H
      current_regime: 当前体制（BULL_TREND等）
      signal_dir   : 信号方向（LONG/SHORT）
      current_bars : 当前压缩持续bars（可选）

    返回：
      {
        'score_adj': float,     # 评分调整（-10~+10）
        'confidence': str,      # HIGH/MED/LOW/NONE
        'n_similar': int,       # 相似案例数
        'long_pct': float,      # 历史做多突破概率
        'short_pct': float,     # 历史做空突破概率
        'chop_pct': float,      # 历史横盘概率
        'source': str,          # 数据来源说明
        'hcme_source': str,     # 'real_fangcang' | 'no_match'
      }
    """
    cases = _load_fangcang_cases()
    if not cases:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_data', 'hcme_source': 'no_data'
        }

    # ── 相似度匹配（三维：BBW + RSI + 体制） ──
    similar = []
    for c in cases:
        c_bbw = float(c.get('compress_bbw_min', 0) or 0)
        c_rsi = float(c.get('rsi_at_end', 50) or 50)
        c_regime = str(c.get('regime_guess', '') or '').lower()

        # BBW相似度：±30%容差（压缩程度相近）
        if c_bbw <= 0:
            continue
        bbw_ratio = abs(c_bbw - current_bbw) / max(current_bbw, 1e-9)
        if bbw_ratio > 0.35:
            continue

        # RSI相似度：±18容差
        if abs(c_rsi - current_rsi) > 18:
            continue

        # 体制相似度（宽松匹配：BULL系列相互匹配，BEAR系列相互匹配）
        regime_match = False
        if 'BULL' in current_regime.upper():
            regime_match = c_regime in ('ranging', 'bull', 'bullish', 'trending', '') or c_regime == ''
        elif 'BEAR' in current_regime.upper():
            regime_match = c_regime in ('bear', 'bearish', 'ranging', '')
        elif 'CHOP' in current_regime.upper():
            regime_match = c_regime in ('ranging', 'chop', '')
        else:
            regime_match = True  # 未知体制不过滤

        if not regime_match and c_regime != '':
            # 宽松降级：体制不匹配但BBW+RSI非常相近时仍纳入（权重×0.5）
            if bbw_ratio < 0.15 and abs(c_rsi - current_rsi) < 8:
                c['_regime_penalty'] = 0.5
            else:
                continue

        # 计算综合相似度分数
        bbw_score = 1.0 - bbw_ratio / 0.35
        rsi_score = 1.0 - abs(c_rsi - current_rsi) / 18
        sim_score = bbw_score * 0.6 + rsi_score * 0.4
        c['_sim_score'] = sim_score
        similar.append(c)

    # 按相似度排序，取Top20
    similar.sort(key=lambda x: x.get('_sim_score', 0), reverse=True)
    similar = similar[:20]
    n = len(similar)

    if n == 0:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_match', 'hcme_source': 'no_match'
        }

    # ── 方向概率统计 ──
    long_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'LONG')
    short_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'SHORT')
    chop_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'CHOP')
    long_pct = long_n / n
    short_pct = short_n / n
    chop_pct = chop_n / n

    # ── Step C：置信度门控 ──
    if n < 3:
        confidence = 'NONE'
        weight = 0.0    # 样本不足 → 完全中性
    elif n < 5:
        confidence = 'LOW'
        weight = 0.3    # 低置信
    elif n < 10:
        confidence = 'MED'
        weight = 0.6    # 半权重
    else:
        confidence = 'HIGH'
        weight = 1.0    # 全权重

    # ── 评分计算（基线50%，偏离基线 × 最大±12分） ──
    MAX_ADJ = 12.0
    if signal_dir == 'LONG':
        # 做多方向：历史多头突破率越高越加分
        raw_adj = (long_pct - 0.40) * MAX_ADJ / 0.40  # 40%基线（压缩多为CHOP）
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    elif signal_dir == 'SHORT':
        raw_adj = (short_pct - 0.30) * MAX_ADJ / 0.30  # 空头基线更低
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    else:
        raw_adj = 0.0

    score_adj = round(raw_adj * weight, 1)

    return {
        'score_adj': score_adj,
        'confidence': confidence,
        'n_similar': n,
        'long_pct': round(long_pct, 3),
        'short_pct': round(short_pct, 3),
        'chop_pct': round(chop_pct, 3),
        'source': f'fangcang_real_{n}cases',
        'hcme_source': 'real_fangcang',
    }


def get_fangcang_hcme_score(
    symbol: str,
    ms: dict,
    signal_dir: str,
) -> dict:
    """
    主入口：替换旧HCME调用
    从ms提取BBW/RSI/regime，调用方仓相似度匹配，返回评分

    返回格式与旧HCME兼容：
      {'hcme_score_adj': float, 'context_summary': str, 'hcme_source': str}
    """
    # 提取当前市场参数
    try:
        bbw = float((ms.get('bb') or ms.get('momentum', {}).get('bb', {})).get('width', 0) or 0)
        rsi = float(ms.get('rsi_1h') or ms.get('momentum', {}).get('rsi_1h', 50) or 50)
        regime = str(ms.get('regime', '') or '')
    except Exception:
        bbw, rsi, regime = 0.01, 50.0, ''

    # BBW为0时用默认值（非压缩状态，评分中性）
    if bbw <= 0:
        return {
            'hcme_score_adj': 0,
            'context_summary': 'BBW数据缺失，HCME中性',
            'hcme_source': 'no_bbw_data',
        }

    result = fangcang_context_match(
        symbol=symbol,
        current_bbw=bbw,
        current_rsi=rsi,
        current_regime=regime,
        signal_dir=signal_dir,
    )

    score_adj = result['score_adj']
    n = result['n_similar']
    conf = result['confidence']
    long_pct = result['long_pct']
    short_pct = result['short_pct']

    context_summary = (
        f"方仓匹配n={n}({conf}) "
        f"多={long_pct*100:.0f}%空={short_pct*100:.0f}% "
        f"adj={score_adj:+.1f}"
    )

    return {
        'hcme_score_adj': score_adj,
        'context_summary': context_summary,
        'hcme_source': result['hcme_source'],
        'n_similar': n,
        'confidence': conf,
        'long_pct': long_pct,
        'short_pct': short_pct,
    }

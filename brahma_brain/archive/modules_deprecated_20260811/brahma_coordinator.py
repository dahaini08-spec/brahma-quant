#!/usr/bin/env python3
"""
brahma_coordinator.py — 梵天系统全局协同总线 v1.0
设计院×达摩院 封印 2026-07-20 苏摩111批准

职责：在brahma_engine.analyze()前聚合所有子系统知识，
     形成统一的"上下文包"注入评分链路。

子系统接入地图：
  暴涨猎手 pump_state.json → pump_context (TIGHT状态+预警等级)
  OI扫描器 pre_filter_candidates.json → oi_context (OI触发评分)
  情景记忆 episodic_memory/ → episodic_context (历史相似场景WR)
  IC追踪器 ic_tracker_state.json → ic_context (维度IC+EV分桶)
  SSI引擎  ssi_state.json → ssi_context (轧空风险等级)
  宏观引擎  macro_state.json → macro_context (DXY/FG/FOMC)
  信号过期  signal_expiry.json → expiry_context (信号有效期)
  鲸鱼追踪  whale_data.json → whale_context (大户持仓方向)

输出：CoordContext dict，直接传入 analyze() extra_data
"""

import json, os, time
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent
_CACHE = {}
_CACHE_TTL = 60  # 秒


def _load_json(path: str, default=None):
    """带缓存的json读取"""
    now = time.time()
    if path in _CACHE and now - _CACHE[path]['ts'] < _CACHE_TTL:
        return _CACHE[path]['data']
    try:
        full = BASE / path
        if full.exists():
            data = json.loads(full.read_text())
            _CACHE[path] = {'ts': now, 'data': data}
            return data
    except Exception:
        pass
    return default or {}


def get_pump_context(symbol: str) -> dict:
    """暴涨猎手：TIGHT压缩状态 + 预警等级"""
    pump_state = _load_json('dharma/pump_hunter/pump_state.json', {})
    watchlist = _load_json('dharma/pump_hunter/watchlist.json', {})
    mode_c = _load_json('data/mode_c_state.json', {})

    sym_state = pump_state.get(symbol, {})
    score = sym_state.get('tight_latest_score', 0)
    hours = sym_state.get('tight_hours', 0)

    # 检查watchlist tier
    tier = 0
    for tier_key, tier_data in watchlist.items():
        if isinstance(tier_data, dict) and 'symbols' in tier_data:
            if symbol in tier_data['symbols']:
                tier = 1 if 'tier1' in tier_key else 2

    # mode_c状态
    mc = mode_c.get(symbol, {})
    mode_c_active = mc.get('mode') == 'MODE_C' and mc.get('short_ban', False)

    return {
        'tight_score': score,
        'tight_hours': hours,
        'watchlist_tier': tier,
        'mode_c_active': mode_c_active,
        'mode_c_source': mc.get('source', ''),
        # 评分加成规则
        'score_bonus': (
            40 if score >= 85 and tier == 1 else
            25 if score >= 75 and tier == 1 else
            15 if score >= 75 else
            8 if score >= 65 else
            0
        ),
        'short_ban': mode_c_active,
    }


def get_oi_context(symbol: str) -> dict:
    """OI扫描器：高频异动状态 + 触发评分"""
    pf = _load_json('data/pre_filter_candidates.json', {})
    candidates = pf.get('candidates', [])
    sym_data = next((c for c in candidates if c.get('symbol') == symbol), None)
    if not sym_data:
        return {'oi_triggered': False, 'oi_score': 0, 'score_bonus': 0, 'vol_ratio': 0}
    return {
        'oi_triggered': True,
        'oi_score': sym_data.get('score', 0),
        'oi_triggers': sym_data.get('triggered', []),
        'vol_ratio': sym_data.get('vol_ratio', 0),
        'rsi_1h': sym_data.get('rsi_1h', 50),
        'bb_width': sym_data.get('bb_width', 0),
        'fr': sym_data.get('fr', 0),
        # 评分加成：OI高分品种在梵天评分中加分
        'score_bonus': min(sym_data.get('score', 0) * 3, 15),
    }


def get_episodic_context(symbol: str, regime: str, direction: str) -> dict:
    """情景记忆：历史相似场景的WR和EV"""
    try:
        sys_path = str(BASE)
        import sys
        if sys_path not in sys.path: sys.path.insert(0, sys_path)
        from data.episodic_memory_manager import get_context_for_analysis
        ctx = get_context_for_analysis(symbol, regime, direction, min_n=3)
        if ctx:
            return {
                'has_history': True,
                'historical_wr': ctx.get('wr', 0),
                'historical_ev': ctx.get('avg_pnl', 0),
                'n_samples': ctx.get('n', 0),
                'score_bonus': 10 if ctx.get('wr', 0) > 0.7 else (-5 if ctx.get('wr', 0) < 0.4 else 0),
            }
    except Exception:
        pass
    return {'has_history': False, 'score_bonus': 0}


def get_ic_context(regime: str, direction: str, score: float) -> dict:
    """IC追踪器：EV分桶结果 + 胜率参考"""
    ic_state = _load_json('data/ic_tracker_state.json', {})
    ev_buckets = ic_state.get('ev_by_bucket', {})

    # 确定score_bin
    if score >= 165: s_bin = '165+'
    elif score >= 155: s_bin = '155-164'
    elif score >= 140: s_bin = '140-154'
    elif score >= 120: s_bin = '120-139'
    else: s_bin = '<120'

    key = f'{regime}:{direction}:{s_bin}'
    bucket = ev_buckets.get(key, {})
    n = bucket.get('n', 0)
    wr = float(bucket.get('wr') or 0)
    ev = float(bucket.get('ev') or 0)

    return {
        'ic_key': key,
        'n': n,
        'wr': wr,
        'ev': ev,
        # 低WR组合的惩罚（n>=10才生效）
        'score_penalty': -8 if (n >= 10 and wr < 0.35) else 0,
        'score_bonus': 5 if (n >= 10 and wr > 0.70) else 0,
    }


def get_macro_context() -> dict:
    """宏观引擎：FG + BTC.D + 下次FOMC"""
    macro = _load_json('data/macro_state.json', {})
    fg = macro.get('fear_greed', {})
    fg_val = float(fg.get('value', fg) if isinstance(fg, dict) else fg or 50)
    return {
        'fear_greed': fg_val,
        'btc_dom': macro.get('btc_dom', 50),
        'macro_score': macro.get('macro_score', 0),
        'next_event': macro.get('next_event', ''),
    }


def build_coord_context(
    symbol: str,
    direction: str,
    regime: str = '',
    score: float = 0,
) -> dict:
    """
    主入口：聚合所有子系统知识，返回CoordContext
    调用方式：extra_data['coord'] = build_coord_context(sym, dir, regime, score)
    """
    pump = get_pump_context(symbol)
    oi   = get_oi_context(symbol)
    epic = get_episodic_context(symbol, regime, direction)
    ic   = get_ic_context(regime, direction, score)
    macro = get_macro_context()

    # 综合评分加成/惩罚
    total_bonus = (
        pump['score_bonus'] +
        oi['score_bonus'] +
        epic['score_bonus'] +
        ic['score_bonus'] +
        ic['score_penalty']
    )

    return {
        'pump': pump,
        'oi': oi,
        'episodic': epic,
        'ic': ic,
        'macro': macro,
        'total_bonus': total_bonus,
        'short_ban_by_pump': pump['short_ban'],
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def format_coord_summary(ctx: dict) -> str:
    """格式化协同上下文摘要（用于日志）"""
    pump = ctx.get('pump', {})
    oi = ctx.get('oi', {})
    ic = ctx.get('ic', {})
    lines = []
    if pump.get('tight_score', 0) >= 65:
        lines.append(f"🎯猎手TIGHT={pump['tight_score']}({pump['tight_hours']:.0f}H)")
    if oi.get('oi_triggered'):
        lines.append(f"📊OI触发={oi['oi_score']}分 vr={oi.get('vol_ratio',0):.1f}x")
    if ic.get('n', 0) >= 5:
        lines.append(f"🧠IC: WR={ic['wr']:.0%} EV={ic['ev']:+.3f}%(n={ic['n']})")
    if ctx.get('total_bonus', 0) != 0:
        lines.append(f"协同加成: {ctx['total_bonus']:+d}分")
    return ' | '.join(lines) if lines else '协同上下文: 无异常信号'


if __name__ == '__main__':
    # 自测
    print("=== brahma_coordinator 自测 ===")
    for sym, d in [('BTCUSDT','LONG'), ('BANKUSDT','LONG'), ('HYPEUSDT','SHORT')]:
        ctx = build_coord_context(sym, d, 'BULL_TREND', 160)
        print(f"\n{sym} {d}:")
        print(f"  pump: tight={ctx['pump']['tight_score']} tier={ctx['pump']['watchlist_tier']} bonus=+{ctx['pump']['score_bonus']}")
        print(f"  oi:   triggered={ctx['oi']['oi_triggered']} bonus=+{ctx['oi']['score_bonus']}")
        print(f"  ic:   WR={ctx['ic']['wr']:.0%} EV={ctx['ic']['ev']:+.3f}% penalty={ctx['ic']['score_penalty']}")
        print(f"  总协同加成: {ctx['total_bonus']:+d}分")
        print(f"  摘要: {format_coord_summary(ctx)}")

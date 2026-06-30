#!/usr/bin/env python3
"""
signal_selector.py — 梵天方向裁决器 v1.0
设计院 2026-06-10

【核心职责】
  接收双向原始分析 + 体制权重
  → 裁决：推送哪个方向（或双向）
  → 输出：加权后的最终信号

【裁决规则】
  1. 对SHORT/LONG各自计算加权分：
       weighted = raw_score × regime_multiplier

  2. |short_w - long_w| >= 15 → 推高分方向（单向）
     |short_w - long_w| <  15 AND chop_prob >= 0.4 → 双向推送（各50%仓位）
     其他 → 推高分方向

  3. 信号有效门槛：weighted_score >= 110（原始门槛140 × 最低乘数0.5=70，
     但逆势信号需要更高原始分才能通过）

【仓位公式】
  base = 2.0%（基础仓位）
  position = base × regime_multiplier × min(raw_score/150, 1.0)
  顺势最高3.0%，逆势最高1.0%，震荡最高1.4%

【持仓时限】
  顺势（乘数≥1.0）→ 无时间止损
  逆势（乘数=0.5）→ 12H强平
  震荡（乘数=0.7）→ 6H强平
"""

import sys
from pathlib import Path

_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_DIR))
sys.path.insert(0, str(_DIR / 'brahma_brain'))

BASE_POSITION   = 2.0   # 基础仓位百分比
MIN_WEIGHTED    = 110   # 最低加权分门槛
SINGLE_DIR_DIFF = 15    # 超过此差值推单向


def select(short_analysis: dict, long_analysis: dict, regime: dict) -> dict:
    """
    核心裁决函数

    参数：
      short_analysis : brahma_analyze(symbol, 'SHORT') 结果
      long_analysis  : brahma_analyze(symbol, 'LONG')  结果
      regime         : regime_scorer.score(symbol) 结果

    返回：
      signals        : list[dict]  要推送的信号列表（0~2个）
      decision       : str         裁决说明
      regime_summary : str         体制摘要
    """
    sym        = (long_analysis.get('symbol') or short_analysis.get('symbol') or regime.get('symbol') or '?')
    # [FIX-N3 2026-06-14] 兼容7体制dict（market_state不输出概率字段，用乘数推断）
    bull_prob  = regime.get('bull_prob', 0.3)
    bear_prob  = regime.get('bear_prob', 0.4)
    chop_prob  = regime.get('chop_prob', 0.3)
    mult_short = regime['multiplier']['SHORT']
    mult_long  = regime['multiplier']['LONG']

    # ── 提取原始分 ──
    def _raw(a: dict) -> float:
        return float(
            a.get('score_final') or
            a.get('score') or
            a.get('confluence', {}).get('total') or 0
        )

    def _grade(a: dict) -> float:
        return float(
            a.get('grade') or
            a.get('confluence', {}).get('structure_grade') or 0
        )

    def _valid(a: dict) -> bool:
        # brahma_analyze --json 输出 valid=false 时仍有参考价值
        # 体制加权后若分数足够，即可推送（selector自己判断门槛）
        return True  # 有效性由 weighted_score >= MIN_WEIGHTED 控制

    short_raw   = _raw(short_analysis)
    long_raw    = _raw(long_analysis)
    short_grade = _grade(short_analysis)
    long_grade  = _grade(long_analysis)
    short_valid = _valid(short_analysis)
    long_valid  = _valid(long_analysis)

    # ── 加权分 ──
    short_w = short_raw * mult_short
    long_w  = long_raw  * mult_long

    # ── 有效性检查 ──
    short_ok = short_valid and short_w >= MIN_WEIGHTED
    long_ok  = long_valid  and long_w  >= MIN_WEIGHTED

    print(f'[Selector] {sym} SHORT raw={short_raw:.0f}×{mult_short}={short_w:.0f}({("✅" if short_ok else "❌")})'
          f' LONG raw={long_raw:.0f}×{mult_long}={long_w:.0f}({("✅" if long_ok else "❌")})')

    # ── [v25.2 2026-06-14 FIX-SSOT] BEAR_TREND/BEAR_EARLY LONG硬封禁（宪法级死穴） ──
    # 铁证：BEAR_TREND_LONG  n=86,120  WR=45.0% avgPnL=-0.265（最惨死穴）
    #       BEAR_EARLY_LONG  n=225,623 WR=49.9% avgPnL=-0.139
    # ⚠️  BEAR_RECOVERY_LONG WR=72.5% avgPnL=+0.255（反直觉alpha，不封禁！）
    # 封禁逻辑：精确匹配 BEAR_TREND/BEAR_EARLY，BEAR_RECOVERY不封禁
    primary_regime = regime.get('regime', regime.get('primary', ''))
    _bear_block = primary_regime in ('BEAR_TREND', 'BEAR_EARLY') or mult_long == 0.0
    if _bear_block:
        long_ok = False
        long_w  = 0.0
        print(f'[Selector] ⚠️ {primary_regime} LONG硬封禁（铁证死穴）')
    elif 'BEAR_RECOVERY' in primary_regime:
        print(f'[Selector] ✅ BEAR_RECOVERY LONG允许（反直觉alpha WR=72.5% n=430）')

    signals = []
    decision = ''

    if not short_ok and not long_ok:
        decision = f'双向均未通过门槛({MIN_WEIGHTED}) SHORT_w={short_w:.0f} LONG_w={long_w:.0f}'
        return {'signals': [], 'decision': decision, 'regime_summary': _regime_summary(regime)}

    # ── 裁决逻辑 ──
    diff = abs(short_w - long_w)

    if short_ok and long_ok and diff < SINGLE_DIR_DIFF and chop_prob >= 0.35:
        # 震荡双向推送
        decision = f'震荡双向 chop={chop_prob:.1%} 差值={diff:.0f}<{SINGLE_DIR_DIFF}'
        signals.append(_build_signal(short_analysis, 'SHORT', mult_short, regime, is_secondary=(long_w >= short_w)))
        signals.append(_build_signal(long_analysis,  'LONG',  mult_long,  regime, is_secondary=(short_w >= long_w)))
    elif short_ok and (not long_ok or short_w >= long_w):
        decision = f'SHORT优先 SHORT_w={short_w:.0f} vs LONG_w={long_w:.0f} 差={diff:.0f}'
        signals.append(_build_signal(short_analysis, 'SHORT', mult_short, regime, is_secondary=False))
    elif long_ok and (not short_ok or long_w > short_w):
        decision = f'LONG优先 LONG_w={long_w:.0f} vs SHORT_w={short_w:.0f} 差={diff:.0f}'
        signals.append(_build_signal(long_analysis, 'LONG', mult_long, regime, is_secondary=False))
    else:
        decision = f'无有效信号'

    return {
        'signals':        signals,
        'decision':       decision,
        'regime_summary': _regime_summary(regime),
        'short_w':        round(short_w, 1),
        'long_w':         round(long_w, 1),
    }


def _build_signal(analysis: dict, direction: str, mult: float,
                  regime: dict, is_secondary: bool) -> dict:
    """构建统一格式信号"""
    raw_score = float(
        analysis.get('score_final') or
        analysis.get('confluence', {}).get('total') or
        analysis.get('score') or 0
    )
    params = analysis.get('params', {})

    # 仓位计算
    quality_factor = min(raw_score / 150, 1.0)
    position_pct   = round(BASE_POSITION * mult * quality_factor, 2)

    # 持仓时限
    if mult >= 1.0:
        time_limit = None           # 顺势，无时间止损
    elif mult == 0.7:
        time_limit = 6              # 震荡，6H
    else:
        time_limit = 12             # 逆势，12H

    # 链条标识
    chain = 'SECONDARY' if is_secondary else 'PRIMARY'
    label = '🟡 反弹辅助单' if is_secondary else '🔵 顺势主力单'
    if regime.get('chop_prob', 0) >= 0.35:
        label = '🟡 震荡双向单'

    return {
        'symbol':      (analysis.get('symbol') or '?'),  # [FIX2 2026-06-14] 从analysis取
        'direction':   direction,
        'chain':       chain,
        'label':       label,
        'raw_score':   raw_score,
        'weighted':    round(raw_score * mult, 1),
        'regime_mult': mult,
        'position_pct': position_pct,
        'time_limit_h': time_limit,
        'entry_lo':    float(params.get('entry_lo', 0)),
        'entry_hi':    float(params.get('entry_hi', 0)),
        'stop_loss':   float(params.get('stop_loss', 0)),
        'tp1':         float(params.get('tp1', 0)),
        'tp2':         float(params.get('tp2', 0)),
        'regime':      regime['primary'],
        'phase':       regime['phase'],
        'momentum':    regime['momentum'],
        'bull_prob':   regime['bull_prob'],
        'bear_prob':   regime['bear_prob'],
        'chop_prob':   regime['chop_prob'],
        'grade':       float(analysis.get('confluence', {}).get('structure_grade') or 0),
        'valid':       bool(analysis.get('valid_signal') or analysis.get('valid')),
        'analysis':    analysis,    # 保留原始分析供pre_trade_engine使用
    }


def _regime_summary(r: dict) -> str:
    # [FIX-SSOT 2026-06-14] 兼容新7体制dict（无 bear_recovery_prob 等字段）
    _label = r.get('primary', r.get('regime', '?'))
    _cn = r.get('regime_cn', _label)
    _bear = r.get('bear_prob', 0)
    _bull = r.get('bull_prob', 0)
    _dominant_prob = max(_bear, _bull, r.get('chop_prob', 0))
    return (f"体制={_label}({_cn}) {_dominant_prob:.0%} "
            f"4H={r.get('phase','?')} 1H={r.get('momentum','?')} "
            f"LONG×{r['multiplier']['LONG']} SHORT×{r['multiplier']['SHORT']}")


def format_signal_card(sig: dict) -> str:
    """格式化信号推送卡片"""
    sym  = sig['symbol'].replace('USDT', '')
    dirn = '▼ 做空' if sig['direction'] == 'SHORT' else '▲ 做多'
    elo  = sig['entry_lo']
    ehi  = sig['entry_hi']
    sl   = sig['stop_loss']
    tp1  = sig['tp1']
    tp2  = sig['tp2']

    def p(v): return f'${v:,.0f}' if v > 100 else f'${v:.4f}'

    risk = abs(elo - sl) if sl and elo else 1
    rr1 = round(abs(tp1 - elo) / max(risk, 1e-9), 1) if tp1 else 0
    rr2 = round(abs(tp2 - elo) / max(risk, 1e-9), 1) if tp2 else 0

    chain_info = ''
    if sig['time_limit_h']:
        chain_info = f'⚠️ {sig["label"]} · 持仓≤{sig["time_limit_h"]}H'
    else:
        chain_info = f'{sig["label"]}'

    time_str = ''
    expire_str = ''
    try:
        from datetime import datetime, timezone, timedelta
        now_dt = datetime.now(timezone.utc)
        expire_dt = now_dt + timedelta(hours=4)
        time_str   = now_dt.isoformat()[:16] + ' UTC'
        expire_str = expire_dt.isoformat()[:16] + ' UTC (有效期4H)'
    except Exception:
        pass

    return f'''{chain_info}
{sym}/USDT {dirn}  加权分={sig["weighted"]:.0f}(原始{sig["raw_score"]:.0f}×{sig["regime_mult"]})
体制={sig["regime"]} 熊={sig["bear_prob"]:.0%} 牛={sig["bull_prob"]:.0%} 震={sig["chop_prob"]:.0%}
━━━━━━━━━━━━━━━━━━━
仓位:    {sig["position_pct"]}%
入场区:  {p(elo)} ~ {p(ehi)}
止损:    {p(sl)}
T1:      {p(tp1)}  R:R={rr1}x
T2:      {p(tp2)}  R:R={rr2}x
━━━━━━━━━━━━━━━━━━━
信号时间: {time_str}
✅ 有效至: {expire_str}
⚠️ 预筛参考 | 需梵天brahma_core确认再操作
⚡ 回复 888 确认开单'''


if __name__ == '__main__':
    # 快速测试
    import sys
    sys.path.insert(0, str(_DIR / 'brahma_brain'))
    # [FIX-SSOT 2026-06-14] 改用 market_state（regime_scorer已废弃）
    from brahma_brain.market_state import analyze as _ms_a
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    _ms = _ms_a(sym)
    print(f'体制={_ms["regime"]} rsi_4h={_ms.get("momentum",{}).get("rsi_4h",0):.1f}')
    print()

    # 模拟双向分析结果
    short_mock = {
        'score_final': 163, 'valid_signal': True,
        'confluence': {'structure_grade': 40, 'total': 163},
        'params': {'entry_lo': 1631.0, 'entry_hi': 1649.0,
                   'stop_loss': 1688.0, 'tp1': 1542.0, 'tp2': 1459.0}
    }
    long_mock = {
        'score_final': 92, 'valid_signal': True,
        'confluence': {'structure_grade': 0, 'total': 92},
        'params': {'entry_lo': 1635.0, 'entry_hi': 1645.0,
                   'stop_loss': 1610.0, 'tp1': 1652.0, 'tp2': 1676.0}
    }
    result = select(short_mock, long_mock, r)
    print(f'裁决: {result["decision"]}')
    print(f'推送信号数: {len(result["signals"])}')
    for s in result['signals']:
        print(format_signal_card(s))
        print()

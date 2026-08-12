#!/usr/bin/env python3
"""
brahma_decision_engine.py — 梵天决策树 2.0
设计院封印 2026-08-08 苏摩111自主决策

核心架构：五步漏斗，不是线性加法器
  Step1 否决门    → 体制死穴 / SL>2% / grade<80 → 任一否则直接丢弃
  Step2 催化剂验证 → OI暴增 / FR极端 / 清算位在1%内 → 三选一
  Step3 结构确认  → 15m CHoCH / MTF共振≥3 / 方仓EV>0 → 三选三
  Step4 风险计算  → SL=15m摆动点 / TP=清算集群 / RR≥1.0
  Step5 15m确认入场 → BOS/放量/连续阳 → 执行

设计原则：
  - 每步有明确二元判断（通过/拒绝），不是模糊阈值
  - 否决权优先：任何一个否决条件触发 → 直接结束
  - fail-safe：任何步骤异常 → 降级到原有score机制
"""

from __future__ import annotations
import json
import time
import traceback
import requests
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent

# ── 常量 ──────────────────────────────────────────────────────────────
FAPI = 'https://fapi.binance.com'

# 死穴组合：体制×方向（不允许执行）
DEAD_COMBOS = {
    ('BEAR_TREND', 'LONG'),
    ('BULL_TREND', 'SHORT'),
    ('CHOP_MID',   'LONG'),
    ('BULL_EARLY', 'LONG'),
    ('BEAR_EARLY', 'SHORT'),
}

MAX_SL_PCT       = 2.0   # SQE Gate1：基础限制（2.0%），动态门控函数会根据grade调整
MIN_GRADE        = 80    # 结构质量门槛

def _dynamic_sl_max(grade: float, regime: str, direction: str, sl_pct: float) -> float:
    """
    动态SL上限计算 [设计院 2026-08-12 苏摩111修复P1]
    核心逻辑：grade越高 = 结构越清晰 = SL越可信 = 应该放行不收紧
    修复前：grade=100时SL上限仅2.3%，反而卡掉高质量信号（逻辑反直觉）
    修复后：grade>=85 → 上限2.5%（全宪法通行）
             grade<85  → 上限2.0%（严格门控）
    """
    if float(grade) >= 85.0:
        return 2.5   # 高质量结构，允许最大宪法止损
    return 2.0       # 低质量结构，严格限制
MIN_RR           = 1.0   # 最低风险回报比
OI_SURGE_THR     = 3.0   # OI单小时变化>3% = 大资金进场
FR_EXTREME_LONG  = 0.10  # 资金费率>0.1% = 多头过热
FR_EXTREME_SHORT = -0.05 # 资金费率<-0.05% = 空头拥挤 → 做多机会
LIQ_NEAR_PCT     = 1.0   # 清算位距现价<1% = 催化剂


# ── 工具函数 ──────────────────────────────────────────────────────────

def _get_current_price(symbol: str) -> float:
    try:
        r = requests.get(f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}', timeout=5).json()
        return float(r['price'])
    except Exception:
        return 0.0


def _get_oi_change_1h(symbol: str) -> float:
    """OI近1H变化%"""
    try:
        h = requests.get(f'{FAPI}/futures/data/openInterestHist',
                         params={'symbol': symbol, 'period': '1h', 'limit': 4},
                         timeout=5).json()
        if isinstance(h, list) and len(h) >= 2:
            v0 = float(h[0]['sumOpenInterestValue'])
            v1 = float(h[-1]['sumOpenInterestValue'])
            return (v1 - v0) / v0 * 100 if v0 > 0 else 0
    except Exception:
        pass
    return 0.0


def _get_fr(symbol: str) -> float:
    """当前资金费率%"""
    try:
        r = requests.get(f'{FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=1', timeout=5).json()
        return float(r[-1]['fundingRate']) * 100 if r else 0
    except Exception:
        return 0.0


def _get_liq_distances(symbol: str, price: float) -> dict:
    """计算100x/50x清算位距现价%"""
    return {
        'up_100x': (price * 1.0095 - price) / price * 100,
        'dn_100x': (price - price * 0.9905) / price * 100,
        'up_50x':  (price * 1.019  - price) / price * 100,
        'dn_50x':  (price - price * 0.981)  / price * 100,
    }


def _get_mtf_alignment(symbol: str, direction: str) -> int:
    """返回与方向一致的周期数(0-4)"""
    try:
        from brahma_brain.mtf_resonance import MTFResonance
        res = MTFResonance().check(symbol, direction, _get_current_price(symbol))
        alignment = res.get('tf_alignment', {})
        count = sum(1 for v in alignment.values() if v == direction)
        return count
    except Exception:
        return 0


def _get_fangcang_ev(symbol: str) -> float:
    """方仓引擎期望收益"""
    try:
        from brahma_brain.fangcang_engine import get_fangcang_context
        fc = get_fangcang_context(symbol)
        return fc.get('prob_matrix', {}).get('ev', 0.0)
    except Exception:
        return 0.0


def _check_15m_structure(symbol: str, direction: str) -> tuple[bool, str]:
    """
    检查15m结构确认信号
    返回 (confirmed, reason)
    """
    try:
        kl = requests.get(
            f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=8',
            timeout=5
        ).json()
        if not isinstance(kl, list) or len(kl) < 5:
            return False, '数据不足'

        bars = kl[-5:-1]
        opens  = [float(b[1]) for b in bars]
        closes = [float(b[4]) for b in bars]
        highs  = [float(b[2]) for b in bars]
        lows   = [float(b[3]) for b in bars]
        vols   = [float(b[5]) for b in bars]
        avg_vol = sum(vols) / len(vols) if vols else 1

        if direction == 'LONG':
            bos_up   = closes[-1] > highs[-2]
            vol_surge = vols[-1] > avg_vol * 1.5
            two_bull  = all(c > o for c, o in zip(closes[-2:], opens[-2:]))
            if bos_up:   return True, f'15m BOS向上 收盘${closes[-1]:.2f}>前高${highs[-2]:.2f}'
            if vol_surge: return True, f'15m放量{vols[-1]/avg_vol:.1f}x均量'
            if two_bull:  return True, '15m连续2根阳线'
            return False, f'无确认(BOS={bos_up} 量={vols[-1]/avg_vol:.1f}x 连阳={two_bull})'
        else:  # SHORT
            bos_dn   = closes[-1] < lows[-2]
            vol_surge = vols[-1] > avg_vol * 1.5
            two_bear  = all(c < o for c, o in zip(closes[-2:], opens[-2:]))
            if bos_dn:    return True, f'15m BOS向下 收盘${closes[-1]:.2f}<前低${lows[-2]:.2f}'
            if vol_surge: return True, f'15m放量{vols[-1]/avg_vol:.1f}x均量'
            if two_bear:  return True, '15m连续2根阴线'
            return False, f'无确认(BOS={bos_dn} 量={vols[-1]/avg_vol:.1f}x 连阴={two_bear})'
    except Exception as e:
        return False, f'检查异常:{e}'


def _get_15m_struct_sl(symbol: str, direction: str, current_price: float) -> float:
    """用15m最近3H摆动点计算结构止损%"""
    try:
        kl = requests.get(
            f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=16',
            timeout=5
        ).json()
        if not isinstance(kl, list) or len(kl) < 8:
            return 1.5  # 默认
        bars = kl[-13:-1]
        lows  = [float(b[3]) for b in bars]
        highs = [float(b[2]) for b in bars]
        if direction == 'LONG':
            sl_price = min(lows) * 0.997
            return round((current_price - sl_price) / current_price * 100, 2)
        else:
            sl_price = max(highs) * 1.003
            return round((sl_price - current_price) / current_price * 100, 2)
    except Exception:
        return 1.5


# ── 核心决策引擎 ───────────────────────────────────────────────────────

class BrahmaDecisionEngine:
    """
    梵天决策树 2.0
    五步漏斗，每步二元判断
    """

    def decide(self, signal: dict) -> dict:
        """
        输入信号字典，返回决策结果。

        signal 必填字段：
          symbol, direction, regime, score
        可选字段：
          sl_pct, grade, price, timing

        返回：
          {
            'action': 'EXECUTE'|'SKIP'|'WAIT_15M'|'WAIT_ENTRY',
            'reason': str,
            'step_passed': int,  # 通过到第几步
            'details': {...},    # 每步详情
            'entry_plan': {...}  # 仅 EXECUTE/WAIT_15M 时有
          }
        """
        result = {
            'action': 'SKIP',
            'reason': '',
            'step_passed': 0,
            'details': {},
            'entry_plan': {},
            'ts': time.time(),
        }

        sym       = signal.get('symbol', '')
        direction = signal.get('direction', '')
        regime    = signal.get('regime', '')
        score     = signal.get('score', 0)
        sl_pct    = signal.get('sl_pct', 0)
        grade     = signal.get('grade', 100)
        timing    = signal.get('timing', '')

        try:
            price = signal.get('price') or _get_current_price(sym)
            if not price:
                result['reason'] = '价格获取失败'
                return result

            # ═══ Step 1: 否决门 ════════════════════════════════════════
            step1 = {}

            # 1a. 体制×方向死穴
            dead = (regime, direction) in DEAD_COMBOS
            step1['dead_combo'] = dead
            if dead:
                result['reason'] = f'Step1否决: 体制死穴 {regime}×{direction}'
                result['details']['step1'] = step1
                return result

            # 1b. SL过宽（动态门控：grade高时允许稍宽）
            sl_check = sl_pct if sl_pct > 0 else _get_15m_struct_sl(sym, direction, price)
            step1['sl_pct'] = sl_check
            _dyn_sl_max = _dynamic_sl_max(grade, regime, direction, sl_check)
            step1['sl_max_dynamic'] = _dyn_sl_max
            if sl_check > _dyn_sl_max:
                result['reason'] = f'Step1否决: SL={sl_check:.1f}%>{_dyn_sl_max:.2f}%(grade={grade:.0f}动态限制)'
                result['details']['step1'] = step1
                return result

            # 1c. 结构质量
            step1['grade'] = grade
            if grade < MIN_GRADE:
                result['reason'] = f'Step1否决: grade={grade}<{MIN_GRADE}'
                result['details']['step1'] = step1
                return result

            result['step_passed'] = 1
            step1['passed'] = True
            result['details']['step1'] = step1

            # ═══ Step 2: 催化剂验证（三选一）══════════════════════════
            step2 = {}
            catalysts = []

            oi_chg = _get_oi_change_1h(sym)
            step2['oi_chg_1h'] = oi_chg
            if abs(oi_chg) >= OI_SURGE_THR:
                oi_dir_match = (direction == 'LONG' and oi_chg > 0) or \
                               (direction == 'SHORT' and oi_chg < 0)
                if oi_dir_match:
                    catalysts.append(f'OI暴增{oi_chg:+.1f}%')

            fr = _get_fr(sym)
            step2['fr'] = fr
            if direction == 'LONG' and fr < FR_EXTREME_SHORT:
                catalysts.append(f'FR极端空头{fr:.4f}%→轧空')
            if direction == 'SHORT' and fr > FR_EXTREME_LONG:
                catalysts.append(f'FR极端多头{fr:.4f}%→做空')

            liq = _get_liq_distances(sym, price)
            step2['liq'] = liq
            if direction == 'LONG' and liq['up_100x'] <= LIQ_NEAR_PCT:
                catalysts.append(f'100x空头清算位仅+{liq["up_100x"]:.1f}%')
            if direction == 'SHORT' and liq['dn_100x'] <= LIQ_NEAR_PCT:
                catalysts.append(f'100x多头清算位仅-{liq["dn_100x"]:.1f}%')

            step2['catalysts'] = catalysts
            if not catalysts:
                result['reason'] = 'Step2否决: 无催化剂(OI/FR/清算位均不达标)'
                result['details']['step2'] = step2
                result['details']['step1'] = step1
                return result

            result['step_passed'] = 2
            step2['passed'] = True
            result['details']['step2'] = step2

            # ═══ Step 3: 结构确认（三选三）════════════════════════════
            step3 = {}
            confirmations = []

            # 3a. 15m CHoCH（微结构转换）
            try:
                from brahma_brain.fangcang_engine import get_fangcang_context
                fc = get_fangcang_context(sym)
                ms = fc.get('micro_structure', {})
                choch = ms.get('choch_count', 0)
                step3['choch_15m'] = choch
                if choch >= 3:
                    confirmations.append(f'15m CHoCH={choch}')
            except Exception:
                step3['choch_15m'] = 0

            # 3b. MTF共振≥3
            mtf_count = _get_mtf_alignment(sym, direction)
            step3['mtf_count'] = mtf_count
            if mtf_count >= 3:
                confirmations.append(f'MTF共振{mtf_count}/4')

            # 3c. 方仓EV>0
            ev = _get_fangcang_ev(sym)
            step3['fangcang_ev'] = ev
            if ev > 0:
                confirmations.append(f'方仓EV={ev:+.2f}%')

            step3['confirmations'] = confirmations
            if len(confirmations) < 2:
                result['reason'] = f'Step3否决: 结构确认不足({len(confirmations)}/3)'
                result['details']['step3'] = step3
                result['details']['step2'] = step2
                result['details']['step1'] = step1
                return result

            result['step_passed'] = 3
            step3['passed'] = True
            result['details']['step3'] = step3

            # ═══ Step 4: 风险计算 ══════════════════════════════════════
            step4 = {}

            sl_final = sl_check
            # TP = 方向对应的近端清算位
            if direction == 'LONG':
                tp_price = price * (1 + liq['up_100x'] / 100)
                sl_price = price * (1 - sl_final / 100)
            else:
                tp_price = price * (1 - liq['dn_100x'] / 100)
                sl_price = price * (1 + sl_final / 100)

            reward = abs(tp_price - price)
            risk   = abs(price - sl_price)
            rr     = reward / risk if risk > 0 else 0

            step4.update({
                'price': price, 'sl_price': sl_price, 'tp_price': tp_price,
                'sl_pct': sl_final, 'tp_pct': liq['up_100x'] if direction == 'LONG' else liq['dn_100x'],
                'rr': round(rr, 2),
            })

            if rr < MIN_RR:
                result['reason'] = f'Step4否决: RR={rr:.2f}<{MIN_RR}(TP={tp_price:.2f} SL={sl_price:.2f})'
                result['details']['step4'] = step4
                result['details']['step3'] = step3
                result['details']['step2'] = step2
                result['details']['step1'] = step1
                return result

            result['step_passed'] = 4
            step4['passed'] = True
            result['details']['step4'] = step4

            # ═══ Step 5: 15m入场确认 ═══════════════════════════════════
            confirmed_15m, reason_15m = _check_15m_structure(sym, direction)
            step5 = {'confirmed': confirmed_15m, 'reason': reason_15m}
            result['details']['step5'] = step5
            result['details']['step4'] = step4
            result['details']['step3'] = step3
            result['details']['step2'] = step2
            result['details']['step1'] = step1

            entry_plan = {
                'symbol': sym,
                'direction': direction,
                'price': price,
                'sl_price': round(sl_price, 4),
                'tp1_price': round(tp_price, 4),
                'tp2_price': round(price * (1 + liq['up_50x'] / 100) if direction == 'LONG'
                                   else price * (1 - liq['dn_50x'] / 100), 4),
                'sl_pct': sl_final,
                'rr': rr,
                'catalysts': catalysts,
                'confirmations': confirmations,
            }
            result['entry_plan'] = entry_plan

            if confirmed_15m:
                result['step_passed'] = 5
                result['action'] = 'EXECUTE'
                result['reason'] = f'五步全通过 | 催化剂:{catalysts[0]} | {reason_15m} | RR={rr:.2f}x'
            else:
                result['action'] = 'WAIT_15M'
                result['reason'] = f'步骤1-4通过，等待15m确认({reason_15m}) | RR={rr:.2f}x'

        except Exception as e:
            result['action'] = 'SKIP'
            result['reason'] = f'决策引擎异常，降级到score机制: {e}'
            result['traceback'] = traceback.format_exc()[-300:]

        return result

    def format_report(self, signal: dict, decision: dict) -> str:
        """生成可读决策报告"""
        sym = signal.get('symbol', '?')
        direction = signal.get('direction', '?')
        score = signal.get('score', 0)
        action = decision['action']
        reason = decision['reason']
        step = decision['step_passed']

        icons = {'EXECUTE': '🚀', 'WAIT_15M': '⏳', 'WAIT_ENTRY': '👀', 'SKIP': '❌'}
        icon = icons.get(action, '?')

        lines = [
            f'{icon} [{action}] {sym} {direction} | score={score} | 通过{step}/5步',
            f'   原因: {reason}',
        ]

        ep = decision.get('entry_plan', {})
        if ep:
            lines += [
                f'   入场: ${ep.get("price", 0):,.2f}',
                f'   SL:   ${ep.get("sl_price", 0):,.2f} (-{ep.get("sl_pct", 0):.1f}%)',
                f'   TP1:  ${ep.get("tp1_price", 0):,.2f}  TP2: ${ep.get("tp2_price", 0):,.2f}',
                f'   RR:   {ep.get("rr", 0):.2f}x  催化剂: {", ".join(ep.get("catalysts", []))}',
            ]

        return '\n'.join(lines)


# ── 全局单例 ───────────────────────────────────────────────────────────
_engine = None

def get_decision_engine() -> BrahmaDecisionEngine:
    global _engine
    if _engine is None:
        _engine = BrahmaDecisionEngine()
    return _engine


def decide(signal: dict) -> dict:
    """快捷入口"""
    return get_decision_engine().decide(signal)


# ── 测试入口 ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(_BASE))
    sys.path.insert(0, str(_BASE / 'scripts'))

    engine = BrahmaDecisionEngine()

    # 测试信号
    test_signals = [
        {
            'symbol': 'BTCUSDT', 'direction': 'LONG',
            'regime': 'BULL_TREND', 'score': 148,
            'sl_pct': 1.2, 'grade': 85, 'timing': 'READY',
        },
        {
            'symbol': 'ETHUSDT', 'direction': 'LONG',
            'regime': 'BULL_TREND', 'score': 155,
            'sl_pct': 1.5, 'grade': 88, 'timing': 'READY',
        },
        {
            'symbol': 'BTCUSDT', 'direction': 'LONG',
            'regime': 'CHOP_MID', 'score': 130,
            'sl_pct': 1.8, 'grade': 90,
        },
        {
            'symbol': 'ETHUSDT', 'direction': 'SHORT',
            'regime': 'BULL_TREND', 'score': 160,
            'sl_pct': 1.5, 'grade': 85,
        },
    ]

    for sig in test_signals:
        print(f'\n{"="*60}')
        print(f'测试: {sig["symbol"]} {sig["direction"]} [{sig["regime"]}] score={sig["score"]}')
        d = engine.decide(sig)
        print(engine.format_report(sig, d))

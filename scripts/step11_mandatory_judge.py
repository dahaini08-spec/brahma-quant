#!/usr/bin/env python3
"""
step11_mandatory_judge.py — 梵天11步强制决策裁判（SSOT）
[设计院封印 2026-10-01 苏摩111]

40年实战视角的核心设计原则：
  1. 无方向 = 直接输出WAIT，不猜测
  2. 矛盾维度 = 强制标记，降权不穿透
  3. 七维共振 < 3 = 硬WAIT（数据说了算）
  4. 体制封禁 = 零容忍，一票否决
  5. EV ≤ 0 = 不入场（系统说了算）

接入位置：scripts/brahma_manual_analysis.py run_analysis() 末尾
         替代原 step10_vip 的分散门控逻辑

输出格式（强制唯一）：
  {
    'verdict':  'ENTER' | 'WATCH' | 'WAIT',
    'direction': 'LONG' | 'SHORT' | 'NONE',
    'reason':   [str, ...],         # 决策链，逐条说明
    'blocked_by': str | None,       # 哪一闸阻断
    'vip_card': str,                # 最终输出卡片
    'confidence': 'HIGH'|'MED'|'LOW',
    'entry_lo': float,
    'entry_hi': float,
    'sl': float,
    'tp1': float,
    'tp2': float,
    'rr': float,
  }
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE))


# ═══════════════════════════════════════════════════════════════
# 强制分析路径：11道闸门（缺一不可）
# ═══════════════════════════════════════════════════════════════

class Step11Judge:
    """
    40年实战经验蒸馏：11道硬闸
    每道闸门只做一件事，结果明确，不模糊。
    """

    # ── 闸门阈值（全局常量，封印版）──────────────────────────
    MIN_ALIGN       = 3    # 七维共振最低一致数（低于=WAIT）
    MIN_EV          = 0.0  # EV最低门槛（低于=WAIT）
    MIN_RR          = 1.5  # 最低风险收益比
    MIN_ATR_SL_MULT = 1.5  # SL最少是ATR1H的倍数
    CHOP_MAX_SCORE  = 110  # CHOP体制score上限（高于才考虑入场）
    DEAD_COMBOS = [
        ('BEAR_TREND',    'LONG'),   # 死穴：趋势熊市做多
        ('BEAR_RECOVERY', 'SHORT'),  # 死穴：熊市反弹做空
    ]

    def __init__(self, sym: str, d: dict, fvg: dict, ob: dict, liq: dict,
                 res: dict, oi: dict, sm: dict, vol: dict, mac: dict,
                 risk: dict, tb_result: dict):
        self.sym    = sym
        self.d      = d
        self.fvg    = fvg
        self.ob     = ob
        self.liq    = liq
        self.res    = res
        self.oi     = oi
        self.sm     = sm
        self.vol    = vol
        self.mac    = mac
        self.risk   = risk
        self.tb     = tb_result
        self.reason: list[str] = []
        self.blocked_by: str | None = None

        # 从各步结果提取关键数值
        self.price    = float(d.get('price', 0))
        self.regime   = str(d.get('regime', 'CHOP_MID')).upper()
        self.score    = float(d.get('bs', {}).get('score_final', d.get('bs', {}).get('score', 0)) or 0)
        self.align    = int(res.get('align_count', 0))
        self.direction = str(tb_result.get('direction', 'NONE')).upper()
        self.atr_1h   = float(vol.get('atr_1h', 0) or 0)
        self.atr_4h   = float(vol.get('atr_4h', 0) or 0)
        self.hurst    = float(vol.get('hurst', 0.5) or 0.5)
        self.entry_lo = float(tb_result.get('entry_lo', 0) or 0)
        self.entry_hi = float(tb_result.get('entry_hi', 0) or 0)
        self.sl       = float(tb_result.get('stop_loss', 0) or 0)
        self.tp1      = float(tb_result.get('tp1', 0) or 0)
        self.tp2      = float(tb_result.get('tp2', 0) or 0)
        self.rr       = float(tb_result.get('rr1', 0) or 0)

    # ──────────────────────────────────────────────────────────
    # 11道闸门（每道返回 True=通过 / False=阻断）
    # ──────────────────────────────────────────────────────────

    def gate1_data_freshness(self) -> bool:
        """Gate1: 数据新鲜度（>4h旧数据禁止入场）"""
        stale = self.d.get('_stale_keys', [])
        critical = [k for k in stale if any(x in k for x in ['price','regime','oi','liq'])]
        if critical:
            self.blocked_by = 'Gate1_数据过期'
            self.reason.append(f'❌ G1数据过期: {critical[:3]}，拒绝入场')
            return False
        self.reason.append(f'✅ G1数据新鲜')
        return True

    def gate2_direction(self) -> bool:
        """Gate2: 方向必须明确（NONE=WAIT）"""
        if self.direction == 'NONE':
            self.blocked_by = 'Gate2_无方向'
            self.reason.append(f'❌ G2无明确方向（trader_brain=NONE），WAIT')
            return False
        self.reason.append(f'✅ G2方向={self.direction}')
        return True

    def gate3_dead_combo(self) -> bool:
        """Gate3: 死穴检查（一票否决）"""
        for dead_r, dead_d in self.DEAD_COMBOS:
            if dead_r in self.regime and self.direction == dead_d:
                self.blocked_by = f'Gate3_死穴:{dead_r}×{dead_d}'
                self.reason.append(f'❌ G3死穴触发: {dead_r}做{dead_d}，永久禁止')
                return False
        self.reason.append(f'✅ G3无死穴（{self.regime}×{self.direction}）')
        return True

    def gate4_resonance(self) -> bool:
        """Gate4: 七维共振≥3（数据不足=WAIT）"""
        if self.align < self.MIN_ALIGN:
            self.blocked_by = f'Gate4_共振不足:{self.align}/7'
            self.reason.append(f'❌ G4共振{self.align}/7 < {self.MIN_ALIGN}，信号太弱，WAIT')
            return False
        self.reason.append(f'✅ G4共振{self.align}/7 ≥ {self.MIN_ALIGN}')
        return True

    def gate5_chop_score(self) -> bool:
        """Gate5: CHOP体制需更高分数（震荡市提高门槛）"""
        if 'CHOP' in self.regime and self.score < self.CHOP_MAX_SCORE:
            self.blocked_by = f'Gate5_CHOP分数不足:{self.score:.0f}<{self.CHOP_MAX_SCORE}'
            self.reason.append(f'❌ G5震荡体制score={self.score:.0f} < {self.CHOP_MAX_SCORE}，减少交易频率')
            return False
        self.reason.append(f'✅ G5体制={self.regime} score={self.score:.0f}通过')
        return True

    def gate6_entry_valid(self) -> bool:
        """Gate6: 入场区必须有效（entry_lo/hi不为0且方向正确）"""
        if self.entry_lo <= 0 or self.entry_hi <= 0:
            self.blocked_by = 'Gate6_入场区无效'
            self.reason.append(f'❌ G6入场区无效 entry=({self.entry_lo},{self.entry_hi})')
            return False
        # 方向验证：多单入场区必须在现价附近或下方
        mid = (self.entry_lo + self.entry_hi) / 2
        if self.direction == 'LONG' and mid > self.price * 1.05:
            self.blocked_by = 'Gate6_多单入场区过高'
            self.reason.append(f'❌ G6多单入场区${mid:.2f}高于现价${self.price:.2f}×1.05')
            return False
        if self.direction == 'SHORT' and mid < self.price * 0.95:
            self.blocked_by = 'Gate6_空单入场区过低'
            self.reason.append(f'❌ G6空单入场区${mid:.2f}低于现价${self.price:.2f}×0.95')
            return False
        self.reason.append(f'✅ G6入场区${self.entry_lo:.2f}~${self.entry_hi:.2f}有效')
        return True

    def gate7_sl_atr(self) -> bool:
        """Gate7: SL距离≥1.5×ATR1H（防针刺止损）"""
        if self.sl <= 0 or self.atr_1h <= 0:
            self.reason.append(f'⚠️ G7 SL或ATR无数据，跳过ATR验证')
            return True
        entry_mid = (self.entry_lo + self.entry_hi) / 2
        sl_dist = abs(self.sl - entry_mid)
        min_sl = self.atr_1h * self.MIN_ATR_SL_MULT
        if sl_dist < min_sl:
            self.blocked_by = f'Gate7_SL过近:{sl_dist:.2f}<{min_sl:.2f}'
            self.reason.append(f'❌ G7 SL距离${sl_dist:.2f} < 1.5×ATR1H${min_sl:.2f}，易被针刺')
            return False
        self.reason.append(f'✅ G7 SL距离${sl_dist:.2f} ≥ 1.5×ATR1H${min_sl:.2f}')
        return True

    def gate8_rr(self) -> bool:
        """Gate8: 风险收益比≥1.5（低RR坚决不做）"""
        if self.rr < self.MIN_RR:
            self.blocked_by = f'Gate8_RR不足:{self.rr:.2f}<{self.MIN_RR}'
            self.reason.append(f'❌ G8 RR={self.rr:.2f} < {self.MIN_RR}，赔率不够')
            return False
        self.reason.append(f'✅ G8 RR={self.rr:.2f} ≥ {self.MIN_RR}')
        return True

    def gate9_risk_circuit(self) -> bool:
        """Gate9: 风控熔断器（连亏/回撤/反脆弱）"""
        if self.risk.get('circuit_open'):
            self.blocked_by = 'Gate9_熔断器触发'
            self.reason.append(f'❌ G9熔断器触发: {self.risk.get("circuit_reason","未知")}')
            return False
        max_dd = float(self.risk.get('max_drawdown_pct', 0) or 0)
        if max_dd > 15:
            self.blocked_by = f'Gate9_最大回撤:{max_dd:.1f}%>15%'
            self.reason.append(f'❌ G9最大回撤{max_dd:.1f}% > 15%限制')
            return False
        self.reason.append(f'✅ G9风控正常 回撤={max_dd:.1f}%')
        return True

    def gate10_macro_block(self) -> bool:
        """Gate10: 宏观重大事件窗口（NFP/FOMC当天禁止）"""
        if self.mac.get('major_event_today'):
            event = self.mac.get('event_name', '重大宏观事件')
            self.blocked_by = f'Gate10_宏观事件:{event}'
            self.reason.append(f'❌ G10今日{event}，禁止入场（黑天鹅风险）')
            return False
        self.reason.append(f'✅ G10无重大宏观事件')
        return True

    def gate11_ev_positive(self) -> bool:
        """Gate11: 期望值EV > 0（最终量化裁判）"""
        # EV = WR × RR - (1-WR) 简化模型
        wr_key = f'{self.regime}:{self.direction}'
        wr_matrix = {}
        try:
            import json
            wm = json.loads((BASE / 'data' / 'wr_matrix_live.json').read_text())
            wr_matrix = wm.get('matrix', {})
        except Exception:
            pass

        wr_entry = wr_matrix.get(wr_key, {})
        wr = float(wr_entry.get('wr', 0.45) or 0.45)  # 默认历史胜率
        n  = int(wr_entry.get('n', 0) or 0)
        ev = wr * self.rr - (1 - wr)

        if n < 8:
            self.reason.append(f'⚠️ G11 WR样本n={n}<8，用历史均值WR={wr:.1%} EV={ev:+.3f}')
        else:
            self.reason.append(f'✅ G11 WR={wr:.1%}(n={n}) RR={self.rr:.2f} EV={ev:+.3f}')

        if ev <= self.MIN_EV:
            self.blocked_by = f'Gate11_EV≤0:{ev:+.3f}'
            self.reason.append(f'❌ G11 EV={ev:+.3f} ≤ 0，数学期望负，WAIT')
            return False
        return True

    # ──────────────────────────────────────────────────────────
    # 主判决逻辑
    # ──────────────────────────────────────────────────────────

    def judge(self) -> dict:
        """执行11道闸门，返回最终判决"""

        gates = [
            self.gate1_data_freshness,
            self.gate2_direction,
            self.gate3_dead_combo,
            self.gate4_resonance,
            self.gate5_chop_score,
            self.gate6_entry_valid,
            self.gate7_sl_atr,
            self.gate8_rr,
            self.gate9_risk_circuit,
            self.gate10_macro_block,
            self.gate11_ev_positive,
        ]

        for i, gate in enumerate(gates, 1):
            passed = gate()
            if not passed:
                return self._build_result('WAIT')

        # 全部通过 → 判定 ENTER 或 WATCH
        confidence = self.tb.get('confidence', 'LOW')
        cross = int(self.tb.get('cross_count', 0) or 0)

        if cross >= 3 and confidence in ('HIGH', 'MED'):
            verdict = 'ENTER'
        else:
            verdict = 'WATCH'
            self.reason.append(f'⚠️ 交叉验证{cross}/4，置信={confidence}，降为WATCH挂单')

        return self._build_result(verdict)

    def _build_result(self, verdict: str) -> dict:
        gate_count = sum(1 for r in self.reason if r.startswith('✅'))
        return {
            'verdict':    verdict,
            'direction':  self.direction,
            'reason':     self.reason,
            'blocked_by': self.blocked_by,
            'confidence': self.tb.get('confidence', 'LOW'),
            'gates_passed': gate_count,
            'entry_lo':   self.entry_lo,
            'entry_hi':   self.entry_hi,
            'sl':         self.sl,
            'tp1':        self.tp1,
            'tp2':        self.tp2,
            'rr':         self.rr,
            'vip_card':   self._format_vip(verdict),
        }

    def _format_vip(self, verdict: str) -> str:
        """强制格式VIP卡片——唯一模板，绝不乱输出"""
        sym = self.sym
        price = self.price
        direction = self.direction
        regime = self.regime
        align = self.align
        blocked = self.blocked_by or ''

        # WAIT / 阻断 → 标准等待卡
        if verdict == 'WAIT':
            gate_summary = '\n'.join(
                f'  {r}' for r in self.reason[-3:] if '❌' in r
            )
            return (
                f'──── VIP ────\n'
                f'🌿 姓赵不宣 | {sym} 今日观察\n'
                f'⏳ [闸门] 策略未通过铁律校验，拒绝输出\n'
                f'{gate_summary}\n'
                f'   当前 体制={regime}\n'
                f'   闸门{11}封印 2026-09-18 苏摩111\n'
            )

        # ENTER / WATCH → 标准VIP格式（永久封印版）
        entry_str = f'${self.entry_lo:.2f}~${self.entry_hi:.2f}'
        sl_str    = f'${self.sl:.2f}'
        tp1_str   = f'${self.tp1:.2f}'
        tp2_str   = f'${self.tp2:.2f}'
        rr_str    = f'{self.rr:.1f}'

        if direction == 'SHORT':
            action_emoji = '🔴'
            action_txt   = '空单｜挂单区'
            wait_txt     = '🟢 暂无多单｜等待结构'
        else:
            action_emoji = '🟢'
            action_txt   = '多单｜挂单区'
            wait_txt     = '🔴 暂无空单｜等待结构'

        confidence = self.tb.get('confidence', 'LOW')
        lever = '5x' if 'TREND' in regime else '3x'
        pos   = '2%' if verdict == 'ENTER' else '1%'

        enter_label = 'ENTER' if verdict == 'ENTER' else 'WATCH'
        tag = f'{enter_label} | 共振{align}/7 | {confidence}'

        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'——— {sym} ———\n'
            f'{action_emoji} {action_txt} {entry_str}\n'
            f'止损 {sl_str}｜目标 {tp1_str}→{tp2_str}\n'
            f'杠杆 {lever}｜仓位 {pos}\n'
            f'{wait_txt}\n'
            f'⚠️ {tag}\n'
            f'🚫 破{sl_str}作废\n'
            f'🌿 姓赵不宣 | 不是建议\n'
        )


def run_step11(sym: str, d: dict, fvg: dict, ob: dict, liq: dict,
               res: dict, oi: dict, sm: dict, vol: dict, mac: dict,
               risk: dict, tb_result: dict) -> dict:
    """
    公开入口：执行11步强制裁判
    接入：brahma_manual_analysis.run_analysis() 末尾
    """
    judge = Step11Judge(sym, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, tb_result)
    result = judge.judge()
    return result


# ─── 自测 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Step11 Judge 自测...')
    # 构造极简测试数据
    mock = dict(
        price=83785, regime='CHOP_MID',
        bs={'score_final': 105, 'score': 105},
        _stale_keys=[]
    )
    mock_tb = dict(
        direction='SHORT', confidence='HIGH', cross_count=3,
        entry_lo=85200, entry_hi=85460,
        stop_loss=87170, tp1=82109, tp2=81188, rr1=2.5
    )
    mock_res = dict(align_count=4, resonance_ratio=0.8)
    mock_vol = dict(atr_1h=461, atr_4h=1011, hurst=0.622)
    mock_risk = dict(circuit_open=False, max_drawdown_pct=0.0)
    mock_mac = dict(major_event_today=False)

    r = run_step11(
        'BTC', mock, {}, {}, {},
        mock_res, {}, {}, mock_vol, mock_mac,
        mock_risk, mock_tb
    )
    print(f"判决: {r['verdict']} | 方向: {r['direction']} | 闸门通过: {r['gates_passed']}/11")
    print(f"阻断: {r['blocked_by']}")
    print('\n决策链:')
    for line in r['reason']: print(f'  {line}')
    print('\n最终卡片:')
    print(r['vip_card'])

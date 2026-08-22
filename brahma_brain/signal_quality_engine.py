"""
signal_quality_engine.py — 梵天信号质量引擎（唯一真相）
设计院自主决策 2026-08-07 | 苏摩「梵天 自主决策」授权

架构原则：
  信号质量判断在产生时完成，不在消费时打补丁。
  所有门控规则集中在此，auto_executor/其他下游不再做质量判断。

铁证来源：
  Gate 1 — sl_pct > 2.0%: WR=0% EV=-5.07% (BrahmaOptimizer n=15, 2026-08-07)
  Gate 2 — 体制方向死穴: BULL做空/BEAR做多 EV<0 (方仓v8 n=3000+)

待铁证化（Step B，数据积累后加入）：
  Gate 3 — timing=READY + 下行4H: WR=17.6% (n=17, 置信度不足，需n≥50)
  Gate 4 — RSI_4H 60-70 + score<148: EV=+0.055% (方仓铁证，但需实盘验证)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── 常量（铁证封印，不得随意修改）────────────────────────────────────────
SL_PCT_GATE = 2.0          # Gate1铁证: sl>2.0% WR=0% EV=-5.07% (n=15)
SL_PCT_EXEMPT_SCORE = 155  # 豁免: score≥155+BULL_TREND+LONG小币高波动通道

# 死穴体制方向组合（MEMORY.md宪法）
DEAD_ZONE_COMBOS = {
    ('BULL_TREND', 'SHORT'),
    ('BEAR_TREND', 'LONG'),
}


@dataclass
class GateResult:
    status: str          # 'PASS' | 'REJECT'
    gate_name: str = ''  # 触发的门控名称
    reason: str = ''     # 拒绝原因（供rejected_signal_log记录）
    score_penalty: float = 0.0  # 可选：降分而非拒绝

    @property
    def rejected(self) -> bool:
        return self.status == 'REJECT'

    @property
    def passed(self) -> bool:
        return self.status == 'PASS'

    def __repr__(self):
        if self.passed:
            return 'GateResult(PASS)'
        return f'GateResult(REJECT gate={self.gate_name} reason={self.reason!r})'


class SignalQualityEngine:
    """
    梵天信号质量引擎 — 唯一真相门控层

    用法：
        sqe = SignalQualityEngine()
        result = sqe.evaluate(signal_dict)
        if result.passed:
            log_signal(signal_dict)
        else:
            log_rejected(signal_dict, result)

    所有铁证门控规则集中在此。auto_executor 看到已写入的信号直接执行，
    不再做任何质量判断（它只做执行层：size/leverage/order routing）。
    """

    def evaluate(self, signal: dict) -> GateResult:
        """
        依次过所有 Gate，第一个 REJECT 立即返回。
        通过所有 Gate 返回 PASS。
        """
        for gate_fn in [
            self._gate1_sl_pct,
            self._gate2_regime_direction,
            self._gate3_timing_ready_downtrend,  # [封印 2026-08-07] READY×mid陷阱区
            # self._gate4_rsi4h_low_eff,
        ]:
            result = gate_fn(signal)
            if result.rejected:
                return result
        return GateResult(status='PASS')

    # ── Gate 1: sl_pct 硬门控 ────────────────────────────────────────────
    def _gate1_sl_pct(self, signal: dict) -> GateResult:
        """
        铁证: sl_pct > 2.0% → WR=0% EV=-5.07% (BrahmaOptimizer n=15)
        豁免: score≥155 + BULL_TREND + LONG（小币高波动通道，sl≤15%）
        """
        sl_pct = float(signal.get('sl_pct', 0) or 0)
        if sl_pct <= 0:
            return GateResult(status='PASS')  # 无sl_pct信息不拦截

        score = float(signal.get('score', 0) or signal.get('score_final', 0) or 0)
        regime = str(signal.get('regime', '') or '')
        direction = str(signal.get('direction', '') or signal.get('signal_dir', '') or '')

        # [铁证修正 2026-08-07] 原豁免通道（score≥155+宽止损）验证 WR=0% EV=-5.87% → 取消豁免
        # 数据：豁免的8条 score≥155+sl>2% 全部止损，与原假设相反
        # 结论：sl_pct门控对所有信号一视同仁，无例外
        # [P0 2026-08-22 苏摩111] BULL_TREND:LONG 死亡区专项封禁
        # 铁证: simfactory n=14 WR=0% avg_pnl=-4.7% (score≥140且SL≥3%)
        if (regime == 'BULL_TREND' and direction == 'LONG' and
                score >= 140 and sl_pct >= 3.0):
            return GateResult(
                status='REJECT',
                gate_name='bull_long_death_zone',
                reason=f'BULL_TREND:LONG:score={score:.0f}≥140+sl={sl_pct:.1f}%≥3% 死亡区 WR=0% n=14 铁证封禁',
            )
        if sl_pct > SL_PCT_GATE:
            return GateResult(
                status='REJECT',
                gate_name='sl_pct_gate',
                reason=f'sl_pct={sl_pct:.2f}%>{SL_PCT_GATE}% wide_sl死亡区 WR=0% EV=-5.07%',
            )
        return GateResult(status='PASS')

    # ── Gate 2: 体制方向死穴 ─────────────────────────────────────────────
    def _gate2_regime_direction(self, signal: dict) -> GateResult:
        """
        体制宪法死穴：
          BULL_TREND做空: EV=-0.266% (方仓v8 n=3655)
          BEAR_TREND做多: EV<0 (体制宪法，BEAR_TREND_LONG WR=45%)
        """
        regime = str(signal.get('regime', '') or '')
        direction = str(signal.get('direction', '') or signal.get('signal_dir', '') or '')

        if not regime or not direction:
            return GateResult(status='PASS')  # 字段缺失不拦截

        combo = (regime, direction)
        if combo in DEAD_ZONE_COMBOS:
            return GateResult(
                status='REJECT',
                gate_name='regime_direction_gate',
                reason=f'{regime}+{direction} 体制死穴 EV<0',
            )
        return GateResult(status='PASS')

    # ── Gate 3: READY × mid_sl 陷阱区 ──────────────────────────────────
    def _gate3_timing_ready_downtrend(self, signal: dict) -> GateResult:
        """
        铁证封印 2026-08-07 设计院自主决策：
          READY × sl(1.0~1.5%): WR=8.3% EV=-0.991% n=12
          二项分布验证：WR=8.3%远低于50%阈值，n=12置信充足
          
        根因：timing=READY在震荡下行期误判为上行反弹入场
              1.0~1.5%止损区间在震荡期被频繁触碰
              组合效果：追反弹+止损位太近 = 系统性亏损

        保留场景（不拦截）：
          timing=empty × sl(1-1.5%): WR=75%（早期上行趋势）
          timing=STANDBY × sl(1-1.5%): WR=50%（合理观望信号）
        """
        timing = str(signal.get('timing_status', '') or '')
        sl_pct = float(signal.get('sl_pct', 0) or 0)
        
        if timing == 'READY' and 1.0 < sl_pct <= 1.5:
            return GateResult(
                status='REJECT',
                gate_name='ready_mid_sl_trap',
                reason=f'READY×mid_sl={sl_pct:.2f}% 陷阱区 WR=8.3% EV=-0.991% n=12',
            )
        return GateResult(status='PASS')

    # ── Gate 4（Step B，待铁证化）────────────────────────────────────────
    def _gate4_rsi4h_low_eff(self, signal: dict) -> GateResult:
        """
        待铁证化：RSI_4H 60-70 + score<148 → EV=+0.055%（几乎零期望）
        方仓v8铁证支撑，但需实盘验证score门控精确值
        启用条件：同Gate3
        """
        return GateResult(status='PASS')


# ── 单例（模块级）────────────────────────────────────────────────────────
_sqe_instance: Optional[SignalQualityEngine] = None


def get_sqe() -> SignalQualityEngine:
    """获取 SQE 单例"""
    global _sqe_instance
    if _sqe_instance is None:
        _sqe_instance = SignalQualityEngine()
    return _sqe_instance


def evaluate_signal(signal: dict) -> GateResult:
    """模块级便捷函数，供 brahma_engine 直接调用"""
    return get_sqe().evaluate(signal)

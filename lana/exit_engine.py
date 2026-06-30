#!/usr/bin/env python3
"""
梵天 v11 · 双轨出场引擎 exit_engine.py
基于EXP-08(PF)和EXP-06(持仓时间)核心发现

轨道A：快出（1-4根K线，PF=1.07，唯一正期望）
轨道B：长持（48H，EXTREME信号均收益+0.672%）
"""

from dataclasses import dataclass
from typing import Optional, Tuple

# ─── 数据结构 ─────────────────────────────────────────────────────

@dataclass
class ExitPlan:
    track: str           # 'TRACK_A' / 'TRACK_B'
    sl: float            # 止损价
    tp1: float           # 止盈1（快速目标）
    tp2: float           # 止盈2（延伸目标）
    tp3: Optional[float] # 止盈3（极端延伸）
    sl_pct: float        # 止损距离%
    tp1_pct: float       # 止盈1距离%
    rr1: float           # 盈亏比T1
    max_bars: int        # 最大持仓K线数
    exit_rule: str       # 出场规则描述

@dataclass
class TradeState:
    entry_price: float
    direction: str       # '做多' / '做空'
    track: str
    entry_bar: int
    plan: ExitPlan
    max_profit_pct: float = 0.0  # 持仓期间最大浮盈
    current_bar: int = 0


# ─── 轨道A：快出策略 ──────────────────────────────────────────────

def build_track_a(entry: float, direction: str,
                  bb_mid: float, sl_struct: float,
                  atr_pct: float) -> ExitPlan:
    """
    轨道A：1-4根K线快出
    数据依据：EXP-08 持有1根PF=1.074，持有4根PF=1.031
    止盈：BB中轨或ATR×1.0（取近的）
    止损：结构低/高点
    时间止损：4根K线后强制出场
    """
    if direction == '做多':
        sl = sl_struct  # 结构低点
        # T1 = BB中轨 or 入场价+ATR×1.0（取近的）
        bb_target = bb_mid
        atr_target = entry * (1 + atr_pct / 100)
        tp1 = min(bb_target, atr_target) if bb_target > entry else atr_target
        tp2 = entry * (1 + atr_pct * 1.5 / 100)
        tp3 = None
    else:  # 做空
        sl = sl_struct  # 结构高点
        bb_target = bb_mid
        atr_target = entry * (1 - atr_pct / 100)
        tp1 = max(bb_target, atr_target) if bb_target < entry else atr_target
        tp2 = entry * (1 - atr_pct * 1.5 / 100)
        tp3 = None

    sl_pct = abs(entry - sl) / entry * 100
    tp1_pct = abs(entry - tp1) / entry * 100
    rr1 = tp1_pct / sl_pct if sl_pct > 0 else 0

    # 验证最低盈亏比
    min_rr = 1.0  # 轨道A最低要求RR=1.0
    if rr1 < min_rr:
        # 调整tp1满足最低RR
        if direction == '做多':
            tp1 = entry + sl_pct / 100 * entry * min_rr
        else:
            tp1 = entry - sl_pct / 100 * entry * min_rr
        tp1_pct = abs(entry - tp1) / entry * 100
        rr1 = tp1_pct / sl_pct

    return ExitPlan(
        track='TRACK_A',
        sl=round(sl, 6), tp1=round(tp1, 6), tp2=round(tp2, 6), tp3=None,
        sl_pct=round(sl_pct, 3), tp1_pct=round(tp1_pct, 3),
        rr1=round(rr1, 2), max_bars=4,
        exit_rule='4根K线后强制出场 | 浮盈>0.8%即时出场',
    )


# ─── 轨道B：长持策略 ──────────────────────────────────────────────

def build_track_b(entry: float, direction: str,
                  bb_mid: float, bb_opposite: float,
                  sl_struct: float, rsi_50_target: float = None) -> ExitPlan:
    """
    轨道B：EXTREME信号长持（最优持仓48H）
    数据依据：EXP-06 CHOP_HIGH体制EXTREME信号48H均收益+0.672%
    止盈：RSI回归50（自然出场）or BB对面边界
    止损：严格结构止损（比轨道A更严）
    时间止损：48根1H K线后强制出场
    """
    if direction == '做多':
        sl = sl_struct * 0.998  # 结构低点再留0.2%缓冲
        tp1 = bb_mid            # 第一目标：BB中轨（约2-3%）
        tp2 = bb_opposite       # 第二目标：BB上轨（约4-6%）
        tp3 = bb_opposite + (bb_opposite - bb_mid) * 0.5  # 超级延伸
    else:  # 做空
        sl = sl_struct * 1.002
        tp1 = bb_mid
        tp2 = bb_opposite
        tp3 = bb_opposite - (bb_mid - bb_opposite) * 0.5

    sl_pct  = abs(entry - sl) / entry * 100
    tp1_pct = abs(entry - tp1) / entry * 100
    tp2_pct = abs(entry - tp2) / entry * 100
    rr1 = tp1_pct / sl_pct if sl_pct > 0 else 0
    rr2 = tp2_pct / sl_pct if sl_pct > 0 else 0

    return ExitPlan(
        track='TRACK_B',
        sl=round(sl, 6), tp1=round(tp1, 6), tp2=round(tp2, 6),
        tp3=round(tp3, 6) if tp3 else None,
        sl_pct=round(sl_pct, 3), tp1_pct=round(tp1_pct, 3),
        rr1=round(rr1, 2), max_bars=48,
        exit_rule='48H强制出场 | RSI回归50区间 | BB中轨T1 | BB对面T2 | 移动止损锁利',
    )


# ─── 实时出场判断 ─────────────────────────────────────────────────

def check_exit(state: TradeState, current_price: float,
               current_rsi: float, current_bar: int) -> Tuple[bool, str]:
    """
    每根K线调用，判断是否触发出场
    返回：(should_exit, reason)
    """
    p = state.plan
    bars_held = current_bar - state.entry_bar
    entry = state.entry_price
    direction = state.direction

    # 计算当前浮盈
    if direction == '做多':
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    # 更新最大浮盈
    state.max_profit_pct = max(state.max_profit_pct, pnl_pct)

    # ─── 止损检查（任何轨道）───
    if direction == '做多' and current_price <= p.sl:
        return True, f'止损 SL={p.sl:.4f} 亏损={pnl_pct:.2f}%'
    if direction == '做空' and current_price >= p.sl:
        return True, f'止损 SL={p.sl:.4f} 亏损={pnl_pct:.2f}%'

    # ─── 轨道A：快出逻辑 ───
    if p.track == 'TRACK_A':
        # 1. 浮盈超过0.8%立刻出
        if pnl_pct >= 0.8:
            return True, f'轨道A快出 浮盈{pnl_pct:.2f}%≥0.8% 目标达成'
        # 2. 到达T1目标
        if direction == '做多' and current_price >= p.tp1:
            return True, f'轨道A T1 {p.tp1:.4f} 盈利{pnl_pct:.2f}%'
        if direction == '做空' and current_price <= p.tp1:
            return True, f'轨道A T1 {p.tp1:.4f} 盈利{pnl_pct:.2f}%'
        # 3. 时间止损：4根K线
        if bars_held >= p.max_bars:
            return True, f'轨道A时间止损 {bars_held}根K线 盈亏{pnl_pct:+.2f}%'
        # 4. 反向RSI：持仓1H后RSI向反方向走
        if bars_held >= 2:
            if direction == '做多' and current_rsi > 60:  # 做多信号RSI超买了，出
                return True, f'轨道A RSI反转 RSI={current_rsi:.0f}'
            if direction == '做空' and current_rsi < 40:
                return True, f'轨道A RSI反转 RSI={current_rsi:.0f}'

    # ─── 轨道B：长持逻辑 ───
    elif p.track == 'TRACK_B':
        # 1. 到达T1（BB中轨）
        if direction == '做多' and current_price >= p.tp1:
            return True, f'轨道B T1 BB中轨 {p.tp1:.4f} 盈利{pnl_pct:.2f}%'
        if direction == '做空' and current_price <= p.tp1:
            return True, f'轨道B T1 BB中轨 {p.tp1:.4f} 盈利{pnl_pct:.2f}%'
        # 2. RSI回归50区间（自然反转完成）
        if direction == '做多' and 45 <= current_rsi <= 60:
            if pnl_pct > 0:
                return True, f'轨道B RSI回归 RSI={current_rsi:.0f} 盈利{pnl_pct:.2f}%'
        if direction == '做空' and 40 <= current_rsi <= 55:
            if pnl_pct > 0:
                return True, f'轨道B RSI回归 RSI={current_rsi:.0f} 盈利{pnl_pct:.2f}%'
        # 3. 移动止损：浮盈>3%后，回撤超过1.5%出场
        if state.max_profit_pct >= 3.0 and (state.max_profit_pct - pnl_pct) >= 1.5:
            return True, f'轨道B移动止损 峰值{state.max_profit_pct:.2f}%回撤至{pnl_pct:.2f}%'
        # 4. 时间止损：48根K线
        if bars_held >= p.max_bars:
            return True, f'轨道B时间止损 48H 盈亏{pnl_pct:+.2f}%'

    return False, ''


# ─── 信号强度识别 ─────────────────────────────────────────────────

def classify_signal_strength(rsi1h: float, bb: float,
                              atr_rank: float) -> str:
    """
    基于EXP-06信号强度定义
    EXTREME：RSI极端+BB极端
    STRONG：RSI强超买卖
    MODERATE：其他
    """
    rsi_extreme = rsi1h < 25 or rsi1h > 75
    rsi_strong  = rsi1h < 30 or rsi1h > 70
    bb_extreme  = bb < 0.10 or bb > 0.90

    if rsi_extreme and bb_extreme:
        return 'EXTREME'
    if rsi_extreme or rsi_strong:
        return 'STRONG'
    return 'MODERATE'


# ─── 测试 ─────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("梵天 v11 · 双轨出场引擎测试\n")

    # 轨道A测试
    plan_a = build_track_a(
        entry=2.50, direction='做空',
        bb_mid=2.48, sl_struct=2.56,
        atr_pct=1.8
    )
    print(f"轨道A（快出）:")
    print(f"  止损: {plan_a.sl:.4f}  距离: {plan_a.sl_pct:.2f}%")
    print(f"  止盈T1: {plan_a.tp1:.4f}  距离: {plan_a.tp1_pct:.2f}%")
    print(f"  盈亏比: {plan_a.rr1:.2f}")
    print(f"  出场规则: {plan_a.exit_rule}")

    print()

    # 轨道B测试
    plan_b = build_track_b(
        entry=2.50, direction='做空',
        bb_mid=2.45, bb_opposite=2.35,
        sl_struct=2.58,
    )
    print(f"轨道B（长持）:")
    print(f"  止损: {plan_b.sl:.4f}  距离: {plan_b.sl_pct:.2f}%")
    print(f"  止盈T1: {plan_b.tp1:.4f}  距离: {plan_b.tp1_pct:.2f}%")
    print(f"  止盈T2: {plan_b.tp2:.4f}")
    print(f"  盈亏比: {plan_b.rr1:.2f}")
    print(f"  出场规则: {plan_b.exit_rule}")

    print()

    # 强度分类测试
    cases = [(22, 0.05, 0.2), (28, 0.15, 0.4), (72, 0.82, 0.5), (50, 0.5, 0.6)]
    for r,b,ar in cases:
        s = classify_signal_strength(r, b, ar)
        print(f"  RSI={r}  BB={b}  ATR分位={ar:.0%} → {s}")


# ════════════════════════════════════════════════════
# 峰值回撤出场（达摩院EXP-09验证 PF=10.21 · 唯一正期望出场）
# ════════════════════════════════════════════════════

@dataclass
class PeakTracker:
    """跟踪峰值浮盈，回撤50%时触发出场"""
    entry:       float
    direction:   str
    drawback:    float = 0.50   # 回撤50%出场
    sl_pct:      float = 0.030  # 兜底止损3%
    time_stop_h: int   = 8   # [N-C 2026-05-22] 101k实证: 全部<1H平仓PF=2.058，32H→8H     # 时间止损72H
    peak_pnl:    float = 0.0    # 当前峰值浮盈%
    bars:        int   = 0      # 持仓K线数

    def update(self, current_price: float) -> tuple[bool, str]:
        """
        每根K线调用一次
        返回 (should_exit: bool, reason: str)
        """
        self.bars += 1
        ep = self.entry

        # 当前浮盈%
        if self.direction == '做多':
            pnl = (current_price - ep) / ep * 100
        else:
            pnl = (ep - current_price) / ep * 100

        # 更新峰值
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl

        # 兜底止损（绝对保护）
        if pnl < -self.sl_pct * 100:
            return True, f'止损({pnl:.2f}%)'

        # 峰值回撤50%出场
        if self.peak_pnl > 0.3 and pnl < self.peak_pnl * (1 - self.drawback):
            return True, f'峰值回撤(峰值{self.peak_pnl:.2f}%→当前{pnl:.2f}%)'

        # 时间止损（兜底）
        if self.bars >= self.time_stop_h:
            return True, f'时间止损({self.bars}H)'

        return False, ''

    @property
    def summary(self) -> str:
        return f"峰值{self.peak_pnl:.2f}%  已持{self.bars}H  回撤阈={self.peak_pnl*(1-self.drawback):.2f}%"



# ════════════════════════════════════════════════════
# 移动止盈 TrailingStop（8年验证升级版）
# 达摩院结论：浮盈>10% 必须锁利，保本止损优先
# ════════════════════════════════════════════════════

@dataclass
class TrailingStop:
    """
    移动止盈模块（EXP-FULL-20260516 升级）
    - 浮盈 > 10%（5x杠杆）→ 止损移至保本（入场价+手续费）
    - 浮盈 > 20%（5x杠杆）→ 止损移至浮盈50%位置
    - 浮盈 > 35%（5x杠杆）→ 止损移至浮盈70%位置（锁定大行情）

    注：pnl_pct 指杠杆后盈亏%，等于现货pnl×杠杆倍数
    """
    entry:      float       # 入场价
    direction:  str         # '做多' / '做空'
    leverage:   float = 5.0 # 杠杆倍数
    fee_pct:    float = 0.001  # 手续费率（0.1%）
    sl_price:   float = 0.0    # 当前止损价（实时更新）
    initial_sl: float = 0.0    # 原始止损价（初始化时设置）
    peak_price: float = 0.0    # 历史最优价
    stage:      str   = 'INITIAL'  # INITIAL/BREAKEVEN/TRAIL_50/TRAIL_70

    def __post_init__(self):
        if self.sl_price == 0.0:
            self.sl_price = self.initial_sl
        if self.peak_price == 0.0:
            self.peak_price = self.entry

    def _pnl(self, price: float) -> float:
        """计算当前杠杆后浮盈%"""
        if self.direction in ('做多', 'LONG'):
            return (price - self.entry) / self.entry * 100 * self.leverage
        else:
            return (self.entry - price) / self.entry * 100 * self.leverage

    def update(self, current_price: float) -> dict:
        """
        每次价格更新时调用。
        返回 {'sl_moved': bool, 'new_sl': float, 'stage': str, 'pnl': float}
        """
        pnl = self._pnl(current_price)
        old_sl = self.sl_price
        old_stage = self.stage
        moved = False

        # 更新历史最优价
        if self.direction in ('做多', 'LONG'):
            if current_price > self.peak_price:
                self.peak_price = current_price
        else:
            if current_price < self.peak_price or self.peak_price == self.entry:
                self.peak_price = current_price

        # 阶段1：浮盈 > 10% → 移至保本
        if pnl >= 10.0 and self.stage == 'INITIAL':
            fee = self.entry * self.fee_pct * 2
            if self.direction in ('做多', 'LONG'):
                new_sl = self.entry + fee
                if new_sl > self.sl_price:
                    self.sl_price = new_sl; self.stage = 'BREAKEVEN'; moved = True
            else:
                new_sl = self.entry - fee
                if new_sl < self.sl_price:
                    self.sl_price = new_sl; self.stage = 'BREAKEVEN'; moved = True

        # 阶段2：浮盈 > 20% → 移至浮盈50%
        if pnl >= 20.0 and self.stage in ('INITIAL', 'BREAKEVEN'):
            if self.direction in ('做多', 'LONG'):
                lock_price = self.entry + (self.peak_price - self.entry) * 0.5
                if lock_price > self.sl_price:
                    self.sl_price = lock_price; self.stage = 'TRAIL_50'; moved = True
            else:
                lock_price = self.entry - (self.entry - self.peak_price) * 0.5
                if lock_price < self.sl_price:
                    self.sl_price = lock_price; self.stage = 'TRAIL_50'; moved = True

        # 阶段3：浮盈 > 35% → 移至浮盈70%，跟踪大行情
        if pnl >= 35.0 and self.stage in ('INITIAL', 'BREAKEVEN', 'TRAIL_50'):
            if self.direction in ('做多', 'LONG'):
                lock_price = self.entry + (self.peak_price - self.entry) * 0.7
                if lock_price > self.sl_price:
                    self.sl_price = lock_price; self.stage = 'TRAIL_70'; moved = True
            else:
                lock_price = self.entry - (self.entry - self.peak_price) * 0.7
                if lock_price < self.sl_price:
                    self.sl_price = lock_price; self.stage = 'TRAIL_70'; moved = True

        return {
            'sl_moved':   moved,
            'new_sl':     self.sl_price,
            'old_sl':     old_sl,
            'stage':      self.stage,
            'prev_stage': old_stage,
            'pnl':        round(pnl, 2),
            'peak_price': self.peak_price,
        }

    def should_exit(self, current_price: float) -> tuple[bool, str]:
        """检查是否触发移动止损"""
        if self.sl_price == 0.0:
            return False, ''
        if self.direction in ('做多', 'LONG'):
            if current_price <= self.sl_price:
                pnl = self._pnl(current_price)
                return True, f'移动止损触发({self.stage}) sl={self.sl_price:.6g} pnl={pnl:+.1f}%'
        else:
            if current_price >= self.sl_price:
                pnl = self._pnl(current_price)
                return True, f'移动止损触发({self.stage}) sl={self.sl_price:.6g} pnl={pnl:+.1f}%'
        return False, ''

    @property
    def summary(self) -> str:
        return (f'TrailingStop[{self.stage}] entry={self.entry:.6g} '
                f'sl={self.sl_price:.6g} peak={self.peak_price:.6g}')

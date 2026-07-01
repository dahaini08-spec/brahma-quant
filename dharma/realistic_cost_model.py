"""
realistic_cost_model.py — 达摩院真实成本建模层 v1.0
════════════════════════════════════════════════════
设计院·达摩院 封印 2026-07-01

使命：
  补全 dharma_system_backtest 长期缺失的成本建模
  消除 回测WR=37.8% vs 实盘WR=69.2% 的虚高差距

三层成本：
  1. Taker 手续费：入场+出场 × 0.04%
  2. 滑点估算：ATR×体制系数，CHOP扩大
  3. 资金费用：持仓时间 × funding_rate_avg

用法：
  from dharma.realistic_cost_model import CostModel, apply_cost_to_trades

达摩院封印约束：
  - 任何回测结果必须先经过此模块校正再输出
  - 不得在 cost_adj 后对结果进行人工修正
  - 参数变更需苏摩批准后方可落地
"""

# ── STATUS: ACTIVE ────────────────────────────────────────────
# P0级模块，每次回测必须调用
# LAST_REVIEW: 2026-07-01 | 设计院初次封印
# ─────────────────────────────────────────────────────────────

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


# ════════════════════════════════════════════════════════════════
# 1. 参数配置
# ════════════════════════════════════════════════════════════════

@dataclass
class CostConfig:
    """
    成本参数配置（达摩院统计实证）

    手续费：Binance USDS-M Futures Taker = 0.04%/单边
    资金费率：BTC历史均值 0.01%/8h（约0.0001/8h）
             BEAR体制资金费率常为负，做空时额外收入
    滑点：体制自适应（BEAR_TREND空单滑点最小，CHOP最大）
    """
    # 手续费
    taker_fee: float = 0.0004           # 0.04% 单边
    maker_fee: float = 0.0002           # 0.02% 单边（限价单，通常为maker）
    use_taker: bool  = True             # 默认用taker（保守估计）

    # 滑点系数（×ATR）
    slippage_normal:      float = 0.05  # 正常市场：ATR × 0.05
    slippage_chop:        float = 0.12  # 震荡市：ATR × 0.12（流动性差）
    slippage_bear_trend:  float = 0.04  # 趋势空头：ATR × 0.04（流动性好）
    slippage_bull_trend:  float = 0.04  # 趋势多头：ATR × 0.04
    slippage_max_pct:     float = 0.003 # 单边滑点上限 0.3%（防极值）

    # 资金费率（每8小时）
    funding_rate_avg:     float = 0.0001   # 0.01%/8h 历史均值（多头支付）
    funding_rate_bear:    float = -0.0001  # BEAR_TREND空头常为负（收入）
    funding_cycle_hours:  float = 8.0

    # 最低持仓时间估算（bar数→小时）
    bar_to_hours_1h:  float = 1.0
    bar_to_hours_15m: float = 0.25
    bar_to_hours_4h:  float = 4.0


DEFAULT_CONFIG = CostConfig()


# ════════════════════════════════════════════════════════════════
# 2. 核心计算
# ════════════════════════════════════════════════════════════════

class CostModel:
    """
    梵天达摩院真实成本建模器

    设计原则：
    - 入场+出场双向手续费
    - 体制感知滑点（CHOP体制滑点×3）
    - 资金费用按持仓时间线性累积
    - 所有成本以 pct of entry_price 表示（便于直接从pnl扣除）

    使用示例：
        model = CostModel()
        cost = model.total_cost(
            entry_price=2000.0,
            atr=30.0,
            direction='SHORT',
            regime='BEAR_TREND',
            hold_hours=16.0
        )
        adjusted_pnl = raw_pnl - cost
    """

    def __init__(self, config: CostConfig = DEFAULT_CONFIG):
        self.cfg = config

    def fee_cost(self) -> float:
        """双边手续费（入场+出场）"""
        if self.cfg.use_taker:
            return self.cfg.taker_fee * 2  # 双边
        return self.cfg.maker_fee * 2

    def slippage_cost(
        self,
        atr: float,
        entry_price: float,
        regime: str = 'UNKNOWN',
        direction: str = 'LONG'
    ) -> float:
        """
        滑点估算（基于ATR+体制）

        BEAR_TREND SHORT：趋势明确，流动性好，滑点小
        CHOP_MID：双向震荡，流动性差，滑点大
        其余：正常
        """
        regime_up = regime.upper()

        if 'CHOP' in regime_up:
            coeff = self.cfg.slippage_chop
        elif 'BEAR_TREND' in regime_up and direction == 'SHORT':
            coeff = self.cfg.slippage_bear_trend
        elif 'BULL_TREND' in regime_up and direction == 'LONG':
            coeff = self.cfg.slippage_bull_trend
        else:
            coeff = self.cfg.slippage_normal

        raw_slip = (atr * coeff) / (entry_price + 1e-9)
        # 双边（入场+出场各一次）
        return min(raw_slip * 2, self.cfg.slippage_max_pct * 2)

    def funding_cost(
        self,
        hold_hours: float,
        direction: str = 'LONG',
        regime: str = 'UNKNOWN'
    ) -> float:
        """
        资金费用（持仓时间累积）

        BEAR_TREND SHORT 通常资金费率为负（空头收入）
        多头通常支付资金费率
        """
        regime_up = regime.upper()
        periods = hold_hours / self.cfg.funding_cycle_hours

        if direction == 'SHORT' and 'BEAR' in regime_up:
            # 空头在BEAR体制通常收到资金费率（负费率）
            rate_per_period = self.cfg.funding_rate_bear
        elif direction == 'LONG':
            rate_per_period = self.cfg.funding_rate_avg
        else:
            rate_per_period = self.cfg.funding_rate_avg * 0.5

        # 负值=收入（对pnl是正贡献），正值=支出
        return rate_per_period * periods

    def total_cost(
        self,
        entry_price: float,
        atr: float,
        direction: str = 'LONG',
        regime: str = 'UNKNOWN',
        hold_hours: float = 12.0,
        include_funding: bool = True
    ) -> float:
        """
        全成本估算（pct，正值=成本，负值=收入）

        Returns:
            float: 需要从 raw_pnl 中扣除的成本（pct of entry）
                   正值 = 成本（亏损方向），负值 = 收入（如空头收到资金费）
        """
        fees     = self.fee_cost()
        slip     = self.slippage_cost(atr, entry_price, regime, direction)
        funding  = self.funding_cost(hold_hours, direction, regime) if include_funding else 0.0

        # 手续费+滑点是纯成本；资金费按方向可正可负
        return fees + slip + funding

    def adjust_pnl(
        self,
        raw_pnl: float,
        entry_price: float,
        atr: float,
        direction: str = 'LONG',
        regime: str = 'UNKNOWN',
        hold_hours: float = 12.0
    ) -> dict:
        """
        对单笔交易raw_pnl做成本校正

        Returns:
            {
              'raw_pnl':   float,
              'adj_pnl':   float,   # 校正后
              'fee':       float,
              'slippage':  float,
              'funding':   float,
              'total_cost':float,
            }
        """
        fee     = self.fee_cost()
        slip    = self.slippage_cost(atr, entry_price, regime, direction)
        funding = self.funding_cost(hold_hours, direction, regime)

        total_cost = fee + slip + funding
        adj_pnl    = raw_pnl - total_cost

        return {
            'raw_pnl':    round(raw_pnl, 6),
            'adj_pnl':    round(adj_pnl, 6),
            'fee':        round(fee, 6),
            'slippage':   round(slip, 6),
            'funding':    round(funding, 6),
            'total_cost': round(total_cost, 6),
            'win_adj':    adj_pnl > 0,
        }


# ════════════════════════════════════════════════════════════════
# 3. 批量应用接口（兼容 dharma_system_backtest 输出格式）
# ════════════════════════════════════════════════════════════════

def apply_cost_to_trades(
    trades: list,
    atr_col: str = 'atr',
    default_atr_pct: float = 0.015,  # 默认ATR=1.5%（无ATR数据时fallback）
    bar_hours: float = 1.0,          # 1H周期
    regime: str = 'UNKNOWN'
) -> list:
    """
    批量对trades列表应用成本校正

    Args:
        trades: [{'direction','pnl','exit_reason','score', ...}, ...]
        atr_col: trades字段里ATR的key（如有）
        default_atr_pct: 无ATR时用价格×该比例估算
        bar_hours: 每根K线对应小时数
        regime: 默认体制（可从trades字段读取）

    Returns:
        list: 同格式，新增 'adj_pnl', 'cost_detail' 字段
    """
    model = CostModel()
    adjusted = []

    for t in trades:
        raw_pnl   = t.get('pnl', 0.0)
        direction = t.get('direction', 'LONG')
        t_regime  = t.get('regime', regime)
        entry_price = t.get('entry_price', 100.0)  # 如无entry_price用100做归一化

        # ATR估算
        atr_val = t.get(atr_col, None)
        if atr_val is None:
            atr_val = entry_price * default_atr_pct

        # 持仓时间估算（exit_bar - entry_bar）× bar_hours
        hold_bars  = t.get('hold_bars', 12)
        hold_hours = hold_bars * bar_hours

        detail = model.adjust_pnl(
            raw_pnl     = raw_pnl,
            entry_price = entry_price,
            atr         = atr_val,
            direction   = direction,
            regime      = t_regime,
            hold_hours  = hold_hours
        )

        new_t = dict(t)
        new_t['adj_pnl']     = detail['adj_pnl']
        new_t['cost_detail'] = detail
        adjusted.append(new_t)

    return adjusted


def calc_stats_with_cost(trades: list, use_adj: bool = True) -> dict:
    """
    基于成本校正后的trades计算统计指标

    Args:
        trades: apply_cost_to_trades 输出
        use_adj: True=使用adj_pnl，False=原始pnl（对比用）
    """
    if not trades:
        return {}

    pnl_key = 'adj_pnl' if use_adj else 'pnl'
    pnls    = [t[pnl_key] for t in trades if pnl_key in t]

    if not pnls:
        return {}

    n    = len(pnls)
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]

    wr     = len(wins) / n
    avg_w  = sum(wins) / len(wins) if wins else 0
    avg_l  = abs(sum(loss) / len(loss)) if loss else 0
    pf     = (len(wins) * avg_w) / (len(loss) * avg_l + 1e-9)
    avg_pnl= sum(pnls) / n

    # 成本分析
    if use_adj and trades and 'cost_detail' in trades[0]:
        avg_fee  = sum(t['cost_detail']['fee']      for t in trades) / n
        avg_slip = sum(t['cost_detail']['slippage'] for t in trades) / n
        avg_fund = sum(t['cost_detail']['funding']  for t in trades) / n
        avg_cost = sum(t['cost_detail']['total_cost'] for t in trades) / n
    else:
        avg_fee = avg_slip = avg_fund = avg_cost = 0.0

    return {
        'n':        n,
        'wr':       round(wr, 4),
        'avg_pnl':  round(avg_pnl, 5),
        'pf':       round(pf, 3),
        'sharpe':   round(
            (sum(pnls)/n) / ((sum((p - sum(pnls)/n)**2 for p in pnls)/n)**0.5 + 1e-9)
            * math.sqrt(252), 2
        ),
        'max_dd':   round(min(pnls), 4),
        'cost_breakdown': {
            'avg_fee':     round(avg_fee,  6),
            'avg_slippage':round(avg_slip, 6),
            'avg_funding': round(avg_fund, 6),
            'avg_total':   round(avg_cost, 6),
            'cost_pct_of_avg_win': round(avg_cost / (avg_w + 1e-9), 3),
        }
    }


# ════════════════════════════════════════════════════════════════
# 4. 诊断工具：展示成本对WR的实际影响
# ════════════════════════════════════════════════════════════════

def cost_impact_report(trades: list, regime: str = 'MIXED') -> str:
    """
    生成成本影响报告，直观展示成本如何影响回测结果

    用于回答：为什么回测WR=37.8% vs 实盘WR=69.2%？
    """
    if not trades:
        return "❌ 无trades数据"

    raw_stats = calc_stats_with_cost(trades, use_adj=False)
    adj_trades = apply_cost_to_trades(trades, regime=regime)
    adj_stats  = calc_stats_with_cost(adj_trades, use_adj=True)

    lines = [
        "╔═══════════════════════════════════════════════════╗",
        "║  达摩院 · 真实成本影响报告                         ║",
        "╠═══════════════════════════════════════════════════╣",
        f"║  体制: {regime:<41}║",
        "╠═══════════════════════════════════════════════╤═══╣",
        f"║  {'指标':<20}{'原始回测':>12}{'成本校正后':>12}  ║",
        "╠═══════════════════════════════════════════════╪═══╣",
        f"║  {'信号数量':<20}{raw_stats.get('n',0):>12}{adj_stats.get('n',0):>12}  ║",
        f"║  {'胜率 WR':<20}{raw_stats.get('wr',0)*100:>11.1f}%{adj_stats.get('wr',0)*100:>11.1f}%  ║",
        f"║  {'盈亏因子 PF':<20}{raw_stats.get('pf',0):>12.3f}{adj_stats.get('pf',0):>12.3f}  ║",
        f"║  {'平均PnL/笔':<20}{raw_stats.get('avg_pnl',0)*100:>11.3f}%{adj_stats.get('avg_pnl',0)*100:>11.3f}%  ║",
        "╠═══════════════════════════════════════════════════╣",
    ]

    if 'cost_breakdown' in adj_stats:
        cb = adj_stats['cost_breakdown']
        lines += [
            f"║  成本拆解（每笔均值）:                           ║",
            f"║    手续费:  {cb['avg_fee']*100:>8.4f}%                              ║",
            f"║    滑  点:  {cb['avg_slippage']*100:>8.4f}%                              ║",
            f"║    资金费:  {cb['avg_funding']*100:>8.4f}%                              ║",
            f"║    合  计:  {cb['avg_total']*100:>8.4f}%  (胜率拖累≈{cb['avg_total']/0.02*100:.0f}pp)       ║",
        ]

    wr_delta = (raw_stats.get('wr', 0) - adj_stats.get('wr', 0)) * 100
    lines += [
        "╠═══════════════════════════════════════════════════╣",
        f"║  成本导致WR下降: {wr_delta:>+.1f}pp                          ║",
        "║  → 达摩院回测应以成本校正后数据为准                 ║",
        "╚═══════════════════════════════════════════════════╝",
    ]

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 5. 主入口（独立运行验证）
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("🔍 CostModel 单元测试\n")

    model = CostModel()

    # 测试1：BEAR_TREND SHORT（最优场景）
    c1 = model.total_cost(
        entry_price=2000, atr=30,
        direction='SHORT', regime='BEAR_TREND', hold_hours=16
    )
    print(f"BEAR_TREND SHORT 16h 总成本: {c1*100:.4f}%")

    # 测试2：CHOP_MID LONG（最差场景）
    c2 = model.total_cost(
        entry_price=2000, atr=30,
        direction='LONG', regime='CHOP_MID', hold_hours=24
    )
    print(f"CHOP_MID LONG 24h 总成本: {c2*100:.4f}%")

    # 测试3：批量应用（模拟100笔交易）
    import random
    mock_trades = []
    for i in range(100):
        raw = random.gauss(0.02, 0.04)  # 模拟raw pnl
        mock_trades.append({
            'pnl': raw,
            'direction': random.choice(['LONG', 'SHORT']),
            'entry_price': 2000.0,
            'atr': 30.0,
            'hold_bars': random.randint(4, 24),
            'exit_reason': 'tp' if raw > 0 else 'sl',
        })

    print("\n" + cost_impact_report(mock_trades, regime='MIXED'))

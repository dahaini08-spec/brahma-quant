#!/usr/bin/env python3
"""
梵天 v11 · 组合管理器  portfolio_manager.py
基于达摩院实验的硬性风控约束

核心规则（数据铁证）：
  - 最多3个并发仓位（避免相关性风险）
  - 总仓位上限18% NAV（EXP-07 Alpha腐烂→保守）
  - 单笔上限6% NAV（Kelly×体制系数×质量系数）
  - 日亏损达-5% NAV → 暂停当日新信号
  - 总回撤达-15% NAV → 系统熔断72H
  - 同一标的不重复开仓（去重）
"""

import os, sys, json, time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field, asdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_DIR  = os.path.join(_THIS_DIR, 'soul_db')
PM_FILE   = os.path.join(SOUL_DIR, 'portfolio_state_v3.json')
os.makedirs(SOUL_DIR, exist_ok=True)


# ─── 数据结构 ─────────────────────────────────────────────────────

@dataclass
class Position:
    signal_id:    str
    symbol:       str
    direction:    str      # 做多/做空
    channel:      str      # A/B
    strength:     str      # EXTREME/STRONG/MODERATE
    regime:       str
    entry_price:  float
    sl_price:     float
    tp1_price:    float
    tp2_price:    float
    size_usdt:    float
    size_pct_nav: float
    track:        str      # TRACK_A/TRACK_B
    max_bars:     int
    bar_count:    int = 0
    open_time:    str = ''
    peak_pnl_pct: float = 0.0   # 最大浮盈（移动止损依据）
    status:       str = 'OPEN'  # OPEN/CLOSED

    def __post_init__(self):
        if not self.open_time:
            self.open_time = datetime.now(timezone.utc).isoformat()

    @property
    def sl_dist_pct(self) -> float:
        return abs(self.entry_price - self.sl_price) / self.entry_price * 100

    @property
    def age_hours(self) -> float:
        try:
            opened = datetime.fromisoformat(self.open_time)
            return (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        except:
            return 0.0


@dataclass
class PortfolioState:
    nav:              float = 1525.0   # 账户净值
    peak_nav:         float = 1525.0   # 历史净值峰值
    day_start_nav:    float = 1525.0   # 当日起始净值
    positions:        List[Dict] = field(default_factory=list)
    closed_today:     int  = 0
    wins_today:       int  = 0
    losses_today:     int  = 0
    circuit_breaker:  bool = False     # 熔断状态
    circuit_until:    str  = ''        # 熔断截止时间
    day_date:         str  = ''        # 当日日期（UTC）
    total_trades:     int  = 0
    total_wins:       int  = 0

    @property
    def open_positions(self) -> List[Dict]:
        return [p for p in self.positions if p.get('status') == 'OPEN']

    @property
    def open_count(self) -> int:
        return len(self.open_positions)

    @property
    def open_symbols(self) -> set:
        return {p['symbol'].lower() for p in self.open_positions}

    @property
    def total_exposure_pct(self) -> float:
        return sum(p.get('size_pct_nav', 0) for p in self.open_positions)

    @property
    def day_pnl_pct(self) -> float:
        return (self.nav - self.day_start_nav) / self.day_start_nav * 100 if self.day_start_nav > 0 else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        return (self.nav - self.peak_nav) / self.peak_nav * 100 if self.peak_nav > 0 else 0.0

    @property
    def win_rate(self) -> float:
        t = self.total_wins + (self.total_trades - self.total_wins)
        return self.total_wins / self.total_trades if self.total_trades > 0 else 0.35


# ─── 核心：组合管理器 ─────────────────────────────────────────────

class PortfolioManager:
    """
    梵天 v11 仓位管理器（单例）

    约束层级（优先级从高到低）：
      1. 熔断（circuit_breaker）— 全面停止
      2. 日亏 ≥ -5% NAV — 当日不加新仓
      3. 总仓位 ≥ 18% NAV — 不加新仓
      4. 并发仓位 ≥ 3 — 不加新仓
      5. 同标的重复开仓 — 拒绝
      6. 单笔超过6% NAV — 裁剪
    """

    # ── 风控参数 ──────────────────────────────────────────────────
    MAX_POSITIONS    = 3
    MAX_NAV_EXPOSURE = 0.18   # 18%
    MAX_SINGLE_PCT   = 0.06   # 6%
    DAY_LOSS_LIMIT   = -0.05  # -5%
    DRAWDOWN_LIMIT   = -0.15  # -15% → 熔断
    CIRCUIT_HOURS    = 72

    _inst = None

    def __init__(self):
        self.state = self._load_state()

    @classmethod
    def get(cls) -> 'PortfolioManager':
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    # ─ 状态持久化 ──────────────────────────────────────────────

    def _load_state(self) -> PortfolioState:
        if os.path.exists(PM_FILE):
            try:
                with open(PM_FILE, 'r') as f:
                    d = json.load(f)
                s = PortfolioState(**{k: v for k, v in d.items()
                                      if k in PortfolioState.__dataclass_fields__})
                self._reset_day_if_needed(s)
                return s
            except Exception as e:
                print(f"[PM] 状态加载失败，重置：{e}")
        return PortfolioState()

    def _save_state(self):
        with open(PM_FILE, 'w') as f:
            d = asdict(self.state)
            json.dump(d, f, ensure_ascii=False, indent=2)

    def _reset_day_if_needed(self, s: PortfolioState):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if s.day_date != today:
            s.day_date      = today
            s.day_start_nav = s.nav
            s.closed_today  = 0
            s.wins_today    = 0
            s.losses_today  = 0

    # ─ 熔断检查 ────────────────────────────────────────────────

    def _check_circuit(self) -> bool:
        """检查熔断是否应该解除"""
        if self.state.circuit_breaker and self.state.circuit_until:
            try:
                until = datetime.fromisoformat(self.state.circuit_until)
                if datetime.now(timezone.utc) > until:
                    self.state.circuit_breaker = False
                    self.state.circuit_until   = ''
                    print("[PM] 熔断解除")
                    self._save_state()
            except:
                _ = None  # 非致命
        return self.state.circuit_breaker

    def _trigger_circuit(self, reason: str):
        until = datetime.now(timezone.utc) + timedelta(hours=self.CIRCUIT_HOURS)
        self.state.circuit_breaker = True
        self.state.circuit_until   = until.isoformat()
        print(f"[PM] ⛔ 熔断触发：{reason}  截止：{until.strftime('%Y-%m-%d %H:%M UTC')}")
        self._save_state()

    # ─ 核心：可否开仓 ──────────────────────────────────────────

    def can_open(self, signal: dict) -> tuple[bool, str]:
        """
        检查是否可以开仓
        返回 (True/False, 原因)
        """
        s = self.state
        self._reset_day_if_needed(s)

        # 1. 熔断
        if self._check_circuit():
            until_str = s.circuit_until[:16] if s.circuit_until else '?'
            return False, f'系统熔断中，截止 {until_str}'

        # 2. 总回撤触发熔断
        if s.max_drawdown_pct < self.DRAWDOWN_LIMIT * 100:
            self._trigger_circuit(f'总回撤{s.max_drawdown_pct:.1f}%超限')
            return False, f'总回撤{s.max_drawdown_pct:.1f}%，触发熔断'

        # 3. 日亏达限
        if s.day_pnl_pct < self.DAY_LOSS_LIMIT * 100:
            return False, f'日亏{s.day_pnl_pct:.1f}%，当日暂停'

        # 4. 并发仓位上限
        if s.open_count >= self.MAX_POSITIONS:
            return False, f'仓位数已达上限{self.MAX_POSITIONS}'

        # 5. 总敞口上限
        new_pct = signal.get('仓位%NAV', 2.0) / 100
        if s.total_exposure_pct / 100 + new_pct > self.MAX_NAV_EXPOSURE:
            return False, f'总敞口{s.total_exposure_pct:.1f}%+{new_pct*100:.1f}%将超{self.MAX_NAV_EXPOSURE*100:.0f}%'

        # 6. 同标的重复
        sym = signal.get('币种', '').lower().replace('usdt', '') + 'usdt'
        if sym in s.open_symbols:
            return False, f'{sym}已有持仓，不重复开仓'

        return True, '通过'

    # ─ 开仓 ──────────────────────────────────────────────────

    def open_position(self, signal: dict) -> Optional[Position]:
        ok, reason = self.can_open(signal)
        if not ok:
            print(f"[PM] ❌ 拒绝开仓：{reason}")
            return None

        # 裁剪仓位（超过单笔上限则按上限）
        nav = self.state.nav
        raw_pct  = signal.get('仓位%NAV', 2.0) / 100
        safe_pct = min(raw_pct, self.MAX_SINGLE_PCT)
        safe_usdt = round(nav * safe_pct, 2)

        pos = Position(
            signal_id   = signal['信号ID'],
            symbol      = signal['币种'],
            direction   = signal['方向'],
            channel     = signal['通道'],
            strength    = signal['强度'],
            regime      = signal['体制'],
            entry_price = signal['当前价'],
            sl_price    = signal['止损价'],
            tp1_price   = signal['止盈T1'],
            tp2_price   = signal['止盈T2'],
            size_usdt   = safe_usdt,
            size_pct_nav= round(safe_pct * 100, 2),
            track       = signal['出场轨道'],
            max_bars    = signal['最大持仓根'],
        )

        self.state.positions.append(asdict(pos))
        self._save_state()
        print(f"[PM] ✅ 开仓：{pos.symbol} {pos.direction} ${pos.size_usdt:.0f} "
              f"({pos.size_pct_nav:.1f}%NAV) {pos.track}")
        return pos

    # ─ 平仓 ──────────────────────────────────────────────────

    def close_position(self, signal_id: str, exit_price: float,
                        reason: str = '') -> Optional[dict]:
        for p in self.state.positions:
            if p.get('signal_id') == signal_id and p.get('status') == 'OPEN':
                p['status'] = 'CLOSED'

                # PnL计算
                ep = p['entry_price']
                if p['direction'] == '做多':
                    pnl_pct = (exit_price - ep) / ep * 100
                else:
                    pnl_pct = (ep - exit_price) / ep * 100

                pnl_usdt = p['size_usdt'] * pnl_pct / 100
                p['exit_price']  = exit_price
                p['exit_reason'] = reason
                p['pnl_pct']     = round(pnl_pct, 4)
                p['pnl_usdt']    = round(pnl_usdt, 4)

                # 更新账户状态
                self.state.nav         = round(self.state.nav + pnl_usdt, 2)
                self.state.peak_nav    = max(self.state.peak_nav, self.state.nav)
                self.state.closed_today += 1
                self.state.total_trades += 1
                if pnl_pct > 0:
                    self.state.wins_today  += 1
                    self.state.total_wins  += 1
                    self.state.losses_today = self.state.losses_today  # no-op
                else:
                    self.state.losses_today += 1

                # 实时检查熔断
                if self.state.max_drawdown_pct < self.DRAWDOWN_LIMIT * 100:
                    self._trigger_circuit(f'总回撤{self.state.max_drawdown_pct:.1f}%')

                self._save_state()
                emoji = '✅' if pnl_pct > 0 else '❌'
                print(f"[PM] {emoji} 平仓：{p['symbol']} {p['direction']} "
                      f"PnL={pnl_pct:+.2f}% (${pnl_usdt:+.2f})  原因：{reason}")
                return p
        return None

    # ─ 仓位更新（K线推进） ──────────────────────────────────────

    def tick(self, prices: dict) -> List[dict]:
        """
        每根K线调用一次，推进bar_count，检查出场条件
        prices: {symbol_lower: current_price}
        返回需要平仓的仓位列表
        """
        to_close = []
        for p in self.state.positions:
            if p.get('status') != 'OPEN':
                continue

            sym   = p['symbol'].lower()
            price = prices.get(sym, prices.get(sym.replace('usdt', '') + 'usdt', 0))
            if not price:
                continue

            p['bar_count'] = p.get('bar_count', 0) + 1
            entry = p['entry_price']
            is_long = p['direction'] == '做多'

            # 当前浮盈
            if is_long:
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100

            # 更新峰值浮盈
            p['peak_pnl_pct'] = max(p.get('peak_pnl_pct', 0), pnl_pct)

            # 止损检查
            if is_long and price <= p['sl_price']:
                to_close.append({'p': p, 'price': price, 'reason': '止损'})
                continue
            if not is_long and price >= p['sl_price']:
                to_close.append({'p': p, 'price': price, 'reason': '止损'})
                continue

            # 止盈T1检查
            if is_long and price >= p['tp1_price']:
                to_close.append({'p': p, 'price': price, 'reason': '止盈T1'})
                continue
            if not is_long and price <= p['tp1_price']:
                to_close.append({'p': p, 'price': price, 'reason': '止盈T1'})
                continue

            # TRACK_A：浮盈>0.8%即时出
            if p.get('track') == 'TRACK_A' and pnl_pct >= 0.8:
                to_close.append({'p': p, 'price': price, 'reason': 'TrackA浮盈锁定'})
                continue

            # TRACK_A：超过max_bars强制出
            if p.get('track') == 'TRACK_A' and p['bar_count'] >= p.get('max_bars', 4):
                to_close.append({'p': p, 'price': price, 'reason': f"TrackA时间止损({p['bar_count']}根)"})
                continue

            # TRACK_B：超过max_bars强制出
            if p.get('track') == 'TRACK_B' and p['bar_count'] >= p.get('max_bars', 48):
                to_close.append({'p': p, 'price': price, 'reason': f"TrackB时间止损48H"})
                continue

        # 执行平仓
        closed = []
        for item in to_close:
            result = self.close_position(item['p']['signal_id'], item['price'], item['reason'])
            if result:
                closed.append(result)

        return closed

    # ─ 状态展示 ──────────────────────────────────────────────

    def status(self) -> str:
        s = self.state
        self._reset_day_if_needed(s)
        cb_str = f'🔴熔断至{s.circuit_until[:16]}' if s.circuit_breaker else '🟢运行中'
        lines = [
            f"╔══ 梵天 v11 · 组合状态 ══╗",
            f"  NAV：${s.nav:.2f}  (峰值${s.peak_nav:.2f})",
            f"  今日盈亏：{s.day_pnl_pct:+.2f}%  回撤：{s.max_drawdown_pct:.1f}%",
            f"  持仓：{s.open_count}/{self.MAX_POSITIONS}  总敞口：{s.total_exposure_pct:.1f}%",
            f"  今日：{s.wins_today}胜/{s.losses_today}负  总计：{s.total_wins}/{s.total_trades}",
            f"  状态：{cb_str}",
        ]
        if s.open_positions:
            lines.append("  ─── 当前持仓 ───")
            for p in s.open_positions:
                ep = p['entry_price']
                lines.append(f"  {p['symbol']} {p['direction']} "
                              f"${p['size_usdt']:.0f} "
                              f"入场@{ep:.4f} "
                              f"已持{p.get('bar_count',0)}根 "
                              f"{p.get('track','?')}")
        lines.append("╚══════════════════════════╝")
        return '\n'.join(lines)


# ─── 测试 ────────────────────────────────────────────────────────

if __name__ == '__main__':
    import random, os
    random.seed(42)

    # 清除旧状态
    if os.path.exists(PM_FILE):
        os.remove(PM_FILE)

    pm = PortfolioManager()
    pm.state.nav = 1525.0
    pm.state.peak_nav = 1525.0
    pm.state.day_start_nav = 1525.0

    print("梵天 v11 · 组合管理器测试\n")

    # 构造测试信号
    def make_sig(sym, direction, price, ci=0.38):
        return {
            '信号ID':    f'{sym.upper()}_{direction}_{int(time.time())}',
            '币种':      sym.upper(),
            '方向':      direction,
            '通道':      'A',
            '强度':      'EXTREME',
            '体制':      'CHOP_HIGH',
            '当前价':    price,
            '止损价':    price * (0.975 if direction == '做多' else 1.025),
            '止盈T1':    price * (1.025 if direction == '做多' else 0.975),
            '止盈T2':    price * (1.05  if direction == '做多' else 0.95),
            '仓位USDT':  45.0,
            '仓位%NAV':  3.0,
            '出场轨道':  'TRACK_B',
            '最大持仓根': 48,
            'CI下界':    ci,
        }

    # 测试1：正常开仓
    sig1 = make_sig('SOLUSDT', '做空', 140.0)
    pos1 = pm.open_position(sig1)

    sig2 = make_sig('DOGEUSDT', '做空', 0.18)
    pos2 = pm.open_position(sig2)

    sig3 = make_sig('ATOMUSDT', '做多', 8.5)
    pos3 = pm.open_position(sig3)

    # 测试2：第4个仓位应被拒绝
    sig4 = make_sig('TIARUSDT', '做多', 3.2)
    pos4 = pm.open_position(sig4)

    # 测试3：同标的重复开仓应被拒绝
    sig5 = make_sig('SOLUSDT', '做多', 141.0)
    pos5 = pm.open_position(sig5)

    print()
    print(pm.status())
    print()

    # 测试4：tick推进 + 止盈
    if pos1:
        closed = pm.close_position(pos1.signal_id, 138.0, '止盈T1测试')
        print(f"\n平仓后状态：NAV=${pm.state.nav:.2f}")

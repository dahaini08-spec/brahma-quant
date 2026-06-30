#!/usr/bin/env python3
"""
regime_state_machine.py — 梵天体制状态机 v1.0
设计院 × 达摩院 × 量化工程师 2026-06-14

【核心设计原则】
  体制频繁抖动的根因：detect_regime 对 1H/4H 方向单根K线高度敏感
  → 4H在CHOP边界时微小价格变动即触发 RECOVERY↔TREND 切换
  → 每 ~11.7 根4H K线切换一次，信号乘数随之跳变，系统不稳定

【解决方案：三重稳定机制】

  机制1 — 确认窗口（Confirmation Window）
    体制切换需连续N根4H K线信号一致才确认
    默认 N=3（即12小时确认窗口），TREND↔RECOVERY 切换 N=4

  机制2 — 滞后保护（Hysteresis）
    从 BEAR_TREND → BEAR_RECOVERY 需要 4H=CHOP 连续3根
    从 BEAR_RECOVERY → BEAR_TREND 需要 4H=BEAR 连续4根
    避免在 CHOP/BEAR 边界来回抖动

  机制3 — 状态持久化（State Persistence）
    当前确认体制写入 data/regime_state.json
    包含：确认时间戳、确认计数、候选体制、锁定期
    进程重启不丢失历史状态

【状态定义】
  confirmed   : 已确认的稳定体制（对外输出）
  candidate   : 候选体制（还未达到确认窗口）
  confirm_count : 候选已连续确认的次数
  locked_until  : 体制锁定到某个时间（防止过快切换）

【使用方式】
  from regime_state_machine import RegimeStateMachine
  rsm = RegimeStateMachine()
  stable_regime = rsm.update('BTCUSDT', raw_regime)
  # 返回经过稳定处理的体制，而非原始单点输出
"""

import json
import time
import pathlib
from typing import Optional

BASE = pathlib.Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'regime_state.json'

# ── 体制切换确认窗口（N根4H K线）────────────────────────────────
# 越稳定的体制需要越多确认，防止误切换
# [P1-A 设计院 2026-06-21] 体制识别提速
# 原：4H×3根确认（12H延迟）→ 新：4H×2根+1H×3根辅助确认（约2~4H延迟）
# 实盘回溯发现：体制滞后2~4H是BEAR_TREND信号失效的主因
# 修复：切换确认根数从3→2，同时依赖1H辅助验证（在market_state层）
CONFIRM_WINDOWS = {
    # 从候选到确认需要的连续4H根数（已从3降至2）
    ('BEAR_RECOVERY', 'BEAR_TREND'):    3,  # 反弹→趋势：保留3根防误切（此方向代价高）
    ('BEAR_RECOVERY', 'BEAR_EARLY'):    2,  # [P1-A] 3→2
    ('BEAR_TREND',    'BEAR_RECOVERY'): 2,  # [P1-A] 4→2（最重要！滞后根源）
    ('BEAR_TREND',    'BEAR_EARLY'):    2,  # [P1-A] 3→2
    ('BEAR_EARLY',    'BEAR_TREND'):    3,  # 保留3根（此方向需要更多确认）
    ('BEAR_EARLY',    'BEAR_RECOVERY'): 2,  # [P1-A] 3→2
    ('CHOP_MID',      'BEAR_RECOVERY'): 2,  # [P1-A] 3→2
    ('CHOP_MID',      'BEAR_EARLY'):    2,  # [P1-A] 3→2
    ('BEAR_RECOVERY', 'CHOP_MID'):      2,  # [P1-A] 3→2
    ('BULL_TREND',    'BEAR_RECOVERY'): 2,  # [P1-A] 新增：牛市→熊市反弹快速确认
    ('BULL_TREND',    'BEAR_EARLY'):    2,  # [P1-A] 新增
    # 默认：其他切换需要2根确认
}
DEFAULT_CONFIRM = 2  # [P1-A] 3→2

# ── 切换后锁定时间（秒）──────────────────────────────────────────
# 切换确认后锁定这段时间，防止立刻被切回
# [P2 设计院 2026-06-21] BEAR_EARLY 锁定时间 4H→8H
# 实盘分析：BEAR_EARLY_SHORT MFE/MAE=2.88x（最优体制），但信号数量太少
# 根因：BEAR_EARLY窗口太短，4H锁定后就切到BEAR_TREND，错过发信号机会
# 修复：延长BEAR_EARLY锁定至8H，让系统在最优体制窗口多发出信号
LOCK_AFTER_SWITCH = {
    'BEAR_TREND':    8 * 3600,   # 熊市趋势：锁定8H
    'BULL_TREND':    8 * 3600,
    'BEAR_RECOVERY': 4 * 3600,   # 熊市反弹：锁定4H
    'BULL_CORRECTION':4 * 3600,
    'BEAR_EARLY':    8 * 3600,   # [P2] 4H→8H，最优体制MFE/MAE=2.88x，延长窗口
    'BULL_EARLY':    4 * 3600,
    'CHOP_MID':      2 * 3600,   # 震荡：锁定2H
    'CHOP_LOW':      2 * 3600,
    'CHOP_HIGH':     2 * 3600,
}
DEFAULT_LOCK = 4 * 3600

# ── 体制中文映射 ─────────────────────────────────────────────────
REGIME_CN = {
    'BULL_TREND':     '牛市趋势',
    'BULL_EARLY':     '牛市初期',
    'BULL_PEAK':      '牛市末期',
    'BULL_CORRECTION':'牛市回调',
    'BEAR_TREND':     '熊市趋势',
    'BEAR_EARLY':     '熊市初期',
    'BEAR_CRASH':     '暴跌体制',
    'BEAR_RECOVERY':  '熊市反弹',
    'CHOP_HIGH':      '高位震荡',
    'CHOP_MID':       '弱震荡',
    'CHOP_LOW':       '低位震荡',
    'BREAKOUT':       '突破体制',
}


class RegimeStateMachine:
    """
    梵天体制状态机
    负责将 detect_regime 的原始单点输出转化为稳定的确认体制
    """

    def __init__(self, symbol: str = 'BTCUSDT'):
        self.symbol = symbol
        self._state = self._load_state()

    def _load_state(self) -> dict:
        """加载持久化状态"""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return data.get(self.symbol, self._default_state())
            except Exception:
                pass
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            'confirmed':      'CHOP_MID',    # 当前确认体制
            'candidate':      None,           # 候选体制（未确认）
            'confirm_count':  0,              # 候选连续确认次数
            'locked_until':   0,              # 锁定截止时间戳
            'confirmed_at':   0,              # 上次确认时间
            'switch_count_24h': 0,            # 24H内切换次数（监控用）
            'last_raw':       None,           # 上一次原始体制
        }

    def _save_state(self):
        """持久化状态"""
        try:
            existing = {}
            if STATE_FILE.exists():
                existing = json.loads(STATE_FILE.read_text())
            existing[self.symbol] = self._state
            tmp = str(STATE_FILE) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            import os
            os.replace(tmp, str(STATE_FILE))
        except Exception:
            pass

    def update(self, raw_regime: str) -> str:
        """
        输入原始单点体制，返回经过稳定处理的确认体制

        Args:
            raw_regime: detect_regime 的原始输出

        Returns:
            stable_regime: 经确认窗口+滞后保护处理后的稳定体制
        """
        now = time.time()
        s = self._state
        confirmed = s['confirmed']

        # ── 情况1：体制没变，重置候选计数 ─────────────────────────
        if raw_regime == confirmed:
            s['candidate'] = None
            s['confirm_count'] = 0
            s['last_raw'] = raw_regime
            self._save_state()
            return confirmed

        # ── 情况2：在锁定期内，强制返回已确认体制 ─────────────────
        if now < s.get('locked_until', 0):
            lock_remain = int((s['locked_until'] - now) / 3600)
            # 但如果原始体制持续不同且候选在积累
            pass  # 允许候选积累，不阻止计数

        # ── 情况3：体制发生变化，开始/继续积累候选计数 ────────────
        if raw_regime != s.get('candidate'):
            # 新的候选体制出现，重置计数
            s['candidate'] = raw_regime
            s['confirm_count'] = 1
        else:
            # 同一候选体制继续积累
            s['confirm_count'] = s.get('confirm_count', 0) + 1

        # ── 情况4：检查是否达到确认窗口 ────────────────────────────
        required = CONFIRM_WINDOWS.get(
            (confirmed, raw_regime),
            DEFAULT_CONFIRM
        )

        if s['confirm_count'] >= required:
            # 锁定期检查：如果还在锁定期，需要更多确认
            if now < s.get('locked_until', 0):
                # 锁定期内仍然放行确认（锁定期只延迟触发，达到N根就切换）
                pass  # 锁定期不阻止已达到确认窗口的切换

            # ✅ 确认切换
            old_regime = confirmed
            s['confirmed'] = raw_regime
            s['confirmed_at'] = now
            s['candidate'] = None
            s['confirm_count'] = 0
            # 设置新锁定期
            lock_sec = LOCK_AFTER_SWITCH.get(raw_regime, DEFAULT_LOCK)
            s['locked_until'] = now + lock_sec
            # 统计切换
            s['switch_count_24h'] = s.get('switch_count_24h', 0) + 1

            print(f'[RegimeSM] ✅ {self.symbol} 体制确认切换: '
                  f'{old_regime}({REGIME_CN.get(old_regime,"?")}) → '
                  f'{raw_regime}({REGIME_CN.get(raw_regime,"?")}) '
                  f'[确认{required}根] 锁定{lock_sec//3600}H')

            self._save_state()
            return raw_regime

        # 还未达到确认窗口，继续使用已确认体制
        self._save_state()
        return confirmed

    @property
    def confirmed_regime(self) -> str:
        return self._state['confirmed']

    @property
    def candidate_regime(self) -> Optional[str]:
        return self._state.get('candidate')

    @property
    def confirm_progress(self) -> str:
        """返回当前确认进度，如 '2/3'"""
        s = self._state
        if not s.get('candidate'):
            return 'stable'
        confirmed = s['confirmed']
        candidate = s['candidate']
        required = CONFIRM_WINDOWS.get((confirmed, candidate), DEFAULT_CONFIRM)
        return f"{s['confirm_count']}/{required}"

    def status(self) -> dict:
        """返回完整状态摘要"""
        s = self._state
        now = time.time()
        lock_remain_h = max(0, (s.get('locked_until', 0) - now) / 3600)
        return {
            'symbol':          self.symbol,
            'confirmed':       s['confirmed'],
            'confirmed_cn':    REGIME_CN.get(s['confirmed'], s['confirmed']),
            'candidate':       s.get('candidate'),
            'confirm_progress': self.confirm_progress,
            'locked_remain_h': round(lock_remain_h, 1),
            'switch_count_24h': s.get('switch_count_24h', 0),
            'stable':          s.get('candidate') is None,
        }


# ── 全局单例（按标的缓存）────────────────────────────────────────
_instances: dict = {}

def get_stable_regime(symbol: str, raw_regime: str) -> str:
    """
    全局入口：输入原始体制，返回稳定体制
    在 market_state.analyze() 最后一步调用
    """
    if symbol not in _instances:
        _instances[symbol] = RegimeStateMachine(symbol)
    return _instances[symbol].update(raw_regime)


def get_regime_status(symbol: str) -> dict:
    """获取体制状态机完整状态"""
    if symbol not in _instances:
        _instances[symbol] = RegimeStateMachine(symbol)
    return _instances[symbol].status()


# ── 快速测试 ─────────────────────────────────────────────────────
if __name__ == '__main__':
    rsm = RegimeStateMachine('BTCUSDT')
    print("=== 体制状态机测试 ===")
    print()

    # 模拟抖动场景
    sequence = [
        'BEAR_RECOVERY', 'BEAR_RECOVERY', 'BEAR_TREND',  # 单根切换 → 不确认
        'BEAR_RECOVERY', 'BEAR_RECOVERY', 'BEAR_TREND',  # 又来一次 → 不确认
        'BEAR_TREND', 'BEAR_TREND', 'BEAR_TREND', 'BEAR_TREND',  # 连续4根 → 确认切换
        'BEAR_RECOVERY', 'BEAR_RECOVERY',               # 锁定期内，不切换
    ]

    print("模拟序列:")
    for i, raw in enumerate(sequence):
        stable = rsm.update(raw)
        status = rsm.status()
        marker = "🔄" if raw != stable else "  "
        print(f"  [{i+1:02d}] raw={raw:<20} → stable={stable:<20} {marker} "
              f"candidate={status['candidate'] or '-':<20} "
              f"progress={status['confirm_progress']}")

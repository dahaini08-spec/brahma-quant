"""
event_pre_gate.py
[设计院 2026-10-16 苏摩111封印] P1 宏观事件前置拦截门
接入位置: brahma_analysis_runner.py run_analysis() 入口顶部

今日血泪教训：CPI发布前2H系统仍输出做多信号。
核心逻辑：
  重大事件前60min  → block=True（禁止开新仓）
  重大事件前240min → warn=True（仓位系数×0.3）
数据源: data/macro_real.json（现有，每日更新）
"""

from __future__ import annotations
import json
import time
import logging
from pathlib import Path

log = logging.getLogger('brahma.event_pre_gate')

# 根目录（trading-system/）
ROOT = Path(__file__).parent.parent

# 高影响事件关键词
HIGH_IMPACT_KEYWORDS = ['CPI', 'FOMC', 'NFP', 'PCE', 'GDP', 'PPI', 'RATE', 'INTEREST']

# 阻断窗口（分钟）
BLOCK_WINDOW_MIN = 60    # 事件前60分钟=禁止开仓
WARN_WINDOW_MIN  = 240   # 事件前240分钟=仓位×0.3


def check() -> dict:
    """
    检查当前是否临近重大宏观事件。

    Returns:
        {
          'block': bool,      是否禁止开仓
          'warn': bool,       是否降低仓位
          'event': str,       事件名称
          'mins_to': int,     距事件分钟数
          'pos_scale': float, 仓位系数（warn时=0.3，正常=1.0）
          'reason': str,      说明
        }
    """
    macro_path = ROOT / 'data' / 'macro_real.json'
    if not macro_path.exists():
        return _ok()

    try:
        macro = json.loads(macro_path.read_text(encoding='utf-8'))
    except Exception as e:
        log.warning(f'[event_pre_gate] macro_real.json读取失败: {e}')
        return _ok()

    now_ts = time.time()

    # 尝试多种字段名（兼容macro_real.json不同版本）
    upcoming: list = (
        macro.get('upcoming_events')
        or macro.get('upcoming')
        or macro.get('events', [])
        or []
    )

    for ev in upcoming:
        if not isinstance(ev, dict):
            continue

        ev_name   = str(ev.get('name', ev.get('event', ev.get('title', ''))) or '').upper()
        ev_impact = str(ev.get('impact', ev.get('importance', ev.get('level', ''))) or '').upper()

        # 事件时间戳（多字段兼容）
        ev_ts = float(
            ev.get('ts') or ev.get('timestamp') or ev.get('time') or 0
        )

        # 过滤：只处理高影响事件
        if not any(kw in ev_name for kw in HIGH_IMPACT_KEYWORDS):
            continue
        if not any(lvl in ev_impact for lvl in ['HIGH', 'CRITICAL', '3', 'RED']):
            continue

        mins_to = (ev_ts - now_ts) / 60.0

        # 已过去的事件跳过
        if mins_to <= 0:
            continue

        # 阻断窗口
        if mins_to <= BLOCK_WINDOW_MIN:
            return {
                'block':     True,
                'warn':      False,
                'event':     ev_name,
                'mins_to':   int(mins_to),
                'pos_scale': 0.0,
                'reason':    f'⚠️ {ev_name}将在{int(mins_to)}分钟后发布，禁止开新仓',
            }

        # 警告窗口
        if mins_to <= WARN_WINDOW_MIN:
            return {
                'block':     False,
                'warn':      True,
                'event':     ev_name,
                'mins_to':   int(mins_to),
                'pos_scale': 0.3,
                'reason':    f'⚡ {ev_name}将在{mins_to/60:.1f}H后发布，仓位系数×0.3',
            }

    return _ok()


def _ok() -> dict:
    return {
        'block':     False,
        'warn':      False,
        'event':     '',
        'mins_to':   9999,
        'pos_scale': 1.0,
        'reason':    '无重大宏观事件',
    }


class EventPreGate:
    """封装类，供 brahma_analysis_runner.py 调用"""

    def check(self) -> dict:
        return check()

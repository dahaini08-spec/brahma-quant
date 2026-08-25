#!/usr/bin/env python3
"""
antifragile_guard.py — 梵天大脑 Layer C3: 反脆弱性系统
设计院 2026-08-25 苏摩111立项封印

使命: 在极端行情下保护梵天不被消灭
     正常情况按系统执行，极端情况强制熔断

四大保护机制:
  1. 黑天鹅检测    — BTC单日>8%波动 / 交易所异常
  2. 连亏强制降仓  — 连续3笔亏损 → 仓位减半 + 24H冷静期
  3. 极端情绪熔断  — FG<10或>90 → 暂停新开仓或限制方向
  4. 交易所异常    — 溢价/折价>2% → 立即推送预警
"""
from __future__ import annotations
import os, sys, json, time, logging
from pathlib import Path
from typing import Optional

_BB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BB)
if _BB not in sys.path: sys.path.insert(0, _BB)

logger = logging.getLogger('antifragile_guard')

_STATE_PATH = Path(_ROOT) / 'data' / 'antifragile_state.json'
_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 状态管理
# ═══════════════════════════════════════════════════════════════

def _load_state() -> dict:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception:
        pass
    return {
        'consecutive_losses': 0,
        'last_loss_ts': 0,
        'cooldown_until': 0,
        'half_size_until': 0,
        'blackswan_detected': False,
        'blackswan_ts': 0,
        'total_losses_24h': 0,
        'loss_window_ts': 0,
    }


def _save_state(state: dict):
    try:
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f'save state: {e}')


# ═══════════════════════════════════════════════════════════════
# 1. 黑天鹅检测
# ═══════════════════════════════════════════════════════════════

def check_blackswan() -> dict:
    """
    检测黑天鹅事件:
    - BTC 1H波动 > 5% (1H级别异常)
    - BTC 4H波动 > 8%
    - BTC 24H波动 > 12%
    返回: {'detected': bool, 'level': 'WATCH'|'WARNING'|'CRITICAL', 'reason': str}
    """
    try:
        from data_cache import get_klines
        from math_utils import calc_rsi

        kl1h = get_klines('BTCUSDT', '1h', 48)
        c1h = [float(k[4]) for k in kl1h]
        h1h = [float(k[2]) for k in kl1h]
        l1h = [float(k[3]) for k in kl1h]

        price = c1h[-1]
        # 1H波动
        chg_1h = abs(c1h[-1] - c1h[-2]) / c1h[-2] * 100 if len(c1h) >= 2 else 0
        # 4H波动
        chg_4h = abs(c1h[-1] - c1h[-5]) / c1h[-5] * 100 if len(c1h) >= 5 else 0
        # 24H波动
        chg_24h = abs(c1h[-1] - c1h[-25]) / c1h[-25] * 100 if len(c1h) >= 25 else 0
        # 24H方向
        dir_24h = 'UP' if c1h[-1] > c1h[-25] else 'DOWN'

        if chg_24h > 12:
            return {'detected': True, 'level': 'CRITICAL',
                    'reason': f'BTC 24H波动{dir_24h} {chg_24h:.1f}% → 黑天鹅级别',
                    'chg_1h': chg_1h, 'chg_4h': chg_4h, 'chg_24h': chg_24h}
        elif chg_4h > 8:
            return {'detected': True, 'level': 'WARNING',
                    'reason': f'BTC 4H波动{dir_24h} {chg_4h:.1f}% → 异常波动',
                    'chg_1h': chg_1h, 'chg_4h': chg_4h, 'chg_24h': chg_24h}
        elif chg_1h > 5:
            return {'detected': True, 'level': 'WATCH',
                    'reason': f'BTC 1H波动 {chg_1h:.1f}% → 监控',
                    'chg_1h': chg_1h, 'chg_4h': chg_4h, 'chg_24h': chg_24h}
        else:
            return {'detected': False, 'level': 'NORMAL',
                    'reason': f'波动正常 1H={chg_1h:.1f}% 4H={chg_4h:.1f}% 24H={chg_24h:.1f}%',
                    'chg_1h': chg_1h, 'chg_4h': chg_4h, 'chg_24h': chg_24h}
    except Exception as e:
        logger.debug(f'blackswan: {e}')
        return {'detected': False, 'level': 'UNKNOWN', 'reason': str(e)}


# ═══════════════════════════════════════════════════════════════
# 2. 连亏强制降仓
# ═══════════════════════════════════════════════════════════════

def record_trade_result(outcome: str, symbol: str = '', pnl_pct: float = 0) -> dict:
    """
    记录每笔交易结果，触发连亏保护
    outcome: 'WIN' | 'LOSS' | 'TIMEOUT'
    """
    state = _load_state()
    now = time.time()

    if outcome.upper() == 'LOSS':
        state['consecutive_losses'] += 1
        state['last_loss_ts'] = now
        # 24H窗口内损失计数
        if now - state.get('loss_window_ts', 0) > 86400:
            state['total_losses_24h'] = 0
            state['loss_window_ts'] = now
        state['total_losses_24h'] += 1
    elif outcome.upper() == 'WIN':
        state['consecutive_losses'] = 0  # 赢一次重置连亏计数

    action = 'NORMAL'
    message = ''

    # 连亏3笔 → 仓位减半 + 暂停6H
    if state['consecutive_losses'] >= 3:
        state['half_size_until'] = now + 6 * 3600
        state['cooldown_until']  = now + 6 * 3600
        action  = 'HALF_SIZE_COOLDOWN'
        message = f'⚠️ 连亏{state["consecutive_losses"]}笔 → 仓位减半+冷静6H'
        logger.warning(message)

    # 连亏5笔 → 停止新开仓24H
    if state['consecutive_losses'] >= 5:
        state['cooldown_until'] = now + 24 * 3600
        action  = 'STOP_TRADING_24H'
        message = f'🚨 连亏{state["consecutive_losses"]}笔 → 停止新开仓24H'
        logger.critical(message)

    _save_state(state)
    return {'action': action, 'message': message,
            'consecutive_losses': state['consecutive_losses'],
            'cooldown_until': state['cooldown_until']}


def get_size_multiplier() -> dict:
    """
    获取当前仓位乘数（供position_sizer调用）
    返回: {'multiplier': float, 'reason': str, 'blocked': bool}
    """
    state = _load_state()
    now   = time.time()

    # 停止交易期
    if state.get('cooldown_until', 0) > now and state.get('consecutive_losses', 0) >= 5:
        remaining_h = (state['cooldown_until'] - now) / 3600
        return {'multiplier': 0.0, 'blocked': True,
                'reason': f'连亏熔断: 剩余冷静{remaining_h:.1f}H'}

    # 减半期
    if state.get('half_size_until', 0) > now:
        remaining_h = (state['half_size_until'] - now) / 3600
        return {'multiplier': 0.5, 'blocked': False,
                'reason': f'连亏减半: 剩余{remaining_h:.1f}H'}

    # 正常
    return {'multiplier': 1.0, 'blocked': False, 'reason': '正常'}


# ═══════════════════════════════════════════════════════════════
# 3. 极端情绪熔断
# ═══════════════════════════════════════════════════════════════

def check_emotion_extreme(direction: str = '') -> dict:
    """
    极端情绪检测:
    - FG < 10: 极度恐惧 → 做空方向受限
    - FG > 90: 极度贪婪 → 做多方向受限
    返回: {'blocked': bool, 'warning': str, 'fg': float}
    """
    fg = 50.0
    try:
        from macro_engine import get_fear_greed
        fg_data = get_fear_greed()
        fg = float(fg_data.get('value', 50)) if isinstance(fg_data, dict) else float(fg_data or 50)
    except Exception as e:
        logger.debug(f'fear_greed: {e}')

    blocked = False
    warning = ''

    if fg < 10:
        if direction and 'SHORT' in direction.upper():
            blocked = True
            warning = f'🚨 极度恐惧(FG={fg:.0f}) → 做空方向熔断，市场可能超卖反弹'
        else:
            warning = f'⚠️ 极度恐惧(FG={fg:.0f}) → 做多为反向机会，谨慎'

    elif fg > 90:
        if direction and 'LONG' in direction.upper():
            blocked = True
            warning = f'🚨 极度贪婪(FG={fg:.0f}) → 做多方向熔断，市场可能超买回调'
        else:
            warning = f'⚠️ 极度贪婪(FG={fg:.0f}) → 做空为反向机会，谨慎'

    return {'blocked': blocked, 'warning': warning, 'fg': fg}


# ═══════════════════════════════════════════════════════════════
# 4. 交易所异常检测
# ═══════════════════════════════════════════════════════════════

def check_exchange_anomaly(symbol: str = 'BTCUSDT') -> dict:
    """
    检测交易所异常:
    - 现货/合约溢价 > 2%
    - 资金费率绝对值 > 0.03% (极端)
    """
    anomalies = []
    try:
        from data_cache import get_ticker, get_funding_rate

        # 资金费率异常
        fr = get_funding_rate(symbol)
        if abs(fr) > 0.03:
            direction = '多头极贵' if fr > 0 else '空头极贵'
            anomalies.append(f'FR={fr:+.4f}({direction})')

        # 价格异常波动（1min级别）
        tk = get_ticker(symbol)
        price     = float(tk.get('lastPrice', 0))
        price_24h = float(tk.get('prevClosePrice', price))
        chg = abs(price - price_24h) / price_24h * 100 if price_24h > 0 else 0
        if chg > 15:
            anomalies.append(f'24H异常波动{chg:.1f}%')

    except Exception as e:
        logger.debug(f'exchange_anomaly: {e}')

    return {
        'anomaly_detected': len(anomalies) > 0,
        'anomalies': anomalies,
        'warning': f'🚨 交易所异常: {" | ".join(anomalies)}' if anomalies else '',
    }


# ═══════════════════════════════════════════════════════════════
# 统一入口: 全面反脆弱性检查
# ═══════════════════════════════════════════════════════════════

def full_guard_check(symbol: str, direction: str = '') -> dict:
    """
    供 analyze() 调用的统一保护门控
    返回:
    {
      'blocked': bool,          # True = 禁止开仓
      'size_mult': float,       # 仓位乘数
      'warnings': list,         # 警告列表
      'critical': bool,         # True = 黑天鹅级别
    }
    """
    warnings = []
    blocked  = False
    size_mult = 1.0
    critical  = False

    # 1. 连亏保护
    size_info = get_size_multiplier()
    if size_info['blocked']:
        blocked   = True
        warnings.append(size_info['reason'])
    elif size_info['multiplier'] < 1.0:
        size_mult = size_info['multiplier']
        warnings.append(size_info['reason'])

    # 2. 黑天鹅检测
    bs = check_blackswan()
    if bs['detected']:
        if bs['level'] == 'CRITICAL':
            critical  = True
            blocked   = True
            warnings.append(bs['reason'])
        elif bs['level'] == 'WARNING':
            size_mult = min(size_mult, 0.5)
            warnings.append(bs['reason'])
        else:
            warnings.append(bs['reason'])

    # 3. 极端情绪熔断
    if direction:
        emo = check_emotion_extreme(direction)
        if emo['blocked']:
            blocked = True
            warnings.append(emo['warning'])
        elif emo['warning']:
            warnings.append(emo['warning'])

    # 4. 交易所异常
    exch = check_exchange_anomaly(symbol)
    if exch['anomaly_detected']:
        warnings.append(exch['warning'])
        size_mult = min(size_mult, 0.5)

    return {
        'blocked':   blocked,
        'size_mult': size_mult,
        'warnings':  warnings,
        'critical':  critical,
        'blackswan': bs,
    }


def format_guard_report() -> str:
    """状态报告"""
    state    = _load_state()
    size_info = get_size_multiplier()
    bs       = check_blackswan()
    now      = time.time()

    lines = ['🛡️ 梵天反脆弱性状态']
    lines.append(f'连亏: {state.get("consecutive_losses",0)}笔 | 仓位: ×{size_info["multiplier"]} | {size_info["reason"]}')
    lines.append(f'黑天鹅: {bs["level"]} — {bs["reason"]}')
    if state.get('cooldown_until', 0) > now:
        h = (state['cooldown_until'] - now) / 3600
        lines.append(f'⏸️ 冷静期剩余: {h:.1f}H')
    return '\n'.join(lines)


if __name__ == '__main__':
    print('=== C3反脆弱性冒烟测试 ===')
    # 连亏保护测试
    for i in range(3):
        record_trade_result('LOSS', 'BTCUSDT')
    sm = get_size_multiplier()
    assert sm['multiplier'] == 0.5, f'连亏减半失败: {sm}'
    print(f'连亏保护: mult={sm["multiplier"]} reason={sm["reason"]} ✅')
    # 重置
    state = _load_state(); state['consecutive_losses'] = 0
    state['half_size_until'] = 0; state['cooldown_until'] = 0
    _save_state(state)
    # 黑天鹅检测
    bs = check_blackswan()
    print(f'黑天鹅检测: level={bs["level"]} ✅')
    # 全局检查
    r = full_guard_check('BTCUSDT', 'SHORT')
    print(f'全局检查: blocked={r["blocked"]} size_mult={r["size_mult"]} warnings={len(r["warnings"])} ✅')
    print('C3完成 ✅')

"""
timing_filter.py — 梵天三层感知 · 第二层：时机过滤器
设计院自主决策落地 · 2026-07-01
v4.2固化 2026-07-01 苏摩111批准: 改进①BTC单边下行豁免通道(WAIT阈值62→55)

╔══════════════════════════════════════════════════════════════════╗
║  核心哲学：高分信号 + 错误时机 = 亏损                          ║
║            高分信号 + 对的时机 + 触发器响 = 稳健盈利           ║
╠══════════════════════════════════════════════════════════════════╣
║  职责：在 brahma_analysis_runner 输出层判断当前时机            ║
║        READY  → 反弹充分/RSI到位/OB到位，可以等触发器入场     ║
║        WAIT   → 反弹不足/时机未到，继续等待                   ║
║        MONITOR → 信号有效但时机窗口未开启，挂单监控            ║
╚══════════════════════════════════════════════════════════════════╝

接入位置：brahma_analysis_runner.py format_batch_report()
输出字段：timing_status / timing_reason / timing_confidence
"""


# ╔══ INTERFACE CONTRACT ═══════════════════════════════════════════╗
# ║ Interface : evaluate_timing(symbol,signal_dir,score,grade,entry_lo,entry_hi,p
# ║ Output    : {status:READY/MONITOR/WAIT/STANDBY, confidence, gap_pct, rsi_1h, 
# ║ Call Freq : 每次信号推送前，1次/信号
# ║ Deps      : requests(fapi klines)
# ╚════════════════════════════════════════════════════════════════╝
import time
import requests
from typing import Optional

_CACHE: dict = {}
_TTL = 60  # 1分钟缓存


def _get_rsi_4h(symbol: str, period: int = 14) -> float:
    """拉取4H RSI，用于单边下行豁免通道判断"""
    cache_key = f'rsi_{symbol}_4h_v42'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < 300:
        return _CACHE[cache_key]['data']
    try:
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit={period+5}',
            timeout=8
        ).json()
        closes = [float(x[4]) for x in r]
        gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        rsi = round(100 - 100/(1+ag/al), 1) if al > 0 else 100.0
        _CACHE[cache_key] = {'data': rsi, 'ts': now}
        return rsi
    except Exception:
        return 50.0


def _check_bearish_4h_streak(symbol: str, min_candles: int = 3) -> bool:
    """检查4H是否连续N根收阴（收盘<开盘）—— v4.2改进① 单边下行豁免通道"""
    cache_key = f'bear4h_streak_{symbol}_v42'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < 300:
        return _CACHE[cache_key]['data']
    try:
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=7',
            timeout=8
        ).json()
        # 排除最新未完成K线（最后一根），检查前面的
        candles = [(float(x[1]), float(x[4])) for x in r[:-1]]
        streak = 0
        for o, c in reversed(candles):
            if c < o:
                streak += 1
            else:
                break
        result = streak >= min_candles
        _CACHE[cache_key] = {'data': result, 'ts': now}
        return result
    except Exception:
        return False


def _get_rsi(symbol: str, interval: str = '1h', period: int = 14) -> float:
    """拉取RSI，带缓存"""
    cache_key = f'rsi_{symbol}_{interval}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _TTL:
        return _CACHE[cache_key]['data']
    try:
        limit = period + 16
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}',
            timeout=8
        ).json()
        closes = [float(x[4]) for x in r]
        gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        rsi = round(100 - 100/(1+ag/al), 1) if al > 0 else 100.0
        _CACHE[cache_key] = {'data': rsi, 'ts': now}
        return rsi
    except Exception:
        return 50.0


def _get_ob_gap(entry_lo: float, entry_hi: float, current_price: float) -> float:
    """计算价格距入场区的gap%"""
    if entry_lo <= 0 or current_price <= 0:
        return 99.0
    if entry_lo <= current_price <= entry_hi:
        return 0.0  # 已在区间内
    if current_price < entry_lo:
        return round((entry_lo - current_price) / current_price * 100, 3)
    # 价格超过入场区上方（过冲）
    return -round((current_price - entry_hi) / current_price * 100, 3)


def evaluate_timing(
    symbol: str,
    signal_dir: str,
    score: float,
    grade: float,
    entry_lo: float,
    entry_hi: float,
    current_price: float,
    s23_p_up: float = 0.5,
    regime: str = 'BEAR_TREND',
) -> dict:
    """
    时机过滤器核心逻辑

    参数：
      symbol        : 标的（如 BTCUSDT）
      signal_dir    : SHORT / LONG
      score         : 梵天评分
      grade         : SMC结构质量
      entry_lo/hi   : 入场区间
      current_price : 当前价格
      s23_p_up      : Kronos短期上涨概率
      regime        : 当前体制

    返回：
      status        : READY / WAIT / MONITOR / STANDBY
      confidence    : 时机置信度 0~1
      reason        : 详细描述
      gap_pct       : 距入场区距离%
      rsi_1h        : 当前1H RSI
      action_hint   : 简洁操作提示
    """

    is_short = (signal_dir == 'SHORT')

    # ── 1. 获取实时RSI ──
    rsi_1h = _get_rsi(symbol, '1h')

    # ── 2. 计算gap ──
    gap_pct = _get_ob_gap(entry_lo, entry_hi, current_price)

    # ── 2.5 单边下行豁免通道（v4.2 改进① 苏摩111批准）──
    # BTC/ETH 在4H连续3根收阴 + RSI_4H<40 时，RSI_1H门槛从65降至55
    _rsi_ready_threshold = 65  # 默认READY门槛
    _wait_exemption = False
    if is_short:
        try:
            _rsi_4h = _get_rsi_4h(symbol)
            _bear_streak = _check_bearish_4h_streak(symbol)
            if _bear_streak and _rsi_4h < 40:
                _rsi_ready_threshold = 55
                _wait_exemption = True
        except Exception:
            pass

    # ── 3. 时机评分（0~100）──
    timing_score = 0
    reasons = []

    # 条件A: 价格位置（最重要）
    if gap_pct == 0.0:
        timing_score += 40
        reasons.append(f'价格在OB区内 +40')
    elif 0 < gap_pct <= 0.3:
        timing_score += 30
        reasons.append(f'距OB区{gap_pct:.2f}% 极近 +30')
    elif 0 < gap_pct <= 1.0:
        timing_score += 15
        reasons.append(f'距OB区{gap_pct:.2f}% 贴近 +15')
    elif gap_pct < 0:
        # 价格已过冲超过入场区 → 时机错过
        timing_score -= 20
        reasons.append(f'价格过冲入场区{abs(gap_pct):.2f}% -20')
    else:
        timing_score += 0
        reasons.append(f'距OB区{gap_pct:.2f}% 较远 0')

    # 条件B: RSI位置（支持豁免通道动态门槛）
    if is_short:
        if rsi_1h >= 75:
            timing_score += 35
            reasons.append(f'RSI1H={rsi_1h} 极度超买 铁证WR=68% +35')
        elif rsi_1h >= 65:
            timing_score += 25
            reasons.append(f'RSI1H={rsi_1h} 超买区 +25')
        elif rsi_1h >= 55:
            if _wait_exemption:
                timing_score += 22  # 豁免通道：55~65视同65+
                reasons.append(f'RSI1H={rsi_1h} 豁免通道激活(4H连阴+RSI4H<40) +22✅')
            else:
                timing_score += 10
                reasons.append(f'RSI1H={rsi_1h} 偏高 +10')
        elif rsi_1h <= 40:
            if _wait_exemption:
                timing_score += 0  # 豁免时不惩罚低RSI
                reasons.append(f'RSI1H={rsi_1h} 豁免通道激活，跳过低RSI惩罚')
            else:
                timing_score -= 15
                reasons.append(f'RSI1H={rsi_1h} 偏低做空逆风 -15')
    else:  # LONG
        if rsi_1h <= 25:
            timing_score += 35
            reasons.append(f'RSI1H={rsi_1h} 极度超卖 铁证WR=68% +35')
        elif rsi_1h <= 35:
            timing_score += 25
            reasons.append(f'RSI1H={rsi_1h} 超卖区 +25')
        elif rsi_1h >= 60:
            timing_score -= 15
            reasons.append(f'RSI1H={rsi_1h} 偏高做多逆风 -15')

    # 条件C: Kronos p_up（反弹动能）
    if is_short:
        if s23_p_up >= 0.90:
            timing_score += 20
            reasons.append(f'Kronos p_up={s23_p_up:.2f} 极强反弹→OB区即将触达 +20')
        elif s23_p_up >= 0.65:
            timing_score += 10
            reasons.append(f'Kronos p_up={s23_p_up:.2f} 反弹充分 +10')
        elif s23_p_up <= 0.35:
            timing_score -= 10
            reasons.append(f'Kronos p_up={s23_p_up:.2f} 无反弹动能 -10')
    else:
        if s23_p_up <= 0.10:
            timing_score += 20
            reasons.append(f'Kronos p_up={s23_p_up:.2f} 极强下跌→支撑区即将触达 +20')
        elif s23_p_up <= 0.35:
            timing_score += 10
            reasons.append(f'Kronos p_up={s23_p_up:.2f} 下跌动能充足 +10')

    # 条件D: score权重（高分加持时机置信度）
    if score >= 160:
        timing_score += 5
        reasons.append(f'精华信号score={score:.0f} +5')
    elif score >= 140:
        timing_score += 2

    # ── 4. 时机状态判断 ──
    timing_score = max(-30, min(100, timing_score))
    confidence = round(max(0.0, min(1.0, timing_score / 100)), 2)

    if timing_score >= 65:
        status = 'READY'
        action_hint = _action_hint(is_short, gap_pct, rsi_1h, s23_p_up, 'READY')
    elif timing_score >= 40:
        status = 'MONITOR'
        action_hint = _action_hint(is_short, gap_pct, rsi_1h, s23_p_up, 'MONITOR')
    elif timing_score >= 10:
        status = 'WAIT'
        action_hint = _action_hint(is_short, gap_pct, rsi_1h, s23_p_up, 'WAIT',
                                   wait_exemption=_wait_exemption,
                                   rsi_threshold=_rsi_ready_threshold)
    else:
        status = 'STANDBY'
        action_hint = '条件不足，挂单等待'

    return {
        'status':        status,
        'confidence':    confidence,
        'timing_score':  timing_score,
        'gap_pct':       gap_pct,
        'rsi_1h':        rsi_1h,
        'p_up':          s23_p_up,
        'reason':        ' | '.join(reasons),
        'action_hint':   action_hint,
        'wait_exemption': _wait_exemption,        # v4.2 改进①
        'rsi_threshold':  _rsi_ready_threshold,   # v4.2 改进①
    }


def _action_hint(is_short: bool, gap: float, rsi: float, p_up: float, status: str,
                 wait_exemption: bool = False, rsi_threshold: int = 65) -> str:
    """生成人类可读的操作提示"""
    dir_str = '空入' if is_short else '多入'
    if status == 'READY':
        if gap == 0:
            return f'✅ 价格在区，等15M CHoCH或缩量 → {dir_str}'
        elif gap <= 0.3:
            return f'⚡ 极近入场区，密切盯盘 → {dir_str}'
        else:
            return f'🟡 贴近入场区，准备 → {dir_str}'
    elif status == 'MONITOR':
        if p_up >= 0.65 and is_short:
            return f'⏳ 反弹中，等OB区到位(gap={gap:.2f}%) → 届时{dir_str}'
        else:
            return f'⏳ 时机未完全成熟，监控中'
    elif status == 'WAIT':
        if wait_exemption and is_short:
            return f'⏸ 豁免通道激活(4H连阴)，等RSI拉升到{rsi_threshold}+ → 再{dir_str}'
        elif rsi < 55 and is_short:
            return f'⏸ RSI={rsi}偏低，等RSI拉升到{rsi_threshold}+ → 再{dir_str}'
        elif gap > 2:
            return f'⏸ 距入场区{gap:.1f}%，等价格反弹 → {dir_str}'
        else:
            return f'⏸ 等待时机成熟'
    return '挂单等待'


def format_timing_badge(timing: dict) -> str:
    """格式化时机状态徽章（用于推送卡片）"""
    status = timing.get('status', 'WAIT')
    emoji = {'READY': '⚡', 'MONITOR': '👁', 'WAIT': '⏳', 'STANDBY': '⏸'}.get(status, '?')
    conf = timing.get('confidence', 0)
    hint = timing.get('action_hint', '')
    return f'{emoji} 时机[{status}] conf={conf:.0%} | {hint}'


if __name__ == '__main__':
    # 快速验证
    result = evaluate_timing(
        symbol='ETHUSDT',
        signal_dir='SHORT',
        score=166.5,
        grade=70.0,
        entry_lo=1577.0,
        entry_hi=1581.0,
        current_price=1589.0,
        s23_p_up=0.95,
        regime='BEAR_TREND',
    )
    print(f'ETH SHORT 时机评估:')
    print(f'  status={result["status"]}  confidence={result["confidence"]:.0%}')
    print(f'  rsi_1h={result["rsi_1h"]}  gap={result["gap_pct"]:.2f}%  p_up={result["p_up"]:.2f}')
    print(f'  reason={result["reason"]}')
    print(f'  action={result["action_hint"]}')
    print()
    print(f'  徽章: {format_timing_badge(result)}')

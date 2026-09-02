# ⚠️ Brahma-Quant Open Source v3.0
# PRO私有内容: 5-regime分类器阈值（实盘精调值，Pro私有）
# 开源版：框架公开，参数需自行调参或获取Pro版

#!/usr/bin/env python3
# ponytail: regime_scorer 414行，核心计算，35维共享_result状态，拆分条件: 状态隔离方案成熟后
"""
regime_scorer.py — 梵天三层体制概率评估 v1.0
设计院 2026-06-10

【大道至简】
  输入：symbol
  输出：bull_prob / bear_prob / chop_prob（三者之和=1.0）
         + phase（4H阶段）+ momentum（1H动量）+ 置信度

【三层体制】
  第一层 日线主趋势：RSI + 价格结构 + EMA斜率
  第二层 4H阶段：高低点序列 + EMA位置
  第三层 1H动量：连续K线方向 + RSI动量

【设计原则】
  - 独立于brahma_core，零依赖
  - 结果缓存30分钟（同symbol同interval不重复调用）
  - 只用公开可靠的指标，不用复杂模型
  - 输出概率而非分数（更直观，便于体制权重计算）
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库
# [P0修复 2026-07-12] _ema返回list，直接与float运算会TypeError；改用取末值的ema()封装
def _ema_scalar(series, period): return ema(series, period)  # 返回float

import json
import time
import urllib.request
from pathlib import Path

FAPI   = 'https://fapi.binance.com'
_CACHE = {}          # {symbol: {ts, result}} | 缓存结构：标的 → {时间戳, 结果}
_TTL   = 600         # [P0修复 2026-08-03] 10分钟缓存（原30分钟→缓存过长导致反弹时仍用熊市RSI）


# ══════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════

def _klines(symbol: str, interval: str, limit: int = 100) -> list:  # [FIX 2026-06-14] 30→100 保证Wilder RSI初始化稳定
    # [P0修复 2026-08-03 苏摩111] limit+1拉取，去除最后一根未收盘K线
    # 根因：未收盘K线的收盘价是当前实时价，会导致RSI虚高/虚低
    url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit+1}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=6, context=_DC_SSL_CTX) as r:
        raw = json.loads(r.read())
        raw = raw[:-1]  # 去除最后一根未收盘K线，确保无穿越
        return [{'o': float(k[1]), 'h': float(k[2]), 'l': float(k[3]),
                 'c': float(k[4]), 'v': float(k[5])} for k in raw]


# _rsi 已由 math_utils import（见文件顶部），本地重复定义已删除 [2026-08-24 设计院精简]


def _higher_highs(klines: list, n: int = 5) -> bool:
    highs = [k['h'] for k in klines[-n:]]
    return highs[-1] > highs[0] and highs[-1] > highs[-2]


def _lower_lows(klines: list, n: int = 5) -> bool:
    lows = [k['l'] for k in klines[-n:]]
    return lows[-1] < lows[0] and lows[-1] < lows[-2]


# ══════════════════════════════════════════════════════════════
# 第一层：日线主趋势
# ══════════════════════════════════════════════════════════════

def _score_daily(kd: list) -> dict:
    """
    返回 bull_pts / bear_pts，各维度得分
    """
    bull, bear = 0, 0
    closes = [k['c'] for k in kd]
    price  = closes[-1]

    # RSI | RSI指标评分
    rsi_1d = rsi(closes)  # [Fix 2026-09-01] _rsi()返回list，必须用rsi()取标量
    if rsi_1d > 60:
        bull += 25
    elif rsi_1d > 50:
        bull += 10
    elif rsi_1d < 30:
        bear += 25
    elif rsi_1d < 40:
        bear += 10

    # 价格 vs EMA50
    ema50 = _ema_scalar(closes, 50)  # [P0修复] 使用scalar版本避免list×float TypeError
    if price > ema50 * 1.01:
        bull += 20
    elif price > ema50:
        bull += 8
    elif price < ema50 * 0.99:
        bear += 20
    else:
        bear += 8

    # 价格 vs EMA20
    ema20 = _ema_scalar(closes, 20)  # [P0修复]
    if price > ema20:
        bull += 15
    else:
        bear += 15

    # 高低点结构（最近10根日线）
    if _higher_highs(kd, 8):
        bull += 20
    if _lower_lows(kd, 8):
        bear += 20

    # 动量：最近3根日线方向
    last3 = kd[-3:]
    bull_candles = sum(1 for k in last3 if k['c'] > k['o'])
    bear_candles = sum(1 for k in last3 if k['c'] < k['o'])
    if bull_candles >= 2:
        bull += 10
    if bear_candles >= 2:
        bear += 10

    return {'bull': bull, 'bear': bear, 'rsi_1d': rsi_1d, 'ema20': ema20, 'ema50': ema50}


# ══════════════════════════════════════════════════════════════
# 第二层：4H阶段
# ══════════════════════════════════════════════════════════════

def _score_4h(k4: list) -> dict:
    """
    输出：phase + bull_pts + bear_pts
    phase: DOWNTREND(下跌趋势) / UPTREND(上涨趋势) / PULLBACK_UP(上升途中回调) / PULLBACK_DN(下降途中反弹) / BOTTOMING(筑底) / TOPPING(顶部) / CHOP(震荡)
    """
    bull, bear = 0, 0
    closes = [k['c'] for k in k4]
    price  = closes[-1]

    rsi_4h = rsi(closes)  # [Fix 2026-09-01] 取标量
    ema20  = _ema_scalar(closes, 20)  # [P0修复] scalar
    ema9   = _ema_scalar(closes, 9)   # [P0修复] scalar

    # EMA位置
    above_ema20 = price > ema20
    above_ema9  = price > ema9

    # 结构
    hh = _higher_highs(k4, 6)
    ll = _lower_lows(k4, 6)

    # 确定4H阶段
    if ll and rsi_4h < 45 and not above_ema20:
        phase = 'DOWNTREND'     # 下跌趋势：低点持续走低 + RSI弱 + 价格在EMA20下方
        bear += 35
    elif hh and rsi_4h > 55 and above_ema20:
        phase = 'UPTREND'       # 上涨趋势：高点持续走高 + RSI强 + 价格在EMA20上方
        bull += 35
    elif not ll and rsi_4h > 40 and rsi_4h < 60 and above_ema20:
        phase = 'PULLBACK_UP'   # 上升途中回调：EMA20支撑，主趋势仍向上
        bull += 20
        bear += 10
    elif not hh and rsi_4h > 40 and rsi_4h < 60 and not above_ema20:
        phase = 'PULLBACK_DN'   # 下降途中反弹：EMA20压制，主趋势仍向下
        bull += 10
        bear += 20
    elif rsi_4h < 35 and not ll:
        phase = 'BOTTOMING'     # 筑底：RSI超卖但低点未继续走低，可能反转
        bull += 25
        bear += 5
    elif rsi_4h > 70 and not hh:
        phase = 'TOPPING'       # 顶部：RSI超买但高点未继续走高，可能回落
        bull += 5
        bear += 25
    else:
        phase = 'CHOP'          # 震荡：方向不明，多空均衡
        bull += 5
        bear += 5

    # RSI附加分
    if rsi_4h > 60: bull += 10
    elif rsi_4h < 40: bear += 10

    # EMA附加
    if above_ema9 and above_ema20: bull += 10
    elif not above_ema9 and not above_ema20: bear += 10

    return {'bull': bull, 'bear': bear, 'phase': phase,
            'rsi_4h': rsi_4h, 'ema20_4h': ema20}


# ══════════════════════════════════════════════════════════════
# 第三层：1H动量
# ══════════════════════════════════════════════════════════════

def _score_1h(k1: list) -> dict:
    bull, bear = 0, 0
    closes = [k['c'] for k in k1]
    price  = closes[-1]

    rsi_1h = rsi(closes)  # [Fix 2026-09-01] 取标量
    ema20  = _ema_scalar(closes, 20)  # [P0修复] scalar

    # RSI | RSI指标评分
    if rsi_1h > 55:   bull += 20
    elif rsi_1h > 50: bull += 8
    elif rsi_1h < 45: bear += 20
    elif rsi_1h < 50: bear += 8

    # 连续K线
    last4 = k1[-4:]
    bull_c = sum(1 for k in last4 if k['c'] > k['o'])
    bear_c = sum(1 for k in last4 if k['c'] < k['o'])
    if bull_c >= 3:   bull += 20
    elif bull_c >= 2: bull += 8
    if bear_c >= 3:   bear += 20
    elif bear_c >= 2: bear += 8

    # Higher High / Higher Low | 高点抬升/低点抬升结构判断
    highs = [k['h'] for k in k1[-6:]]
    lows  = [k['l'] for k in k1[-6:]]
    hh_1h = highs[-1] > highs[-3]
    hl_1h = lows[-1] > lows[-3]
    ll_1h = lows[-1] < lows[-3]
    lh_1h = highs[-1] < highs[-3]

    if hh_1h and hl_1h: bull += 15
    if ll_1h and lh_1h: bear += 15

    # EMA位置
    if price > ema20: bull += 10
    else:             bear += 10

    momentum = 'BULLISH' if bull > bear + 10 else ('BEARISH' if bear > bull + 10 else 'NEUTRAL')  # BULLISH=偏多 / BEARISH=偏空 / NEUTRAL=中性

    return {'bull': bull, 'bear': bear, 'momentum': momentum,
            'rsi_1h': rsi_1h, 'hh': hh_1h, 'hl': hl_1h}


# ══════════════════════════════════════════════════════════════
# 主入口：三层综合
# ══════════════════════════════════════════════════════════════

def score(symbol: str, force: bool = False, vol_ratio: float = None) -> dict:
    """
    三层体制概率评估

    返回：
      bull_prob   : float 0.0~1.0
      bear_prob   : float 0.0~1.0
      chop_prob   : float 0.0~1.0
      primary     : 'BULL'|'BEAR'|'CHOP'
      confidence  : float 0.0~1.0（最高概率 vs 第二高概率的差距）
      phase       : 4H阶段
      momentum    : 1H动量方向
      rsi_1d/4h/1h: RSI参考
      multiplier  : dict SHORT/LONG 体制乘数（0.5~1.5）
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    # 缓存检查
    now = time.time()
    if not force and sym in _CACHE and now - _CACHE[sym]['ts'] < _TTL:
        return _CACHE[sym]['result']

    # 拉数据
    kd = _klines(sym, '1d', 60)
    k4 = _klines(sym, '4h', 100)  # [FIX 2026-06-14] Wilder RSI需要足够K线
    k1 = _klines(sym, '1h', 100)  # [FIX 2026-06-14] Wilder RSI需要足够K线

    d = _score_daily(kd)
    h = _score_4h(k4)
    m = _score_1h(k1)

    # 三层加权合并（日线权重最高）
    # 权重：日线40% / 4H35% / 1H25%
    bull_raw = d['bull'] * 0.40 + h['bull'] * 0.35 + m['bull'] * 0.25
    bear_raw = d['bear'] * 0.40 + h['bear'] * 0.35 + m['bear'] * 0.25

    # 震荡判断：多空势均力敌
    total    = bull_raw + bear_raw
    diff_pct = abs(bull_raw - bear_raw) / max(total, 1)
    chop_raw = max(0, 30 * (1 - diff_pct * 3))  # 差距越小，震荡分越高

    # 归一化为概率
    grand_total = bull_raw + bear_raw + chop_raw
    bull_prob = round(bull_raw / max(grand_total, 1), 3)
    bear_prob = round(bear_raw / max(grand_total, 1), 3)
    chop_prob = round(1 - bull_prob - bear_prob, 3)
    chop_prob = max(chop_prob, 0)

    # [v25.2 网格最优 2026-06-14] vol降权 v_looser
    # 依据：167,200组合全搜，v_looser Top20 100%占位
    # 阈值：vl=0.20（原0.30），vvl=0.05（原0.10），收敛比20%/50%（原30%/60%）
    if vol_ratio is not None and vol_ratio > 0:
        if vol_ratio < 0.05:
            bull_prob = round(bull_prob * 0.50 + 0.333 * 0.50, 3)
            bear_prob = round(bear_prob * 0.50 + 0.333 * 0.50, 3)
        elif vol_ratio < 0.20:
            bull_prob = round(bull_prob * 0.80 + 0.333 * 0.20, 3)
            bear_prob = round(bear_prob * 0.80 + 0.333 * 0.20, 3)
        chop_prob = max(round(1 - bull_prob - bear_prob, 3), 0)

    # 主体制
    probs   = {'BULL': bull_prob, 'BEAR': bear_prob, 'CHOP': chop_prob}
    primary = max(probs, key=probs.get)
    sorted_probs = sorted(probs.values(), reverse=True)
    confidence = round(sorted_probs[0] - sorted_probs[1], 3)

    # 体制乘数（用于仓位/权重修正）
    # 顺势=1.5，中性=1.0，逆势=0.5
    def _mult(direction: str) -> float:
        # [大样本修正 2026-06-11]
        # 旧: ≥55%→1.5 / ≥40%→1.0 / else→0.5（熊市35-45%时SHORT永远×0.5，无法过门槛）
        # 新: ≥50%→1.5 / ≥33%→1.0 / 震荡→0.7 / 逆势→0.5
        # 依据: 大样本12万笔验证，BEAR_TREND(熊市趋势) SHORT WR=54%，不应被体制乘数封死
        if direction == 'LONG':  # 做多乘数
            # 铁证：熊市做多是宪法级死穴（225K+样本验证）
            # BEAR_EARLY_LONG WR=49.9% avgPnL=-0.139 / BEAR_TREND_LONG WR=45.6% avgPnL=-0.218
            if bear_prob >= 0.55: return 0.0    # 熊市初期/趋势→做多硬封禁，乘数归零
            if bull_prob >= 0.50: return 1.5    # 牛市趋势（BULL_TREND）→强顺势，满仓加速
            if bull_prob >= 0.40: return 1.2    # 牛市偏强→较强顺势
            if bull_prob >= 0.33: return 1.0    # 弱牛市→中性顺势
            if chop_prob >= 0.40: return 0.7    # 震荡区间→降权
            return 0.5                          # 逆势→减半
        else:  # SHORT 做空乘数
            if bear_prob >= 0.50: return 1.5    # 熊市趋势（BEAR_TREND）→强顺势，满仓加速
            if bear_prob >= 0.42: return 1.0    # 熊市初期（BEAR_EARLY）→中性顺势
            if chop_prob >= 0.40: return 0.7    # 震荡
            return 0.5                          # 逆势

    # 计算regime标签（统一字符串，与market_state对齐）
    # [v25.2 网格最优 2026-06-14] 全门槛更新，alpha体制占比 70%→92.1%
    # bear: hi=0.60(不变) / mid=0.55(↑+0.10) / lo=0.42(↑+0.09)
    # bull: hi=0.50(↓-0.10) / mid=0.38(↓-0.07)
    # 依据：167,200组合盲测，BTC+ETH 19000条采样，Top20 100%收敛
    if bear_prob >= 0.60:   _regime_label = 'BEAR_TREND'     # 熊市趋势：bear_prob≥60%，做空最佳体制 EV+0.182
    elif bear_prob >= 0.55: _regime_label = 'BEAR_EARLY'     # 熊市初期：bear_prob 55~60%，趋势形成中
    elif bear_prob >= 0.42: _regime_label = 'BEAR_RECOVERY'  # 熊市反弹：bear_prob 42~55%，做多反直觉alpha EV+0.255
    elif bull_prob >= 0.50: _regime_label = 'BULL_TREND'     # 牛市趋势：bull_prob≥50%，做多最佳体制 EV+0.242
    elif bull_prob >= 0.38: _regime_label = 'BULL_EARLY'     # 牛市初期：bull_prob 38~50%，趋势形成中
    elif chop_prob >= 0.40: _regime_label = 'CHOP_HIGH'      # 强震荡：方向极不明确，避免交易
    else:                   _regime_label = 'CHOP_MID'       # 弱震荡：多空均衡，方向待确认

    result = {
        'symbol':     sym,
        'regime':     _regime_label,   # [Fix-Regime-SSOT] 统一字段
        'bull_prob':  bull_prob,
        'bear_prob':  bear_prob,
        'chop_prob':  chop_prob,
        'primary':    primary,
        'confidence': confidence,
        'phase':      h['phase'],
        'momentum':   m['momentum'],
        'rsi_1d':     d['rsi_1d'],
        'rsi_4h':     h['rsi_4h'],
        'rsi_1h':     m['rsi_1h'],
        'hh_1h':      m['hh'],
        'hl_1h':      m['hl'],
        'multiplier': {
            'LONG':  _mult('LONG'),
            'SHORT': _mult('SHORT'),
        },
        'ts': now,
    }

    _CACHE[sym] = {'ts': now, 'result': result}
    return result


def format_regime(r: dict) -> str:
    bull_bar = '█' * int(r['bull_prob'] * 20)
    bear_bar = '█' * int(r['bear_prob'] * 20)
    chop_bar = '█' * int(r['chop_prob'] * 20)
    return (
        f"体制评估 {r['symbol']}\n"
        f"  🟢 牛市 {r['bull_prob']:.1%} {bull_bar}\n"
        f"  🔴 熊市 {r['bear_prob']:.1%} {bear_bar}\n"
        f"  🟡 震荡 {r['chop_prob']:.1%} {chop_bar}\n"
        f"  主体制={r['primary']} 置信={r['confidence']:.1%}\n"
        f"  4H阶段={r['phase']} 1H动量={r['momentum']}\n"
        f"  RSI 1D={r['rsi_1d']:.1f} 4H={r['rsi_4h']:.1f} 1H={r['rsi_1h']:.1f}\n"
        f"  乘数 LONG×{r['multiplier']['LONG']} SHORT×{r['multiplier']['SHORT']}"
    )


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
    r = score(sym, force=True)
    print(format_regime(r))


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/regime_state_machine.py ══
#!/usr/bin/env python3
# ponytail: regime_state_machine 362行，有意为之，重构前先 grep 所有调用方
"""

# STATUS: ACTIVE
# 体制状态机，转换逻辑
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
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
  from brahma_brain.regime_scorer import RegimeStateMachine
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
    ('CHOP_MID',      'BULL_TREND'):   3,  # [P1修复 2026-07-15] CHOP→BULL需草3次确认，避免震荡期过频切入牛市
    ('BULL_TREND',     'CHOP_MID'):    3,  # [P1修复 2026-07-15] BULL→CHOP需草3次确认，防止短暂回调误切震荡
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

# ── 防抖：最短更新间隔（秒）────────────────────────────────────
# 根因修复 [设计院 2026-07-05]：
# RegimeStateMachine.update() 每次 brahma_core 被调用都会执行。
# eth-alert(每3min x2)、rsi-watcher(每5min) 等高频任务导致 confirm_count
# 在 6~10 分钟内就累积到 DEFAULT_CONFIRM=2，远低于设计的4H节奏。
# 修复：同一 symbol 两次有效计数之间必须间隔 ≥ MIN_UPDATE_INTERVAL。
# 这样 CONFIRM_WINDOW=2 实际对应 2*30min=60min，近似1根4H的节奏。
MIN_UPDATE_INTERVAL = 60 * 60   # [P1修复 2026-07-15] 30min→60min：CHOP_MID切换防抖，减少震荡期误切

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
                state = data.get(self.symbol, self._default_state())
                # [防抖迁移] 旧版 state 没有 last_update_ts 字段，或 last_update_ts=0
                # 迁移策略：用 confirmed_at 作为基准，防止历史 symbols 立刻绕过门控
                if 'last_update_ts' not in state or state.get('last_update_ts', 0) == 0:
                    # confirmed_at 存在则用它，否则用当前时间（视为刚刚更新过）
                    state['last_update_ts'] = state.get('confirmed_at') or time.time()
                return state
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
            'last_update_ts': 0,              # [防抖] 上次有效计数更新时间戳
        }

    def _save_state(self):
        """持久化状态（始终同步confirmed_cn，防止历史遗留字段错位）"""
        try:
            # [BUG-1 fix 2026-07-08] 每次保存前强制同步confirmed_cn
            self._state['confirmed_cn'] = REGIME_CN.get(
                self._state.get('confirmed', ''), self._state.get('confirmed', '')
            )
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

    def update(self, raw_regime: str, symbol: str = None, klines_4h: list = None) -> str:
        """
        输入原始单点体制，返回经过稳定处理的确认体制
        INT-2: 支持 HMM 概率化辅助（设计院六方联合 2026-07-11）
          当 symbol + klines_4h 传入时，调用 regime_hmm_v2 获取概率分布
          若 HMM top1 与 raw_regime 一致且概率>0.7 → confirm_count+1加速
          若 HMM top1 与 raw_regime 不同且概率>0.7 → raw_regime 降权
        """
        now = time.time()
        # ── INT-2: HMM 概率化辅助判断 ────────────────────────────
        _hmm_boost = 0  # 正值=加速确认, 负值=降权
        if symbol and klines_4h:
            try:
                from regime_hmm_v2 import predict_regime_proba
                _hmm = predict_regime_proba(symbol, klines_4h)
                _hmm_top = _hmm.get('top_regime', '')
                _hmm_prob = _hmm.get('top_prob', 0.0)
                if _hmm_top == raw_regime and _hmm_prob > 0.70:
                    _hmm_boost = 1   # HMM一致+高概率 → 加速confirm
                elif _hmm_top != raw_regime and _hmm_prob > 0.70:
                    _hmm_boost = -1  # HMM不同+高概率 → 降权，需更多确认
            except Exception:
                pass
        s = self._state
        confirmed = s['confirmed']

        # ── 情况1：体制没变，重置候选计数 ─────────────────────────
        if raw_regime == confirmed:
            s['candidate'] = None
            s['confirm_count'] = 0
            s['last_raw'] = raw_regime
            # 更新最后有效更新时间（体制不变也算）
            s['last_update_ts'] = now
            self._save_state()
            return confirmed

        # ── 防抖门控：距上次有效计数更新 < MIN_UPDATE_INTERVAL，直接返回 ──
        # [设计院 2026-07-05] 防止高频任务（eth-alert每3min, rsi-watcher每5min）
        # 在几分钟内快速堆积 confirm_count，导致74次/天的体制抖动
        last_update_ts = s.get('last_update_ts', 0)

        # [设计院 2026-07-06] sw24h 每日重置修复
        # 原bug: switch_count_24h 从未重置，历史累积（BTC=77,ETH=43）
        # 修复: 检查上次重置时间，超过24H则清零
        _last_sw_reset = s.get('last_sw_reset', 0)
        if now - _last_sw_reset >= 86400:
            s['switch_count_24h'] = 0
            s['last_sw_reset'] = now

        if now - last_update_ts < MIN_UPDATE_INTERVAL:
            # 时间间隔不足，不允许计数更新，静默返回已确认体制
            # 注意：不重置 candidate/confirm_count，等待足够时间后继续
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
            s['candidate_start_ts'] = now  # [P2 2026-08-26] 记录候选开始时间，供延迟量化
        else:
            # 同一候选体制继续积累
            s['confirm_count'] = s.get('confirm_count', 0) + 1

        # 记录本次有效计数更新时间
        s['last_update_ts'] = now

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

            # [P2修复 2026-08-26] 体制切换延迟量化
            # 延迟 = 从第一根candidate根K线到确认切换的时间
            _confirm_start = s.get('candidate_start_ts', now)
            _latency_sec = now - _confirm_start
            _latency_4h = round(_latency_sec / (4 * 3600), 1)  # 转扦4H K线根数
            s['last_switch_latency_sec'] = round(_latency_sec)
            s['last_switch_latency_4h_bars'] = _latency_4h
            s['last_switch_from'] = old_regime
            s['last_switch_to'] = raw_regime
            # 积累历史延迟，为月度复盘提供数据
            _hist = s.get('switch_latency_history', [])
            _hist.append({'from': old_regime, 'to': raw_regime,
                          'latency_4h': _latency_4h, 'ts': now})
            s['switch_latency_history'] = _hist[-20:]  # 保留最近20条

            pass  # [静默]

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
            # [P2 2026-08-26] 切换延迟量化指标
            'last_switch_latency_4h': s.get('last_switch_latency_4h_bars'),
            'last_switch_from':       s.get('last_switch_from'),
            'last_switch_to':         s.get('last_switch_to'),
            'switch_latency_history': s.get('switch_latency_history', [])[-5:],
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

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/market_quadrant.py ══
#!/usr/bin/env python3
"""
market_quadrant.py — 梵天大脑 Layer B1: 四象限市场状态
设计院 2026-08-25 苏摩111立项封印

20年老手的第一判断框架:
         散户多头拥挤
              ↑
  大户做空 ←─────────── 大户做多
              ↓
         散户空头拥挤

四象限:
  Q1: 散户多 + 大户空 → 做空机会最高 ★★★
  Q2: 散户空 + 大户多 → 做多机会最高 ★★★
  Q3: 散户多 + 大户多 → 趋势持续做多 ★★
  Q4: 散户空 + 大户空 → 趋势持续做空 ★★

三阶段节奏识别 (Layer B2):
  ACCUMULATION  积累期: 横盘+量萎+OI升
  EXPANSION     爆发期: 放量突破+OI急升
  EXHAUSTION    衰竭期: 量价背离+OI顶+LSR极端
"""
import os, sys, time, logging
from typing import Optional
from data_cache import _SSL_CTX as _DC_SSL_CTX

_BB = os.path.dirname(os.path.abspath(__file__))
if _BB not in sys.path: sys.path.insert(0, _BB)

logger = logging.getLogger('market_quadrant')


# ═══════════════════════════════════════════════════════════════
# B1: 四象限判断
# ═══════════════════════════════════════════════════════════════

def get_quadrant(symbol: str) -> dict:
    """
    返回当前四象限状态 + 操作建议

    Returns:
        {
          'quadrant': 'Q1'|'Q2'|'Q3'|'Q4'|'NEUTRAL',
          'retail_bias': 'LONG'|'SHORT'|'NEUTRAL',  # 散户方向
          'whale_bias':  'LONG'|'SHORT'|'NEUTRAL',  # 大户方向
          'signal':      'SHORT'|'LONG'|'TREND_SHORT'|'TREND_LONG'|'NEUTRAL',
          'stars':       int,   # 1~3 机会等级
          'confidence':  float, # 0~1
          'lsr':         float,
          'whale_net':   float, # 正=大户净多, 负=大户净空 (百万USD)
          'whale_diverge': bool,
          'note':        str,
          'raw': dict,
        }
    """
    sym = symbol.upper()

    # ── 散户维度: LSR ────────────────────────────────────────
    retail_bias = 'NEUTRAL'
    lsr = 1.0
    try:
        from data_cache import get_long_short_ratio
        lsr = get_long_short_ratio(sym)
        # LSR = 多/空比例，>1多头占优
        # 换算为多头百分比: lsr/(1+lsr)
        long_pct = lsr / (1.0 + lsr) * 100 if lsr > 0 else 50.0
        if long_pct > 60:
            retail_bias = 'LONG'    # 散户多头拥挤
        elif long_pct < 40:
            retail_bias = 'SHORT'   # 散户空头拥挤
        # 注: lsr_oi_engine 的 lsr 可能直接是百分比，做容错
        if lsr > 10:  # 像70.91这样的直接是%
            long_pct = lsr
            if long_pct > 60:   retail_bias = 'LONG'
            elif long_pct < 40: retail_bias = 'SHORT'
            else:               retail_bias = 'NEUTRAL'
            lsr_display = long_pct
        else:
            lsr_display = long_pct
    except Exception as e:
        logger.debug(f'LSR: {e}')
        lsr_display = 50.0

    # ── 大户维度: whale_engine ───────────────────────────────
    whale_bias = 'NEUTRAL'
    whale_net  = 0.0
    whale_diverge = False
    whale_notes = []
    big_buy = big_sell = 0.0
    try:
        from brahma_brain.onchain_engine import get_whale_activity
        wa = get_whale_activity(sym)
        whale_net   = wa.get('whale_net', 0.0)    # 正=净买，负=净卖
        whale_diverge = wa.get('diverge', False)
        whale_notes = wa.get('notes', [])
        big_buy  = wa.get('big_buy', 0.0)
        big_sell = wa.get('big_sell', 0.0)
        wd = wa.get('whale_dir', 'NEUTRAL')
        if wd == 'BUY':   whale_bias = 'LONG'
        elif wd == 'SELL': whale_bias = 'SHORT'
    except Exception as e:
        logger.debug(f'whale_activity: {e}')

    # 补充: smart_money_engine 大户持仓方向
    smart_bias = 'NEUTRAL'
    try:
        from brahma_brain.onchain_engine import get_smart_money_signal
        sm = get_smart_money_signal(sym)
        sm_dir = sm.get('direction', 'NEUTRAL')
        if sm_dir in ('LONG', 'SHORT'):
            smart_bias = sm_dir
            # 与whale_engine综合
            if whale_bias == 'NEUTRAL':
                whale_bias = smart_bias
            elif whale_bias != smart_bias:
                whale_bias = 'NEUTRAL'  # 两源冲突→中性
    except Exception as e:
        logger.debug(f'smart_money: {e}')

    # ── 四象限映射 ───────────────────────────────────────────
    if retail_bias == 'LONG' and whale_bias == 'SHORT':
        quadrant = 'Q1'   # 散户多 + 大户空
        signal   = 'SHORT'
        stars    = 3
        note     = f'散户多头拥挤({lsr_display:.0f}%) + 大户净空 → 做空机会最高'
        confidence = 0.80

    elif retail_bias == 'SHORT' and whale_bias == 'LONG':
        quadrant = 'Q2'   # 散户空 + 大户多
        signal   = 'LONG'
        stars    = 3
        note     = f'散户空头拥挤({lsr_display:.0f}%) + 大户净多 → 做多机会最高'
        confidence = 0.80

    elif retail_bias == 'LONG' and whale_bias == 'LONG':
        quadrant = 'Q3'   # 散户多 + 大户多
        signal   = 'TREND_LONG'
        stars    = 2
        note     = f'多头共振({lsr_display:.0f}%) → 趋势持续做多，不逆势'
        confidence = 0.60

    elif retail_bias == 'SHORT' and whale_bias == 'SHORT':
        quadrant = 'Q4'   # 散户空 + 大户空
        signal   = 'TREND_SHORT'
        stars    = 2
        note     = f'空头共振({lsr_display:.0f}%) → 趋势持续做空，不逆势'
        confidence = 0.60

    else:
        quadrant = 'NEUTRAL'
        signal   = 'NEUTRAL'
        stars    = 1
        note     = f'散户{retail_bias} 大户{whale_bias} → 信号不明，观望'
        confidence = 0.30

    # 大户背离加权
    if whale_diverge and stars >= 2:
        stars = min(3, stars + 1)
        confidence = min(0.90, confidence + 0.10)
        note += f' | ⚡背离信号确认'

    return {
        'quadrant':      quadrant,
        'retail_bias':   retail_bias,
        'whale_bias':    whale_bias,
        'signal':        signal,
        'stars':         stars,
        'confidence':    round(confidence, 2),
        'lsr':           round(lsr_display, 1),
        'whale_net':     round(whale_net, 2),
        'whale_diverge': whale_diverge,
        'note':          note,
        'raw': {
            'big_buy': big_buy, 'big_sell': big_sell,
            'whale_notes': whale_notes,
            'smart_bias': smart_bias,
        },
    }


# ═══════════════════════════════════════════════════════════════
# B2: 三阶段市场节奏识别
# ═══════════════════════════════════════════════════════════════

def get_market_phase(symbol: str) -> dict:
    """
    识别当前市场所处阶段

    Returns:
        {
          'phase': 'ACCUMULATION'|'EXPANSION'|'EXHAUSTION'|'UNKNOWN',
          'confidence': float,
          'signals': list,
          'action': str,
        }
    """
    sym = symbol.upper()
    phase_signals = []
    acc_score = exp_score = exh_score = 0

    try:
        from data_cache import get_klines, get_long_short_ratio
        from math_utils import atr, calc_rsi, ema

        kl1h = get_klines(sym, '1h', 72)
        c1h = [float(k[4]) for k in kl1h]
        h1h = [float(k[2]) for k in kl1h]
        l1h = [float(k[3]) for k in kl1h]
        v1h = [float(k[5]) for k in kl1h]

        # 价格区间宽度（24H）
        hi24 = max(h1h[-24:]); lo24 = min(l1h[-24:])
        price_range_pct = (hi24 - lo24) / lo24 * 100 if lo24 > 0 else 0

        # 量能趋势（近12根vs前12根）
        vol_recent = sum(v1h[-12:]) / 12
        vol_prior  = sum(v1h[-24:-12]) / 12
        vol_ratio  = vol_recent / vol_prior if vol_prior > 0 else 1.0

        # OI变化方向（通过RSI代理）
        rsi = calc_rsi(c1h, 14)

        # LSR极值
        lsr_raw = get_long_short_ratio(sym)
        lsr_pct = lsr_raw if lsr_raw > 10 else lsr_raw / (1 + lsr_raw) * 100

        # ── 积累期特征 ──
        if price_range_pct < 3.0:
            acc_score += 2; phase_signals.append(f'价格横盘({price_range_pct:.1f}%)')
        if vol_ratio < 0.8:
            acc_score += 2; phase_signals.append(f'量能萎缩({vol_ratio:.2f}x)')
        if 40 < rsi < 60:
            acc_score += 1; phase_signals.append(f'RSI中性({rsi:.1f})')

        # ── 爆发期特征 ──
        if vol_ratio > 1.5:
            exp_score += 2; phase_signals.append(f'量能放大({vol_ratio:.2f}x)')
        if price_range_pct > 5.0:
            exp_score += 2; phase_signals.append(f'价格突破({price_range_pct:.1f}%)')
        if rsi > 65 or rsi < 35:
            exp_score += 1; phase_signals.append(f'RSI方向明确({rsi:.1f})')

        # ── 衰竭期特征 ──
        if vol_ratio < 0.7 and price_range_pct > 3.0:
            exh_score += 2; phase_signals.append(f'量价背离(量{vol_ratio:.2f}x 价{price_range_pct:.1f}%)')
        if lsr_pct > 70 or lsr_pct < 30:
            exh_score += 2; phase_signals.append(f'持仓极端({lsr_pct:.0f}%)')
        if rsi > 75 or rsi < 25:
            exh_score += 2; phase_signals.append(f'RSI超买/超卖({rsi:.1f})')

    except Exception as e:
        logger.debug(f'market_phase: {e}')
        return {'phase': 'UNKNOWN', 'confidence': 0.0, 'signals': [], 'action': '数据获取失败'}

    # 取最高分阶段
    scores = {'ACCUMULATION': acc_score, 'EXPANSION': exp_score, 'EXHAUSTION': exh_score}
    phase = max(scores, key=scores.get)
    max_score = scores[phase]
    total = sum(scores.values())
    confidence = max_score / total if total > 0 else 0.0

    action_map = {
        'ACCUMULATION': '等待爆发，轻仓试探，不追涨',
        'EXPANSION':    '跟随趋势，顺势加仓，不逆势',
        'EXHAUSTION':   '反转信号，做反方向，严控仓位',
    }

    return {
        'phase':      phase,
        'confidence': round(confidence, 2),
        'signals':    phase_signals,
        'action':     action_map.get(phase, '观望'),
        'scores':     scores,
    }


# ═══════════════════════════════════════════════════════════════
# 统一入口: 四象限 + 三阶段
# ═══════════════════════════════════════════════════════════════

def get_market_context(symbol: str) -> dict:
    """完整市场认知上下文，供analyze()和price_zone_engine使用"""
    quadrant = get_quadrant(symbol)
    phase    = get_market_phase(symbol)

    # 综合操作建议
    q_signal = quadrant['signal']
    p_phase  = phase['phase']

    if q_signal in ('SHORT', 'LONG') and p_phase == 'EXHAUSTION':
        master_signal = q_signal
        master_conf   = (quadrant['confidence'] + phase['confidence']) / 2 * 1.2
        master_note   = f'四象限{q_signal} + 衰竭期共振 → 强信号'
    elif q_signal in ('SHORT', 'LONG') and p_phase == 'EXPANSION':
        master_signal = q_signal
        master_conf   = quadrant['confidence'] * 0.8
        master_note   = f'四象限{q_signal} 但处于爆发期，需谨慎逆势'
    elif q_signal in ('TREND_SHORT', 'TREND_LONG') and p_phase == 'EXPANSION':
        master_signal = q_signal.replace('TREND_', '')
        master_conf   = (quadrant['confidence'] + phase['confidence']) / 2
        master_note   = f'共振趋势 + 爆发期 → 顺势最优'
    else:
        master_signal = q_signal if q_signal != 'NEUTRAL' else 'NEUTRAL'
        master_conf   = min(quadrant['confidence'], 0.5)
        master_note   = f'{quadrant["note"]} | {phase["action"]}'

    return {
        'symbol':        symbol,
        'quadrant':      quadrant,
        'phase':         phase,
        'master_signal': master_signal,
        'master_conf':   round(min(1.0, master_conf), 2),
        'master_note':   master_note,
        'ts':            time.time(),
    }


def format_quadrant_report(ctx: dict) -> str:
    """格式化四象限报告"""
    q = ctx['quadrant']
    p = ctx['phase']
    sym = ctx['symbol'][:3]
    stars = '★' * q['stars'] + '☆' * (3 - q['stars'])

    lines = [
        f'🧠 梵天四象限 | {sym}',
        f'象限: {q["quadrant"]} {stars} | {q["note"]}',
        f'散户: LSR={q["lsr"]:.1f}% ({q["retail_bias"]}) | 大户净: ${q["whale_net"]:+.1f}M ({q["whale_bias"]})',
    ]
    if q['whale_diverge']:
        lines.append(f'⚡ 背离信号: {" | ".join(q["raw"]["whale_notes"][:2])}')
    lines += [
        f'市场阶段: {p["phase"]} (conf={p["confidence"]:.0%})',
        f'节奏信号: {" | ".join(p["signals"][:3])}',
        f'综合操作: {ctx["master_signal"]} (置信={ctx["master_conf"]:.0%})',
        f'判断: {ctx["master_note"]}',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    args = parser.parse_args()
    for sym in args.symbols:
        ctx = get_market_context(sym)
        print(format_quadrant_report(ctx))
        print()
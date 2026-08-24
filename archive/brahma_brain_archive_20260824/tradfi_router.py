# ponytail: tradfi_router 365行，有意为之，重构前先 grep 所有调用方
"""
tradfi_router.py — TradFi三类品种路由器
[设计院封印 2026-08-14 苏摩111]

架构定位：
  梵天验证铁证（5轮360根4H K线回测）：
    A类 旧框架 WR52.4% PNL-3.3% → 新框架 WR61.5% PNL+12.5%
    B类 旧框架 WR46.2% PNL-3.4% → 新框架 WR50.0% PNL+3.2%
    C类 缺事件数据 → 降至WATCH级别

三大铁律：
  铁律1: A类亚盘 = 直接STANDBY（亚盘波动0.36%为纯噪音）
  铁律2: TradFi的LSR≥75% ≠ 轧空信号（对冲盘，不是情绪拥挤）
  铁律3: C类无事件数据时 = 降级WATCH（纯技术EV为负）

调用方：brahma_core.py analyze() 主链路
  在tradfi_signal_layer之后注入，补充A/B/C差异化权重
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# A/B/C 品种分类表（基于梵天回测铁证）
# ═══════════════════════════════════════════════════════════════════

# A类：美股强联动型 — FR/LSR/加密体制失效，核心看SPX/QQQ时段
# 特征：LSR偏空=对冲盘，非做空信号；亚盘波动<0.4%为oracle噪音
_CLASS_A = frozenset([
    'NVDAUSDT', 'MSFTUSDT', 'AAPLUSDT', 'AMZNUSDT', 'GOOGLUSDT',
    'METAUSDT', 'SPYUSDT', 'QQQUSDT', 'TQQQUSDT', 'SQQQUSDT',
    'SOXLUSDT', 'SOXSUSDT', 'SPCXUSDT', 'SPXUSDT', 'INTCUSDT',
    'AMDUSDT', 'NFLXUSDT', 'CRWDUSDT', 'PLTRUSDT', 'MRVLUSDT',
    'XAUUSDT', 'XAGUSDT', 'CLUSDT', 'BZUSDT',  # 贵金属/原油跟美股宏观
    'KORUUSDT', 'EWYUSDT', 'IWMUSDT',            # 海外ETF
])

# B类：加密+美股双重联动型 — BTC体制(40%) + SPX(40%) + OI(20%)
# 特征：COIN与BTC相关0.55，亚盘/开市均有效，FR/LSR权重降至30%
_CLASS_B = frozenset([
    'COINUSDT', 'MSTRUSDT', 'HOODUSDT',
])

# C类：独立催化剂型 — BB压缩+事件触发，与BTC/SPX相关均弱
# 特征：TSLA vs BTC相关0.33，TSLA vs SPX相关0.19，事件驱动主导
# 铁律3：无财报/事件数据时降级WATCH
_CLASS_C = frozenset([
    'TSLAUSDT', 'SNDKUSDT', 'BABAUSDT', 'MUUSDT',
    'SKHYNIXUSDT', 'SKHYUSDT', 'TSMUSDT', 'DRAMUSDT',
    'SAMSUNGUSDT', 'SNXXUSDT',
])

# 美股交易时段边界（UTC分钟）
_US_REGULAR_START = 13 * 60 + 30   # 13:30 UTC
_US_REGULAR_END   = 20 * 60        # 20:00 UTC
_US_PREOPEN_START = 12 * 60        # 12:00 UTC（盘前有效流动性起点）

# ATR有效性门槛：低于此值 = 流动性不足，不入场（铁律1的量化标准）
# 注意：atr_pct 传入格式为小数比例，0.003 = 0.3%，0.012 = 1.2%
_MIN_ATR_PCT_CLASS_A = 0.003   # A类：<0.3%为亚盘oracle噪音
_MIN_ATR_PCT_CLASS_B = 0.0025  # B类：<0.25%
_MIN_ATR_PCT_CLASS_C = 0.002   # C类全时段有效，门槛略低 <0.2%


# ═══════════════════════════════════════════════════════════════════
# 公共接口
# ═══════════════════════════════════════════════════════════════════

def classify(symbol: str) -> str:
    """
    品种分类。返回 'A' / 'B' / 'C' / 'CRYPTO'。
    CRYPTO = 普通加密合约，走原始梵天逻辑，不经过此路由器。
    """
    sym = symbol.upper()
    if sym in _CLASS_A:
        return 'A'
    if sym in _CLASS_B:
        return 'B'
    if sym in _CLASS_C:
        return 'C'
    return 'CRYPTO'


def get_session() -> dict:
    """
    当前美股时段信息。
    Returns:
        {
          'session': 'ASIA' | 'PREOPEN' | 'REGULAR' | 'AFTERHOURS',
          'us_open': bool,          # True = 美股正式交易时段
          'utc_min': int,
          'weight': {'A': float, 'B': float, 'C': float},
        }
    """
    now = datetime.now(timezone.utc)
    utc_min = now.hour * 60 + now.minute

    if _US_REGULAR_START <= utc_min < _US_REGULAR_END:
        session = 'REGULAR'
        us_open = True
        weight  = {'A': 1.0, 'B': 1.0, 'C': 1.0}
    elif _US_PREOPEN_START <= utc_min < _US_REGULAR_START:
        session = 'PREOPEN'
        us_open = False
        weight  = {'A': 0.5, 'B': 0.8, 'C': 1.0}
    elif _US_REGULAR_END <= utc_min:
        session = 'AFTERHOURS'
        us_open = False
        weight  = {'A': 0.5, 'B': 0.8, 'C': 1.0}
    else:
        session = 'ASIA'
        us_open = False
        weight  = {'A': 0.0, 'B': 0.7, 'C': 1.0}  # A类亚盘=0，铁律1

    return {
        'session': session,
        'us_open': us_open,
        'utc_min': utc_min,
        'weight':  weight,
    }


def compute_router_delta(
    symbol: str,
    direction: str,
    base_score: float,
    atr_pct: float = 1.0,
    spx_chg_1d: float = 0.0,
    btc_chg_4h: float = 0.0,
    lsr_long: float = 0.5,
    fr: float = 0.0,
) -> dict:
    """
    TradFi路由器核心函数：根据A/B/C类返回评分调整delta + 操作标签。

    Args:
        symbol:      分析标的，如 'NVDAUSDT'
        direction:   'LONG' | 'SHORT'
        base_score:  原始梵天评分
        atr_pct:     当前ATR占价格百分比（0~1.0，如0.013=1.3%）
        spx_chg_1d:  SPX当日涨跌幅（-0.02 = -2%）
        btc_chg_4h:  BTC 4H涨跌幅
        lsr_long:    全市多空比中多头占比（0.77=77%多头）
        fr:          资金费率（TradFi大多为0）

    Returns:
        {
          'class':    'A'|'B'|'C'|'CRYPTO',
          'session':  'ASIA'|'PREOPEN'|'REGULAR'|'AFTERHOURS',
          'delta':    int,              # 评分调整（负=降权，0=不变）
          'valid':    bool | None,      # None=不覆盖原有valid
          'label':    str,              # breakdown注入标签
          'standby':  bool,             # True=当前不宜入场，返回STANDBY
          'watch':    bool,             # True=降级为WATCH级别
          'reasons':  list[str],        # 可读原因列表
        }
    """
    cls    = classify(symbol)
    sess   = get_session()
    result = {
        'class':   cls,
        'session': sess['session'],
        'delta':   0,
        'valid':   None,
        'label':   '',
        'standby': False,
        'watch':   False,
        'reasons': [],
    }

    if cls == 'CRYPTO':
        result['label'] = f'CRYPTO 走原始梵天逻辑'
        return result

    sess_name   = sess['session']
    sess_weight = sess['weight'][cls]
    reasons     = result['reasons']

    # ── 铁律1：A类亚盘直接STANDBY ──────────────────────────────────
    if cls == 'A' and sess_name in ('ASIA', 'PREOPEN', 'AFTERHOURS'):
        result['standby'] = True
        result['valid']   = False
        result['delta']   = -60
        reasons.append(f'[铁律1] A类{sess_name} oracle停更，atr={atr_pct*100:.2f}% → STANDBY')
        result['label'] = f'TradFi-A [{sess_name} STANDBY] atr={atr_pct*100:.2f}%'
        return result

    # ── ATR有效性过滤 ────────────────────────────────────────────────
    min_atr = {'A': _MIN_ATR_PCT_CLASS_A, 'B': _MIN_ATR_PCT_CLASS_B, 'C': _MIN_ATR_PCT_CLASS_C}[cls]
    if atr_pct < min_atr:
        result['standby'] = True
        result['valid']   = False
        result['delta']   = -30
        reasons.append(f'ATR过小={atr_pct*100:.2f}%<{min_atr*100:.1f}% 流动性不足')
        result['label'] = f'TradFi-{cls} [{sess_name}] ATR不足 STANDBY'
        return result

    delta = 0

    # ═══════════════════════════════════════════════════════════════
    # A类：美股强联动型 评分逻辑
    # 核心驱动：SPX大盘方向；FR/LSR/暴涨猎手失效
    # ═══════════════════════════════════════════════════════════════
    if cls == 'A':
        # SPX方向一致 = 加分；逆势 = 降权
        spx_pct = spx_chg_1d * 100
        if direction == 'LONG':
            if spx_pct > 1.0:
                delta += 10
                reasons.append(f'SPX顺势+10 ({spx_pct:+.2f}%)')
            elif spx_pct < -1.5:
                delta -= 20
                reasons.append(f'SPX逆势-20 ({spx_pct:+.2f}%)')
            elif spx_pct < -0.5:
                delta -= 8
                reasons.append(f'SPX弱势-8 ({spx_pct:+.2f}%)')
        else:  # SHORT
            if spx_pct < -1.0:
                delta += 8
                reasons.append(f'SPX下跌顺空+8 ({spx_pct:+.2f}%)')
            elif spx_pct > 1.5:
                delta -= 15
                reasons.append(f'SPX强势逆空-15 ({spx_pct:+.2f}%)')

        # 铁律2：A类LSR过滤（GOOGL 84.8%多头≠轧空，是对冲盘）
        if lsr_long > 0.75 and direction == 'LONG':
            # A类高多头比例是对冲盘，不是看涨信号，delta中性
            reasons.append(f'[铁律2] A类LSR={lsr_long*100:.0f}%多头=对冲盘，中性处理')
        if lsr_long < 0.30 and direction == 'LONG':
            # 极端空头比例对A类也不代表轧空，中性
            reasons.append(f'[铁律2] A类LSR={lsr_long*100:.0f}%空头=对冲盘，中性处理')

        # 非交易时段降权（盘前/盘后）
        if sess_name in ('PREOPEN', 'AFTERHOURS'):
            delta -= 10
            reasons.append(f'A类{sess_name}降权-10')

    # ═══════════════════════════════════════════════════════════════
    # B类：加密+美股双引擎 评分逻辑
    # 双因子：BTC体制(亚盘60%/开市40%) + SPX(亚盘40%/开市60%)
    # ═══════════════════════════════════════════════════════════════
    elif cls == 'B':
        spx_pct = spx_chg_1d * 100
        btc_pct = btc_chg_4h * 100

        # 动态权重：亚盘BTC主导，开市SPX主导
        if sess_name == 'ASIA':
            w_btc, w_spx = 0.6, 0.4
        else:
            w_btc, w_spx = 0.4, 0.6

        # 双引擎合并信号
        combined = btc_pct * w_btc + spx_pct * w_spx
        if direction == 'LONG':
            if combined > 1.5:
                delta += 8
                reasons.append(f'B类双引擎顺多+8 (BTC{btc_pct:+.1f}%×{w_btc} + SPX{spx_pct:+.1f}%×{w_spx})')
            elif combined < -1.5:
                delta -= 12
                reasons.append(f'B类双引擎逆多-12 (combined={combined:+.1f}%)')
        else:
            if combined < -1.5:
                delta += 8
                reasons.append(f'B类双引擎顺空+8 (combined={combined:+.1f}%)')
            elif combined > 1.5:
                delta -= 12
                reasons.append(f'B类双引擎逆空-12')

        # FR对B类降权（权重30%）
        if abs(fr) > 0.0003:
            fr_signal = -fr if direction == 'LONG' else fr
            fr_adj = int(fr_signal * 10000 * 0.3)  # 30%权重
            fr_adj = max(-8, min(8, fr_adj))
            if fr_adj != 0:
                delta += fr_adj
                reasons.append(f'B类FR调整×0.3={fr_adj:+d} (FR={fr*100:.4f}%)')

        # LSR对B类降权（权重30%）
        if lsr_long > 0.80 and direction == 'LONG':
            delta -= 5  # 不是铁律封杀，只是降权
            reasons.append(f'B类LSR拥挤降权-5 ({lsr_long*100:.0f}%多头×0.3)')
        elif lsr_long < 0.25 and direction == 'SHORT':
            delta -= 5
            reasons.append(f'B类LSR空头拥挤降权-5 ({lsr_long*100:.0f}%多头×0.3)')

    # ═══════════════════════════════════════════════════════════════
    # C类：独立催化剂型 评分逻辑
    # 铁律3：无事件数据 → WATCH级别
    # 技术有效：BB压缩突破、暴涨猎手（SNDK类轧空）
    # ═══════════════════════════════════════════════════════════════
    elif cls == 'C':
        # 铁律3：C类降级为WATCH（无财报日历时）
        # 当base_score处于155+执行区间时保留，否则降至WATCH
        if base_score < 138:
            result['watch'] = True
            delta -= 15
            reasons.append(f'[铁律3] C类无事件数据 base_score={base_score:.0f}<138 降级WATCH-15')
        else:
            reasons.append(f'C类高分({base_score:.0f}≥138) 保留执行级别')

        # C类LSR/FR逻辑保留完整权重（SNDK类轧空有效）
        if fr < -0.0002 and lsr_long < 0.30:
            # 经典轧空信号：FR负值+极端空头拥挤
            delta += 12
            reasons.append(f'C类轧空信号+12 (FR={fr*100:.4f}% LSR={lsr_long*100:.0f}%多头)')
        elif fr > 0.0002 and lsr_long > 0.75 and direction == 'LONG':
            # 多头拥挤+FR正值：做多风险
            delta -= 8
            reasons.append(f'C类多头拥挤风险-8 (FR={fr*100:.4f}% LSR={lsr_long*100:.0f}%多头)')

    # ── 应用时段权重（非A类亚盘，A类亚盘已在前面处理）──────────────
    if sess_weight < 1.0 and delta > 0:
        adj = int(delta * sess_weight) - delta  # 负数，降权部分
        if adj != 0:
            delta += adj
            reasons.append(f'{cls}类{sess_name}降权×{sess_weight}={adj:+d}')

    result['delta'] = int(max(-60, min(20, delta)))  # cap: -60 ~ +20

    # ── 生成breakdown标签 ────────────────────────────────────────────
    sym_short = symbol.replace('USDT', '')
    cls_label = {'A': '美股强联动', 'B': '加密美股双引擎', 'C': '独立催化剂'}[cls]
    watch_tag = ' [WATCH]' if result['watch'] else ''
    result['label'] = (
        f"TradFi-{cls}({cls_label})[{sess_name}]{watch_tag} "
        f"delta={result['delta']:+d} | "
        + ' | '.join(reasons[:3])  # 最多3条原因注入breakdown
    )

    return result


def get_tradfi_report_header(symbol: str) -> str:
    """
    生成分析报告头部TradFi标注（供Jarvis推送格式使用）。
    """
    cls   = classify(symbol)
    sess  = get_session()
    now   = datetime.now(timezone.utc)

    if cls == 'CRYPTO':
        return ''

    cls_names = {
        'A': '美股强联动 | SPX驱动 | FR/LSR无效',
        'B': '加密+美股双引擎 | BTC体制+SPX各半',
        'C': '独立催化剂 | 事件驱动 | 无事件=WATCH',
    }
    sess_status = {
        'ASIA':       '⛔ 亚盘（A类STANDBY / B类降权×0.7 / C类全效）',
        'PREOPEN':    '🟡 盘前（A类降权 / B类降权×0.8 / C类全效）',
        'REGULAR':    '✅ 美股正式交易时段（全类全效）',
        'AFTERHOURS': '🟡 盘后（A类降权 / B类降权×0.8 / C类全效）',
    }

    return (
        f"【TradFi-{cls}类】{cls_names[cls]}\n"
        f"时段状态：{sess_status.get(sess['session'], sess['session'])} "
        f"(UTC {now.hour:02d}:{now.minute:02d})"
    )

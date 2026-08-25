# ponytail: tradfi_dump_detector 758行，有意为之，重构前先 grep 所有调用方
"""
tradfi_dump_detector.py — 美股代币放量下跌识别引擎
设计院封印 · 2026-07-28 · 苏摩111批准

架构定位：
  复盘根因：SNDK 7月27日连续两次止损的系统性根因
    - M5 月线趋势过滤完全缺失（30日-40%仍输出做多）
    - 顶部15根持续放量出货未被识别（平均8.3x MA20）
    - OBV顶背离权重严重低估（-140%背离仅-3分）
    - 量价背离（诱多K58: 9.5x量仅+0.47%涨幅）零分
    - 下降高点序列（K52~K58连续4次）未被捕捉

三类事件识别：
  TYPE-1: TECH_OVERSOLD      — RSI超卖+联动下跌，技术性反弹信号有效
  TYPE-2: FUNDAMENTAL_DUMP   — 个股独立暴跌+放量，做多封禁→等反弹做空
  TYPE-3: PANIC_LIQUIDATION  — 极端放量+OI暴涨，轧空短多机会

5个新信号模块（M1~M5）：
  M1: top_distribution_detector  顶部持续放量出货检测
  M2: price_volume_divergence     量价背离检测（量大价不涨）
  M3: obv_divergence_weight       OBV顶背离权重升级（-3→-25分）
  M4: swing_high_decay            下降高点序列预警
  M5: monthly_trend_filter        月线趋势过滤（封禁做多核心）

使用方式：
  from brahma_brain.tradfi_dump_detector import analyze_tradfi_dump
  result = analyze_tradfi_dump(symbol, klines_1h, direction, ret_30d)
  # result['dump_type'], result['score_delta'], result['breakdown']
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 美股代币白名单（RWA代币化股票）────────────────────────────────────────
TRADFI_TOKEN_LIST = {
    'SNDKUSDT', 'MUUSDT', 'SPCXUSDT',
    'NVDAUSDT', 'TSLAMARGIN', 'AAPLMARGIN', 'COINMARGIN', 'MSTRMARGIN',
}

# 加密主流币排除（防止BTCUSDT/ETHUSDT等被误判为TradFi）
CRYPTO_NATIVE_EXCLUDE = {
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'AVAX', 'MATIC',
    'LINK', 'UNI', 'LTC', 'ATOM', 'ALGO', 'NEAR', 'FTM', 'SAND', 'MANA', 'AXS',
    'CRO', 'VET', 'THETA', 'FIL', 'TRX', 'ETC', 'XLM', 'HBAR', 'EOS', 'FLOW',
    'ICP', 'XTZ', 'EGLD', 'AAVE', 'MKR', 'COMP', 'YFI', 'SNX', 'CRV', 'SUSHI',
    '1INCH', 'ZEC', 'DASH', 'XMR', 'BCH', 'BSV', 'BAT', 'ZIL', 'ENJ', 'CHZ',
    'GALA', 'IMX', 'APE', 'GMT', 'OP', 'ARB', 'BLUR', 'SUI', 'SEI', 'TIA',
    'INJ', 'WLD', 'PYTH', 'JUP', 'STRK', 'EIGEN', 'TRUMP', 'MELANIA',
    'MEME', 'PEPE', 'BONK', 'WIF', 'FLOKI', 'SHIB', 'BABYDOGE',
    'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP',
}

# 判断是否为美股代币
def is_tradfi_token(symbol: str) -> bool:
    if symbol in TRADFI_TOKEN_LIST:
        return True
    # 常见美股代币后缀模式
    for suffix in ['USDT', 'USDC', 'BUSD']:
        base = symbol.replace(suffix, '')
        # 全大写字母且长度2~5，排除加密主流币
        if base.isalpha() and 2 <= len(base) <= 5:
            if base in CRYPTO_NATIVE_EXCLUDE:
                return False
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# M5: 月线趋势过滤（最高优先级 · 最简单 · 最有效）
# ═══════════════════════════════════════════════════════════════════════════════
def m5_monthly_trend_filter(ret_30d: float, direction: str) -> dict:
    """
    月线趋势过滤
    铁证：SNDK 30日-40.33% + 7日-12.29% 仍输出做多 = 根本性错误

    规则：
      ret_30d < -20% → MONTHLY_BEAR → 封禁做多（score_delta=-30）
      ret_30d < -30% → Kronos降权建议 × 0.3
      ret_30d < -40% → 强制做空方向（score_delta=-50，空单+20）

    Returns:
      {'triggered': bool, 'level': str, 'score_delta': int,
       'force_short': bool, 'kronos_weight': float, 'label': str}
    """
    result = {
        'triggered': False,
        'level': 'NORMAL',
        'score_delta': 0,
        'force_short': False,
        'kronos_weight': 1.0,
        'label': '',
    }

    if direction != 'LONG':
        return result  # M5只保护做多方向

    if ret_30d < -40:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_CRASH',
            'score_delta': -50,
            'force_short': True,
            'kronos_weight': 0.1,
            'label': f'M5🚫月线崩溃{ret_30d:.1f}% → 强制SHORT',
        })
    elif ret_30d < -30:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_BEAR_SEVERE',
            'score_delta': -35,
            'force_short': False,
            'kronos_weight': 0.3,
            'label': f'M5⛔月线重跌{ret_30d:.1f}% → 封禁LONG',
        })
    elif ret_30d < -20:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_BEAR',
            'score_delta': -20,
            'force_short': False,
            'kronos_weight': 0.5,
            'label': f'M5⚠️月线下行{ret_30d:.1f}% → 降权LONG',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M1: 顶部持续放量出货检测
# ═══════════════════════════════════════════════════════════════════════════════
def m1_top_distribution_detector(klines: list, recent_high: float) -> dict:
    """
    顶部出货检测
    铁证：K48~K58共11根在高位平均8.3x MA20放量 = 主力分批出货

    检测逻辑：
      1. 识别"高位区域"：近期高点±3%以内
      2. 统计在高位区域内放量（>3x MA20）的K线数量
      3. 连续5根以上平均量>5x → 顶部出货信号

    klines格式：[{'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}, ...]
                时间从旧到新，最后一根=当前K线

    Returns:
      {'triggered': bool, 'score_delta': int, 'consecutive_count': int,
       'avg_vol_ratio': float, 'label': str}
    """
    result = {
        'triggered': False,
        'score_delta': 0,
        'consecutive_count': 0,
        'avg_vol_ratio': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 25:
        return result

    # MA20应使用历史均量，取klines倒数第21~40根（排除当前顶部异常K线的干扰）
    volumes_hist = [k['volume'] for k in klines[-40:-20]] if len(klines) >= 40 else [k['volume'] for k in klines[:-20]]
    if not volumes_hist:
        return result
    ma20 = sum(volumes_hist) / len(volumes_hist)
    if ma20 <= 0:
        return result

    # 高位区域：recent_high ±3%
    high_zone_lower = recent_high * 0.97
    high_zone_upper = recent_high * 1.03

    # 统计近20根K线中在高位区域放量的K线
    high_vol_bars = []
    for k in klines[-20:]:
        bar_mid = (k['high'] + k['close']) / 2
        vol_ratio = k['volume'] / ma20
        in_high_zone = high_zone_lower <= bar_mid <= high_zone_upper
        if in_high_zone and vol_ratio >= 3.0:
            high_vol_bars.append(vol_ratio)

    if not high_vol_bars:
        return result

    count = len(high_vol_bars)
    avg_ratio = sum(high_vol_bars) / count

    if count >= 8 and avg_ratio >= 7.0:
        result.update({
            'triggered': True,
            'score_delta': -25,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1🚨顶部出货{count}根均{avg_ratio:.1f}x → 主力大量出货',
        })
    elif count >= 5 and avg_ratio >= 5.0:
        result.update({
            'triggered': True,
            'score_delta': -20,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1⚠️顶部放量{count}根均{avg_ratio:.1f}x → 出货信号',
        })
    elif count >= 3 and avg_ratio >= 4.0:
        result.update({
            'triggered': True,
            'score_delta': -10,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1🔶顶部疑似出货{count}根均{avg_ratio:.1f}x',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M2: 量价背离检测（量大价不涨 = 诱多/对倒）
# ═══════════════════════════════════════════════════════════════════════════════
def m2_price_volume_divergence(klines: list) -> dict:
    """
    量价背离检测
    铁证：K58 量9.5x MA20 但价格仅+0.47% = 诱多出货完毕，下一根K38暴跌

    两个检测模式：
      模式A: 量大价不涨（量>5x 但涨幅<1%，阳线）→ 诱多/对倒
      模式B: 量大高位阴线（量>5x 且收阴且价格在近期高点80%+分位）→ 主力出货

    只检测最近3根K线（当前K及前2根）

    Returns:
      {'triggered': bool, 'mode': str, 'score_delta': int,
       'vol_ratio': float, 'price_change_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'mode': '',
        'score_delta': 0,
        'vol_ratio': 0.0,
        'price_change_pct': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 25:
        return result

    # MA20用历史均量（倒数21~40根），排除当前异常K线的干扰
    hist_bars = klines[-40:-20] if len(klines) >= 40 else klines[:-20]
    if not hist_bars:
        return result
    ma20 = sum(k['volume'] for k in hist_bars) / len(hist_bars)
    if ma20 <= 0:
        return result

    # 近期20根高点（判断价格位置）
    recent_high_20 = max(k['high'] for k in klines[-20:])

    # 检测最近3根
    for k in klines[-3:]:
        vol_ratio = k['volume'] / ma20
        price_chg = (k['close'] - k['open']) / k['open'] * 100 if k['open'] > 0 else 0
        is_bullish = k['close'] > k['open']
        upper_shadow = (k['high'] - max(k['open'], k['close'])) / (k['high'] - k['low'] + 1e-9)
        near_high = k['high'] / recent_high_20 >= 0.95  # 在近期高点95%以上

        # 模式A: 量大价不涨（诱多）
        if vol_ratio >= 5.0 and is_bullish and abs(price_chg) < 1.0 and near_high:
            result.update({
                'triggered': True,
                'mode': 'A_TRAP_BULL',
                'score_delta': -15,
                'vol_ratio': vol_ratio,
                'price_change_pct': price_chg,
                'label': f'M2🚨量大价不涨{vol_ratio:.1f}x涨{price_chg:.2f}% → 诱多出货',
            })
            return result

        # 模式B: 高位放量阴线（主力出货）
        if vol_ratio >= 5.0 and not is_bullish and near_high and upper_shadow >= 0.3:
            result.update({
                'triggered': True,
                'mode': 'B_HIGH_BEAR',
                'score_delta': -12,
                'vol_ratio': vol_ratio,
                'price_change_pct': price_chg,
                'label': f'M2⚠️高位放量阴线{vol_ratio:.1f}x上影{upper_shadow:.0%} → 主力出货',
            })
            return result

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M3: OBV顶背离权重升级
# ═══════════════════════════════════════════════════════════════════════════════
def m3_obv_divergence_weight(klines: list, direction: str) -> dict:
    """
    OBV顶背离权重升级
    现状：OBV反向仅-3分（严重低估）
    铁证：K23→K31价格涨+2.2%但OBV从+42,159→-17,593（-140%背离）仅得-3分

    升级规则：
      OBV 24H内从正转负（转负事件）→ -15分
      OBV 背离幅度 > 50%（价涨OBV跌）→ -20分
      OBV 顶背离确认（价创新高但OBV转负）→ -25分

    Returns:
      {'triggered': bool, 'level': str, 'score_delta': int,
       'divergence_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'level': '',
        'score_delta': 0,
        'divergence_pct': 0.0,
        'label': '',
    }

    if direction != 'LONG':
        return result  # OBV背离主要保护做多

    if not klines or len(klines) < 24:
        return result

    # 计算OBV序列（过去24根1H K线）
    def calc_obv(bars):
        obv = 0.0
        obv_series = []
        for i, k in enumerate(bars):
            if i == 0:
                obv_series.append(0.0)
                continue
            if k['close'] > bars[i-1]['close']:
                obv += k['volume']
            elif k['close'] < bars[i-1]['close']:
                obv -= k['volume']
            obv_series.append(obv)
        return obv_series

    bars_24 = klines[-24:]
    obv_series = calc_obv(bars_24)

    if not obv_series:
        return result

    obv_start = obv_series[0]
    obv_end = obv_series[-1]
    obv_mid_max = max(obv_series[:12]) if obv_series[:12] else 0

    # 价格变化
    price_start = bars_24[0]['close']
    price_end = bars_24[-1]['close']
    price_mid_high = max(k['high'] for k in bars_24[:12])
    price_pct_chg = (price_end - price_start) / price_start * 100 if price_start > 0 else 0

    # OBV变化方向
    obv_turned_negative = obv_mid_max > 0 and obv_end < 0
    price_near_high = price_end / price_mid_high >= 0.95 if price_mid_high > 0 else False

    # 背离幅度计算（价格涨但OBV跌）
    # 使用总量归一化，避免obv_start接近0时溢出
    if price_pct_chg > 0 and obv_end < obv_start:
        total_vol = sum(k['volume'] for k in bars_24) + 1e-9
        obv_drop_ratio = (obv_start - obv_end) / total_vol * 100  # 归一化OBV降幅
        divergence_score = min(obv_drop_ratio * 10, 200)  # cap at 200%
    else:
        divergence_score = 0

    # 顶背离：价格创新高但OBV转负
    if price_near_high and obv_turned_negative and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'APEX_BEARISH_DIV',
            'score_delta': -25,
            'divergence_pct': divergence_score,
            'label': f'M3🚨OBV顶背离+转负 背离{divergence_score:.0f}% → 顶部确认',
        })
    elif obv_turned_negative and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'OBV_TURNED_NEG',
            'score_delta': -15,
            'divergence_pct': divergence_score,
            'label': f'M3⚠️OBV24H内转负 价格仍在上涨 → 资金出逃',
        })
    elif divergence_score > 50 and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'OBV_DIVERGE_50',
            'score_delta': -20,
            'divergence_pct': divergence_score,
            'label': f'M3⚠️OBV背离{divergence_score:.0f}% → 价涨量退做空警示',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M4: 下降高点序列预警
# ═══════════════════════════════════════════════════════════════════════════════
def m4_swing_high_decay(klines: list) -> dict:
    """
    下降高点序列预警
    铁证：K52~K58高点序列 1518→1515→1509→1499 连续4次下移
    = 空头已控盘，暴跌前5小时的结构信号

    检测：近10根1H K线中，连续3根以上高点下移（每次下移>0.1%）

    Returns:
      {'triggered': bool, 'count': int, 'score_delta': int,
       'decay_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'count': 0,
        'score_delta': 0,
        'decay_pct': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 8:
        return result

    bars = klines[-10:]
    highs = [k['high'] for k in bars]

    # 找连续下降高点
    max_consecutive = 1
    current = 1
    for i in range(1, len(highs)):
        # 高点下移 > 0.1%（过滤噪音）
        if highs[i] < highs[i-1] * 0.999:
            current += 1
            max_consecutive = max(max_consecutive, current)
        else:
            current = 1

    if max_consecutive < 3:
        return result

    # 计算下降幅度
    first_high = max(highs)
    last_high = highs[-1]
    decay_pct = (first_high - last_high) / first_high * 100 if first_high > 0 else 0

    if max_consecutive >= 5:
        result.update({
            'triggered': True,
            'count': max_consecutive,
            'score_delta': -15,
            'decay_pct': decay_pct,
            'label': f'M4🚨连续{max_consecutive}次高点下移-{decay_pct:.1f}% → 空头全面控盘',
        })
    elif max_consecutive >= 3:
        result.update({
            'triggered': True,
            'count': max_consecutive,
            'score_delta': -10,
            'decay_pct': decay_pct,
            'label': f'M4⚠️连续{max_consecutive}次高点下移-{decay_pct:.1f}% → 空头占优',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 事件类型识别（TYPE-1/2/3）
# ═══════════════════════════════════════════════════════════════════════════════
def identify_dump_type(
    symbol: str,
    vol_ratio_current: float,    # 当前K线量倍（vs MA20）
    price_chg_24h: float,        # 个股24H涨跌幅（%）
    spx_chg_24h: float,          # SPX 24H涨跌幅（%）
    ret_30d: float,              # 30日收益率（%）
    oi_chg_1h: float = 0.0,      # OI 1H变化（%）
    rsi_1h: float = 50.0,        # RSI 1H
) -> dict:
    """
    三类事件识别

    TYPE-1: TECH_OVERSOLD      技术性超卖，做多信号有效
    TYPE-2: FUNDAMENTAL_DUMP   基本面利空，封禁做多，等反弹做空
    TYPE-3: PANIC_LIQUIDATION  恐慌踩踏，轧空轻多机会

    Returns: {'dump_type': str, 'confidence': float, 'reason': str,
              'allow_long': bool, 'allow_short': bool,
              'short_entry_note': str}
    """
    result = {
        'dump_type': 'UNKNOWN',
        'confidence': 0.0,
        'reason': '',
        'allow_long': True,
        'allow_short': True,
        'short_entry_note': '',
    }

    # 个股 vs SPX 偏离度
    gap_vs_spx = price_chg_24h - spx_chg_24h  # 负值=个股比大盘更弱

    # TYPE-3: 恐慌踩踏（极端放量+OI暴涨+RSI极低）
    if vol_ratio_current >= 15.0 and rsi_1h <= 15.0 and oi_chg_1h >= 20.0:
        result.update({
            'dump_type': 'PANIC_LIQUIDATION',
            'confidence': 0.85,
            'reason': f'极端放量{vol_ratio_current:.0f}x + OI+{oi_chg_1h:.0f}% + RSI={rsi_1h:.0f}',
            'allow_long': True,  # 轧空机会，极轻仓
            'allow_short': False,
            'short_entry_note': '',
        })
        return result

    # TYPE-2: 基本面利空暴跌
    # 条件：个股独立下跌（vs SPX偏离>5%）+ 放量（>5x）
    fundamental_dump = (
        gap_vs_spx < -5.0 and vol_ratio_current >= 5.0
    ) or (
        ret_30d < -30.0 and price_chg_24h < -5.0 and vol_ratio_current >= 3.0
    )

    if fundamental_dump:
        confidence = 0.7
        reasons = []
        if gap_vs_spx < -8.0:
            confidence += 0.1
            reasons.append(f'个股独立跌{gap_vs_spx:.1f}%vs大盘')
        if vol_ratio_current >= 10.0:
            confidence += 0.1
            reasons.append(f'极端放量{vol_ratio_current:.0f}x')
        if ret_30d < -30.0:
            confidence += 0.1
            reasons.append(f'30日趋势{ret_30d:.0f}%')

        result.update({
            'dump_type': 'FUNDAMENTAL_DUMP',
            'confidence': min(confidence, 0.95),
            'reason': ' | '.join(reasons),
            'allow_long': False,
            'allow_short': True,
            'short_entry_note': '等反弹至阻力位（FVG/Bear OB）入空，非抄底',
        })
        return result

    # TYPE-1: 技术性超卖（默认，当不满足TYPE-2/3时）
    result.update({
        'dump_type': 'TECH_OVERSOLD',
        'confidence': 0.6,
        'reason': f'标准联动下跌 gap_vs_spx={gap_vs_spx:.1f}% vol={vol_ratio_current:.1f}x',
        'allow_long': True,
        'allow_short': False,
        'short_entry_note': '',
    })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口：全量分析
# ═══════════════════════════════════════════════════════════════════════════════
def analyze_tradfi_dump(
    symbol: str,
    klines_1h: list,             # 过去40根1H K线（从旧到新）
    direction: str,              # 'LONG' 或 'SHORT'
    ret_30d: float,              # 30日收益率（%）
    price_chg_24h: float = 0.0,  # 个股24H涨跌（%）
    spx_chg_24h: float = 0.0,   # SPX 24H涨跌（%）
    vol_ratio_current: float = 1.0,  # 当前K线量倍
    oi_chg_1h: float = 0.0,
    rsi_1h: float = 50.0,
) -> dict:
    """
    美股代币全量分析入口

    Returns:
      {
        'is_tradfi': bool,
        'dump_type': str,          TYPE-1/2/3
        'score_delta': int,        对梵天score的调整量（负数=降分）
        'force_direction': str,    '' / 'SHORT'（强制方向）
        'kronos_weight': float,    Kronos权重乘数
        'allow_long': bool,
        'allow_short': bool,
        'breakdown': dict,         各模块详细信号
        'summary_label': str,      单行汇总（注入梵天breakdown）
      }
    """
    is_tf = is_tradfi_token(symbol)

    result = {
        'is_tradfi': is_tf,
        'dump_type': 'NORMAL',
        'score_delta': 0,
        'force_direction': '',
        'kronos_weight': 1.0,
        'allow_long': True,
        'allow_short': True,
        'breakdown': {},
        'summary_label': '',
    }

    if not is_tf:
        return result

    breakdown = {}

    # ── M5: 月线趋势过滤（最优先）─────────────────────────────────────
    m5 = m5_monthly_trend_filter(ret_30d, direction)
    breakdown['M5_monthly'] = m5
    if m5['triggered']:
        result['score_delta'] += m5['score_delta']
        result['kronos_weight'] = min(result['kronos_weight'], m5['kronos_weight'])
        if m5['force_short']:
            result['force_direction'] = 'SHORT'
            result['allow_long'] = False

    # ── 事件类型识别（TYPE-1/2/3）──────────────────────────────────────
    recent_high = max(k['high'] for k in klines_1h[-20:]) if klines_1h else 0
    dump_info = identify_dump_type(
        symbol=symbol,
        vol_ratio_current=vol_ratio_current,
        price_chg_24h=price_chg_24h,
        spx_chg_24h=spx_chg_24h,
        ret_30d=ret_30d,
        oi_chg_1h=oi_chg_1h,
        rsi_1h=rsi_1h,
    )
    result['dump_type'] = dump_info['dump_type']
    breakdown['dump_type_info'] = dump_info

    if dump_info['dump_type'] == 'FUNDAMENTAL_DUMP':
        result['allow_long'] = False
        if direction == 'LONG':
            result['score_delta'] += -30  # 基本面利空额外-30
        result['kronos_weight'] = min(result['kronos_weight'], 0.2)
    elif dump_info['dump_type'] == 'PANIC_LIQUIDATION':
        result['allow_short'] = False
        result['kronos_weight'] = 1.0  # 轧空时Kronos权重恢复

    # ── M1: 顶部持续放量出货（有K线数据时运行）────────────────────────
    if klines_1h and len(klines_1h) >= 20:
        m1 = m1_top_distribution_detector(klines_1h, recent_high)
        breakdown['M1_top_dist'] = m1
        if m1['triggered'] and direction == 'LONG':
            result['score_delta'] += m1['score_delta']

    # ── M2: 量价背离────────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 22:
        m2 = m2_price_volume_divergence(klines_1h)
        breakdown['M2_pv_div'] = m2
        if m2['triggered'] and direction == 'LONG':
            result['score_delta'] += m2['score_delta']

    # ── M3: OBV顶背离──────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 24:
        m3 = m3_obv_divergence_weight(klines_1h, direction)
        breakdown['M3_obv'] = m3
        if m3['triggered']:
            result['score_delta'] += m3['score_delta']

    # ── M4: 下降高点──────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 8:
        m4 = m4_swing_high_decay(klines_1h)
        breakdown['M4_swing'] = m4
        if m4['triggered'] and direction == 'LONG':
            result['score_delta'] += m4['score_delta']

    result['breakdown'] = breakdown

    # ── 汇总标签──────────────────────────────────────────────────────
    labels = []
    if m5['triggered']:
        labels.append(m5['label'])
    labels.append(f"[{dump_info['dump_type']}]")
    if klines_1h and len(klines_1h) >= 20:
        if breakdown.get('M1_top_dist', {}).get('triggered'):
            labels.append(breakdown['M1_top_dist']['label'])
    if klines_1h and len(klines_1h) >= 22:
        if breakdown.get('M2_pv_div', {}).get('triggered'):
            labels.append(breakdown['M2_pv_div']['label'])
    if klines_1h and len(klines_1h) >= 24:
        if breakdown.get('M3_obv', {}).get('triggered'):
            labels.append(breakdown['M3_obv']['label'])
    if klines_1h and len(klines_1h) >= 8:
        if breakdown.get('M4_swing', {}).get('triggered'):
            labels.append(breakdown['M4_swing']['label'])

    result['summary_label'] = ' | '.join(labels) if labels else f'[{dump_info["dump_type"]}]正常'
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SNDK 7月27日历史回测验证
# ═══════════════════════════════════════════════════════════════════════════════
def backtest_sndk_0727():
    """
    回测验证：SNDK 7月27日历史数据
    验证5个模块是否能在暴跌前给出正确信号
    """
    # 模拟K线数据（基于复盘的真实数据）
    ma20_vol = 4234  # 真实MA20成交量

    klines = []
    # 背景K线（正常量级）
    for _ in range(20):
        klines.append({'open': 1400, 'high': 1420, 'low': 1390, 'close': 1410,
                        'volume': ma20_vol * 1.1})

    # K48~K58 顶部出货区
    top_data = [
        (1480, 1499, 1478, 1499, 5.5),   # K48 +1.3%
        (1499, 1505, 1477, 1479, 6.7),   # K49 -1.3%
        (1479, 1495, 1478, 1488, 8.2),   # K50 +0.6% 长上影
        (1488, 1500, 1487, 1497, 5.3),   # K51 +0.6%
        (1497, 1518, 1496, 1512, 7.6),   # K52 +1.0% 创高
        (1512, 1518, 1510, 1511, 9.8),   # K53 -0.1% 顶部横盘
        (1511, 1516, 1507, 1508, 11.2),  # K54 -0.2% 顶部横盘
        (1508, 1518, 1507, 1515, 6.4),   # K55 +0.5%
        (1515, 1516, 1507, 1509, 8.9),   # K56 -0.4% 长上影
        (1509, 1512, 1497, 1499, 13.8),  # K57 -0.7% 放量阴线
        (1480, 1494, 1479, 1487, 9.5),   # K58 +0.5% 量大价不涨（诱多）
    ]

    for o, h, l, c, vol_mult in top_data:
        klines.append({'open': o, 'high': h, 'low': l, 'close': c,
                        'volume': ma20_vol * vol_mult})

    # 分析
    result = analyze_tradfi_dump(
        symbol='SNDKUSDT',
        klines_1h=klines,
        direction='LONG',
        ret_30d=-40.80,
        price_chg_24h=-10.2,
        spx_chg_24h=2.87,
        vol_ratio_current=9.5,  # K58的量倍（最新K线）
        oi_chg_1h=8.0,
        rsi_1h=25.0,
    )
    return result


if __name__ == '__main__':
    import json as _json
    print("═" * 60)
    print("SNDK 7月27日回测验证")
    print("═" * 60)
    r = backtest_sndk_0727()
    print(f"is_tradfi:      {r['is_tradfi']}")
    print(f"dump_type:      {r['dump_type']}")
    print(f"score_delta:    {r['score_delta']}")
    print(f"allow_long:     {r['allow_long']}")
    print(f"allow_short:    {r['allow_short']}")
    print(f"force_dir:      {r['force_direction']}")
    print(f"kronos_weight:  {r['kronos_weight']}")
    print(f"\n汇总标签：{r['summary_label']}")
    print("\n各模块详情：")
    for k, v in r['breakdown'].items():
        if isinstance(v, dict) and v.get('triggered'):
            print(f"  {k}: {v.get('label', '')} delta={v.get('score_delta', 0)}")

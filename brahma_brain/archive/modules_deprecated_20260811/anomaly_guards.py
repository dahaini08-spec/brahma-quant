"""
P1 量价异常检测 + P2 多币联动预警 + P3 框架切换机制
梵天设计院封印 2026-07-24 · 苏摩111批准

P1: 滞涨放量/瀑布启动识别
P2: 相关币种同步异动预警
P3: BULL_TREND+BEAR_CHOCH → 体制转换警告
"""
import urllib.request, json
from datetime import datetime, timezone


# ── P2: 联动币对配置 ────────────────────────────────────
CORRELATED_PAIRS = {
    'SNDKUSDT': ['MUUSDT'],
    'MUUSDT':   ['SNDKUSDT'],
    'BTCUSDT':  ['ETHUSDT'],
    'ETHUSDT':  ['BTCUSDT'],
    'SOLUSDT':  ['APTUSDT', 'SUIUSDT'],
}

# 量价异常阈值
VOL_SURGE_MULT   = 2.5   # 成交量倍数：当前根 > MA20 × 2.5 = 异常
PRICE_DROP_PCT   = 0.005 # 价格跌幅：> 0.5% + 量能放大 = 预警
VOL_WARN_MULT    = 2.0   # 较宽松的量能警告阈值（与价格滞涨结合）


def _fetch_klines(symbol: str, interval: str = '15m', limit: int = 25) -> list:
    url = (f'https://fapi.binance.com/fapi/v1/klines'
           f'?symbol={symbol}&interval={interval}&limit={limit}')
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return data
    except Exception:
        return []


def _vol_ma(klines: list, n: int = 20) -> float:
    vols = [float(k[5]) for k in klines[-n-1:-1]]
    return sum(vols) / len(vols) if vols else 0


# ── P1: 量价异常检测 ────────────────────────────────────

def detect_vol_price_anomaly(symbol: str) -> dict:
    """
    检测量价异常：
    - 滞涨放量（价格涨幅 < 0.3% 但量能 > MA×2）
    - 下跌放量（价格跌 > 0.5% 且量能 > MA×2.5）
    - 瀑布启动（连续2根量能放大 + 价格加速下跌）

    返回:
      {'anomaly': bool, 'level': 'WARN'|'ALERT'|'OK',
       'type': str, 'message': str, 'vol_mult': float}
    """
    klines = _fetch_klines(symbol, '15m', 25)
    if len(klines) < 6:
        return {'anomaly': False, 'level': 'OK', 'message': ''}

    vol_ma = _vol_ma(klines, 20)
    if vol_ma <= 0:
        return {'anomaly': False, 'level': 'OK', 'message': ''}

    # 最近3根K线
    k1 = klines[-2]  # 已完成的最后一根
    k2 = klines[-3]  # 前一根
    k3 = klines[-4]  # 再前一根

    c1_open, c1_high, c1_low, c1_close, c1_vol = (
        float(k1[1]), float(k1[2]), float(k1[3]), float(k1[4]), float(k1[5]))
    c2_open, c2_close, c2_vol = float(k2[1]), float(k2[4]), float(k2[5])
    c3_vol = float(k3[5])

    price_chg1 = (c1_close - c1_open) / c1_open
    vol_mult1  = c1_vol / vol_ma
    vol_mult2  = c2_vol / vol_ma

    anomaly_type = None
    level = 'OK'

    # 滞涨放量（看空预警）
    if (abs(price_chg1) < 0.003 and vol_mult1 >= VOL_WARN_MULT
            and c1_close < c1_high * 0.998):
        anomaly_type = '滞涨放量'
        level = 'WARN'

    # 下跌放量（出货预警）
    if price_chg1 <= -PRICE_DROP_PCT and vol_mult1 >= VOL_SURGE_MULT:
        anomaly_type = '下跌放量'
        level = 'ALERT'

    # 瀑布启动（连续2根量放大 + 价格下跌加速）
    if (vol_mult1 >= 2.0 and vol_mult2 >= 2.0
            and price_chg1 < -0.003
            and c1_close < c2_close):
        anomaly_type = '瀑布启动'
        level = 'ALERT'

    if anomaly_type:
        msg = (
            f"⚠️ [P1量价异常] {symbol} · {anomaly_type}\n"
            f"  最新K线: O={c1_open:.2f} C={c1_close:.2f} "
            f"涨跌={price_chg1*100:+.2f}%\n"
            f"  成交量: {c1_vol:.0f} (MA20={vol_ma:.0f}, 放大{vol_mult1:.1f}x)\n"
            f"  {'🚨 警惕主力出货/结构破坏' if level=='ALERT' else '⚠️ 量价背离，关注方向确认'}"
        )
        return {
            'anomaly': True, 'level': level,
            'type': anomaly_type, 'message': msg,
            'vol_mult': round(vol_mult1, 2),
        }

    return {'anomaly': False, 'level': 'OK', 'message': '', 'vol_mult': round(vol_mult1, 2)}


# ── P2: 多币联动预警 ────────────────────────────────────

def detect_correlation_alert(symbol: str, current_drop_pct: float) -> dict:
    """
    检测相关币种是否同步异动
    current_drop_pct: 当前币种1H跌幅（负数表示下跌）

    返回: {'alert': bool, 'message': str, 'corr_moves': list}
    """
    peers = CORRELATED_PAIRS.get(symbol.upper(), [])
    if not peers or abs(current_drop_pct) < 0.015:
        return {'alert': False, 'message': ''}

    corr_moves = []
    for peer in peers:
        klines = _fetch_klines(peer, '1h', 3)
        if not klines:
            continue
        k = klines[-2]  # 已完成的最近1H
        peer_chg = (float(k[4]) - float(k[1])) / float(k[1])
        peer_vol  = float(k[5])
        # 同向下跌 > 1%
        if peer_chg <= -0.01:
            corr_moves.append({
                'symbol': peer,
                'change': round(peer_chg * 100, 2),
                'vol': peer_vol,
            })

    if corr_moves:
        peers_str = ', '.join(
            f"{c['symbol']}({c['change']:+.1f}%)" for c in corr_moves)
        msg = (
            f"🔗 [P2联动预警] {symbol} + 相关币种同步下跌\n"
            f"  {symbol}: {current_drop_pct*100:+.1f}%\n"
            f"  联动: {peers_str}\n"
            f"  ⚠️ 板块性事件风险，非单币技术性下跌！"
        )
        return {'alert': True, 'message': msg, 'corr_moves': corr_moves}

    return {'alert': False, 'message': '', 'corr_moves': []}


# ── P3: 框架切换机制 ────────────────────────────────────

def detect_regime_switch_warning(regime: str, choch_direction: str,
                                 grade: float, score: float) -> dict:
    """
    BULL_TREND + BEAR_CHOCH → 体制转换警告（不封禁做空，只输出警告）
    BEAR_TREND + BULL_CHOCH → 体制反转预警（机会信号）

    返回: {'warning': bool, 'level': str, 'message': str}
    """
    if not regime or not choch_direction:
        return {'warning': False, 'message': ''}

    warning = False
    level = 'INFO'
    msg = ''

    # BULL_TREND 体制下出现 BEAR_CHOCH
    if regime == 'BULL_TREND' and 'BEAR' in choch_direction.upper():
        warning = True
        level = 'WARN' if grade < 70 else 'INFO'
        msg = (
            f"🔄 [P3框架切换] BULL_TREND体制出现BEAR_CHOCH\n"
            f"  当前: {regime} grade={grade:.1f} score={score:.1f}\n"
            f"  ⚠️ 体制转换预警：多头框架受损，建议:\n"
            f"    ① 已持多仓 → 收紧止损至最近Bull OB下沿\n"
            f"    ② 未持仓 → 暂停做多计划，等BULL_CHOCH重建\n"
            f"    ③ 不建议在当前框架下做空（BULL_TREND封禁）"
        )

    # BEAR_TREND 体制下出现 BULL_CHOCH（机会预警）
    elif regime in ('BEAR_TREND', 'BEAR_RECOVERY') and 'BULL' in choch_direction.upper():
        warning = True
        level = 'OPPORTUNITY'
        msg = (
            f"🌱 [P3框架切换] {regime}体制出现BULL_CHOCH\n"
            f"  grade={grade:.1f} score={score:.1f}\n"
            f"  ✅ 体制转换机会：空头框架受损，关注反转\n"
            f"    ① 等grade≥80确认结构有效性\n"
            f"    ② 配合量能放大确认买盘入场\n"
            f"    ③ 止损设最近BEAR_CHOCH价位下方"
        )

    return {'warning': warning, 'level': level, 'message': msg}


# ── P4: Bull OB=0 输出模板重写 ─────────────────────────

def fmt_no_bull_ob_template(symbol: str, price: float,
                             bear_obs: list, liq_pools: dict,
                             fib_level: float = None) -> str:
    """
    P4: 当Bull OB=0时，切换为"等待重建模式"输出
    隐藏无意义TP，显示止损猎杀区+反弹阻力位+重建条件
    """
    lines = ['⚠️ 结构真空区 — 无Bull OB锚定，暂停多头计划']

    # 止损猎杀区（最近等低止损池）
    nearest_pool = liq_pools.get('nearest_low_pool')
    if nearest_pool:
        lines.append(f"  止损猎杀区（观察）: ${nearest_pool['price']:.2f}"
                     f"（距{nearest_pool['dist_pct']:+.2f}%）")
    else:
        # Fib理论位提示
        if fib_level:
            lines.append(f"  Fib理论位（参考意义低）: ${fib_level:.2f}")
        lines.append(f"  ⚠️ 进场区为Fib外推，无机构OB锚定，不可作为支撑依据")

    # 反弹阻力位
    if bear_obs:
        nearest_bear = bear_obs[0]
        lines.append(f"  反弹第一阻力: ${nearest_bear['low']:.2f}~"
                     f"${nearest_bear['high']:.2f}（Bear OB）")

    # Bull OB重建条件
    lines += [
        f"  Bull OB重建条件:",
        f"    → 价格在某区间连续3根1H收阳 + 量能放大确认",
        f"    → 重建后重新运行1号工程评估入场",
    ]

    return '\n'.join(lines)

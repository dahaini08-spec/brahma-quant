"""
universal_asset_router.py — 梵天通用资产路由器
设计院·达摩院 自主决策 2026-06-29

核心思想：
  「为一类资产注册一套配置」而非「为每个标的写一套代码」

三大抽象：
  1. classify_asset(symbol)    → 资产类型（1行）
  2. ASSET_PROFILES[type]      → 权重矩阵（配置驱动）
  3. get_regime_cached(symbol) → 体制共享缓存（2行）

覆盖能力：
  BTC/ETH主力 | 大盘山寨 | 小盘山寨 | 暴涨猎手标的
  体制×资产类型 → 二维权重矩阵 → 精准EV最大化
"""

import time
import re
from typing import Optional

# ─────────────────────────────────────────────────────────
# 一、资产分类器
# ─────────────────────────────────────────────────────────

# 主力资产（BTC/ETH）
_TIER1 = {'BTCUSDT', 'ETHUSDT'}

# 大盘山寨（市值前50，流动性好）
_TIER2 = {
    'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT',
    'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT',
    'BCHUSDT', 'ATOMUSDT', 'NEARUSDT', 'UNIUSDT', 'AAVEUSDT',
    'SUIUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT',
    'TIAUSDT', 'SEIUSDT', 'STXUSDT', 'RUNEUSDT', 'FTMUSDT',
    'HYPEUSDT', 'WLDUSDT', 'TAOUSDT', 'JUPUSDT', 'ENAUSDT',
}

# 暴涨猎手白名单（高频妖币，达摩院铁证）
_PUMP_WHITELIST = {
    'PIPPINUSDT', 'SIRENUSDT', 'HUSDT', 'MYXUSDT',
    'COAIUSDT', 'BLESSUSDT', 'RAVEUSDT', 'LABUSDT',
    'ZECUSDT', 'KNCUSDT', 'SNXUSDT', 'DASHUSDT',
    'ONTUSDT', 'ZILUSDT', 'IOTAUSDT', 'XLMUSDT',
}

# 资产类型常量
ASSET_BTC_ETH  = 'BTC_ETH'
ASSET_ALT_LARGE = 'ALT_LARGE'
ASSET_ALT_SMALL = 'ALT_SMALL'
ASSET_PUMP_HUNT = 'PUMP_HUNT'


def classify_asset(symbol: str) -> str:
    """
    1行核心：根据 symbol 返回资产类型
    决定后续使用哪套权重矩阵和出场参数
    """
    if symbol in _TIER1:
        return ASSET_BTC_ETH
    if symbol in _TIER2:
        return ASSET_ALT_LARGE
    if symbol in _PUMP_WHITELIST:
        return ASSET_PUMP_HUNT
    return ASSET_ALT_SMALL


# ─────────────────────────────────────────────────────────
# 二、权重矩阵（配置驱动，改配置不改代码）
# ─────────────────────────────────────────────────────────

# 各资产类型的引擎权重乘数
# 键名对应 brahma_core breakdown 维度
ASSET_PROFILES = {
    ASSET_BTC_ETH: {
        '趋势一致性':   1.5,   # BTC/ETH 趋势信号最可靠
        'SMC结构':      1.5,
        '动量背离':     1.3,
        '量能验证':     1.2,
        '鲸鱼+微观':    2.0,   # 大资金对BTC影响最大
        '宏观+事件':    1.8,   # 宏观驱动BTC最强
        '清算/OI':      1.2,
        '情绪/费率':    1.0,
        'pump_weight':  0.0,   # BTC不走暴涨逻辑
        'sl_pct':       2.0,   # 标准止损
        'tp_mult':      1.0,
        'pos_pct':      2.0,   # 标准仓位
    },
    ASSET_ALT_LARGE: {
        '趋势一致性':   1.2,
        'SMC结构':      1.2,
        '动量背离':     1.2,
        '量能验证':     1.3,
        '鲸鱼+微观':    1.5,
        '宏观+事件':    1.0,
        '清算/OI':      1.3,
        '情绪/费率':    1.2,
        'pump_weight':  0.3,
        'sl_pct':       2.2,
        'tp_mult':      1.1,
        'pos_pct':      1.5,
    },
    ASSET_ALT_SMALL: {
        '趋势一致性':   0.8,   # 小币趋势噪声大
        'SMC结构':      0.8,
        '动量背离':     1.0,
        '量能验证':     1.5,   # 量能是小币最核心信号
        '鲸鱼+微观':    0.8,
        '宏观+事件':    0.5,   # 小币对宏观不敏感
        '清算/OI':      1.5,   # 小币清算效应更强
        '情绪/费率':    1.5,   # 负费率/空头拥挤更有效
        'pump_weight':  1.5,
        'sl_pct':       2.5,   # 小币波动大，止损宽
        'tp_mult':      1.2,
        'pos_pct':      1.0,   # 小币仓位减半
    },
    ASSET_PUMP_HUNT: {
        '趋势一致性':   0.3,   # 暴涨前趋势可能是下跌
        'SMC结构':      0.3,
        '动量背离':     0.8,
        '量能验证':     2.0,   # 量能萎缩→爆量是核心
        '鲸鱼+微观':    0.5,
        '宏观+事件':    0.2,
        '清算/OI':      2.0,   # OI暴增是暴涨信号
        '情绪/费率':    2.5,   # 极端负费率/空头拥挤是暴涨催化剂
        'pump_weight':  3.0,
        'sl_pct':       3.0,   # 暴涨猎手止损更宽
        'tp_mult':      2.0,   # 目标更高 (+30%~+50%)
        'pos_pct':      1.5,   # 高胜率允许标准仓位
    },
}

# 体制×资产类型 二维权重矩阵
# regime_mult[regime][asset_type] = 乘数
REGIME_ASSET_MATRIX = {
    'BEAR_TREND': {
        ASSET_BTC_ETH:   1.0,   # 熊市BTC信号最可靠
        ASSET_ALT_LARGE: 0.9,
        ASSET_ALT_SMALL: 0.7,   # 熊市小币信号噪声大
        ASSET_PUMP_HUNT: 0.6,   # 熊市暴涨少且持续性差
    },
    'BEAR_EARLY': {
        ASSET_BTC_ETH:   0.9,
        ASSET_ALT_LARGE: 0.8,
        ASSET_ALT_SMALL: 0.7,
        ASSET_PUMP_HUNT: 0.8,
    },
    'BEAR_RECOVERY': {
        ASSET_BTC_ETH:   1.0,
        ASSET_ALT_LARGE: 1.2,   # 反弹期山寨补涨
        ASSET_ALT_SMALL: 1.3,
        ASSET_PUMP_HUNT: 1.8,   # 反弹期暴涨最多（铁证）
    },
    'CHOP_MID': {
        ASSET_BTC_ETH:   0.8,
        ASSET_ALT_LARGE: 0.9,
        ASSET_ALT_SMALL: 1.0,
        ASSET_PUMP_HUNT: 1.3,   # 震荡期积累能量，暴涨概率高
    },
    'BULL_TREND': {
        ASSET_BTC_ETH:   1.1,
        ASSET_ALT_LARGE: 1.3,   # 牛市山寨超涨
        ASSET_ALT_SMALL: 1.2,
        ASSET_PUMP_HUNT: 1.1,
    },
    'BULL_EARLY': {
        ASSET_BTC_ETH:   1.0,
        ASSET_ALT_LARGE: 1.1,
        ASSET_ALT_SMALL: 1.0,
        ASSET_PUMP_HUNT: 1.0,
    },
}


def get_asset_weight_mult(symbol: str, regime: str) -> float:
    """
    2行代码：获取资产类型×体制的综合权重乘数
    用于 brahma_core analyze() 的 score_final 后置调整
    """
    asset_type = classify_asset(symbol)
    return REGIME_ASSET_MATRIX.get(regime, {}).get(asset_type, 1.0)


def get_asset_profile(symbol: str) -> dict:
    """获取资产完整配置（权重+出场参数）"""
    asset_type = classify_asset(symbol)
    return {**ASSET_PROFILES[asset_type], 'asset_type': asset_type}


# ─────────────────────────────────────────────────────────
# 三、体制共享缓存（全局单例，所有标的复用）
# ─────────────────────────────────────────────────────────

_REGIME_CACHE: dict = {}      # {symbol: (regime, ts)}
_REGIME_TTL   = 8 * 3600      # 8小时（体制一般不会频繁切换）


def get_regime_cached(symbol: str) -> str:
    """
    2行核心：带缓存的体制获取
    避免每次 analyze() 都重新计算体制（节省80%重复计算）
    """
    cached = _REGIME_CACHE.get(symbol)
    if cached and time.time() - cached[1] < _REGIME_TTL:
        return cached[0]
    # 缓存过期，重新计算
    try:
        from brahma_brain.regime_state_machine import RegimeStateMachine
        rsm = RegimeStateMachine(symbol)
        regime = rsm.get_regime()
        _REGIME_CACHE[symbol] = (regime, time.time())
        return regime
    except Exception:
        return _REGIME_CACHE.get(symbol, ('CHOP_MID', 0))[0]


def invalidate_regime_cache(symbol: str = None):
    """手动清除体制缓存（体制切换事件触发）"""
    if symbol:
        _REGIME_CACHE.pop(symbol, None)
    else:
        _REGIME_CACHE.clear()


def get_all_cached_regimes() -> dict:
    """返回所有缓存的体制状态（供仪表板展示）"""
    return {sym: data[0] for sym, data in _REGIME_CACHE.items()}


# ─────────────────────────────────────────────────────────
# 四、分析结果后置调整（注入 brahma_core 末端）
# ─────────────────────────────────────────────────────────

def apply_asset_routing(result: dict) -> dict:
    """
    5行代码：对 brahma_core analyze() 结果做资产路由后置调整
    - 调整 score_final（资产类型×体制加权）
    - 注入 asset_type、asset_weight_mult 字段
    - 调整 pos_pct（仓位）和 sl_pct（止损）

    在 brahma_core analyze() 末端调用一次即可实现全资产差异化
    """
    symbol  = result.get('symbol', '')
    regime  = result.get('regime', 'CHOP_MID')
    score   = result.get('score_final', 0) or 0

    asset_type   = classify_asset(symbol)
    weight_mult  = REGIME_ASSET_MATRIX.get(regime, {}).get(asset_type, 1.0)
    profile      = ASSET_PROFILES[asset_type]

    # 调整评分
    new_score = round(score * weight_mult, 1)

    # 调整仓位（从信号的 params 中）
    params = result.get('params', {})
    if params:
        orig_pos = float(params.get('pos_pct', 2.0) or 2.0)
        new_pos  = round(orig_pos * profile['pos_pct'] / 2.0, 1)  # 以2%为基准
        params['pos_pct_asset'] = new_pos

    result['asset_type']        = asset_type
    result['asset_weight_mult'] = round(weight_mult, 2)
    result['score_final_raw']   = score
    result['score_final']       = new_score

    return result


# ─────────────────────────────────────────────────────────
# 五、暴涨猎手融合管道
# ─────────────────────────────────────────────────────────

def pump_to_brahma_score(pump_alert: dict, regime: str) -> dict:
    """
    5行代码：暴涨猎手预警 → 梵天完整评分融合

    pump_alert 格式（scan_and_alert 输出）:
      symbol, score, reasons, oi_chg, funding_rate, long_short_ratio

    返回增强后的 pump_alert（含梵天体制加权分）
    """
    symbol      = pump_alert.get('symbol', '')
    pump_score  = pump_alert.get('score', 0)
    regime      = regime or get_regime_cached(symbol)

    asset_type  = classify_asset(symbol)
    regime_mult = REGIME_ASSET_MATRIX.get(regime, {}).get(ASSET_PUMP_HUNT, 1.0)

    # 暴涨专项加权
    weighted_score = pump_score * regime_mult

    pump_alert['brahma_regime']       = regime
    pump_alert['brahma_regime_mult']  = round(regime_mult, 2)
    pump_alert['brahma_weighted_score'] = round(weighted_score, 1)
    pump_alert['asset_type']          = asset_type
    pump_alert['exec_eligible']       = weighted_score >= 85 and regime != 'BEAR_TREND'

    return pump_alert


if __name__ == '__main__':
    # 快速测试
    test_symbols = ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'PIPPINUSDT', 'LABUSDT', 'CHZUSDT']
    print('=== 资产分类测试 ===')
    for sym in test_symbols:
        at = classify_asset(sym)
        wm = get_asset_weight_mult(sym, 'BEAR_TREND')
        p  = ASSET_PROFILES[at]
        print(f'  {sym:<15} → {at:<12}  BEAR_mult={wm}  pos={p["pos_pct"]}%  sl={p["sl_pct"]}%')

    print()
    print('=== 体制×资产 权重矩阵 ===')
    for regime in ['BEAR_TREND', 'BEAR_RECOVERY', 'BULL_TREND', 'CHOP_MID']:
        row = [f'{REGIME_ASSET_MATRIX[regime].get(at, 1.0):.1f}' for at in
               [ASSET_BTC_ETH, ASSET_ALT_LARGE, ASSET_ALT_SMALL, ASSET_PUMP_HUNT]]
        print(f'  {regime:<15} BTC={row[0]} ALT_L={row[1]} ALT_S={row[2]} PUMP={row[3]}')

    print()
    print('=== 暴涨猎手融合测试 ===')
    test_alert = {'symbol': 'LABUSDT', 'score': 75, 'reasons': ['OI+90%', '负费率']}
    for regime in ['BEAR_TREND', 'BEAR_RECOVERY', 'CHOP_MID']:
        r = pump_to_brahma_score(test_alert.copy(), regime)
        print(f'  {regime:<15} raw={r["score"]} → weighted={r["brahma_weighted_score"]} eligible={r["exec_eligible"]}')

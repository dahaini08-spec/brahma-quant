"""
regime_config.py — 梵天体制×方向乘数矩阵 SSOT
设计院 2026-08-24 从brahma_core.py提取封印

职责: 集中管理所有体制乘数配置，brahma_core.py通过 get_regime_mult() 调用
好处: 更新乘数不需要改动核心评分逻辑，热更新友好

调用方式:
    from regime_config import get_regime_mult
    _regime_mult = get_regime_mult(symbol, regime, signal_dir)
"""

# ── 通用矩阵（默认，适用非BTC/ETH标的）──────────────────────────────────────
# 格式: (SHORT乘数, LONG乘数)
REGIME_MULT_DEFAULT = {
    'BEAR_TREND':    (1.50,  0.35),   # SHORT S+级WR=71.8% n=2413 | LONG极端降权0.35×
    'BEAR_EARLY':    (1.15,  0.35),   # SHORT强Alpha WR=66.5% | LONG降权0.35x WR=50.4%
    'BEAR_RECOVERY': (0.35,  1.20),   # LONG=反直觉alpha WR=72.5% | SHORT极端降权
    'BULL_TREND':    (0.50,  1.10),   # LONG正alpha WR=70.3% | SHORT死穴WR=47.7%
    'BULL_EARLY':    (0.35,  1.20),   # LONG S级WR=64.4% | SHORT降权0.35x
    'BULL_CORRECTION':(1.10, 0.65),   # SHORT强，LONG样本不足
    'BULL_PEAK':     (1.00,  0.75),
    'BULL_BREAK':    (1.00,  0.75),
    'BEAR_CRASH':    (0.90,  0.65),   # 极端体制，两向均降权
    'CHOP':          (0.88,  0.50),   # SHORT解锁0.88x EV=+0.37%/笔 | LONG=0.5x
    'CHOP_HIGH':     (0.80,  0.50),
    'CHOP_MID':      (0.88,  0.50),   # CHOP_MID SHORT解锁0.88x WR=57.3%铁证
    'CHOP_LOW':      (0.88,  0.50),
    'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # 区间底部做多 LONG=1.20x WR=70.0% n=120
    'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # 区间顶部做空 SHORT=1.10x WR=61.3% n=163
}

# ── BTC专属矩阵（达摩院v4.0铁证）──────────────────────────────────────────────
REGIME_MULT_BTC = {
    'BEAR_TREND':    (1.60,  0.35),   # BTC SHORT WR=72% S+级
    'BEAR_EARLY':    (1.20,  0.35),   # BTC SHORT WR=68% S级
    'BEAR_RECOVERY': (0.35,  1.25),   # BTC LONG WR=77.6%
    'BULL_TREND':    (0.50,  1.20),   # LONG S级alpha WR=70.5% | SHORT死穴WR=48.2%
    'BULL_EARLY':    (0.35,  1.20),   # BTC BULL_EARLY LONG WR=64.6%
    'BULL_CORRECTION':(1.20, 0.60),
    'BULL_PEAK':     (1.05,  0.70),
    'BULL_BREAK':    (1.08,  0.65),
    'BEAR_CRASH':    (0.75,  0.60),
    'CHOP':          (0.88,  0.50),   # BTC CHOP SHORT WR=57.3% EV=+0.365%/笔
    'CHOP_HIGH':     (0.80,  0.50),
    'CHOP_MID':      (0.88,  0.50),
    'CHOP_LOW':      (0.88,  0.50),
    'CHOP_RANGE_DISCOUNT': (0.50,  1.20),
    'CHOP_RANGE_PREMIUM':  (1.10,  0.35),
}

# ── ETH专属矩阵（达摩院v4.0铁证）──────────────────────────────────────────────
REGIME_MULT_ETH = {
    'BEAR_TREND':    (1.60,  0.35),   # ETH SHORT WR=74% S+级
    'BEAR_EARLY':    (1.20,  0.35),   # ETH SHORT WR=70% S级
    'BEAR_RECOVERY': (0.35,  1.15),   # ETH LONG WR=67.1%
    'BULL_TREND':    (0.50,  1.30),   # LONG最强alpha WR=70.0% | SHORT死穴WR=47.1%
    'BULL_EARLY':    (0.35,  1.10),   # ETH BULL_EARLY LONG WR=64.2%
    'BULL_CORRECTION':(1.02, 0.60),
    'BULL_PEAK':     (1.05,  0.70),
    'BULL_BREAK':    (1.10,  0.75),
    'BEAR_CRASH':    (0.75,  0.60),
    'CHOP':          (0.88,  0.50),   # ETH CHOP SHORT WR=57.5% EV=+0.375%/笔
    'CHOP_HIGH':     (0.80,  0.50),
    'CHOP_MID':      (0.88,  0.50),
    'CHOP_LOW':      (0.88,  0.50),
    'CHOP_RANGE_DISCOUNT': (0.50,  1.20),
    'CHOP_RANGE_PREMIUM':  (1.10,  0.35),
}

# ── 山寨币专属矩阵（达摩院离线回放 5标的 2020~2026）──────────────────────────
REGIME_MULT_ALTCOIN = {
    'SOLUSDT': {
        'BEAR_TREND':     (0.75, 0.28),  # SHORT n=28 WR=53.6%  | LONG n=20 WR=20.0%
        'BEAR_EARLY':     (0.58, 0.35),
        'BULL_EARLY':     (0.35, 0.56),
        'BULL_TREND':     (0.35, 0.28),
        'BEAR_RECOVERY':  (0.35, 0.80),
        'BULL_CORRECTION':(0.60, 0.35),
        'CHOP': (0.50,0.50), 'CHOP_HIGH': (0.50,0.50),
        'CHOP_MID': (0.50,0.50), 'CHOP_LOW': (0.55,0.55),
    },
    'NEARUSDT': {
        'BEAR_TREND':     (0.70, 0.35),
        'BEAR_EARLY':     (0.57, 0.35),
        'BULL_EARLY':     (0.35, 0.58),
        'BULL_TREND':     (0.35, 0.81),
        'BEAR_RECOVERY':  (0.35, 0.80),
        'BULL_CORRECTION':(0.60, 0.35),
        'CHOP': (0.50,0.50), 'CHOP_HIGH': (0.50,0.50),
        'CHOP_MID': (0.50,0.50), 'CHOP_LOW': (0.55,0.55),
    },
    'MANAUSDT': {
        'BEAR_TREND':     (0.35, 0.35),
        'BEAR_EARLY':     (0.59, 0.35),
        'BULL_EARLY':     (0.35, 0.51),
        'BULL_TREND':     (0.35, 0.55),
        'BEAR_RECOVERY':  (0.35, 0.70),
        'BULL_CORRECTION':(0.50, 0.35),
        'CHOP': (0.50,0.50), 'CHOP_HIGH': (0.50,0.50),
        'CHOP_MID': (0.50,0.50), 'CHOP_LOW': (0.55,0.55),
    },
    'AXSUSDT': {
        'BEAR_TREND':     (0.46, 0.35),
        'BEAR_EARLY':     (0.55, 0.35),
        'BULL_EARLY':     (0.35, 0.50),
        'BULL_TREND':     (0.35, 0.50),
        'BEAR_RECOVERY':  (0.35, 0.70),
        'BULL_CORRECTION':(0.50, 0.35),
        'CHOP': (0.50,0.50), 'CHOP_HIGH': (0.50,0.50),
        'CHOP_MID': (0.50,0.50), 'CHOP_LOW': (0.55,0.55),
    },
    'GALAUSDT': {
        'BEAR_TREND':     (0.70, 0.35),
        'BEAR_EARLY':     (0.57, 0.35),
        'BULL_EARLY':     (0.35, 0.51),
        'BULL_TREND':     (0.35, 0.50),
        'BEAR_RECOVERY':  (0.35, 0.70),
        'BULL_CORRECTION':(0.55, 0.35),
        'CHOP': (0.50,0.50), 'CHOP_HIGH': (0.50,0.50),
        'CHOP_MID': (0.50,0.50), 'CHOP_LOW': (0.55,0.55),
    },
}

_FALLBACK_MULT = 0.85  # 未知体制，保守降权


def get_regime_mult(symbol: str, regime: str, signal_dir: str) -> float:
    """
    统一入口：根据标的、体制、方向返回乘数
    brahma_core.confluence_score() 调用此函数替代内联矩阵
    """
    sym_upper    = symbol.upper() if symbol else ''
    regime_upper = (regime or '').upper()
    is_long      = (signal_dir == 'LONG')

    # 选择矩阵（优先标的专属，其次BTC/ETH，最后DEFAULT）
    if sym_upper in REGIME_MULT_ALTCOIN:
        table = REGIME_MULT_ALTCOIN[sym_upper]
    elif 'BTC' in sym_upper:
        table = REGIME_MULT_BTC
    elif 'ETH' in sym_upper:
        table = REGIME_MULT_ETH
    else:
        table = REGIME_MULT_DEFAULT

    for key in table:
        if key in regime_upper:
            s_mult, l_mult = table[key]
            return l_mult if is_long else s_mult

    return _FALLBACK_MULT

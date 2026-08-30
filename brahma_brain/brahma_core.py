# ponytail: brahma_core 4405行，核心计算，35维共享_result状态，拆分条件: 状态隔离方案成熟后
"""
brahma_brain.py · 梵天分析大脑主入口  VERSION = v3.0
brahma_brain · Phase 1 完整整合

调用流程：
  1. market_state.py  → 多框架趋势 + 体制 + 关键位
  2. smc_engine.py    → BOS/CHoCH/OB/FVG/流动性
  3. confluence_score → 150分共振评分
  4. 输出精确交易参数 + 钉钉1格式文本
"""

# ⚠️ 开源版 | Pro版权重通过 factor_weights.yaml 注入
_OSS_MODE = True  # Pro版设为False以启用训练权重


import os, sys, time
import copy  # [P1-C audit-fix] deepcopy for cf dict
import json  # [D1-fix] 提升到顶部
from datetime import datetime, timezone, timedelta  # [D1-fix] 提升到顶部
from pathlib import Path  # [D1-fix] 提升到顶部

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

from data_cache        import prefetch_symbol, get_klines, klines_to_ohlcv
from market_state      import analyze   as ms_analyze
from smc_engine        import analyze_smc
from divergence_engine import divergence_score
from volume_engine     import volume_score
from range_engine      import range_score  # [Phase2a] 区间结构引擎
try:
    from math_utils import ema as _mu_ema, rsi as _mu_rsi, atr as _mu_atr  # [设计院 2026-06-30 全量接入] 统一数学库
    _MATH_UTILS_OK = True
except Exception:
    _MATH_UTILS_OK = False
from options_engine    import sentiment_score, analyze_funding_trend
# [CLEANED 2026-06-11] from elliott_engine    import analyze_elliott, format_elliott
# ══ INT-1: online_learner 校准权重热加载（设计院六方联合 2026-07-11）══
import json as _json_calib
_CALIB_WEIGHTS: dict = {}
try:
    _calib_path = Path(__file__).parent.parent / 'data' / 'calibrated_weights.json'
    if _calib_path.exists():
        import time as _time_calib
        if _time_calib.time() - _calib_path.stat().st_mtime < 72 * 3600:
            _CALIB_WEIGHTS = _json_calib.loads(_calib_path.read_text())
except Exception:
    _CALIB_WEIGHTS = {}

def _apply_calib(dim_key: str, raw_score: float) -> float:
    mult = _CALIB_WEIGHTS.get(dim_key, {}).get('mult', 1.0)
    return round(raw_score * float(mult), 3)

try:
    from onchain_engine import onchain_score as _onchain_score
    _ONCHAIN_OK = True
except Exception:
    _ONCHAIN_OK = False
try:
    from pattern_engine import pattern_score as _pattern_score
    _PATTERN_OK = True
except Exception:
    _PATTERN_OK = False
try:
    from order_flow_engine import order_flow_score as _order_flow_score
    _OF_OK = True
except Exception:
    _OF_OK = False
try:
    from macro_engine import macro_score as _macro_score
    _MACRO_OK = True
except Exception:
    _MACRO_OK = False
# [CLEANED 2026-06-11] harmonic_engine removed — permanently disabled
_HARMONIC_OK = False
try:
    from volume_exhaustion_engine import volume_exhaustion_score as _vol_exh_score
    _VOL_EXH_OK = True
except Exception:
    _VOL_EXH_OK = False
try:
    from divergence_engine import multitf_divergence_score as _multitf_div_score
    _MULTITF_DIV_OK = True
except Exception:
    _MULTITF_DIV_OK = False
try:
    from multitf_engine import multitf_score as _multitf_score
    _MULTITF_OK = True
except Exception:
    _MULTITF_OK = False
try:
    from enhanced_signal_engine import enhanced_score as _enhanced_score
    _ENHANCED_OK = True
except Exception:
    _ENHANCED_OK = False
try:
    from whale_engine import whale_score as _whale_score
    _WHALE_OK = True
except Exception:
    _WHALE_OK = False
try:
    from cross_market_engine import cross_market_score as _cross_market_score
    _CROSS_OK = True
except Exception:
    _CROSS_OK = False
try:
    from microstructure_engine import microstructure_score as _micro_score
    _MICRO_OK = True
except Exception:
    _MICRO_OK = False

# [架构拆分 2026-07-01] 入场参数计算已移至 brahma_core_entry
try:
    from brahma_brain.brahma_core_entry import (
        calc_trade_params as _ctp_entry,
        rebase_params as _rbp_entry,
    )
    _ENTRY_OK = True
except Exception:
    _ENTRY_OK = False

# ═══════════════════════════════════════════════════════════════
# 150分共振评分器（Phase 1 内置版）
# ═══════════════════════════════════════════════════════════════

def _calc_mtf_alignment(closes_1h, closes_4h, closes_1d):
    """多周期趋势对齐 — 原brahma_engine独有函数，合并入brahma_core [2026-08-09]"""
    def _trend(closes):
        if len(closes) < 5: return 'NEUTRAL'
        return 'UP' if closes[-1] > closes[-5] else 'DOWN'
    t1h = _trend(closes_1h)
    t4h = _trend(closes_4h)
    t1d = _trend(closes_1d)
    aligned = t1h == t4h == t1d
    return {'1h': t1h, '4h': t4h, '1d': t1d,
            'aligned': aligned,
            'consensus': t1h if aligned else 'MIXED'}


def confluence_score(ms: dict, smc: dict, signal_dir: str,
                     extra_data: dict = None) -> dict:
    """
    150分共振评分引擎
    基于 skills/ta-engine/references/analysis_engine.md
    """
    score = 0
    breakdown = {}

    # [2026-08-12 苏摹封印] RSI扁平化修复
    # 根因: ms['rsi_1h']存在momentum子字典里，顶层ms.get('rsi_1h')返回None
    # 导致 or 50 兄底 → 触发50-70区+8误得分（BUG封印）
    # 修复: 将momentum子字典的RSI写回顶层供所有下游正确读取
    if ms:
        _mom = ms.get('momentum', {})
        if _mom:
            for _rk in ('rsi_15m', 'rsi_1h', 'rsi_4h', 'rsi_1d'):
                if ms.get(_rk) is None and _mom.get(_rk) is not None:
                    ms[_rk] = _mom[_rk]
            # ATR同步
            for _ak in ('atr_1h', 'atr_4h', 'atr_pct'):
                if ms.get(_ak) is None and _mom.get(_ak) is not None:
                    ms[_ak] = _mom[_ak]

    # [UP-TRAIN10K] T01 Bootstrap置信等级表（达摩院1万次训练验证）
    # 信号质量排名: 量价配合(B,PF=1.277) > MACD金叉(B) > EMA趋势(C) > MACD零轴(C)
    # WR主导信号: MACD背离(A,52.8%) | RSI(A,53.3%) | 布林带(A,53.1%)
    # PF主导信号: 量价配合(B,1.277) — 蒙特卡洛10000次选出的核心信号
    # 实盘层使用: breakdown中记录Bootstrap置信等级供analyze()参考
    _boot_grades = {
        '量价配合': 'B',      # PF=1.277 CI=[1.25,1.31] ← 最可靠
        'MACD金叉死叉': 'B',  # PF=1.046 CI=[1.02,1.08]
        'EMA趋势顺势': 'C',   # PF=1.121 CI=[1.07,1.18]
        'MACD零轴位置': 'C',  # PF=1.096 CI=[1.05,1.15]
        'MACD背离': 'A',      # WR=52.8% CI=[52.1%,53.5%] (WR优先)
        'RSI超卖超买': 'A',   # WR=53.3% (WR优先)
        '布林带反弹': 'A',    # WR=53.1% (WR优先)
    }
    breakdown['_T01_boot_ref'] = 'QP(B)>EMA(C)>ML(C)>MACD_div(A-WR)'


    # ╔══════════════════════════════════════════════════════════╗
    # ╔══════════════════════════════════════════════════════════╗
    # ║ BLOCK-A: 技术分析层 (维度1-6)                            ║
    # ║ [封印 2026-08-11] 已提取到 brahma_core_block_a.py        ║
    # ╚══════════════════════════════════════════════════════════╝
    try:
        from brahma_brain.brahma_core_block_a import calc_block_a as _calc_block_a
    except ImportError:
        from brahma_core_block_a import calc_block_a as _calc_block_a
    _ba = _calc_block_a(ms, smc, signal_dir, extra_data, score, breakdown, symbol=ms.get('symbol',''))
    s1, s2, s3, s4 = _ba['s1'], _ba['s2'], _ba['s3'], _ba['s4']
    s5, s5b, s6    = _ba['s5'], _ba['s5b'], _ba['s6']
    score          = _ba['score']
    breakdown      = _ba['breakdown']

    _sym = (ms.get('symbol') or '').upper()  # [s7局部_sym 2026-07-01]
    _sym_price = extra_data.get('price', 0) if extra_data else 0  # s7局部
    # ╔══════════════════════════════════════════════════════════╗
    # ║ BLOCK-B: 链上/清算/资金费层 (维度7-10)                   ║
    # ║ [封印 2026-08-11] 已提取到 brahma_core_block_b.py        ║
    # ╚══════════════════════════════════════════════════════════╝
    try:
        from brahma_brain.brahma_core_block_b import calc_block_b as _calc_block_b
    except ImportError:
        from brahma_core_block_b import calc_block_b as _calc_block_b
    _bb = _calc_block_b(ms, smc, signal_dir, extra_data, score, breakdown)
    s7, s8, s9, s10 = _bb['s7'], _bb['s8'], _bb['s9'], _bb['s10']
    score            = _bb['score']
    breakdown        = _bb['breakdown']

    # ╔══════════════════════════════════════════════════════════╗
    # ║ BLOCK-C: 高级信号层 (维度11-19 + s20-s22 + s_research)   ║
    # ║ [封印 2026-08-11] 已提取到 brahma_core_block_c.py         ║
    # ╚══════════════════════════════════════════════════════════╝
    try:
        from brahma_brain.brahma_core_block_c import calc_block_c as _calc_block_c
    except ImportError:
        from brahma_core_block_c import calc_block_c as _calc_block_c
    _bc = _calc_block_c(ms, smc, signal_dir, extra_data, score, breakdown)
    s11 = _bc['s11']
    s12 = _bc['s12']
    s13 = _bc['s13']
    s14 = _bc['s14']
    s15 = _bc['s15']
    s15_adj = _bc['s15_adj']
    s16 = _bc['s16']
    s17 = _bc['s17']
    s18 = _bc['s18']
    s19 = _bc['s19']
    s20 = _bc['s20']
    s21 = _bc['s21']
    s22 = _bc['s22']
    s_research = _bc['s_research']
    score     = _bc['score']
    breakdown = _bc['breakdown']

    # [WFV-v5.0 2026-05-28] 达摩院真实梵天体制驱动训练
    # 用 brahma_brain.market_state.detect_regime() 真实体制标注
    # 15资产×IS/OOS无穿越验证，覆盖2023-2024真实市场
    #
    # 体制OOS均PF（真实值）：
    #   BEAR_EARLY(熊市初期)   PF=1.141 → SHORT奖励, LONG惩罚
    #   CHOP_HIGH(震荡高波)    PF=1.137 → 不惩罚（旧×0.82是错的）
    #   BEAR_RECOVERY(熊市修复) PF=0.998 → 轻惩罚（旧×1.08是错的）
    #   BULL_EARLY(牛市初期)   PF=0.959 → 轻惩罚（旧×1.08是错的）
    #   CHOP_LOW(震荡低波)     PF=0.865 → 惩罚
    #   CHOP_MID     PF=0.862 → 惩罚
    #   BULL_CORRECTION PF=0.687 → 强惩罚
    #   BEAR_TREND(熊市趋势)   PF=0.560 → 强惩罚（旧×1.20是严重错误！）
    # ═══════════════════════════════════════════════════════════
    _regime_str = ms.get('regime', '')
    _regime_upper = str(_regime_str).upper()
    _regime_mult = 1.0

    # ── Fix1+Fix2: 上位共识锁 + BEAR_RECOVERY幅度门槛（设计院 2026-06-29）──
    # 核心逻辑：当月/周/日线三周期全BEAR时，4H的BEAR_RECOVERY切换为噪音
    # Fix1: BEAR_RECOVERY时检查1H方向，1H仍空则维持BEAR_TREND权重
    # Fix2: 上位共识锁，三周期全BEAR时BEAR_RECOVERY乘数历史最大限制到×0.5
    if _regime_upper == 'BEAR_RECOVERY':
        try:
            _d1h_closes = _dc.get_kline_closes(_sym, '1h', 20) if hasattr(_dc, 'get_kline_closes') else []
            _d1h_rsi    = _calc_rsi(_d1h_closes, 14) if len(_d1h_closes) >= 15 else 50
            _d1h_ema20  = _ema_last(_d1h_closes, 20) if len(_d1h_closes) >= 20 else 0
            _d1h_price  = _d1h_closes[-1] if _d1h_closes else 0
            _d1h_is_bear = (_d1h_price > 0 and _d1h_ema20 > 0 and _d1h_price < _d1h_ema20)
            # Fix1: 1H仍在EMA20下方 = 1H未确认反弹 → 将BEAR_RECOVERY降级为BEAR_TREND权重
            if _d1h_is_bear and signal_dir == 'SHORT':
                _regime_upper = 'BEAR_TREND'
                print(f'[Fix1-上位共识锁] {_sym} 1H仍在EMA下方(RSI={_d1h_rsi:.0f}) → BEAR_RECOVERY降级处理为BEAR_TREND权重')
        except Exception:
            pass  # 静默降级，不阻断主流
    # Fix2: 三周期全BEAR时，BEAR_RECOVERY乘数限制上限
    _full_bear_consensus = (ms.get('d1d') == 'BEAR' and ms.get('d1w','BEAR') == 'BEAR')
    if _regime_upper == 'BEAR_RECOVERY' and _full_bear_consensus:
        _recovery_cap = 0.5  # 三周期全BEAR时，RECOVERY最高乘数限分0.5
        print(f'[Fix2-三周期共识锁] {_sym} 日/周线全BEAR → BEAR_RECOVERY乘数将限制到×{_recovery_cap}')
    else:
        _recovery_cap = 1.0
    # [v25.0 达摩院矩阵v4.0 · 2026-06-12]
    # 设计哲学：方向由价格结构决定，不由体制决定。体制只做权重调整，永不封锁。
    # 铁证：140,000次蒙特卡洛 + 8窗口WFV + 8年全周期。MDD>80%组降权0.75，不封锁。
    # _direction_block 永久废除 —— 封禁是懒人修复，降权是外科手术。
    _direction_block = False  # 永久保持False，历史遗留字段保留兼容性

    # ── BTC/ETH 双向 regime_mult 矩阵 v4.0 ─────────────────────
    _sym_upper = (ms.get('symbol') or ms.get('sym') or '').upper()
    _is_long_signal = (signal_dir == 'LONG')

    # 通用矩阵（默认，适用非BTC/ETH标的）
    _REGIME_MULT_DEFAULT = {
        # 体制            SHORT   LONG
        'BEAR_TREND':    (1.50,  0.35),   # [v25.6 为交易而生 2026-06-18] SHORT S+级WR=71.8% n=2413 | LONG极端降权0.35×(WR=45% n=3322，需score≥400，自然淘汰，梵天能力提升后开放)
        'BEAR_EARLY':    (1.15,  0.35),   # [v25.5 2026-06-18] SHORT强Alpha WR=66.5% | LONG降权0.35x(WR=50.4% n=5396 avg=-0.110 非死穴，降权非封禁)
        'BEAR_RECOVERY': (0.35,  1.20),   # [v25.6 设计院 2026-06-18] LONG=反直觉alpha WR=72.5% | SHORT极端降权0.35×（WR=47.9% n=603，为交易而生，非封禁）
        'BULL_TREND':    (0.50,  1.10),   # [v25.1 2026-06-13] LONG=正alpha(n=3046 WR=70.3% avgPnL=+0.242) SHORT=死穴(n=4999 WR=47.7% avgPnL=-0.229)
        'BULL_EARLY':    (0.35,  1.20),   # [v25.5 2026-06-18] LONG=S级alpha(WR=64.4% n=5396 +0.093%) | SHORT降权0.35x(WR=51.9% n=5396 avg=-0.137% 非死穴，降权非封禁)
        'BULL_CORRECTION':(1.10, 0.65),   # 牛回调: SHORT强，LONG样本不足
        'BULL_PEAK':     (1.00,  0.75),   # 牛顶:   SHORT尚可
        'BULL_BREAK':    (1.00,  0.75),   # 牛突破: 参考BULL_TREND
        'BEAR_CRASH':    (0.90,  0.65),   # 崩盘:   极端体制，两向均降权
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] 铁证EV=+0.37%/笔(n=3636) SHORT解锁0.88x | LONG保持0.5x（无铁证）
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] 高波动CHOP SHORT=0.80x保守 | LONG=0.5x
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] CHOP_MID SHORT解锁0.88x（WR=57.3%铁证） | LONG=0.5x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] CHOP_LOW SHORT解锁0.88x | LONG=0.5x
        # [设计院 2026-06-30 P2-D] RANGE_LOCK区间状态独立乘数通道（苏摩111审批）
        # 达摩院验证：DISCOUNT債 WR=70.0% | PREMIUM空 WR=61.3%
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # 区间底部做多解锁: LONG=1.20x(达摩院验证WR=70.0% n=120)
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # 区间顶部做空解锁: SHORT=1.10x(达摩院验证WR=61.3% n=163)
    }
    _REGIME_MULT_BTC = {
        # 体制            SHORT   LONG    # Calmar(S) / Calmar(L)
        'BEAR_TREND':    (1.60,  0.35),   # [v25.6 为交易而生] BTC SHORT WR=72% S+级 | LONG极端降权0.35×(非封禁，梵天识别能力问题，非方向永错)
        'BEAR_EARLY':    (1.20,  0.35),   # [v25.5 2026-06-18] BTC SHORT WR=68% S级 | LONG降权0.35x(WR=50.4% avg=-0.110 非死穴)
        'BEAR_RECOVERY': (0.35,  1.25),   # [v25.6] BTC LONG WR=77.6% | SHORT极端降权0.35×
        'BULL_TREND':    (0.50,  1.20),   # [v25.1 2026-06-13] LONG=S级alpha(n=1614 WR=70.5% avgPnL=+0.170) SHORT=死穴(n=2579 WR=48.2% avgPnL=-0.186)
        'BULL_EARLY':    (0.35,  1.20),   # [v25.5 2026-06-18] BTC BULL_EARLY LONG=S级alpha(WR=64.6% n=2737 +0.093%) | SHORT降权0.35x(WR=51.7% n=3398 非死穴)
        'BULL_CORRECTION':(1.20, 0.60),   # S=14.6 WR=97% / L=不激活
        'BULL_PEAK':     (1.05,  0.70),   # 参考BULL_TREND/BULL_CORRECTION
        'BULL_BREAK':    (1.08,  0.65),
        'BEAR_CRASH':    (0.75,  0.60),
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] BTC CHOP SHORT n=3636 WR=57.3% EV=+0.365%/笔(v4.0参数)
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] BTC CHOP_HIGH SHORT=0.80x保守
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] BTC CHOP_MID SHORT解锁0.88x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] BTC CHOP_LOW SHORT解锁0.88x
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # [设计院 P2-D] BTC区间底部做多: LONG=1.20x
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # [设计院 P2-D] BTC区间顶部做空: SHORT=1.10x
    }

    # ETH专属矩阵（达摩院v4.0铁证）
    _REGIME_MULT_ETH = {
        # 体制            SHORT   LONG    # Calmar(S) / Calmar(L)
        'BEAR_TREND':    (1.60,  0.35),   # [v25.6 为交易而生] ETH SHORT WR=74% S+级 | LONG极端降权0.35×
        'BEAR_EARLY':    (1.20,  0.35),   # [v25.5 2026-06-18] ETH SHORT WR=70% S级 | LONG降权0.35x(非死穴，WR=50.4% avg=-0.110)
        'BEAR_RECOVERY': (0.35,  1.15),   # [v25.6] ETH LONG WR=67.1% | SHORT极端降权0.35×
        'BULL_TREND':    (0.50,  1.30),   # [v25.1 2026-06-13] LONG=最强alpha(n=1432 WR=70.0% avgPnL=+0.324) SHORT=死穴(n=2420 WR=47.1% avgPnL=-0.274)
        'BULL_EARLY':    (0.35,  1.10),   # [v25.5 2026-06-18] ETH BULL_EARLY LONG=S级alpha(WR=64.2% n=2659) | SHORT降权0.35x(WR=52.2% n=3457 非死穴)
        'BULL_CORRECTION':(1.02, 0.60),   # S=1.5 / L=不激活(n/yr=6.3)
        'BULL_PEAK':     (1.05,  0.70),
        'BULL_BREAK':    (1.10,  0.75),
        'BEAR_CRASH':    (0.75,  0.60),
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] ETH CHOP SHORT n=3663 WR=57.5% EV=+0.375%/笔(v4.0参数)
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] ETH CHOP_HIGH SHORT=0.80x保守
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] ETH CHOP_MID SHORT解锁0.88x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] ETH CHOP_LOW SHORT解锁0.88x
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # [设计院 P2-D] ETH区间底部做多: LONG=1.20x
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # [设计院 P2-D] ETH区间顶部做空: SHORT=1.10x
    }

    # ── [P1-哲学修复 设计院 2026-06-24] 中小币专属乘数矩阵 ──────────────────
    # 哲学：不封禁，让评分自然淘汰。
    # 方法：每个标的的铁证WR / BTC+ETH参考WR = 标的专属乘数
    # 来源：达摩院 altcoin_iron_evidence.json（5标的 2020~2026 离线回放）
    # 未覆盖的组合降级到 _REGIME_MULT_DEFAULT，不猜测，不封禁。
    # 更新规则：auto_learner 每 N 条实盘后自动更新此表
    _REGIME_MULT_ALTCOIN = {
        'SOLUSDT': {
            # 铁证WR / BTC+ETH参考 → 比例乘数（范围0.25~1.2）
            'BEAR_TREND':     (0.75, 0.28),  # SHORT n=28 WR=53.6%  | LONG n=20 WR=20.0%
            'BEAR_EARLY':     (0.58, 0.35),  # SHORT n=412 WR=38.3% | LONG 降权对齐DEFAULT
            'BULL_EARLY':     (0.35, 0.56),  # LONG n=411 WR=35.8%  | SHORT 降权
            'BULL_TREND':     (0.35, 0.28),  # LONG n=20 WR=20.0% → 极端降权
            'BEAR_RECOVERY':  (0.35, 0.80),  # 无足够样本，保守
            'BULL_CORRECTION':(0.60, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'NEARUSDT': {
            'BEAR_TREND':     (0.70, 0.35),  # SHORT n=10 WR=50.0%  | LONG 无样本
            'BEAR_EARLY':     (0.57, 0.35),  # SHORT n=435 WR=38.2% | LONG 降权
            'BULL_EARLY':     (0.35, 0.58),  # LONG n=413 WR=37.5%  | SHORT 降权
            'BULL_TREND':     (0.35, 0.81),  # LONG n=14 WR=57.1%（n偏少，保守）
            'BEAR_RECOVERY':  (0.35, 0.80),
            'BULL_CORRECTION':(0.60, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'MANAUSDT': {
            'BEAR_TREND':     (0.35, 0.35),  # SHORT n=12 WR=25.0% → 极端降权
            'BEAR_EARLY':     (0.59, 0.35),  # SHORT n=422 WR=39.1%
            'BULL_EARLY':     (0.35, 0.51),  # LONG n=342 WR=33.0%
            'BULL_TREND':     (0.35, 0.55),  # LONG n=13 WR=38.5%（n偏少）
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.50, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'AXSUSDT': {
            'BEAR_TREND':     (0.46, 0.35),  # SHORT n=15 WR=33.3%
            'BEAR_EARLY':     (0.55, 0.35),  # SHORT n=438 WR=36.5%
            'BULL_EARLY':     (0.35, 0.50),  # LONG n=363 WR=32.5%
            'BULL_TREND':     (0.35, 0.50),  # 无足够样本
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.50, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'GALAUSDT': {
            'BEAR_TREND':     (0.70, 0.35),  # SHORT n=18 WR=50.0%
            'BEAR_EARLY':     (0.57, 0.35),  # SHORT n=418 WR=38.0%
            'BULL_EARLY':     (0.35, 0.51),  # LONG n=280 WR=32.9%
            'BULL_TREND':     (0.35, 0.50),
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.55, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
    }

    # 选择矩阵 → 委托 regime_config.get_regime_mult() [2026-08-24 设计院提取]
    # 矩阵数据源: brahma_brain/regime_config.py (SSOT，热更新友好)
    _matched_regime_key = None  # 初始化
    try:
        from regime_config import get_regime_mult as _get_rm
        _rm_val = _get_rm(_sym_upper, _regime_upper, signal_dir)
        if _rm_val is not None:
            _regime_mult = _rm_val
            # 找到匹配的key用于breakdown记录
            _matched_regime_key = next(
                (_rk for _rk in (_REGIME_MULT_BTC if 'BTC' in _sym_upper
                                 else _REGIME_MULT_ETH if 'ETH' in _sym_upper
                                 else _REGIME_MULT_DEFAULT)
                 if _rk in _regime_upper), _regime_upper or 'UNKNOWN'
            )
    except Exception:
        # fallback: 内联矩阵（regime_config.py不可用时保底）
        if _sym_upper in _REGIME_MULT_ALTCOIN:
            _mult_table = _REGIME_MULT_ALTCOIN[_sym_upper]
        elif 'BTC' in _sym_upper:
            _mult_table = _REGIME_MULT_BTC
        elif 'ETH' in _sym_upper:
            _mult_table = _REGIME_MULT_ETH
        else:
            _mult_table = _REGIME_MULT_DEFAULT
        _matched_regime_key = next((_rk for _rk in _mult_table if _rk in _regime_upper), None)
        if _matched_regime_key:
            _s_mult, _l_mult = _mult_table[_matched_regime_key]
            _regime_mult = _l_mult if _is_long_signal else _s_mult
        else:
            _regime_mult = 0.85

    # ── [P1-B 苏摩111批准 2026-07-11] regime_hmm_v2 概率化乘数接入 ──────────────────
    # 架构: HMM概率分布 → get_weighted_multiplier() → 概率加权乘数
    # 降级策略: HMM失败 / confidence<0.55 → 保留规则乘数（零侵入）
    # 效果: 消除硬切换噪声，体制转换期平滑过渡
    _hmm_mult_applied = False
    try:
        import sys as _hmm_sys, os as _hmm_os
        if _hmm_os.path.dirname(_hmm_os.path.abspath(__file__)) not in _hmm_sys.path: _hmm_sys.path.insert(0, _hmm_os.path.dirname(_hmm_os.path.abspath(__file__)))
        from regime_hmm_v2 import predict_regime_proba, get_weighted_multiplier as _get_hmm_mult
        _hmm_result = predict_regime_proba(_sym, (extra_data or {}).get('_klines_4h'))
        _hmm_conf   = _hmm_result.get('confidence', 0)
        _hmm_method = _hmm_result.get('method', '')
        # 仅当HMM置信度>=0.55时使用概率乘数，否则保留规则乘数
        if _hmm_conf >= 0.55 and _hmm_method != 'rule_fallback':
            _hmm_mult = _get_hmm_mult(_sym, 'LONG' if _is_long_signal else 'SHORT')
            # 安全阀：HMM乘数偏离规则乘数超过40%时降权混合
            _mult_dev = abs(_hmm_mult - _regime_mult) / max(_regime_mult, 0.01)
            if _mult_dev > 0.40:
                # 混合: 60%规则 + 40%HMM（平滑过渡）
                _final_mult = _regime_mult * 0.60 + _hmm_mult * 0.40
                breakdown['HMM乘数'] = f'混合({_hmm_mult:.3f}×0.4+{_regime_mult:.3f}×0.6={_final_mult:.3f}) conf={_hmm_conf:.2f}'
            else:
                _final_mult = _hmm_mult
                breakdown['HMM乘数'] = f'{_hmm_mult:.3f} conf={_hmm_conf:.2f} [{_hmm_method}]'
            _regime_mult = _final_mult
            _hmm_mult_applied = True
        else:
            breakdown['HMM乘数'] = f'降级(规则乘数={_regime_mult:.3f}) conf={_hmm_conf:.2f}'
    except Exception:
        pass  # HMM不可用时静默降级，完全不影响主流程
    # ── [P1-B END] ────────────────────────────────────────────────────────────

    score = int(score * _regime_mult)
    breakdown['_regime_mult'] = _regime_mult
    breakdown['_regime_v4_key'] = _matched_regime_key or 'UNKNOWN'
    breakdown['_regime'] = _regime_str

    # ── [v25.4 设计院封印] 硬封禁门控 — mult=0.00 后强制 score=0 ──────────
    # 防止：乘数为0但其他维度加分（s_research / T04奖励等）绕过封禁
    # 覆盖体制：BEAR_TREND_LONG / BULL_TREND_SHORT / BEAR_RECOVERY_SHORT 等
    # 哲学：不封禁 = 为交易而生；但死穴（WR<48%,n≥100铁证）= 硬封禁，没有例外
    # ── [v25.6 2026-06-18 设计院] 废除 HARD_BLOCK ─────────────────────────
    # 原则：为交易而生，没有方向是永远封禁的
    # 低WR组合改为极端降权0.35×（需score≥400才能通过门控=自然淘汰）
    # 梵天能力提升后，这些方向仍有机会被激活
    # _HARD_BLOCK_COMBOS 已废除，此处保留注释记录历史
    # 历史被封禁原因：BEAR_TREND_LONG WR=45% / BULL_TREND_SHORT WR=47.7%
    # 改造方向：提升识别能力，而不是永久关闭

    # [UP-TRAIN10K] T04体制×最优信号奖励矩阵
    # 达摩院1万次训练: 特定体制下命中最优信号给予×1.08奖励
    # BULL_PEAK+量价配合PF=1.393 | BULL_TREND(牛市趋势)+EMA PF=1.344
    # BEAR_CRASH+布林反弹PF=1.261 | BEAR_TREND(熊市趋势)+MACD零轴PF=1.156
    _t04_regime = _regime_upper
    _t04_bonus_applied = False
    _s4_optimal = (  # 体制×最优信号命中检测
        ('BULL_PEAK' in _t04_regime and breakdown.get('量能验证', 0) >= 15) or
        ('BULL_TREND' in _t04_regime and breakdown.get('趋势一致性', 0) >= 15) or
        ('BEAR_CRASH' in _t04_regime and breakdown.get('关键位精确度', 0) >= 12) or
        ('BEAR_TREND' in _t04_regime and breakdown.get('动量背离', 0) >= 10)
    )
    if _s4_optimal and not _direction_block and score > 0:
        score = int(score * 1.08)
        breakdown['T04体制最优'] = f'×1.08 ({_t04_regime[:10]}命中最优信号)'
        _t04_bonus_applied = True

    # [UP-NODE] 深度节点训练 N01~N06 注入
    # ─────────────────────────────────────────────
    # N01: RSI超卖超买 是最高协同信号（与量价/MACD背离搭档PF=1.232）
    #   → RSI信号同时命中时，额外+3分确认
    _rsi_score_raw = breakdown.get('关键位精确度', 0)  # RSI代理维度
    _vol_score_raw = breakdown.get('量能验证', 0)
    _macd_div_raw  = breakdown.get('动量背离', 0)
    _n01_synergy = (
        (_rsi_score_raw >= 10 and _vol_score_raw >= 12) or   # RSI+量价 synergy=0.012
        (_rsi_score_raw >= 10 and _macd_div_raw >= 10)        # RSI+MACD背离 synergy=0.012
    )
    if _n01_synergy and not _direction_block and score > 0:
        score = min(score + 3, 175)
        breakdown['N01协同奖励'] = '+3 (RSI双重协同)'

    # [Phase2c] RSI 50-70 中性偏强区加分
    # 达摩院铁证: RSI 50-70 WR=72.5%，超过超买(66.4%)和超卖(69.5%)
    # n=69,895，最大样本区间
    try:
        _rsi_now = ms.get('rsi_1h', 50) or 50
        if 50 <= _rsi_now <= 70 and signal_dir == 'SHORT' and score > 0 and not _direction_block:
            score = min(score + 8, 175)
            breakdown['Phase2c_RSI中性偏强'] = f'+8 (RSI={_rsi_now:.0f} 50-70区 WR=72.5%)'
        elif 30 <= _rsi_now <= 50 and signal_dir == 'LONG' and score > 0 and not _direction_block:
            score = min(score + 8, 175)
            breakdown['Phase2c_RSI中性偏强_v2'] = f'+8 (RSI={_rsi_now:.0f} 30-50区做多 WR=72.5%)'  # [P1-B audit-fix] 重复key加后缀
    except Exception:
        pass

    # [Phase2c] 量能×RSI>60 协同奖励 (黄金矩阵最大样本组合)
    # 达摩院铁证: 量能+RSI>60+OB WR=75.5% n=10,194，6年最差年WR=71.4%
    try:
        _vol_strong = breakdown.get('量能验证', 0) >= 10  # 量能引擎分数较高
        _rsi_60plus = _rsi_now > 60 if signal_dir == 'SHORT' else _rsi_now < 40
        if _vol_strong and _rsi_60plus and score > 0 and not _direction_block:
            score = min(score + 6, 175)
            breakdown['Phase2c_量能×RSI协同'] = f'+6 (量能强+RSI={_rsi_now:.0f} WR=75.5% n=10K)'
    except Exception:
        pass

    # N03: 时段权重 [Phase2c 2026-06-03 达摩院实证重写]
    # 铁证(n=140,443 BTC 15m OB做空 6年):
    #   欧盘 UTC07-13: WR=77.3% → +10分
    #   纽约盘后 UTC19-23: WR=69.4% → +2分
    #   亚盘 UTC00-06: WR=68.2% → 0分
    #   美盘 UTC14-18: WR=66.4% → -8分（最差，散户噪音）
    #   峰值: UTC11h WR=80.2%, UTC10h WR=78.8%（欧盘核心）
    import datetime as _dt
    _hour_utc = _dt.datetime.now(_dt.timezone.utc).hour
    _n03_delta = 0
    if 7 <= _hour_utc <= 13:    # 欧盘：WR=77.3%，比基线+6.6%
        _n03_delta = 10
        _n03_label = f'+10 (欧盘UTC{_hour_utc:02d}h WR=77.3%)'
    elif 19 <= _hour_utc <= 23: # 纽约盘后：WR=69.4%，轻微正向
        _n03_delta = 2
        _n03_label = f'+2 (纽约盘后UTC{_hour_utc:02d}h WR=69.4%)'
    elif 14 <= _hour_utc <= 18: # 美盘: 注意 n=7 样本不足，仅为观察值；降权-15基于哲学原则（降权不封禁），非数据铁证
        # [v24.2-fix 2026-06-12] 硬拒绝→降权-15分
        # 哲学原则: 不封禁时段，降权让grade≥70自然过滤
        # WR=22.2%是B级(grade55)污染结果，升门槛后美盘grade≥70仍有价值
        _n03_delta = -15
        _n03_label = f'-15 (美盘UTC{_hour_utc:02d}h 降权非封禁 v24.2)'
    else:                        # 亚盘 UTC00-06：WR=68.2%，中性
        _n03_delta = 0
        _n03_label = f'0 (亚盘UTC{_hour_utc:02d}h WR=68.2%)'
    if not _direction_block and score > 0 and _n03_delta != 0:
        score = max(0, min(score + _n03_delta, 175))
        if _n03_delta > 0:
            breakdown['N03时段奖励'] = _n03_label

    # N04: 周末惩罚 (Sat/Sun PF=0.836/0.810 < 1.0)
    _dow = -1
    try:
        _ts2 = row.name
        _dow = _ts2.dayofweek if hasattr(_ts2, 'dayofweek') else -1
    except Exception:
        pass
    if _dow in {5, 6} and not _direction_block and score > 0:  # Sat=5, Sun=6
        # [v24.3-fix] 周末 硬拒绝→降权-20分 — 哲学: 降权不封禁
        # 周末WR=65%(干净数据,样本少)，不是封死的理由；grade≥70的A级信号降权后仍可通过
        _weekend_penalty = 20
        score = max(0, score - _weekend_penalty)
        breakdown['N04周末降权'] = f'周{"六" if _dow==5 else "日"} -20分降权(v24.3) 当前score={score:.0f}'

    # N06: CHOP体制持仓期提示（最优2h vs 全局12h）
    if 'CHOP' in _regime_upper and not _direction_block:
        breakdown['N06持仓建议'] = '⚡CHOP最优持仓2h (N06实训)'


    # ════════════════════════════════════════════════════════════
    # [L7] Kronos方向验证层 v2.0 2026-05-30（设计院全局落地）
    # ════════════════════════════════════════════════════════════
    # 修复：灰区置信度（50%~70%）不再静默，轻惩罚/轻奖励
    # 同向任意置信度奖励 | 反向分级惩罚
    try:
        import json as _j, os as _os, time as _t
        _kf = '/tmp/kronos_signal.json'
        if _os.path.exists(_kf) and (_t.time() - _os.path.getmtime(_kf)) < 21600:
            _kd    = _j.load(open(_kf))
            _sym_k = (extra_data.get('_symbol','') if extra_data else '') or (ms.get('symbol','') if ms else '')
            _kp    = _kd.get(_sym_k, {})
            _kdir  = _kp.get('direction', 'NEUTRAL')
            _kconf = float(_kp.get('confidence', 0.5))
            _met   = _kd.get('_meta', {}).get('method', 'stat')
            _kage_h= (_t.time() - _os.path.getmtime(_kf)) / 3600  # Kronos数据年龄
            # 数据老化折扣（超过2H降低权重）
            _age_factor = 1.0 if _kage_h < 2 else (0.7 if _kage_h < 4 else 0.4)
            _up = (signal_dir == 'LONG'  and _kdir == 'UP')
            _dn = (signal_dir == 'SHORT' and _kdir == 'DOWN')
            _conflict = (
                (signal_dir == 'LONG'  and _kdir == 'DOWN') or
                (signal_dir == 'SHORT' and _kdir == 'UP')
            )
            if not _direction_block:
                if _up or _dn:
                    # 同向：置信度分级奖励
                    if _kconf >= 0.65:
                        _s = round(13 * _age_factor)
                    elif _kconf >= 0.55:
                        _s = round(8  * _age_factor)
                    else:
                        _s = round(4  * _age_factor)  # 弱同向也给分
                    if _s > 0:
                        score += _s
                        breakdown['L7_Kronos'] = f'+{_s}(同向{_kconf:.0%} age={_kage_h:.1f}h [{_met}])'
                elif _conflict and _kdir != 'NEUTRAL':
                    # 反向：分级惩罚（原>0.70才-10，现在灰区也有惩罚）
                    if _kconf >= 0.70:
                        _pen = round(10 * _age_factor)
                    elif _kconf >= 0.60:
                        _pen = round(5  * _age_factor)  # 中等置信反向 -5（新增）
                    else:
                        _pen = round(2  * _age_factor)  # 弱反向 -2（新增）
                    if _pen > 0:
                        score -= _pen
                        breakdown['L7_Kronos_v2'] = f'-{_pen}(反向{_kconf:.0%} age={_kage_h:.1f}h [{_met}])'  # [P1-B audit-fix] 重复key加后缀
    except Exception:
        pass

    # [UP-NODE-v3] 深度节点训练 v3 N07~N12 注入
    # ─────────────────────────────────────────────
    # N08: RSI深度分层 — 体制加限（避免震荡追高）
    _rsi_val = float(ms.get('rsi_1h', 50) if ms else 50)
    _is_long_signal = (signal_dir == 'LONG')
    _n08_boost = False
    _trend_regimes = ('BULL_TREND', 'BULL_PEAK')
    _bear_regimes  = ('BEAR_TREND', 'BEAR_CRASH')
    # 做多超买只在牛市体制有效 | 做空超卖只在熊市体制有效
    if _is_long_signal and _rsi_val > 75 and any(r in _regime_upper for r in _trend_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N08_RSI强化'] = f'+4 (RSI={_rsi_val:.0f} 牛市超买PF=1.421)'
        _n08_boost = True
    elif not _is_long_signal and _rsi_val < 20 and any(r in _regime_upper for r in _bear_regimes) and not _direction_block and score > 0:
        # [WFV-v1 2026-05-28] RSI阈值收紧 25→20 (OOS验证: 更纯净信号)
        score = min(int(score) + 4, 175)
        breakdown['N08_RSI强化_v2'] = f'+4 (RSI={_rsi_val:.0f} 熊市超卖<20 PF=1.292)'  # [P1-B audit-fix] 重复key加后缀
        _n08_boost = True

    # N08: BULL_TREND体制RSI=45~55区间特别强化(PF=2.102)
    if 'BULL_TREND' in _regime_upper and 45 <= _rsi_val < 55 and not _direction_block and score > 0:
        score = min(int(score) + 6, 175)
        breakdown['N08_牛市RSI中性'] = f'+6 (BULL_TREND RSI=45~55 PF=2.102)'

    # N10: 7维全覆盖叠加奖励 — 所有主信号都有贡献时+5分
    _sig_scores_v3 = [
        breakdown.get('动量背离', 0),
        breakdown.get('关键位精确度', 0),
        breakdown.get('SMC结构', 0),
        breakdown.get('趋势一致性', 0),
        breakdown.get('量能验证', 0),
        breakdown.get('形态成熟度', 0),
        breakdown.get('时段权重', 0),
    ]
    _n_active_sigs = sum(1 for s in _sig_scores_v3 if s > 0)
    if _n_active_sigs >= 7 and not _direction_block and score > 0:
        score = min(int(score) + 5, 175)
        breakdown['N10_全覆盖奖励'] = '+5 (7维全覆盖 PF=1.363)'

    # N12: BB位置精度强化 — 仅趋势体制有效
    _bb_pct_v3 = float(ms.get('bb', {}).get('pos', 0.5) if ms else 0.5)
    if _is_long_signal and _bb_pct_v3 > 0.90 and any(r in _regime_upper for r in _trend_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N12_BB上沿'] = f'+4 (BB={_bb_pct_v3:.2f} 牛市上沿PF=1.414)'
    elif not _is_long_signal and _bb_pct_v3 < 0.10 and any(r in _regime_upper for r in _bear_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N12_BB下沿'] = f'+4 (BB={_bb_pct_v3:.2f} 熊市下沿PF=1.263)'


    # [UP-FIX-SOL-BNB] 根因修复注入 (2026-05-26 诊断)
    # ─────────────────────────────────────────────
    # [N_VOL_PCT] ATR历史百分位评分 [2026-08-29 苏摩111]
    # vol_percentile_master.json 13个主流标的ATR历史分位
    # 【背景：各标的ATR局火/局冰存储完按时间排序】
    # 需要客观标准: 不能用单一标的ATR判断高低，必须指和自身历史百分位比
    # 高ATR(>80%分位)做多 = 追高风险，-3分
    # 低ATR(<20%分位) + BULL/BULL_EARLY = 局火正在被压缩，+3分
    try:
        import json as _jvol
        from pathlib import Path as _Pvol
        import bisect as _bisect
        _vol_path = _Pvol(__file__).parent.parent / 'data' / 'vol_percentile_master.json'
        if _vol_path.exists():
            _vol_data = _jvol.loads(_vol_path.read_text())
            _vol_sym  = _sym.replace('USDT','').replace('usdt','').upper()
            _vol_entry = _vol_data.get('data', {}).get(_vol_sym)
            if _vol_entry:
                _atr_series  = sorted(_vol_entry.get('atr_series', []))
                _cur_atr_abs = float(ms.get('momentum',{}).get('atr_1h', 0) or ms.get('atr_1h', 0) or 0)
                if _cur_atr_abs > 0 and len(_atr_series) > 50:
                    _atr_rank = _bisect.bisect_left(_atr_series, _cur_atr_abs)
                    _atr_pctile = _atr_rank / len(_atr_series)  # 0.0~1.0
                    _vol_pts = 0
                    if _atr_pctile > 0.80 and signal_dir == 'LONG':
                        _vol_pts = -3  # 高ATR做多，追高风险
                    elif _atr_pctile > 0.80 and signal_dir == 'SHORT':
                        _vol_pts = +2  # 高ATR做空，动能充足
                    elif _atr_pctile < 0.20 and 'BULL' in _regime_upper:
                        _vol_pts = +3  # 低ATR牛市 = 局火屏息就要爆
                    elif _atr_pctile < 0.20 and 'BEAR' in _regime_upper and signal_dir == 'SHORT':
                        _vol_pts = +2  # 熊市将爆量下打
                    if _vol_pts != 0:
                        score += _vol_pts
                        breakdown[f'N_VOL_PCT'] = (
                            f'{_vol_pts:+d}(ATR百分位={_atr_pctile:.0%} n={len(_atr_series)})'
                        )
    except Exception:
        pass
    # [END N_VOL_PCT] ─────────────────────────────────────────────

    # ══ [N_REPLAY 2026-08-29 苏摩111] 40年经验复盘升级——四修正 ══════════════
    # 铁证: 20392条案例 + 2001笔回测(IS/OOS偏差3%)
    try:
        _replay_rsi  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        # vol×burst: 从ms多路读取，兼容不同数据结构
        _replay_vol  = float(
            ms.get('vol_ratio') or
            (ms.get('volume') or {}).get('vol_ratio') or
            (ms.get('momentum') or {}).get('vol_ratio') or 1.0
        )
        _replay_brst = float(
            ms.get('burst_atr_mult') or
            (ms.get('momentum') or {}).get('burst_atr_mult') or
            (_result.get('fangcang') or {}).get('avg_burst_atr_mult') or 0
        )
        # compress_bars: 从fangcang结果或ms读取
        _replay_bars = int(
            ms.get('compress_bars') or
            ms.get('fangcang_bars') or
            (_result.get('fangcang') or {}).get('avg_squeeze_bars') or 0
        )
        _replay_dir  = signal_dir or _result.get('signal_dir','LONG') or 'LONG'

        # P0: squeeze_bars_w修正 [15-30根最优,60+降权]
        # 铁证: 15-30根 avg_burst=1.95x最高 / 60+根=1.76x能量分散
        if _replay_bars > 0:
            if 15 <= _replay_bars < 30:
                score = min(175, int(score) + 2)
                breakdown['P0_压缩最优窗口'] = f'+2(压缩{_replay_bars}根=15-30根黄金区)'
            elif _replay_bars >= 60:
                score = max(0, int(score) - 2)
                breakdown['P0_长压缩泡压弱'] = f'-2(压缩{_replay_bars}根>=60,能量分散)'

        # P1: vol×burst组合维度 [量能单用无效]
        # 铁证: vol单用WR差异<1% / vol×ATR>3x才是机构出手信号
        if _replay_vol > 0 and _replay_brst > 0:
            _vb_combo = _replay_vol * _replay_brst
            if _vb_combo >= 3.0 and _replay_brst >= 1.5:
                score = min(175, int(score) + 4)
                breakdown['P1_量价齐升'] = f'+4(vol={_replay_vol:.1f}x×burst={_replay_brst:.1f}x={_vb_combo:.1f}≥3.0)'
            elif _vb_combo >= 1.5 and _replay_brst >= 1.0:
                score = min(175, int(score) + 2)
                breakdown['P1_量价有效'] = f'+2(vol×burst={_vb_combo:.1f}≥1.5)'

        # P2: RSI中性区45-55加分 [机构建仓最优区]
        # 铁证: RSI45-55 做多/做空WR=77-78%(最强) n=1202/1277
        if 45 <= _replay_rsi < 55 and 'BULL_TREND' not in _regime_upper:
            score = min(175, int(score) + 4)
            breakdown['P2_RSI中性机构入场'] = f'+4(RSI={_replay_rsi:.0f}在中性区 WR=77%铁证)'

        # P3: BEAR_TREND SHORT RSI精细管控
        # 铁证RSI<45: WR=35%(n=298陷阱) / RSI55-70: WR=49%(最佳做空区)
        if 'BEAR_TREND' in _regime_upper and _replay_dir == 'SHORT':
            if _replay_rsi < 45:
                score = max(0, int(score) - 4)
                breakdown['P3_BEAR_SHORT_RSI陷阱'] = f'-4(BEAR SHORT RSI={_replay_rsi:.0f}<45 WR=35%铁证)'
            elif 55 <= _replay_rsi < 70:
                score = min(175, int(score) + 3)
                breakdown['P3_BEAR_SHORT_RSI最佳'] = f'+3(BEAR SHORT RSI={_replay_rsi:.0f}=55-70最佳做空区)'

    except Exception:
        pass
    # ══ [END N_REPLAY] ═══════════════════════════════════════════════════════


    # FIX-1: 极低波动率假牛市惩罚（精确版v2）
    _atr_pct_val = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    # [潜力释放 P1 2026-07-12] 暴涨猎手豆免通道：FR极度负值 + ATR压缩 = 爆发前元，不应惩罚
    _fr_val = float(ms.get('sentiment', {}).get('funding_rate', 0) if ms else 0)
    _pump_hunter_exempt = (
        _atr_pct_val < 0.005 and          # ATR压缩条件
        _fr_val < -0.0001 and             # FR负值（空头付费）
        signal_dir == 'LONG'              # 做多方向
    )
    if ('BULL_TREND' in _regime_upper and signal_dir == 'LONG'
            and _atr_pct_val < 0.005 and not _direction_block and score > 0):
        if _pump_hunter_exempt:
            breakdown['FIX1_假牛市'] = f'豆免(暴涨猎手) ATR={_atr_pct_val:.4f} FR={_fr_val:.4f}负值压缩=爆发前元'
        else:
            score = int(score * 0.88)
            breakdown['FIX1_假牛市'] = f'×0.88 (ATR_pct={_atr_pct_val:.4f} 极低波动假趋势)'

    # FIX-2: CHOP超卖<25做空惩罚（精确版v2）
    _rsi_chop = float(ms.get('rsi_1h', 50) if ms else 50)
    if ('CHOP' in _regime_upper and not _is_long_signal
            and _rsi_chop < 25 and not _direction_block and score > 0):
        score = int(score * 0.88)
        breakdown['FIX2_CHOP追空'] = f'×0.88 (CHOP RSI={_rsi_chop:.0f}<25 超卖追空)'


    # [UP-NODE-v4] 梵天大脑v4注入
    # ─────────────────────────────────────────────────────
    _atr_v4 = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    _rsi_v4 = float(ms.get('rsi_1h', 50) if ms else 50)
    _is_long_v4 = (signal_dir == 'LONG')
    # [潜力释放 P1 2026-07-12] N16暴涨猎手豆免：与FIX1共用同一豆免标记
    _n16_pump_exempt = _pump_hunter_exempt  # 继承FIX1的判断结果

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 ATR体制过滤器] N16完整版 — 基于 N16_atr_layers 铁证
    # CHOP 0.005~0.015最优(PF=1.44~1.98) | BULL_TREND(牛市趋势) <0.010禁区(PF=0.567)
    # ══════════════════════════════════════════════════════════════
    _atr_regime_tag = ''
    if 'BULL_TREND' in _regime_upper:
        # BULL_TREND(牛市趋势) ATR禁区：<0.010 PF=0.567（铁证）
        if _atr_v4 < 0.010 and not _direction_block and score > 0:
            if _n16_pump_exempt:
                _atr_regime_tag = f'N16_豁免(暴涨猎手) ATR={_atr_v4:.4f} FR负值压缩=爆发前元'
            else:
                score = int(score * 0.80)
                _atr_regime_tag = f'N16_ATR禁区 ×0.80 (BULL ATR={_atr_v4:.4f}<0.010, PF=0.567)'
        # BULL_TREND(牛市趋势) ATR黄金区：0.010~0.015
        elif 0.010 <= _atr_v4 <= 0.015 and _is_long_v4 and not _direction_block and score > 0:
            score = min(int(score * 1.05), 175)
            _atr_regime_tag = f'N16_ATR黄金 ×1.05 (BULL ATR={_atr_v4:.4f} PF=1.087)'
    elif 'CHOP' in _regime_upper:
        # CHOP最优区：0.005~0.015 PF=1.44~1.98
        if 0.005 <= _atr_v4 <= 0.015 and not _direction_block and score > 0:
            bonus = int((1.98 - max(0, (_atr_v4 - 0.005) / 0.010)) * 2)  # 动态加分
            score = min(score + bonus, 175)
            _atr_regime_tag = f'N16_CHOP优区 +{bonus} (ATR={_atr_v4:.4f} PF≈1.5+)'
        # CHOP大ATR区：>0.015 PF=1.013接近无效
        elif _atr_v4 > 0.020 and not _direction_block and score > 0:
            score = int(score * 0.90)
            _atr_regime_tag = f'N16_CHOP大ATR ×0.90 (ATR={_atr_v4:.4f}>0.020)'
    elif 'BEAR' in _regime_upper:
        # BEAR体制 ATR有效区：0.007~0.025
        if _atr_v4 < 0.007 and not _direction_block and score > 0:
            score = int(score * 0.88)
            _atr_regime_tag = f'N16_BEAR低ATR ×0.88 (ATR={_atr_v4:.4f}<0.007)'
    if _atr_regime_tag:
        breakdown['N16_ATR体制'] = _atr_regime_tag

    # N14: 体制切换时机强化 v2 [设计院P0b封印 2026-06-27]
    # 达摩院铁证：5~10min黄金窗口 PF=1.625，15~25min死亡窗口 PF=0.81
    try:
        import json as _j14, time as _t14
        _dm14 = _j14.loads(open('data/dharma_runtime.json').read())
        _rt14 = _dm14.get('regime_timing', {})
        _rss14_path = __import__('pathlib').Path('data/regime_switch_state.json')
        _n14_delta = 0
        _n14_label = ''
        if _rss14_path.exists():
            _rss14 = _j14.loads(_rss14_path.read_text())
            _last_switch = _rss14.get('last_switch_ts', 0)
            _cur_regime14 = _rss14.get('current_regime', '')
            _dist_min = (_t14.time() - _last_switch) / 60 if _last_switch else 9999
            # 匹配达摩院时段矩阵
            for _window, _wdata in _rt14.items():
                if '~' not in str(_window): continue
                try:
                    _wlo, _whi = [float(x) for x in str(_window).split('~')]
                    if _wlo <= _dist_min < _whi:
                        _n14_delta = int(_wdata.get('delta', 0))
                        _n14_label = _wdata.get('label', '')
                        break
                except Exception as _e:
                        if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                            pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        # 当无regime_switch_state时，保d原 N14逻辑
        elif 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42:
            _n14_delta = 5
            _n14_label = '熊市边界早鸟(fallback)'
        if _n14_delta != 0:
            score = max(0, min(int(score) + _n14_delta, 175))
            breakdown['N14_体制切换时机'] = f'{_n14_delta:+d} ({_n14_label} dist={_dist_min if "_dist_min" in dir() else "?":.0f}min PF={_rt14.get(str(int(_dist_min))+"~"+str(int(_dist_min)+5),{}).get("pf","?")})'
            pass  # [静默] f'[N14-Timing] {_sym}: {_n14_delta:+d}分 {_n14_label}'
    except Exception:
        # 安全回退：保留原 N14逻辑
        if 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42 and _atr_v4 > 0.012 and not _direction_block and score > 0:
            score = min(int(score) + 5, 175)
            breakdown['N14_熊转边界'] = '+5 (熊市边界早鸟 PF=1.625)'

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 N15评分分层仓位映射] — 基于 N15_kelly 铁证
    # 150~160分: PF=1.538 Calmar=5.16（最优） | 130~140: PF=1.02（噪声）
    # ══════════════════════════════════════════════════════════════
    _score_tier_tag = ''
    if score >= 165:
        _kelly_tier = 'S+';  _pos_tier = 0.08  # 极高分：最大仓位
        _score_tier_tag = f'N15_S+层({score}分) pos={_pos_tier:.0%}'
    elif score >= 158:
        _kelly_tier = 'S';   _pos_tier = 0.065  # S1标准仓
        _score_tier_tag = f'N15_S层({score}分) pos={_pos_tier:.0%}'
    elif score >= 150:
        _kelly_tier = 'S2';  _pos_tier = 0.05   # [武曲OOS✅] S2层实盘 WR=66.7% PF=3.575 n=72（实盘运行样本，非离线训练样本，待积累至n≥500增强可信度）
        _score_tier_tag = f'N15_S2层({score}分) pos={_pos_tier:.0%} [武曲认证]'
    elif score >= 130:
        _kelly_tier = 'B';   _pos_tier = 0.02   # 极轻仓观察
        _score_tier_tag = f'N15_B层({score}分) pos={_pos_tier:.0%}'
    else:
        _kelly_tier = 'C';   _pos_tier = 0.0
    # 将仓位分级注入 extra_data 供执行层使用
    if extra_data is not None and isinstance(extra_data, dict):
        extra_data['score_tier'] = _kelly_tier
        extra_data['score_pos']  = _pos_tier
    breakdown['N15_分层仓位'] = _score_tier_tag if _score_tier_tag else f'N15_C层({score}分) 不执行'

    # ── [GAP2 仓位管理器 2026-06-03] 中仓解锁 + 动态仓位 ─────────────────────
    # 武曲Paper 200笔+WR≥75% → 倍数1.5x | 3连胜 → 倍数2.0x
    try:
        import sys as _pm_sys, os as _pm_os
        _pm_root = _pm_os.path.dirname(_pm_os.path.dirname(_pm_os.path.abspath(__file__)))
        if _pm_root not in _pm_sys.path:
            if _pm_root not in _pm_sys.path: _pm_sys.path.insert(0, _pm_root)
        from scripts.position_manager import get_position_multiplier as _get_pm
        _pm_mult = _get_pm()
        if _pm_mult > 1.0 and _pos_tier > 0:
            _pos_tier_adjusted = round(_pos_tier * _pm_mult, 4)
            if extra_data is not None and isinstance(extra_data, dict):
                extra_data['score_pos']   = _pos_tier_adjusted
                extra_data['pos_mult']    = _pm_mult
            breakdown['N15_仓位倍数'] = (
                f'×{_pm_mult} → pos={_pos_tier_adjusted:.1%} '
                f'({"中仓已解锁" if _pm_mult==1.5 else "连胜加仓"})'
            )
    except Exception as _pm_e:
        pass   # 静默失败，不影响主流程
    # ── [END 仓位管理器] ──────────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 M09] 品种×维度权重修正层
    # 来源：full_universe_backtest dim_contrib铁证
    # BTC谐波-0.381/宏观-0.256清零 | ETH背离+0.277→×2.0 | SOL期权-0.093→×0.5
    # 已通过DharmaBus总线写入，此处读取并追溯调整评分
    # 设计院升级 2026-06-27: 无score下限限制，所有体制均触发
    # ══════════════════════════════════════════════════════════════
    try:
        import os as _os2, sys as _sys2
        _bus_dir2 = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), '..')
        if _bus_dir2 not in _sys2.path: _sys2.path.insert(0, _bus_dir2)
        from dharma.dharma_bus import get_dim_weight as _get_dw
        _m09_dims = {
            '关键位精确度': breakdown.get('关键位精确度', 0),
            '形态成熟度':   breakdown.get('形态成熟度',   0),
            '清算/OI':      breakdown.get('清算/OI',      0),
            '谐波+多周期':  breakdown.get('谐波+多周期',  0),
            'L2+贝叶斯+宏观': breakdown.get('L2+贝叶斯+宏观', 0),
            '量能验证':     breakdown.get('量能验证',     0),
            '动量背离':     breakdown.get('动量背离',     0),
            '期权+订单流':  breakdown.get('期权+订单流',  0),
            'LSTM+NLP情绪': breakdown.get('LSTM+NLP情绪', 0),
        }
        _m09_delta = 0
        _m09_log = []
        for _dim, _orig in _m09_dims.items():
            if _orig <= 0: continue
            _w = _get_dw(_sym, _dim)
            if _w == 1.0: continue
            _adjusted = round(_orig * _w)
            _delta = _adjusted - _orig
            _m09_delta += _delta
            if abs(_delta) >= 1:
                _m09_log.append(f'{_dim}:{_orig}→{_adjusted}(×{_w})')
                # 同步更新breakdown实际分数字段
                breakdown[_dim] = _adjusted
        if _m09_delta != 0:
            score = max(0, min(score + _m09_delta, 175))
            breakdown['M09_维度权重'] = f'Δ{_m09_delta:+d}分 [{" | ".join(_m09_log[:4])}]'
            pass  # [静默] f'[M09-DimWeight] {_sym}: {_m09_delta:+d}分 | {" | ".join(_m09_log)}'
    except Exception as _e09:
        pass
    # ══════════════════════════════════════════════════════════════

    # ─── [设计院 2026-06-30 P1-C] WICK_HUNTER 第10因子 ──────────────────────
    # 根因：系统缺乏15m插针信号识别，58,850/58,888极端下影线未被捕捉
    # 铁证：22:45 L:58888 体/影比=0.12（教科书级插针），02:00 L:58850 振幅644
    # 逻辑：下影线主导（>实体+上影线×1.5）+ 触碰近期低点支撑 + 收盘收复 → +20分
    # fail-safe：异常静默，不阻断主流程
    try:
        _k15m = extra_data.get('_klines_15m') if extra_data else None
        if _k15m and len(_k15m.get('c', [])) >= 5:
            _wh_o = _k15m['o'][-1]
            _wh_h = _k15m['h'][-1]
            _wh_l = _k15m['l'][-1]
            _wh_c = _k15m['c'][-1]
            _wh_body  = abs(_wh_c - _wh_o)
            _wh_upper = _wh_h - max(_wh_o, _wh_c)
            _wh_lower = min(_wh_o, _wh_c) - _wh_l
            _wh_total = _wh_h - _wh_l
            _wh_score = 0
            if _wh_total > 0:
                if signal_dir == 'LONG':
                    # 条件1：下影线主导（>实体+上影线的1.5倍）
                    if _wh_lower > (_wh_body + _wh_upper) * 1.5:
                        _support_ref = min(_k15m['l'][-20:]) if len(_k15m['l']) >= 20 else _wh_l
                        # 条件2：触碰近期支撑（±0.3%）
                        if _wh_l <= _support_ref * 1.003:
                            # 条件3：收盘收复支撑上方
                            if _wh_c > _support_ref * 1.004:
                                # [达摩院验证 2026-06-30] LONG插针需额外满足:
                                # 体/影比<0.25(防假插针) + DISCOUNT区(系数由区间路由提供)
                                _in_discount = breakdown.get('区间Zone_v2', '').startswith('DISCOUNT')
                                _extreme_wick = (_wh_body / _wh_total < 0.25)
                                if _in_discount and _extreme_wick:
                                    _wh_score = 25 if (_wh_body / _wh_total < 0.15) else 20
                                    breakdown['WICK_HUNTER_LONG'] = f'+{_wh_score}(下影{_wh_lower:.0f}pts 体影比{_wh_body/_wh_total:.2f} DISCOUNT区联合)'
                                elif _extreme_wick:
                                    # 非DISCOUNT区但是极端插针，小加分
                                    _wh_score = 10
                                    breakdown['WICK_HUNTER_LONG_WEAK'] = f'+{_wh_score}(下影 体影比{_wh_body/_wh_total:.2f} 非DISCOUNT小加分)'
                elif signal_dir == 'SHORT':
                    # 条件1：上影线主导（>实体+下影线的2.0倍）
                    if _wh_upper > (_wh_body + _wh_lower) * 2.0:
                        _resist_ref = max(_k15m['h'][-20:]) if len(_k15m['h']) >= 20 else _wh_h
                        # 条件2：触碰近期阻力（±0.3%）
                        if _wh_h >= _resist_ref * 0.997:
                            # 条件3：收盘回落阻力下方
                            if _wh_c < _resist_ref * 0.996:
                                _wh_score = 15
                                breakdown['WICK_HUNTER_SHORT'] = f'+{_wh_score}(上影{_wh_upper:.0f}pts 体影比{_wh_body/_wh_total:.2f})'
            if _wh_score > 0:
                score += _wh_score
    except Exception:
        pass
    # ─── [P1-C END] ──────────────────────────────────────────────────────────

    # ══ [设计院 2026-06-30 全量接入 N10-A] CVD 订单流因子 ════════════════════
    # 模块: cvd_engine · 订单流核心指标，多周期CVD累积成交量差
    # 达摩院铁证：CVD顺势+15分 / 逆势-10分
    try:
        from cvd_engine import cvd_score_for_signal as _cvd_fn
        _cvd_score, _cvd_notes = _cvd_fn(ms.get('symbol', ''), signal_dir)
        if _cvd_score != 0:
            score += _cvd_score
            breakdown['CVD订单流'] = f'{_cvd_score:+d} ' + ('; '.join(_cvd_notes[:2]) if _cvd_notes else '')
    except Exception:
        pass
    # ══ [N10-A END] ══════════════════════════════════════════════════════════

    # ══ [设计院 2026-08-12 苏摩111封印] HAR-RV波动率预测接入 ══
    # 替代失效的Kronos torch依赖，学术界黄金标准，纯numpy/statsmodels
    try:
        from har_rv_engine import get_har_rv as _harv_fn
        _harv = _harv_fn(_sym)
        breakdown['HAR-RV波动率'] = f"{_harv.get('score_adj', 0):+d} {_harv.get('regime_vol','')} RV={_harv.get('rv_forecast',0):.4f}"
        _harv_adj = int(_harv.get('score_adj', 0))
        if _harv_adj != 0:
            score += _harv_adj
        # 更新p_up供后续Kronos降级使用
        _harv_p_up = _harv.get('p_up_proxy', 0.5)
        ms['_harv_p_up'] = _harv_p_up
    except Exception as _harv_e:
        pass

    # ══ [设计院 2026-08-12 苏摩111封印] Hurst指数体制验证接入 ══
    # 给CHOP_MID识别加数学底座，防止趋势策略在随机游走区间错误触发
    try:
        from hurst_engine import get_hurst as _hurst_fn
        _hurst_regime = ms.get('regime', 'CHOP_MID')
        _hurst_res = _hurst_fn(_sym, _hurst_regime)
        _hurst_adj = int(_hurst_res.get('score_adj', 0))
        breakdown['Hurst体制验证'] = _hurst_res.get('note', '')
        if _hurst_adj != 0:
            score += _hurst_adj
    except Exception as _hurst_e:
        pass

    # ══ [设计院 2026-08-12 苏摩111封印] Volume Profile成交量分布接入 ══
    # 根因：volume_profile.py存在但未接入，POC价格磁力区信息缺失
    try:
        from volume_profile import get_vp_score as _vp_fn
        _vp_price = float(ms.get('close', ms.get('price', 0)) or 0)
        _vp_score, _vp_reason = _vp_fn(_sym, _vp_price, signal_dir)
        if _vp_score != 0:
            score += _vp_score
            breakdown['VolProfile密度'] = f'{_vp_score:+d} {_vp_reason[:40]}'
    except Exception as _vp_e:
        pass  # VP接入失败不阻断

    # ══ [设计院 2026-06-30 全量接入 N10-B] 实时清算流 因子 ════════════════════
    # 模块: realtime_liq_tracker · 追踪近5分钟三所清算流方向
    # 逻辑：同向清算涌入（如大量多单被爆仓时做空）→ 加分
    try:
        from realtime_liq_tracker import get_liq_score as _liq_score_fn
        _liq_adj, _liq_desc = _liq_score_fn(ms.get('symbol', ''), signal_dir)
        if _liq_adj != 0:
            score += _liq_adj
            breakdown['清算流追踪'] = f'{_liq_adj:+d} {_liq_desc[:50]}'
    except Exception:
        pass
    # ══ [N10-B END] ══════════════════════════════════════════════════════════

    # [v13.0] 单一化输出裁决：评分决定唯一行动，不再并列多方案
    # 裁决规则：评分主导， R:R 在 analyze() 层做最终覆盖
    # [v14.0 设计院 2026-07-08] action阈值与宪法门槛对齐
    # 宪法：valid_signal需score≥155；action=ENTER不应在score<138时触发
    # 修复：ENTER_FULL≥155，ENTER≥138（铁证线），WATCH≥100，低分→WATCH_ONLY
    if score >= 155:
        grade = '🔴神级';  kelly_mult = 2.0;  action = 'ENTER_FULL'  # [N18] 顶级信号全仓
    elif score >= 138:
        grade = '🟠极强';   kelly_mult = 1.5;  action = 'ENTER'       # [N18] 铁证线以上
    elif score >= 130:
        grade = '🟡强+';   kelly_mult = 1.0;  action = 'ENTER_WATCH'  # [v7.0 2026-07-11] 六方封印 130-138新层
    elif score >= 110:
        grade = '🟡强';    kelly_mult = 0.5;  action = 'WATCH'        # [v14.0] 110-130降为WATCH
    elif score >= 80:
        grade = '🔵中等';   kelly_mult = 0.3;  action = 'WATCH'
    else:
        grade = '⚫放弃';   kelly_mult = 0.0;  action = 'SKIP'

    return {
        'total':      score,
        'score':      score,    # [P1修复 2026-07-12] 补充score别名 — analyze()/run_analysis读.get('score')，原只有'total'导致永远None
        'max':        150,
        'grade':      grade,
        'grade_num':  score,   # [设计院 2026-06-30 G修复] brahma_analyze.py期期得此字段，补入整数评分
        'kelly_mult': kelly_mult,
        'action':     action,   # 注意：若params.valid=False，analyze()会覆盖此字段
        'breakdown':  breakdown,
    }

# ═══════════════════════════════════════════════════════════════
# 精确交易参数生成
# ═══════════════════════════════════════════════════════════════

def _nearest_swing_above(swing_highs: list, entry: float) -> float:
    """找到入场价上方最近的摆动高点（用于做空止损）"""
    candidates = [v for v in swing_highs if v > entry]
    return min(candidates) if candidates else entry * 1.015

def _nearest_swing_below(swing_lows: list, entry: float) -> float:
    """找到入场价下方最近的摆动低点（用于做多止损）"""
    candidates = [v for v in swing_lows if v < entry]
    return max(candidates) if candidates else entry * 0.985

def calc_trade_params(ms: dict, smc: dict, signal_dir: str,
                      mtf_result: dict = None) -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _ctp_entry(ms, smc, signal_dir, mtf_result)
    raise ImportError('brahma_core_entry not available')


def rebase_params(params: dict, current_price: float,
                  symbol: str = '') -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _rbp_entry(params, current_price, symbol)
    raise ImportError('brahma_core_entry not available')


# ═══════════════════════════════════════════════════════════════
# 主分析入口
# ═══════════════════════════════════════════════════════════════

def analyze(symbol: str, signal_dir: str = None, deep: bool = False) -> dict:
    """
    梵天大脑主入口
    symbol:     交易对（如 ETHUSDT）
    signal_dir: 强制方向（LONG/SHORT），None=自动判断
    deep:       True=深度分析模式，跳过方向中性快速退出，返回完整数据
    """
    t0 = time.time()
    _sym = symbol.upper()
    pass  # [静默] f'[BrahmaBrain] 开始分析 {_sym} dir={signal_dir or "AUTO"}'

    # ══ [设计院 2026-06-30 P3] BrahmaBus 数据总线初始化 ══════════════════════
    # 模块: brahma_bus · TTL缓存单例，0.01ms命中 vs HTTP 50ms
    # 仅初始化，后续模块可通过 BrahmaBus() 直接获取缓存数据
    try:
        from brahma_bus import BrahmaBus as _BBus
        _bus = _BBus()
        _bus.invalidate(_sym)   # 强制刷新当前标的缓存
    except Exception:
        pass
    # ══ [BrahmaBus END] ════════════════════════════════════════════════════════

    # [价格修复 v1.1] analyze()入口：强制刷新实时价格到live_prices.json，确保降级链拿到最新价
    # 设计院 2026-06-29 · 根因：ws_guardian停运时live_prices.json超期→降级到ticker缓存价
    try:
        import sys as _lpf_sys, os as _lpf_os
        _lpf_base = _lpf_os.path.dirname(_lpf_os.path.abspath(__file__))
        if _lpf_base not in _lpf_sys.path:
            if _lpf_base not in _lpf_sys.path: _lpf_sys.path.insert(0, _lpf_base)
        from live_price_feed import bulk_update_from_api as _lpf_bulk
        _lpf_bulk([_sym])
        pass  # [静默] f'[PriceFix] {_sym} 入口强制刷新价格 ✅'
    except Exception as _lpf_e:
        pass  # [静默] f'[PriceFix] 价格刷新异常（不阻断）: {_lpf_e}'

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║ Step1-3: 市场分析/方向/SMC                                        ║
    # ║ [封印 2026-08-11] → brahma_core_analyze_steps.py                  ║
    # ╚══════════════════════════════════════════════════════════════════╝
    try:
        from brahma_brain.brahma_core_analyze_steps import (
            _analyze_step1, _analyze_step2, _analyze_step3)
    except ImportError:
        from brahma_core_analyze_steps import (
            _analyze_step1, _analyze_step2, _analyze_step3)

    _r1 = _analyze_step1(symbol, signal_dir)
    ms = _r1['ms']; _cv_adj = _r1['_cv_adj']
    _cv_verdict = _r1['_cv_verdict']; _causal_v_result = _r1['_causal_v_result']

    _r2 = _analyze_step2(symbol, ms, signal_dir, deep)
    signal_dir = _r2['signal_dir']

    price = float(ms.get('price', 0))
    _r3 = _analyze_step3(symbol, ms, signal_dir, price)
    smc = _r3['smc']; _smc_4h = _r3['_smc_4h']
    _mtf_result = _r3['_mtf_result']; price = _r3['price']

    # ╔══════════════════════════════════════════════════════════════╗
    # ║ Step4: extra_data 构建层                                      ║
    # ║ [封印 2026-08-11] → brahma_core_step4.py                      ║
    # ╚══════════════════════════════════════════════════════════════╝
    try:
        from brahma_brain.brahma_core_step4 import _analyze_step4
    except ImportError:
        from brahma_core_step4 import _analyze_step4
    _r4       = _analyze_step4(symbol, ms, smc, signal_dir, price, _causal_v_result)
    extra_data = _r4['extra_data']
    _bd        = _r4.get('_bd', {})
    _spec      = _r4.get('_spec', {})
    _sm        = _r4.get('_sm', {})
    # [Step4防护] Step4提取后，为局部变量提供默认值，防止UnboundLocalError
    _regime_str = str(ms.get('regime', 'UNKNOWN') if ms else 'UNKNOWN')
    params: dict = {}

    # Step 5: 共振评分
    cf = confluence_score(ms, smc, signal_dir, extra_data)
    # [根本修复 2026-07-12 设计院封印] cf 将在_result初始化后立即写入
    # 见 L4550后: _result['confluence'] = cf  (平现注入，不在这里操作_result)

    # ── [因果AI P0-B] Counterfactual Score Check ───────────────
    # 设计院因果增强 v1.0 · 2026-06-18
    # ── P2-A 多周期权重调整（设计院六方联合 2026-07-11）────────────
    # 根据合约流动性层级和信号周期，对最终score做轻度调整
    # L1/L2主流=不变, L4小币4H信号=×0.85, L4小币15M=×1.05
    try:
        from confluence_tf_weights import get_score_multiplier as _get_tf_mult
        _ptf = ms.get('primary_tf', '1h') or '1h'
        _ssrc = extra_data.get('signal_source', 'default') if extra_data else 'default'
        _tf_mult = _get_tf_mult(ms.get('symbol', ''), score, _ptf, _ssrc)
        if abs(_tf_mult - 1.0) > 0.01:
            _score_before_tf = score
            score = round(score * _tf_mult, 1)
            breakdown['TF权重调整'] = f'×{_tf_mult:.2f} {_score_before_tf:.0f}→{score:.0f}'
    except Exception:
        pass  # TF权重调整失败不影响主流程

    # ── [P2-A增强版 苏摩111批准 2026-07-11] confluence_by_tf 多周期共振奖励 ──────
    # 架构: 分析breakdown各维度所属周期 → 计算共振奖励(+0~+8)
    # 双周期共振=+3, 三周期=+6, 四周期全共振=+8
    # L4/L5小币奖励减半（高周期信号可信度低）
    try:
        import sys as _p2a_sys, os as _p2a_os
        if _p2a_os.path.dirname(_p2a_os.path.abspath(__file__)) not in _p2a_sys.path: _p2a_sys.path.insert(0, _p2a_os.path.dirname(_p2a_os.path.abspath(__file__)))
        from confluence_by_tf import apply_tf_confluence as _apply_tf_cf
        _ptf2  = ms.get('primary_tf', '1h') or '1h'
        _ssrc2 = extra_data.get('signal_source', 'default') if extra_data else 'default'
        _sym2  = ms.get('symbol', '') or ''
        _adj_score, _tf_meta = _apply_tf_cf(
            float(score), breakdown, _sym2, signal_dir, _ptf2, _ssrc2
        )
        if _tf_meta.get('tf_boost', 0) > 0:
            score = _adj_score
            breakdown['TF共振奖励'] = f"+{_tf_meta['tf_boost']} [{_tf_meta['summary']}]"
            if extra_data is not None:
                extra_data['tf_confluence'] = _tf_meta
    except Exception:
        pass  # 多周期共振失败不影响主流程
    # ── [P2-A END] ────────────────────────────────────────────────────────────

    # 对 score ≥ 100 的信号执行维度因果归因，识别相关性掃车维度
    # fail-safe: 异常不阻断主流程
    try:
        import sys as _cfc_sys, os as _cfc_os
        _cfc_root = _cfc_os.path.dirname(_cfc_os.path.abspath(__file__))
        if _cfc_root not in _cfc_sys.path:
            if _cfc_root not in _cfc_sys.path: _cfc_sys.path.insert(0, _cfc_root)
        from counterfactual_score_check import check as _cfc_check
        _cf_score = float(cf.get('score', 0) or 0)
        if _cf_score >= 100:
            _cfc_result = _cfc_check(cf, signal_dir, ms.get('regime', ''), timeout_ms=80)
            _cfc_adj = _cfc_result.get('score_adj', 0)
            _cfc_verdict = _cfc_result.get('verdict', 'NEUTRAL')
            if _cfc_adj != 0:
                cf['score'] = _cf_score + _cfc_adj
                cf.setdefault('breakdown', {})['_counterfactual'] = (
                    f'{_cfc_adj:+d}(因果归因:{_cfc_verdict} '
                    f'因果维度{_cfc_result.get("causal_ratio",0):.0%})'
                )
            extra_data['counterfactual'] = _cfc_result
    except Exception as _cfc_e:
        pass  # [静默] f'[CounterfactualCheck] ⚠ 异常（不阻断）: {_cfc_e}'

    # ── Causal Verifier 评分叠加 ─────────────────────────────
    # 将 P0-A 的 score_adj 运用到最终评分
    _cv_adj = extra_data.get('causal_verifier', {}).get('score_adj', 0)
    if _cv_adj != 0:
        _cf_score_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _cf_score_pre + _cv_adj
        cf.setdefault('breakdown', {})['_causal_regime'] = (
            f'{_cv_adj:+d}(体制因果:{extra_data.get("causal_verifier",{}).get("verdict","?")} '
            f'conf={extra_data.get("causal_verifier",{}).get("causal_confidence",0):.2f})'
        )
        pass  # [静默] f'[CausalVerifier] {_sym} 评分叠加: {_cf_score_pre:.0f}→{cf["score"]:.0f} ({_cv_adj:

    # ── [s_cross 2026-07-01] 跨所FR+Basis 评分叠加 ──────────────────
    _cfb_adj = extra_data.get('cross_fr_basis', {}).get('score_adj', 0)
    if signal_dir != 'SHORT':
        _cfb_adj = -_cfb_adj  # 做多时反转：FR高时做多不利
    if _cfb_adj != 0:
        _cfb_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _cfb_pre + _cfb_adj
        cf.setdefault('breakdown', {})['_cross_fr_basis'] = (
            f'{_cfb_adj:+d}(FR均值={extra_data.get("cross_fr_basis",{}).get("fr_avg",0):.4f}% '
            f'Basis={extra_data.get("cross_fr_basis",{}).get("basis_pct",0):.3f}%)'
        )

    # ── [s_options 2026-07-01] Deribit P/C OI 评分叠加 ──────────────────
    _dpc_adj = extra_data.get('deribit_pc', {}).get('score_adj', 0)
    if signal_dir != 'SHORT':
        _dpc_adj = -_dpc_adj  # 做多时反转
    if _dpc_adj != 0:
        _dpc_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _dpc_pre + _dpc_adj
        cf.setdefault('breakdown', {})['_options_pc'] = (
            f'{_dpc_adj:+d}(P/C={extra_data.get("deribit_pc",{}).get("pc_oi_ratio",0):.2f} '
            f'{extra_data.get("deribit_pc",{}).get("signal","")})'
        )

    # ── [s_macro_v2 2026-07-01] DXY实时+纳指+BTC.D 评分叠加 ────────────
    _mv2_adj = extra_data.get('macro_v2', {}).get('score_addon', 0)
    if _mv2_adj != 0:
        _mv2_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _mv2_pre + _mv2_adj
        cf.setdefault('breakdown', {})['_macro_v2'] = (
            f'{_mv2_adj:+d}(' + ' | '.join(extra_data.get('macro_v2', {}).get('notes', [])[:2]) + ')'
        )
        print(f'[s_macro_v2] {_sym} 宏观叠加: {_mv2_pre:.0f}→{cf["score"]:.0f} ({_mv2_adj:+d})')

    # ── [s_smart_money 2026-07-01] 聊明錢流向分析 ───────────────────────
    # Glassnode盲区替代方案：大户持仓比+大户-散户背离 = 巨鲸流向代理指标
    try:
        from smart_money_engine import get_smart_money_signal as _gsms
        _sm = _gsms(_sym)
        extra_data['smart_money'] = _sm
        _sm_adj = _sm.get('score_adj', 0)
        if signal_dir != 'SHORT':
            _sm_adj = -_sm_adj  # 做多时反转
        if _sm_adj != 0 and _sm.get('confidence', 0) >= 0.5:
            _sm_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _sm_pre + _sm_adj
            cf.setdefault('breakdown', {})['_smart_money'] = (
                f'{_sm_adj:+d}(大户持仓={_sm.get("big_pos_long",0.5):.0%} '
                f'背离={_sm.get("whale_retail_gap",0):+.3f})'
            )
            print(f'[s_smart] {_sym} 聊明錢: {_sm_pre:.0f}→{cf["score"]:.0f} ({_sm_adj:+d}) | {_sm.get("note","")[:60]}')
    except Exception:
        pass
    params = calc_trade_params(ms, smc, signal_dir, mtf_result=_mtf_result)

    # [N17专项] 标的专属SL/TP参数覆盖
    # [WFV-v4.0 2026-05-28] 达摩院高强度训练 200轮Bootstrap认证
    # 全局冠军: RSI<20/>>85 SL=0.6x TP=4.0x  核心OOS PF=1.347 Bootstrap=MEDIUM
    # [N17专项] 标的专属SL/TP参数覆盖
    # [WFV-v4.0 2026-05-28] 达摩院高强度训练 200轮Bootstrap认证
    # 全局冠军: RSI<20/>>85 SL=0.6x TP=4.0x  核心OOS PF=1.347 Bootstrap=MEDIUM
    # [M07时间效应 ERR-012 2026-05-30] 10万次训练M07节点认证
    # 最佳时段(UTC): 18H/22H/11H/7H → EV高40%+  最差月份: 8/9月 → 降权
    # 最佳交易日: 周四/周三/周一
    import datetime as _dt_m07
    _now_m07 = _dt_m07.datetime.utcnow()
    _hour_m07 = _now_m07.hour
    _wday_m07 = _now_m07.weekday()  # 0=Mon, 3=Thu
    _month_m07 = _now_m07.month
    _time_mult = 1.0
    _time_tag = ''
    # ── M07/M06 后置修正：操作 cf['total'] 和 cf['breakdown']（正确作用域）
    # 最佳时段加权 +5分
    if _hour_m07 in (18, 22, 11, 7, 20):
        cf['total'] = cf.get('total', 0) + 5
        _time_tag += f'M07最佳时段(UTC{_hour_m07}H)+5 '
        cf.setdefault('breakdown', {})['M07时间效应'] = f'+5(UTC{_hour_m07}H黄金时段 EV+40%)'
    # 最差月份降权 -5分
    if _month_m07 in (8, 9):
        cf['total'] = max(0, cf.get('total', 0) - 5)
        _time_tag += f'M07夏季降权({_month_m07}月)-5 '
        cf.setdefault('breakdown', {})['M07时间效应'] = cf.get('breakdown',{}).get('M07时间效应','') + f'-5({_month_m07}月低流动性)'
    # 最佳交易日 +3分（周四=3, 周三=2, 周一=0）
    if _wday_m07 in (3, 2):  # 周四/周三
        cf['total'] = cf.get('total', 0) + 3
        _time_tag += f'M07最佳交易日+3 '
        cf.setdefault('breakdown', {})['M07时间效应'] = cf.get('breakdown',{}).get('M07时间效应','') + f'+3(周{["一","二","三","四","五"][_wday_m07]})'
    if _time_tag:
        cf.setdefault('breakdown', {}).setdefault('M07时间效应', _time_tag.strip())

    # [M06相关系数惩罚] 双向等概率品种，做空信号无统计优势
    # [2026-08-12 苏摩111修复P5] -5 → -2，惩罚过重导致有效信号被压制
    _m06_zero_coef = {'ETHUSDT', 'ATOMUSDT'}
    _cur_score = cf.get('total', 0)
    if _sym in _m06_zero_coef and _cur_score > 0:
        _pen = 2
        cf['total'] = max(0, _cur_score - _pen)
        cf.setdefault('breakdown', {})['M06相关惩罚'] = f'-{_pen}({_sym} coef=0 双向等概率)'

    # [N17专项 v2.0 ERR-012 2026-05-30] 10万次训练冠军参数全面落地
    # 全局冠军: thr=160, sl=1.5x, mh=12H → 全局PF=1.647 WR=46.7% CI=[1.454,1.860] P(PF>1)=100%
    # 原则: sl从「噪音区外」设置(ATR×1.5+), mh对齐东西方市场完整轮换周期(12~16H)
    _sym_spec_map = {
        # S+级 — 训练PF>=3.0，冠军参数下高度稳定
        'LINKUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override':  8, 'pf_evidence': 3.585, 'grade': 'S+'},  # 训练PF=3.585 WR=58.7% N=46
        'DOGEUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 3.234, 'grade': 'S+'},  # 训练PF=3.234 WR=62.3% N=53 [ERR-011修复sl0.8→1.5]
        'DOTUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 2.388, 'grade': 'S+'},  # 训练PF=2.388 WR=50.7%
        'SUIUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 2.382, 'grade': 'S+'},  # 训练PF=2.382
        # S级 — 训练PF 1.5~2.5，核心主力品种
        'SOLUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 2.064, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 训练认证
        # ETH/LTC: 体制动态SL（设计院 2026-05-30）
        # CHOP体制sl=1.2x（防止贪婪止据）、BEAR趋势体制sl=2.0x（顺势止据）
        'ETHUSDT':  {'sl_mult_override': 2.8, 'tp_mult_override': 1.8, 'mh_override': 18, 'pf_evidence': 1.735, 'grade': 'S',
                     '_regime_sl': {'CHOP_LOW':1.2,'CHOP_MID':1.2,'CHOP_HIGH':1.5,'BEAR_EARLY':1.5,'BEAR_TREND':2.0,'BEAR_CRASH':2.0,'BEAR_RECOVERY':1.5,'BULL_TREND':1.8,'BULL_EARLY':1.8,'BULL_PEAK':1.8,'BULL_CORRECTION':1.5}},  # [v7-2026-06-14] WFV12/12 sl=2.8x tp=1.8x hold=18H EV=+0.397%/笔 WR=68.4%
        'BNBUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.750, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 mh8→16
        'BTCUSDT':  {'sl_mult_override': 2.527, 'tp_mult_override': 1.964, 'mh_override': 17, 'pf_evidence': 1.662, 'grade': 'S'},  # [v7-2026-06-14] WFV12/12 sl=2.527x tp=1.964x hold=17H EV=+0.515%/笔 WR=65.7%
        'ADAUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.968, 'grade': 'S'},   # [ERR-012] sl0.6→1.5
        'ATOMUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.961, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 mh8→16
        # A级 — 训练PF 1.2~1.5
        'AVAXUSDT': {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.303, 'grade': 'A'},   # [ERR-012] sl0.6→2.0
        'LTCUSDT':  {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.398, 'grade': 'A',
                     '_regime_sl': {'CHOP_LOW':1.2,'CHOP_MID':1.2,'CHOP_HIGH':1.5,'BEAR_EARLY':1.5,'BEAR_TREND':2.0,'BEAR_CRASH':2.0,'BEAR_RECOVERY':1.5,'BULL_TREND':1.5,'BULL_EARLY':1.5,'BULL_PEAK':1.8,'BULL_CORRECTION':1.5}},
        'NEARUSDT': {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.441, 'grade': 'A'},   # [ERR-012] sl0.6→2.0 mh8→16
        # 观察级 — 训练PF<1.2，谨慎
        'XRPUSDT':  {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override':  8, 'pf_evidence': 0.888, 'grade': 'WATCH'},  # 训练PF=0.888 监管风险高，仅保留不封禁
        'INJUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.712, 'grade': 'S'},   # 训练PF=1.712
        'OPUSDT':   {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.798, 'grade': 'S'},   # 训练PF=1.798
    }

    # 体制动态SL覆盖（ETH/LTC）
    _current_regime = (ms.get('regime','') or '').upper()
    _spec_tmp = _sym_spec_map.get(_sym, {})
    if _spec_tmp and '_regime_sl' in _spec_tmp and _current_regime:
        _regime_sl_val = _spec_tmp['_regime_sl'].get(_current_regime)
        if _regime_sl_val:
            _sym_spec_map[_sym] = dict(_spec_tmp)
            _sym_spec_map[_sym]['sl_mult_override'] = _regime_sl_val

    # [N19] BTC传导系数 — 低传导标的在BTC突破时降权
    # 数据来源: train_10k_v5.py N19节点，15标的分析
    # 低传导(<40%): BTC突破后4h内跟随率偏低
    _btc_low_conductance = {
        '1000PEPEUSDT', 'APTUSDT', 'INJUSDT', 'LUNA2USDT', 'NEARUSDT'
    }
    # BTC突破判断阈值: 1H涨幅>1.5%或4H EMA金叉
    _btc_breakout_pct = 0.015
    _spec = _sym_spec_map.get(_sym)
    if _spec and params.get('valid'):
        # 重算SL/TP（用专项sl_mult覆盖）
        _sl_ov = _spec['sl_mult_override']
        _tp_ov = _spec.get('tp_mult_override', 4.0)  # [WFV-v3] 专属TP倍数
        _atr1 = float(ms.get('momentum', {}).get('atr_1h', ms.get('price', 1) * 0.01))
        _price_ov = float(ms.get('price', 0))
        _entry_lo_ov = params.get('entry_lo', _price_ov)
        _entry_hi_ov = params.get('entry_hi', _price_ov)
        _entry_mid_ov = (_entry_lo_ov + _entry_hi_ov) / 2
        if _price_ov > 0 and _atr1 > 0:
            if signal_dir == 'SHORT':
                # [BUG修复] SL从入场区上沿算，确保SL > entry_hi
                _sl_new = round(_entry_hi_ov + _atr1 * _sl_ov, 6)
                _risk_ov = abs(_sl_new - _entry_mid_ov)
                _tp1_new = round(_entry_mid_ov - _risk_ov * _tp_ov, 6)
                _tp2_new = round(_entry_mid_ov - _risk_ov * (_tp_ov * 1.8), 6)
            else:
                # [BUG修复] SL从入场区下沿算，确保SL < entry_lo
                _sl_new = round(_entry_lo_ov - _atr1 * _sl_ov, 6)
                _risk_ov = abs(_entry_mid_ov - _sl_new)
                _tp1_new = round(_entry_mid_ov + _risk_ov * _tp_ov, 6)
                _tp2_new = round(_entry_mid_ov + _risk_ov * (_tp_ov * 1.8), 6)
            # 用当前价算R:R会因为「价格离入场区还有距离」导致分母虚大，R:R严重失真
            # ETH实测: 当前价基准R:R=1.41 vs 入场中点基准R:R=4.66
            _sl_pct_new = round(abs(_sl_new - _entry_mid_ov) / _entry_mid_ov * 100, 3)
            _risk_for_rr = abs(_sl_new - _entry_mid_ov)
            _rr1_new = round(abs(_tp1_new - _entry_mid_ov) / max(_risk_for_rr, 1e-9), 2)
            # [设计院 2026-06-23 P0修复 v4] N17覆盖层护栏：tp2必须在tp1更远方向
            _risk_ov2 = abs(_sl_new - _entry_mid_ov)
            if signal_dir == 'LONG' and _tp2_new <= _tp1_new:
                _tp2_new = round(_tp1_new + _risk_ov2, 6)
            elif signal_dir == 'SHORT' and _tp2_new >= _tp1_new:
                _tp2_new = round(_tp1_new - _risk_ov2, 6)
            _rr2_new = round(abs(_tp2_new - _entry_mid_ov) / max(_risk_for_rr, 1e-9), 2)
            params = dict(params)
            params.update({
                'stop_loss': _sl_new, 'tp1': _tp1_new, 'tp2': _tp2_new,
                'sl_pct': _sl_pct_new, 'rr1': _rr1_new, 'rr2': _rr2_new,
                'sl_atr_mult': _sl_ov,
                '_spec_override': f'{_sym} 专项sl={_sl_ov}x mh={_spec["mh_override"]}h PF={_spec["pf_evidence"]}',
                'valid': _rr1_new >= 1.2,  # [六方修复 2026-06-25] 最低门槛1.2
            })

    # ── [v4.0出场后置层 2026-06-28] N17专项覆写后再次应用exit_params_v4 ──
    # 原因：N17专项 tp_mult_override 会把RR重新拉高（如BTC tp=1.964x → rr=1.9+）
    #       v4.0铁证要求BEAR/CHOP体制RR=1.0，必须在N17后再压近目标
    try:
        import json as _jv4b, pathlib as _pv4b
        _v4b_path = _pv4b.Path(__file__).parent.parent / 'data' / 'dharma_runtime.json'
        _v4b_data = _jv4b.loads(_v4b_path.read_text()) if _v4b_path.exists() else {}
        _v4b_params = _v4b_data.get('exit_params_v4', {})
        _regime_v4b = ms.get('regime', '')
        if any(x in _regime_v4b for x in ('CHOP',)):
            _v4b_key = 'CHOP'
        elif any(x in _regime_v4b for x in ('BULL',)):
            _v4b_key = 'BULL'
        else:
            _v4b_key = 'BEAR'
        _v4b_cfg = _v4b_params.get(_v4b_key, {})
        _v4b_min_sl = float(_v4b_cfg.get('sl_pct', 0))
        _v4b_rr    = float(_v4b_cfg.get('rr', 0))
        if _v4b_min_sl > 0 and _v4b_rr > 0:
            _p_mid_v4b = (params.get('entry_lo',0) + params.get('entry_hi',0)) / 2
            _p_sl_v4b  = params.get('stop_loss', 0)
            _p_sl_pct  = params.get('sl_pct', 0)
            _cur_rr1   = params.get('rr1', 0)
            _risk_v4b  = abs(_p_sl_v4b - _p_mid_v4b) if _p_sl_v4b and _p_mid_v4b else 0
            _v4b_applied = False
            # Step1：若sl_pct < v4最低门槛，扩大止损
            if _p_sl_pct > 0 and _p_sl_pct < _v4b_min_sl and _p_mid_v4b > 0:
                _risk_v4b = _p_mid_v4b * _v4b_min_sl / 100
                if signal_dir == 'SHORT':
                    params['stop_loss'] = round(_p_mid_v4b + _risk_v4b, 6)
                else:
                    params['stop_loss'] = round(_p_mid_v4b - _risk_v4b, 6)
                params['sl_pct'] = _v4b_min_sl
                _v4b_applied = True
            # Step2：若当前RR > v4目标RR，压近TP
            if _risk_v4b > 0 and _cur_rr1 > _v4b_rr + 0.05:
                if signal_dir == 'SHORT':
                    params['tp1'] = round(_p_mid_v4b - _risk_v4b * _v4b_rr, 6)
                    params['tp2'] = round(_p_mid_v4b - _risk_v4b * max(_v4b_rr * 2.0, 2.0), 6)
                else:
                    params['tp1'] = round(_p_mid_v4b + _risk_v4b * _v4b_rr, 6)
                    params['tp2'] = round(_p_mid_v4b + _risk_v4b * max(_v4b_rr * 2.0, 2.0), 6)
                params['rr1'] = round(abs(params['tp1'] - _p_mid_v4b) / max(_risk_v4b, 1e-9), 2)
                params['rr2'] = round(abs(params['tp2'] - _p_mid_v4b) / max(_risk_v4b, 1e-9), 2)
                _v4b_applied = True
            if _v4b_applied:
                params['valid'] = params.get('rr1', 0) >= 1.0  # v4.0体制下1.0已有正期望
    except Exception as _ev4b:
        pass  # 静默失败，不影响主流程
    # ── [END v4.0出场后置层] ──

    # [v13.0] 单一化输出层：R:R不足成为唱拘定局式，覆盖action为WATCH
    # 规则：TP1 R:R ≥ 1.5 才论入场（设计院2026-06-14 宽止损策略允许1.5）
    if not params.get('valid'):
        rr1_val = params.get('rr1', 0)
        sl_basis = params.get('sl_basis', 'ATR')
        # [FIX-RR 2026-05-27] R:R不达标时，尝试用ATR×2.0自动扩展止损重算
        _entry_mid = (params.get('entry_lo',0) + params.get('entry_hi',0)) / 2
        _atr4h = ms['momentum'].get('atr_4h', ms['momentum'].get('atr_1h',0)*2.5)
        if _entry_mid > 0 and _atr4h > 0:  # [FIX-RR-v2 2026-06-14] 移除rr1_val>0条件，score清零不影响RR扩展
            _new_risk = _atr4h * 2.0
            if signal_dir == 'SHORT':
                _new_sl  = _entry_mid + _new_risk
                _new_tp1 = _entry_mid - _new_risk * 2.5
                _new_rr1 = abs(_new_tp1 - _entry_mid) / _new_risk
            else:
                _new_sl  = _entry_mid - _new_risk
                _new_tp1 = _entry_mid + _new_risk * 2.5
                _new_rr1 = abs(_new_tp1 - _entry_mid) / _new_risk
            # 拓展后止损宽度 ≤ 5%，且新RR ≥ 2.5
            _new_sl_pct = abs(_new_sl - _entry_mid) / _entry_mid * 100
            if _new_rr1 >= 1.5 and _new_sl_pct <= 5.0:  # [FIX-RR-v2 2026-06-14] 1.5允许宽止损策略
                # [设计院 2026-06-23 P0修复 v5] 拓展重算分支：tp2同步更新
                _new_tp2 = _entry_mid - _new_risk * 4.5 if signal_dir == 'SHORT' else _entry_mid + _new_risk * 4.5
                if signal_dir == 'LONG' and _new_tp2 <= _new_tp1:
                    _new_tp2 = _new_tp1 + _new_risk
                elif signal_dir == 'SHORT' and _new_tp2 >= _new_tp1:
                    _new_tp2 = _new_tp1 - _new_risk
                _new_rr2 = round(abs(_new_tp2 - _entry_mid) / _new_risk, 2)
                params = dict(params)
                params['stop_loss'] = round(_new_sl, 4)
                params['tp1']       = round(_new_tp1, 4)
                params['tp2']       = round(_new_tp2, 4)
                params['rr1']       = round(_new_rr1, 2)
                params['rr2']       = _new_rr2
                params['sl_pct']    = round(_new_sl_pct, 2)
                params['sl_basis']  = 'atr4h×2.0(拓展重算)'
                params['valid']     = True
                rr1_val = params['rr1']
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        # ── [六方联合修复 2026-06-25] 方案C：体制分级R:R最低门槛 ──
        # 铁证依据：BEAR_RECOVERY WR=72.5% × R:R=1.2 → EV=0.595（正期望）
        #           震荡行情TP目标有限，强求2.5是脱离实际
        #           每个体制应有独立R:R门槛，而非统一1.5
        _cur_regime_rr = ms.get('regime', '') if ms else ''
        _rr_thresholds = {
            'BEAR_TREND':      1.8,   # 趋势强，目标远，保持高标准
            'BULL_TREND':      1.8,
            'BEAR_EARLY':      1.6,   # 初期趋势，稍宽松
            'BULL_EARLY':      1.6,
            'BEAR_RECOVERY':   1.2,   # 反弹体制WR=72.5%，低R:R有正期望
            'BULL_CORRECTION': 1.2,
            'CHOP_MID':        1.0,   # [v25.4 苏摩111 2026-06-28] 对齐v4.0 RR=1.0铁证 EV=+0.37%/笔
            'CHOP_LOW':        1.0,   # [v25.4] CHOP_LOW RR=1.0
            'CHOP_HIGH':       1.2,   # [v25.4] CHOP_HIGH稍保守 1.2（高波动不确定性）
        }
        _rr_min = _rr_thresholds.get(_cur_regime_rr, 1.4)  # 默认1.4
        _is_valid_rr = rr1_val >= _rr_min
        if not _is_valid_rr:
            cf['action']     = f'WATCH(R:R={rr1_val:.2f}<{_rr_min}({_cur_regime_rr}) sl={sl_basis})'
            cf['kelly_mult'] = 0
            cf['rr_gate']    = 'FAIL'
            cf['rr_min_used'] = _rr_min
        else:
            # ── 修复C：最小SL=1×ATR_1H，防止紧SL被针形K线振出 ───────────────
            # 根因：6/13月6/14 ETH LONG sl_pct=0.8~0.9%，6/14 14:00被针形振出
            #       6/14 20:00 ETH暴涨至1732 → 如果SL够宽能等到TP
            # 规则：SL必须≥1×ATR_1H，优先保护SL不过项，RR重算
            try:
                _c_atr_1h = float(ms.get('momentum', {}).get('atr_1h', 0) or
                                  ms.get('atr_1h', 0) or 0) if ms else 0
                _c_price  = float(ms.get('price', 0) or 0)
                _c_entry_mid = (float(params.get('entry_lo', _c_price) or _c_price) +
                                float(params.get('entry_hi', _c_price) or _c_price)) / 2
                if _c_atr_1h > 0 and _c_entry_mid > 0:
                    _c_min_sl_pct = _c_atr_1h / _c_entry_mid * 100  # 1×ATR_1H百分比
                    _c_cur_sl_pct = float(params.get('sl_pct', 0) or 0)
                    if 0 < _c_cur_sl_pct < _c_min_sl_pct:
                        # SL太紧，拖到ATR_1H宽度
                        _c_new_risk = _c_entry_mid * _c_min_sl_pct / 100
                        _c_tp1 = float(params.get('tp1', 0) or 0)
                        _c_tp_dist = abs(_c_tp1 - _c_entry_mid) if _c_tp1 else 0
                        _c_new_rr1 = _c_tp_dist / _c_new_risk if _c_new_risk > 0 else 0
                        if _c_new_rr1 >= _rr_min * 0.8:  # 拖宽后仍满足肠门槛皀80%才执行
                            if signal_dir == 'LONG':
                                params = dict(params)
                                params['stop_loss'] = round(_c_entry_mid - _c_new_risk, 4)
                            else:
                                params = dict(params)
                                params['stop_loss'] = round(_c_entry_mid + _c_new_risk, 4)
                            params['sl_pct'] = round(_c_min_sl_pct, 3)
                            params['rr1']    = round(_c_new_rr1, 2)
                            params['sl_basis'] = f'min1xATR_1H(orig={_c_cur_sl_pct:.2f}%)'
                            print(f'[修复C] {signal_dir} SL拖宽: {_c_cur_sl_pct:.2f}%→{_c_min_sl_pct:.2f}%(1×ATR_1H={_c_atr_1h:.4f}) rr1={_c_new_rr1:.2f}')
            except Exception as _c_err:
                pass  # 静默
            cf['action']  = 'ENTER_FULL'
            cf['rr_gate'] = 'PASS'
            cf['rr_min_used'] = _rr_min
    else:
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['rr_gate'] = 'PASS'
        # [v13.0] 单一化：行动与 primary_tf 周期同步
        cf['primary_tf'] = params.get('primary_tf', '4H')
        cf['entry_tf']   = params.get('entry_tf',   '1H')
        cf['sl_basis']   = params.get('sl_basis',   'swing_4h+atr4h×0.3')

    # [Phase C-2] RL 仓位乘数覆盖 kelly_mult
    rl = extra_data.get('rl_position', {})
    if rl.get('kelly_mult') and cf.get('action') in ('ENTER_FULL', 'ENTER'):
        rl_mult = rl['kelly_mult']
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        base_kelly = cf.get('kelly_base', cf.get('kelly_mult', 1.0))
        cf['kelly_mult'] = round(base_kelly * rl_mult, 3)
        cf['rl_kelly_note'] = rl.get('note', '')

    # ══════════════════════════════════════════════════════════
    # [v12.8] I2 冲突解析器 / I3 Kelly分配 / I4/I7 漂移+健康检测
    # ══════════════════════════════════════════════════════════
    import sys as _sys
    _bb_dir = str(__file__).replace('brahma_brain.py','')
    if _bb_dir not in _sys.path: _sys.path.insert(0, _bb_dir)

    # I4/I7: 漂移检测
    try:
# [CLEANED 2026-06-11] from drift_detector import detect as _drift_detect
        extra_data['drift'] = _drift
        if _drift['alert'] == 'ALERT':
            pass  # [静默] f'[BrahmaBrain] ⚠️ DRIFT ALERT {_sym}: {_drift["summary"]}'
    except Exception as _de:
        pass

    # I2: 冲突解析
    try:
        from conflict_resolver import resolve as _cr_resolve
        _bd = cf.get('breakdown', {})
        _conflict = _cr_resolve(_bd, signal_dir, cf.get('total', 0))
        extra_data['conflict'] = _conflict
        if _conflict['verdict'] == 'REJECT':
            pass  # [静默] f'[BrahmaBrain] 🚫 CONFLICT REJECT {_sym}: {_conflict["conflict_summary"]}'
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['conflict_reject'] = True
        elif _conflict['verdict'] == 'DOWNWEIGHT':
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = round(cf.get('kelly_mult', 1.0) * _conflict['confidence_adj'], 3)
            cf['conflict_adj'] = _conflict['confidence_adj']
        elif _conflict['verdict'] == 'APPROVE' and _conflict['confidence_adj'] > 1.0:
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = round(min(cf.get('kelly_mult', 1.0) * _conflict['confidence_adj'], 2.0), 3)
    except Exception as _ce:
        pass

    # I3: Kelly仓位分配
    try:
# [CLEANED 2026-06-11] from kelly_allocator import compute as _kelly_compute
        _bayes_wr = None
        if extra_data.get('online_bayes'):
            _bayes_wr = extra_data['online_bayes'].get('post_wr')
        _xgb_prob = None
        if extra_data.get('xgboost'):
            _xgb_prob = extra_data['xgboost'].get('win_prob')
        _drift_mult = extra_data.get('drift', {}).get('confidence_mult', 1.0)
        _kelly_result = _kelly_compute(
            rr_ratio=params.get('rr_ratio', 1.5),
            signal_score=int(cf.get('total', 100)),
            bayes_wr=_bayes_wr,
            xgb_prob=_xgb_prob,
            extra_data={'drift': {'confidence_mult': _drift_mult}},
        )
        extra_data['kelly'] = _kelly_result
    except Exception as _ke:
        pass

    # ══════════════════════════════════════════════════════════
    # [v24.3] PRE-COMPUTE structure grade（前移，供Queue check使用）
    # 原设计：structure计算在行3101，Queue check在行2662，grade=0导致冷却死循环
    # 修复：提前计算grade，让Queue check读到真实值
    # ══════════════════════════════════════════════════════════
    try:
        from structure_quality_engine import evaluate_structure_quality as _pre_sqe
        _tc = params.get('trigger_15m_confidence', 0) or cf.get('trigger_15m_confidence', 0) or 0  # [v24.5-fix] 优先从 params 读取，cf不包含时备用
        _pre_sq_result = _pre_sqe(
            symbol     = _sym,
            signal_dir = signal_dir,
            price      = float(ms.get('price', 0)),
            entry_lo   = float(params.get('entry_lo', 0) or 0),
            entry_hi   = float(params.get('entry_hi', 0) or 0),
            smc        = smc,
            swing_4h   = ms.get('swing_4h', {}),
            key_levels = ms.get('key_levels', {}),
            momentum   = ms.get('momentum', {}),
            trigger_confidence = int(_tc),
        )
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['structure_grade'] = _pre_sq_result.get('grade', 0)
        # [v24.5-debug] 临时打印，确认修复后grade值
        import os
        if os.environ.get('BRAHMA_DEBUG'):
            pass  # [静默] f'[PRE-SQE] {_sym} price={ms.get("price",0):.0f} entry={params.get("entry_lo",0)
    except Exception as _pre_sq_err:
        pass  # 失败不影响主流程

    # ══════════════════════════════════════════════════════════
    # [v12.9] I5 队列/资金 / I3 动态SL / I7 归因（Phase 1）
    # ══════════════════════════════════════════════════════════

    # I5: 信号队列检查（是否可以进入队列）
    try:
        from brahma_signal import add_signal as _sq_add, get_queue_status as _sq_status
        _sq_result = _sq_add(
            symbol=_sym,
            signal_dir=signal_dir,
            score=float(cf.get('total', 100)),
            regime=str(ms.get('regime','')),
            grade=int(cf.get('structure_grade', 0) or 0),
            effective_grade=round(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0), 1),
            grade_mult=round(float(cf.get('grade_mult', 1.0) or 1.0), 2),
        )
        extra_data['signal_queue'] = _sq_result
        if not _sq_result.get('accepted', True):
            pass  # [静默] f'[BrahmaBrain] 🚫 Queue reject {_sym}: {_sq_result["reason"]}'
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['queue_reject'] = _sq_result['reason']
    except Exception as _sqe:
        pass

    # I5: 资金分配
    try:
        from capital_allocator import compute as _ca_compute
        _ca_result = _ca_compute(
            symbol=_sym,
            signal_score=float(cf.get('total', 100)),
            sl_pct=params.get('sl_pct', None),
        )
        extra_data['capital'] = _ca_result
        if not _ca_result.get('allowed', True):
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['capital_reject'] = _ca_result['reason']
    except Exception as _cae:
        pass

    # I3: 动态止损
    try:
        from dynamic_sl import compute as _dsl_compute
        _drift_alert = extra_data.get('drift', {}).get('alert', 'OK')
        _kls = [lvl for lvl in ms.get('key_levels', {}).values()
                if isinstance(lvl, (int,float)) and lvl > 0] if ms.get('key_levels') else []
        _dsl = _dsl_compute(
            symbol=_sym,
            entry_price=float(ms.get('price', 0)),
            signal_dir=signal_dir,
            regime=str(ms.get('regime','')),
            score=float(cf.get('total', 100)),
            drift_alert=_drift_alert,
            key_levels=_kls,
        )
        extra_data['dynamic_sl'] = _dsl
        params = dict(params)
        params['sl_price_dyn'] = _dsl.get('sl_price')
        params['sl_pct_dyn']   = _dsl.get('sl_pct')
        params['sl_reasoning'] = _dsl.get('reasoning')
    except Exception as _dsle:
        pass

    # I7: 实时归因（轻量，从attribution.json读缓存而非重算）
    try:
        _attr_f = __import__('pathlib').Path('data/attribution.json')
        if _attr_f.exists():
            _attr = __import__('json').loads(_attr_f.read_text())
            extra_data['attribution'] = {
                'top_misleaders': _attr.get('top_misleaders', [])[:3],
                'ts': _attr.get('ts', ''),
            }
    except Exception as _ate:
        pass

    # ══════════════════════════════════════════════════════════════
    # [设计院终极版 v2.0] 六层防线集成入口
    _globally_blocked = False  # [设计院修复 2026-06-26] 默认值防止try异常时UnboundLocalError
    # regime_gate → asset_universe → regime_weights → adaptive_threshold → MTF → Kelly | 体制门控 → 资产池 → 体制权重 → 自适应阈值 → 多时框 → Kelly
    # ══════════════════════════════════════════════════════════════
    try:
        import sys as _v2_sys, os as _v2_os
        _v2_base = _v2_os.path.dirname(_v2_os.path.dirname(_v2_os.path.abspath(__file__)))
        if _v2_base not in _v2_sys.path: _v2_sys.path.insert(0, _v2_base)
        from upgrade_v2.v2_integrator import v2_enhance_signal as _v2_enhance
        _v2_result = _v2_enhance(
            symbol    = _sym,
            direction = signal_dir,
            score     = float(cf.get('total', 0)),
            ms        = ms,
            breakdown = cf.get('breakdown', {}),
            nav       = float(ms.get('nav', 127.62) or 127.62),
            interval  = '1h',
        )
        # 写入 cf 供日志记录
        cf['v2_audit']     = _v2_result.get('audit', {})
        cf['v2_mode']      = _v2_result.get('mode', '')
        cf['v2_mtf_note']  = _v2_result.get('mtf_note', '')
        cf['v2_pos_pct']   = _v2_result.get('pos_pct', 0)
        cf['v2_breakdown'] = _v2_result.get('breakdown_ext', {})

        _globally_blocked = not _v2_result.get('allowed', True)
        if _globally_blocked:
            # v2 硬封锁 → 评分归零0，不退出，让analyze()完整构建返回结构
            _block_reason = _v2_result.get('block_reason', 'v2封锁')
            pass  # [静默] f'[BrahmaBrain-v2] 🛡️ 封锁 {_sym} {signal_dir}: {_block_reason[:60]}'
            cf['total']         = 0
            cf['score_final']   = 0
            cf['action']        = 'SKIP'
            cf['kelly_mult']    = 0
            cf['v2_blocked']    = True
            cf['v2_block_reason'] = _block_reason
        else:
            # v2 通过 → 更新评分和仓位
            _v2_final_score = _v2_result.get('final_score', cf.get('total', 0))
            if _v2_final_score != cf.get('total', 0):
                pass  # [静默] f'[BrahmaBrain-v2] 📊 {_sym} 评分调整: {cf.get("total",0):.0f}→{_v2_final_score:.0f} 
                cf['total'] = _v2_final_score
            # 仓位由v2接管
            cf['v2_pos_pct'] = _v2_result.get('pos_pct', 0)
    except Exception as _v2_err:
        # v2失败降级，不影响原有流程
        _v2_err_str = str(_v2_err)
        # 模块缺失静默处理（ModuleNotFoundError / ImportError 不输出告警）
        if not isinstance(_v2_err, (ModuleNotFoundError, ImportError)):
            import traceback
            cf['v2_error'] = _v2_err_str[:100]
        # upgrade_v2 模块缺失时完全静默，不写入任何内容
        else:
            pass  # 静默降级，不输出任何日志

    # [达摩院v2.0] P2: Score门槛 — 从参数总线读取品种专项门槛
    # M01铁证: thr=160品种均PF=2.944, 158为实盘安全边际
    try:
        import sys as _sys, os as _os
        _bus_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')
        if _bus_dir not in _sys.path: _sys.path.insert(0, _bus_dir)
        from dharma.dharma_bus import get_sym_params as _get_bus_p
        _bus_d = _get_bus_p(_sym) if _sym else {}
        MIN_SCORE_OPEN = int(_bus_d.get('thr', 140))
    except Exception:
        MIN_SCORE_OPEN = 140   # fallback: 2026-06-04 设计院统一门槛（原158偏高，adaptive_threshold=140）
    MIN_SCORE_S2   = 130   # S2门槛：轻仓3%试探
    MIN_SCORE_S3   = 100   # S3门槛：观察记录，不开仓
    _score_raw = cf.get('total', 0)

    # 防止后续 StructureGate/DharmaFactor/N20/N21 等重新写入 cf['total'] 覆盖清零
    if _globally_blocked:
        _score_raw = 0
        cf['total'] = 0  # [S4-fix audit-2026-06-17] 再次确保cf同步
        _score_gate_ok = False  # [S4-fix] 封锁时门控标志同步清零，防止后续门控误判

    # ── [P2-C] N19 BTC传导系数 ─────────────────────────────────────────────
    # 低传导标的(<40%) 在BTC强势突破(1H涨幅>1.5%)时 score×0.90
    # 数据来源: train_10k_v5.py N19节点
    try:
        _btc_low_cond = {'1000PEPEUSDT','APTUSDT','INJUSDT','LUNA2USDT','NEARUSDT'}
        if _sym in _btc_low_cond:
            _btc_state = extra_data.get('btc_market', {}) or {}
            _btc_chg_1h = float(_btc_state.get('price_change_pct_1h', 0) or 0)
            if abs(_btc_chg_1h) >= 1.5:
                _cond_factor = 0.90
                _score_raw = round(_score_raw * _cond_factor)
                cf['total'] = _score_raw
                _log(f'[BrahmaBrain] 📉 P2-C N19低传导惩罚: {_sym} ×{_cond_factor} BTC1H={_btc_chg_1h:+.1f}% score→{_score_raw}')
    except Exception:
        pass
    # ── [END P2-C] | P2-C 阶段结束 ──────────────────────────────────────────────────────────
    # ── [v25.5 能力升级-A] 体制×方向动态门控提升 ─────────────────────────
    # 原则：不封禁，但低WR组合需要更高评分才能通过（精化筛选）
    # 数据：BEAR_EARLY_LONG WR=50.4% / BULL_EARLY_SHORT WR=51.9%（n>6000铁证）
    # 解决：提高这些组合的动态门控阈值，要求信号质量更高才入场
    # analyze() 作用域内不存在。改从 cf(breakdown) 读取 _regime_v4_key。
    _regime_dir_key = f"{(cf or {}).get('_regime_v4_key','') or ''}_{signal_dir}"
    _DYNAMIC_THRESHOLD_BOOST = {
        # 负期望组合：要求额外+18分才能通过（约等于要求score≥158）
        'BEAR_EARLY_LONG':       18,   # WR=50.4% avg=-0.110% → 高门控筛出低质信号
        'BULL_EARLY_SHORT':      18,   # WR=51.9% avg=-0.137% → 高门控筛出低质信号
        # 震荡×多：WR=56%，略提高
        'CHOP_LONG':              8,   # WR=56.0% avg=-0.001% → 轻提高
        'CHOP_MID_LONG':          8,
        'CHOP_LOW_LONG':          5,
    }
    _thr_boost = _DYNAMIC_THRESHOLD_BOOST.get(_regime_dir_key, 0)
    _MIN_SCORE_EFFECTIVE = MIN_SCORE_OPEN + _thr_boost
    if _thr_boost > 0:
        cf['dynamic_threshold_boost'] = _thr_boost
        cf['dynamic_threshold_effective'] = _MIN_SCORE_EFFECTIVE

    # ── [v25.5 能力升级-D] 1D方向性修正 ─────────────────────────────────────
    # 原则：逆1D大趋势方向时降权（非封禁），要求更高质量信号
    # 数据：BEAR_EARLY_LONG在1D DOWNTREND时失败率极高（1D逆势做多）
    try:
        _ms_1d = ms.get('1d', ms.get('daily', {})) or {}
        _phase_1d = str(_ms_1d.get('phase', '')).upper()
        _1d_penalty = 0
        if _phase_1d in ('DOWNTREND', 'PULLBACK_DN', 'TOPPING') and signal_dir == 'LONG':
            # 1D下跌趋势中做多：+12分门控（不封禁，但要求更高质量）
            _1d_penalty = 12
            cf['_1d_direction_penalty'] = f'+{_1d_penalty}门控(1D={_phase_1d}逆势做多)'
        elif _phase_1d in ('UPTREND', 'PULLBACK_UP', 'BOTTOMING') and signal_dir == 'SHORT':
            # 1D上涨趋势中做空：+12分门控
            _1d_penalty = 12
            cf['_1d_direction_penalty'] = f'+{_1d_penalty}门控(1D={_phase_1d}逆势做空)'
        _MIN_SCORE_EFFECTIVE += _1d_penalty
    except Exception:
        pass

    _score_gate_ok = float(_score_raw) >= _MIN_SCORE_EFFECTIVE

    # [苏摩哲学校正 2026-06-30 A1修正] CHOP_MID做多WATCH通道
    # CHOP强反转上限=105，阈值必须≤105才能触发，修正为100
    # 原110阈值 > CHOP上限105 → 永远无法触发（设计院顶层修正 2026-06-30）
    _is_chop_long_watch = (
        'CHOP' in str(_regime_str).upper()
        and signal_dir == 'LONG'
        and float(_score_raw) >= 100   # 修正: 110→100，CHOP上限=105可触发
        and not _score_gate_ok
    )
    if _is_chop_long_watch:
        _score_gate_ok = True   # 豁免score gate
        cf['chop_long_watch'] = f'CHOP_MID做多WATCH通道: score={_score_raw:.0f}≥100 → 0.5%NAV观察仓'
        pass  # [静默] f'[CHOP-WATCH] {_sym} CHOP_MID做多: score={_score_raw:.0f}≥100 WATCH信号解锁（A1修正）'

    if not _score_gate_ok:
        pass  # [静默] f'[BrahmaBrain] ⚠️ Score gate {_sym}: {_score_raw:.0f} < {_MIN_SCORE_EFFECTIVE} 
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['score_gate_reject'] = True
        cf['score_gate_min'] = MIN_SCORE_OPEN

    # ══════════════════════════════════════════════════════════════
    # [P0-3 设计院封印 2026-08-21 苏摩111] entry_source=? 硬性门控
    # 铁证：entry_source=? 的83条信号 WR=0.0%（一个月实盘铁证）
    # 无有效OB/FVG入场结构时，强制score<100，阻止开仓
    _entry_src_raw = params.get('entry_source', '') or ''
    if (not _entry_src_raw or _entry_src_raw == '?') and _score_gate_ok:
        _score_raw = min(_score_raw, 95.0)
        cf['total'] = _score_raw
        cf['P0_3_no_entry_struct'] = f'entry_source=?/空，无OB/FVG结构，score压至{_score_raw:.0f}≤95（WR=0%死穴）'
        _score_gate_ok = float(_score_raw) >= _MIN_SCORE_EFFECTIVE
    # ══════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════
    # [P1-1 设计院封印 2026-08-21 苏摩111] BULL_TREND:LONG score 120~154 封禁
    # 铁证：一个月实盘 score120~154 BULL_TREND:LONG n=74 WR=1.3%（78条只有1WIN）
    # 根因：4H触发BULL_TREND:LONG = 追涨信号，价格已走完2/3，TP空间不足 → TIMEOUT
    # 解除条件：1H触发层完全生效后（T1/T4触发的BULL_TREND:LONG除外）
    _is_bull_trend_long = ('BULL_TREND' in str(_regime_str).upper() and signal_dir == 'LONG')
    _trigger_is_1h = params.get('trigger_type','') in ('T1_RSI_UP','T4_OS_LONG') or \
                     params.get('source_trigger','') in ('rsi_1h_trigger',)
    if _is_bull_trend_long and not _trigger_is_1h and 120 <= float(_score_raw) < 155:
        _score_raw = 95.0  # 强制降至score gate以下
        cf['total'] = _score_raw
        cf['P1_1_bull_trend_long_ban'] = f'BULL_TREND:LONG score120~154封禁(WR=1.3%铁证) → score={_score_raw:.0f}'
        _score_gate_ok = float(_score_raw) >= _MIN_SCORE_EFFECTIVE
    # ══════════════════════════════════════════════════════════════
    # LINK CI宽5.86→×0.70 | DOGE CI宽5.11→×0.70 | NEAR ×0.55
    # 确保高不确定性品种不会因单笔大仓拖垃最大回撤
    # ══════════════════════════════════════════════════════════════
    try:
        from dharma.dharma_bus import get_pos_with_ci_discount as _get_ci_pos
        _ci_pos_cap = _get_ci_pos(_sym)
        # score_pos是分层仓位，_ci_pos_cap是总线上限，取小者
        _score_pos_cur = extra_data.get('score_pos', 0.065) if extra_data and isinstance(extra_data, dict) else 0.065
        _final_pos = min(_score_pos_cur, _ci_pos_cap)
        if _final_pos < _score_pos_cur:
            if extra_data and isinstance(extra_data, dict):
                extra_data['score_pos'] = _final_pos
                extra_data['ci_discount_applied'] = True
            _log(f'[BrahmaBrain] M11 CI折扣 {_sym}: {_score_pos_cur:.1%}→{_final_pos:.1%}')
    except Exception:
        pass

    # [P2-A] 4h多周期方向确认层（N13实证: 4h泛化率75%优于1h67%）
    _mom_4h = ms.get('momentum', {})
    _rsi_4h = float(_mom_4h.get('rsi_4h', 50))
    _macd_4h = _mom_4h.get('macd_4h', 0) or _mom_4h.get('macd', 0) or 0
    _ema50_4h = float(_mom_4h.get('ema50_4h', 0) or 0)
    _ema200_4h = float(_mom_4h.get('ema200_4h', 0) or 0)
    _price_4h = float(ms.get('price', 0) or 0)
    _4h_align = 'NEUTRAL'
    # 4h方向判断：RSI方向 + EMA排列
    if _rsi_4h > 55 and (_ema50_4h > _ema200_4h or _macd_4h > 0) and _price_4h > _ema50_4h > 0:
        _4h_align = 'BULL'
    elif _rsi_4h < 45 and (_ema50_4h < _ema200_4h or _macd_4h < 0) and _price_4h < _ema50_4h > 0:
        _4h_align = 'BEAR'
    # 4h与1h信号方向一致时加分（N13: +12%泛化率）
    if _4h_align == 'BULL' and signal_dir == 'LONG' and _score_gate_ok:
        _score_raw = round(_score_raw * 1.05, 1)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['mtf_4h_confirm'] = f'4H✅BULL RSI={_rsi_4h:.0f} +5%'
        pass  # [静默] f'[BrahmaBrain] 📊 {_sym} 4H共振BULL: score×1.05 → {_score_raw:.0f}'
    elif _4h_align == 'BEAR' and signal_dir == 'SHORT' and _score_gate_ok:
        _score_raw = round(_score_raw * 1.05, 1)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['mtf_4h_confirm'] = f'4H✅BEAR RSI={_rsi_4h:.0f} +5%'
        pass  # [静默] f'[BrahmaBrain] 📊 {_sym} 4H共振BEAR: score×1.05 → {_score_raw:.0f}'
    elif _4h_align != 'NEUTRAL' and _4h_align == ('BEAR' if signal_dir=='LONG' else 'BULL'):
        # [v24.3-fix] 4H方向冲突 → 降权-25分（哲学: 降权不封禁）
        # 4H逆势是风险因子，用分数惩罚体现，grade≥70仍可通过
        # 顺势+5%奖励 vs 逆势-25分惩罚，不对称反映风险
        _4h_penalty = 25
        _score_raw = max(0, _score_raw - _4h_penalty)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['mtf_4h_conflict'] = f'4H⚠️{_4h_align} vs {signal_dir} 降权-{_4h_penalty}分 → {_score_raw:.0f}'
        pass  # [静默] f'[BrahmaBrain] ⚠️ {_sym} 4H逆势降权-{_4h_penalty}: {_4h_align} vs {signal_dir} → sc
    elif _4h_align == 'NEUTRAL' and _score_gate_ok:
        # [设计院 2026-07-06] MTF=NEUTRAL降权 -4%（原-8%过于激进）
        # 修正依据：实际WR差距约4~5%，不是8%；BULL_TREND下4H NEUTRAL很常见且多单结构其实良好
        # 改为-4%，阻断门槛仅针对真正中性/逃顶形态
        _neutral_penalty_pct = 0.98  # -2%（设计院v6.0 2026-07-08 外部审计建议）
        _score_before_neutral = _score_raw
        _score_raw = round(_score_raw * _neutral_penalty_pct, 1)
        cf['total'] = _score_raw
        cf = copy.deepcopy(cf)
        cf['mtf_4h_neutral'] = f'4H NEUTRAL 降权×0.98 {_score_before_neutral:.0f}→{_score_raw:.0f}'
        pass  # [静默] f'[BrahmaBrain] 🟡 {_sym} MTF=NEUTRAL 降抎2%[v6.0]: score {_score_before_neutral:.0

    # [设计院 2026-05-24] 达摩院6节点预测验证 — 接入真实信号流
    _dharma_nodes = {'nodes_pass': 0, 'verdict': 'UNKNOWN', 'score_mult': 1.0, 'detail': ''}
    try:
        from brahma_brain.dharma_nodes import evaluate_nodes as _eval_nodes
        _fg = 50
        try:
            from brahma_brain.macro_stub import get_fear_greed as _fg_fn
            _fg = _fg_fn() or 50
        except Exception: pass
        _dharma_nodes = _eval_nodes(ms, signal_dir, fg=_fg)
        # 节点乘数调整score
        _node_mult = _dharma_nodes['score_mult']
        if _node_mult == 0.0:
            # [v24.3-fix] 达摩院节点0/1 → 降权-30分（哲学: 不归零）
            _score_raw = max(0, _score_raw - 30)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            pass  # [静默] f'[Dharma] ⚠️ 节点不足 {_sym}: {_dharma_nodes["nodes_pass"]}/6节点 → -30分 score={_scor
        elif _node_mult != 1.0:
            _score_raw = round(_score_raw * _node_mult, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            pass  # [静默] f'[Dharma] 🔱 {_sym} 节点={_dharma_nodes["nodes_pass"]}/6 mult={_node_mult} score: 
        else:
            pass  # [静默] f'[Dharma] ✅ {_sym} 节点={_dharma_nodes["nodes_pass"]}/6 verdict={_dharma_nodes["v
        # [v24.3-fix] 节点数<3 → 额外-15分而非强制拒绝（哲学: 降权）
        if _dharma_nodes['nodes_pass'] < 3:
            _score_raw = max(0, _score_raw - 15)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        _score_gate_ok = _score_gate_ok  # 不再因节点数强制block
        # [设计院 2026-05-24] ≥5节点为高置信（HIGH_CONF），分数額外加成
        if _dharma_nodes.get('verdict') == 'HIGH_CONF':
            _score_raw = round(_score_raw * 1.05, 1)  # +5%加成
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            pass  # [静默] f'[Dharma] 🌟 HIGH_CONF {_sym}: score加成 ×1.05 → {_score_raw:.0f}'
    except Exception as _dne:
        pass  # 节点验证失败不阻断主流

    elapsed = round(time.time() - t0, 2)

    # ── [v25.5] 低市値品种校正层 ─────────────────────────────────
    # 铁证: DOGE/PEPE/TRUMP score虍高全部TIMEOUT，因果: 低流动性标的OB/FVG是假信号（ICM原则）
    # 修复: 降权评分 + 强制 TP小化（降低 TIMEOUT率）
    # ── [v25.5-AUDIT 已回滚] 以下品种校正因无铁证支撑而移除 ──────────────────
    # 回滚原因: DOGE实盘 n=3，铁证库无DOGE专项数据，违反最高宪法 n<30不得引用
    # 后续待办: 积累至 n≥100 后基于实盘数据重新评估
    # _LOW_CAP_CORRECTIONS = {...}  # 已回滚

    # [v22.1 2026-06-10] 进场区距离动态惩罚（gap远离惩罚维度）
    # 铁证: DOGE 180+全部TIMEOUT根因是 gap=-10%（价格已远超入场区）
    # gap定义: (entry_lo - price) / price * 100
    #   >0: 价格在入场区下方，需要反弹（正常等待）
    #   <0: 价格已穿越入场区（对SHORT=已经下跌超过入场区，信号失效）
    try:
        _gap_price  = float(ms.get('price', 0) or 0)
        _gap_elo    = float(params.get('entry_lo', 0) or 0)
        if _gap_price > 0 and _gap_elo > 0 and signal_dir == 'SHORT':
            _gap_dist = (_gap_elo - _gap_price) / _gap_price * 100
            if _gap_dist < -2.0:
                # 价格已远在入场区下方2%+，信号基本失效
                _gap_penalty = max(-40, round(_gap_dist * 3))  # -2% → -6分，-10% → -30分
                _score_raw = round(_score_raw + _gap_penalty, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            elif _gap_dist > 5.0:
                # 入场区距现价>5%，很难触达
                _gap_penalty = max(-20, round(-((_gap_dist - 5.0) * 2)))
                _score_raw = round(_score_raw + _gap_penalty, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        elif _gap_price > 0 and _gap_elo > 0 and signal_dir == 'LONG':
            _gap_dist_l = (_gap_price - params.get('entry_hi', _gap_elo)) / _gap_price * 100
            if _gap_dist_l < -2.0:
                _gap_penalty_l = max(-40, round(_gap_dist_l * 3))
                _score_raw = round(_score_raw + _gap_penalty_l, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
    except Exception:
        pass  # gap惩罚失败不阻断主流程

    _score = _score_raw

    # ── [v25.5-AUDIT 已回滚] BEAR_RECOVERY SHORT ×0.4 和 entry_source 惩罚 ──
    # 回滚原因A: 实盘BR_SHORT n=0有效样本，28条TIMEOUT是settler bug制造的假结果，
    #   不代表方向本身失败。离线铁证WR=47.9%(n=603)支持降权，但体制方向乘数矩阵
    #   已有0.4×机制覆盖，无需在字面量评分层二次干预。
    # 回滚原因B: entry_source=unknown n=20 < 30，违反最高宪法，禁止引用。
    # 后续待办: 积累 n≥100 实盘BEAR_RECOVERY SHORT 信号后重新评估。

    # ── P2 CHOP 硬性上限保护 [v25.4升级 CHOP-tc解锁 2026-06-27] ─────────────
    # 原哲学：CHOP EV=-0.11%（整体铁证n=14902）→ 硬性上限90
    # 新发现（达摩院CHOP专项）：tc共识分层后 WR=61~78%！CHOP是反转信号体制！
    # tc_strong×反向CHOP：WR=70~78%，解除上限（年均26条，BTC+ETH）
    # tc_lean×反向CHOP：  WR=61~63%，上限放宽至105
    # tc_neutral：         维持上限90（整体EV负，不变）
    # tc_同向CHOP（做多但全多共识）：上限收紧至75（反向逻辑，极危险）
    _is_chop_regime = any(x in str(ms.get('regime','')) for x in ('CHOP_MID','CHOP_HIGH','CHOP'))
    if _is_chop_regime:
        _tc_val   = int(ms.get('tc', ms.get('trend_consensus', 0)) or 0)
        _dir_chop = str(ms.get('signal_dir', ms.get('direction', '')))
        # 方向与tc的关系：SHORT信号 + tc偏空(负) = 逆向做空（CHOP反转逻辑）
        # CHOP_SHORT + tc_strong_bull(+2/+3) = 全市场多 → 震荡顶做空 ✅
        # CHOP_LONG  + tc_strong_bear(-2/-3) = 全市场空 → 震荡底做多 ✅
        _is_chop_short = (_dir_chop == 'SHORT')
        _is_chop_long  = (_dir_chop == 'LONG')
        _tc_align_short = (_tc_val >= 2)   # 多周期全多共识 → CHOP做空（反转）
        _tc_align_long  = (_tc_val <= -2)  # 多周期全空共识 → CHOP做多（反转）
        _tc_lean_short  = (_tc_val == 1)   # 单向偏多 → CHOP做空（弱反转）
        _tc_lean_long   = (_tc_val == -1)  # 单向偏空 → CHOP做多（弱反转）
        _tc_reverse_short = (_tc_val <= -2)  # 全空共识做空 → 同向顺势，危险！
        _tc_reverse_long  = (_tc_val >= 2)   # 全多共识做多 → 同向顺势，危险！

        _score_before_cap = _score
        if (_is_chop_short and _tc_align_short) or (_is_chop_long and _tc_align_long):
            # tc_strong 反转方向：WR=70~78%，完全解除上限（苏摩审批通过）
            _chop_cap_applied = None  # 无上限
            pass  # [静默] f'[P2-CHOP-UNLOCK] {ms.get("symbol","?")} CHOP×tc_strong反转: score={_score:.0f} 无
            cf['breakdown']['CHOP解锁'] = f'tc_strong反转 tc={_tc_val} WR=70~78% 无上限'
        elif (_is_chop_short and _tc_lean_short) or (_is_chop_long and _tc_lean_long):
            # tc_lean 反转方向：WR=61~63%，上限放宽至105
            _chop_cap_applied = 105
            if _score > 105:
                _score = 105
                cf['breakdown']['CHOP上限'] = f'tc_lean反转 tc={_tc_val} WR=61~63% 上限105: {_score_before_cap:.0f}→105'
                pass  # [静默] f'[P2-CHOP-CAP] {ms.get("symbol","?")} CHOP×tc_lean: {_score_before_cap:.0f}→105
        elif (_is_chop_short and _tc_reverse_short) or (_is_chop_long and _tc_reverse_long):
            # 同向顺势（全空做空/全多做多）：WR=30~46%！极危险，上限收紧至75
            _chop_cap_applied = 75
            if _score > 75:
                _score = 75
                cf['breakdown']['CHOP危险'] = f'tc同向顺势 tc={_tc_val} WR=30~46% 上限75: {_score_before_cap:.0f}→75'
                pass  # [静默] f'[P2-CHOP-DANGER] {ms.get("symbol","?")} CHOP×tc同向: {_score_before_cap:.0f}→75'
        else:
            # tc_neutral(0)：SHORT方向上限120，LONG方向维持90
            # [2026-08-12 苏摩111修复P3] CHOP SHORT执行线155，上限90永远不可达，修复为120
            _chop_dir = str(signal_dir or '').upper()
            _chop_cap_applied = 120 if _chop_dir == 'SHORT' else 90
            if _score > _chop_cap_applied:
                _score = _chop_cap_applied
                cf['breakdown']['CHOP硬性上限'] = f'P2保护tc_neutral: {_score_before_cap:.0f}→{_chop_cap_applied}（CHOP {"SHORT上限120" if _chop_dir=="SHORT" else "LONG上限90 EV=-0.11%"}）'
                pass  # [静默]
    # ── 死穴精英解锁通道（苏摩哲学校正 2026-06-30）────────────────────────────
    # 哲学：梵天为交易而生，体制=仓位权重调节器，不是封禁系统
    # 极端结构识别场景（RSI极值+高score+高grade）允许精英解锁
    _regime_str = str(ms.get('regime',''))
    _dir_check  = str(ms.get('signal_dir', ms.get('direction', '')))
    _dz_score   = float(cf.get('total', 0) or 0)
    _dz_grade   = float(cf.get('effective_grade', cf.get('structure_grade', cf.get('grade', 0))) or 0)
    _dz_rsi1h   = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)

    if 'BEAR_TREND' in _regime_str and _dir_check == 'LONG':
        # 精英解锁：score≥155 AND grade≥90 AND RSI_1H<20（极度超卖底部反弹）
        _bt_elite = (_dz_score >= 155 and _dz_grade >= 90 and _dz_rsi1h < 20)
        if _bt_elite:
            print(f'[死穴-精英解锁] {_sym} BEAR_TREND_LONG: score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 RSI={_dz_rsi1h:.0f}<20 → 0.5%NAV观察仓')
            cf['breakdown']['死穴精英解锁'] = f'BEAR_TREND_LONG RSI={_dz_rsi1h:.0f}<20底部反弹 score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 → 0.5%NAV'
        else:
            _valid = False
            _score_gate_ok = False  # [P0-1修复 2026-07-16 苏摩111] 死穴封禁同步置零，防L3742覆盖
            cf['breakdown']['死穴封禁'] = f'BEAR_TREND_LONG WR=45%(铁证n=3322) 未达精英解锁[score≥155+grade≥90+RSI<20] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BEAR_TREND_LONG: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BULL_TREND' in _regime_str and _dir_check == 'SHORT':
        # 精英解锁：score≥155 AND grade≥90 AND RSI_1H>75（高RSI顶部结构做空）
        _bu_elite = (_dz_score >= 155 and _dz_grade >= 90 and _dz_rsi1h > 75)
        if _bu_elite:
            print(f'[死穴-精英解锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 RSI={_dz_rsi1h:.0f}>75 → 0.5%NAV观察仓')
            cf['breakdown']['死穴精英解锁'] = f'BULL_TREND_SHORT RSI={_dz_rsi1h:.0f}>75顶部结构做空 score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 → 0.5%NAV'
        else:
            _valid = False
            _score_gate_ok = False  # [P0-1修复 2026-07-16 苏摩111] 死穴封禁同步置零，防L3742覆盖
            cf['breakdown']['死穴封禁'] = f'BULL_TREND_SHORT WR=47.7%(铁证n=4999) 未达精英解锁[score≥155+grade≥90+RSI>75] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BEAR_RECOVERY' in _regime_str and _dir_check == 'SHORT':
        # [v25.4死穴修复 2026-06-27] BEAR_RECOVERY_SHORT WR=46.6%/46.0% 升级为物理封锁
        # 达摩院铁证 n=233(BTC)/238(ETH) avg_pnl=-0.183/-0.305
        # 例外解锁：score>=145 AND grade>=90 AND Kronos p_up<0.2
        _br_score = cf.get('total', 0)
        _br_grade = float(cf.get('grade_num', cf.get('structure_grade', 0)) or 0)  # [P1-1修复 2026-07-16] grade是emoji字符串需用grade_num
        _br_pup   = cf.get('s23_p_up', 1.0)
        # [v25.4b防封闭修复] 例外条件放宽：145→140, 90→85, 0.2→0.25
        # 理由：n=233次铁证，非宪法级死穴，不应过严封闭
        if not (_br_score >= 140 and _br_grade >= 85 and _br_pup < 0.25):
            _valid = False
            _score_gate_ok = False  # [P0-1修复 2026-07-16 苏摩111] 死穴封禁同步置零，防L3742覆盖
            cf['breakdown']['死穴封禁'] = (
                f'BEAR_RECOVERY_SHORT WR=46% 物理封锁[v25.4b] '
                f'score={_br_score:.0f} grade={_br_grade} p_up={_br_pup:.2f}'
            )
            print(f'[死穴-BEAR_RECOVERY_SHORT] {_sym} 封锁: score={_br_score:.0f} grade={_br_grade} p_up={_br_pup:.2f}')
        else:
            print(f'[死穴-BEAR_RECOVERY_SHORT] {_sym} 精英解锁: score={_br_score:.0f}>=140 grade={_br_grade}>=85 p_up={_br_pup:.2f}<0.25')
    # ────────────────────────────────────────────────────────────────────────────

    # ── [P0-B 设计院 2026-06-21] BULL_TREND宏观核验门 ────────────────────────────
    # 问题：实盘回溯 BULL_TREND_LONG MAE=10.7%，小市技术反弹被误识别为 BULL_TREND
    # 修复：当 regime=BULL_TREND 且 price < EMA200日线 时，强制降级为 BEAR_RECOVERY
    # 依据：宏观熏市中日山微分不是 BULL_TREND，该信号应按 BEAR_RECOVERY 规则处理
    # [设计院] 此门展不修改 ms['regime']，仅拦截信号输出
    try:
        _p0b_regime = str(ms.get('regime', '') or '').upper()
        _p0b_price  = float(ms.get('price', 0) or 0)
        # [v6.0 设计院 2026-07-08] BEAR_RECOVERY体制豁免P0B宏观门控
        # 依据：BEAR_RECOVERY体制本身就是宏观熊市中的反弹，EMA200必然在上方
        # 该体制LONG WR=72.5%(n=603)，P0B拦截是误伤
        _is_bear_recovery = 'BEAR_RECOVERY' in _p0b_regime
        if 'BULL_TREND' in _p0b_regime and not _is_bear_recovery and signal_dir == 'LONG' and _p0b_price > 0:
            # 尝试拉取 EMA200日线（式 fib_macro结果已有）
            _p0b_ema200 = 0.0
            try:
                from fib_macro_engine import fib_macro_score as _p0b_fib
                _p0b_res = _p0b_fib(symbol=_sym, price=_p0b_price, signal_dir='LONG')
                _p0b_ema200 = float(_p0b_res.get('ema200', 0) or 0)
            except: pass
            if _p0b_ema200 > 0 and _p0b_price < _p0b_ema200:
                # [设计院 2026-07-06] P0B灰度通道: EMA200下方9%内+score>=170允许开单
                _p0b_ratio = _p0b_price / _p0b_ema200
                _P0B_GRAY_RATIO = 0.91   # EMA200下方9%内
                _P0B_GRAY_SCORE = 170    # 需超高分才允许灰度开单
                _pre_score = float(cf.get('total', 0) or 0)
                if _p0b_ratio >= _P0B_GRAY_RATIO and _pre_score >= _P0B_GRAY_SCORE:
                    cf['breakdown']['P0B_GRAY_PASS'] = (
                        f'[P0B灰度] ratio={_p0b_ratio:.3f}>={_P0B_GRAY_RATIO} score={_pre_score:.0f}>={_P0B_GRAY_SCORE} 允许'
                    )
                    pass  # [静默] f'[P0B-MacroGate] 🟡 {_sym} 灰度允许 ratio={_p0b_ratio:.3f} score={_pre_score:.0f}'
                else:
                    _score_gate_ok = False
                    cf['breakdown']['P0B_BULL_TREND_MACRO'] = (
                        f'[P0-B宏观门] price={_p0b_price:.2f} < EMA200={_p0b_ema200:.2f} '
                        f'ratio={_p0b_ratio:.3f} 封锁LONG'
                    )
    except Exception as _p0b_e:
        pass
    # ── [END P0-B 宏观门] ──────────────────────────────────────────────────────────

    _valid = cf['kelly_mult'] > 0 and params['valid'] and _score_gate_ok
    # [P2-B] N14体制边界追踪 — 记录当前体制稳定度（供brahma_core判断早鸟加成）
    _regime_now = str(ms.get('regime','') or '')
    try:
        import json as _j; from pathlib import Path as _P
        _rts_f = _P(__file__).parent.parent / 'data' / '_regime_timing_state.json'
        _rts = _j.loads(_rts_f.read_text()) if _rts_f.exists() else {}
        _last_regime = _rts.get('last_regime','')
        _last_change_ts = _rts.get('last_change_ts', 0)
        import time as _tm
        _now_ts = _tm.time()
        if _last_regime != _regime_now:
            _rts = {'last_regime': _regime_now, 'last_change_ts': _now_ts, 'last_regime_prev': _last_regime}
            _rts_f.write_text(_j.dumps(_rts))
        _regime_age_h = (_now_ts - _last_change_ts) / 3600
        extra_data['regime_timing'] = {
            'current': _regime_now,
            'age_hours': round(_regime_age_h, 1),
            'is_early': _regime_age_h < 5,   # 体制切换5h内为"早鸟"
            'prev': _rts.get('last_regime_prev','')
        }
        # 早鸟加成（N14: BEAR_TREND(熊市趋势) early PF=1.625）
        if _regime_age_h < 5 and 'BEAR_TREND' in _regime_now and signal_dir == 'SHORT' and _score_gate_ok:
            _score_raw = round(_score_raw * 1.04, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['n14_early_bird'] = f'BEAR_TREND早鸟({_regime_age_h:.1f}h) ×1.04'
            pass  # [静默] f'[BrahmaBrain] 🦅 {_sym} N14早鸟: {_regime_now} {_regime_age_h:.1f}h 进入 score→{_sc

        # ── [P3 TREND_fresh Elite v3.0 苏摩111 2026-06-28] ─────────────────
        # 铁证：TREND体制刚进入1-2根4H K线时 WR=75.6% EV=+0.687%（v3.0实盘对齐 n=334）
        # 机制：从 _regime_timing_state 的 age_hours 换算4H根数（1根4H≈4h）
        # 条件：顺势方向 + fresh窗口(≤2根≈≤8h) + score门控通过
        _bars_est = max(1, round(_regime_age_h / 4))  # 时间→4H根数估算
        extra_data['regime_timing']['bars_est'] = _bars_est
        _trend_fresh_regimes = {
            'BEAR_TREND': 'SHORT',
            'BULL_TREND': 'LONG',
        }
        _tf_expected_dir = _trend_fresh_regimes.get(_regime_now)

        # ── [P1 RSI>60做空专项加分 v3.0 苏摩111 2026-06-28] ──────────────────
        # 铁证：BTC BEAR_TREND_SHORT RSI>60 WR=68.1% EV=+0.458%（vs RSI<40 EV=+0.169%）
        # 条件：BEAR_TREND体制 + SHORT方向 + RSI>60
        _rsi_for_p1 = float(ms.get('rsi_1h', ms.get('rsi', 50)) if ms else 50)
        if (signal_dir == 'SHORT'
                and 'BEAR_TREND' in _regime_now
                and _rsi_for_p1 > 60
                and _score_gate_ok
                and not _direction_block):
            _p1_bonus = 5  # +5分：RSI>60做空 EV差2.7倍
            _score_raw = round(_score_raw + _p1_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p1_rsi60_short'] = (
                f'RSI>60做空({_rsi_for_p1:.0f}) +{_p1_bonus}分 WR=68.1%(v3.0)')
            pass  # [静默] f'[P1-RSI60] 🎯 {_sym} RSI={_rsi_for_p1:.0f} BEAR_TREND SHORT: +{_p1_bonus}分 scor
        # ── [END P1 RSI>60] ──────────────────────────────────────────────────

        if (_bars_est <= 2
                and _tf_expected_dir == signal_dir
                and _score_gate_ok
                and _regime_now in _trend_fresh_regimes):
            _fresh_bonus = 15  # [v3.0 苏摩111 2026-06-28] +15分：达摩院v3.0铁证 BTC WR=75.6% EV=+0.687% n=334
            _score_raw = round(_score_raw + _fresh_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p3_trend_fresh'] = (
                f'TREND_fresh({_regime_now} age≈{_bars_est}根) +{_fresh_bonus}分 WR=75.6%(v3.0)')
        elif (_bars_est in (3, 4)
                and _tf_expected_dir == signal_dir
                and _score_gate_ok
                and _regime_now in _trend_fresh_regimes):
            # [v3.0 苏摩111 2026-06-28] EARLY_golden +8分：BTC WR=62.6% EV=+0.282% n=255
            _early_bonus = 8
            _score_raw = round(_score_raw + _early_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p3_trend_early'] = (
                f'TREND_early({_regime_now} age≈{_bars_est}根) +{_early_bonus}分 WR=62.6%(v3.0)')
        # ── [END P3 TREND_fresh/early] ────────────────────────────────────────
    except Exception: pass

    # ── [B2 v2 2026-05-31 设计院重写] 结构甜点区奖励 ────────────────────────────
    # 实证铁律（376条live信号）：
    #   gap<0.5%   实盘SL组均值0.57% → 极危险，入场即止损 → -15分
    #   gap 0.5-1.0% 同属SL危险区       → -8分
    #   gap 1.0-1.5% WR=40%            → 边界，中性 → 0分
    #   gap 1.5-4.0% TP组均值2.43% WR=100% → 甜点区 → +15分
    #   gap>4%   偏远难触发              → -5分
    # 铁证来源：52条实盘结算 TP组gap均值=2.43% vs SL组gap均值=0.57%（2026-05-31）
    try:
        _entry_lo_b2 = float(params.get('entry_lo', 0) or 0)
        _price_b2    = float(ms.get('price', 0) or 0)
        _b2_bonus    = 0

        # [P0-A B2-fix 2026-06-17] 修复LONG方向gap计算（原逻辑只处理SHORT）
        _entry_hi_b2 = float(params.get('entry_hi', params.get('entry_lo', 0)) or 0)
        _gap_b2 = 0.0
        _b2_dir_ok = False
        if _entry_lo_b2 and _price_b2 and signal_dir == 'SHORT':
            _gap_b2 = (_entry_lo_b2 - _price_b2) / _price_b2 * 100
            _b2_dir_ok = True
        elif _entry_hi_b2 and _price_b2 and signal_dir == 'LONG':
            # LONG: 价格回落到入场区间，gap = (price - entry_hi) / price * 100
            # gap<0 = 已在区间内（最优），gap>0 = 还需等待回落
            _gap_b2 = (_price_b2 - _entry_hi_b2) / _price_b2 * 100
            _b2_dir_ok = True
        if _b2_dir_ok and (_entry_lo_b2 if signal_dir=='SHORT' else _entry_hi_b2):
            if _gap_b2 < 0.5:
                # [v3修复 2026-05-31] 极危险：入场即止损，SL组实盘均值0.57%在此区间
                _b2_bonus = -15
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}%<0.5% 极危险(WR=3%) -15'  # [B2-fix]
            elif _gap_b2 < 1.0:
                # [v3修复 2026-05-31] 危险区：SL组均值0.57%全部落在此区间
                _b2_bonus = -8
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 危险区(SL高频) -8'  # [B2-fix]
            elif _gap_b2 <= 1.5:
                # 边界区，中性
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 边界区 中性'  # [B2-fix]
            elif _gap_b2 <= 4.0:
                # 甜点区：TP组实盘均值2.43%，WR=100%实证奖励
                _b2_bonus = 15
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 甜点区(WR=100%) +15'  # [B2-fix]
            else:
                # >4% 偏远难触发
                _b2_bonus = -5
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}%>4% 偏远难触发 -5'  # [B2-fix]

        if _b2_bonus != 0 and _score_gate_ok:
            _score_raw = round(_score_raw + _b2_bonus, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            if _score_raw < 0: _score_raw = 0
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['total'] = _score_raw
            pass  # [静默] f'[B2-Structure] {"⚠️" if _b2_bonus < 0 else "✅"} {_sym}: gap={_gap_b2:.2f}% {_b

        # ── [B2 v5 V2.0报告P0-A修复 2026-06-05] GapGate逻辑倒转
        # Round2铁证：BTC/ETH全部55+58条成功信号 gap均<0.5%（gap越小=最优入场）
        # 原逻辑完全反了：gap<0.8%需165分 = 封锁最赚钱的信号类型
        # 新规则（按V2.0报告）：
        #   gap < 0   → 价格在入场区内，直接允许（最优状态）
        #   gap 0~0.5% → 贴近区间，score≥140允许（非常好）
        #   gap 0.5~1% → 轻微偏离，score≥140允许（好）
        #   gap 1~3%  → 回调区间，score≥150允许（一般）
        #   gap 3~5%  → 偏远，score≥160允许（需结构极强）
        #   gap > 5%  → 极偏远，score≥165允许（稀有但允许）
        try:
            if _entry_lo_b2 and _price_b2 and signal_dir == 'SHORT':
                _gap_check = (_entry_lo_b2 - _price_b2) / _price_b2 * 100
                if _gap_check < 0:
                    # 价格已在入场区内 → 最佳状态，直接通过
                    cf['gap_gate'] = f'gap={_gap_check:.2f}% 价格在入场区内 命中 通过'
                    pass  # [静默] f'[GapGate] ✅ {_sym}: gap={_gap_check:.2f}% 价格在入场区内，允许'
                # [v24.3-fix] GapGate: score=0清零 → 按gap比例降权
                # 哲学：距入场区越远惩罚越重，但不清零——让grade门控最终拍板
                elif _gap_check < 0.5:   _gap_penalty = 0   # 贴近：不惩罚
                elif _gap_check < 1.0:   _gap_penalty = 4   # [六方修复] 6→4，BEAR_RECOVERY追涨行情轻惩
                elif _gap_check < 2.0:   _gap_penalty = 8   # [六方修复] 12→8
                elif _gap_check < 3.0:   _gap_penalty = 14  # [六方修复] 18→14，3%内不过分惩罚
                elif _gap_check < 5.0:   _gap_penalty = 22  # [六方修复] 25→22
                elif _gap_check < 10.0:  _gap_penalty = 32  # [六方修复] 35→32
                elif _gap_check < 20.0:  _gap_penalty = 45  # [六方修复] 50→45
                # BEAR_RECOVERY/BULL_EARLY体制额外宽松（追涨不追跌是反弹特征）
                _gap_regime = ms.get('regime','') if ms else ''
                if _gap_regime in ('BEAR_RECOVERY','BULL_EARLY','BULL_CORRECTION') and _gap_check < 5.0:
                    _gap_penalty = max(0, _gap_penalty - 8)  # 反弹体制减8分惩罚
                else:  # gap>20% 直接封锁
                    _gap_penalty = 0; _score_raw = 0; cf['total'] = 0  # [P1-B fix] gap>20%极端封锁
                if _gap_check >= 0.5:
                    _score_raw = max(0, _score_raw - _gap_penalty)
                    cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
                    cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
                    cf['gap_gate'] = f'gap={_gap_check:.2f}% -惩罚{_gap_penalty}分 → score={_score_raw:.0f}'
                    pass  # [静默] f'[GapGate] ⚠️ {_sym}: gap={_gap_check:.2f}% -{_gap_penalty}分 score={_score_raw:
                else:
                    cf['gap_gate'] = f'gap={_gap_check:.2f}%<0.5% 贴近 通过'
                    pass  # [静默] f'[GapGate] ✅ {_sym}: gap={_gap_check:.2f}% 贴近'
        except Exception: pass
    except Exception: pass
    # ── [END B2 v3] | B2 v3 段结束 ──────────────────────────────────────────────────────────

    # ── [设计院 2026-05-31] 可交易性辅助（结构门已是主力）──────────────────
    # 注：ATR门卫和WR封顶已移除，由结构质量引擎(L0)负责识别
    # 只保留入场区偏离作为轻微提示，不再是主要惩罚
    try:
        _entry_lo_t = float(params.get('entry_lo', 0) or 0)
        _price_t    = float(ms.get('price', 0) or 0)
        _t_penalty  = 0

        # 入场区偏离（保留，但只作轻提示，结构门已处理主要问题）
        if _entry_lo_t and _price_t and signal_dir == 'SHORT':
            _entry_gap = (_entry_lo_t - _price_t) / _price_t * 100
            if _entry_gap > 5.0:
                _t_penalty += 15   # 从30降至15，结构门已惩罚
                cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['t_score_gap'] = f'入场区偏离{_entry_gap:.1f}%>5% -15分'
            elif _entry_gap > 3.0:
                _t_penalty += 8    # 从15降至8
                cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['t_score_gap'] = f'入场区偏离{_entry_gap:.1f}%>3% -8分'

        if _t_penalty > 0 and _score_gate_ok:
            _score_raw = max(0, round(_score_raw - _t_penalty, 1))
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['total'] = _score_raw
    except Exception:
        pass
    # ── [END 可交易性辅助] ────────────────────────────────────────────────────

    # ── [设计院 2026-05-31] L0 结构质量门（Structure Quality Gate）─────────
    # 哲学：好信号的本质是「入场区有真实价格结构」，而非「评分高」
    # 无结构入场(grade<30) = 拒绝，无论评分多高
    try:
        from structure_quality_engine import evaluate_structure_quality, get_time_weight  # [D1-note] 按需import(主SQE)
        _sq = evaluate_structure_quality(
            symbol     = _sym,
            signal_dir = signal_dir,
            price      = float(ms.get('price', 0)),
            entry_lo   = float(params.get('entry_lo', 0) or 0),
            entry_hi   = float(params.get('entry_hi', 0) or 0),
            smc        = smc,
            swing_4h   = ms.get('swing_4h', {}),
            key_levels = ms.get('key_levels', {}),
            momentum   = ms.get('momentum', {}),
            trigger_confidence = int(params.get('trigger_15m_confidence', 0) or cf.get('trigger_15m_confidence', 0) or 0),  # [v24.5-fix] 优先从 params 读取
        )
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['structure_grade']  = _sq['grade']
        cf['structure_label']  = _sq['label']
        cf['structure_sources']= _sq['sources']

        # ── 结构质量联合门控 [v24.2 2026-06-12 铁征升级] ─────────────────────
        # 武曲Paper干净68条实战铁证：
        #   grade≥70 (A级): WR=92% TO率=8%   → 正常通过
        #   grade 50-69 (B级): WR=27% TO率=73% → 全局封堵
        #   grade 25-49 (C级): WR=0%  TO玗=100% → 封堵
        #   grade<25 (X级):   完全无结构 → 封堵

        # ── [effective_grade v25.4b 2026-06-27] 体制感知 grade 修正 ──────────
        # 哲学：同样的 OB 结构，在不同体制下可信度不同
        # 熊市做多 = 趋势反向，OB 支撑极易被贯穿，grade 实际价值打折
        # 铁证依据：BEAR_TREND_LONG grade_<70 WR=44.6% vs grade_90+ WR=71.3%
        #           BULL_TREND_SHORT 对称成立
        # 设计院×达摩院六方裁决 2026-06-27
        _REGIME_GRADE_MULT = {
            # LONG 方向：顺势=1.0，逆势递减至0.72
            ('BULL_TREND',      'LONG'):  1.00,
            ('BULL_EARLY',      'LONG'):  0.95,
            ('BULL_CORRECTION', 'LONG'):  0.90,
            ('BULL_RECOVERY',   'LONG'):  0.92,
            ('CHOP',            'LONG'):  0.88,
            ('CHOP_MID',        'LONG'):  0.88,
            ('CHOP_HIGH',       'LONG'):  0.85,
            ('CHOP_LOW',        'LONG'):  0.90,
            ('BEAR_RECOVERY',   'LONG'):  0.88,
            ('BEAR_EARLY',      'LONG'):  0.82,
            ('BEAR_CORRECTION', 'LONG'):  0.80,
            ('BEAR_TREND',      'LONG'):  0.72,  # 最危险：逆势做多 WR=44.6%
            # SHORT 方向：顺势=1.0，逆势递减至0.72
            ('BEAR_TREND',      'SHORT'): 1.00,
            ('BEAR_EARLY',      'SHORT'): 0.95,
            ('BEAR_CORRECTION', 'SHORT'): 0.90,
            ('BEAR_RECOVERY',   'SHORT'): 0.88,
            ('CHOP',            'SHORT'): 0.88,
            ('CHOP_MID',        'SHORT'): 0.88,
            ('CHOP_HIGH',       'SHORT'): 0.85,
            ('CHOP_LOW',        'SHORT'): 0.90,
            ('BULL_RECOVERY',   'SHORT'): 0.88,
            ('BULL_EARLY',      'SHORT'): 0.82,  # 死穴体制 WR=51.6%
            ('BULL_CORRECTION', 'SHORT'): 0.80,
            ('BULL_TREND',      'SHORT'): 0.72,  # 最危险：逆势做空 WR=48.2%
        }
        _raw_grade   = int(cf.get('structure_grade', 0) or 0)
        _regime_key  = str(ms.get('regime', '')).upper()
        # 体制键匹配：优先精确匹配，fallback到前缀匹配
        _mult = 1.00  # 默认不降权
        for (r_pat, d_pat), m in _REGIME_GRADE_MULT.items():
            if signal_dir == d_pat and (r_pat in _regime_key or _regime_key.startswith(r_pat)):
                _mult = m
                break
        _eff_grade = round(_raw_grade * _mult, 1)
        cf['effective_grade'] = _eff_grade
        cf['grade_mult']      = _mult
        # StructureGate 使用 effective_grade
        _sq = {'grade': _eff_grade, 'label': cf.get('structure_label', f'grade={_eff_grade:.0f}')}
        # [v25.4 死穴修复 2026-06-27] StructureGate 门槛 70→80
        # 设计院达摩院六方裁决：grade70-80 实测WR=47%（死亡区），与grade<70同性质
        # 真正优质结构从 grade≥80 开始（WR=69.8%）
        # [v5.1 设计院 2026-07-03] BULL_TREND三重特例通道（苏摩授权）
        # 条件：BULL_TREND体制 + grade≥75 + score≥155 + EMA200宏观通过
        # 依据：grade70-80的WR=47%统计混入大量逆势SHORT，BULL×LONG实际WR更高
        # [v6.0 设计院 2026-07-08] 新增BEAR_RECOVERY特例通道
        # 依据：BEAR_RECOVERY体制LONG WR=72.5%(n=603)，grade75-79实测WR=65%+
        # 条件：BEAR_RECOVERY体制 + grade≥75 + score≥155（保留三重防护）
        _bull_grade_exception = (
            (
                'BULL_TREND' in _regime_key
                or 'BEAR_RECOVERY' in _regime_key  # [v6.0] BEAR_RECOVERY WR=72.5%
            )
            and signal_dir == 'LONG'
            and _sq['grade'] >= 75
            and (
                _score_raw >= 155                        # 标准屠饮
                or (
                    'BEAR_RECOVERY' in _regime_key       # [设计院封印 2026-08-20]
                    and _score_raw >= 100                # BEAR_RECOVERY WR=88%，降低门槛到100
                )                                        # 根因：实测WR=88% n=8但仅产生8条信号，严重undersampled
            )
        )
        # [2026-08-14 高频开单 设计院封印] BEAR_TREND做空特例通道
        # 依据: BEAR_TREND:SHORT WR=87% 是死稴最高胜率体制，grade75~79可信任
        _bear_short_exception = (
            'BEAR_TREND' in _regime_key
            and signal_dir == 'SHORT'
            and _sq['grade'] >= 75
            and _score_raw >= 130  # 低于BULL的155门槛，因BEAR体制本身就是共识
        )
        if _sq['grade'] < 80 and not _bull_grade_exception and not _bear_short_exception:
            # grade<80: 包含grade70-79死亡区（WR=47%）全部封堵
            _score_raw = 0
            cf['total'] = 0
            cf['action'] = 'SKIP'
            cf['kelly_mult'] = 0
            cf['structure_reject'] = f'grade={_sq["grade"]}({_sq["label"]}) grade<80 WR=47%死亡区封堵 [v25.4]'
            pass
        elif _sq['grade'] < 80 and (_bull_grade_exception or _bear_short_exception):
            # 特例通道：BULL_TREND LONG grade75~79 或 BEAR_TREND SHORT grade75~79
            pass
        elif _sq['grade'] >= 90:
            _sq_bonus = round((_sq['grade'] - 80) * 0.3, 1)
            _score_raw = round(_score_raw + _sq_bonus, 1)
            cf['total'] = _score_raw
            pass  # [静默] f'[StructureGate] ✅ {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]} +{_
        else:  # grade 80-89
            _sq_bonus = round((_sq['grade'] - 80) * 0.15, 1)
            _score_raw = round(_score_raw + _sq_bonus, 1)
            cf['total'] = _score_raw
            pass  # [静默] f'[StructureGate] ✅ {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]} +{_
        # [v25.4] grade 80-89: 正常通过，小额加分
        # else分支不需要（grade<70已在if分支封堵）

        # 时间权重：记录但不惩罚（UTC14-16样本仅12条，统计不显著）
        _utc_hour = _dt.datetime.now(_dt.timezone.utc).hour
        _tw = get_time_weight(_utc_hour)
        cf['time_weight_ref'] = f'UTC{_utc_hour:02d}:00 ref={_tw}'  # 仅记录，不调分
    except Exception as _sqe:
        pass
    # ── [END 结构质量门] ──────────────────────────────────────────────────────

    # ── [v25.7 设计院 2026-06-18] P0 体制专项过滤器 ─────────────────────────
    # 原则：为交易而生，不封禁；通过精准条件过滤提升低WR组合质量
    # 每个体制×方向组合针对其根本失败原因做专项检测
    try:
        _regime_now = _matched_regime_key or ''
        _p0_reject  = False
        _p0_reason  = ''

        # ── P0-A: BULL_CORRECTION（牛市回调）× LONG ────────────────────────
        # 根因：接刀问题（回调未到OB支撑位就做多）+ ob_dist>1.5%失去锚点
        # 修复：强制要求 ob_dist_pct<1.5%（B级以上精准支撑）
        if _regime_now == 'BULL_CORRECTION' and signal_dir == 'LONG':
            _ob_dist = cf.get('ob_dist_pct', 99)
            if _ob_dist is None: _ob_dist = 99
            if float(_ob_dist) > 1.5:
                _p0_reject = True
                _p0_reason = f'P0-A BULL_CORRECTION_LONG: ob_dist={_ob_dist:.2f}%>1.5%（未到OB支撑位，拒绝接刀）'

        # ── P0-B: BEAR_RECOVERY（熊市反弹）× SHORT ─────────────────────────
        # 根因：反弹途中做空=与动能对抗；只有反弹至阻力位才有alpha
        # 修复：要求 price≥swing_high_4h×0.95（反弹至4H摆动高点附近才空）
        elif _regime_now == 'BEAR_RECOVERY' and signal_dir == 'SHORT':
            try:
                _sw4h_h = cf.get('swing_high_4h', 0) or 0
                _cur_price = ms.get('price', ms.get('close', 0)) or 0
                if _sw4h_h > 0 and _cur_price > 0:
                    _dist_to_swing = (_sw4h_h - _cur_price) / _sw4h_h
                    if _dist_to_swing > 0.05:   # 距4H高点>5%，反弹尚未到位
                        _p0_reject = True
                        _p0_reason = (f'P0-B BEAR_RECOVERY_SHORT: price={_cur_price:.1f} '
                                      f'距4H高点{_dist_to_swing*100:.1f}%>5%（反弹未到阻力位，拒绝逆势空）')
            except Exception:
                pass  # 数据不可用时放行

        # ── P0-C: BULL_TREND（牛市趋势）× SHORT 回调深度过滤 ──────────────
        # 根因：牛市小回调噪音做空，没有吃到中级回调
        # 修复：价格需从近期高点下跌≥1.2×ATR（真正的中级回调信号）
        elif _regime_now == 'BULL_TREND' and signal_dir == 'SHORT':
            try:
                _atr4h = ms.get('atr_4h', ms.get('atr', 0)) or 0
                _high4h = max(ms.get('highs_4h', ms.get('highs', [0]))[-6:] or [0])
                _cur_price = ms.get('price', ms.get('close', 0)) or 0
                if _atr4h > 0 and _high4h > 0 and _cur_price > 0:
                    _pullback = (_high4h - _cur_price) / _cur_price
                    _atr_pct  = _atr4h / _cur_price
                    if _pullback < _atr_pct * 1.2:
                        # 回调幅度不足1.2×ATR，小回调噪音，门控+10
                        _score_raw = round(_score_raw - 10, 1)
                        cf['total'] = _score_raw
                        cf['p0c_pullback_penalty'] = f'-10(回调{_pullback*100:.1f}%<1.2×ATR{_atr_pct*100:.1f}%)'
            except Exception:
                pass

        # ── P0-D: BEAR_TREND（熊市趋势）× LONG BOTTOMING子阶段奖励 ────────
        # 根因：BOTTOMING阶段（RSI超卖+背离+Higher Low）有真实alpha
        # 修复：检测到BOTTOMING特征时，门控降低-15（增加通过机会）
        elif _regime_now == 'BEAR_TREND' and signal_dir == 'LONG':
            try:
                _phase_1h  = str(ms.get('phase_1h', ms.get('phase', ''))).upper()
                _rsi_1h    = ms.get('rsi', ms.get('rsi_1h', 50)) or 50
                _phase_4h  = str(ms.get('phase_4h', '')).upper()
                _is_bottom = (_phase_1h in ('BOTTOMING','PULLBACK_UP') and _rsi_1h < 38)
                _is_4h_ok  = (_phase_4h in ('BOTTOMING','UPTREND','PULLBACK_UP'))
                if _is_bottom and _is_4h_ok:
                    # 真正的底部结构 → 额外奖励（相当于门控降低）
                    _bot_bonus = 15
                    _score_raw = round(_score_raw + _bot_bonus, 1)
                    cf['total'] = _score_raw
                    cf['p0d_bottoming_bonus'] = f'+{_bot_bonus}(BOTTOMING结构:1H={_phase_1h} RSI={_rsi_1h:.0f} 4H={_phase_4h})'
            except Exception:
                pass

        if _p0_reject:
            _score_gate_ok = False
            cf['p0_reject'] = _p0_reason
            cf['kelly_mult'] = 0
            pass  # [静默] f'[P0SpecialFilter] 🚫 {_sym} {signal_dir}: {_p0_reason[:80]}'

    except Exception as _p0e:
        pass  # P0过滤器异常不阻塞主流程

    # ── [设计院 2026-06-07] N20 LSR+OI联合评分（六方辩论落地）────────────────
    # 实证：ETH多头70.9%→空头做空+15分，OI减少+价格涨→做多-12分
    try:
        from lsr_oi_engine import lsr_oi_score as _lsr_oi_fn
        # [修复 2026-07-08 设计院] 补传 price_change_pct（4H价格变化）
        # 修复前：N20自行拉取API，丢失上下文，OI方向解读可能错误
        # 修复后：从已缓存的k4h计算精确4H变化，区分「多头离场」vs「空头建仓」
        _k4h_cls = extra_data.get('_k4h_closes', []) if extra_data else []
        _price_chg_4h = round(
            (_k4h_cls[-1] - _k4h_cls[-5]) / _k4h_cls[-5] * 100, 2
        ) if len(_k4h_cls) >= 5 else 0.0
        _lsr_oi_res  = _lsr_oi_fn(
            symbol    = _sym,
            signal_dir= signal_dir,
            long_pct  = ms.get('sentiment', {}).get('long_short_ratio'),
            oi_change_pct = ms.get('sentiment', {}).get('oi_change_pct'),
            oi_momentum   = ms.get('sentiment', {}).get('oi_momentum'),
            price_change_pct = _price_chg_4h,  # [修复] 精确4H变化传入
        )
        _lsr_oi_pts = _lsr_oi_res.get('score', 0)
        if _lsr_oi_pts != 0 and _score_raw > 0:
            _score_raw = round(_score_raw + _lsr_oi_pts, 1)
            cf['total'] = _score_raw
            cf['n20_lsr_oi'] = _lsr_oi_res.get('note', '')
    except Exception as _lsr_e:
        pass
    # ── [END N20 LSR+OI] | N20 多空比+持仓量段结束 ─────────────────────────────────────────────────────

    # ── [设计院 2026-06-07] N21 宏观Fib+EMA200+周线RSI（六方辩论落地）────────
    # 实证：ETH低于EMA200(-14.8%)→做多-10，周线RSI=50(非底部)→做多-8
    try:
        from fib_macro_engine import fib_macro_score as _fib_macro_fn
        _fib_res  = _fib_macro_fn(
            symbol    = _sym,
            price     = float(ms.get('price', 0)),
            signal_dir= signal_dir,
        )
        _fib_pts = _fib_res.get('score', 0)
        if _fib_pts != 0 and _score_raw > 0:
            _score_raw = round(_score_raw + _fib_pts, 1)
            cf['total'] = _score_raw
            cf['n21_fib_macro'] = f"regime={_fib_res.get('regime_tag','')} ema200=${_fib_res.get('ema200',0):,.0f} wRSI={_fib_res.get('weekly_rsi',0):.0f} {_fib_pts:+d}pts"
            pass  # [静默] f'[N21-FibMacro] {_sym} {signal_dir}: {_fib_pts:+d}分 → {_score_raw:.0f} | {_fib_
    except Exception as _fib_e:
        pass
    # ── [END N21 宏观Fib] ────────────────────────────────────────────────────


    # ── [N22b] WR矩阵动态加成层 v8 RSI分层版 [2026-08-29 苏摩111] ──────────────
    # 升级: v7(体制×方向 ALL平均) → v8(体制×方向×RSI分层，6.5年铁证)
    # 铁证: BEAR_TREND SHORT RSI>60 WR=62.8% n=613 EV=+0.512
    #       BEAR_TREND SHORT RSI>70 WR=66.7% n=90  EV=+0.667
    #       v7 ALL=51.9% → RSI分层后精准区间WR差距达15%
    try:
        import json as _j22b
        _rsi22b = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        _regime22b = str(ms.get('regime', '')).upper()
        _combo22   = f"{_regime22b}_{signal_dir}"

        # ── v8 RSI分桶 ─────────────────────────────────────────────────
        def _rsi_bucket_v8(rsi):
            if rsi < 40:  return 'RSI_0_40'
            if rsi < 50:  return 'RSI_40_50'
            if rsi < 55:  return 'RSI_50_55'
            if rsi < 60:  return 'RSI_55_60'
            if rsi < 70:  return 'RSI_60_70'
            return 'RSI_70_100'

        _bucket22b = _rsi_bucket_v8(_rsi22b)
        _v8_path   = __import__('pathlib').Path(__file__).parent.parent / 'data' / 'wr_matrix_v8_6y5.json'
        _wv8       = _j22b.loads(_v8_path.read_text()) if _v8_path.exists() else {}
        _sym_wv8   = _wv8.get(_sym, _wv8.get('BTC', {}))  # 山寨fallback到BTC
        _regime_wv8 = _sym_wv8.get(_regime22b, {})
        _dir_wv8    = _regime_wv8.get(signal_dir, {})
        # 优先RSI分桶，fallback ALL
        _wdata22   = _dir_wv8.get(_bucket22b) or _dir_wv8.get('ALL', {})
        _used_bucket = _bucket22b if _bucket22b in _dir_wv8 else 'ALL'

        _wr22b  = float(_wdata22.get('wr', 0)) / 100 if _wdata22.get('wr', 0) > 1 else float(_wdata22.get('wr', 0))
        _n22b   = int(_wdata22.get('n', 0))
        _ev22b  = float(_wdata22.get('ev', 0))
        _pts22b = 0
        if _n22b >= 50 and _wr22b > 0:
            # WR→评分: WR=65%→+6  WR=55%→+2  WR=50%→0  WR=45%→-2  WR=35%→-6
            _pts22b = max(-10, min(12, round((_wr22b - 0.50) * 20)))
            # EV加权：EV>0.3额外+2，EV<-0.3额外-2
            if _ev22b > 0.3:   _pts22b = min(14, _pts22b + 2)
            elif _ev22b < -0.3: _pts22b = max(-12, _pts22b - 2)
        elif _n22b < 50 and _n22b > 0:
            _pts22b = 0  # 样本不足跳过

        # 若v8没有数据，fallback v7
        if _n22b == 0:
            _dm22b = _j22b.loads(open('data/dharma_runtime.json').read())
            _wv7   = _dm22b.get('wr_matrix_v7', {})
            _sym_wv7 = _wv7.get(_sym, {})
            _wdata_v7 = _sym_wv7.get(_combo22, {})
            _act22 = _wdata_v7.get('action', 'SKIP')
            _wr22b = _wdata_v7.get('wr', 0)
            _n22b  = _wdata_v7.get('n', 0)
            if _act22 == 'ALLOW' and _n22b >= 500:
                _pts22b = max(-10, min(15, round((_wr22b - 0.50) * 20)))
            elif _act22 in ('BLOCK', 'PERMANENT_BLOCK'):
                _pts22b = -15
            elif _act22 == 'PENALIZE':
                _pts22b = int(_wdata_v7.get('penalize_pts', -10))
            _used_bucket = 'v7_fallback'

        if _pts22b != 0:
            _score_raw += _pts22b
            cf['n22b_wr_matrix'] = (f'N22b_WRv8:{_pts22b:+d}'
                f'({_combo22}@{_used_bucket} WR={_wr22b:.1%} n={_n22b} EV={_ev22b:+.3f})')
    except Exception:
        pass
    # ── [END N22b] ──────────────────────────────────────────────────────────
    # ── [EarlyTrendGate v25.4 死穴修复 2026-06-27] ──────────────────────────
    # 针对宪法级死穴：BULL_EARLY_SHORT(n=5526 WR=51.6%) / BEAR_EARLY_LONG(n=5070 WR=50.5%)
    # 机制：体制逆势方向检测 → N22b已-10分 + 结构确认再-8分（叠加-18分）
    # 豁免：RSI极值（超卖<25做多 / 超买>75做空）→ 仅保留-10分
    try:
        _etg_regime = str(ms.get('regime', '')).upper()
        _etg_dir    = signal_dir
        _etg_rsi1h  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        _etg_active = False
        _etg_exempt = False  # RSI极值豁免

        if 'BULL_EARLY' in _etg_regime and _etg_dir == 'SHORT':
            _etg_active = True
            _etg_exempt = (_etg_rsi1h > 75)  # 超买区做空，豁免结构惩罚
        elif 'BEAR_EARLY' in _etg_regime and _etg_dir == 'LONG':
            _etg_active = True
            _etg_exempt = (_etg_rsi1h < 25)  # 极度超卖做多，豁免结构惩罚

        if _etg_active and not _etg_exempt:
            # [P1-2 设计院 2026-06-30] 双重惩罚修复：乘数→惩罚 二选一
            # 原：N22b已-10分 + ETG再-8分 = -18分（双重惩罚哲学矛盾）
            # 新：N22b已有惩罚 → ETG仅补充-3分（确保不超过-10分总惩罚上限）
            # 逻辑：N22b是数据驱动的WR惩罚，ETG是体制方向确认，职责不同不应叠加同等权重
            _etg_n22b_applied = cf.get('n22b_wr_matrix', '') != ''  # N22b是否已惩罚
            _etg_penalty = -3 if _etg_n22b_applied else -8  # N22b已惩罚则ETG仅补-3
            _score_raw = round(_score_raw + _etg_penalty, 1)
            cf['total'] = _score_raw
            cf['etg_penalty'] = (
                f'EarlyTrendGate[v25.4-P1fix]: {_etg_regime}×{_etg_dir} '
                f'逆势 RSI={_etg_rsi1h:.0f} {_etg_penalty:+d}分({"N22b已惩罚,仅补充" if _etg_n22b_applied else "独立惩罚"}) → {_score_raw:.0f}'
            )
            pass  # [静默] f'[EarlyTrendGate] {_sym} {_etg_regime}×{_etg_dir}: {_etg_penalty:+d}分 RSI={_etg
        elif _etg_active and _etg_exempt:
            pass  # [静默] f'[EarlyTrendGate] {_sym} {_etg_regime}×{_etg_dir}: RSI极值豁免 RSI={_etg_rsi1h:.0f}
    except Exception:
        pass
    # ── [END EarlyTrendGate] ─────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════
    # [P0 苏摩111 2026-06-28] BEAR_EARLY+TC≥+1 门控
    # 正确位置：所有因子计算完毕后 _score_raw = 最终值
    # 铁证：BEAR_EARLY+tc=+1 BTC WR=91.9% ETH=84.7% (p=0.000 n=104)
    #        BEAR_EARLY+tc=-3 WR=53.8%（差距3.4倍）
    # ══════════════════════════════════════════════════════════════
    try:
        _tc_p0 = int(ms.get('tc', 0) if ms else 0)
        if 'BEAR_EARLY' in str(ms.get('regime','') if ms else '').upper() and signal_dir == 'SHORT':
            if _tc_p0 >= 1:
                _p0_bonus = 15
                _score_raw = min(175, round(_score_raw + _p0_bonus, 1))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p0_bear_early_tc'] = (
                    f'BEAR_EARLY+tc={_tc_p0:+d}(空头排列) +{_p0_bonus}分 WR=91.9%(v4.0)')
                pass  # [静默] f'[P0-BearEarlyTC] 🎯 {_sym} BEAR_EARLY tc={_tc_p0:+d}: +{_p0_bonus}分 score→{_sco
            elif _tc_p0 <= -2:
                _p0_penalty = -10
                _score_raw = max(0, round(_score_raw + _p0_penalty, 1))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p0_bear_early_tc'] = (
                    f'BEAR_EARLY+tc={_tc_p0:+d}(多头排列做空) {_p0_penalty}分 WR=53.8%')
                pass  # [静默] f'[P0-BearEarlyTC] ⚠️ {_sym} BEAR_EARLY tc={_tc_p0:+d}: {_p0_penalty}分 score→{_s
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # ══════════════════════════════════════════════════════════════
    # [P1 苏摩111 2026-06-28] BTC领先ETH（跨标的领先指标）
    # 铁证：BTC_TP后1-4H内ETH WR=85.7% EV=+1.396%（宪法级）
    #        BTC_SL后1-4H内ETH WR=21.8%（几乎必亏）
    # ══════════════════════════════════════════════════════════════
    try:
        if _sym in ('ETHUSDT',) and signal_dir == 'SHORT':
            import pathlib as _pl1, time as _tl1
            _bsp = _pl1.Path('data/btc_settlement_state.json')
            if _bsp.exists():
                _bst = __import__('json').loads(_bsp.read_text())
                _bres = _bst.get('last_result', '')
                _bts  = float(_bst.get('last_ts', 0))
                _bh   = (_tl1.time() - _bts) / 3600
                if 0 < _bh <= 4:
                    if _bres in ('TP1', 'TP2', 'TP'):  # [fix 2026-07-27 兼容TP1/TP2]
                        _p1v = 20
                        _score_raw = min(175, round(_score_raw + _p1v, 1))
                        cf['total'] = _score_raw
                        cf.setdefault('breakdown', {})['p1_btc_lead'] = (
                            f'BTC_TP领先{_bh:.1f}H +{_p1v}分 WR=85.7%(宪法级)')
                        pass  # [静默] f'[P1-BTCLead] 🚀 ETH BTC_TP {_bh:.1f}H前: +{_p1v}分 score→{_score_raw:.0f}'
                    elif _bres == 'SL':
                        _p1v = -25
                        _score_raw = max(0, round(_score_raw + _p1v, 1))
                        cf['total'] = _score_raw
                        cf.setdefault('breakdown', {})['p1_btc_lead'] = (
                            f'BTC_SL领先{_bh:.1f}H {_p1v}分 WR=21.8%')
                        pass  # [静默] f'[P1-BTCLead] ☠️ ETH BTC_SL {_bh:.1f}H前: {_p1v}分 score→{_score_raw:.0f}'
    except Exception: pass

    # ══════════════════════════════════════════════════════════════
    # [P2 苏摩111 2026-06-28] 季节性月份过滤
    # 铁证：BTC 6.6年月份WR（Fisher p=0.001，OOS稳定<2%）
    # [细化 2026-07-01] 7月内部分层：上旬冷起动 / 中旬品质 / 下旬谨慎
    # ══════════════════════════════════════════════════════════════
    try:
        import datetime as _dt_p2
        _now_p2 = _dt_p2.datetime.utcnow()
        _mth = _now_p2.month
        _day = _now_p2.day
        if signal_dir == 'SHORT' and 'BEAR' in str(ms.get('regime','') if ms else '').upper():
            if _mth == 4:
                _p2v, _p2lbl = -30, '4月禁止做空(WR=50.9%)'
            elif _mth == 7:
                # 7月内部分层：达摩院铁证 n=6.6年
                if _day <= 10:
                    _p2v, _p2lbl = -15, '7月上旬冷起动期(WR最低)'
                elif _day <= 20:
                    _p2v, _p2lbl = -5,  '7月中旬回暖期(小心)'
                else:
                    _p2v, _p2lbl = -8,  '7月下旬谨慎期(WR偏低)'
            elif _mth == 9:
                _p2v, _p2lbl = -10, f'{_mth}月谨慎(WR≈55%)'
            elif _mth in (1, 5, 8, 10, 11):
                _p2v, _p2lbl = 5, f'{_mth}月好月(WR=70%+)'
            else:
                _p2v = 0; _p2lbl = ''
            if _p2v != 0:
                _score_raw = max(0, min(175, round(_score_raw + _p2v, 1)))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p2_seasonal'] = (
                    f'{_p2lbl} {_p2v:+d}分 (p=0.001 OOS稳定) [{_now_p2.strftime("%m-%d")}]')
                if abs(_p2v) >= 5:
                    pass  # [静默] f'[P2-Seasonal] 📅 {_sym} {_p2lbl}: {_p2v:+d}分 score→{_score_raw:.0f}'
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
    # ── [END P0/P1/P2 苏摩111 2026-06-28] ────────────────────────


    # ── [设计院 2026-06-07] N22 做市商轨道B评分（六方辩论落地）────────────────
    # 实证：LAB处于派发阶段→做空+18，吸筹阶段→做多+10
    # 轨道B品种不走主流评分框架加成，而是单独做市商阶段加分
    try:
# [CLEANED 2026-06-11] from market_maker_engine import market_maker_score as _mm_fn, is_track_b as _is_tb
        if _is_tb(_sym):
            _mm_pts  = _mm_res.get('score', 0)
            if _mm_pts != 0 and _score_raw > 0:
                _score_raw = round(_score_raw + _mm_pts, 1)
                cf['total'] = _score_raw
                cf['n22_market_maker'] = f"stage={_mm_res.get('stage','')} conf={_mm_res.get('confidence',0)}% {_mm_pts:+d}pts"
                print(f'[N22-MM轨道B] {_sym} {signal_dir}: stage={_mm_res.get("stage","")} {_mm_pts:+d}分 → {_score_raw:.0f}')
    except Exception as _mm_e:
        pass
    # ── [END N22 做市商轨道B] ────────────────────────────────────────────────

    # ── [达摩院因子引擎 2026-06-03] DharmaFactorEngine 标准化落地层 ──────────
    # 读取 dharma/factor_weights.yaml，应用所有 pending/live 因子
    # 规则：YAML数据驱动，不改代码，达摩院发现直接更新YAML即可
    try:
        import sys as _dfe_sys, os as _dfe_os
        _dfe_root = _dfe_os.path.dirname(_dfe_os.path.dirname(_dfe_os.path.abspath(__file__)))
        if _dfe_root not in _dfe_sys.path:
            if _dfe_root not in _dfe_sys.path: _dfe_sys.path.insert(0, _dfe_root)
        from dharma.dharma_factor_engine import apply_dharma_factors as _dfe_apply
        # [达摩院v2.0 2026-06-04] 计算新因子字段，传入DharmaFactorEngine
        _rsi_1h   = float(ms.get('momentum', {}).get('rsi_1h', 50) or 50)
        _vol_r    = float(ms.get('volume', {}).get('vol_ratio', 1.0) or 1.0)
        _price_bb = ms.get('bb', {}) or {}  # BB数据
        _bb_mid   = float(_price_bb.get('mid', 0) or 0)
        _cur_price= float(ms.get('price', 0) or 0)
        _price_below_bb_mid = (_cur_price < _bb_mid) if _bb_mid > 0 else False
        _price_above_bb_mid = (_cur_price > _bb_mid) if _bb_mid > 0 else False
        _bb_upper = float(_price_bb.get('upper', 0) or 0)
        _bb_lower = float(_price_bb.get('lower', 0) or 0)
        _bb_k25u  = _cur_price <= _bb_lower * 0.998 if _bb_lower > 0 else False  # 触碰2.5σ下轨
        _bb_k25d  = _cur_price >= _bb_upper * 1.002 if _bb_upper > 0 else False  # 触碰2.5σ上轨
        # SMC FVG信息
        _smc_fvg  = smc.get('fvg', {}) if isinstance(smc, dict) else {}
        _has_fvg_l= bool(_smc_fvg.get('bullish') or _smc_fvg.get('long'))
        _has_fvg_s= bool(_smc_fvg.get('bearish') or _smc_fvg.get('short'))
        # 三重共振判断（达摩院铁证：RSI+VOL+BB）
        _triple_l = (_rsi_1h < 40 and _vol_r >= 1.1 and _price_below_bb_mid)
        _triple_s = (_rsi_1h > 60 and _vol_r >= 1.1 and _price_above_bb_mid)
        # RSI_BB双重共振（超大样本6.5万验证）
        _rsi_bb_l = (_rsi_1h < 40 and _price_below_bb_mid)
        _rsi_bb_s = (_rsi_1h > 70 and _price_above_bb_mid)
        # VOL_RSI最优量价（vol×1.2+RSI<40）
        _vol_rsi  = (_vol_r >= 1.2 and _rsi_1h < 40)
        # FVG+量能（4H最强中频）
        _fvg_v4h  = ((_has_fvg_l and signal_dir=='LONG') or (_has_fvg_s and signal_dir=='SHORT')) and _vol_r >= 1.3
        _fvg_v1h  = _fvg_v4h  # 同逻辑，通过tf区分
        # OBV方向（简单用volume趋势代理）
        _obv_pos  = _vol_r > 1.0 and ms.get('trend', {}).get('1h', {}).get('direction', '') == 'UP'
        _dfe_ctx = {
            'symbol':     _sym,
            'tf':         '4h',   # brahma主周期
            'signal_dir': signal_dir,
            'utc_hour':   __import__('datetime').datetime.now(__import__('datetime').timezone.utc).hour,
            'vol_ratio':  _vol_r,
            'rsi_1h':     _rsi_1h,
            'atr_pct':    float(params.get('sl_pct', 0.4) or 0.4),
            'range_pos':  float(cf.get('range_position', 0.5) or 0.5),
            'has_div':    bool(ms.get('momentum', {}).get('has_div', False)),
            'regime':     ms.get('regime', ''),
            # [达摩院v2.0] 黄金因子字段
            'bb_edge_25_confirmed': (_bb_k25l := (_cur_price <= _bb_lower and _price_below_bb_mid)) if signal_dir=='LONG' else (_cur_price >= _bb_upper and _price_above_bb_mid),
            'bb_edge_20_touch':     (_bb_lower > 0 and _cur_price <= _bb_lower * 1.002) if signal_dir=='LONG' else (_bb_upper > 0 and _cur_price >= _bb_upper * 0.998),
            'triple_resonance_long':  _triple_l,
            'triple_resonance_short': _triple_s,
            'rsi_bb_dual_long':       _rsi_bb_l,
            'rsi_bb_dual_short':      _rsi_bb_s,
            'vol_rsi_optimal':        _vol_rsi,
            'fvg_vol_4h':             _fvg_v4h,
            'fvg_vol_1h':             _fvg_v1h,
            'l4_triple_resonance':    False,  # 需要L4三层同时满足，默认False
            'h4_obv_positive':        _obv_pos,
            'has_fvg_long':           _has_fvg_l,
            'has_fvg_short':          _has_fvg_s,
        }
        # 仅当信号有效（score>0，未被Gate清零）时才应用
        if _score_raw > 0:
            _score_raw, cf['breakdown'] = _dfe_apply(_score_raw, _dfe_ctx, cf.get('breakdown', {}))
            cf['total'] = _score_raw
            _score = _score_raw
    except Exception as _dfe_e:
        pass   # 引擎失败静默，不影响主流程

    # ── [15m信号层 P1-B 2026-06-05] ─────────────────────────────────────────
    # 训练铁证：BB_EDGE_LONG k=2.5 WR=75.7% n=19,479 | TRIPLE WR=75.5% n=13,778
    # 直接从ms['bb_15m']读取15m指标（若trigger_15m已计算）
    try:
        _bb15 = ms.get('bb_15m', {}) or {}
        _rsi15 = float(ms.get('momentum', {}).get('rsi_15m', 50) or 50)
        _v15   = float(ms.get('volume', {}).get('vol_ratio_15m', 1.0) or 1.0)
        _p15_lo = float(_bb15.get('lower', 0) or 0)
        _p15_up = float(_bb15.get('upper', 0) or 0)
        _p15_mid= float(_bb15.get('mid', 0) or 0)
        _cp = float(ms.get('price', 0) or 0)

        _score15 = 0
        _score15_note = []

        if _p15_lo > 0 and _cp > 0:
            # BB_EDGE k=2.5: 价格触碰2.5σ边轨（WR=75.7% n=19K）
            if signal_dir == 'SHORT' and _cp >= _p15_up * 0.999:
                _score15 += 10
                _score15_note.append('BB_EDGE25_SHORT+10')
            elif signal_dir == 'LONG' and _cp <= _p15_lo * 1.001:
                _score15 += 10
                _score15_note.append('BB_EDGE25_LONG+10')

            # BB_MID 方向确认（WR=70.8% n=70K）
            if signal_dir == 'SHORT' and _cp > _p15_mid:
                _score15 += 4
                _score15_note.append('BB_MID_SHORT+4')
            elif signal_dir == 'LONG' and _cp < _p15_mid:
                _score15 += 4
                _score15_note.append('BB_MID_LONG+4')

        if _rsi15 > 0:
            # TRIPLE共振（WR=75.5% n=13K）
            if signal_dir == 'SHORT' and _rsi15 > 60 and _v15 >= 1.1 and _cp > _p15_mid:
                _score15 += 11
                _score15_note.append(f'TRIPLE_SHORT+11(rsi15={_rsi15:.0f})')
            elif signal_dir == 'LONG' and _rsi15 < 40 and _v15 >= 1.1 and _cp < _p15_mid:
                _score15 += 11
                _score15_note.append(f'TRIPLE_LONG+11(rsi15={_rsi15:.0f})')

            # RSI_BB双向（WR=71.6% n=19K）
            if signal_dir == 'SHORT' and _rsi15 > 70:
                _score15 += 7
                _score15_note.append(f'RSI_BB_S+7(rsi15={_rsi15:.0f})')
            elif signal_dir == 'LONG' and _rsi15 < 30:
                _score15 += 7
                _score15_note.append(f'RSI_BB_L+7(rsi15={_rsi15:.0f})')

        if _score15 > 0 and _score_raw > 0:
            _score_raw += _score15
            cf['total'] = _score_raw
            _score = _score_raw
            cf.setdefault('breakdown', {})['15mLayer'] = '+'.join(_score15_note) + f' total=+{_score15}'
    except Exception as _15m_e:
        pass  # 15m层失败不影响主流程
    # ── [END 15m信号层] ────────────────────────────────────────────────────────

    # ── [END DharmaFactorEngine] | 达摩因子引擎段结束 ──────────────────────────────────────────────────────────

    # ── [P2 评分校准 2026-06-05] 高分段体制适配门 ───────────────────────────
    # 实盘数据：160+分WR=63% < 150-160分WR=80% → 高分段过拟合修正
    # 规则：评分>160且体制不强烈支持该方向 → 封顶165
    _regime_str = str(ms.get('regime','') or '')
    _bears = ('BEAR_TREND','BEAR_EARLY','CRASH')
    _bulls = ('BULL_TREND','BULL_EARLY')
    _regime_matches = (
        (signal_dir == 'SHORT' and any(b in _regime_str for b in _bears)) or
        (signal_dir == 'LONG'  and any(b in _regime_str for b in _bulls))
    )
    if _score > 160 and not _regime_matches:
        # 体制与方向不强烈吻合，高分段可信度下降，封顶165防过拟合
        _score = min(_score, 165)
        cf['total'] = _score
        cf.setdefault('breakdown', {})['P2_RegimeCap'] = f'score capped @165 (regime={_regime_str} dir={signal_dir})'
    # ── [END P2] | P2 主流程段结束 ─────────────────────────────────────────────────────────────

    pass  # [静默] f'[BrahmaBrain] ✓ {_sym} {signal_dir} score={_score:.0f} rr1={params["rr1"]} rr_

    _REGIME_CN = {
        'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_PEAK':'牛市末期',
        'BULL_CORRECTION':'牛市回调','BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期',
        'BEAR_CRASH':'暴跌体制','BEAR_RECOVERY':'熊市反弹',
        'CHOP_HIGH':'高位震荡','CHOP_LOW':'低位震荡','CHOP_MID':'中位震荡',
        'BREAKOUT':'突破体制',
    }  # [v25.3 2026-06-14] 体制中文映射
    _result = {
        'symbol':      symbol,
        'price':       ms['price'],
        'price_ts':    time.time(),   # [设计院 2026-08-25] 强制写入实时时间戳，防旧数据输出
        'data_age_sec': 0,             # 刚从API取，age=0
        'signal_dir':  signal_dir,
        'regime':      ms['regime'],
        'regime_cn':   _REGIME_CN.get(ms['regime'], ms['regime']),  # [v25.3] 体制中文
        'consensus':   ms['trend']['consensus']['consensus'],
        'wave':        ms['wave'],
        'momentum':    ms['momentum'],
        'sentiment':   ms['sentiment'],
        'key_levels':  ms['key_levels'],
        'swing_4h':    ms.get('swing_4h', {}),
        'smc':         smc,
        'confluence':  cf,
        'params':      params,
        'summary':     ms['summary'],
        'elapsed':     elapsed,
        'valid_signal': _valid,
        'primary_tf':   params.get('primary_tf', '4H'),
        'entry_tf':     params.get('entry_tf',   '1H'),
        'sl_basis':     params.get('sl_basis',   'swing_4h+atr4h×0.3'),
        'sl_atr_mult':  params.get('sl_atr_mult', 0),
        'extra':       extra_data,
        # [修复 2026-08-24] RSI顶层字段，供P1/P4直接读取（原只在market_state_raw里）
        'rsi_1h':  float((ms.get('momentum') or {}).get('rsi_1h', 50) or 50),
        'rsi_4h':  float((ms.get('momentum') or {}).get('rsi_4h', 50) or 50),
        'rsi_1d':  float((ms.get('momentum') or {}).get('rsi_1d', 50) or 50),
        # [2026-08-12 苏摩111封印 v3] ms完整原始数据注入，全路径修正版，供35维逐项核对
        'market_state_raw': {
            # ── 趋势模块 (ms['trend'][tf]) ──
            'consensus':      ((ms.get('trend') or {}).get('consensus') or {}).get('consensus'),
            'trend_dir_1h':   ((ms.get('trend') or {}).get('1h') or {}).get('direction'),
            'trend_dir_4h':   ((ms.get('trend') or {}).get('4h') or {}).get('direction'),
            'trend_dir_1d':   ((ms.get('trend') or {}).get('1d') or {}).get('direction'),
            'adx_1h':         ((ms.get('trend') or {}).get('1h') or {}).get('adx'),
            'adx_4h':         ((ms.get('trend') or {}).get('4h') or {}).get('adx'),
            'ema20_1h':       ((ms.get('trend') or {}).get('1h') or {}).get('ema20'),
            'ema50_1h':       ((ms.get('trend') or {}).get('1h') or {}).get('ema50'),
            'ema200_1h':      ((ms.get('trend') or {}).get('1h') or {}).get('ema200'),
            'ema20_4h':       ((ms.get('trend') or {}).get('4h') or {}).get('ema20'),
            'ema50_4h':       ((ms.get('trend') or {}).get('4h') or {}).get('ema50'),
            'ema200_1d':      ((ms.get('trend') or {}).get('1d') or {}).get('ema200'),
            # ── 动量模块 (ms['momentum']) ──
            'rsi_15m':        (ms.get('momentum') or {}).get('rsi_15m'),
            'rsi_1h':         (ms.get('momentum') or {}).get('rsi_1h'),
            'rsi_4h':         (ms.get('momentum') or {}).get('rsi_4h'),
            'rsi_1d':         (ms.get('momentum') or {}).get('rsi_1d'),
            'atr_1h':         (ms.get('momentum') or {}).get('atr_1h'),
            'atr_4h':         (ms.get('momentum') or {}).get('atr_4h'),
            'atr_pct':        (ms.get('momentum') or {}).get('atr_pct'),
            'bb_width':       ((ms.get('momentum') or {}).get('bb') or {}).get('width'),
            'bb_pos':         ((ms.get('momentum') or {}).get('bb') or {}).get('pos'),
            'bb_upper':       ((ms.get('momentum') or {}).get('bb') or {}).get('upper'),
            'bb_lower':       ((ms.get('momentum') or {}).get('bb') or {}).get('lower'),
            # ── 情绪模块 (ms['sentiment']) ──
            'funding_rate':   (ms.get('sentiment') or {}).get('funding_rate'),
            'long_short_ratio': (ms.get('sentiment') or {}).get('long_short_ratio'),
            'oi':             (ms.get('sentiment') or {}).get('oi'),
            'oi_change_pct':  (ms.get('sentiment') or {}).get('oi_change_pct'),
            'oi_momentum':    (ms.get('sentiment') or {}).get('oi_momentum'),
            # ── 宏观/DXY (extra_data['macro_v2']) ──
            'dxy':            ((extra_data or {}).get('macro_v2') or {}).get('dxy', {}).get('price') if isinstance(((extra_data or {}).get('macro_v2') or {}).get('dxy'), dict) else ((extra_data or {}).get('macro_v2') or {}).get('dxy'),
            'dxy_dir':        ((extra_data or {}).get('macro_v2') or {}).get('dxy', {}).get('direction'),
            'nasdaq_price':   ((extra_data or {}).get('macro_v2') or {}).get('nasdaq', {}).get('price'),
            'macro_v2_score': ((extra_data or {}).get('macro_v2') or {}).get('score_addon'),
            'macro_v2_notes': ((extra_data or {}).get('macro_v2') or {}).get('notes'),
            # ── CVD (via enhanced_signal_engine结果) ──
            'cvd_score':      ((extra_data or {}).get('enhanced') or {}).get('breakdown', {}).get('cvd'),
            'cvd_notes':      (((extra_data or {}).get('enhanced') or {}).get('notes') or [])[:2],
            'lsr_trend':      ((extra_data or {}).get('enhanced') or {}).get('lsr', {}).get('trend'),
            'lsr_current':    ((extra_data or {}).get('enhanced') or {}).get('lsr', {}).get('current'),
            'session_name':   ((extra_data or {}).get('enhanced') or {}).get('session', {}).get('session'),
            'session_vol_mult': ((extra_data or {}).get('enhanced') or {}).get('session', {}).get('vol_mult'),
            'liq_bias':       ((extra_data or {}).get('liq_snap') or {}).get('bias'),
            'liq_long':       ((extra_data or {}).get('coinglass') or {}).get('liquidation', {}).get('long_liq'),
            'liq_short':      ((extra_data or {}).get('coinglass') or {}).get('liquidation', {}).get('short_liq'),
            # ── OB/FVG/SMC ──
            'structure_grade': cf.get('structure_grade'),
            'effective_grade': cf.get('effective_grade'),
            'smc_structure':  (smc.get('structure') or {}).get('structure'),
            'bos_count':      len((smc.get('structure') or {}).get('bos') or []),
            'choch_count':    len((smc.get('structure') or {}).get('choch') or []),
            'ob_bull_count':  len((smc.get('order_blocks') or {}).get('bull_obs') or []),
            'ob_bear_count':  len((smc.get('order_blocks') or {}).get('bear_obs') or []),
            'fvg_count':      len(smc.get('fvg') or []),
            # ══ [P2封印 2026-08-30 苏摩111] Hurst解析字段 ══
            'hurst_4h':       (lambda _s: float(__import__('re').search(r'H=([0-9.]+)', _s).group(1)) if __import__('re').search(r'H=([0-9.]+)', str(_s or '')) else None)(cf.get('breakdown', {}).get('Hurst体制验证')),
            # ── 时段（实时计算）──
            'utc_hour':       __import__('datetime').datetime.utcnow().hour,
            'weekday':        __import__('datetime').datetime.utcnow().weekday(),
            'month':          __import__('datetime').datetime.utcnow().month,
            # ── ML/Kronos ──
            'kronos_p_up':    (extra_data or {}).get('kronos_p_up'),
            'xgb_score':      ((extra_data or {}).get('_snap_for_xgb') or {}).get('xgb_score',
                               (extra_data or {}).get('xgb_score')),
            # ── 资金费率跨所 ──
            'cross_fr_avg':   ((extra_data or {}).get('cross_fr_basis') or {}).get('fr_avg'),
            'cross_basis':    ((extra_data or {}).get('cross_fr_basis') or {}).get('basis_pct'),
            # ── 期权 ──
            'pc_ratio':       ((extra_data or {}).get('deribit_pc') or {}).get('pc_oi_ratio'),
        },
        # [设计院 2026-05-24] 达摩院6节点预测评分
        'dharma_nodes': _dharma_nodes,
        'nodes_pass':   _dharma_nodes.get('nodes_pass', 0),
        'nodes_verdict':_dharma_nodes.get('verdict', 'UNKNOWN'),
        'score_final':  _score,
        # [v25.4c effective_grade] 体制感知grade写入顶层，供offline_replay使用
        'grade':          int(cf.get('structure_grade', 0) or 0),
        'effective_grade': round(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0), 1),
        'grade_mult':      round(float(cf.get('grade_mult', 1.0) or 1.0), 2),
    }

    # [WFV-v1 闭环 2026-05-28] 达摩院信号日志（live_signal_log.jsonl）
    # [双写修复 2026-07-23 设计院] brahma_engine.analyze()已在外层写入，此处跳过防止重复
    # brahma_core.analyze()是内层函数，由brahma_engine调用，写入责任在engine层
    # pass  # 原log_signal调用已移至brahma_engine.py L2821

    # ── FIX-I1: CHOP体制智能过滤（设计院 2026-06-06）────────────────
    # alpha_market_filter模块接入：CHOP噪音降级
    # 达摩院实证：CHOP_MID/CHOP_LOW(震荡低波) PF=0.862/0.865，grade<60时噪音率极高
    # 规则：CHOP体制 + grade<60 + 无强背离(s16<8) → -10分降噪惩罚
    try:
        _chop_regime = any(x in str(_result.get('regime','') or '').upper()
                          for x in ['CHOP_LOW','CHOP_MID'])
        _cf = _result.get('confluence', {}) or {}
        _chop_grade = _cf.get('structure_grade', 0) or 0
        try: _chop_grade = int(float(_chop_grade))
        except Exception as _bare_e: _chop_grade = 0  # [R4-fix audit-2026-06-17] 裸except已命名，保留原值0
        _chop_s16 = _cf.get('breakdown', {}).get('量能衰竭+背离共振', 0) or 0
        _chop_score = float(_cf.get('score', 0) or 0)

        if _chop_regime and _chop_grade < 60 and _chop_s16 < 8 and _chop_score > 0:
            _chop_penalty = 10
            _cf['score'] = _chop_score - _chop_penalty
            _cf.setdefault('breakdown', {})['_chop_filter'] = f'-{_chop_penalty}(CHOP噪音降级:grade={_chop_grade}<60,s16={_chop_s16}<8)'
            _result['confluence'] = _cf
            pass  # [静默] f"[BrahmaBrain] 🔇 CHOP过滤: {_chop_score:.0f}→{_cf['score']:.0f} (grade={_chop_gra
    except Exception as _chop_e:
        try:
            _s3172 = str(__import__('pathlib').Path(__file__).parent.parent / 'scripts')
            import sys as _sys
            if _s3172 not in _sys.path: _sys.path.insert(0, _s3172)  # [修复 S1 2026-08-24]
            from error_collector import log_error as _le
            _le('brahma_brain_chop_filter', _chop_e)
        except Exception as _bare_e:  # [R4-fix audit-2026-06-17] 裸except已命名
            pass

    # ── Score过热拦截（设计院 2026-06-06）─────────────────────────
    # 铁证：score>175 WR=0%，score 150~160 WR=96%（武曲Paper 121条）
    # score过高=多维叠加但gap收缩=结构被侵蚀，反而是风险信号
    _final_score = _result.get('confluence', {}).get('score', 0)
    if _final_score and float(_final_score) > 175:
        _overheat_penalty = min(int((float(_final_score) - 175) * 2), 30)
        _result['confluence']['score'] = float(_final_score) - _overheat_penalty
        _result['confluence']['_overheat_penalty'] = _overheat_penalty
        pass  # [静默] f"[BrahmaBrain] ⚠️ score过热惩罚: {_final_score:.0f}→{_result['confluence']['score']

    # ── s20: Tardis清算墙维度（星枢引擎 Phase1）────────────
    try:
        from tardis_engine import get_tardis_score
        _sym_t  = _result.get('symbol', '')
        _dir_t  = _result.get('signal_dir', 'NEUTRAL')
        _pa_t   = _result.get('params', {})
        _elo    = float(_pa_t.get('entry_lo', 0))
        _ehi    = float(_pa_t.get('entry_hi', _elo * 1.002))
        if _dir_t in ('SHORT', 'LONG') and _elo > 0:
            _s20, _s20_detail = get_tardis_score(_sym_t, _dir_t, _elo, _ehi)
            if _s20 != 0:
                _cur_score = float(_result.get('confluence', {}).get('score', 0))
                _result['confluence']['score'] = _cur_score + _s20
                _result['confluence']['_s20_tardis'] = _s20
                _result['confluence'].setdefault('breakdown', {})['s20_tardis'] = f'{_s20:+.0f} {_s20_detail}'
                print(f'[s20-Tardis] {_sym_t} {_dir_t}: {_s20:+.0f} | {_s20_detail}')
    except Exception as _e20:
        pass  # Tardis数据不影响主流评分

    # ── s22: GEX Gamma Exposure Sentiment（Deribit期权数据）────
    try:
        import sys as _sys22, os as _os22
        _bb_dir = _os22.path.dirname(_os22.path.abspath(__file__))
        _root_dir = _os22.path.dirname(_bb_dir)
        for _p22 in [_bb_dir, _root_dir]:
            if _p22 not in _sys22.path:
                _sys22.path.insert(0, _p22)
        from gex_engine import score_gex as _score_gex22, compute_gex as _compute_gex22
        _currency_g = 'BTC' if 'BTC' in _sym_t.upper() else \
                      'ETH' if 'ETH' in _sym_t.upper() else 'BTC'
        # [设计院 2026-06-30] 优先用 gex_scanner（博尔正项BS公式），fallback到 gex_engine
        try:
            from gex_scanner import get_gex_state as _gex_state_fn, get_gex_score_for_signal as _gex_sig_fn
            _gex_cached = _gex_state_fn(_currency_g)
            if _gex_cached and _gex_cached.get('max_gex_strike'):
                _gex_adj, _gex_desc = _gex_sig_fn(_currency_g, _dir_t)
                _s22 = max(-10, min(12, _gex_adj))
                _gex_data = _gex_cached  # 多字段可用
                _result['confluence']['_gex_max'] = _gex_cached.get('max_gex_strike')
                _result['confluence']['_gex_min'] = _gex_cached.get('min_gex_strike')
                _result['confluence']['_gex_pos_pct'] = _gex_cached.get('spot_pos_pct')
                if _s22 != 0:
                    # [GEX到期日识别 2026-07-01] 设计院防错机制
                    # 每月最后一个周五 = 期权到期日，GEX磁铁效应最强→权重×1.5
                    try:
                        import datetime as _dt_gex
                        _today = _dt_gex.datetime.utcnow()
                        # 找当月最后一个周五
                        import calendar as _cal_gex
                        _last_day = _cal_gex.monthrange(_today.year, _today.month)[1]
                        _last_fri = max(
                            d for d in range(1, _last_day+1)
                            if _dt_gex.date(_today.year, _today.month, d).weekday() == 4
                        )
                        _days_to_expiry = _last_fri - _today.day
                        if 0 <= _days_to_expiry <= 3:
                            # 将近到期日：GEX权重×1.5
                            _gex_mult = 1.5
                            _s22 = max(-10, min(12, round(_s22 * _gex_mult)))
                            print(f'[s22-GEX到期日] {_sym_t} 到期日还有{_days_to_expiry}天 GEX权重×1.5→{_s22:+d}')
                    except Exception:
                        pass
                    _cur_score22 = _result['confluence']['score']
                    _result['confluence']['score'] = _cur_score22 + _s22
                    _result['confluence']['_s22_gex'] = _s22
                    _result['confluence'].setdefault('breakdown', {})['s22_gex'] = \
                        f'{_s22:+d} MAX=${_gex_cached["max_gex_strike"]:,.0f} MIN=${_gex_cached["min_gex_strike"]:,.0f} pos={_gex_cached.get("spot_pos_pct",0):.0f}% | {_gex_desc[:40]}'
                    print(f'[s22-GEX★] {_sym_t} {_dir_t}: {_s22:+d} | MAX=${_gex_cached["max_gex_strike"]:,.0f} MIN=${_gex_cached["min_gex_strike"]:,.0f}')
                _gex_data = _gex_cached
                raise StopIteration  # 跳过旧gex_engine
        except StopIteration:
            pass
        except Exception:
            pass  # gex_scanner不可用，fallback到gex_engine
        _gex_data = _compute_gex22(_currency_g)
        if _gex_data:
            _s22_res = _score_gex22(_sym_t, _dir_t, _gex_data)
            _s22 = _s22_res.get('s22', 0)
            _s22 = max(-10, min(8, _s22))
            if _s22 != 0:
                _cur_score22 = _result['confluence']['score']
                _result['confluence']['score'] = _cur_score22 + _s22
                _result['confluence']['_s22_gex'] = _s22
                _result['confluence'].setdefault('breakdown', {})['s22_gex'] = \
                    f'{_s22:+d} {_s22_res.get("reason","")[:60]}'
                print(f'[s22-GEX] {_sym_t} {_dir_t}: {_s22:+d} | {_s22_res.get("reason","")}')
    except Exception as _e22:
        pass  # GEX不影响主流评分

    # ── s23: Kronos统一域 v2.0 (brahma_kronos) ────────────────────────
    # [2026-08-24 设计院顶层重构] 201行三段式→30行统一入口
    # [2026-08-25 fix P2] 直接用kronos_bridge.get_s23_kronos，绕过brahma_kronos降级链传参bug
    try:
        import sys as _sys23, os as _os23
        _bb23 = _os23.path.dirname(_os23.path.abspath(__file__))
        if _bb23 not in _sys23.path: _sys23.path.insert(0, _bb23)
        from kronos_bridge import get_s23_kronos as _bk_fn
    except ImportError:
        def _bk_fn(*a, **kw): return (0, {'score': 0, 'p_up': 0.5, 'source': 'import_err'})
    try:
        from recovery_unlocker import check_unlock as _check_unlock
    except ImportError:
        def _check_unlock(*a, **kw): return {'unlocked': False}  # [2026-08-25 fix] recovery_unlocker可选模块，不阻塑Kronos主链路

    try:
        _sym_t = _result.get('symbol', symbol)
        _dir_t = _result.get('signal_dir', signal_dir)
        _kl15m = ms.get('klines_15m', [])
        if not _kl15m and extra_data:
            _kl15m = extra_data.get('_klines_15m', [])
        if not _kl15m:
            try:
                _raw15 = get_klines(_sym_t, '15m', 200)
                _kl15m = [[float(c[0]),float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])] for c in _raw15]  # [2026-08-25 fix] 保留timestamp_ms供_build_ohlcv_df使用
            except Exception: _kl15m = []
        # 格式标准化: dict→list
        if _kl15m and isinstance(_kl15m[0], dict):
            _kl15m = [[float(_k.get('o',0)),float(_k.get('h',0)),float(_k.get('l',0)),float(_k.get('c',0)),float(_k.get('v',0))] for _k in _kl15m]
        elif _kl15m and isinstance(_kl15m[0], (list,tuple)):
            try: _kl15m = [[float(v) for v in k[:5]] for k in _kl15m]
            except Exception: _kl15m = []

        if len(_kl15m) >= 60:
            _s23_regime = _result.get('regime', '')
            _bk_result  = _bk_fn(_kl15m, _sym_t, _dir_t, _s23_regime)  # [2026-08-25 fix] get_s23_kronos参数顺序: (klines,symbol,dir,regime)
            # [2026-08-25 fix] get_s23_kronos返回(score, meta_dict)元组，需要解包
            if isinstance(_bk_result, tuple) and len(_bk_result) == 2:
                _s23, _bk_result = int(_bk_result[0]), (_bk_result[1] if isinstance(_bk_result[1], dict) else {})
            else:
                _s23 = int(_bk_result.get('score', 0)) if isinstance(_bk_result, dict) else 0
            _p_up_raw   = float(_bk_result.get('p_up', 0.5))
            _s23_meta   = _bk_result

            # CHOP方向冲突惩罚
            if 'CHOP' in _s23_regime and _s23_meta.get('direction_conflict', False):
                _s23 = min(_s23, -10)

            # CORRECTION/RECOVERY解锁
            _unlock = _check_unlock(regime=_s23_regime, direction=_dir_t,
                                    base_score=_result['confluence']['score'],
                                    kronos_meta=_s23_meta, symbol=_sym_t)
            if _unlock.get('unlocked'):
                _s23 = max(_s23, _unlock['s23_bonus'])

            # Kronos极値模式（p_up>0.90做空=惩罚减半）
            if _p_up_raw >= 0.90 and _dir_t == 'SHORT':
                _s23 = max(_s23, -4)
                print(f'[s23-Kronos极値] {_sym_t} p_up={_p_up_raw:.2f}: 惩罚降半至{_s23}')

            # 注入评分（50%降权）
            if _s23 != 0:
                _s23_w = round(_s23 * 0.5)
                _result['confluence']['score'] += _s23_w
                _result['confluence'].setdefault('breakdown', {})['s23_kronos'] = (
                    f"{_s23_w:+d}(原{_s23:+d}×50%) src={_bk_result.get('source','?')[:20]}"
                )
                _result['s23_p_up'] = _p_up_raw

            # kronos_p_up写入extra_data和result顶层
            if extra_data is not None:
                extra_data['kronos_p_up']  = _p_up_raw
                extra_data['kronos_score'] = _s23
                extra_data['kronos_src']   = _bk_result.get('source', 'brahma_kronos')
            _result['kronos_p_up']  = _p_up_raw
            _result['kronos_score'] = _s23
            print(f'[KronosBridge·ACTIVE] {_sym_t}: Kronos={_s23:+d} p_up={_p_up_raw:.3f} src={_bk_result.get("source","?")}')
    except Exception:
        pass  # Kronos不影响主流程
    # ── [s23 brahma_kronos END] ─────────────────────────────────────────────────

    # ── s24: 已归档 (2026-06-26 设计院封印) ────────────────────────────
    pass  # s24已归档

    # ── s26: OI持仓量驱动拉升猎手（2026-06-30 设计院 × 苏摩授权）──────
    # 五层过滤：OI结构+大户方向+资金费率+技术+体制
    # 区分空头建仓 vs 聪明钱潜伏，BEAR_TREND下最多+5分
    try:
        import os as _os26, sys as _sys26
        _bb26 = _os26.path.dirname(_os26.path.abspath(__file__))
        _root26 = _os26.path.dirname(_bb26)
        for _p26 in [_bb26, _root26]:
            if _p26 not in _sys26.path:
                _sys26.path.insert(0, _p26)
        from oi_surge_scanner import get_oi_bonus as _get_oi_bonus
        _oi_sym = _result.get('symbol', '')
        _oi_dir = _result.get('signal_dir', 'NEUTRAL')
        if _oi_sym and _oi_dir in ('LONG', 'SHORT'):
            _oi_bonus, _oi_detail = _get_oi_bonus(_oi_sym)
            # 只对LONG方向有效（OI猎手识别的是做多蓄能）
            if _oi_dir == 'LONG' and _oi_bonus > 0:
                _cur_s26 = float(_result.get('confluence', {}).get('score', 0))
                _result['confluence']['score'] = _cur_s26 + _oi_bonus
                _result['confluence']['_s26_oi'] = _oi_bonus
                _result['confluence'].setdefault('breakdown', {})['s26_oi'] = \
                    f'{_oi_bonus:+d} {_oi_detail}'
                print(f'[s26-OI] {_oi_sym} LONG: {_oi_bonus:+d} | {_oi_detail}')
    except Exception as _e26:
        pass  # OI数据不影响主流评分

    # ── s25: OpenRouter 推理验证门控 v2 (苏摩B档 · 2026-06-26) ────────────
    # 升级内容：score阈值120（原130）+ 四模块并行ThreadPool
    # 触发：score≥120 + valid=True + 非CHOP + Kronos p_up>0.65
    # 苏摩B档：并行调用，各模块独立cache，异常全部吞咽
    try:
        import os as _os25, concurrent.futures as _cf25
        _s25_key = _os25.environ.get('OPENROUTER_API_KEY', '') or ''
        if not _s25_key:
            _env25 = Path(__file__).parent.parent / '.env'
            if _env25.exists():
                for _ln in _env25.read_text().splitlines():
                    if _ln.startswith('OPENROUTER_API_KEY='):
                        _s25_key = _ln.split('=',1)[1].strip()
                        _os25.environ['OPENROUTER_API_KEY'] = _s25_key
                    if _ln.startswith('REASONING_MODEL=') and not _os25.environ.get('REASONING_MODEL'):
                        _os25.environ['REASONING_MODEL'] = _ln.split('=',1)[1].strip()
                    if _ln.startswith('REASONING_MODEL_FAST=') and not _os25.environ.get('REASONING_MODEL_FAST'):
                        _os25.environ['REASONING_MODEL_FAST'] = _ln.split('=',1)[1].strip()

        _s25_score  = _result.get('score_final', 0) or 0
        _s25_regime = _result.get('regime', '')
        _s25_valid  = _result.get('valid_signal', False)
        _s25_sym    = _result.get('symbol', '')
        _s25_dir    = _result.get('signal_dir', '')
        _s25_price  = _result.get('price', 0)
        _s25_params = _result.get('params', {})
        _s25_macro  = extra_data.get('macro_report', {}) if extra_data else {}

        # Kronos p_up 解析
        _s25_kronos_str = _result.get('confluence', {}).get('breakdown', {}).get('s23_kronos', '')
        _s25_pup = 0.5
        try:
            if 'p_up=' in _s25_kronos_str:
                _s25_pup = float(_s25_kronos_str.split('p_up=')[1].split('|')[0].strip())
        except Exception:
            pass

        # B档触发条件：score≥120（原130）
        # P1a放宽触发条件：p_up>0.55 OR score>150（任一满足）—设计院封印 2026-06-27
        # P1b 2026-06-29：去掉CHOP排除 → CHOP体制也允许reasoning增强
        #   reasoning_gate会自动WARN/BLOCK低质量信号，不会误放，无副作用
        #   仅保留 score≥100（原120降低）提高边缘信号捕获率
        _s25_should = (
            bool(_s25_key) and
            _s25_score >= 100 and   # 原120，按需放开至100
            _s25_valid and
            # CHOP体制不再排除：reasoning_gate自行判断 (P1b 2026-06-29)
            (_s25_pup > 0.55 or _s25_score >= 130)  # 略收紧score门槛补偿CHOP放开
        )

        if _s25_should:
            import sys as _sys25
            _s25_parent = str(Path(__file__).parent)
            if _s25_parent not in _sys25.path: _sys25.path.insert(0, _s25_parent)  # [S1修复 2026-08-24]
            from reasoning_client import reasoning_gate as _rg25
            from macro_reasoning_enhancer import enhance_macro_score as _rmac25
            from sl_reasoning_enhancer import enhance_stop_loss as _rsl25
            from trigger_reasoning_enhancer import enhance_trigger_timing as _rtrig25

            _s25_entry_lo = _s25_params.get('entry_lo', 0)
            _s25_entry_hi = _s25_params.get('entry_hi', 0)
            _s25_sl       = _s25_params.get('stop_loss', 0)
            _s25_entry    = (_s25_entry_lo + _s25_entry_hi) / 2 if _s25_entry_lo else _s25_price

            # ── 并行调用四模块（苏摩B档核心升级）──────────────────
            _futures = {}
            with _cf25.ThreadPoolExecutor(max_workers=4, thread_name_prefix='s25') as _ex25:
                _futures['gate']    = _ex25.submit(_rg25, _result, True)
                _futures['macro']   = _ex25.submit(_rmac25,
                    _s25_sym, _s25_dir, _s25_regime,
                    float(_result.get('confluence',{}).get('breakdown',{}).get('宏观+事件', 10) or 10),
                    _s25_macro)
                _futures['sl']      = _ex25.submit(_rsl25,
                    _s25_sym, _s25_dir,
                    float(_s25_sl), float(_s25_entry), float(_s25_price),
                    0.0, 0.0, 0.0, 0.0, _s25_pup, _s25_regime)
                _s25_t15 = _s25_params.get('trigger_15m', {})
                _futures['trigger'] = _ex25.submit(_rtrig25,
                    _s25_sym, _s25_dir,
                    int(_s25_t15.get('confidence', 70) if _s25_t15 else 70),
                    float(_s25_price), float(_s25_entry_lo), float(_s25_entry_hi),
                    str(_s25_t15.get('wick_rejection',{}).get('type','') if _s25_t15 else ''),
                    _s25_pup, 0.0, 0.0, '', _s25_regime)

            # ── 收集并行结果 ────────────────────────────────────────
            _bd25 = _result['confluence'].setdefault('breakdown', {})

            # P0: 信号门控
            try:
                _gate25 = _futures['gate'].result(timeout=15)
                _v25 = _gate25.get('verdict', 'PASS')
                _c25 = _gate25.get('confidence', 0.5)
                if _v25 == 'WARN':
                    _result['score_final'] = _result.get('score_final', 0) - 8
                    _result['confluence']['score'] = _result['confluence'].get('score', 0) - 8
                elif _v25 == 'BLOCK':
                    _result['score_final'] = _result.get('score_final', 0) - 25
                    _result['valid_signal'] = False
                _bd25['s25_reasoning'] = (
                    f"{_v25} conf={_c25:.2f} pup={_s25_pup:.2f} | {_gate25.get('reason','')[:55]}"
                )
                print(f'[s25-Gate] {_s25_sym} {_s25_dir}: {_v25} conf={_c25:.2f}'
                      f' pup={_s25_pup:.2f} adj={-8 if _v25=="WARN" else (-25 if _v25=="BLOCK" else 0)}'
                      f' {_gate25.get("elapsed",0):.1f}s')
            except Exception:
                pass

            # P1a: 宏观增强
            try:
                _mac25 = _futures['macro'].result(timeout=15)
                _mac_score = _mac25.get('enhanced_score', 10)
                _mac_delta = _mac25.get('delta', 0)
                if abs(_mac_delta) >= 1.0:
                    _result['score_final'] = (_result.get('score_final', 0) or 0) + _mac_delta
                    _result['confluence']['score'] = (_result['confluence'].get('score', 0) or 0) + _mac_delta
                    _bd25['s25_macro'] = (
                        f"宏观动态={_mac_score:.0f}分(Δ{_mac_delta:+.0f}) "
                        f"impact={_mac25.get('impact','?')} src={_mac25.get('source','?')}"
                    )
                    print(f'[s25-Macro] {_s25_sym}: score={_mac_score:.0f} Δ{_mac_delta:+.0f}'
                          f' impact={_mac25.get("impact","?")} src={_mac25.get("source","?")}')
            except Exception:
                pass

            # P1b: 止损优化
            try:
                _sl25 = _futures['sl'].result(timeout=15)
                if _sl25.get('source') == 'reasoning_model' and _sl25.get('recommended_sl', 0) > 0:
                    _new_sl = _sl25['recommended_sl']
                    _result.setdefault('params', {})['stop_loss'] = _new_sl
                    _bd25['s25_sl'] = (
                        f"SL推理优化: {_s25_sl:.0f}→{_new_sl:.0f} "
                        f"action={_sl25.get('action','?')} conf={_sl25.get('confidence',0):.2f}"
                    )
                    print(f'[s25-SL] {_s25_sym}: {_s25_sl:.0f}→{_new_sl:.0f}'
                          f' action={_sl25.get("action","?")} conf={_sl25.get("confidence",0):.2f}')
            except Exception:
                pass

            # P2: 触发时机
            try:
                _trig25 = _futures['trigger'].result(timeout=15)
                _cadj = _trig25.get('confidence_adj', 0)
                if abs(_cadj) >= 5 or not _trig25.get('execute_now', True):
                    _bd25['s25_trigger'] = (
                        f"触发推理: exec={_trig25.get('execute_now',True)}"
                        f" cadj={_cadj:+d} wait={_trig25.get('wait_for','')[:40]}"
                    )
                    print(f'[s25-Trigger] {_s25_sym}: exec={_trig25.get("execute_now",True)}'
                          f' adj={_cadj:+d} {_trig25.get("reasoning","")[:40]}')
            except Exception:
                pass

    except Exception as _e25:
        pass  # s25任何异常绝对不影响主流程

    # ── UniversalAssetRouter 后置调整（设计院 2026-06-29）─────────────────
    # 资产类型×体制 二维权重矩阵 → score_final 精准调整
    # 3行代码让单一评分变成体系化资产路由
    try:
        from brahma_brain.universal_asset_router import apply_asset_routing as _uar
        _result = _uar(_result)
        _uar_mult = _result.get('asset_weight_mult', 1.0)
        _uar_type = _result.get('asset_type', '?')
    except Exception:
        pass

    # ══ [设计院 2026-06-30 P3] coingecko_client — 注入Token分类字段 ══════════
    # 模块: coingecko_client · 市值排名+类别，增强资产路由准确性
    try:
        from coingecko_client import classify_token as _cg_classify
        _cg_token_class = _cg_classify(_sym)
        if _cg_token_class:
            _result['token_class'] = _cg_token_class   # BLUECHIP / ALTCOIN / MEME / DEFI
    except Exception:
        pass
    # ══ [coingecko_client END] ═════════════════════════════════════════════════

    # ══ [设计院 2026-06-30 全量接入] PositionSizer ════════════════════════════
    # 模块: position_sizer · 替代手算仓位，基于评分+体制+Kelly公式
    try:
        from position_sizer import get_position_pct as _pos_fn
        _ps_score = _result.get('score_final', _result.get('score', 0))
        _ps_dir   = signal_dir or _result.get('signal_dir', 'SHORT')
        _ps_sl    = float((_result.get('params') or {}).get('sl_pct',
                    _result.get('sl_pct', 0)) or 0)
        # [P3 陷阱预警仓位减半 2026-08-22 苏摩111封印]
        # 方仓陷阱=True：入场减仓×0.5，等待CHoCH确认后恢复满仓
        _ps_trap  = bool(_result.get('fangcang_trap', False))
        _pos_res  = _pos_fn(_sym, _ps_score, _ps_dir,
                            nav=nav if nav else 0,
                            sl_pct=_ps_sl if _ps_sl > 0 else None)
        if _pos_res.get('allowed'):
            _pos_pct = _pos_res.get('pct', 0)
            if _ps_trap and _pos_pct > 0:
                _pos_pct = round(_pos_pct * 0.5, 2)
                _result['pos_trap_halved'] = True
            _result['pos_pct_sizer']    = _pos_pct
            _result['pos_level_sizer']  = _pos_res.get('level', '')
            _result['pos_reason_sizer'] = _pos_res.get('reason', '') + (' [陷阱预警×0.5]' if _ps_trap else '')
    except Exception:
        pass
    # ══ [PositionSizer END] ════════════════════════════════════════════════════

    # ══ [设计院 2026-06-30 全量接入] BrahmaEventBus 信号事件发布 ══════════════
    # 模块: brahma_event_bus · 信号发出时publish，解耦跨模块通信
    try:
        from brahma_event_bus import BrahmaEventBus as _BEB
        _eb       = _BEB()
        _sig_act  = _result.get('action', 'SKIP')
        _sig_scr  = _result.get('score_final', _result.get('score', 0))
        if _sig_act in ('ENTER', 'ENTER_FULL') and _sig_scr >= 120:
            _eb.emit_regime_change(
                _sym,
                ms.get('regime', ''),
                ms.get('regime', '')
            ) if hasattr(_eb, 'emit_regime_change') else None
    except Exception:
        pass
    # ══ [EventBus END] ════════════════════════════════════════════════════════

    # ══ [P2-6 设计院审判2026-06-30: 暴涨猎手不注入brahma_core] ══════════════
    # 判决：两套系统信号类型根本不同，不得混评分
    # 梵天 = 精确趋势入场信号 | 暴涨猎手 = 蓄能预警信号
    # 正确架构：独立信号通道，见 scripts/pump_signal_executor.py
    # ══ [END] ══════════════════════════════════════════════════════════════════

    # ── [s27/s28/s29 2026-07-03] 统计模式维度：Gap Up / Bounce / First Red Day ──
    try:
        import os as _os_sp
        _sp_dir = _os_sp.path.dirname(_os_sp.path.abspath(__file__))
        import sys as _sys_sp
        if _sp_dir not in _sys_sp.path: _sys_sp.path.insert(0, _sp_dir)
        from s27_gap_bounce_frd import s27_gap_up, s28_bounce_setup, s29_first_red_day
        _sp_k1h  = _result.get('_klines_1h') or (extra_data or {}).get('_klines_1h') or []
        _sp_k4h  = _result.get('_klines_4h') or (extra_data or {}).get('_klines_4h') or []
        _sp_reg  = _result.get('regime', '')
        _sp_sym  = _result.get('symbol', _sym)
        _s27 = s27_gap_up(_sp_sym, _sp_k1h, _sp_reg) if _sp_k1h else 0
        _s28 = s28_bounce_setup(_sp_sym, _sp_k1h, _sp_k4h, _sp_reg) if _sp_k1h else 0
        _s29 = s29_first_red_day(_sp_sym, _sp_k1h, _sp_reg) if _sp_k1h else 0
        _sp_total = _s27 + _s28 + _s29
        # 始终写入_result供full_report渲染（即使全0）
        _result['s27_gap_up']       = _s27
        _result['s28_bounce_setup'] = _s28
        _result['s29_first_red_day']= _s29
        if _sp_total != 0:
            _result['score_final'] = (_result.get('score_final') or 0) + _sp_total
            print(f'[s27-29] {_sp_sym} gap={_s27:+d} bounce={_s28:+d} frd={_s29:+d} total={_sp_total:+d}')
    except Exception as _esp:
        pass  # 统计模式维度不影响主评分

    # ══ [可观测-v2] ══
    try:
        _s=_result.get('score_final',_result.get('score',0))
        pass  # [静默] f'[SIGNAL-SUMMARY] {_sym} {signal_dir} score={_s:.0f} action={_result.get("actio
    except Exception: pass

    # ══ [设计院 2026-08-09 苏摩111封印] 方仓向量WR → score_final 架构接线 ══
    # 铁证：Qdrant 3071案例 黄金区(bb1.5-2%+RSI60-75) WR=70.8% EV=+3.41%
    # 接线逻辑：fangcang.vector_stats.wr_directional 影响最终执行评分
    #   wr>=0.65 → +8分（高置信方仓确认，推过155执行门槛）
    #   wr<=0.40 → -8分（低置信，过滤假信号）
    # 注意：fangcang字段在下方代码块写入，此处读取时尚未写入，故在写入后接线
    # ══ 方仓+决策树接入brahma_core主链路 ══
    # 根因修复：runner调用brahma_core.analyze()，今日修复误打到brahma_engine.py
    # 现在正确打到brahma_core.py的return _result前
    try:
        from brahma_brain.fangcang_engine import get_fangcang_context as _fc_fn
        _fc_regime = _result.get('regime', '')
        _fc_res = _fc_fn(_sym, current_regime=_fc_regime)
        _result['fangcang'] = _fc_res
        # [HCME升级 2026-08-23 苏摩111封印] 方仓增强型HCME替换旧pseudo HCME
        # 旧：hcme_matcher(pseudo伪信号45670条) → WR=46.4% EV=-0.146%（伪数据，无效惩罚）
        # 新：fangcang_hcme_bridge(真实案例1597条) → 方向概率驱动，置信度门控
        # Step A: pseudo HCME完全弃用
        # Step B: 方仓1597条真实案例相似度匹配（BBW±35% + RSI±18 + 体制）
        # Step C: 置信度门控（n<3→0分 / n3~9→半权 / n≥10→全权）
        _actual_dir = _result.get('signal_dir', '')
        if _actual_dir in ('LONG', 'SHORT') and isinstance(_fc_res, dict):
            try:
                # [设计院 2026-08-25 苏摩111] 统一方仓查询层：合并系统1(K线)+系统2(案例库)
                # 替代原来的 hcme_wr_adj=0.5（几乎没用）
                from brahma_fangcang_unified import unified_fangcang as _uf
                _uf_result = _uf(
                    symbol=_sym,
                    ms=ms,
                    signal_dir=_actual_dir,
                    regime=_result.get('regime', 'UNKNOWN'),
                )
                _fc_res['hcme_wr_adj']    = _uf_result['unified_adj']
                _fc_res['hcme_context']   = _uf_result['summary']
                _fc_res['hcme_source']    = 'unified_v1'
                _fc_res['unified_s1_adj'] = _uf_result['s1_adj']
                _fc_res['unified_s2_adj'] = _uf_result['s2_adj']
                _fc_res['unified_s2_wr']  = _uf_result['s2_wr']
                _fc_res['unified_s2_n']   = _uf_result['s2_n']
                _result['fangcang'] = _fc_res
            except Exception as _hcme_e:
                import logging as _lg2; _lg2.getLogger('brahma').warning(f'[unified_fangcang] {_hcme_e}')
        _hcme_adj = _fc_res.get('hcme_wr_adj', 0) if isinstance(_fc_res, dict) else 0
        _hcme_ctx = _fc_res.get('hcme_context', '') if isinstance(_fc_res, dict) else ''
        if _hcme_adj != 0:
            _cf = _result.setdefault('confluence', {})
            _bd = _cf.setdefault('breakdown', {})
            _bd['HCME情境匹配'] = _hcme_adj
            # 同步更新 score_final
            _old_score = float(_result.get('score_final', _result.get('score', 0)) or 0)
            _new_score = round(_old_score + _hcme_adj, 1)
            _result['score_final'] = _new_score
            _result['score']       = _new_score
            _result['hcme_adj']    = _hcme_adj
            _result['hcme_ctx']    = str(_hcme_ctx)[:120]
    except Exception as _fc_e:
        import logging as _lg; _lg.getLogger('brahma').warning(f'[fangcang] {_fc_e}')
        _result['fangcang'] = {'status': 'unavailable', 'reason': str(_fc_e)[:60]}

    # [设计院 2026-08-25 苏摩111] 长期记忆注入：跨资产20年知识库
    try:
        from brahma_longmem import get_longmem_score_adj as _lm_fn
        _lm_res = _lm_fn(_sym, _result.get('regime', 'UNKNOWN'),
                         _result.get('signal_dir', signal_dir or 'LONG'))
        _lm_adj = float(_lm_res.get('adj', 0) or 0)
        _result['longmem_adj']  = _lm_adj
        _result['longmem_ctx']  = _lm_res.get('summary', '')[:120]
        _result['extreme_warn'] = _lm_res.get('extreme_warning', {}).get('warning_level', 'NONE')
        if _lm_adj != 0:
            _cf = _result.setdefault('confluence', {})
            _bd = _cf.setdefault('breakdown', {})
            _bd['长期记忆跨资产'] = _lm_adj
            _old = float(_result.get('score_final', _result.get('score', 0)) or 0)
            _new = round(_old + _lm_adj, 1)
            _result['score_final'] = _new
            _result['score']       = _new
    except Exception as _lm_e:
        import logging as _lm_log; _lm_log.getLogger('brahma').debug(f'[longmem] {_lm_e}')

    # ══ [N_EXP 2026-08-29 苏摩111] 40年经验引擎注入 ══════════════════════════
    # 使命：把20392条6.5年K线蒸馏的经验矩阵实时注入评分
    # 接入：longmem之后，decision_engine之前
    try:
        from brahma_brain.fangcang_experience_engine import get_exp_adj as _exp_fn
        _exp_rsi   = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        _exp_burst = float((_result.get('fangcang') or {}).get('avg_burst_atr_mult', 1.0) or 1.0)
        _exp_tf    = str(ms.get('entry_tf', ms.get('tf', '4h')) or '4h')
        _exp_res   = _exp_fn(
            regime     = _result.get('regime', 'CHOP_MID'),
            signal_dir = _result.get('signal_dir', signal_dir or 'LONG'),
            timeframe  = _exp_tf,
            rsi        = _exp_rsi,
            burst_mult = _exp_burst,
        )
        _exp_adj = float(_exp_res.get('adj', 0) or 0)
        if _exp_adj != 0 and _exp_res.get('n', 0) >= 10:
            _old_exp = float(_result.get('score_final', _result.get('score', 0)) or 0)
            _new_exp = round(_old_exp + _exp_adj, 1)
            _result['score_final'] = _new_exp
            _result['score']       = _new_exp
            _result['exp_engine']  = _exp_res
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['N_EXP40年经验'] = (
                f'{_exp_adj:+.1f}({_exp_res["rule_hit"]} WR={_exp_res["wr"]:.0%} n={_exp_res["n"]})'
            )
    except Exception as _exp_e:
        import logging as _exp_log; _exp_log.getLogger('brahma').debug(f'[exp_engine] {_exp_e}')
    # ══ [END N_EXP] ══════════════════════════════════════════════════════════

    try:
        from brahma_brain.brahma_decision_engine import decide as _dt_decide
        _dt_signal = {
            'symbol':    _result.get('symbol', _sym),
            'direction': _result.get('direction', signal_dir or 'LONG'),
            'regime':    _result.get('regime', ''),
            'score':     float(_result.get('score', 0) or 0),
            'sl_pct':    float((_result.get('params') or {}).get('sl_pct', 0) or 0),
            'grade':     float(_result.get('grade', _result.get('structure_grade', 0)) or 0),
            'timing':    _result.get('timing_label', _result.get('timing', '')),
            'price':     float(_result.get('price', 0) or 0),
            'entry_lo':  float((_result.get('params') or {}).get('entry_lo', 0) or 0),
            'sl':        float((_result.get('params') or {}).get('stop_loss', 0) or 0),
            'tp1':       float((_result.get('params') or {}).get('tp1', 0) or 0),
            'rr':        float((_result.get('params') or {}).get('rr1', 0) or 0),
        }
        _dt_res = _dt_decide(_dt_signal)
        _result['decision']        = _dt_res
        _result['decision_action'] = _dt_res.get('action', 'SKIP')
        _result['decision_reason'] = _dt_res.get('reason', '')
        _result['decision_step']   = _dt_res.get('step_passed', 0)
    except Exception as _dt_e:
        import logging as _lg; _lg.getLogger('brahma').warning(f'[decision_tree] {_dt_e}')
        _result['decision'] = {'action': 'SKIP', 'reason': f'error:{_dt_e}', 'step_passed': 0}

    # [设计院封印 2026-08-09 苏摩111] 方仓向量WR → score_final 架构接线
    # 核心：fangcang/Qdrant的WR结果终于影响auto_executor的执行决策
    # 铁证：黄金区(bb1.5-2%+RSI60-75) WR=70.8% EV=+3.41%
    #         极压缩(bb<0.5%) WR=35% EV=-0.50%——区分度高
    try:
        _fc_wr = (
            _result
            .get('fangcang', {})
            .get('vector_stats', {})
            .get('wr_directional', 0.5)
        )
        if isinstance(_fc_wr, (int, float)) and _fc_wr > 0:
            if _fc_wr >= 0.65:
                _delta = 8
            elif _fc_wr <= 0.40:
                _delta = -8
            else:
                _delta = 0
            if _delta != 0:
                _result['score_final'] = (_result.get('score_final') or 0) + _delta
                _result['fangcang_wr_delta'] = _delta
                _result['fangcang_wr_used']  = round(_fc_wr, 3)
    except Exception:
        pass

    # ══ [P1 方仓RSI分层 2026-08-22 设计院自主封印] ══════════════════════════════════
    # 铁证(535条 6.8年): RSI>65+方仓在压缩→做多率24.4% RSI<35→做空率27.5%
    # 分层设计：RSI方向与方仓偏向一致→加分；矛盾→减分
    try:
        _fc_rsi_dir = _result.get('signal_dir', 'LONG')
        # [修复 2026-08-24] rsi_1h/rsi_4h不在_result顶层，从ms['momentum']读取
        _fc_mom = (_result.get('market_state_raw') or ms).get('momentum', {})
        _fc_rsi_1h  = float(_result.get('rsi_1h') or _fc_mom.get('rsi_1h', 50) or 50)
        _fc_rsi_4h  = float(_result.get('rsi_4h') or _fc_mom.get('rsi_4h', 50) or 50)
        _fc_hint    = (_result.get('fangcang') or {}).get('signal_hint', 'NEUTRAL')
        _fc_trap    = (_result.get('fangcang') or {}).get('trap_alert', False)
        _fc_rsi_adj = 0
        _fc_rsi_note = ''
        # 方仓铁证：RSI>65 = 做多极佳条件（历号53/217条突破向上）
        if _fc_rsi_dir == 'LONG' and _fc_rsi_4h > 65 and _fc_hint in ('LONG_BIAS', 'NEUTRAL'):
            _fc_rsi_adj = +12
            _fc_rsi_note = f'P1方仓RSI分层做多(RSI4H={_fc_rsi_4h:.0f}>65) +12'
        # 方仓铁证：RSI<35 = 做空极佳条件（46/167条突破向下）
        elif _fc_rsi_dir == 'SHORT' and _fc_rsi_4h < 35 and _fc_hint in ('SHORT_BIAS', 'NEUTRAL'):
            _fc_rsi_adj = +12
            _fc_rsi_note = f'P1方仓RSI分层做空(RSI4H={_fc_rsi_4h:.0f}<35) +12'
        # 矛盾信号：方向与RSI矛盾→减分
        elif _fc_rsi_dir == 'LONG' and _fc_rsi_4h < 35:
            _fc_rsi_adj = -8
            _fc_rsi_note = f'P1方仓RSI分层失分(做多RSI4H={_fc_rsi_4h:.0f}<35矛盾) -8'
        elif _fc_rsi_dir == 'SHORT' and _fc_rsi_4h > 65:
            _fc_rsi_adj = -8
            _fc_rsi_note = f'P1方仓RSI分层失分(做空RSI4H={_fc_rsi_4h:.0f}>65矛盾) -8'
        # 陷阱预警：仳位×0.5需要在position_sizer处理，这里只指爱标记
        if _fc_rsi_adj != 0:
            _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _fc_rsi_adj, 1)
            _result['score'] = _result['score_final']
            _result.setdefault('confluence', {}).setdefault('breakdown', {})\
                .update({'P1方仓RSI': _fc_rsi_note})
            _result['fangcang_rsi_adj'] = _fc_rsi_adj
            _result['fangcang_rsi_note'] = _fc_rsi_note
        # 陷阱预警标记：传递给position_sizer减仓
        if _fc_trap:
            _result['fangcang_trap'] = True
    except Exception:
        pass
    # ══ [END P1 方仓RSI分层] ══

    # ══ [HTF周月线锚定 score_addon 接入 2026-08-28 苏摩111] ══════════════════
    # 接入位置: fangcang已将htf_anchor存入返回对象，这里提取其score_addon注入总分
    # weekly_monthly_anchor铁证: htf_bias=BULLISH → +8 / BEARISH → -8 / NEUTRAL → 0
    # [P0修复 2026-08-29 苏摩111] 无论score_addon是否为0，都写入breakdown展示真实共振值
    try:
        _htf_data = (_result.get('fangcang') or {}).get('htf_anchor', {})
        _htf_addon = int(_htf_data.get('score_addon', 0) or 0)
        _htf_bias = _htf_data.get('htf_bias', 'NEUTRAL')
        _htf_res  = _htf_data.get('htf_resonance', 0.0)
        # [根囤修复 2026-08-29 苏摩111] 若缓存共振值=0，实时调用get_features()补充
        if _htf_res == 0.0 or not _htf_bias or _htf_bias == 'NEUTRAL':
            try:
                from brahma_brain.weekly_monthly_anchor import get_anchor as _wma_fn
                _wma_inst = _wma_fn(symbol)
                _wf = _wma_inst.get_features(current_price=float((_result.get('price') or 0)))
                _htf_res  = _wf.get('htf_resonance', _htf_res)
                _htf_bias = _wf.get('htf_bias', _htf_bias)
                _htf_addon = int(_wf.get('score_addon', _htf_addon) or _htf_addon)
            except Exception:
                pass
        if _htf_addon != 0:
            _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _htf_addon, 1)
            _result['score'] = _result['score_final']
        # 始终写入breakdown，让共振值可见
        if _htf_data:
            _result.setdefault('confluence', {}).setdefault('breakdown', {})\
                .update({'HTF周月线锚定': f'{_htf_addon:+d} ({_htf_bias} 共振={_htf_res:.2f})'})
            _result['htf_score_addon'] = _htf_addon
    except Exception:
        pass
    # ══ [END HTF周月线锚定] ══

    # ══ [P4 三周期RSI共振 2026-08-22 设计院自主] ══════════════════════════════
    # 顶级交易员标准：1H+4H+1D三周期同向=信号最强，分歧=降权
    # 规则(铁证来源：方仓535条SHORT/LONG突破规律):
    #   做多三重超卖(1H<35+4H<45+1D<55) → +15 | 两重(1H<35+4H<45) → +8
    #   做空三重超买(1H>65+4H>60+1D>60) → +15 | 两重(1H>65+4H>60) → +8
    #   逆流做多(三重超买) → -5 | 逆流做空(三重超卖) → -5
    try:
        _p4_dir   = _result.get('signal_dir', 'LONG')
        # [修复 2026-08-24] rsi从ms['momentum']读取
        _p4_mom = (_result.get('market_state_raw') or ms).get('momentum', {})
        _p4_r1h   = float(_result.get('rsi_1h') or _p4_mom.get('rsi_1h', 50) or 50)
        _p4_r4h   = float(_result.get('rsi_4h') or _p4_mom.get('rsi_4h', 50) or 50)
        _p4_r1d   = float(_result.get('rsi_1d') or _p4_mom.get('rsi_1d', 50) or 50)
        _p4_adj   = 0
        _p4_note  = ''
        if _p4_dir == 'LONG':
            _p4_low1h = _p4_r1h < 35
            _p4_low4h = _p4_r4h < 45
            _p4_low1d = _p4_r1d < 55
            _p4_hi1h  = _p4_r1h > 65
            _p4_hi4h  = _p4_r4h > 65
            _p4_hi1d  = _p4_r1d > 65
            if _p4_low1h and _p4_low4h and _p4_low1d:
                _p4_adj = +15
                _p4_note = f'P4三重超卖共振做多(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}/1D={_p4_r1d:.0f}) +15'
            elif _p4_low1h and _p4_low4h:
                _p4_adj = +8
                _p4_note = f'P4双重超卖共振做多(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}) +8'
            elif _p4_hi1h and _p4_hi4h and _p4_hi1d:
                _p4_adj = -5
                _p4_note = f'P4三重超买逆流做多(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}/1D={_p4_r1d:.0f}) -5'
        elif _p4_dir == 'SHORT':
            _p4_hi1h = _p4_r1h > 65
            _p4_hi4h = _p4_r4h > 60
            _p4_hi1d = _p4_r1d > 60
            _p4_low1h = _p4_r1h < 35
            _p4_low4h = _p4_r4h < 40
            _p4_low1d = _p4_r1d < 45
            if _p4_hi1h and _p4_hi4h and _p4_hi1d:
                _p4_adj = +15
                _p4_note = f'P4三重超买共振做空(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}/1D={_p4_r1d:.0f}) +15'
            elif _p4_hi1h and _p4_hi4h:
                _p4_adj = +8
                _p4_note = f'P4双重超买共振做空(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}) +8'
            elif _p4_low1h and _p4_low4h and _p4_low1d:
                _p4_adj = -5
                _p4_note = f'P4三重超卖逆流做空(1H={_p4_r1h:.0f}/4H={_p4_r4h:.0f}/1D={_p4_r1d:.0f}) -5'
        if _p4_adj != 0:
            _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _p4_adj, 1)
            _result['score'] = _result['score_final']
            _result.setdefault('confluence', {}).setdefault('breakdown', {})                .update({'P4三周期共振': _p4_note})
            _result['p4_resonance_adj']  = _p4_adj
            _result['p4_resonance_note'] = _p4_note
    except Exception:
        pass
    # ══ [END P4 三周期RSI共振] ══

    # 根因：timing_filter模块存在但从未接入主链路，时机判断完全缺失
    # 接入逻辑：timing badge → 注入breakdown → 影响score_final → 传递给决策树Step5
    try:
        from brahma_brain.timing_filter import evaluate_timing as _tf_eval
        _tf_dir    = _result.get('signal_dir', 'LONG')
        _tf_regime = _result.get('regime', 'CHOP_MID')
        _tf_score  = float(_result.get('score_final', 0) or 0)
        _tf_rsi1h  = float(_result.get('rsi_1h', 50) or 50)
        _tf_p_up   = float((_result.get('fangcang', {}) or {}).get('long_prob', 0.5) or 0.5)
        _tf_grade  = float(_result.get('grade', _result.get('structure_grade', 50)) or 50)
        _tf_price  = float(_result.get('price', 0) or 0)
        _tf_elo    = float((_result.get('params') or {}).get('entry_lo', _tf_price) or _tf_price)
        _tf_ehi    = float((_result.get('params') or {}).get('entry_hi', _tf_price) or _tf_price)
        _tf_res    = _tf_eval(
            symbol=_sym,
            signal_dir=_tf_dir,
            score=_tf_score,
            grade=_tf_grade,
            entry_lo=_tf_elo,
            entry_hi=_tf_ehi,
            current_price=_tf_price,
            rsi_1h=_tf_rsi1h,
            s23_p_up=_tf_p_up,
            regime=_tf_regime,
        )
        _tf_badge  = _tf_res.get('badge', 'MONITOR')
        _tf_adj    = int(_tf_res.get('score', _tf_res.get('score_adj', 0)) or 0)
        _result['timing_badge']  = _tf_badge
        _result['timing_result'] = _tf_res
        # 注入breakdown和score
        if _tf_adj != 0:
            _cf2 = _result.setdefault('confluence', {})
            _bd2 = _cf2.setdefault('breakdown', {})
            _bd2['时机门控'] = _tf_adj
            _old_s2 = float(_result.get('score_final', 0) or 0)
            _result['score_final'] = round(_old_s2 + _tf_adj, 1)
            _result['score']       = _result['score_final']
    except Exception as _tf_e:
        import logging as _lg3; _lg3.getLogger('brahma').warning(f'[timing_filter] {_tf_e}')
        _result.setdefault('timing_badge', 'MONITOR')

    # [设计院封印 2026-08-10 苏摩111] TradFi专属方仓向量库接入
    # 当分析标的是TradFi代币时，额外查询 fangcang_tradfi_db
    # wr>=0.65 → +6 / wr<=0.40 → -6（略低于BTC方仓±8，TradFi数据年限较短）
    try:
        # [2026-08-10 验证封印] MSTR降权±6→±3 / TSLA降为B级暂停调整
        # 铁证: MSTR历史WR=47% EV多次为负 异常BTC代理力学
        #         TSLA OOS n=61样本量不足 衡减保守处理
        _tradfi_tokens = set([
            'XAUUSDT','QQQUSDT','NVDAUSDT','AAPLUSDT','MSFTUSDT','XAGUSDT',
            'SNDKUSDT','MUUSDT','INTCUSDT','GOOGLUSDT','AMDUSDT','CLUSDT',
        ])  # TSLAUSDT降为B级暂移出 / MSTRUSDT单独处理
        _mstr_tokens = {'MSTRUSDT'}  # MSTR专属：权重±3（降半）
        if _sym in _tradfi_tokens or _sym in _mstr_tokens:
            from brahma_brain.fangcang_tradfi_db import query_tradfi as _tfi_q
            _tfi_bbw  = _result.get('fangcang', {}).get('bbw_4h',
                        _result.get('confluence', {}).get('bbw_4h', 1.5))
            _tfi_rsi  = _result.get('rsi_1h', 55.0)
            _tfi_dir  = signal_dir or 'UP'
            _tfi_res  = _tfi_q(
                token=_sym, bb_width_raw=float(_tfi_bbw or 1.5),
                squeeze_bars=42, burst_atr=0.9, vol_ratio=2.0,
                rsi=float(_tfi_rsi or 55), direction=_tfi_dir, top_k=20,
            )
            _tfi_wr = _tfi_res.get('wr_directional', 0.5)
            # MSTR权重降半：±3（验证: BTC代理工具，方仓逻辑与普通股票不同）
            _max_delta = 3 if _sym in _mstr_tokens else 6
            _tfi_delta = _max_delta if _tfi_wr >= 0.65 else (-_max_delta if _tfi_wr <= 0.40 else 0)
            if _tfi_delta != 0:
                _result['score_final'] = (_result.get('score_final') or 0) + _tfi_delta
            _result['tradfi_wr']       = round(_tfi_wr, 3)
            _result['tradfi_wr_delta'] = _tfi_delta
            _result['tradfi_n']        = _tfi_res.get('n', 0)
    except Exception:
        pass
    # ══ [END 方仓+决策树] ══
    # ══ [B类模块接入 2026-08-09 设计院深度排查封印 苏摩111] ══════════════════════
    # 根因：4个模块功能建好但未接通主链路，靠苏摩追问发现。
    # 铁律：封印 = 代码完成 + 调用验证 + full_report输出可见 + 冒烟测试

    # B1: SSI轧空强度指数 — 做空时注入轧空风险门控
    try:
        from brahma_brain.ssi_engine import compute_ssi as _ssi_fn
        _ssi_dir = _result.get('signal_dir', 'LONG')
        # LONG方向：空头极拥挤 → SSI轧空利好（加分，而非惩罚）
        if _ssi_dir == 'LONG':
            try:
                _ssi_sent_l = _result.get('sentiment', {})
                _ssi_short_r = 100.0 - float(_ssi_sent_l.get('long_short_ratio', 50.0))
                if _ssi_short_r >= 70.0:
                    # 极端空头拥挤：轧空潜力，做多正面信号
                    _ssi_bonus = min(8, round((_ssi_short_r - 70.0) * 0.4, 1))
                    _result.setdefault('breakdown_extra', {})['ssi_long_squeeze_bonus'] = _ssi_bonus
                    _result.setdefault('confluence', {}).setdefault('breakdown', {})['SSI轧空潜力'] = _ssi_bonus
                    _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _ssi_bonus, 1)
                    _result['score'] = _result['score_final']
            except Exception:
                pass
        if _ssi_dir == 'SHORT':
            _ssi_sent = _result.get('sentiment', {})
            _ssi_res = _ssi_fn(
                symbol=symbol,
                short_ratio=100.0 - float(_ssi_sent.get('long_short_ratio', 50.0)),
                oi=float(_ssi_sent.get('oi', 0) or 0),
                price=float(_result.get('price', 0) or 0),
                vol_current=float(_ssi_sent.get('oi_change_pct', 0) or 0),
                fr_rate=float(_ssi_sent.get('funding_rate', 0) or 0),
            )
            _ssi_level = _ssi_res.get('level', 'NORMAL')
            _result['ssi'] = _ssi_res
            # 轧空高风险 → 做空降分
            # [2026-08-12 苏摩111修复P0] 只记录penalty，不在此扣分
            # 统一在下方"SSI惩罚同步注入"块执行一次，防止双重扣分
            if _ssi_level == 'HIGH':
                _result.setdefault('breakdown_extra', {})['ssi_penalty'] = -12
            elif _ssi_level == 'EXTREME':
                # [P2修复] 若当前价在空OB内（压力位做空），惩罚减半
                _ssi_in_ob = False
                try:
                    _ssi_bear_ob = (_result.get('smc') or {}).get('order_blocks', {}).get('nearest_bear_ob') or {}
                    _ssi_price   = float(_result.get('price', 0) or 0)
                    _ssi_ob_low  = float(_ssi_bear_ob.get('low', 0) or 0)
                    _ssi_ob_high = float(_ssi_bear_ob.get('high', 0) or 0)
                    if _ssi_ob_low > 0 and _ssi_ob_low <= _ssi_price <= _ssi_ob_high * 1.02:
                        _ssi_in_ob = True
                except Exception:
                    pass
                _ssi_penalty = -10 if _ssi_in_ob else -20
                _result.setdefault('breakdown_extra', {})['ssi_penalty'] = _ssi_penalty
        # SSI惩罚统一注入confluence.breakdown（仅此一处修改score_final）
        _ssi_pen = _result.get('breakdown_extra', {}).get('ssi_penalty', 0)
        if _ssi_pen != 0:
            _cf_ssi = _result.setdefault('confluence', {})
            _bd_ssi = _cf_ssi.setdefault('breakdown', {})
            _bd_ssi['SSI轧空门控'] = _ssi_pen
            _old_s3 = float(_result.get('score_final', 0) or 0)
            _result['score_final'] = round(_old_s3 + _ssi_pen, 1)
            _result['score']       = _result['score_final']
    except Exception as _ssi_e:
        pass  # SSI接入失败不阻断主流程

    # ══ [设计院 2026-08-12 苏摩111封印] cross_asset_gate BTC/ETH相关性门控接线 ══
    # 根因：cross_asset_gate.py存在但完全未接入，BTC/ETH双开时1.85x风险敞口无法检测
    # 逻辑：BTC是市场锚；ETH信号时检查BTC联动跌幅是否超过ETH止损
    try:
        from brahma_brain.cross_asset_gate import get_gate as _cag_get
        _cag_dir   = _result.get('signal_dir', 'LONG')
        _cag_sym   = _sym
        # 只对ETH/山寨做联动检查（BTC本身是锚）
        if _cag_sym not in ('BTCUSDT', 'BTCDOMUSDT'):
            _cag_gate = _cag_get()
            _cag_sl_pct = float(_result.get('sl_atr_mult', 2.0) or 2.0)
            _cag_price  = float(_result.get('price', 0) or 0)
            _cag_entry  = _cag_price  # 当前价格作为入场代理
            _cag_signal = {
                'symbol':    _cag_sym,
                'direction': _cag_dir,
                'price':     _cag_price,
                'entry_lo':  _cag_price * (1 - _cag_sl_pct/100),
                'entry_hi':  _cag_price * (1 + _cag_sl_pct/100),
                'sl_pct':    _cag_sl_pct,
                'regime':    _result.get('regime', 'CHOP_MID'),
                'score':     float(_result.get('score_final', 0) or 0),
            }
            _cag_res = _cag_gate.check(_cag_signal)
            _cag_action = _cag_res.get('action', 'PASS')
            _result['cross_asset_gate'] = _cag_res
            if _cag_action in ('WAIT', 'DOWNGRADE'):
                _cag_adj = -8  # 联动风险惩罚
                _cf_cag = _result.setdefault('confluence', {})
                _bd_cag = _cf_cag.setdefault('breakdown', {})
                _bd_cag['跨资产联动风险'] = _cag_adj
                _old_s4 = float(_result.get('score_final', 0) or 0)
                _result['score_final'] = round(_old_s4 + _cag_adj, 1)
                _result['score']       = _result['score_final']
    except Exception as _cag_e:
        import logging as _lg4; _lg4.getLogger('brahma').warning(f'[cross_asset_gate] {_cag_e}')

    # ══ [cross_asset_correlator 2026-08-29 苏摩111] 宏观相关性评分注入 ══
    # 之前只在 brahma_1hao_analysis.py 展示，brahma_core scoring 完全没用到
    # VIX/DXY/BTC.D/利率 → score_addon_total → 注入 score_final
    try:
        from brahma_brain.cross_asset_correlator import get_cross_asset_context as _get_cross
        _cross_ctx   = _get_cross(symbol=_sym, current_price=float(_result.get('price', 0) or 0))
        _cross_addon = int(_cross_ctx.get('score_addon_total', 0) or 0)
        if _cross_addon != 0:
            _old_s_cross = float(_result.get('score_final', 0) or 0)
            _result['score_final'] = round(_old_s_cross + _cross_addon, 1)
            _result['score']       = _result['score_final']
            _result['cross_asset_macro'] = _cross_ctx
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['宏观相关性'] = (
                f'{_cross_addon:+d}'
                f'(VIX={_cross_ctx.get("vix",{}).get("vix_now","N/A")}'
                f' BTC.D={"✅山寨季" if _cross_ctx.get("btcd",{}).get("altcoin_season") else ""})'
            )
    except Exception:
        pass  # 宏观层失败静默降级，不阻断主链

    # B2: brahma_coordinator — 子系统上下文聚合
    try:
        from brahma_brain.brahma_coordinator import get_episodic_context as _coord_ep
        from brahma_brain.brahma_coordinator import get_ic_context as _coord_ic
        _regime_c = _result.get('regime', 'UNKNOWN')
        _dir_c = _result.get('signal_dir', 'LONG')
        _score_c = float(_result.get('score_final') or 0)
        _ep_ctx = _coord_ep(symbol, _regime_c, _dir_c)
        _ic_ctx = _coord_ic(_regime_c, _dir_c, _score_c)
        _result['coordinator'] = {'episodic': _ep_ctx, 'ic': _ic_ctx}
    except Exception:
        pass  # coordinator失败不阻断

    # B3: signal_integrity_gate — P0~P2 信号完整性校验
    try:
        from brahma_brain.signal_integrity_gate import gate_check as _gate_fn
        _cf_gate = _result.get('confluence', {})
        _params_gate = _result.get('params', {})
        _ms_gate = _result.get('momentum', {})
        _gate_ok, _gate_reason = _gate_fn(_cf_gate, _params_gate, _ms_gate)
        _result['integrity_gate'] = {'passed': _gate_ok, 'reason': _gate_reason}
        if not _gate_ok:
            # 完整性校验失败 → score强制降权，不硬封禁（不改decision）
            _result['score_final'] = (_result.get('score_final') or 0) - 10
            _result.setdefault('breakdown_extra', {})['integrity_gate'] = -10
    except Exception:
        pass  # gate失败不阻断

    # B4: mode_c_detector — 庄家行情识别，高波动假信号过滤
    try:
        from brahma_brain.mode_c_detector import detect as _mode_c_fn
        _mc_sent = _result.get('sentiment', {})
        _mc_mom = _result.get('momentum', {})
        _mc_kl_raw = (_result.get('extra') or {}).get('_k1h_raw') or []
        _mc_highs = [float(k[2]) for k in _mc_kl_raw[-20:]] if _mc_kl_raw and isinstance(_mc_kl_raw[0],(list,tuple)) else []
        _mc_lows  = [float(k[3]) for k in _mc_kl_raw[-20:]] if _mc_kl_raw and isinstance(_mc_kl_raw[0],(list,tuple)) else []
        _mc_vols  = [float(k[5]) for k in _mc_kl_raw[-20:]] if _mc_kl_raw and isinstance(_mc_kl_raw[0],(list,tuple)) else []
        _mc_price = float(_result.get('price', 0) or 0)
        _mc_res = _mode_c_fn(
            symbol=symbol,
            price=_mc_price,
            price_low_24h=min(_mc_lows) if _mc_lows else _mc_price * 0.98,
            short_ratio=100.0 - float(_mc_sent.get('long_short_ratio', 50.0)),
            vol_current=_mc_vols[-1] if _mc_vols else 0,
            vol_avg_20=sum(_mc_vols)/len(_mc_vols) if _mc_vols else 1,
            candle_high=max(_mc_highs) if _mc_highs else _mc_price * 1.01,
            candle_low=min(_mc_lows) if _mc_lows else _mc_price * 0.99,
            fr_rate=float(_mc_sent.get('funding_rate', 0) or 0),
        )
        _result['mode_c'] = _mc_res
        if _mc_res and _mc_res.get('is_mode_c'):
            # 庄家行情 → 仓位系数×0.5（写入pos_pct_sizer，不改score）
            _result['pos_pct_sizer'] = (_result.get('pos_pct_sizer') or 0.5) * 0.5
            _result.setdefault('breakdown_extra', {})['mode_c_halved'] = True
    except Exception:
        pass  # mode_c失败不阻断

    # ══ [END B类模块接入] ══════════════════════════════════════════════════════


    # ══ [C类孤岛模块接入 2026-08-09 设计院] ══════════════════════════════════
    # us_session_gate / volatility_context / tradfi_signal_layer

    # C1: us_session_gate — 美股时段门控，TradFi标的需要时段感知
    try:
        from brahma_brain.us_session_gate import get_us_session as _us_sess_fn
        from brahma_brain.us_session_gate import get_session_regime_delta as _us_delta_fn
        _us_info = _us_sess_fn()
        _us_delta = _us_delta_fn(_us_info, _result.get('regime', ''), _result.get('signal_dir', 'LONG'))
        _result['us_session'] = {'session': _us_info.get('session'), 'delta': _us_delta}
        if isinstance(_us_delta, (int, float)) and _us_delta != 0:
            _result['score_final'] = (_result.get('score_final') or 0) + _us_delta
            _result.setdefault('breakdown_extra', {})['us_session_delta'] = _us_delta
    except Exception:
        pass

    # C2: volatility_context — HCME M5 波动率历史分位
    try:
        from brahma_brain.volatility_context import get_volatility_context as _vol_ctx_fn
        _vc_mom = _result.get('momentum', {})
        _vc_atr = float(_vc_mom.get('atr_1h') or 0) / float(_result.get('price', 1) or 1)
        _vc_bb  = (_result.get('extra') or {}).get('bb_width') or 0.01
        _vol_ctx = _vol_ctx_fn(symbol, current_atr=_vc_atr, current_bbw=float(_vc_bb))
        _result['volatility_context'] = _vol_ctx
        # 极低波动率(compress <10th pct) → 压缩仓位×0.7
        if _vol_ctx.get('vol_regime') == 'ULTRA_LOW':
            _result['pos_pct_sizer'] = (_result.get('pos_pct_sizer') or 0.5) * 0.7
            _result.setdefault('breakdown_extra', {})['vol_ultra_low_compress'] = True
    except Exception:
        pass

    # C3: tradfi_signal_layer — TradFi信号层，标签注入breakdown
    try:
        from brahma_brain.tradfi_signal_layer import compute_tradfi_context as _tf_sig_fn
        _tf_sig = _tf_sig_fn(
            symbol, _result.get('signal_dir','LONG'),
            float(_result.get('score_final') or 0),
            _result.get('regime','UNKNOWN'),
        )
        if _tf_sig and _tf_sig.get('available'):
            _result['tradfi_signal'] = _tf_sig
            # Phase A: 仅标签，不修改score
    except Exception:
        pass
    # C4: tradfi_dump_detector — TradFi/美股代币放量抛售检测
    # [接入位置 2026-08-29 苏摩111] 建了未接入，今日修复
    try:
        from brahma_brain.tradfi_dump_detector import analyze_tradfi_dump as _td_fn, is_tradfi_token as _is_tf
        if _is_tf(symbol):
            _kl1h = extra_data.get('klines_1h') or extra_data.get('kl1h', [])
            _ret30 = float(extra_data.get('ret_30d', 0) or 0)
            _td = _td_fn(
                symbol=symbol,
                klines_1h=_kl1h[-40:] if _kl1h else [],
                direction=_result.get('signal_dir', 'LONG'),
                ret_30d=_ret30,
                price_chg_24h=float(extra_data.get('price_chg_24h', 0) or 0),
                rsi_1h=float(ms.get('rsi1h', 50) or 50),
            )
            if _td and _td.get('score_delta', 0) != 0:
                _td_delta = int(_td['score_delta'])
                _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _td_delta, 1)
                _result['score'] = _result['score_final']
                _result.setdefault('breakdown_extra', {})['tradfi_dump'] = _td.get('summary_label', f'TradFiDump {_td_delta:+d}')
            if _td:
                _result['_tradfi_dump'] = _td
    except Exception:
        pass
    # ══ [END C类孤岛模块接入] ══════════════════════════════════════════════════

    # C5: market_quadrant — 四象限市场状态评分
    # [P0接入 2026-08-29 苏摩111] 接入位置: brahma_core block_b C5
    # 铁证: LSR>65%+大户净空 = 多头拥挤象限Q2 → score-15; LSR<35%+大户净多 = 空头拥挤Q4 → score+12
    try:
        from brahma_brain.market_quadrant import get_quadrant as _mq_fn
        _mq = _mq_fn(symbol)
        if _mq and isinstance(_mq, dict):
            _mq_quadrant = _mq.get('quadrant', 'NEUTRAL')
            _mq_signal   = _mq.get('signal', 'NEUTRAL')
            _mq_lsr      = float(_mq.get('lsr', 50) or 50)
            _mq_dir      = _result.get('signal_dir', 'LONG')
            _mq_delta = 0
            # 多头拥挤象限(Q1/Q2): LSR>65% → 做多降权-15, 做空加权+12
            if _mq_quadrant in ('Q1', 'Q2') or _mq_lsr > 65:
                if _mq_dir == 'LONG':  _mq_delta = -15
                else:                   _mq_delta = +12
            # 空头拥挤象限(Q3/Q4): LSR<35% → 做空降权-15, 做多加权+12
            elif _mq_quadrant in ('Q3', 'Q4') or _mq_lsr < 35:
                if _mq_dir == 'SHORT': _mq_delta = -15
                else:                   _mq_delta = +12
            if _mq_delta != 0:
                _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _mq_delta, 1)
                _result['score'] = _result['score_final']
                _result.setdefault('breakdown_extra', {})['market_quadrant'] = f'{_mq_delta:+d}({_mq_quadrant} LSR={_mq_lsr:.0f}%)'
            _result['_market_quadrant'] = _mq
            _result['market_quadrant_label'] = _mq_quadrant
    except Exception:
        pass

    # ══ [P0 设计院封印 2026-08-11 苏摩111] TRADFI交易时段门控 ══════════════
    # 美股代币非交易时段(亚洲白天)流动性极低，发信号有执行风险
    # UTC 13:30~20:00 = 北京21:30~04:00 = 美股正常交易时段
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK':
            import datetime as _dt_trd
            _utc_now = _dt_trd.datetime.utcnow()
            _tot_min = _utc_now.hour * 60 + _utc_now.minute
            # 美股交易时段: UTC 13:30(810min) ~ 20:00(1200min)
            _in_us_session = (810 <= _tot_min <= 1200)
            _result['tradfi_in_session'] = _in_us_session
            if not _in_us_session:
                # 非交易时段：score降60分，valid强制False，注入原因
                _old_score = float(_result.get('score_final') or 0)
                _result['score_final'] = _old_score - 60
                _result['score']       = _result['score_final']
                _result['valid']       = False
                _result.setdefault('breakdown_extra', {})['tradfi_off_hours'] = -60
                _result['tradfi_session_warn'] = (
                    f'非交易时段(UTC {_utc_now.hour:02d}:{_utc_now.minute:02d})'
                    f' score-60={_result["score_final"]:.1f} valid=False'
                )
    except Exception:
        pass
    # ══ [TRADFI交易时段门控 END] ═══════════════════════════════════════════════

    # ══ [设计院 2026-08-11 苏摩111] TRADFI整体落地：sector_corr + macro_link ══
    # 仅在交易时段内（valid未被时段门控清除）才执行联动/宏观门控
    # 避免非交易时段已valid=False时继续消耗计算资源
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK' and _result.get('tradfi_in_session', True):
            _direction = _result.get('direction', 'LONG')

            # ── sector_corr：板块联动评分 ─────────────────────────────────────
            from brahma_brain.tradfi_sector_engine import (
                compute_tradfi_sector_score as _sector_fn,
                get_quick_rsi_1h as _sector_rsi_fn,
            )
            _sector_result = _sector_fn(_sym, _direction, _sector_rsi_fn)
            _sector_score  = float(_sector_result.get('score', 0))
            if _sector_score != 0:
                _result['score_final'] = float(_result.get('score_final') or 0) + _sector_score
                _result['score']       = _result['score_final']
                _result.setdefault('breakdown_extra', {})['sector_corr'] = _sector_score
            _result['tradfi_sector'] = _sector_result

            # ── macro_link：宏观门控 ───────────────────────────────────────────
            from brahma_brain.tradfi_macro_gate import compute_tradfi_macro_gate as _macro_fn
            _macro_result = _macro_fn(_sym, _direction, 'TRADFI_STOCK')
            _macro_score  = float(_macro_result.get('score', 0))
            if _macro_score != 0:
                _result['score_final'] = float(_result.get('score_final') or 0) + _macro_score
                _result['score']       = _result['score_final']
                _result.setdefault('breakdown_extra', {})['macro_link'] = _macro_score
                # 宏观重大利空时（总扣分≥30）强制降低valid门槛
                if _macro_score <= -30:
                    _result['valid'] = False
                    _result['macro_gate_warn'] = _macro_result.get('detail', '')
            _result['tradfi_macro'] = _macro_result
    except Exception as _te:
        import logging as _tlog
        _tlog.getLogger(__name__).warning(f'TRADFI联动门控异常: {_te}')
    # ══ [TRADFI整体落地 END] ══════════════════════════════════════════════════

    # ══ [设计院封印 2026-08-14 苏摩111] TradFi三类路由器接入 ═════════════════
    # 验证铁证: A类 WR+9.1pp PNL-3.3%→+12.5% | 铁律1/2/3差异化评分
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK':
            from brahma_brain.tradfi_router import compute_router_delta as _tr_fn
            from brahma_brain.tradfi_router import get_tradfi_report_header as _tr_hdr_fn
            # 提取当前分析结果中的技术指标
            _tr_atr_pct   = float((_result.get('momentum') or {}).get('atr_1h') or 0) / float(_result.get('price', 1) or 1)
            _tr_spx_chg   = float((_result.get('tradfi_macro') or {}).get('spx_chg_1d', 0) or 0)
            _tr_btc_chg   = float((_result.get('momentum') or {}).get('btc_chg_4h', 0) or 0)
            _tr_lsr_long  = float((_result.get('sentiment') or {}).get('lsr_long', 0.5) or 0.5)
            _tr_fr        = float((_result.get('sentiment') or {}).get('fr', 0) or 0)
            _tr_score_now = float(_result.get('score_final') or 0)
            _tr_direction = _result.get('signal_dir') or _result.get('direction', 'LONG')
            _tr_out = _tr_fn(
                symbol      = _sym,
                direction   = _tr_direction,
                base_score  = _tr_score_now,
                atr_pct     = _tr_atr_pct,
                spx_chg_1d  = _tr_spx_chg,
                btc_chg_4h  = _tr_btc_chg,
                lsr_long    = _tr_lsr_long,
                fr          = _tr_fr,
            )
            # 注入路由器结果
            _result['tradfi_router'] = _tr_out
            _tr_delta = _tr_out.get('delta', 0)
            if _tr_delta != 0:
                _result['score_final'] = _tr_score_now + _tr_delta
                _result['score']       = _result['score_final']
                _result.setdefault('breakdown_extra', {})['tradfi_router_delta'] = _tr_delta
            # STANDBY/WATCH 处理
            if _tr_out.get('standby'):
                _result['valid'] = False
                _result.setdefault('breakdown_extra', {})['tradfi_router_standby'] = True
            if _tr_out.get('watch'):
                _result.setdefault('breakdown_extra', {})['tradfi_router_watch'] = True
            # 报告头部标注（供formatter使用）
            _tr_header = _tr_hdr_fn(_sym)
            if _tr_header:
                _result['tradfi_report_header'] = _tr_header
    except Exception as _tr_e:
        import logging as _tr_log
        _tr_log.getLogger(__name__).warning(f'tradfi_router接入异常: {_tr_e}')
    # ══ [TradFi三类路由器 END] ════════════════════════════════════════════════

    # [设计院封印 2026-08-09] 修复F12: analyze()结束时写入structured日志
    # 保证 brahma360 F12检查不再告警「SMC结构过旧」
    try:
        import json as _jsl2, time as _tsl2
        from pathlib import Path as _Psl2
        _sl2_path = _Psl2(__file__).parent.parent / 'data' / 'brahma_structured.jsonl'
        _cf2 = _result.get('confluence', {}) or {}
        _bd2 = _cf2.get('breakdown', {}) or {}
        _sl2_regime = _result.get('regime', '')
        _sl2_dir    = _result.get('signal_dir', _result.get('direction', ''))
        _sl2_score  = float(_result.get('score_final', _result.get('score', 0)) or 0)
        _sl2_entry = {
            'ts':        _tsl2.time(),
            'iso':       __import__('datetime').datetime.utcnow().isoformat() + 'Z',
            'level':     'SIGNAL',
            'module':    'brahma_core',
            'event':     'analysis_complete',
            'symbol':    _result.get('symbol', _sym),
            'score':     _sl2_score,
            # [BUG修复 2026-08-11 设计院] 顶层直接存储regime/direction，不嵌套在metrics里
            'regime':    _sl2_regime,
            'direction': _sl2_dir,
            'metrics': {
                'ob_score':        float(_bd2.get('OB结构', _bd2.get('ob_score', 0)) or 0),
                'fvg_score':       float(_bd2.get('FVG', _bd2.get('fvg_score', 0)) or 0),
                'structure_score': float(_bd2.get('SMC结构', _bd2.get('structure_score', 0)) or 0),
                'score':           _sl2_score,
                'regime':          _sl2_regime,
                'direction':       _sl2_dir,
            }
        }
        with open(_sl2_path, 'a', encoding='utf-8') as _slf2:
            _slf2.write(_jsl2.dumps(_sl2_entry, ensure_ascii=False) + '\n')
    except Exception:
        pass

    # ── [梦天大脑 Layer A2+C3 注入 2026-08-25] ──────────────────────────────────
    # A2: 极端事件库风险注释
    try:
        from extreme_event_db import get_extreme_risk_note as _ern
        _extreme_note = _ern(_sym)
        if _extreme_note:
            _result['extreme_risk_note'] = _extreme_note
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['extreme_event'] = _extreme_note
    except Exception:
        pass

    # C3: 反脆弱性黑天鹅检测
    try:
        from antifragile_guard import full_guard_check as _fgc
        _guard = _fgc(_sym, _result.get('signal_dir', signal_dir or ''))
        _result['antifragile'] = _guard
        if _guard['warnings']:
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['antifragile'] = ' | '.join(_guard['warnings'][:2])
        if _guard['blocked']:
            _result['decision_action'] = 'BLOCKED_GUARD'
            _result['decision_reason'] = f'[反脆弱性熔断] {_guard["warnings"][0] if _guard["warnings"] else "保护熔断"}'
    except Exception:
        pass
    # ── [END 梦天大脑注入] ────────────────────────────────────────────────────────────

    # [2026-08-25 fix P3] direction字段映射 signal_dir → direction，供AI议会/外部调用
    if _result.get('direction') is None:
        _result['direction'] = _result.get('signal_dir') or signal_dir or None

    # ── [N_SW] signal_weight_updater 动态乘数层 [2026-08-29 苏摩111] ─────────────
    # 职责: 读取实战结算后的动态WR权重，对score_final做乘数修正
    # 接入位置: return前最后一步（所有其他评分已完成后）
    # 铁律: STATIC_LOCK条目不可被动态覆盖
    try:
        import json as _jsw
        from pathlib import Path as _Psw
        _sw_path = _Psw(__file__).parent.parent / 'data' / 'signal_weights.json'
        if _sw_path.exists():
            _sw_data    = _jsw.loads(_sw_path.read_text())
            _sw_weights = _sw_data.get('weights', {})
            _sw_regime  = _result.get('regime', '')
            _sw_dir     = _result.get('signal_dir', _result.get('direction', ''))
            _sw_score   = float(_result.get('score_final', 0) or 0)
            # 生成分段key: REGIME:DIR:TIER
            def _sw_tier(s):
                if s >= 165: return '165+'
                if s >= 155: return '155-164'
                if s >= 140: return '140-154'
                if s >= 120: return '120-139'
                return 'sub120'
            _sw_key = f"{_sw_regime}:{_sw_dir}:{_sw_tier(_sw_score)}"
            _sw_key2 = f"{_sw_regime}:{_sw_dir}"  # 无tier fallback
            _sw_entry = _sw_weights.get(_sw_key) or _sw_weights.get(_sw_key2, {})
            _sw_mult  = float(_sw_entry.get('multiplier', 1.0) or 1.0)
            # 乘数有效范围 0.3~1.5，避免极端放大
            _sw_mult = max(0.3, min(1.5, _sw_mult))
            if _sw_mult != 1.0 and _sw_score != 0:
                _sw_old = _sw_score
                _sw_new = round(_sw_score * _sw_mult, 1)
                _result['score_final'] = _sw_new
                _result['score']       = _sw_new
                _result.setdefault('confluence', {}).setdefault('breakdown', {})['SW动态权重'] = (
                    f'{_sw_mult:.2f}x({_sw_key} n={_sw_entry.get("n","?")} WR={_sw_entry.get("wr","?")})'
                    f' {_sw_old:.1f}→{_sw_new:.1f}'
                )
    except Exception:
        pass
    # ── [END N_SW] ──────────────────────────────────────────────────────────────

    return _result

def format_report(r: dict) -> str:
    """[shim] 已迁移到 brahma_brain/formatter.py · v25.0"""
    from brahma_brain.formatter import format_report as _fmt
    return _fmt(r)
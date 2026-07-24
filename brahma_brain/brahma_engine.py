# brahma_engine.py · 梵天主分析引擎
# 从 brahma_core.py 拆分 · 2026-07-12 设计院6方联合封印
# 职责: analyze() 主入口 (原行2066~5175)
# 依赖: brahma_scoring.confluence_score + 自包含完整依赖声明
# 架构: 零循环依赖，独立可运行

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


# ─── 从brahma_scoring导入评分函数 ──────────────────────────────────
from brahma_brain.brahma_scoring import confluence_score, _apply_calib
from brahma_brain.grade_utils import parse_grade, grade_to_label  # [P1 2026-07-23 设计院] 统一grade解析入口接入主链路

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
            _lpf_sys.path.insert(0, _lpf_base)
        from live_price_feed import bulk_update_from_api as _lpf_bulk
        _lpf_bulk([_sym])
        pass  # [静默] f'[PriceFix] {_sym} 入口强制刷新价格 ✅'
    except Exception as _lpf_e:
        pass  # [静默] f'[PriceFix] 价格刷新异常（不阻断）: {_lpf_e}'

    # Step 1: 市场状态分析
    ms = ms_analyze(symbol)
    if 'error' in ms:
        pass  # [静默] f'[BrahmaBrain] ✗ {_sym} ms_analyze失败: {ms["error"]}'
        return {'error': ms['error']}

    # ── [设计院 2026-06-30 P0-A] RegimeStateMachine 体制防抖接入 ────────
    # 根因：brahma_core直接消费ms_analyze()原始体制输出，单根4H K棒噪声即触发切换
    # 修复：经过确认窗口(2~3根4H)+滞后保护+状态持久化，过滤伪切换
    # fail-safe：异常时不阻断主流程，维持原始体制
    try:
        import sys as _rsm_sys, os as _rsm_os
        _rsm_path = _rsm_os.path.dirname(_rsm_os.path.abspath(__file__))
        if _rsm_path not in _rsm_sys.path:
            _rsm_sys.path.insert(0, _rsm_path)
        from regime_state_machine import RegimeStateMachine
        _rsm = RegimeStateMachine(_sym)
        _raw_regime = ms.get('regime', 'CHOP_MID')
        _stable_regime = _rsm.update(_raw_regime)
        if _stable_regime != _raw_regime:
            pass  # [静默] f'[RSM] {_sym} 体制防抖: {_raw_regime}→{_stable_regime}（状态机稳定输出，已过滤伪切换）'
        else:
            pass  # [静默] f'[RSM] {_sym} 体制稳定: {_stable_regime}（无切换）'
        ms['regime'] = _stable_regime
    except Exception as _rsm_e:
        pass  # [静默] f'[RSM] 状态机异常（不阻断，维持原始体制）: {_rsm_e}'
    # ── [P0-A END] ────────────────────────────────────────────────────────

    # ══ [D: BEAR_RECOVERY_TRANSITION 转势前瞻探测器 2026-07-20 苏摩111批准] ══
    # 解决问题：阶段4(2023复苏) α=-154%，根因是EMA200滞后15-20日，错过复苏初期
    # 逻辑：不修改体制判定，在RSM稳定输出后叠加一个"前瞻子标签"
    #   条件1: 当前体制为 BEAR_TREND 或 BEAR_EARLY
    #   条件2: 价格持续 >= 5 个4H K线在 EMA200 上方（EMA200首次被站上）
    #   条件3: 4H EMA50 上穿 EMA200 临界点（距离 < 3%）
    #   效果: regime 保持不变，但 ms 增加 _transition_hint = 'BEAR_RECOVERY_TRANSITION'
    #          position_sizer 读取此 hint，将做多乘数从 0.10x 升至 0.35x（探索仓）
    try:
        _tr_regime = ms.get('regime', '')
        if _tr_regime in ('BEAR_TREND', 'BEAR_EARLY'):
            _tr_closes = ms.get('closes_4h', [])
            _tr_ema200_4h = ms.get('ema200_4h', 0)
            _tr_ema50_4h = ms.get('ema50_4h', 0)
            if _tr_closes and _tr_ema200_4h > 0 and len(_tr_closes) >= 5:
                # 条件2: 最近5根4H收盘价全在EMA200上方
                _above_ema200 = all(c > _tr_ema200_4h for c in _tr_closes[-5:])
                # 条件3: EMA50接近EMA200（差距 < 3%，即将上穿）
                _ema_gap_pct = abs(_tr_ema50_4h - _tr_ema200_4h) / _tr_ema200_4h * 100 if _tr_ema200_4h > 0 else 99
                _approaching = _ema_gap_pct < 3.0 and _tr_ema50_4h < _tr_ema200_4h  # EMA50仍在200下但接近
                if _above_ema200 and _approaching:
                    ms['_transition_hint'] = 'BEAR_RECOVERY_TRANSITION'
                    ms['_transition_ema_gap'] = round(_ema_gap_pct, 2)
            # 条件B: EMA50已上穿EMA200但RSM还没确认BEAR_RECOVERY（滞后期）
            elif _tr_ema50_4h > _tr_ema200_4h > 0:
                _cross_pct = (_tr_ema50_4h - _tr_ema200_4h) / _tr_ema200_4h * 100
                if _cross_pct < 2.0:  # 刚刚上穿（<2%），RSM尚未确认
                    ms['_transition_hint'] = 'BEAR_RECOVERY_TRANSITION'
                    ms['_transition_ema_gap'] = round(_cross_pct, 2)
    except Exception:
        pass  # 降级，不影响主流程
    # ══ [END BEAR_RECOVERY_TRANSITION] ══════════════════════════════════════

    # ── [因果AI P0-A] Causal Regime Verifier ────────────────────
    # 设计院因果增强 v1.0 · 2026-06-18
    # 在 Step 2 方向确认前，验证当前体制的因果结构是否支持入场
    # fail-safe: 异常时返回默认通过，不阻断主流程
    _causal_v_result = {}
    try:
        import sys as _cv_sys, os as _cv_os
        _cv_root = _cv_os.path.dirname(_cv_os.path.abspath(__file__))
        if _cv_root not in _cv_sys.path:
            _cv_sys.path.insert(0, _cv_root)
        from causal_regime_verifier import verify as _cv_verify
        _cv_regime = ms.get('regime', '?')
        _cv_dir = signal_dir or ms.get('signal_bias', 'SHORT')
        _causal_v_result = _cv_verify(_sym, _cv_regime, _cv_dir, ms, timeout_ms=150)
        _cv_adj = _causal_v_result.get('score_adj', 0)
        _cv_verdict = _causal_v_result.get('verdict', '?')
        if _cv_verdict not in ('STRONG', 'MODERATE'):
            pass  # [静默] f'[CausalVerifier] ⚡ {_sym} verdict={_cv_verdict} conf={_causal_v_result.get("ca
        pass  # extra_data5c1a672a521d59cb5316Ff0c7ed3679c5b585728_causal_v_result4e2d
    except Exception as _cv_e:
        pass  # [静默] f'[CausalVerifier] ⚠ 异常（不阻断）: {_cv_e}'

    # Step 2: 确定方向
    if signal_dir is None:
        signal_dir = ms['signal_bias']

    # ── [HARD_BLOCK END] ──────────────────────────────────────────────────
    _rcn = {'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_PEAK':'牛市末期','BULL_CORRECTION':'牛市回调','BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期','BEAR_CRASH':'暴跌体制','BEAR_RECOVERY':'熊市反弹','CHOP_HIGH':'高位震荡','CHOP_LOW':'低位震荡','CHOP_MID':'中位震荡','BREAKOUT':'突破体制'}
    _reg_raw = ms.get('regime','?')
    _reg_display = f'{_reg_raw}({_rcn.get(_reg_raw,_reg_raw)})'
    pass  # [静默] f'[BrahmaBrain] {_sym} 体制={_reg_display} 方向={signal_dir} RSI_1H={ms.get("momentu
    if signal_dir == 'NEUTRAL':
        pass  # [静默] f'[BrahmaBrain] {_sym} 方向中性，不入场'
        if not deep:
            return {
                'symbol': symbol,
                'signal_dir': 'NEUTRAL',
                'action': '不入场',
                'reason': '三框架方向中性，无共识',
                'summary': ms['summary'],
            }
        # [deep=True] 中性体制下仍继续运行，选择体制最优方向
        from brahma_brain.regime_scorer import score as _rs_fn, _CACHE as _RS_CACHE
        _RS_CACHE.clear()
        _live_reg = _rs_fn(symbol, force=True)
        _live_regime = _live_reg.get('regime','')
        _bear_p = _live_reg.get('bear_prob',0)
        _bull_p = _live_reg.get('bull_prob',0)
        if _bear_p >= _bull_p:
            signal_dir = 'SHORT'
        else:
            signal_dir = 'LONG'
        pass  # [静默] f'[BrahmaBrain][deep] {_sym} 深度模式强制方向={signal_dir}（bear={_bear_p:.1%} bull={_bul

    # Step 3: SMC结构分析
    price = float(ms.get('price', 0))  # [v21.0 fix] MTF路由器需要price变量
    smc = analyze_smc(symbol, signal_dir, '1h', 200)
    # [v21.0 自顶向下 2026-06-08] 补充4H SMC分析 + MTF路由器（自顶向下）
    _smc_4h = {}
    _mtf_result = None  # multi_timeframe_router结果
    try:
        _smc_4h = analyze_smc(symbol, signal_dir, '4h', 60)
        # [v21.0] MTF路由：4H战略区优先，1H确认（自顶向下）
        try:
            from brahma_brain.multi_timeframe_router import route_entry_zone as _mtf_route
            _mtf_result = _mtf_route(symbol, signal_dir, price, smc, _smc_4h)
            _tf_used = _mtf_result.get('timeframe', '1H')
            _tf_warn = _mtf_result.get('warning', '')
            _tf_upgrade = _mtf_result.get('upgrade_reason', '')
            if _tf_used == '4H':
                _mtf_lo = _mtf_result['entry_lo']
                _mtf_hi = _mtf_result['entry_hi']
                if _tf_warn:
                    pass  # [静默]
        except Exception as _mtf_err:
            _mtf_result = None

        # [旧逻辑兼容] 如果MTF路由未激活，保留原1H→4H降级逻辑
        if _mtf_result is None or _mtf_result.get('timeframe') == '1H':
            if not smc.get('fvg', {}).get('nearest_bear') and not smc.get('order_blocks', {}).get('nearest_bear_ob'):
                _ob4h = _smc_4h.get('order_blocks', {}).get('nearest_bear_ob')
                _fvg4h = _smc_4h.get('fvg', {}).get('nearest_bear')
                if _ob4h:
                    smc['order_blocks']['nearest_bear_ob'] = _ob4h
                    pass  # [静默] f'[SMC-4H] {symbol} 1H无OB，使用4H Bear OB [{_ob4h.get("low",0):.4g}~{_ob4h.get("hig
                if _fvg4h:
                    smc['fvg']['nearest_bear'] = _fvg4h
                    pass  # [静默] f'[SMC-4H] {symbol} 1H无FVG，使用4H Bear FVG'
            if not smc.get('fvg', {}).get('nearest_bull') and not smc.get('order_blocks', {}).get('nearest_bull_ob'):
                _ob4h_bull = _smc_4h.get('order_blocks', {}).get('nearest_bull_ob')
                _fvg4h_bull = _smc_4h.get('fvg', {}).get('nearest_bull')
                if _ob4h_bull:
                    smc['order_blocks']['nearest_bull_ob'] = _ob4h_bull
                if _fvg4h_bull:
                    smc['fvg']['nearest_bull'] = _fvg4h_bull
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # ══════════════════════════════════════════════════════════
    # [达摩院v12.9c 修订 设计院 2026-05-30] FVG 条件升级
    # 原逻辑：无FVG → 硬拒绝（导致OB/Fib降级路径被绕过）
    # 新逻辑：无「有效方向FVG（gap>0.3%且在正确方向）」→ 降级，不硬拒绝
    #         但无FVG且无OB且Fib也不在价格外侧合理距离 → 才硬拒绝（真正无结构）
    # 修复原因：FVG阈值从0.1%提升到0.3%后，很多情况nearest_bear=None
    #           但OB/Fib降级是合法的入场参数来源，不应拒绝
    # ══════════════════════════════════════════════════════════
    _fvg_hard = smc.get('fvg', {})
    _fvg_exists = (
        (_fvg_hard.get('nearest_bull') is not None) if signal_dir == 'LONG'
        else (_fvg_hard.get('nearest_bear') is not None)
    )
    _ob_hard = smc.get('order_blocks', {})
    _ob_exists = (
        (_ob_hard.get('nearest_bull_ob') is not None) if signal_dir == 'LONG'
        else (_ob_hard.get('nearest_bear_ob') is not None)
    )
    # 只有FVG和OB都没有才是真正「无结构」，Fib始终存在所以允许降级
    if not _fvg_exists and not _ob_exists:
        pass  # [静默] f'[BrahmaBrain] ⚠️ FVG/OB均无 {_sym}: {signal_dir}方向无SMC结构 → 降级用Fib入场'
        # 不拒绝，继续走Fib降级路径（calc_trade_params会处理）
    elif not _fvg_exists:
        pass  # [静默] f'[BrahmaBrain] ℹ️ {_sym} 无有效FVG，使用OB入场'

    # Step 4: Phase 2 额外引擎
    k1h = klines_to_ohlcv(get_klines(symbol, '1h', 200))
    k4h = klines_to_ohlcv(get_klines(symbol, '4h', 200))
    extra_data = {
        '_symbol': _sym,
        'price': price,  # [2026-07-06] s7-LiqDens需要price字段
        '_k4h_closes':  list(k4h['c'][-20:]) if k4h and k4h.get('c') else [],
        '_k4h_volumes': list(k4h['v'][-20:]) if k4h and k4h.get('v') else [],
        '_klines_1h':   k1h,  # [v25.1 2026-06-14] s20/s21/s22初始化即提前注入，避免流程中断导致三个维度全部归零
        '_k1h_raw':     get_klines(symbol, '1h', 50),  # [s8b-VolSkew 2026-07-08] 注入原始1H K线供成交量偏度计算
    }
    # Bug1修复(2026-06-26): CausalVerifier在extra_data初始化前调用，现在补写
    if _causal_v_result:
        extra_data['causal_verifier'] = _causal_v_result
    # ── [UP-017 2026-05-22] CoinGlass 链上数据接入 ───────────────
    try:
        import sys as _sys_cg, os as _os_cg
        _root_cg = _os_cg.path.dirname(_os_cg.path.dirname(_os_cg.path.abspath(__file__)))
        _bb_dir  = _os_cg.path.dirname(_os_cg.path.abspath(__file__))
        for _p in [_root_cg, _bb_dir]:
            if _p not in _sys_cg.path: _sys_cg.path.insert(0, _p)
        import coinglass_engine as _cg
        _cg_snap = _cg.get_full_snapshot(_sym)
        # [设计院 2026-05-30] CoinGlass失效时自动降级
        if not _cg_snap or not _cg_snap.get('available'):
            raise Exception('CoinGlass不可用，触发降级链')
        extra_data['coinglass'] = _cg_snap
        extra_data['fear_greed'] = _cg_snap['fear_greed']
        extra_data['onchain_score'] = _cg_snap['onchain_score']
    except Exception as _cg_e:
        # [设计院 2026-05-30] 降级链：尝试备用数据源
        try:
            from coinglass_fallback import get_full_snapshot_with_fallback as _cg_fb
            _cg_snap_fb = _cg_fb(_sym)
            extra_data['coinglass']     = _cg_snap_fb
            extra_data['fear_greed']    = _cg_snap_fb['fear_greed']
            extra_data['onchain_score'] = _cg_snap_fb.get('onchain_score', 0)
            _src = _cg_snap_fb['fear_greed'].get('source','?')
            pass  # [静默] f'[BrahmaBrain] CoinGlass降级[{_src}]: F&G={_cg_snap_fb["fear_greed"]["value"]} FR
        except Exception as _fb_e:
            pass  # [静默] f'[BrahmaBrain] CoinGlass+降级均失败: {_cg_e}'
    # ── liq_scanner 补充清算数据（Binance公开接口，无需Coinglass Key）────
    try:
        from liq_scanner import get_liq_snapshot
        _liq_snap = get_liq_snapshot(_sym)
        if not extra_data.get('coinglass'):
            extra_data['coinglass'] = {}
        _cg_liq = extra_data['coinglass'].get('liquidation', {})
        if not _cg_liq.get('available'):
            # Coinglass失效时用liq_scanner补充
            extra_data['coinglass']['liquidation'] = {
                'long_liq':  _liq_snap.get('cg_long_liq_m', 0) or 0,
                'short_liq': _liq_snap.get('cg_short_liq_m', 0) or 0,
                'liq_ratio': 1.0,
                'bias':      _liq_snap.get('liq_bias', 'NEUTRAL'),
                'available': True,
            }
        # 始终补充Binance公开数据字段
        extra_data['liq_snap'] = _liq_snap
        pass  # [静默] f'[BrahmaBrain] LiqScan: 散户多{_liq_snap["long_pct"]:.0f}% 大户多{_liq_snap["top_long
    except Exception as _liq_e:
        pass  # [静默] f'[BrahmaBrain] LiqScan跳过: {_liq_e}'
    # ─────────────────────────────────────────────────────────────
    try:
        # 达摩院 v3 升级：传入 volumes + regime + 当前时间戳
        import time as _time_m
        _cur_ts_ms = int(_time_m.time() * 1000)
        _regime_str = ms.get('regime', '') if ms else ''
        div_1h = divergence_score(
            k1h['o'], k1h['h'], k1h['l'], k1h['c'], signal_dir, '1H',
            volumes=list(k1h['v']), regime=_regime_str, ts_ms=_cur_ts_ms
        )
        div_4h = divergence_score(
            k4h['o'], k4h['h'], k4h['l'], k4h['c'], signal_dir, '4H',
            volumes=list(k4h['v']), regime=_regime_str, ts_ms=_cur_ts_ms
        )
        # v3: 直接用 score 字段（已含所有修正）
        s_1h = div_1h['score']
        s_4h = div_4h['score']
        best  = div_4h if s_4h >= s_1h else div_1h
        best_s = max(s_1h, s_4h)
        extra_data['divergence'] = {
            'score':        best_s,
            'score_long':   best_s if signal_dir=='LONG' else 0,
            'score_short':  best_s if signal_dir=='SHORT' else 0,
            'details_1h':   div_1h['grade_notes'],
            'details_4h':   div_4h['grade_notes'],
            'rsi_div':      best['rsi_div'],
            'macd_div':     best['macd_div'],
            'macd_zero':    '0轴上方(多头区)' if best['macd_div'].get('zero_cross_up') or
                             (div_4h['macd_div'].get('score_long',0)>0) else '0轴下方(空头区)',
            'vol_1h':       div_1h.get('vol_info', {}),
            'vol_4h':       div_4h.get('vol_info', {}),
            'time_penalty': max(div_1h.get('time_penalty',0), div_4h.get('time_penalty',0)),
            'regime_adj':   max(div_1h.get('regime_penalty',0), div_4h.get('regime_penalty',0)),
        }
        _tp = extra_data['divergence']['time_penalty']
        _rp = extra_data['divergence']['regime_adj']
        _vb = max(div_1h.get('vol_bonus',0), div_4h.get('vol_bonus',0))
        if _tp or _rp or _vb:
            pass  # [静默] f'[D03-v3] 实训修正: 时间惩罚={-_tp} 体制调整={-_rp} 量缩奖励=+{_vb} 最终分={best_s}'
        # [v25.2 2026-06-16 P1] 1H+4H双重背离共振加分
        # 离线铁证: 1H信号WR=58% vs 15M WR=52.8%（+5.2%）
        # 当1H和4H背离评分都有效时（各≥6），双重共振+3分
        if s_1h >= 6 and s_4h >= 6:
            _dual_div_bonus = 3
            extra_data['divergence']['score'] = min(best_s + _dual_div_bonus, 18)
            extra_data['divergence']['score_long'] = min(extra_data['divergence'].get('score_long',0) + _dual_div_bonus, 18) if signal_dir=='LONG' else extra_data['divergence'].get('score_long',0)
            extra_data['divergence']['score_short'] = min(extra_data['divergence'].get('score_short',0) + _dual_div_bonus, 18) if signal_dir=='SHORT' else extra_data['divergence'].get('score_short',0)
    except Exception:
        pass
    try:
        vol_res = volume_score(k1h['h'],k1h['l'],k1h['c'],k1h['v'], signal_dir)
        extra_data['volume'] = {'score': vol_res['score'], 'details': vol_res['details']}
    except Exception:
        pass
    try:
        # [Phase2a] 区间结构引擎数据注入
        extra_data['_klines_1h'] = k1h
    except Exception:
        pass
    try:
        # Phase 3: Elliott波浪引擎（已禁用 2026-06-11，模块已清除）
        # analyze_elliott已从 elliott_engine 移除，此处跳过
        pass
    except Exception as _ew_err:
        pass  # 已禁用，无需记录错误
    try:
        sent = sentiment_score(
            symbol, signal_dir,
            ms['sentiment']['funding_rate'],
            ms['sentiment']['long_short_ratio']
        )
        extra_data['sentiment'] = sent
    except Exception:
        pass
    # P1b/P2c/P2d: 链上+订单流+宏观 并发执行（原串行3×~1s → 并发后只需最慢1个）
    from concurrent.futures import ThreadPoolExecutor as _TPE
    _fg_pass = extra_data.get('fear_greed')
    _k1h_ohlcv_pat = klines_to_ohlcv(get_klines(symbol, '1h', 200))

    def _run_onchain():
        if not _ONCHAIN_OK: return None
        return _onchain_score(symbol, signal_dir)

    def _run_pattern():
        if not _PATTERN_OK: return None
        if _k1h_ohlcv_pat and len(_k1h_ohlcv_pat.get('h',[])) >= 20:
            return _pattern_score(_k1h_ohlcv_pat['h'], _k1h_ohlcv_pat['l'], _k1h_ohlcv_pat['c'], signal_dir)
        return None

    def _run_orderflow():
        if not _OF_OK: return None
        return _order_flow_score(symbol, signal_dir)

    def _run_macro():
        if not _MACRO_OK: return None
        return _macro_score(symbol, signal_dir, fg_data=_fg_pass)

    with _TPE(max_workers=4) as _ex:
        _f_oc  = _ex.submit(_run_onchain)
        _f_pt  = _ex.submit(_run_pattern)
        _f_of  = _ex.submit(_run_orderflow)
        _f_mc  = _ex.submit(_run_macro)
        try: extra_data['onchain'] = _f_oc.result(timeout=8)
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        try:
            _pt = _f_pt.result(timeout=8)
            if _pt: extra_data['pattern'] = _pt
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        try:
            _of = _f_of.result(timeout=8)
            if _of: extra_data['order_flow'] = _of
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        try: extra_data['macro'] = _f_mc.result(timeout=8)
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P0-NEW: 谐波形态引擎（4H + 日线双重扫描）
    try:
        if _HARMONIC_OK:
            # 若4H无结果，降级用日线数据扫描
            if not h_res.get('patterns'):
                _k1d = klines_to_ohlcv(get_klines(symbol, '1d', 60))
                if _k1d and len(_k1d.get('h',[])) >= 20:
                    h_res_1d = _harmonic_score(_k1d['h'], _k1d['l'], _k1d['c'], signal_dir)
                    if h_res_1d.get('score', 0) > 0:
                        h_res_1d['timeframe'] = '1d'
                        h_res = h_res_1d
            extra_data['harmonic'] = h_res
            if h_res.get('score', 0) > 0:
                pass  # [静默] f'[HarmonicEngine] {symbol} {signal_dir}: {h_res.get("patterns",[])} score={h_re
    except Exception as _e:
        extra_data['harmonic_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'harmonic','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P0-NEW: 多周期对齐引擎
    try:
        if _MULTITF_OK:
            mt_res = _multitf_score(symbol, signal_dir)
            extra_data['multitf'] = mt_res
    except Exception as _e:
        extra_data['multitf_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'multitf','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P1-NEW: 增强信号引擎（CVD+清算+多空比趋势+时段）
    try:
        if _ENHANCED_OK:
            en_res = _enhanced_score(symbol, signal_dir)
            extra_data['enhanced'] = en_res
    except Exception as _e:
        extra_data['enhanced_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'enhanced','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P2-NEW: 鲸鱼引擎（链上大单+交易所流向）
    try:
        if _WHALE_OK:
            wh_res = _whale_score(symbol, signal_dir)
            extra_data['whale'] = wh_res
    except Exception as _e:
        extra_data['whale_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'whale','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P2-NEW: 跨市场引擎（BTC-ETH相关/DXY/风险偏好）
    try:
        if _CROSS_OK:
            cx_res = _cross_market_score(symbol, signal_dir)
            extra_data['cross_market'] = cx_res
    except Exception as _e:
        extra_data['cross_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'cross','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # [s_cross 2026-07-01] 跨所FR+Basis（设计院三项外部路由落地）
    try:
        from cross_market_engine import get_cross_fr_basis as _get_cfb
        _cfb = _get_cfb(symbol)
        extra_data['cross_fr_basis'] = _cfb
        if _cfb.get('score_adj', 0) != 0:
            pass  # [静默]
    except Exception:
        pass

    # [s_options 2026-07-01] Deribit P/C OI
    try:
        from cross_market_engine import get_deribit_pc as _get_dpc
        _dpc = _get_dpc(symbol)
        extra_data['deribit_pc'] = _dpc
        if _dpc.get('score_adj', 0) != 0:
            pass  # [静默]
    except Exception:
        pass

    # [s_macro_v2 2026-07-01] DXY实时+纳指+BTC.D精准加权
    try:
        from macro_engine import macro_score_v2 as _macro_v2
        _mv2 = _macro_v2(symbol, signal_dir)
        extra_data['macro_v2'] = _mv2
        if _mv2.get('score_addon', 0) != 0:
            for _mn in _mv2.get('notes', []):
                print(f'[s_macro_v2] {symbol} {signal_dir}: {_mn}')
    except Exception:
        pass

    # [v5.6 设计院封印 2026-07-13] 拡展能力三项集成（路径修复 2026-07-13）
    # 修复: from scripts.xxx 在 brahma_brain/ 运行环境下不可用
    # 改为: 动态注入 scripts/ 目录后再导入
    _scripts_v56 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
    if _scripts_v56 not in sys.path:
        sys.path.insert(0, _scripts_v56)
    # P0: 清算热力图
    try:
        from liq_heatmap import get_liq_heatmap as _liq_hm
        _lhm = _liq_hm(symbol)
        extra_data['liq_heatmap'] = _lhm
    except Exception:
        pass
    # P1b: 鲸鱼监控
    try:
        from whale_monitor import get_whale_signal as _whale_sig
        _wh = _whale_sig(symbol)
        extra_data['whale_v2'] = _wh
    except Exception:
        pass
    # P1c: 期权P/C比（设计院 2026-07-13 补充集成）
    try:
        from options_pc_ratio import get_options_pc as _get_pc_v56
        _cur_v56 = 'BTC' if 'BTC' in symbol else ('ETH' if 'ETH' in symbol else symbol.replace('USDT',''))
        _pc_v56 = _get_pc_v56(_cur_v56)
        extra_data['options_pc'] = _pc_v56
    except Exception:
        pass
    # P2: 矿工卖压
    try:
        if symbol.startswith('BTC'):
            from miner_pressure import get_miner_pressure as _mp
            extra_data['miner_pressure'] = _mp()
    except Exception:
        pass

    # P2-NEW: 微观结构引擎（大单吸收/耗尽/停顿）
    try:
        if _MICRO_OK:
            ms_res = _micro_score(symbol, signal_dir)
            extra_data['microstructure'] = ms_res
    except Exception as _e:
        extra_data['micro_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'micro','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # ─── Phase NEW: 量能衰竭 + 多周期背离共振 ────────────────────────
    # VOL-EXH: 量能衰竭引擎（底部识别核心）
    try:
        if _VOL_EXH_OK and k1h and len(k1h.get('c',[])) >= 20:
            _v_res = _vol_exh_score(
                k1h['h'], k1h['l'], k1h.get('o', k1h['c']),
                k1h['c'], k1h.get('v', []), signal_dir
            )
            extra_data['vol_exhaustion'] = _v_res
            if _v_res.get('score', 0) > 0:
                pass  # [静默] f'[VolExh] {symbol} {signal_dir}: {_v_res["exhaustion_level"]} score={_v_res["sc
    except Exception as _e:
        extra_data['vol_exh_err'] = str(_e)[:80]

    # MULTITF-DIV: 多周期背离共振引擎
    try:
        if _MULTITF_DIV_OK:
            _md_res = _multitf_div_score(symbol, signal_dir)
            extra_data['multitf_div'] = _md_res
            if _md_res.get('resonance', 'NONE') not in ('NONE',):
                pass  # [静默] f'[MultiTFDiv] {symbol} {signal_dir}: {_md_res["resonance"]} score={_md_res["sco
    except Exception as _e:
        extra_data['multitf_div_err'] = str(_e)[:80]

    # ─── Phase A: 新引擎接入 ─────────────────────────────────────────
    # A1: L2订单簿深度
    try:
        import sys as _sys_ob, os as _os_ob
        _bd = _os_ob.path.join(_os_ob.path.dirname(_os_ob.path.abspath(__file__)))
        if _bd not in _sys_ob.path: _sys_ob.path.insert(0, _bd)
        from orderbook_engine import analyze_orderbook as _ob_fn
        # [潜力释放 P1 2026-07-12] 实际调用 analyze_orderbook 并写入 extra_data
        # 根因：之前只 import 不调用，l2贝叶斯永远拿不到 orderbook 数据
        _ob_result = _ob_fn(symbol, signal_dir)
        extra_data['orderbook'] = _ob_result
    except Exception as _e:
        extra_data['orderbook_err'] = str(_e)[:80]

    # A2: 贝叶斯胜率调整（已禁用 2026-06-11，模块已清除）
    # bayesian_updater 已从项目移除，该块跳过
    # extra_data['bayesian'] 将保持空，不影响评分

    # A3: VaR 单仓风险
    try:
        from var_engine import single_position_var as _var_fn
        extra_data['var'] = _var_fn(symbol, 0.05, signal_dir)
    except Exception as _e:
        extra_data['var_err'] = str(_e)[:80]

    # A5: 宏观事件日历
    try:
        from macro_calendar import get_active_risk as _cal_fn
        extra_data['macro_calendar'] = _cal_fn()
    except Exception as _e:
        extra_data['macro_calendar_err'] = str(_e)[:80]

    # A6: 合约基差引擎（合约标记价格 vs 现货指数价格）
    try:
        from data_cache import get_basis as _basis_fn
        extra_data['basis'] = _basis_fn(symbol)
    except Exception as _e:
        extra_data['basis_err'] = str(_e)[:80]

    # A7: ATR历史百分位（波动率体制）
    try:
        from data_cache import get_atr_percentile as _atr_pctile_fn
        extra_data['atr_percentile'] = _atr_pctile_fn(symbol, '1h', 90)
    except Exception as _e:
        extra_data['atr_percentile_err'] = str(_e)[:80]

    # ─── Phase B: ML/滑点/在线学习/链上WS ──────────────────────────
    # B1: XGBoost 信号分类器
    try:
        import sys as _sys_xgb, os as _os_xgb
        _bd = _os_xgb.path.join(_os_xgb.path.dirname(_os_xgb.path.abspath(__file__)))
        if _bd not in _sys_xgb.path: _sys_xgb.path.insert(0, _bd)
# [CLEANED 2026-06-11] from xgboost_engine import predict_win_prob as _xgb_fn
    except Exception as _e:
        extra_data['xgboost_err'] = str(_e)[:80]

    # B2: 在线贝叶斯多维后验（已由brahma_core主流程online_bayes接管，此处跳过）
    # [CLEANED 2026-06-11] _ob_fn / _ob_adj 已移除，调用代码已清除
    try:
        pass  # B2已禁用，结果在主评分流程的s14段处理
    except Exception as _e:
        pass

    # B3: 滑点模型
    try:
# [CLEANED 2026-06-11] from slippage_model import estimate_slippage as _slip_fn
        _nav = 124.97
        _kelly = 0.05
        _notional = _nav * _kelly * float(ms.get('leverage', 10))
    except Exception as _e:
        extra_data['slippage_err'] = str(_e)[:80]

    # B4: 链上大单 WS/REST
    try:
        from onchain_ws import analyze as _ws_fn
        extra_data['onchain_ws'] = _ws_fn(symbol, signal_dir)
    except Exception as _e:
        extra_data['onchain_ws_err'] = str(_e)[:80]

    # 传递给 xgboost（需要完整 snap）
    extra_data['_snap_for_xgb'] = {
        'confluence': extra_data.get('confluence_preview', {}),
        'direction': signal_dir,
        'regime': ms.get('regime', ''),
        'params': {'rr1': 2.0},
        'extra': extra_data,
        'market_state': ms,
    }

    # ─── Phase C: LSTM + RL + NLP | 阶段C：LSTM + 强化学习 + 自然语言处理 ──────────────────────────────────
    # C1: LSTM 时序预测
    try:
# [CLEANED 2026-06-11] from lstm_engine import analyze as _lstm_fn
        _klines_1h = extra_data.get('_klines_1h') or ms.get('klines_1h')
    except Exception as _e:
        extra_data['lstm_err'] = str(_e)[:80]

    # C2: RL 仓位决策（已禁用 2026-06-11，模块已清除）
    # [CLEANED 2026-06-11] _rl_fn 已移除，调用代码已清除
    try:
        pass  # C2已禁用
    except Exception as _e:
        pass

    # C3: NLP 情绪引擎
    try:
        import sys as _sys_sent, os as _os_sent
        _bd_sent = _os_sent.path.join(_os_sent.path.dirname(_os_sent.path.abspath(__file__)))
        if _bd_sent not in _sys_sent.path: _sys_sent.path.insert(0, _bd_sent)
        # 直接通过完整路径加载模块
        import importlib.util as _ilu_sent
        _spec = _ilu_sent.spec_from_file_location(
            'sentiment_engine_local',
            _os_sent.path.join(_bd_sent, 'sentiment_engine.py'))
        _sm = _ilu_sent.module_from_spec(_spec)
        _spec.loader.exec_module(_sm)
        extra_data['sentiment_nlp'] = _sm.analyze(symbol, signal_dir)
    except Exception as _e:
        extra_data['sentiment_nlp_err'] = str(_e)[:80]

    # ══ [Kronos环境感知器 2026-07-24 设计院封印] ══════════════════════════════
    # 定位: 信号生成层最前端 — 在35维评分之前判断时序环境
    # 核心思想(六方联合): Kronos不只是打分项，应该是环境感知器
    #   p_up高度对齐体制 → 放行+记录共振标记
    #   p_up严重逆体制   → 直接WAIT，不进入35维评分（节省计算+防噪音）
    # 规则（三层）:
    #   L1 强势WAIT: LONG方向 + p_up<0.35 + 非BEAR_RECOVERY → 环境看空，直接返回WAIT
    #   L2 共振奖励: LONG方向 + p_up>0.65 + BEAR_RECOVERY → 时序与体制共振，+环境标记
    #   L3 中性放行: 其余情况 → 继续正常35维评分
    _kronos_env_badge = 'NEUTRAL'
    try:
        _ke_regime = ms.get('regime', 'CHOP_MID')
        _ke_dir    = str(signal_dir or 'LONG').upper()
        _ke_p_up   = ms.get('s23_p_up', 0.5)  # 已在前序步骤写入ms
        if _ke_p_up and 0.0 < _ke_p_up < 1.0:
            if _ke_dir == 'LONG' and _ke_p_up < 0.35 and 'BEAR_RECOVERY' not in _ke_regime:
                # L1: 时序强看空 + 非复苏体制 → 直接WAIT
                return {
                    'symbol': symbol, 'direction': signal_dir,
                    'score': 0, 'score_final': 0, 'valid': False,
                    'regime': _ke_regime,
                    'kronos_env': 'BLOCKED',
                    'kronos_p_up': _ke_p_up,
                    'reason': f'[Kronos环境感知器] p_up={_ke_p_up:.2f}<0.35 时序强看空，禁止做多',
                    'timing_status': 'WAIT',
                    'timing_badge': '⏸ WAIT(Kronos环境感知)',
                }
            elif _ke_dir == 'LONG' and _ke_p_up > 0.65 and 'BEAR_RECOVERY' in _ke_regime:
                _kronos_env_badge = 'RESONANCE'  # 时序与体制共振，35维评分后额外加权
            elif _ke_dir == 'SHORT' and _ke_p_up > 0.65:
                _kronos_env_badge = 'SHORT_CONFIRM'  # 空单方向Kronos确认
    except Exception:
        pass  # 环境感知失败不阻断主链路
    # ══ [Kronos环境感知器 END] ══════════════════════════════════════════════════

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
        _p2a_sys.path.insert(0, _p2a_os.path.dirname(_p2a_os.path.abspath(__file__)))
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
            _cfc_sys.path.insert(0, _cfc_root)
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

    # ── [v5.6 设计院封印 2026-07-13] 新三项能力评分叠加 ──────────────────
    # P0: 清算热力图评分（上方近距空头清算=轧空诱因）
    _lhm = extra_data.get('liq_heatmap', {})
    if _lhm and not _lhm.get('error'):
        _lhm_bull = _lhm.get('liq_bull_score', 0)
        _lhm_bear = _lhm.get('liq_bear_score', 0)
        _lhm_adj  = (_lhm_bull - _lhm_bear) if signal_dir != 'SHORT' else (_lhm_bear - _lhm_bull)
        if _lhm_adj != 0:
            _lhm_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _lhm_pre + _lhm_adj
            cf.setdefault('breakdown', {})['_liq_heatmap'] = (
                f'{_lhm_adj:+d}(空头清算={_lhm.get("nearest_short_liq",0):,.0f} '
                f'+{_lhm.get("dist_to_short_liq",0):.2f}%)'
            )

    # P1b: 鲸鱼监控评分
    # [设计院统计修正 2026-07-20] 实证发现：BULL_TREND体制下做多方向鲸鱼高分→亏损率更高
    # 数据：WIN均值=11.13 vs LOSS均值=13.95，差值-2.82，p=0.083（统计显著）
    # 修正：BULL_TREND做多时鲸鱼分值降权×0.6（顺势鲸鱼/逆势鲸鱼需要区分）
    _wh2 = extra_data.get('whale_v2', {})
    if _wh2 and not _wh2.get('error'):
        _wh2_adj = _wh2.get('whale_score', 0)
        if signal_dir == 'SHORT': _wh2_adj = -_wh2_adj
        # P0-修正：BULL_TREND做多降权×0.6（统计回归p=0.083，鲸鱼反转效应）
        _wh2_regime = str(ms.get('regime', ''))
        if 'BULL_TREND' in _wh2_regime and signal_dir == 'LONG' and _wh2_adj > 0:
            _wh2_adj_orig = _wh2_adj
            _wh2_adj = round(_wh2_adj * 0.6)
            print(f'[鲸鱼降权] BULL_TREND_LONG 鲸鱼分值降权×0.6: {_wh2_adj_orig:+d}→{_wh2_adj:+d}')
        if _wh2_adj != 0:
            _wh2_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _wh2_pre + _wh2_adj
            cf.setdefault('breakdown', {})['_whale_v2'] = (
                f'{_wh2_adj:+d}({_wh2.get("whale_direction","?")} '
                f'LS={_wh2.get("whale_ls_trend","?")})'
            )

    # P2: 矿工卖压评分（BTC专属）
    _mp = extra_data.get('miner_pressure', {})
    if _mp and not _mp.get('error') and _sym.startswith('BTC'):
        _mp_adj = _mp.get('miner_score', 0)
        if signal_dir == 'SHORT': _mp_adj = -_mp_adj
        if _mp_adj != 0:
            _mp_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _mp_pre + _mp_adj
            cf.setdefault('breakdown', {})['_miner_pressure'] = (
                f'{_mp_adj:+d}(矿工利润={_mp.get("miner_margin_pct",0):+.1f}% '
                f'{_mp.get("pressure_level","?")})'
            )

    # P1c: 期权P/C比评分（设计院 2026-07-13 补充）
    _opc = extra_data.get('options_pc', {})
    if _opc and not _opc.get('error'):
        _opc_adj = int(_opc.get('pc_score', 0) or 0)
        if _opc_adj != 0:
            _opc_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _opc_pre + _opc_adj
            cf.setdefault('breakdown', {})['_options_pc_v56'] = (
                f'{_opc_adj:+d}(P/C OI={_opc.get("pc_oi_ratio",0):.3f} '
                f'{_opc.get("interpretation_oi","")})'
            )

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
    _m06_zero_coef = {'ETHUSDT', 'ATOMUSDT'}
    _cur_score = cf.get('total', 0)
    if _sym in _m06_zero_coef and _cur_score > 0:
        _pen = 5
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
        import os as _os_dbg
        if _os_dbg.environ.get('BRAHMA_DEBUG'):
            pass  # [静默] f'[PRE-SQE] {_sym} price={ms.get("price",0):.0f} entry={params.get("entry_lo",0)
    except Exception as _pre_sq_err:
        pass  # 失败不影响主流程

    # ══════════════════════════════════════════════════════════
    # [v12.9] I5 队列/资金 / I3 动态SL / I7 归因（Phase 1）
    # ══════════════════════════════════════════════════════════

    # I5: 信号队列检查（是否可以进入队列）
    try:
        from signal_queue import add_signal as _sq_add, get_status as _sq_status
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
    # [达摩院v2.0 M11] CI宽度仓位折扣层 — 安全保险丝
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
        # [设计院统计封印 2026-07-20] CHOP_HIGH专项强化封禁
        # 实证数据：CHOP_HIGH WR=9.1%(n=11)，T检验p=0.016(★★强方向负偏误)
        # 峰度=3.82（重尾，小亏多次+偶尔大亏模式），苏摩111批准封印
        _chop_high_regime = 'CHOP_HIGH' in str(ms.get('regime',''))
        if _chop_high_regime:
            _ch_score_before = _score
            _score = min(_score, 75)  # 硬性上限75（比tc同向CHOP一样处理）
            if _ch_score_before > 75:
                cf['breakdown']['CHOP_HIGH封禁'] = f'WR=9.1% T检验p=0.016 统计强负偏误: {_ch_score_before:.0f}→75'
                print(f'[CHOP_HIGH封禁] score: {_ch_score_before:.0f}→75 WR=9.1% p=0.016')
            else:
                cf.setdefault('breakdown', {})['CHOP_HIGH警告'] = f'WR=9.1% 高危体制，score={_score:.0f} 仍低于75上限'
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
            # tc_neutral(0)：维持原90上限
            _chop_cap_applied = 90
            if _score > 90:
                _score = 90
                cf['breakdown']['CHOP硬性上限'] = f'P2保护tc_neutral: {_score_before_cap:.0f}→90（CHOP整体EV=-0.11%）'
                pass  # [静默] f'[P2-CHOP-CAP] {ms.get("symbol","?")} CHOP体制tc_neutral上限: {_score_before_cap:.0
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
            cf['breakdown']['死穴封禁'] = f'BEAR_TREND_LONG WR=45%(铁证n=3322) 未达精英解锁[score≥155+grade≥90+RSI<20] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BEAR_TREND_LONG: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BULL_TREND' in _regime_str and _dir_check == 'SHORT':
        # 精英解锁：score≥155 AND grade≥90 AND RSI_1H>75（高RSI顶部结构做空）
        # [Phase1-3 2026-07-23 设计院] 扩展解锁通道：BTC.D<52%时降低score门槛至148
        _btcd = float(cf.get('btc_dominance', 55) or 55)
        _bu_elite_std  = (_dz_score >= 155 and _dz_grade >= 90 and _dz_rsi1h > 75)
        _bu_elite_ext  = (_dz_score >= 148 and _dz_grade >= 85 and _dz_rsi1h > 72 and _btcd < 52)
        _bu_elite = _bu_elite_std or _bu_elite_ext
        if _bu_elite:
            _unlock_mode = 'std' if _bu_elite_std else f'ext(BTC.D={_btcd:.1f}%<52%)'
            print(f'[死穴-精英解锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f} mode={_unlock_mode} → 0.5%NAV观察仓')
            cf['breakdown']['死穴精英解锁'] = f'BULL_TREND_SHORT RSI={_dz_rsi1h:.0f}>75顶部结构做空 score={_dz_score:.0f} grade={_dz_grade} mode={_unlock_mode} → 0.5%NAV'
        else:
            _valid = False
            cf['breakdown']['死穴封禁'] = f'BULL_TREND_SHORT WR=47.7%(铁证n=4999) 未达精英解锁[score≥155+grade≥90+RSI>75] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BEAR_RECOVERY' in _regime_str and _dir_check == 'SHORT':
        # [v25.4死穴修复 2026-06-27] BEAR_RECOVERY_SHORT WR=46.6%/46.0% 升级为物理封锁
        # 达摩院铁证 n=233(BTC)/238(ETH) avg_pnl=-0.183/-0.305
        # 例外解锁：score>=145 AND grade>=90 AND Kronos p_up<0.2
        _br_score = cf.get('total', 0)
        _br_grade = cf.get('grade', 0)
        _br_pup   = cf.get('s23_p_up', 1.0)
        # [v25.4b防封闭修复] 例外条件放宽：145→140, 90→85, 0.2→0.25
        # 理由：n=233次铁证，非宪法级死穴，不应过严封闭
        if not (_br_score >= 140 and _br_grade >= 85 and _br_pup < 0.25):
            _valid = False
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
            and _score_raw >= 155
        )
        if _sq['grade'] < 80 and not _bull_grade_exception:
            # grade<80: 包含grade70-79死亡区（WR=47%）全部封堵
            _score_raw = 0
            cf['total'] = 0
            cf['action'] = 'SKIP'
            cf['kelly_mult'] = 0
            cf['structure_reject'] = f'grade={_sq["grade"]}({_sq["label"]}) grade<80 WR=47%死亡区封堵 [v25.4]'
            pass  # [静默] f'[StructureGate] 🚫 {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]}<80 
        elif _sq['grade'] < 80 and _bull_grade_exception:
            # BULL_TREND特例通道：grade75~79允许通过（三重条件保护）
            pass  # [静默] f'[StructureGate] ⚡ {_sym} LONG: BULL_TREND特例 grade={_sq["grade"]}(≥75) score≥15
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


    # ── [N22b] WR矩阵动态加成层 [设计院封印 2026-06-27] ──────────────────────
    # 职责：读取 dharma_runtime.wr_matrix_v7，为主战场体制提供实证WR加分
    # BEAR_TREND×SHORT=71.3% n=1188 → +4分；BLOCK体制→-15分
    try:
        import json as _j22b
        _dm22b = _j22b.loads(open('data/dharma_runtime.json').read())
        _wv7   = _dm22b.get('wr_matrix_v7', {})
        # [方案C v25.4 苏摩审批] 周期感知查找：优先 REGIME_DIR_TF，fallback REGIME_DIR
        _tf22    = ms.get('entry_tf', ms.get('tf', '15M'))  # 信号触发周期
        _combo22     = f"{ms.get('regime','').upper()}_{signal_dir}"
        _combo22_tf  = f"{ms.get('regime','').upper()}_{signal_dir}_{_tf22}"
        _sym_wv7 = _wv7.get(_sym, {})
        # 优先使用带周期的精确键，fallback到混合键
        _wdata22 = _sym_wv7.get(_combo22_tf) or _sym_wv7.get(_combo22, {})
        if _sym_wv7.get(_combo22_tf):  # 命中周期分层
            _combo22 = _combo22_tf  # 用于日志显示
        _wr22b   = _wdata22.get('wr', 0)
        _n22b    = _wdata22.get('n', 0)
        _act22   = _wdata22.get('action', 'SKIP')
        _pts22b  = 0
        if _act22 == 'ALLOW' and _n22b >= 500 and _wr22b > 0:
            _pts22b = max(-10, min(15, round((_wr22b - 0.50) * 20)))
        elif _act22 in ('BLOCK', 'PERMANENT_BLOCK'):
            _pts22b = -15
        elif _act22 == 'PENALIZE':  # [v25.4] 新增：宪法级潜伏死穴惩罚
            _pts22b = int(_wdata22.get('penalize_pts', -10))
        if _pts22b != 0:
            _score_raw += _pts22b
            cf['n22b_wr_matrix'] = f'N22b_WR矩阵:{_pts22b:+d}({_combo22} wr={_wr22b:.1%} n={_n22b})'
            pass  # [静默] f'[N22b-WRMatrix] {_sym} {_combo22}: {_pts22b:+d}分 WR={_wr22b:.1%} n={_n22b}'
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
                    if _bres == 'TP':
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
            _dfe_sys.path.insert(0, _dfe_root)
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
    try:
        import sys as _sys_b, os as _os_b
        _bd = _os_b.path.dirname(_os_b.path.abspath(__file__))
        _root = _os_b.path.dirname(_bd)
        if _root not in _sys_b.path:
            _sys_b.path.insert(0, _root)
        # [fix 2026-07-18 苏摩111] timing层注入：在log_signal之前计算timing并写入_result
        # 根因：timing通常由brahma_analysis_runner后处理，brahma_engine直接调用时timing字段缺失
        try:
            from timing_filter import evaluate_timing
        except ImportError:
            try:
                from brahma_brain.timing_filter import evaluate_timing
            except ImportError:
                evaluate_timing = None
        if evaluate_timing is not None and not _result.get('timing_badge'):
            try:
                _pa_tf = _result.get('params', {}) or {}
                _cf_tf = _result.get('confluence', {}) or {}
                _timing_res = evaluate_timing(
                    symbol=_sym,
                    signal_dir=_result.get('signal_dir', _result.get('direction', 'LONG')),
                    score=float(_cf_tf.get('total', _result.get('score', 0)) or 0),
                    grade=float(_cf_tf.get('effective_grade', _cf_tf.get('structure_grade', 70)) or 70),
                    entry_lo=float(_pa_tf.get('entry_lo', 0) or 0),
                    entry_hi=float(_pa_tf.get('entry_hi', 0) or 0),
                    current_price=float(_result.get('price', 0) or 0),
                    s23_p_up=_result.get('s23_p_up', 0.5),
                    regime=_result.get('regime', 'BEAR_TREND'),
                )
                _result['timing_badge']  = _timing_res.get('badge', '')
                _result['timing_status'] = _timing_res.get('status', 'UNKNOWN')
                _result['timing_score']  = _timing_res.get('score', 0)
                _result['_timing']       = _timing_res  # [自主决策 2026-07-20] signal_trace.py兼容字段
            except Exception:
                pass  # timing失败不阻断主流
        from dharma_data_bridge import log_signal as _log_dharma
        _logged = _log_dharma(_result)
        if _logged:
            pass  # [静默] f'[DharmaBridge] ✓ {_sym} score={_score:.0f} 已写入 live_signal_log'
    except Exception as _e:
        pass  # [静默] f'[DharmaBridge] ⚠ 写入失败（不阻断主流）: {_e}'

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
            import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'scripts'))
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

    # ── s23: Kronos-Lite × 体制解锁器 × CHOP过滤器 ─────────────────────
    # 设计院 × 达摩院 v9.0-SLIM · 2026-06-17
    # 三个职责：
    #   A. 基础预测维度（p_up方向概率 → ±12分）
    #   B. CHOP期方向冲突惩罚（过滤不确定信号）
    #   C. CORRECTION/RECOVERY体制解锁（双证据激活最高WR体制）
    try:
        import sys as _sys23, os as _os23
        _bb23 = _os23.path.dirname(_os23.path.abspath(__file__))
        if _bb23 not in _sys23.path:
            _sys23.path.insert(0, _bb23)
        from kronos_lite import get_s23_score as _get_s23
        from recovery_unlocker import check_unlock as _check_unlock
        # [根本修复 2026-07-12] _sym_t/_dir_t可能因s20 try异常而未定义，这里就地定义
        _sym_t  = _result.get('symbol', symbol)
        _dir_t  = _result.get('signal_dir', signal_dir)

        _kl15m = ms.get('klines_15m', [])
        # 如果ms中没有klines_15m，尝试从extra_data或直接获取
        if not _kl15m and extra_data is not None:
            _kl15m = extra_data.get('_klines_15m', [])
        if not _kl15m:
            try:
                _raw15 = get_klines(ms.get('symbol', _sym_t), '15m', 200)
                _kl15m = [[float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])] for c in _raw15]
            except Exception:
                _kl15m = []
        # [潜力释放 P1 2026-07-12] klines格式强制转换，避免Kronos klines含NaN报错
        # 根因: ms.klines_15m是dict格式或字符串数字，Kronos需要float列表
        if _kl15m and isinstance(_kl15m[0], dict):
            _kl15m = [[float(_k.get('o',0)),float(_k.get('h',0)),
                       float(_k.get('l',0)),float(_k.get('c',0)),float(_k.get('v',0))]
                      for _k in _kl15m]
        elif _kl15m and isinstance(_kl15m[0], (list, tuple)):
            try:
                _kl15m = [[float(_v) for _v in _k[:5]] for _k in _kl15m]
            except Exception:
                _kl15m = []

        if len(_kl15m) >= 60:

            # ① 计算Kronos-Lite s23基础分 (v2.0: 体制自适应+BTC领先信号)
            _s23_regime = _result.get('regime', '')
            # v2.0: 获取BTC领先信号修正（仅非BTC标的）
            _btc_p_up_s23 = None
            if _sym_t != 'BTCUSDT':
                try:
                    from kronos_lite import _compute_p_up as _kl_cpu, _CACHE as _kl_cache
                    _btc_ck = 'BTCUSDT_15m'
                    import time as _t23
                    if _btc_ck in _kl_cache and (_t23.time() - _kl_cache[_btc_ck][0]) < 900:
                        _btc_p_up_s23 = _kl_cache[_btc_ck][1]
                    else:
                        _btc_kl15 = get_klines('BTCUSDT', '15m', 200)
                        _btc_kl15f = [[float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])] for c in _btc_kl15]
                        if len(_btc_kl15f) >= 60:
                            _btc_p_up_s23, _ = _kl_cpu(_btc_kl15f, regime=_s23_regime, tf_hint='15m')
                            _kl_cache[_btc_ck] = (_t23.time(), _btc_p_up_s23, 0.0)
                except Exception:
                    _btc_p_up_s23 = None
            _s23, _s23_meta = _get_s23(
                _sym_t, _dir_t, _kl15m,
                regime=_s23_regime,
                tf_hint='15m',
                btc_p_up=_btc_p_up_s23,
            )

            # ② CHOP期方向冲突额外惩罚（突破二）
            _cur_regime = _result.get('regime', '')
            if 'CHOP' in _cur_regime and _s23_meta.get('direction_conflict', False):
                _s23 = min(_s23, -10)  # 方向冲突 = 否决性惩罚

            # ③ CORRECTION/RECOVERY体制解锁（突破一）
            _cur_score23 = _result['confluence']['score']
            _unlock = _check_unlock(
                regime=_cur_regime,
                direction=_dir_t,
                base_score=_cur_score23,
                kronos_meta=_s23_meta,
                symbol=_sym_t,
            )
            if _unlock['unlocked']:
                _s23 = max(_s23, _unlock['s23_bonus'])
                _s23_meta['unlock_regime'] = _unlock['regime']
                _s23_meta['unlock_reason'] = _unlock['reason']

            # ④ 注入总分（仅非零才注入，避免污染breakdown）
            # [P2 设计院 2026-06-21] s23边际贡献为负(-2.9%WR, Gate2未通过) → 降权50%
            # [Kronos极値封印 2026-07-01] p_up>0.90时 = 反弹窗口打开，不是空单否决
            # 设计院分析：极値应被解读为「录入数据工程师信息」而非封空单
            _p_up_raw = _s23_meta.get('p_up', 0.5)
            if _p_up_raw >= 0.90 and _dir_t == 'SHORT':
                # p_up极高(>0.90) + 做空 = 反弹动能强，价格即将触达OB区
                # 不封空单，而是转化为「待功反弹到位​再空」模式
                _s23_extreme_note = f'注意: p_up={_p_up_raw:.2f}极高 = 反弹窗口打开，等OB区再空入'
                # 惩罚减半: -8分降为-4（保持警示但不过度封空）
                _s23 = max(_s23, -4)  # 最大惩罚降半
                print(f'[s23-Kronos极値] {_sym_t} p_up={_p_up_raw:.2f}极高: 惩罚降半至{_s23} | {_s23_extreme_note}')
            elif _p_up_raw >= 0.90 and _dir_t == 'LONG':
                # p_up极高 + 做多 = 顺势，保留完整加分
                pass
            if _s23 != 0:
                # [Kronos体制自适应权重 2026-07-24 设计院封印]
                # 根因: IC数据显示不同体制下Kronos预测力差异巨大
                # BEAR_RECOVERY:LONG IC=0.762 vs BULL_TREND:LONG IC=0.154
                # 用固定0.5x无法释放BEAR_RECOVERY下Kronos的真实价值
                _KRONOS_REGIME_WEIGHT = {
                    'BEAR_RECOVERY': 1.5,  # IC=0.762，高度信任
                    'BULL_TREND':    0.8,  # IC=0.154，中等信任
                    'BEAR_EARLY':    0.8,  # 与BULL_TREND同级
                    'BEAR_TREND':    0.4,  # n=1样本过少，保守
                    'CHOP_MID':      0.3,  # 震荡期Kronos噪音大
                }
                # 样本门控：n<10的体制回退到0.5x，防止小样本IC过度信任
                _IC_SAMPLE_MIN = {
                    'BEAR_RECOVERY': 6, 'BULL_TREND': 39,
                    'BEAR_TREND': 1,    'CHOP_MID': 3,
                }
                _cur_regime_key = str(_regime_t).upper().replace(' ', '_') if '_regime_t' in dir() else ''
                _regime_mult = 0.5  # 默认
                for _rk, _rw in _KRONOS_REGIME_WEIGHT.items():
                    if _rk in _cur_regime_key:
                        _n_sample = _IC_SAMPLE_MIN.get(_rk, 99)
                        _regime_mult = _rw if _n_sample >= 10 else 0.5  # 样本不足→保守
                        break
                _s23_w = round(_s23 * _regime_mult)
                _result['confluence']['score'] = _cur_score23 + _s23_w
                _result['confluence']['_s23_kronos'] = _s23_w
                _result['confluence'].setdefault('breakdown', {})['s23_kronos'] = (
                    f"{_s23_w:+d}(原{_s23:+d}×{_regime_mult}) ({_s23_meta.get('reason','')[:60]})"
                )
                # [CHOP专项 2026-06-27] 写入 p_up 供 offline_replay 三维分层使用
                _result['s23_p_up'] = _p_up_raw
                _unlock_tag = f" 🔓UNLOCK:{_unlock['regime']}" if _unlock.get('unlocked') else ''
                _extreme_tag = f' 🚨极値模式' if _p_up_raw >= 0.90 else ''
                print(f'[s23-Kronos] {_sym_t} {_dir_t}: {_s23:+d}'
                      f' | p_up={_p_up_raw:.2f}'
                      f' | src={_s23_meta.get("source","?")}{_unlock_tag}{_extreme_tag}')

    except Exception as _e23:
        pass  # s23任何异常不影响主流程

    # ══ [设计院 2026-06-30 P3] kronos_engine — 完整版时序预测（模型可用时）══
    # 逻辑：kronos_lite是轻量RSI代理，kronos_engine是真正的4.1M参数模型
    # 当模型可用时，用完整版覆盖s23分（更高精度）
    # fail-safe：模型未下载/torch不可用时静默跳过，不影响主流程
    try:
        from kronos_engine import get_kronos_score as _ke_fn, _is_available as _ke_ok
        if _ke_ok():   # 只在模型已加载时运行
            _ke_score, _ke_reason = _ke_fn(
                _sym_t,
                signal_dir or _result.get('signal_dir', 'SHORT'),
                _kl15m if '_kl15m' in dir() else [],
                ms.get('regime', '')
            )
            if _ke_score != 0:
                _score_raw = _result.get('score', _result.get('cf', {}).get('total', 0))
                print(f'[s23-KronosEngine] {_sym_t}: {_ke_score:+d} 完整模型覆盖 | {_ke_reason[:40]}')
    except Exception:
        pass
    # ══ [KronosEngine END] ═════════════════════════════════════════════════════

    # ══ [KronosBridge SHADOW] 设计院 v17 达摩院验证路径 2026-07-01 ══════════
    # shadow模式：并联记录 Kronos大模型 vs Kronos-Lite 差异
    # 不修改任何分数；积累n≥100后达摩院M1验证 → blend → live
    try:
        import sys as _sys_kb, os as _os_kb
        _kb_brain = _os_kb.path.dirname(_os_kb.path.abspath(__file__))
        _kb_root  = _os_kb.path.dirname(_kb_brain)
        for _kb_p in [_kb_brain, _kb_root, _kb_root + '/external/Kronos']:
            if _kb_p not in _sys_kb.path:
                _sys_kb.path.insert(0, _kb_p)
        from kronos_bridge import get_s23_kronos as _kb_fn
        # 获取 kronos_lite 的原始分和 p_up（用于对比记录）
        _kb_lite_score = _s23 if '_s23' in dir() else None
        _kb_lite_p_up  = _p_up_raw if '_p_up_raw' in dir() else None
        _kb_klines     = _kl15m if '_kl15m' in dir() and _kl15m else []
        if _kb_klines and len(_kb_klines) >= 32:
            _kb_score, _kb_meta = _kb_fn(
                klines_15m = _kb_klines,
                symbol     = _sym_t,
                direction  = _dir_t if '_dir_t' in dir() else 'LONG',
                regime     = ms.get('regime', 'UNKNOWN') if 'ms' in dir() else 'UNKNOWN',
                lite_score = _kb_lite_score,
                lite_p_up  = _kb_lite_p_up,
            )
            # shadow模式：只打印，不修改score
            _kb_delta = _kb_meta.get('kronos_score', 0) - (_kb_lite_score or 0)
            if abs(_kb_delta) >= 2:  # 差异≥2分才打印，减少噪音
                print(f'[KronosBridge·SHADOW] {_sym_t}: '
                      f'Kronos={_kb_meta["kronos_score"]:+d} '
                      f'Lite={_kb_lite_score:+d} '
                      f'Δ={_kb_delta:+d} '
                      f'p_up={_kb_meta["p_up"]:.3f} '
                      f'src={_kb_meta["source"]}')
    except Exception as _e_kb:
        pass  # KronosBridge shadow不影响主流程
    # ══ [KronosBridge SHADOW END] ══════════════════════════════════════════════

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
            _sys25.path.insert(0, str(Path(__file__).parent))
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
        _pos_res  = _pos_fn(_sym, _ps_score, _ps_dir)
        if _pos_res.get('allowed'):
            _result['pos_pct_sizer']    = _pos_res.get('pct', 0)
            _result['pos_level_sizer']  = _pos_res.get('level', '')
            _result['pos_reason_sizer'] = _pos_res.get('reason', '')
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
        _sp_k1h  = _result.get('_klines_1h', [])
        _sp_k4h  = _result.get('_klines_4h', [])
        _sp_reg  = _result.get('regime', '')
        _sp_sym  = _result.get('symbol', _sym)
        _s27 = s27_gap_up(_sp_sym, _sp_k1h, _sp_reg) if _sp_k1h else 0
        _s28 = s28_bounce_setup(_sp_sym, _sp_k1h, _sp_k4h, _sp_reg) if _sp_k1h else 0
        _s29 = s29_first_red_day(_sp_sym, _sp_k1h, _sp_reg) if _sp_k1h else 0
        _sp_total = _s27 + _s28 + _s29
        if _sp_total != 0:
            _result['score_final'] = (_result.get('score_final') or 0) + _sp_total
            _result['s27_gap_up']       = _s27
            _result['s28_bounce_setup'] = _s28
            _result['s29_first_red_day']= _s29
            print(f'[s27-29] {_sp_sym} gap={_s27:+d} bounce={_s28:+d} frd={_s29:+d} total={_sp_total:+d}')
    except Exception as _esp:
        pass  # 统计模式维度不影响主评分

    # ══ [可观测-v2] ══
    try:
        _s=_result.get('score_final',_result.get('score',0))
        pass  # [静默] f'[SIGNAL-SUMMARY] {_sym} {signal_dir} score={_s:.0f} action={_result.get("actio
    except Exception: pass

    # ══ [v5.1 梵天历史引用层 + 中期记忆层] ══
    try:
        import sys as _s51, os as _o51
        _r51 = _o51.path.dirname(_o51.path.dirname(_o51.path.abspath(__file__)))
        if _r51 not in _s51.path: _s51.path.insert(0, _r51)
        from scripts.regime_memory_7d import get_regime_score_adjustment
        from scripts.signal_history_scorer import get_history_score_adjustment
        _reg51 = _result.get('regime', 'BULL_TREND')
        _dir51 = signal_dir or 'LONG'
        _sym51 = symbol
        # 中期体制记忆
        _radj, _rreason = get_regime_score_adjustment(_reg51, _dir51)
        # 历史胜率引用
        _elo51 = float(_result.get('entry_lo', 0) or 0)
        _ehi51 = float(_result.get('entry_hi', 0) or 0)
        _hadj, _hreason = get_history_score_adjustment(_sym51, _reg51, _dir51, _elo51, _ehi51)
        _total51 = _radj + _hadj
        if _total51 != 0:
            _result['score_final'] = (_result.get('score_final') or 0) + _total51
            _result['v51_regime_adj'] = _radj
            _result['v51_history_adj'] = _hadj
            _result['v51_reason'] = f'regime:{_rreason} | history:{_hreason}'
    except Exception:
        pass  # v5.1层错误静默，不影响主流程

    # ══ [梵天2.0 Phase 1b · MODE_C庄家行情识别+对冲健康注入] ══════════════
    # 设计院×达摩院 封印 2026-07-20 · 最小侵入，fail-safe零阻断
    # 积分消耗：0 tokens（纯本地计算）
    try:
        from brahma_brain.mode_c_detector import detect as _mc_detect, quick_mode_check as _mc_quick
        _mc_ms = ms  # ms在analyze()作用域内全局可用

        # 从klines_15m提取量能数据
        _mc_kl15 = _mc_ms.get('klines_15m', [])
        _mc_vol_cur  = float(_mc_kl15[-1]['v']) if _mc_kl15 else 0
        _mc_vols20   = [float(k['v']) for k in _mc_kl15[-20:]] if _mc_kl15 else [1]
        _mc_vol_avg  = sum(_mc_vols20) / len(_mc_vols20) if _mc_vols20 else 1
        _mc_candle_h = float(_mc_kl15[-1]['h']) if _mc_kl15 else float(_mc_ms.get('price', 0)) * 1.01
        _mc_candle_l = float(_mc_kl15[-1]['l']) if _mc_kl15 else float(_mc_ms.get('price', 0)) * 0.99

        # low24h 从klines_15m[-96:]推算
        _mc_lows24   = [float(k['l']) for k in _mc_kl15[-96:]] if len(_mc_kl15) >= 96 else [float(k['l']) for k in _mc_kl15]
        _mc_low24h   = min(_mc_lows24) if _mc_lows24 else float(_mc_ms.get('price', 0)) * 0.5

        # 多空比、FR
        _mc_sent     = _mc_ms.get('sentiment', {}) or {}
        _mc_lsr      = float(_mc_sent.get('long_short_ratio', 50) or 50)
        _mc_short_r  = 1.0 - (_mc_lsr / 100.0)  # long_short_ratio是多头%
        _mc_fr       = float(_mc_sent.get('funding_rate', 0) or 0)
        _mc_fr_sat   = sum(1 for k in _mc_kl15[-24:] if False)  # FR满值由外部注入，默认0
        # 用chg24估算FR满值期数（chg24>50%时大概率有FR满值）
        _mc_chg24    = float(_mc_ms.get('chg24', 0) or 0)
        if _mc_fr >= 0.000049 and _mc_chg24 > 30:
            _mc_fr_sat = min(int(_mc_chg24 / 10), 6)  # 估算

        _mc_price    = float(_mc_ms.get('price', 0))

        # 运行MODE_C检测
        _mc_result = _mc_detect(
            symbol=_sym,
            price=_mc_price,
            price_low_24h=_mc_low24h,
            short_ratio=_mc_short_r,
            vol_current=_mc_vol_cur,
            vol_avg_20=_mc_vol_avg,
            candle_high=_mc_candle_h,
            candle_low=_mc_candle_l,
            fr_rate=_mc_fr,
            fr_saturation_count=_mc_fr_sat,
        )

        # 注入结果到_result
        _result['mode_c'] = _mc_result
        _result['market_mode'] = _mc_result.get('mode', 'MODE_A')

        # MODE_C激活时：对score_final施加降权
        if _mc_result.get('mode') == 'MODE_C' and _mc_result.get('short_ban'):
            _mc_wr_mult = _mc_result.get('wr_multiplier', 1.0)
            _mc_dir     = _result.get('direction', signal_dir or 'LONG')
            if _mc_dir == 'SHORT':
                # 做空方向：score降权
                _mc_penalty = int((_result.get('score_final', 0) or 0) * (1 - _mc_wr_mult))
                _result['score_final'] = (_result.get('score_final') or 0) - _mc_penalty
                _result['mode_c_penalty'] = _mc_penalty
                _result['mode_c_note'] = f'MODE_C做空降权×{_mc_wr_mult}: -{_mc_penalty}分'
            # 无论方向，记录封禁状态
            _result['short_ban'] = True
            _result['short_ban_reason'] = _mc_result.get('note', '')
        else:
            _result['short_ban'] = False

    except Exception as _mc_e:
        _result['mode_c'] = {'mode': 'MODE_A', 'note': f'[mode_c_detector异常,不阻断] {_mc_e}'}
    # ══ [Phase 1b END] ══════════════════════════════════════════════════════

    # ── [协同融合总线 2026-07-20 设计院自主] brahma_coordinator全局协同 ──────
    if 'coord' not in _result:  # [A1修复] 防止retry重复累加
      try:
        from brahma_brain.brahma_coordinator import build_coord_context, format_coord_summary
        _coord_ctx = build_coord_context(symbol, signal_dir, _result.get('regime',''), _result.get('score',0))
        _result['coord'] = _coord_ctx
        # 应用协同总加成
        _coord_bonus = _coord_ctx.get('total_bonus', 0)
        if _coord_bonus != 0:
            _result['score'] = round(_result.get('score', 0) + _coord_bonus, 1)
            _result.setdefault('breakdown', {})['协同总线加成'] = f'{_coord_bonus:+d}分 ({format_coord_summary(_coord_ctx)})'
        # 暴涨猎手MODE_C触发做空封禁
        if _coord_ctx.get('short_ban_by_pump') and signal_dir == 'SHORT':
            _result['globally_blocked'] = True
            _result['blocked_reason'] = f'暴涨猎手MODE_C封禁做空 ({_coord_ctx["pump"]["mode_c_source"]})'
      except Exception as _coord_e:
          pass  # [静默，非阻断]
    # ────────────────────────────────────────────────────────────────────────

    # ── [协同融合 2026-07-20 设计院自主] 孤立资产接入层 ─────────────────────
    # 1. SSI轧空风险门控
    try:
        from brahma_brain.ssi_engine import compute_ssi, get_ssi_level as _ssi_level_fn
        # [SSI接口修复 2026-07-20] compute_ssi需要short_ratio/oi/price参数
        import urllib.request as _uu, json as _jj
        _oi_resp = _jj.loads(_uu.urlopen(f'https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}', timeout=3).read())
        _pr_resp = _jj.loads(_uu.urlopen(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}', timeout=3).read())
        _ls_resp = _jj.loads(_uu.urlopen(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1', timeout=3).read())
        _oi_val = float(_oi_resp.get('openInterest', 0))
        _pr_val = float(_pr_resp.get('price', 0))
        _short_ratio = 1 - float(_ls_resp[0].get('longAccount', 0.5)) if _ls_resp else 0.5
        _ssi_r = compute_ssi(symbol, _short_ratio, _oi_val, _pr_val)
        if _ssi_r:
            _ssi_val = _ssi_r.get('ssi', 0)
            _ssi_name, _ssi_desc = _ssi_level_fn(_ssi_val)
            _result['ssi_risk'] = _ssi_name
            _result['ssi_val'] = round(_ssi_val, 4)
            if _ssi_name in ('HIGH', 'EXTREME') and signal_dir == 'SHORT':
                _ssi_penalty = -20 if _ssi_name == 'EXTREME' else -12
                _result['score'] = round(_result.get('score', 0) + _ssi_penalty, 1)
                _result['breakdown']['SSI轧空降权'] = f'{_ssi_name} ssi={_ssi_val:.3f} → {_ssi_penalty}分'
    except Exception:
        pass

    # 2. IC权重乘数注入（来自signal_weights.json）
    try:
        import json as _sw_json
        _sw_path = _BASE_DIR / 'data' / 'signal_weights.json'
        if _sw_path.exists():
            _sw = _sw_json.loads(_sw_path.read_text()).get('weights', {})
            _cur_regime = _result.get('regime', '')
            _cur_score = _result.get('score', 0)
            # 按体制+方向+score区间查权重
            _sw_key = None
            if _cur_score >= 155: _sw_key = f'{_cur_regime}:{signal_dir}:155+'
            elif _cur_score >= 140: _sw_key = f'{_cur_regime}:{signal_dir}:140-154'
            elif _cur_score >= 120: _sw_key = f'{_cur_regime}:{signal_dir}:120-139'
            if _sw_key and _sw_key in _sw:
                _mult = float(_sw[_sw_key].get('multiplier', 1.0))
                if abs(_mult - 1.0) > 0.05:
                    _result['ic_weight_mult'] = _mult
                    _result['score'] = round(_cur_score * _mult, 1)
                    _result['breakdown']['IC权重乘数'] = f'{_sw_key} ×{_mult}'
    except Exception:
        pass
    # ─────────────────────────────────────────────────────────────────────────

    # ── [TradFi信号层 Phase A 2026-07-22 设计院自主] 标签模式（不改score）────
    # Phase A: 仅将TradFi信号注入breakdown标签，积累数据供达摩院验证
    # Phase B/C: 达摩院统计验证50+条后升级为score注入
    try:
        from brahma_brain.tradfi_signal_layer import compute_tradfi_context
        from brahma_brain.us_session_gate import get_us_session, get_session_regime_delta
        _ssi_pen = 0
        if _result.get('ssi_risk') == 'EXTREME': _ssi_pen = -20
        elif _result.get('ssi_risk') == 'HIGH': _ssi_pen = -12
        _tradfi = compute_tradfi_context(
            symbol=symbol,
            direction=signal_dir,
            base_score=_result.get('score', 0),
            regime=_result.get('regime', ''),
            ssi_penalty=_ssi_pen,
        )
        # 注入breakdown标签（Phase A: delta=0，不修改score）
        _result.setdefault('breakdown', {})['TradFi'] = _tradfi.get('breakdown_label', '')
        _result['tradfi_phase'] = _tradfi.get('phase', 'A')
        # 美股时段标签
        _sess = get_us_session()
        _result['us_session'] = _sess.get('session', 'unknown')
        # Phase A下的时段block仅作标注（不硬性block，避免误伤）
        if _sess.get('is_open_window'):
            _result.setdefault('breakdown', {})['US时段'] = '⚠️ 开盘冲击波窗口 14:00-14:45 UTC（Phase A观察中）'
        # 补丁1: CHOP_HIGH死穴重封（防止未来Phase B意外绕过）
        if _tradfi.get('need_chop_high_recheck') and _result.get('score', 0) > 75:
            _result['score'] = 75
            _result.setdefault('breakdown', {})['CHOP_HIGH_REAPPLY'] = '死穴重封≤75'
    except Exception:
        pass  # TradFi层失败不阻断主链路
    # ─────────────────────────────────────────────────────────────────────────

    # ══ [P0-1 设计院封印 2026-07-23 苏摩111] consensus=FULL_BEAR 封禁 LONG ══
    # 根因: regime=BULL_TREND 会覆盖 consensus，强行产出多单
    # 当六方推理结论为 FULL_BEAR 时，LONG 方向直接封禁
    # 精英解锁通道: score≥170 AND structure_grade≥90 AND RSI_1H<35 → 0.5%NAV观察仓
    try:
        _consensus_val = str(_result.get('consensus', '') or '').upper()
        _dir_val       = str(_result.get('direction', signal_dir or '') or '').upper()
        if 'FULL_BEAR' in _consensus_val and 'LONG' in _dir_val:
            _elite_score  = float(_result.get('score_final', _result.get('score', 0)) or 0)
            _elite_struct = float(_result.get('structure_grade', 0) or 0)
            _elite_rsi    = float(_result.get('rsi_1h', 99) or 99)
            _elite_unlock = _elite_score >= 170 and _elite_struct >= 90 and _elite_rsi < 35
            if not _elite_unlock:
                _result['valid_signal']   = False
                _result['globally_blocked'] = True
                _result['blocked_reason'] = (
                    f'consensus=FULL_BEAR封禁LONG '
                    f'(score={_elite_score:.0f} struct={_elite_struct:.0f} rsi_1h={_elite_rsi:.1f})'
                )
                _result.setdefault('breakdown', {})['consensus门控'] = (
                    f'🚫 FULL_BEAR→LONG封禁 '
                    f'[精英解锁需score≥170+struct≥90+RSI_1H<35]'
                )
            else:
                # 精英解锁：降级为观察仓标注
                _result.setdefault('breakdown', {})['consensus门控'] = (
                    f'⚡ FULL_BEAR精英解锁 score={_elite_score:.0f} '
                    f'struct={_elite_struct:.0f} RSI={_elite_rsi:.1f} → 0.5%NAV观察仓'
                )
                _result['action'] = 'WATCH_ONLY_0.5NAV'
    except Exception:
        pass
    # ══════════════════════════════════════════════════════════════════════════

    # ══ [P1 设计院封印 2026-07-23] BRAHMA output_tag 写入修复 ══
    # 根因: 5条SNDK信号全部缺失 [BRAHMA:...] 标签，无法溯源校验
    # 修复: 在 return 前确保 output_tag 含有效 BRAHMA 标签
    try:
        import hashlib as _hlib
        _ot = str(_result.get('output_tag', '') or '')
        if '[BRAHMA:' not in _ot:
            # 重新生成标签
            _tag_score  = _result.get('score_final', _result.get('score', 0))
            _tag_dir    = _result.get('direction', signal_dir or 'LONG')
            _tag_regime = _result.get('regime', 'UNKNOWN')
            _tag_sid    = str(_result.get('signal_id', '') or '')[:8]
            _tag_ts     = str(int(_result.get('ts', 0) or 0))
            _tag_level  = (
                'DIVINE' if (_tag_score or 0) >= 170 else
                'ELITE'  if (_tag_score or 0) >= 155 else
                'STRONG' if (_tag_score or 0) >= 138 else 'WATCH'
            )
            _tag_src    = 'brahma_engine'
            _tag_raw    = f'{symbol}:{_tag_dir}:{_tag_regime}:{_tag_ts}:{_tag_sid}'
            _tag_sha8   = _hlib.sha256(_tag_raw.encode()).hexdigest()[:8]
            _brahma_tag = (
                f'[BRAHMA:{_tag_level}:{_tag_src}:{symbol}:'
                f'{int(_tag_score or 0)}:{_tag_dir}:{_tag_regime}:'
                f'{_tag_ts}:{_tag_sha8}]'
            )
            _result['output_tag'] = _brahma_tag
    except Exception:
        pass
    # ══════════════════════════════════════════════════════════════════════════
    # [Kronos环境感知器] 将badge注入最终结果
    try:
        _result['kronos_env'] = _kronos_env_badge
        if 's23_p_up' not in _result:
            _result['kronos_p_up'] = ms.get('s23_p_up', None) if ms else None
        else:
            _result['kronos_p_up'] = _result.get('s23_p_up')
    except Exception:
        pass

    return _result

def format_report(r: dict) -> str:
    """[shim] 已迁移到 brahma_brain/formatter.py · v25.0"""
    from brahma_brain.formatter import format_report as _fmt


# ============================================================
# [第36维 Phase3 2026-07-24 设计院·苏摩确认]
# 多时间框架一致性层 (Multi-Timeframe Alignment Layer)
# 解决根因：1H体制信号被错误外推为宏观底部判断
# ============================================================
def _calc_mtf_alignment(symbol: str, regime_1h: str, score: float,
                         cf: dict = None) -> dict:
    """
    计算多时间框架一致性，防止时间框架混淆。
    返回 mtf_result 注入 confluence breakdown。
    """
    import urllib.request, json as _json
    try:
        def _rsi(closes, period=14):
            gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
            losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
            ag = sum(gains[-period:])/period if gains else 1
            al = sum(losses[-period:])/period if losses else 1
            return round(100-100/(1+ag/al), 1) if al else 100

        def _get_closes(sym, interval, limit=20):
            is_futures = True
            try:
                url = f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}'
                r = urllib.request.urlopen(url, timeout=4)
                return [float(k[4]) for k in _json.loads(r.read())]
            except:
                return []

        closes_1d = _get_closes(symbol, '1d', 20)
        closes_1w = _get_closes(symbol, '1w', 20)

        rsi_1d = _rsi(closes_1d) if closes_1d else 50
        rsi_1w = _rsi(closes_1w) if closes_1w else 50

        # 判断各层方向
        dir_1h = 'BULL' if 'RECOVERY' in regime_1h or 'BULL' in regime_1h else 'BEAR'
        dir_1d = 'BULL' if rsi_1d > 50 else 'BEAR'
        dir_1w = 'BULL' if rsi_1w > 50 else 'BEAR'

        aligned = (dir_1h == dir_1d == dir_1w)
        mixed   = not aligned

        if aligned and dir_1h == 'BULL':
            alignment = 'ALL_ALIGNED_BULL'
            label = '✅多时间框架共振做多'
            confidence = 'HIGH'
            ttf_note = '1H+1D+1W均偏多，宏观支撑'
        elif aligned and dir_1h == 'BEAR':
            alignment = 'ALL_ALIGNED_BEAR'
            label = '✅多时间框架共振做空'
            confidence = 'HIGH'
            ttf_note = '1H+1D+1W均偏空，宏观顺势'
        elif dir_1h != dir_1w:
            alignment = 'SHORT_ONLY'
            label = '⚠️仅短线有效，宏观趋势相反'
            confidence = 'LOW'
            ttf_note = f'1H={dir_1h} vs 1W={dir_1w}，时间框架冲突'
        else:
            alignment = 'MIXED'
            label = '⚠️混合信号，中等置信度'
            confidence = 'MEDIUM'
            ttf_note = f'1H={dir_1h} 1D={dir_1d} 1W={dir_1w}'

        return {
            'alignment': alignment,
            'label': label,
            'confidence': confidence,
            'rsi_1d': rsi_1d,
            'rsi_1w': rsi_1w,
            'dir_1h': dir_1h,
            'dir_1d': dir_1d,
            'dir_1w': dir_1w,
            'ttf_note': ttf_note,
            'valid_for_macro': aligned,  # 是否可用于宏观级别决策
        }
    except Exception as e:
        return {
            'alignment': 'UNKNOWN', 'label': '无法获取多周期数据',
            'confidence': 'LOW', 'rsi_1d': 50, 'rsi_1w': 50,
            'valid_for_macro': False, 'ttf_note': str(e)[:50]
        }

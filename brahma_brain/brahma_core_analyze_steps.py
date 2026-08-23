#!/usr/bin/env python3
"""
brahma_core_analyze_steps.py — analyze() Step1-3 helper函数
[设计院封印 2026-08-11 苏摩111]

从 brahma_core.analyze() 提取，降低函数体行数
每个 helper 接受 (symbol, ...) 并返回 dict，由 analyze() 解包使用

analyze() 调用方式:
    r1 = _analyze_step1(symbol, ms, signal_dir)
    ms = r1['ms']; _cv_adj = r1['_cv_adj']; _causal_v_result = r1['_causal_v_result']

    r2 = _analyze_step2(symbol, ms, signal_dir, deep)
    signal_dir = r2['signal_dir']

    r3 = _analyze_step3(symbol, ms, signal_dir, price)
    smc = r3['smc']; _smc_4h = r3['_smc_4h']; _mtf_result = r3['_mtf_result']
"""


# 依赖：从 brahma_core 同目录导入（Step1-3需要的所有模块）
import os as _steps_os, sys as _steps_sys

_BRAIN_DIR = _steps_os.path.dirname(_steps_os.path.abspath(__file__))
_SCRIPTS_DIR = _steps_os.path.join(_BRAIN_DIR, '..', 'scripts')
for _p in [_BRAIN_DIR, _SCRIPTS_DIR]:
    if _p not in _steps_sys.path:
        _steps_sys.path.insert(0, _p)

try:
    from market_state import analyze as ms_analyze
except ImportError:
    def ms_analyze(symbol):
        return {'error': f'market_state not available for {symbol}'}

try:
    from smc_engine import analyze_smc
except ImportError:
    def analyze_smc(symbol, direction, tf, limit):
        return {}


def _analyze_step1(symbol: str, signal_dir: str) -> dict:
    """
    Step1: 市场状态分析
      - RegimeStateMachine 体制防抖
      - CausalVerifier 体制因果验证
    输入: symbol, signal_dir (ms由内部ms_analyze获取)
    输出: {ms, _cv_adj, _causal_v_result}
    """
    _sym = symbol
    ms = None
    _cv_adj = 0
    _causal_v_result = {}
    _cv_verdict = '?'

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
            if _rsm_path not in _rsm_sys.path: _rsm_sys.path.insert(0, _rsm_path)
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

    # ── [ROOT-FIX-3 2026-08-23 苏摩111封印] 实时体制覆盖门控 ─────────────────
    # 根因：brahma_state.json刷新有延迟，价格单边大跌时系统仍发BULL_TREND多单
    # 修复：实时检查过去4H的实际价格跌幅，超阈值强制覆盖体制
    # fail-safe：异常不阻断，维持当前体制
    try:
        _cur_regime = ms.get('regime', '')
        # 只在多头体制下检查，防止在已经是BEAR体制时重复覆盖
        if _cur_regime in ('BULL_TREND', 'BULL_EARLY', 'BULL_PEAK', 'BULL_CORRECTION'):
            import requests as _rtc_req
            _rtc_url = f'https://api.binance.com/api/v3/klines?symbol={_sym}&interval=4h&limit=3'
            _rtc_resp = _rtc_req.get(_rtc_url, timeout=3)
            if _rtc_resp.status_code == 200:
                _rtc_klines = _rtc_resp.json()
                if len(_rtc_klines) >= 2:
                    _rtc_open = float(_rtc_klines[0][1])   # 最早4H K线的开盘价
                    _rtc_cur  = float(_rtc_klines[-1][4])  # 最新K线的收盘价
                    _rtc_drop = (_rtc_open - _rtc_cur) / _rtc_open if _rtc_open > 0 else 0
                    if _rtc_drop > 0.08:  # 4H内跌超8%
                        ms['regime'] = 'BEAR_TREND'
                        ms['_rtc_override'] = f'实时覆盖: {_cur_regime}→BEAR_TREND drop={_rtc_drop:.1%}'
                    elif _rtc_drop > 0.05:  # 4H内跌超5%
                        ms['regime'] = 'BEAR_EARLY'
                        ms['_rtc_override'] = f'实时覆盖: {_cur_regime}→BEAR_EARLY drop={_rtc_drop:.1%}'
    except Exception:
        pass  # 实时体制覆盖异常，不阻断
    # ── [ROOT-FIX-3 END] ─────────────────────────────────────────────────

    # ── [因果AI P0-A] Causal Regime Verifier ────────────────────
    # 设计院因果增强 v1.0 · 2026-06-18
    # 在 Step 2 方向确认前，验证当前体制的因果结构是否支持入场
    # fail-safe: 异常时返回默认通过，不阻断主流程
    _causal_v_result = {}
    try:
        import sys as _cv_sys, os as _cv_os
        _cv_root = _cv_os.path.dirname(_cv_os.path.abspath(__file__))
        if _cv_root not in _cv_sys.path:
            if _cv_root not in _cv_sys.path: _cv_sys.path.insert(0, _cv_root)
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


    return {
        'ms': ms,
        '_cv_adj': _cv_adj,
        '_cv_verdict': _cv_verdict,
        '_causal_v_result': _causal_v_result,
    }


def _analyze_step2(symbol: str, ms: dict, signal_dir, deep: bool) -> dict:
    """
    Step2: 确定交易方向
      - NEUTRAL体制深度模式处理
      - 方向确认日志
    输入: symbol, ms, signal_dir, deep
    输出: {signal_dir}
    """
    _sym = symbol

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


    return {'signal_dir': signal_dir}


def _analyze_step3(symbol: str, ms: dict, signal_dir: str, price: float) -> dict:
    """
    Step3: SMC结构分析
      - 1H + 4H SMC分析
      - MTF路由器（自顶向下）
    输入: symbol, ms, signal_dir, price
    输出: {smc, _smc_4h, _mtf_result, price}
    """
    _sym = symbol
    smc = {}
    _smc_4h = {}
    _mtf_result = None

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


    return {
        'smc': smc,
        '_smc_4h': _smc_4h,
        '_mtf_result': _mtf_result,
        'price': price,
    }

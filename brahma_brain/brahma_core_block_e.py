#!/usr/bin/env python3
# ponytail: brahma_core_block_e _result装配层，纯移动自brahma_core.analyze L2511-L2636
# [P4封印 2026-10-01 苏摩111] 接入位置：brahma_brain/brahma_core.py analyze

import time as _time_be, time

def build_result_dict(symbol: str, ms: dict, smc: dict, cf: dict, params: dict,
                      signal_dir: str, extra_data: dict, _score: int, elapsed: float,
                      _data_health: dict, _dharma_nodes: dict, _valid: bool) -> dict:
    """
    构建result顶层dict（纯移动零逻辑改动）
    """
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
        'data_health': _data_health,  # [2026-09-16 FinanceMCP借鉴] 数据健康检查+降级标记
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
        # [2026-08-12 苏摩111封印 v3] ms完整原始数据注入，全路径修正版，供94维逐项核对
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
            'utc_hour':       __import__('datetime').datetime.now(__import__('datetime').timezone.utc).hour,
            'weekday':        __import__('datetime').datetime.now(__import__('datetime').timezone.utc).weekday(),
            'month':          __import__('datetime').datetime.now(__import__('datetime').timezone.utc).month,
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
        # [9.21苏摩设计院] extra_data叠叠加权：Causal/FR/PC/宏观/聪明钱的扣分从cf['score']注入score_final
        # 根因：cf['score']=138+叠加，cf['total']=138，但score_final只用_score(cf['total']路径)
        # 导致Causal -5 / FR -8 / PC -2的扣分从未进入最终输出
        # 修复：将cf['score']-cf['total']的差值注入score_final初始值
        'score_final_raw': _score,
        # [v25.4c effective_grade] 体制感知grade写入顶层，供offline_replay使用
        'grade':          int(cf.get('structure_grade', 0) or 0),
        'effective_grade': round(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0), 1),
        'grade_mult':      round(float(cf.get('grade_mult', 1.0) or 1.0), 2),
    }
    return _result

#!/usr/bin/env python3
# ponytail: brahma_core_block_f 达摩因子引擎+15m信号层，纯移动自brahma_core.analyze L2364-L2488
# [P4封印 2026-10-01 苏摩111] 接入位置：brahma_brain/brahma_core.py analyze

def apply_dfe_and_15m(ms: dict, smc: dict, cf: dict, params: dict, signal_dir: str, _sym: str, _score: float, _score_raw: float) -> dict:
    """达摩因子引擎(dharma_factor_engine)标准化落地 + 15m信号层(P1-B)。
    外源: ms, smc(只读), cf(读写total/breakdown), params(只读), signal_dir, _sym
    入参分数: _score(当前值), _score_raw(当前值)
    返回: {'score':.., 'score_raw':.., 'cf':cf}（cf原地修改+返回引用）
    """
    import sys as _sys_f
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
        print(f"[WARN] brahma_core: {_dfe_e}", file=sys.stderr)

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
        print(f"[WARN] brahma_core: _15m_e", file=sys.stderr)
    return {"score": _score, "score_raw": _score_raw, "cf": cf}

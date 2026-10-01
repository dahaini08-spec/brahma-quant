#!/usr/bin/env python3
# ponytail: brahma_core_block_d N17专项SL/TP覆写层，纯移动自brahma_core.analyze L987-L1073
# [P4封印 2026-10-01 苏摩111] 接入位置：brahma_brain/brahma_core.py analyze

def apply_n17_override(ms: dict, params: dict, signal_dir: str, _sym: str) -> dict:
    """N17专项参数覆写：符号专属sl/tp/mh + 体制动态SL + RR重算。
    外源: ms(regime/price/momentum.atr_1h), params(entry_lo/hi/valid), signal_dir, _sym
    返回: 覆写后的params dict（纯移动零逻辑改动）
    """
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
    return params

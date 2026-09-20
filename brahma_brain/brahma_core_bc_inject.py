"""
brahma_core_bc_inject.py
[B类模块接入 2026-08-09] + [C类孤岛模块接入 2026-08-09] + [cross_asset_gate 2026-08-12]

从 brahma_core.py analyze() 中提取。
接入位置：post_inject之后、TradFi router之前。
接口：inject_bc_modules(_result, ms, score, breakdown, signal_dir, symbol, extra_data) -> (_result, score, breakdown)
"""
import sys
from typing import Any


def inject_bc_modules(_result: dict, ms: dict, score: int, breakdown: dict,
                      signal_dir: str, symbol: str, extra_data: dict = None) -> tuple[dict, int, dict]:
    """
    B类模块接入 + cross_asset_gate + C类孤岛模块接入 + OI四象限
    """
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
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
                except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
        print(f"[WARN] brahma_core_bc_inject: _ssi_e", file=sys.stderr)

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
        pass  # 宏观层失败静默降级，不阻断主链

    # B2: brahma_coordinator — 子系统上下文聚合
        pass  # coordinator失败不阻断

    # B3: signal_integrity_gate — P0~P2 信号完整性校验
    try:
        from brahma_brain.signal_quality_engine import gate_check as _gate_fn
        _cf_gate = _result.get('confluence', {})
        _params_gate = _result.get('params', {})
        _ms_gate = _result.get('momentum', {})
        _gate_ok, _gate_reason = _gate_fn(_cf_gate, _params_gate, _ms_gate)
        _result['integrity_gate'] = {'passed': _gate_ok, 'reason': _gate_reason}
        if not _gate_ok:
            # 完整性校验失败 → score强制降权，不硬封禁（不改decision）
            _result['score_final'] = (_result.get('score_final') or 0) - 10
            _result.setdefault('breakdown_extra', {})['integrity_gate'] = -10
    except Exception as _e:
        print(f"[WARN] brahma_core_bc_inject: _e", file=sys.stderr)

    # B4: mode_c_detector — 庄家行情识别，高波动假信号过滤
        pass  # mode_c失败不阻断



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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # C4: tradfi_dump_detector — TradFi/美股代币放量抛售检测
    # [接入位置 2026-08-29 苏摩111] 建了未接入，今日修复
    try:
        from brahma_brain.tradfi_signal_layer import analyze_tradfi_dump as _td_fn, is_tradfi_token as _is_tf
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # C5: market_quadrant — 四象限市场状态评分
    # [P0接入 2026-08-29 苏摩111] 接入位置: brahma_core block_b C5
    # 铁证: LSR>65%+大户净空 = 多头拥挤象限Q2 → score-15; LSR<35%+大户净多 = 空头拥挤Q4 → score+12
    try:
        from brahma_brain.regime_scorer import get_quadrant as _mq_fn
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ══ [C6 OI四象限联合判断 2026-08-30 苏摩111] ═══════════════════════════
    # 铁证：BTC/ETH量化文献综述 — OI+价格+费率三维联合判断优于单一OI
    # 四象限定义（来自量化微观结构文献）：
    #   Q_LONG_CROWD : 价涨+OI增+费率升 → 多头拥挤，做多扣分
    #   Q_SHORT_CROWD: 价跌+OI增+费率负 → 空头拥挤，做空扣分
    #   Q_SHORT_COVER: 价涨+OI降 → 空头回补，燃料有限，做多注意
    #   Q_LONG_EXIT  : 价跌+OI降 → 多头平仓，做空注意
    try:
        _sent_oi  = (_result.get('_market_state') or {})
        _oi_chg   = float((_result.get('_sentiment') or {}).get('oi_change_pct', 0) or 0)
        _fr_oi    = float((_result.get('_sentiment') or {}).get('funding_rate', 0) or 0)
        _px_chg   = float(_result.get('price_change_pct_4h', 0) or 0)
        _dir_oi   = _result.get('signal_dir', 'LONG')
        _oi_delta = 0
        _oi_quad  = 'NEUTRAL'

        if _oi_chg > 2.0 and _px_chg > 1.0 and _fr_oi > 0.005:
            # Q_LONG_CROWD: 价涨+OI增+费率正 → 多头拥挤
            _oi_quad = 'LONG_CROWD'
            if _dir_oi == 'LONG':  _oi_delta = -5   # 追多时扣分
            else:                   _oi_delta = +3   # 做空时加分（轧多潜力）
        elif _oi_chg > 2.0 and _px_chg < -1.0 and _fr_oi < -0.002:
            # Q_SHORT_CROWD: 价跌+OI增+费率负 → 空头拥挤
            _oi_quad = 'SHORT_CROWD'
            if _dir_oi == 'SHORT': _oi_delta = -5   # 追空时扣分
            else:                   _oi_delta = +3   # 做多时加分（轧空潜力）
        elif _oi_chg < -2.0 and _px_chg > 1.0:
            # Q_SHORT_COVER: 价涨+OI降 → 空头回补，燃料有限
            _oi_quad = 'SHORT_COVER'
            if _dir_oi == 'LONG':  _oi_delta = -2   # 做多时轻微扣分（回补燃料耗尽风险）
        elif _oi_chg < -2.0 and _px_chg < -1.0:
            # Q_LONG_EXIT: 价跌+OI降 → 多头平仓
            _oi_quad = 'LONG_EXIT'
            if _dir_oi == 'SHORT': _oi_delta = -2   # 做空时轻微扣分（砸盘动力衰减）

        if _oi_delta != 0:
            _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _oi_delta, 1)
            _result['score'] = _result['score_final']
            _result.setdefault('breakdown_extra', {})['oi_quadrant'] = (
                f'{_oi_delta:+d}({_oi_quad} OI={_oi_chg:+.1f}% PX={_px_chg:+.1f}% FR={_fr_oi:.4f})'
            )
        _result['_oi_quadrant'] = _oi_quad
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ══ [P0 设计院封印 2026-08-11 苏摩111] TRADFI交易时段门控 ══════════════
    return _result, score, breakdown

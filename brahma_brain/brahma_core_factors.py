"""
brahma_core_factors.py
[设计院 2026-06-30] CVD/HAR-RV/Hurst/VolumeProfile/清算流 因子层

从 brahma_core.py confluence_score() 中提取。
接入位置：N_REPLAY之后、return之前。
接口：calc_factors(ms, signal_dir, score, breakdown, extra_data, _result, symbol) -> (score, breakdown)
"""
import sys
from typing import Any


def calc_factors(ms: dict, signal_dir: str, score: int, breakdown: dict,
                 extra_data: dict, _result: dict, symbol: str) -> tuple[int, dict]:
    """
    CVD订单流 + HAR-RV波动率 + Hurst体制验证 + Volume Profile + 实时清算流
    + 逆势最终惩罚
    只修改 score 和 breakdown
    """
    import sys
    # ══ [设计院 2026-06-30 全量接入 N10-A] CVD 订单流因子 ════════════════════
    # 模块: cvd_engine · 订单流核心指标，多周期CVD累积成交量差
    # 达摩院铁证：CVD顺势+15分 / 逆势-10分
    try:
        # [Fix 2026-09-01] cvd_engine在brahma_brain/，修复路径问题
        try:
            from brahma_brain.volume_unified import cvd_score_for_signal as _cvd_fn
        except ImportError:
            from brahma_brain.volume_unified import cvd_score_for_signal as _cvd_fn
        _cvd_score, _cvd_notes = _cvd_fn(ms.get('symbol', ''), signal_dir)
        if _cvd_score != 0:
            # [2026-08-30 苏摩111] ETH订单流权重放大：arXiv铁证ETH盘口状态依赖更强
            try:
                from brahma_brain.regime_config import get_order_flow_mult as _of_mult
                _of_factor = _of_mult(ms.get('symbol', ''))
                if _of_factor != 1.0:
                    _cvd_score = round(_cvd_score * _of_factor)
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
            score += _cvd_score
            breakdown['CVD订单流'] = f'{_cvd_score:+d} ' + ('; '.join(_cvd_notes[:2]) if _cvd_notes else '')
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
        import sys as _sys_harv; print(f"[brahma_core] HAR-RV计算失败: {_harv_e}", file=_sys_harv.stderr)

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
        print(f"[WARN] brahma_core_factors: _hurst_e", file=sys.stderr)

    # ══ [设计院 2026-08-12 苏摩111封印] Volume Profile成交量分布接入 ══
    # 根因：volume_profile.py存在但未接入，POC价格磁力区信息缺失
    try:
        from brahma_brain.volume_unified import get_vp_score as _vp_fn
        _vp_price = float(ms.get('close', ms.get('price', 0)) or 0)
        _vp_score, _vp_reason = _vp_fn(_sym, _vp_price, signal_dir)
        if _vp_score != 0:
            score += _vp_score
            breakdown['VolProfile密度'] = f'{_vp_score:+d} {_vp_reason[:40]}'
    except Exception as _vp_e:
        print(f"[WARN] brahma_core_factors: _vp_e", file=sys.stderr)

    # ══ [设计院 2026-06-30 全量接入 N10-B] 实时清算流 因子 ════════════════════
    # 模块: realtime_liq_tracker · 追踪近5分钟三所清算流方向
    # 逻辑：同向清算涌入（如大量多单被爆仓时做空）→ 加分
    try:
        from brahma_brain.liq_density_engine import get_liq_score as _liq_score_fn
        _liq_adj, _liq_desc = _liq_score_fn(ms.get('symbol', ''), signal_dir)
        if _liq_adj != 0:
            score += _liq_adj
            breakdown['清算流追踪'] = f'{_liq_adj:+d} {_liq_desc[:50]}'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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

    # [果蝇Phase 0] 维度级trace写入 — 每个维度的最终分数和状态
    try:
        from brahma_brain.dim_trace_writer import trace_dim as _trace
        _sym_for_trace = (ms.get('symbol') or '').upper()
        _regime_for_trace = str(ms.get('regime', '')).upper()
        for _dn, _ds in [('s1', s1), ('s2', s2), ('s3', s3), ('s4', s4),
                        ('s5', s5), ('s5b', s5b), ('s6', s6),
                        ('s7', s7), ('s8', s8), ('s9', s9), ('s10', s10),
                        ('s11', s11), ('s12', s12), ('s13', s13), ('s14', s14),
                        ('s15', s15), ('s16', s16), ('s17', s17), ('s18', s18),
                        ('s19', s19), ('s20', s20), ('s21', s21), ('s22', s22)]:
            _status = 'sleep' if _ds == 0 else 'ok'
            _trace(_dn, {'symbol': _sym_for_trace, 'regime': _regime_for_trace, 'dir': signal_dir},
                   {'score': _ds}, _status, 0)
    except Exception as _e:
        print(f"[WARN] brahma_core_factors: _e", file=sys.stderr)

    # ── [FIX-3 2026-09-18] 逆势方向最终惩罚 — return前最后一步 ──────────
    # 根因：Phase C取消score乘数后，逆势RSI超卖加分导致BEAR_TREND LONG>SHORT
    # 修复：在所有加分层完成后对逆势组合施加×0.68（对应WR=45%信息比）
    _is_counter_trend_final = (
        ('BEAR_TREND' in str(breakdown.get('_regime', '')).upper() and
         str(signal_dir).upper() in ('LONG', '做多', 'UP')) or
        ('BULL_TREND' in str(breakdown.get('_regime', '')).upper() and
         str(signal_dir).upper() in ('SHORT', '做空', 'DOWN'))
    )
    if _is_counter_trend_final and score > 0:
        score = int(score * 0.68)
        breakdown['_counter_trend_penalty'] = 'FIX-3: 逆势最终惩罚×0.68'
    return score, breakdown

"""
brahma_core_fangcang_inject.py
[设计院 2026-08-09 苏摩111封印] 方仓向量WR + 40年经验引擎 + RSI分层 + HTF锚定 + 三周期RSI共振

从 brahma_core.py analyze() 中提取。
接入位置：PositionSizer之后、TradFi之前。
接口：inject_fangcang(_result, ms, score, breakdown, signal_dir, symbol) -> (_result, score, breakdown)
"""
import sys
from typing import Any


def inject_fangcang(_result: dict, ms: dict, score: int, breakdown: dict,
                    signal_dir: str, symbol: str) -> tuple[dict, int, dict]:
    """
    方仓向量WR → score_final 架构接线
    + 40年经验引擎注入
    + P1 方仓RSI分层
    + HTF周月线锚定
    + P4 三周期RSI共振
    """
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
                from brahma_brain.fangcang_engine import unified_fangcang as _uf
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
            _result['hcme_ctx']    = str(_hcme_ctx)[:200]  # [2026-09-12] 120→200 避免截断
    except Exception as _fc_e:
        import logging as _lg; _lg.getLogger('brahma').warning(f'[fangcang] {_fc_e}')
        _result['fangcang'] = {'status': 'unavailable', 'reason': str(_fc_e)[:60]}

    # [设计院 2026-08-25 苏摩111] 长期记忆注入：跨资产20年知识库
        import logging as _lm_log; _lm_log.getLogger('brahma').debug(f'[longmem] {_fc_e}')

    # ══ [N_EXP 2026-08-29 苏摩111] 40年经验引擎注入 ══════════════════════════
    # 使命：把20392条6.5年K线蒸馏的经验矩阵实时注入评分
    # 接入：longmem之后，decision_engine之前
    try:
        from brahma_brain.fangcang_engine import get_exp_adj as _exp_fn
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

    try:
        from brahma_brain.brahma_decision_engine import decide as _dt_decide
        _dt_signal = {
            'symbol':    _result.get('symbol', _sym),
            'direction': (_result.get('direction') or signal_dir or 'LONG') if _result.get('direction') not in ('NEUTRAL','NONE','') else (signal_dir or 'LONG'),  # [9.17] NEUTRAL→fallback
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        if _htf_addon != 0:
            _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + _htf_addon, 1)
            _result['score'] = _result['score_final']
        # 始终写入breakdown，让共振值可见
        if _htf_data:
            _result.setdefault('confluence', {}).setdefault('breakdown', {})\
                .update({'HTF周月线锚定': f'{_htf_addon:+d} ({_htf_bias} 共振={_htf_res:.2f})'})
            _result['htf_score_addon'] = _htf_addon
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
            'SPCXUSDT',  # [P0-A 2026-08-30 苏摩111] SPX指数代币，补入方仓TradFi DB
        ])  # TSLAUSDT降为B级暂移出 / MSTRUSDT单独处理
        _mstr_tokens = {'MSTRUSDT'}  # MSTR专属：权重±3（降半）
        if _sym in _tradfi_tokens or _sym in _mstr_tokens:
            from brahma_brain.fangcang_engine import query_tradfi as _tfi_q
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)

    # ══ [V2.0 2026-09-20 苏摩111] TradFi跨市场参照 + 212K经验库 + 亏损记忆 ═══════
    return _result, score, breakdown

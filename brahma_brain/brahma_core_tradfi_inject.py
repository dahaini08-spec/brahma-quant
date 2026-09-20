"""
brahma_core_tradfi_inject.py
[P0 设计院封印 2026-08-11] TRADFI交易时段门控 + sector_corr + macro_link + TradFi三类路由器

从 brahma_core.py analyze() 中提取。
接入位置：BC模块之后、return之前。
接口：inject_tradfi(_result, ms, score, breakdown, signal_dir, symbol, extra_data) -> (_result, score, breakdown)
"""
import sys
from typing import Any


def inject_tradfi(_result: dict, ms: dict, score: int, breakdown: dict,
                  signal_dir: str, symbol: str, extra_data: dict = None) -> tuple[dict, int, dict]:
    """
    TradFi交易时段门控 + sector_corr + macro_link + TradFi三类路由器
    + 最终SL/TP覆写
    """
    # ══ [P0 设计院封印 2026-08-11 苏摩111] TRADFI交易时段门控 ══════════════
    # 美股代币非交易时段(亚洲白天)流动性极低，发信号有执行风险
    # UTC 13:30~20:00 = 北京21:30~04:00 = 美股正常交易时段
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK':
            import datetime as _dt_trd
            _utc_now = _dt_trd.datetime.now(timezone.utc)
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ══ [设计院 2026-08-11 苏摩111] TRADFI整体落地：sector_corr + macro_link ══
    # 仅在交易时段内（valid未被时段门控清除）才执行联动/宏观门控
    # 避免非交易时段已valid=False时继续消耗计算资源
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK' and _result.get('tradfi_in_session', True):
            _direction = _result.get('direction', 'LONG')

            # ── sector_corr：板块联动评分 ─────────────────────────────────────
            from brahma_brain.tradfi_signal_layer import (
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
            from brahma_brain.tradfi_signal_layer import compute_tradfi_macro_gate as _macro_fn
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

    # ══ [设计院封印 2026-08-14 苏摩111] TradFi三类路由器接入 ═════════════════
    # 验证铁证: A类 WR+9.1pp PNL-3.3%→+12.5% | 铁律1/2/3差异化评分
    try:
        if _result.get('asset_type') == 'TRADFI_STOCK':
            from brahma_brain.tradfi_signal_layer import compute_router_delta as _tr_fn
            from brahma_brain.tradfi_signal_layer import get_tradfi_report_header as _tr_hdr_fn
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
            'iso':       __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat() + 'Z',
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ── [梦天大脑 Layer A2+C3 注入 2026-08-25] ──────────────────────────────────
    # A2: 极端事件库风险注释
    try:
        from brahma_brain.brahma_experience_engine import get_extreme_risk_note as _ern
        _extreme_note = _ern(_sym)
        if _extreme_note:
            _result['extreme_risk_note'] = _extreme_note
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['extreme_event'] = _extreme_note
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # C3: 反脆弱性黑天鹅检测
    try:
        from antifragile_guard import full_guard_check as _fgc
        _guard = _fgc(_sym, _result.get('signal_dir', signal_dir or ''))
        _result['antifragile'] = _guard
        if _guard['warnings']:
            _result.setdefault('confluence', {}).setdefault('breakdown', {})['antifragile'] = ' | '.join(_guard['warnings'][:2])
        # [9.17 苏摩111] 不再block决策，只记录警告
        if False and _guard['blocked']:
            _result['decision_action'] = 'BLOCKED_GUARD'
            _result['decision_reason'] = f'[反脆弱性熔断] {_guard["warnings"][0] if _guard["warnings"] else "保护熔断"}'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
        if False:  # [9.17 苏摩111] 禁用signal_weights动态权重——11层门控之一，放开系统
            _sw_data    = _jsw.loads(_sw_path.read_text())
            _sw_weights = _sw_data.get('weights', {})
            _sw_regime  = _result.get('regime', '')
            _sw_dir     = _result.get('signal_dir', _result.get('direction', ''))
            _sw_score   = float(_result.get('score_final', 0) or 0)
            # 生成分段key: REGIME:DIR:TIER
            def _sw_tier(s) -> str:
                """sw tier"""
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
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # [Karpathy断言层 2026-08-31 苏摩111] 断言优于注释——防止regime=None时静默产生垃圾信号
    # 根因: regime字段如果为None/空字符串，后续所有体制相关逻辑会静默失效
    _valid_regimes = {
        'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION',
        'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
        'CHOP_MID', 'CHOP_LOW', 'CHOP_HIGH', 'UNKNOWN'
    }
    _final_regime = _result.get('regime', '')
    if not _final_regime:
        _result['regime'] = 'UNKNOWN'  # 防止None导致下游错误
    elif not any(_final_regime.startswith(r) for r in _valid_regimes):
        _result['_regime_nonstandard'] = True  # 子体制如BEAR_TREND_FRESH，打标记不修改

    # ── [Trader Brain 2026-09-11 苏摩111] 交易员大脑接入 ───────────────
    # 职责: 6层确定性决策，注入分析结果末尾（不修改score，只注入决策）
    # 接入位置: return前最后一步（SW权重+断言后）
    try:
        from brahma_brain.trader_brain import decide as _tb_decide
        _tb_r = _tb_decide(
            regime=_result.get('regime', 'CHOP_MID'),
            score=float(_result.get('score_final', _result.get('score', 0)) or 0),
            grade=float(_result.get('grade', 0) or 0),
            macro={'high_impact': bool(_result.get('confluence', {}).get('breakdown', {}).get('宏观压制'))},
            risk={'regime_state': _result.get('regime_state', 'GREEN'), 'nav_mult': 1.0},
            hurst=float(_result.get('confluence', {}).get('breakdown', {}).get('Hurst', 0.5) or 0.5),
            fvg=_result.get('fvg', {}),
            ob=_result.get('ob', {}),
            liq=_result.get('liq', {}),
            atr_1h=float(_result.get('atr_1h', 0) or 0),
            atr_4h=float(_result.get('atr_4h', 0) or 0),
            price=float(_result.get('price', 0) or 0),
            oi=_result.get('oi', {}),
            sm=_result.get('sm', {}),
            vol={
                **(_result.get('vol') or {}),
                'rsi_15m': (ms.get('momentum') or {}).get('rsi_15m', 50) or 50,
                'min_gex_price': (_result.get('confluence', {}) or {}).get('_gex_min', 0) or 0,
            },
            res={
                **(_result.get('resonance') or {}),
                'fangcang': _result.get('fangcang', {}),
            },
            symbol=_result.get('symbol', ''),
            b2_proximity=str((_result.get('confluence') or {}).get('b2_proximity', '') or ''),
        )
        _result['trader_brain'] = {
            'action': _tb_r.get('action'),
            'direction': _tb_r.get('direction'),
            'entry_lo': _tb_r.get('entry_lo'),
            'entry_hi': _tb_r.get('entry_hi'),
            'sl': _tb_r.get('sl'),
            'rr': _tb_r.get('rr'),
            'confidence': _tb_r.get('confidence'),
            'missing': _tb_r.get('missing', []),
        }
        # [2026-09-17 设计院S1修复] trader_brain action覆盖confluence_score action
        # 根因：format_full_report读c.get('action')即confluence_score.action，不是trader_brain.action
        # 修复：trader_brain是最终决策层，其action应覆盖confluence_score的初步action
        _tb_action = _tb_r.get('action')
        if _tb_action and _tb_action != 'ENTER':
            # trader_brain说不能ENTER → 覆盖confluence_score的ENTER_FULL
            try:
                _cf_ref = _result.get('confluence', {})
                if isinstance(_cf_ref, dict) and _cf_ref.get('action') in ('ENTER_FULL', 'ENTER'):
                    _cf_ref['action'] = _tb_action
                    _result['confluence'] = _cf_ref
            except Exception as _e:
                print(f"[WARN] brahma_core_tradfi_inject: _e", file=sys.stderr)
    except Exception as _e:
        print(f"[WARN] brahma_core_tradfi_inject: _e", file=sys.stderr)

    # [2026-09-15 苏摩111] 神经总线感知 — 分析完成时emit
    try:
        from brahma_brain.nerve_bus_writer import emit_analysis_done
        _score_val = _result.get('confluence_score', _result.get('score', 0))
        _regime = _result.get('regime', ms.get('regime', 'UNKNOWN'))
        _signal = _result.get('signal', 'WATCH')
        _symbol = _result.get('symbol', ms.get('symbol', 'UNKNOWN'))
        _direction = signal_dir
        _ev = _result.get('ev', 0)
        _breakdown = _result.get('breakdown', {})
        _active = [k for k, v in _breakdown.items() if isinstance(v, (int, float)) and v != 0]
        _sleep = [k for k, v in _breakdown.items() if k not in _active]
        emit_analysis_done(_symbol, float(_score_val or 0), _regime, _direction,
                          len(_breakdown), _active[:10], _sleep[:10],
                          float(_ev or 0), _signal)
    except Exception as _e:
        print(f"[WARN] brahma_core_tradfi_inject: _e", file=sys.stderr)

    # [2026-09-15 苏摩111] 防御性保证：analyze()永远返回dict
    if not isinstance(_result, dict):
        _result = {'symbol': _sym, 'error': f'non-dict return: {type(_result).__name__}', 'score_final': 0}
    if 'symbol' not in _result:
        _result['symbol'] = _sym
    return _result, score, breakdown

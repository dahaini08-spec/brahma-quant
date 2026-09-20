"""
brahma_core_post_inject.py
[V2.0 2026-09-20 苏摩111] TradFi跨市场参照 + 212K经验库 + 亏损记忆

从 brahma_core.py analyze() 中提取。
接入位置：方仓注入之后、B类模块之前。
接口：inject_post_score(_result, ms, score, breakdown, signal_dir, symbol) -> (_result, score, breakdown)
"""
import sys
from typing import Any


def inject_post_score(_result: dict, ms: dict, score: int, breakdown: dict,
                      signal_dir: str, symbol: str) -> tuple[dict, int, dict]:
    """
    TradFi跨市场参照 + 212K经验库 + 亏损记忆仓位自适应
    """
    # ══ [V2.0 2026-09-20 苏摩111] TradFi跨市场参照 + 212K经验库 + 亏损记忆 ═══════
    try:
        if _sym in ('BTCUSDT', 'ETHUSDT'):
            from brahma_brain.fangcang_engine import query_tradfi as _tfi_q2
            _tfi_bbw2  = float(ms.get('bb_width_4h', ms.get('bb_width', 1.5)) or 1.5)
            _tfi_rsi2  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
            _tfi_dir2  = 'UP' if (signal_dir or 'LONG') == 'LONG' else 'DOWN'
            _tradfi_refs = {}
            for _ref_token in ('NVDAUSDT', 'XAUUSDT'):
                try:
                    _r = _tfi_q2(_ref_token, _tfi_bbw2, 42, 0.9, 2.0, _tfi_rsi2, _tfi_dir2, top_k=20)
                    _tradfi_refs[_ref_token] = {'wr': round(_r.get('wr', 0.5), 3), 'n': _r.get('n', 0), 'ev': round(_r.get('ev', 0), 2)}
                except Exception as _e: print(f'[WARN] {__name__}: tradfi {_ref_token}: {_e}', file=sys.stderr)
            _result['tradfi_xref'] = _tradfi_refs
            _tradfi_bullish = sum(1 for v in _tradfi_refs.values() if v.get('wr', 0.5) >= 0.6)
            _tradfi_bearish = sum(1 for v in _tradfi_refs.values() if v.get('wr', 0.5) <= 0.4)
            if _tradfi_bullish >= 2 and (signal_dir or '') == 'LONG':
                _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + 2, 1)
                _result.setdefault('confluence', {}).setdefault('breakdown', {})['TradFi跨市场共振'] = '+2 40年TradFi看多'
            elif _tradfi_bearish >= 2 and (signal_dir or '') == 'SHORT':
                _result['score_final'] = round(float(_result.get('score_final', 0) or 0) + 2, 1)
                _result.setdefault('confluence', {}).setdefault('breakdown', {})['TradFi跨市场共振'] = '+2 40年TradFi看空'
    except Exception as _e: print(f'[WARN] {__name__}: tradfi_xref: {_e}', file=sys.stderr)

    # 212K experience_payloads KD-Tree查询
    try:
        from brahma_brain.exp_payloads_query import query_similar as _ep_q
        _ep_rsi  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        _ep_bbw  = float(ms.get('bb_width_4h', ms.get('bb_width', 20)) or 20)
        _ep_mg   = float(ms.get('momentum_gap', 0) or 0)
        _ep_ml   = float(ms.get('mean_reversion_level', 0) or 0)
        _ep_wl   = float(ms.get('win_loss_ratio', 1) or 1)
        _ep_ws   = float(ms.get('win_streak', 0) or 0)
        _ep_sym  = symbol.replace('USDT', '') if symbol.endswith('USDT') else symbol
        _ep_reg  = _result.get('regime', '')
        _ep_res  = _ep_q(rsi=_ep_rsi, bbw=_ep_bbw, mg=_ep_mg, ml=_ep_ml, wl=_ep_wl, ws=_ep_ws, top_k=10, sym_filter=_ep_sym if _ep_sym in ('BTC','ETH') else None, reg_filter=_ep_reg if _ep_reg else None)
        _result['exp_payloads'] = {'n': _ep_res.get('n', 0), 'cases': _ep_res.get('cases', [])[:5]}
    except Exception as _e: print(f'[WARN] {__name__}: exp_payloads: {_e}', file=sys.stderr)

    # 亏损记忆引擎
    try:
        from brahma_brain.loss_memory_engine import query_similar_memory as _lm_q
        _lm_fp = {'symbol': _sym, 'regime': _result.get('regime', ''), 'signal_dir': _result.get('signal_dir', signal_dir or ''), 'score_final': float(_result.get('score_final', 0) or 0), 'rsi_1h': float(ms.get('rsi_1h', 50) or 50), 'rsi_4h': float(ms.get('rsi_4h', 50) or 50), 'bb_width': float(ms.get('bb_width_4h', 20) or 20), 'hurst': float(_result.get('hurst', 0.5) or 0.5), 'hurst_1d': float(_result.get('hurst_1d', 0.5) or 0.5), 'hurst_4h': float(_result.get('hurst_4h', 0.5) or 0.5), 'fvg_consensus': _result.get('fvg_consensus', ''), 'oi_signal': _result.get('oi_signal', '')}
        _lm_res = _lm_q(_lm_fp, top_k=5)
        _result['loss_memory'] = _lm_res
        if _lm_res.get('warning'):
            _result['loss_memory_warning'] = _lm_res['warning']
            if _lm_res.get('win_rate', 0.5) <= 0.4 and _lm_res.get('n', 0) >= 3:
                _result.setdefault('confluence', {}).setdefault('breakdown', {})['亏损记忆'] = f'-3 {_lm_res["warning"][:30]}'
                _result['score_final'] = round(float(_result.get('score_final', 0) or 0) - 3, 1)
                _result['score'] = _result['score_final']
    except Exception as _e: print(f'[WARN] {__name__}: loss_memory: {_e}', file=sys.stderr)

    # ══ [B类模块接入 2026-08-09 设计院深度排查封印 苏摩111] ══════════════════════
    return _result, score, breakdown

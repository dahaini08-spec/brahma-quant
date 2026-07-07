#!/usr/bin/env python3
"""
# ── 全局内存优化（工程师建议 P1）──
import gc as _gc_mod
import psutil as _psutil_mod
_gc_mod.enable()
_gc_mod.set_threshold(700, 10, 10)

def _check_and_gc():
    _gc_mod.collect()
    if _psutil_mod.virtual_memory().percent > 75:
        _gc_mod.collect(2)
# ─────────────────────────────────────
brahma_analyze.py — 梵天分析CLI入口 v24.0
用法: python3 brahma_analyze.py BTCUSDT [--json] [--dir SHORT|LONG]
"""
import sys, os, json, argparse

# [修复 2026-07-06] Kronos LightGBM预注入：必须在kronos_engine import前先import lightgbm
# 否则_model_load_attempted=True后lgbm路径被跳过，导致fallback:lgbm_err
try:
    import lightgbm as _lgbm_pre
    sys.modules['lightgbm'] = _lgbm_pre
except ImportError:
    pass

_base = os.path.dirname(os.path.abspath(__file__))
for p in [_base, os.path.join(_base,'scripts'), os.path.join(_base,'brahma_brain')]:
    if p not in sys.path: sys.path.insert(0, p)

def _sf(v, d=0.0):
    try: return float(v)
    except: return d

def main():
    parser = argparse.ArgumentParser(description='梵天信号分析')
    parser.add_argument('symbol', nargs='?', default='BTCUSDT')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dir', default='SHORT', choices=['SHORT','LONG'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    import io, contextlib
    # 标准化symbol（BTC → BTCUSDT）
    sym = args.symbol.upper()
    if not sym.endswith('USDT') and not sym.endswith('PERP') and len(sym) <= 6:
        sym = sym + 'USDT'
    args.symbol = sym

    # [设计院 FIX 2026-07-06] P4: 改用 run_analysis() 全链路入口
    # 原因: brahma_brain.analyze() = 裸分析，缺少 bull_bonus/timing/sw_noise等强化层
    # 修复: run_analysis 是封印版唯一入口，包含所有层级增强
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis
        _log_buf = io.StringIO()
        with contextlib.redirect_stdout(_log_buf):
            r = run_analysis(sym)
        sys.stderr.write(_log_buf.getvalue())
    except Exception as _runner_err:
        # 降级到裸analyze（兼容保障）
        sys.stderr.write(f'[brahma_analyze] runner失败，降级裸analyze: {_runner_err}\n')
        from brahma_brain import analyze, format_report
        _log_buf = io.StringIO()
        with contextlib.redirect_stdout(_log_buf):
            r = analyze(args.symbol, signal_dir=args.dir)
        sys.stderr.write(_log_buf.getvalue())

    # [设计院 FIX 2026-07-06] 字段标准化修复：取正确的最终分 score_final和完整字段
    params   = r.get('params', {}) or {}
    conf     = r.get('confluence', {}) or {}

    # score: 取 AssetRouter 后的最终分 score_final（包含资产类型加成）
    score    = _sf(r.get('score_final',
                  conf.get('total', r.get('score', 0))))

    # grade: confluence.structure_grade 最准确
    grade    = _sf(conf.get('structure_grade',
                  conf.get('grade_num', r.get('grade', 0))))

    # valid: _valid = kelly_mult>0 AND params['valid'] AND score_gate
    # valid_signal 是 brahma_core 的最终结果，就用它
    valid    = bool(r.get('valid_signal', params.get('valid', False)))

    regime   = r.get('regime', '?')
    action   = conf.get('action', '')
    rr1      = _sf(params.get('rr1', 0))
    sl_pct   = _sf(params.get('sl_pct', 0))
    entry_lo = _sf(params.get('entry_lo', r.get('entry_lo', 0)))
    entry_hi = _sf(params.get('entry_hi', r.get('entry_hi', 0)))
    sl       = _sf(params.get('stop_loss', r.get('stop_loss', 0)))
    tp1      = _sf(params.get('tp1', r.get('tp1', 0)))
    sig_dir  = r.get('signal_dir', args.dir)

    # timing_status: 优先从result顶层读取（run_analysis已注入）
    timing   = (r.get('timing_status') or
                r.get('extra', {}).get('timing_status', '') or
                conf.get('timing_status', ''))

    # pos_size
    pos_pct  = _sf(r.get('pos_pct_sizer', 0))
    pos_level= r.get('pos_level_sizer', '')

    if args.json:
        out = {
            'symbol':     r.get('symbol', args.symbol),
            'score':      round(score, 1),
            'score_raw':  round(_sf(r.get('score_final_raw', score)), 1),
            'grade':      round(grade, 0),
            'valid':      valid,
            'regime':     regime,
            'signal_dir': sig_dir,
            'action':     action,
            'rr1':        round(rr1, 2),
            'sl_pct':     round(sl_pct, 2),
            'timing':     timing,
            'pos_pct':    round(pos_pct, 1),
            'pos_level':  pos_level,
            'entry_lo':   round(entry_lo, 6),
            'entry_hi':   round(entry_hi, 6),
            'stop_loss':  round(sl, 6),
            'tp1':        round(tp1, 6),
        }
        print(json.dumps(out, ensure_ascii=False))
    else:
        try:
            print(format_report(r))
        except:
            print(f"[{args.symbol}] score={score:.0f} grade={grade:.0f} valid={valid} {regime}")

if __name__ == '__main__':
    main()

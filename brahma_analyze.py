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

    from brahma_brain import analyze, format_report
    # 分析期间把print日志重定向到stderr，保持stdout干净（JSON输出用）
    _log_buf = io.StringIO()
    with contextlib.redirect_stdout(_log_buf):
        r = analyze(args.symbol, signal_dir=args.dir)
    # 把日志转发到stderr
    sys.stderr.write(_log_buf.getvalue())

    # 字段标准化（兼容两套命名）
    params   = r.get('params', {})
    conf     = r.get('confluence', {})
    score    = _sf(conf.get('total', conf.get('score', r.get('score', r.get('score_final', 0)))))
    grade    = _sf(conf.get('grade_num', r.get('grade', params.get('grade', 0))))
    valid    = bool(r.get('valid_signal', r.get('valid', False)))
    regime   = r.get('regime', '?')
    entry_lo = _sf(params.get('entry_lo', r.get('entry_lo', 0)))
    entry_hi = _sf(params.get('entry_hi', r.get('entry_hi', 0)))
    sl       = _sf(params.get('stop_loss', r.get('stop_loss', 0)))
    tp1      = _sf(params.get('tp1', r.get('tp1', 0)))
    sig_dir  = r.get('signal_dir', args.dir)

    # grade来自 confluence.structure_grade（最准确）
    conf2 = r.get('confluence', {})
    if conf2 and 'structure_grade' in conf2:
        grade = _sf(conf2['structure_grade'])

    if args.json:
        out = {
            'symbol':    r.get('symbol', args.symbol),
            'score':     round(score, 1),
            'grade':     round(grade, 0),
            'valid':     valid,
            'regime':    regime,
            'signal_dir': sig_dir,
            'entry_lo':  round(entry_lo, 4),
            'entry_hi':  round(entry_hi, 4),
            'stop_loss': round(sl, 4),
            'tp1':       round(tp1, 4),
        }
        print(json.dumps(out, ensure_ascii=False))
    else:
        try:
            print(format_report(r))
        except:
            print(f"[{args.symbol}] score={score:.0f} grade={grade:.0f} valid={valid} {regime}")

if __name__ == '__main__':
    main()

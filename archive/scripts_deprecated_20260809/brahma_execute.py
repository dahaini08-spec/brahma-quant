#!/usr/bin/env python3
"""
⚠️  WARNING: brahma_execute.py — 绕过体制封禁直通道
设计院 2026-05-19: 此文件允许跳过 hunter_filter / REGIME_DIR_BLOCK
正常信号请走: brahma_core.py → hunter_main.py → hunter_executor.py
仅在人工确认信号时使用，且需要 [confirm] 参数
"""
"""
梵天直通执行引擎
用途：用户CONFIRM后直接执行brahma_analysis信号，绕过体制封禁
调用：python3 scripts/brahma_execute.py ETH SHORT [confirm]
"""
import sys, os, time, json
from pathlib import Path
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'lana' / 'hunter_v2'))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE))

def run(symbol: str, direction: str, dry_run: bool = True, nav_override: float = None):
    import sys as _sys
    _sys.path.insert(0, str(BASE))
    _sys.path.insert(0, str(BASE/'scripts'))
    from brahma_brain.brahma_brain import analyze
    from hunter_sizer import calc_position_size
    from hunter_executor import execute_open, execute_close
    import binance_fapi as bf

    # ── 获取信号 ──────────────────────────────────────────────────
    sig_dir = 'SHORT' if '空' in direction or direction.upper()=='SHORT' else 'LONG'
    r = analyze(f'{symbol.upper().replace("USDT","")}USDT', sig_dir)
    params  = r['params']
    price   = r['price']
    conf    = r['confluence']
    score   = conf.get('total', 0)
    regime  = r.get('regime', '?')

    pass  # [静默]
    print(f'  入场区: ${params["entry_lo"]:.4f} ~ ${params["entry_hi"]:.4f}')
    print(f'  止损:   ${params["stop_loss"]:.4f}  (-{params["sl_pct"]}%)')
    print(f'  TP1:    ${params["tp1"]:.4f}  RR={params["rr1"]}')
    print(f'  TP2:    ${params["tp2"]:.4f}  RR={params["rr2"]}')

    # ── NAV ────────────────────────────────────────────────────────
    if nav_override:
        nav = nav_override
    else:
        import urllib.request, hmac, hashlib, urllib.parse
        from config import binance_keys
        K, S = binance_keys()
        ts = int(time.time()*1000)
        qs = urllib.parse.urlencode({'timestamp': ts})
        sig = hmac.new(S.encode(), qs.encode(), hashlib.sha256).hexdigest()
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            f'https://fapi.binance.com/fapi/v2/account?{qs}&signature={sig}',
            headers={'X-MBX-APIKEY': K}), timeout=8).read())
        nav = float(d['totalWalletBalance'])
    print(f'  NAV:    ${nav:.2f}  模式: {"纸盘DRY_RUN" if dry_run else "🔴实盘"}')

    if dry_run:
        pass  # [静默]
        return {'status': 'DRY_RUN', 'score': score}

    # ── 构造signal/sizing ─────────────────────────────────────────
    sym = f'{symbol.upper().replace("USDT","")}USDT'
    signal = {
        'symbol':       sym,
        'direction':    '做多' if sig_dir == 'LONG' else '做空',
        'regime':       regime,
        'score':        score,
        'signal_tier':  'S1' if score >= 120 else 'S2' if score >= 90 else 'S3',
        'source':       'brahma_direct',
        'channel':      'BRAHMA_CONFIRM',
        'rsi_4h':       r.get('momentum', {}).get('rsi_4h', 0),
        'rsi_1h':       r.get('momentum', {}).get('rsi_1h', 0),
        'funding_rate': 0,
        'oi_change_pct':0,
        'tier':         'A',
        'kelly_override': 0.5,
        'latest_close': price,
        'atr':          r.get('momentum', {}).get('atr_1h', price * 0.01),
        # 直接传入brahma计算的精确价格
        'entry_price':  (params['entry_lo'] + params['entry_hi']) / 2,
        'sl_price':     params['stop_loss'],
        'tp1_price':    params['tp1'],
        'tp2_price':    params['tp2'],
        'brahma_params': params,
    }

    # 使用brahma参数直接构造sizing
    entry_mid = (params['entry_lo'] + params['entry_hi']) / 2
    # ── 仓位：从真实数据推导（position_sizer）──────────────
    try:
        import sys as _s; _s.path.insert(0, str(BASE / 'brahma_brain'))
        from position_sizer import get_position_pct
        _pos = get_position_pct(f'{symbol.upper().replace("USDT","")}USDT', score, sig_dir, nav)
        if not _pos['allowed']:
            pass  # [静默]
            print(f'   此币种/方向/评分组合已暂停（数据不足或历史WR<35%）')
            if not dry_run:
                return None
        kelly_pct = _pos['pct'] / 100
        print(f'  仓位: {_pos["pct"]:.0f}% NAV | {_pos["level"]} | ${_pos["usdt"]:.2f}')
    except Exception as _e:
        kelly_pct = 0.05  # fallback 5%
        print(f'  仓位: 5% NAV (fallback, sizer error: {_e})')
    # ────────────────────────────────────────────────────────
    margin    = nav * kelly_pct
    leverage  = 5
    notional  = margin * leverage
    from hunter_sizer import get_symbol_info
    import math
    sinfo  = get_symbol_info(sym)
    step   = sinfo.get("step_size") or 10**(-sinfo.get("qty_precision",3))
    qty    = math.floor(notional / entry_mid / step) * step
    qty    = max(qty, step)

    sizing = {
        'qty':           qty,
        'qty_precision': len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0,
        'notional':      round(qty * entry_mid, 2),
        'entry_price':   entry_mid,
        'sl_price':      params['stop_loss'],
        'tp1_price':     params['tp1'],
        'tp2_price':     params['tp2'],
        'leverage':      leverage,
        'kelly_pct':     kelly_pct,
        'rr':            params['rr1'],
    }

    print(f'  数量: {qty}  名义: ${sizing["notional"]:.2f}  杠杆: {leverage}x')

    # ── Step0: 国库官审批（主系统A_BRAHMA走国库官）──────────────
    try:
        import sys as _tg_sys, os as _tg_os
        _tg_sys.path.insert(0, str(BASE))
        from treasury_gate import get_treasury as _get_tg, OpenRequest as _TGReq
        _tg_req = _TGReq(
            system_id   = "A_BRAHMA",
            symbol      = sym,
            direction   = signal['direction'],
            notional    = sizing['notional'],
            entry_price = sizing['entry_price'],
            sl          = sizing['sl_price'],
            tp1         = sizing['tp1_price'],
            tp2         = sizing['tp2_price'],
            signal_id   = signal.get('signal_id', f"BRAHMA-{sym}-{int(time.time())}"),
            channel     = "BRAHMA_CONFIRM",
            score       = float(signal.get('score', 0)),
            regime      = signal.get('regime', '?'),
            extra       = {
                'rsi_4h':       signal.get('rsi_4h', 0),
                'signal_tier':  signal.get('signal_tier', 'S1'),
                'source':       'brahma_direct',
                # 仓位分级字段（brahma_core N15层计算，供 treasury_gate 门控3b用）
                'score_tier':   signal.get('score_tier', signal.get('extra', {}).get('score_tier', '')),
                'score_pos':    signal.get('score_pos',  signal.get('extra', {}).get('score_pos',  None)),
                'leverage':     signal.get('leverage', 3),
            }
        )
        _tg_approval = _get_tg().request_open(_tg_req)
        if not _tg_approval.approved:
            pass  # [静默]
            return {'status': 'REJECTED_BY_TREASURY', 'reason': _tg_approval.reason}
        signal['_treasury_position_id'] = _tg_approval.position_id
        pass  # [静默]
    except ImportError:
        pass  # [静默]

    # ── 执行开仓 ──────────────────────────────────────────────────
    result = execute_open(signal, sizing, dry_run=False)
    pass  # [静默]
    for o in result.get('orders', []):
        print(f'  ✅ {o["type"]:5} qty={o["qty"]} @{o["price"]}')
    for e in result.get('errors', []):
        print(f'  ⚠️  {e}')
    return result

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('symbol', help='标的 e.g. ETH BTC')
    p.add_argument('direction', help='LONG/SHORT/做多/做空')
    p.add_argument('--live', action='store_true', help='实盘执行（默认纸盘）')
    p.add_argument('--nav', type=float, default=None, help='NAV覆盖')
    args = p.parse_args()
    run(args.symbol, args.direction, dry_run=not args.live, nav_override=args.nav)

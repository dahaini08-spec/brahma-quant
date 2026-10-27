#!/usr/bin/env python3
"""
leading_signal_executor.py — 先行信号独立执行通道
[封印 2026-10-27 苏摩111] 预判型升级P0-A

核心逻辑：
  先行信号（BB收窄/LSR猎杀/OI骤增/GEX穿越）任意2项触发
  + 宏观锚确认方向（BULL_EARLY→做多 / BEAR_EARLY→做空）
  → 绕过Step11 CHOP_MID体制门控
  → 找liq清算位作为入场区
  → 推送草案给苏摩111确认

绕过的门控：
  ✓ CHOP_MID体制阻断（Gate5）
  ✓ score<110（Gate5）

不绕过的铁律：
  ✗ 宏观方向锁（BULL禁空/BEAR禁多）
  ✗ SL≥1.5×ATR1H
  ✗ 风控熔断（circuit_breaker）
  ✗ ETH_WEAK铁律⑤
  ✗ RR≥1.5

仓位：1%NAV（小仓，不依赖score，先行通道专属）
接入：brahma_crontab.txt 每15min + signal_early_alert触发后调用
"""
import sys, os, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 先行信号触发阈值 ──────────────────────────────────────────
BB_SQUEEZE_CRIT   = 0.6    # BB宽度<0.6 = 极度压缩
LSR_CROWD_CRIT    = 75.0   # 散户>75% = 猎杀条件
LSR_RATIO_CRIT    = 3.0    # 大户/散户比>3.0
OI_SURGE_PCT      = 3.0    # 15min内OI变化>3%
GEX_ZF_DIST_MAX   = 0.008  # 价格距ZeroFlip<0.8% = 穿越临近
FR_EXTREME        = 0.012  # FR绝对值>0.012% = 极端

# ── SL计算铁律 ─────────────────────────────────────────────
ATR_SL_MULT       = 1.5    # SL≥1.5×ATR1H
MIN_RR            = 1.5    # RR≥1.5

# ── 仓位 ───────────────────────────────────────────────────
LEADING_POS_PCT   = 1.0    # 先行通道固定1%NAV

COOLDOWN_MIN      = 120    # 同标的2H冷却（避免连续触发）

# ══════════════════════════════════════════════════════════
# 先行信号读取
# ══════════════════════════════════════════════════════════

def _load_state(sym: str) -> dict:
    f = DATA / f'brahma_state_{sym.lower()}.json'
    return json.loads(f.read_text()) if f.exists() else {}

def _load_macro() -> dict:
    try:
        from macro_regime_engine import run_macro_regime
        return run_macro_regime()
    except Exception:
        return {}

def _check_leading_signals(sym: str, state: dict) -> dict:
    """
    检测5类先行信号，返回触发情况
    任意2项触发 = 条件满足
    """
    px     = float(state.get('price', 0))
    bb_w   = float(state.get('bb_width', state.get('bbw', 1.0) or 1.0))
    lsr    = float(state.get('lsr_retail', state.get('long_ratio', 50) or 50))
    lsr_r  = float(state.get('lsr_ratio', 1.0) or 1.0)
    zf     = float(state.get('gex_zero_flip', state.get('zero_flip', 0) or 0))
    fr     = abs(float(state.get('funding_rate', 0) or 0))
    atr1h  = float(state.get('atr_1h', 300) or 300)

    # OI骤增：从state的OI序列推断
    oi_sig = str((state.get('oi') or {}).get('main_signal', '') or
                 state.get('oi_signal', '') or '')
    oi_surge = 'BUILD' in oi_sig and 'SHORT_BUILD' not in oi_sig  # LONG_BUILD

    # GEX ZF穿越：价格距ZF距离
    gex_zf_cross = zf > 0 and abs(px - zf) / px < GEX_ZF_DIST_MAX

    signals = {
        'BB_SQUEEZE': bb_w < BB_SQUEEZE_CRIT and bb_w > 0,
        'LSR_CROWD':  lsr > LSR_CROWD_CRIT or lsr_r > LSR_RATIO_CRIT,
        'OI_SURGE':   oi_surge,
        'GEX_ZF':     gex_zf_cross,
        'FR_EXTREME': fr > FR_EXTREME,
    }
    triggered = [k for k, v in signals.items() if v]
    return {
        'triggered':  triggered,
        'count':      len(triggered),
        'ready':      len(triggered) >= 2,
        'bb_w':       bb_w,
        'lsr':        lsr,
        'lsr_r':      lsr_r,
        'oi_sig':     oi_sig,
        'gex_zf':     zf,
        'fr':         fr,
        'atr1h':      atr1h,
        'px':         px,
    }

# ══════════════════════════════════════════════════════════
# 宏观方向锚
# ══════════════════════════════════════════════════════════

def _macro_direction(sym: str, macro: dict) -> str:
    """
    宏观锚：确定允许的交易方向
    返回: 'LONG' / 'SHORT' / 'BOTH' / 'NONE'
    """
    sym_macro = macro.get(sym.upper(), {})
    regime = str(sym_macro.get('macro_regime', '') or macro.get('macro_regime', ''))

    # ETH_WEAK铁律⑤：ETH相对BTC弱>5% + BULL体制 → 禁多
    eth_btc_rel = float(macro.get('ETH_BTC_rel_4w', 0) or 0)
    eth_weak = sym.upper() == 'ETH' and eth_btc_rel < -0.05

    if 'BULL' in regime:
        if eth_weak:
            return 'SHORT'   # 铁律⑤：ETH弱势禁多
        return 'LONG'
    elif 'BEAR' in regime:
        return 'SHORT'
    else:
        return 'BOTH'

# ══════════════════════════════════════════════════════════
# 入场区计算（清算位锚定）
# ══════════════════════════════════════════════════════════

def _calc_entry(sym: str, direction: str, state: dict, sig: dict) -> dict:
    """
    基于清算位计算入场区、SL、TP
    LONG: 在 liq_long（多头清算密集区）下方接多
    SHORT: 在 liq_short（空头清算密集区）上方入空
    """
    px    = sig['px']
    atr1h = sig['atr1h']
    liq_s = float(state.get('liq_short', state.get('liq_up', 0)) or 0)
    liq_l = float(state.get('liq_long',  state.get('liq_dn', 0)) or 0)
    liq_s2 = float(state.get('liq_short2', 0) or 0)
    liq_l2 = float(state.get('liq_long2',  0) or 0)

    if direction == 'LONG':
        # 在多头清算位接多：价格跌到liq_l扫止损后反弹
        if liq_l > 0 and liq_l < px:
            entry_hi = round(liq_l * 1.003, 1)
            entry_lo = round(liq_l * 0.999, 1)
            sl       = round(min(liq_l - atr1h * ATR_SL_MULT,
                                 liq_l * 0.993), 1)
        else:
            # 无清算位：用当前价×0.985
            entry_hi = round(px * 0.988, 1)
            entry_lo = round(px * 0.984, 1)
            sl       = round(px * 0.984 - atr1h * ATR_SL_MULT, 1)

        tp1 = round(liq_s * 0.998 if liq_s > px else px * 1.02, 1)
        tp2 = round(liq_s2 * 0.998 if liq_s2 > tp1 else tp1 * 1.015, 1)
        rr  = (tp1 - entry_hi) / (entry_hi - sl) if entry_hi > sl > 0 else 0

    else:  # SHORT
        # 在空头清算位入空：价格涨到liq_s扫止损后下行
        if liq_s > 0 and liq_s > px:
            entry_lo = round(liq_s * 0.997, 1)
            entry_hi = round(liq_s * 1.001, 1)
            sl       = round(max(liq_s + atr1h * ATR_SL_MULT,
                                 liq_s * 1.007), 1)
        else:
            entry_lo = round(px * 1.012, 1)
            entry_hi = round(px * 1.016, 1)
            sl       = round(px * 1.016 + atr1h * ATR_SL_MULT, 1)

        tp1 = round(liq_l * 1.002 if liq_l > 0 and liq_l < px else px * 0.98, 1)
        tp2 = round(liq_l2 * 1.002 if liq_l2 > 0 and liq_l2 < tp1 else tp1 * 0.985, 1)
        rr  = (entry_lo - tp1) / (sl - entry_lo) if sl > entry_lo > tp1 > 0 else 0

    return {
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'sl':       sl,
        'tp1':      tp1,
        'tp2':      tp2,
        'rr':       round(rr, 2),
        'atr1h':    atr1h,
        'sl_valid': (sl < entry_lo if direction == 'LONG' else sl > entry_hi),
        'rr_valid': rr >= MIN_RR,
    }

# ══════════════════════════════════════════════════════════
# 冷却期检查
# ══════════════════════════════════════════════════════════

_STATE_FILE = DATA / 'leading_signal_state.json'

def _load_executor_state() -> dict:
    if _STATE_FILE.exists():
        try: return json.loads(_STATE_FILE.read_text())
        except: pass
    return {}

def _save_executor_state(s: dict):
    tmp = _STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False))
    tmp.replace(_STATE_FILE)

def _in_cooldown(sym: str, es: dict) -> bool:
    last = es.get(f'last_trigger_{sym.upper()}', 0)
    return (time.time() - last) < COOLDOWN_MIN * 60

# ══════════════════════════════════════════════════════════
# 主执行逻辑
# ══════════════════════════════════════════════════════════

def run(sym: str, push: bool = True) -> dict:
    """对单标的运行先行信号检测+草案生成"""
    state  = _load_state(sym)
    macro  = _load_macro()
    es     = _load_executor_state()

    # 1. 先行信号检测
    sig = _check_leading_signals(sym, state)
    if not sig['ready']:
        return {'status': 'NO_SIGNAL', 'sym': sym,
                'triggered': sig['triggered'], 'count': sig['count']}

    # 2. 冷却期
    if _in_cooldown(sym, es):
        last = es.get(f'last_trigger_{sym.upper()}', 0)
        return {'status': 'COOLDOWN', 'sym': sym,
                'remaining_min': round((COOLDOWN_MIN*60-(time.time()-last))/60, 1)}

    # 3. 宏观方向锚
    direction = _macro_direction(sym, macro)
    if direction == 'NONE':
        return {'status': 'MACRO_BLOCKED', 'sym': sym, 'macro': macro}

    # 4. 如果宏观BOTH，用先行信号方向
    if direction == 'BOTH':
        # BB压缩+OI_BUILD → 做多；BB压缩+FR_EXTREME（负） → 做空
        fr_raw = float(state.get('funding_rate', 0) or 0)
        oi_sig = sig.get('oi_sig', '')
        if 'LONG_BUILD' in oi_sig or fr_raw < -0.005:
            direction = 'SHORT'  # FR极度负值=逼空
        else:
            direction = 'LONG'

    # 5. 入场区计算
    entry = _calc_entry(sym, direction, state, sig)

    # SL铁律验证
    if not entry['sl_valid']:
        return {'status': 'SL_INVALID', 'sym': sym,
                'entry': entry, 'reason': 'SL方向错误'}
    if not entry['rr_valid']:
        return {'status': 'RR_INSUFFICIENT', 'sym': sym,
                'rr': entry['rr'], 'min_rr': MIN_RR}

    # 6. 风控熔断检查
    cb = DATA / 'circuit_breaker_state.json'
    if cb.exists():
        cb_data = json.loads(cb.read_text())
        if cb_data.get('triggered') or cb_data.get('status') == 'RED':
            return {'status': 'CIRCUIT_BREAKER', 'sym': sym}

    # 7. 构建结果
    result = {
        'status':     'READY',
        'sym':        sym,
        'direction':  direction,
        'signals':    sig['triggered'],
        'signal_cnt': sig['count'],
        'entry_lo':   entry['entry_lo'],
        'entry_hi':   entry['entry_hi'],
        'sl':         entry['sl'],
        'tp1':        entry['tp1'],
        'tp2':        entry['tp2'],
        'rr':         entry['rr'],
        'pos_pct':    LEADING_POS_PCT,
        'macro':      str(macro.get(sym.upper(), {}).get('macro_regime', '?')),
        'bb_w':       sig['bb_w'],
        'lsr':        sig['lsr'],
        'ts':         time.time(),
    }

    # 8. 推送
    if push:
        _push_draft(result)

    # 9. 更新冷却
    es[f'last_trigger_{sym.upper()}'] = time.time()
    _save_executor_state(es)

    print(f'[leading_executor] {sym} {direction} ✅ '
          f'entry={entry["entry_lo"]:.1f}~{entry["entry_hi"]:.1f} '
          f'sl={entry["sl"]:.1f} rr={entry["rr"]:.1f} '
          f'signals={sig["triggered"]}')
    return result

def _push_draft(r: dict):
    """推送先行信号草案给苏摩"""
    try:
        from push_hub import push_jarvis
        side = '🟢 多单' if r['direction'] == 'LONG' else '🔴 空单'
        sig_str = '+'.join(r['signals'])
        msg = (
            f"⚡ 先行信号触发 | {r['sym']} {r['direction']}\n"
            f"触发: {sig_str}\n"
            f"宏观: {r['macro']}\n"
            f"\n"
            f"{side}｜挂单区 ${r['entry_lo']:,.1f}~${r['entry_hi']:,.1f}\n"
            f"止损 ${r['sl']:,.1f}｜目标 ${r['tp1']:,.1f}→${r['tp2']:,.1f}\n"
            f"RR={r['rr']:.1f}  仓位={r['pos_pct']}%NAV\n"
            f"BB宽={r['bb_w']:.3f}  LSR={r['lsr']:.1f}%\n"
            f"\n"
            f"回复 111 确认执行（先行通道，绕过CHOP门控）"
        )
        push_jarvis(msg, priority='P1')
    except Exception as e:
        print(f'[leading_executor] 推送失败: {e}')

# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    p.add_argument('--no-push', action='store_true')
    args = p.parse_args()

    for sym in args.symbols:
        r = run(sym, push=not args.no_push)
        print(f'[{sym}] {r.get("status")} {r.get("direction","")} '
              f'signals={r.get("signals",[])} rr={r.get("rr",0)}')

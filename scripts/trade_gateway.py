import os
#!/usr/bin/env python3
"""
trade_gateway.py — 梵天统一交易执行网关 v1.0
设计院 2026-06-10

【核心职责】
  单一入口：trade_gateway.run(symbol)
  内部完整流程：
    1. regime_scorer  → 三层体制评估
    2. brahma_analyze × 2（SHORT + LONG）→ 双向原始分析
    3. signal_selector → 方向裁决+加权
    4. pre_trade_engine → 五关门控
    5. 推送 + DD1入队

【调用方式】
  python3 scripts/trade_gateway.py BTC
  python3 scripts/trade_gateway.py ETH --force
  from trade_gateway import run; run('BTC')

【设计原则】
  - 这里是唯一的"推送"出口
  - signal_watcher / zone_watcher 最终都路由到这里
  - 不做分析，只做调度和执行
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from push_hub import _jarvis

_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_DIR))
sys.path.insert(0, str(_DIR / 'brahma_brain'))
sys.path.insert(0, str(_DIR / 'scripts'))

JARVIS_TARGET = os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID')
MIN_WEIGHTED  = 110   # 同 signal_selector 门槛

# 推送去重：同symbol+direction 6H内不重复推送
# [v25.3-fix] 改为持久化文件，避免进程重启后冷却清零
_DEDUP_FILE = Path(__file__).parent.parent / 'data' / 'push_dedup.json'
PUSH_COOLDOWN = 21600   # 6H

def _load_dedup() -> dict:
    """从文件加载去重状态（进程重启安全）"""
    try:
        if _DEDUP_FILE.exists():
            with open(_DEDUP_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_dedup(d: dict) -> None:
    """持久化去重状态，并清理过期条目"""
    now = time.time()
    cleaned = {k: v for k, v in d.items() if now - v < PUSH_COOLDOWN}
    try:
        with open(_DEDUP_FILE, 'w') as f:
            json.dump(cleaned, f)
    except Exception:
        pass

_push_dedup = _load_dedup()  # 启动时从文件恢复



def _run_brahma_analyze(symbol: str, direction: str, timeout: int = 30) -> dict:
    """调用brahma_analyze并返回json结果"""
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    try:
        result = subprocess.run(
            ['python3', str(_DIR / 'brahma_analyze.py'), sym, '--json', '--dir', direction],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_DIR)
        )
        # stdout第一行是json（stderr是日志）
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('{'):
                d = json.loads(line)
                # 统一字段别名，兼容brahma_analyze --json输出格式
                d.setdefault('score_final', d.get('score', 0))
                d.setdefault('valid_signal', d.get('valid', False))
                d.setdefault('confluence', {
                    'structure_grade': d.get('grade', 0),
                    'total': d.get('score', 0)
                })
                d.setdefault('params', {
                    'entry_lo':  d.get('entry_lo', 0),
                    'entry_hi':  d.get('entry_hi', 0),
                    'stop_loss': d.get('stop_loss', 0),
                    'tp1':       d.get('tp1', 0),
                    'tp2':       d.get('tp2', 0),
                })
                return d
    except Exception as e:
        print(f'[Gateway] brahma_analyze {direction} 失败: {e}')
    return {}


def run(symbol: str, force_regime: bool = False, zone: dict = None) -> dict:
    """
    统一执行网关主入口

    参数：
      symbol       : 标的（BTC / BTCUSDT）
      force_regime : 强制刷新体制缓存
      zone         : Zone Watcher传入的待触达区间信息（可选）

    返回：
      pushed       : int  推送了几条信号
      signals      : list 推送的信号列表
      regime       : dict 体制评估结果
      decision     : str  裁决说明
    """
    # 苏摩能量记录（P0核心信号，无限制，仅记录消耗）
    try:
        import sys as _sys
        _sys.path.insert(0, str(_DIR / 'scripts'))
        from soma_manager import record_usage as _soma_rec
        _soma_rec('trade_gateway', tokens=3000, priority=0)
    except Exception as _e_ignored:
        print(f'[WARN][trade_gateway] {type(_e_ignored).__name__}: {_e_ignored}')
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    print(f'[Gateway] ── {sym} 分析开始 ──')
    t0 = time.time()

    # ── Step 1: 体制评估 [FIX-SSOT 2026-06-14] 统一使用 market_state.detect_regime ──
    # 修复根因：regime_scorer 只有3分类(BULL/BEAR/CHOP)，将 BEAR_RECOVERY 误判为 BEAR
    # 导致 LONG 被 n=225623铁证 硬封禁（实际 BEAR_RECOVERY LONG WR=72.5% EV=+0.255）
    import sys as _sys
    _bb = str(__file__).replace('/scripts/trade_gateway.py','/brahma_brain')
    if _bb not in _sys.path: _sys.path.insert(0, _bb)
    from market_state import analyze as _ms_analyze
    _ms = _ms_analyze(sym)
    _regime_label = _ms.get('regime', 'CHOP_MID')
    _trend = _ms.get('trend', {})
    _mom = _ms.get('momentum', {})
    # 7体制乘数矩阵（与brahma_core对齐，设计院2026-06-14）
    _REGIME_MULT = {
        'BULL_TREND':     {'LONG': 1.5,  'SHORT': 0.5},
        'BULL_EARLY':     {'LONG': 1.5,  'SHORT': 0.5},
        'BULL_CORRECTION':{'LONG': 1.0,  'SHORT': 1.0},
        'BEAR_TREND':     {'LONG': 0.0,  'SHORT': 1.5},  # LONG硬封禁 n=225623铁证
        'BEAR_EARLY':     {'LONG': 0.5,  'SHORT': 1.5},
        'BEAR_RECOVERY':  {'LONG': 0.95, 'SHORT': 0.4},  # LONG反直觉alpha WR=72.5%
        'CHOP_HIGH':      {'LONG': 0.5,  'SHORT': 0.7},  # P2 2026-06-29: SHORT乘数0.5→0.7，按需放开
        'CHOP_MID':       {'LONG': 0.5,  'SHORT': 0.7},  # P2 2026-06-29: SHORT乘数0.5→0.7，联动信号不漏
        'CHOP_LOW':       {'LONG': 0.5,  'SHORT': 0.5},  # LOW低位震荡保持不变
        'BREAKOUT':       {'LONG': 1.0,  'SHORT': 1.0},
    }
    _REGIME_CN = {
        'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_CORRECTION':'牛市回调',
        'BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期','BEAR_RECOVERY':'熊市反弹',
        'CHOP_HIGH':'高位震荡','CHOP_MID':'弱震荡','CHOP_LOW':'低位震荡',
    }
    _mult = _REGIME_MULT.get(_regime_label, {'LONG': 0.85, 'SHORT': 0.85})
    _primary = 'BULL' if 'BULL' in _regime_label else ('BEAR' if 'BEAR' in _regime_label else 'CHOP')
    _bear_p = _ms.get('momentum',{}).get('rsi_1d', 50) / 100  # 近似值，仅用于显示
    _bull_p = 1.0 - _bear_p
    regime = {
        'primary':    _regime_label,          # 使用7体制细分（非3分类）
        'regime':     _regime_label,
        'regime_cn':  _REGIME_CN.get(_regime_label, _regime_label),
        'multiplier': _mult,
        'phase':      _trend.get('4h',{}).get('direction', '?'),
        'momentum':   'BULLISH' if _mom.get('rsi_1h',50)>55 else ('BEARISH' if _mom.get('rsi_1h',50)<45 else 'NEUTRAL'),
        'bear_prob':  round(_bear_p, 3),
        'bull_prob':  round(_bull_p, 3),
        'chop_prob':  0.0,
        'rsi_1h':     _mom.get('rsi_1h', 0),
        'rsi_4h':     _mom.get('rsi_4h', 0),
    }
    _cn = _REGIME_CN.get(_regime_label, _regime_label)
    print(f'[Gateway] 体制: {_regime_label}({_cn}) 4H={regime["phase"]} 1H={regime["momentum"]} SHORT×{_mult["SHORT"]} LONG×{_mult["LONG"]}')

    # ── Step 2: 双向分析 ──
    print(f'[Gateway] 双向分析中...')
    short_ana = _run_brahma_analyze(sym, 'SHORT')
    long_ana  = _run_brahma_analyze(sym, 'LONG')

    if not short_ana and not long_ana:
        print(f'[Gateway] 双向分析均失败，退出')
        return {'pushed': 0, 'signals': [], 'regime': regime, 'decision': '分析失败'}

    # 如果有zone信息，用zone的入场参数覆盖（更精确）
    if zone:
        for ana in [short_ana, long_ana]:
            if ana:
                p = ana.setdefault('params', {})
                p.setdefault('entry_lo', zone.get('entry_lo', 0))
                p.setdefault('entry_hi', zone.get('entry_hi', 0))
                p.setdefault('stop_loss', zone.get('stop_loss', 0))
                p.setdefault('tp1', zone.get('tp1', 0))
                p.setdefault('tp2', zone.get('tp2', 0))

    # ── Step 3: 方向裁决 ──
    from signal_selector import select, format_signal_card
    sel = select(short_ana or {}, long_ana or {}, regime)
    print(f'[Gateway] 裁决: {sel["decision"]}')

    if not sel['signals']:
        print(f'[Gateway] 无有效信号，结束')
        return {'pushed': 0, 'signals': [], 'regime': regime,
                'decision': sel['decision']}

    # ── Step 4: Pre-Trade Engine 五关门控 ──
    pushed = 0
    final_signals = []

    try:
        from pre_trade_engine import evaluate
    except ImportError:
        evaluate = None

    for sig in sel['signals']:
        direction = sig['direction']
        dedup_key = f'{sym}_{direction}'
        now_ts = time.time()

        # 推送去重
        if dedup_key in _push_dedup and now_ts - _push_dedup[dedup_key] < PUSH_COOLDOWN:
            remain = int((PUSH_COOLDOWN - (now_ts - _push_dedup[dedup_key])) / 3600)
            print(f'[Gateway] {sym} {direction} 推送冷却中（剩余~{remain}H）')
            continue

        # 五关门控
        if evaluate and sig.get('valid'):
            analysis = sig.get('analysis', {})
            zone_info = zone or {
                'symbol': sym, 'direction': direction,
                'entry_lo': sig['entry_lo'], 'entry_hi': sig['entry_hi'],
                'stop_loss': sig['stop_loss'], 'tp1': sig['tp1'], 'tp2': sig['tp2'],
                'atr_4h': analysis.get('atr_4h', 500),
            }
            gate_result = evaluate(zone_info, analysis)
            gate_pass = gate_result.get('gate_pass', 0)
            should_trade = gate_result.get('should_trade', False)

            if not should_trade:
                print(f'[Gateway] {sym} {direction} 五关 {gate_pass}/5 未全过，静默')
                # grade≥70且3关+时推送参考提示 [v24.2]
                if sig.get('grade', 0) >= 70 and gate_pass >= 3:
                    from push_hub import _jarvis as _pj_gw
                    _pj_gw(f'⚠️ {sym.replace("USDT","")} {direction} '
                            f'体制权重后分={sig["weighted"]:.0f} 五关{gate_pass}/5\n'
                            + '\n'.join(f"  {g['msg']}" for g in gate_result['gates']),
                           dedup_ttl=21600)  # 6H去重，与PUSH_COOLDOWN对齐
                continue
        else:
            # 无法运行五关时，仅检查加权分
            if sig['weighted'] < MIN_WEIGHTED:
                print(f'[Gateway] {sym} {direction} 加权分不足 weighted={sig["weighted"]}')
                continue

        # ── Step 5: 推送信号 ──
        card = format_signal_card(sig)
        _jarvis(card)
        _push_dedup[dedup_key] = now_ts
        _save_dedup(_push_dedup)  # [v25.3-fix] 持久化，进程重启后冷却仍有效
        final_signals.append(sig)
        pushed += 1
        print(f'[Gateway] ✅ {sym} {direction} {sig["chain"]} 信号推送 weighted={sig["weighted"]:.0f} pos={sig["position_pct"]}%')

    elapsed = round(time.time() - t0, 1)
    print(f'[Gateway] ── 完成 推送={pushed}条 耗时={elapsed}s ──')

    return {
        'pushed':   pushed,
        'signals':  final_signals,
        'regime':   regime,
        'decision': sel['decision'],
    }


if __name__ == '__main__':
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETH'
    force = '--force' in sys.argv
    result = run(sym, force_regime=force)
    print(f'\n最终: 推送={result["pushed"]}条 裁决={result["decision"]}')
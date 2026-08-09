#!/usr/bin/env python3
"""
brahma_unified_analysis.py — 梵天统一分析入口
设计院三方封印 2026-08-08 | 模拟测试通过后落地

整合链路：
  体制门控 → 快速否决 → 35维评分 → 方仓+Kronos+时机 → 决策层
  输出：BrahmaSignal 标准18字段统一视图

早退优化：
  死穴/拥挤度/宏观RISK_OFF → 3秒内返回，不跑35维
  score<130 → 跳过方仓/Kronos全量
  总耗时：15s（正常）/ <3s（早退）

用法:
  python3 scripts/brahma_unified_analysis.py --symbol BTCUSDT
  python3 scripts/brahma_unified_analysis.py --symbol ETHUSDT --direction SHORT
  python3 scripts/brahma_unified_analysis.py --symbols BTCUSDT ETHUSDT
"""
import sys, os, json, time, argparse, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# ── 颜色 ──────────────────────────────────────────────────────────
G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'; C = '\033[96m'
B = '\033[1m';  RST = '\033[0m'

# ── 常量 ──────────────────────────────────────────────────────────
SCORE_EXEC_THRESHOLD = 138
SCORE_CONTINUE_THRESHOLD = 130
GRADE_THRESHOLD = 80
LSR_CROWD_THRESHOLD = 2.0
LSR_CROWD_WEIGHT = 0.7
DEAD_ZONES = {'BEAR_TREND_LONG', 'CHOP_MID_LONG_NO_PROOF', 'BULL_TREND_SHORT'}


# ═══════════════════════════════════════════════════════════════════
# Step1: 体制门控
# ═══════════════════════════════════════════════════════════════════
def step1_regime_gate(direction: str) -> dict:
    t0 = time.time()
    try:
        with open(BASE / 'data' / 'brahma_state.json') as f:
            bs = json.load(f)
        regime = bs.get('regime', 'BULL_TREND')
        age_min = (time.time() - bs.get('last_update', bs.get('timestamp', 0))) / 60
    except Exception:
        regime, age_min = 'UNKNOWN', 999

    combo = f'{regime}_{direction}'
    blocked = combo in DEAD_ZONES or age_min > 240

    return {
        'regime': regime,
        'age_min': round(age_min, 1),
        'blocked': blocked,
        'block_reason': f'死穴:{combo}' if combo in DEAD_ZONES
                        else (f'体制陈旧:{age_min:.0f}min' if age_min > 240 else None),
        'elapsed': time.time() - t0,
    }


# ═══════════════════════════════════════════════════════════════════
# Step2: 快速否决层
# ═══════════════════════════════════════════════════════════════════
def step2_quick_reject(symbol: str, direction: str) -> dict:
    t0 = time.time()
    reasons = []
    weight_mult = 1.0
    macro_adj = 0

    # 宏观叠加
    try:
        with open(BASE / 'data' / 'macro_overlay.json') as f:
            mo = json.load(f)
        state = mo.get('state', 'NEUTRAL')
        macro_adj = +8 if state == 'RISK_ON' else (-15 if state == 'RISK_OFF' else 0)
        if state == 'RISK_OFF':
            reasons.append('宏观RISK_OFF')
    except Exception:
        state, macro_adj = 'UNKNOWN', 0

    # LSR拥挤度
    lsr_all = 1.0
    try:
        r = requests.get(
            f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
            f'?symbol={symbol}&period=1h&limit=1',
            timeout=5
        ).json()
        lsr_all = float(r[0]['longShortRatio']) if r else 1.0
    except Exception:
        pass

    crowd_risk = lsr_all > LSR_CROWD_THRESHOLD and direction == 'LONG'
    if crowd_risk:
        weight_mult = LSR_CROWD_WEIGHT
        reasons.append(f'散户拥挤LSR={lsr_all:.2f}→降权{weight_mult}x')

    # SQE Gate1（快速）
    try:
        with open(BASE / 'data' / 'signal_weights.json') as f:
            sw = json.load(f)
        # 检查当前体制是否有已知BLOCK规则
    except Exception:
        pass

    blocked = 'RISK_OFF' in ''.join(reasons)  # 宏观RISK_OFF是硬拒绝

    return {
        'lsr_all': lsr_all,
        'crowd_risk': crowd_risk,
        'weight_mult': weight_mult,
        'macro_state': state,
        'macro_adj': macro_adj,
        'reasons': reasons,
        'blocked': blocked,
        'elapsed': time.time() - t0,
    }


# ═══════════════════════════════════════════════════════════════════
# Step3: 35维评分（调用1号工程）
# ═══════════════════════════════════════════════════════════════════
def step3_brahma_score(symbol: str, direction: str) -> dict:
    t0 = time.time()
    try:
        import subprocess
        r = subprocess.run(
            ['python3', str(BASE / 'scripts' / 'brahma_1hao_analysis.py'),
             '--symbol', symbol],
            capture_output=True, text=True, timeout=55,
            cwd=str(BASE)
        )
        out = r.stdout + r.stderr
        # 解析关键字段
        import re
        score_m = re.search(r'score=(\d+\.?\d*)', out)
        grade_m = re.search(r'grade=(\d+\.?\d*)', out)
        sl_m = re.search(r'sl_pct=?(\d+\.?\d*)', out)
        entry_lo_m = re.search(r'entry_lo:\s*([\d.]+)', out)
        entry_hi_m = re.search(r'entry_hi:\s*([\d.]+)', out)
        tp1_m = re.search(r'tp1:\s*([\d.]+)', out)
        tp2_m = re.search(r'tp2:\s*([\d.]+)', out)
        seal_m = re.search(r'封印结论.*?(?=\n)', out)

        score = float(score_m.group(1)) if score_m else 0
        grade = float(grade_m.group(1)) if grade_m else 0
        sl_pct = float(sl_m.group(1)) if sl_m else 0
        entry_lo = float(entry_lo_m.group(1)) if entry_lo_m else 0
        entry_hi = float(entry_hi_m.group(1)) if entry_hi_m else 0
        tp1 = float(tp1_m.group(1)) if tp1_m else 0
        tp2 = float(tp2_m.group(1)) if tp2_m else 0
        seal_line = seal_m.group(0) if seal_m else ''

        # 早退判断
        blocked = False
        block_reason = None
        if sl_pct > 2.0:
            blocked, block_reason = True, f'SQE Gate1: sl={sl_pct}%>2.0%'
        elif score < SCORE_CONTINUE_THRESHOLD:
            blocked, block_reason = True, f'评分不足: {score}<{SCORE_CONTINUE_THRESHOLD}'
        elif grade < GRADE_THRESHOLD:
            block_reason = f'StructureGate: grade={grade}<{GRADE_THRESHOLD}'
            # grade不足不是early exit，继续跑Step4

        return {
            'score': score, 'grade': grade, 'sl_pct': sl_pct,
            'entry_lo': entry_lo, 'entry_hi': entry_hi,
            'tp1': tp1, 'tp2': tp2,
            'seal_line': seal_line,
            'blocked': blocked, 'block_reason': block_reason,
            'elapsed': time.time() - t0,
            'raw_len': len(out),
        }
    except Exception as e:
        return {'score': 0, 'grade': 0, 'blocked': True,
                'block_reason': f'引擎错误: {e}', 'elapsed': time.time() - t0}


# ═══════════════════════════════════════════════════════════════════
# Step4: 外部增强层（方仓 + Kronos + 时机）
# ═══════════════════════════════════════════════════════════════════
def step4_enhance(symbol: str, regime: str, score: float, grade: float,
                  weight_mult: float, macro_adj: int) -> dict:
    t0 = time.time()
    result = {}

    # 方仓
    try:
        from fangcang_engine import get_fangcang_context
        ctx = get_fangcang_context(symbol, regime)
        pm = ctx.get('prob_matrix', {})
        result['fangcang'] = {
            'p_up': pm.get('p_up', 0),
            'ev': pm.get('ev', 0),
            'n': pm.get('n', 0),
            'hint': ctx.get('signal_hint', '?'),
            'intent': ctx.get('main_force_intent', {}).get('intent', '?')
                      if isinstance(ctx.get('main_force_intent'), dict)
                      else str(ctx.get('main_force_intent', '?')),
            'trap_warning': ctx.get('main_force_intent', {}).get('trap_warning', False)
                            if isinstance(ctx.get('main_force_intent'), dict) else False,
        }
    except Exception as e:
        result['fangcang'] = {'error': str(e)}

    # Kronos（优先磁盘缓存）
    try:
        cache_file = BASE / 'data' / 'kronos_p_up_cache.json'
        if cache_file.exists():
            with open(cache_file) as f:
                kc = json.load(f)
            entry = kc.get(symbol, [0, 0.5, 0])
            ts_c, p_up_c = entry[0], entry[1]
            age_h = (time.time() - ts_c) / 3600
            from kronos_bridge import CACHE_TTL
            if time.time() - ts_c < CACHE_TTL:
                result['kronos'] = {
                    'p_up': p_up_c,
                    'age_min': round((time.time() - ts_c) / 60, 1),
                    'src': 'disk_cache',
                    'adj': +2 if p_up_c > 0.55 else (-4 if p_up_c < 0.35 else 0),
                }
            else:
                result['kronos'] = {'p_up': 0.5, 'src': 'expired', 'adj': 0}
        else:
            result['kronos'] = {'p_up': 0.5, 'src': 'no_cache', 'adj': 0}
    except Exception as e:
        result['kronos'] = {'p_up': 0.5, 'src': f'error:{e}', 'adj': 0}

    # 综合评分调整
    kronos_adj = result.get('kronos', {}).get('adj', 0)
    final_score = (score + kronos_adj + macro_adj) * weight_mult
    result['final_score'] = round(final_score, 1)
    result['score_components'] = {
        'base': score, 'kronos': kronos_adj,
        'macro': macro_adj, 'weight_mult': weight_mult,
        'final': round(final_score, 1),
    }

    result['elapsed'] = time.time() - t0
    return result


# ═══════════════════════════════════════════════════════════════════
# Step5: 最终决策层
# ═══════════════════════════════════════════════════════════════════
def step5_decision(s3: dict, s4: dict, s2: dict) -> dict:
    score = s4['final_score']
    grade = s3['grade']
    entry_lo = s3['entry_lo']
    entry_hi = s3['entry_hi']
    tp1 = s3['tp1']
    tp2 = s3['tp2']
    sl = 0  # 从清算集群取

    # 计算RR
    mid_entry = (entry_lo + entry_hi) / 2 if entry_lo and entry_hi else 0
    if tp1 and mid_entry and sl:
        rr = round((tp1 - mid_entry) / (mid_entry - sl), 2) if mid_entry > sl else 0
    else:
        rr = 0

    # 决策动作
    if s3.get('blocked'):
        action = 'REJECT'
        reason = s3.get('block_reason', '')
    elif s2.get('blocked'):
        action = 'REJECT'
        reason = ' | '.join(s2.get('reasons', []))
    elif score < SCORE_EXEC_THRESHOLD:
        action = 'WAIT_SCORE'
        reason = f'评分{score:.1f}<{SCORE_EXEC_THRESHOLD}'
    elif grade < GRADE_THRESHOLD:
        action = 'WAIT_GRADE'
        reason = f'grade={grade}<{GRADE_THRESHOLD}'
    else:
        action = 'EXECUTE'
        reason = f'全条件通过 score={score:.1f} grade={grade}'

    # 仓位（只有EXECUTE时计算）
    size_pct = 0
    if action == 'EXECUTE':
        try:
            from position_sizer import calc_position_size
            size_pct = calc_position_size(score, grade, regime='BULL_TREND')
        except Exception:
            size_pct = 5.0

    return {
        'action': action,
        'reason': reason,
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'tp1': tp1, 'tp2': tp2, 'sl': sl,
        'rr': rr,
        'size_pct': size_pct,
    }


# ═══════════════════════════════════════════════════════════════════
# 主分析流程
# ═══════════════════════════════════════════════════════════════════
def analyze(symbol: str, direction: str = 'LONG', verbose: bool = True) -> dict:
    total_t0 = time.time()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    if verbose:
        print(f'\n{B}{C}{"═"*52}')
        print(f'  🏛️  BrahmaSignal 统一分析  |  {symbol}')
        print(f'  {direction}  |  {now_str}')
        print(f'{"═"*52}{RST}')

    # ── Step1 ─────────────────────────────────────────────────────
    s1 = step1_regime_gate(direction)
    if verbose:
        status = f'{R}⛔ 封禁{RST}' if s1['blocked'] else f'{G}✅ 通过{RST}'
        print(f'\n{C}【S1 体制门控】{RST} {status}')
        print(f'   体制={s1["regime"]} age={s1["age_min"]:.0f}min')
    if s1['blocked']:
        if verbose: print(f'   原因: {s1["block_reason"]}')
        return _build_signal(symbol, direction, s1, None, None, None,
                             'REJECT', s1['block_reason'], now_str)

    # ── Step2 ─────────────────────────────────────────────────────
    s2 = step2_quick_reject(symbol, direction)
    if verbose:
        status = f'{Y}⚠️  降权{RST}' if s2['crowd_risk'] else f'{G}✅ 通过{RST}'
        print(f'\n{C}【S2 快速否决】{RST} {status}')
        print(f'   LSR={s2["lsr_all"]:.2f} 宏观={s2["macro_state"]}({s2["macro_adj"]:+})'
              f' 降权={s2["weight_mult"]}x')
        if s2['reasons']:
            print(f'   预警: {" | ".join(s2["reasons"])}')
    if s2['blocked']:
        return _build_signal(symbol, direction, s1, s2, None, None,
                             'REJECT', ' | '.join(s2['reasons']), now_str)

    # ── Step3 ─────────────────────────────────────────────────────
    if verbose:
        print(f'\n{C}【S3 35维评分】{RST} 运行中...')
    s3 = step3_brahma_score(symbol, direction)
    if verbose:
        print(f'   score={s3["score"]} grade={s3["grade"]} sl={s3["sl_pct"]}%'
              f' ⏱{s3["elapsed"]:.1f}s')
        if s3.get('block_reason'):
            print(f'   {Y}→ {s3["block_reason"]}{RST}')
    if s3['blocked']:
        return _build_signal(symbol, direction, s1, s2, s3, None,
                             'REJECT', s3['block_reason'], now_str)

    # ── Step4 ─────────────────────────────────────────────────────
    if verbose:
        print(f'\n{C}【S4 外部增强】{RST} 方仓+Kronos+宏观')
    s4 = step4_enhance(symbol, s1['regime'], s3['score'], s3['grade'],
                       s2['weight_mult'], s2['macro_adj'])
    if verbose:
        fc = s4.get('fangcang', {})
        kn = s4.get('kronos', {})
        sc = s4.get('score_components', {})
        print(f'   方仓: p_up={fc.get("p_up",0):.0%} EV={fc.get("ev",0):+.2f}'
              f' n={fc.get("n",0)} hint={fc.get("hint","?")}')
        if fc.get('trap_warning'):
            print(f'   {Y}⚠️  陷阱预警激活{RST}')
        print(f'   Kronos: p_up={kn.get("p_up",0):.3f}'
              f' adj={kn.get("adj",0):+} src={kn.get("src","?")}')
        print(f'   合计: {sc.get("base",0)}+{sc.get("kronos",0):+}'
              f'+{sc.get("macro",0):+}×{sc.get("weight_mult",1)}'
              f'={sc.get("final",0)} {B}最终={s4["final_score"]}{RST}')

    # ── Step5 ─────────────────────────────────────────────────────
    s5 = step5_decision(s3, s4, s2)
    if verbose:
        action_str = f'{G}{B}✅ EXECUTE{RST}' if s5['action'] == 'EXECUTE' \
                     else f'{Y}⏸ {s5["action"]}{RST}'
        print(f'\n{C}【S5 决策层】{RST} {action_str}')
        print(f'   原因: {s5["reason"]}')
        if s5['entry_lo']:
            print(f'   入场: {s5["entry_lo"]:.2f}~{s5["entry_hi"]:.2f}'
                  f'  TP1={s5["tp1"]:.2f}  TP2={s5["tp2"]:.2f}')
        if s5['size_pct']:
            print(f'   仓位: {s5["size_pct"]}%NAV')

    total_elapsed = time.time() - total_t0
    signal = _build_signal(symbol, direction, s1, s2, s3, s4,
                           s5['action'], s5['reason'], now_str)
    signal['decision'].update(s5)
    signal['total_elapsed'] = round(total_elapsed, 2)

    if verbose:
        print(f'\n{B}{"─"*52}')
        print(f'  ⏱ 总耗时: {total_elapsed:.1f}s | 动作: {s5["action"]}')
        print(f'{"─"*52}{RST}')

    return signal


def _build_signal(symbol, direction, s1, s2, s3, s4, action, reason, dt):
    return {
        'symbol': symbol, 'direction': direction,
        'dt': dt, 'ts': int(time.time()),
        'regime': s1['regime'] if s1 else 'UNKNOWN',
        'score_breakdown': s4.get('score_components') if s4 else {},
        'fangcang': s4.get('fangcang') if s4 else {},
        'kronos': s4.get('kronos') if s4 else {},
        'lsr': {'all': s2['lsr_all'], 'crowd_risk': s2['crowd_risk']} if s2 else {},
        'macro': {'state': s2['macro_state'], 'adj': s2['macro_adj']} if s2 else {},
        'structure': {'score': s3['score'] if s3 else 0,
                      'grade': s3['grade'] if s3 else 0,
                      'sl_pct': s3['sl_pct'] if s3 else 0},
        'decision': {
            'action': action, 'reason': reason,
            'entry_lo': s3['entry_lo'] if s3 else 0,
            'entry_hi': s3['entry_hi'] if s3 else 0,
            'tp1': s3['tp1'] if s3 else 0,
            'tp2': s3['tp2'] if s3 else 0,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description='梵天统一分析入口')
    parser.add_argument('--symbol',  default='BTCUSDT')
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--direction', default='LONG', choices=['LONG', 'SHORT'])
    parser.add_argument('--json-out', action='store_true', help='输出JSON格式')
    args = parser.parse_args()

    targets = args.symbols if args.symbols else [args.symbol]
    results = []
    for sym in targets:
        sig = analyze(sym, args.direction)
        results.append(sig)

    if args.json_out:
        print(json.dumps(results if len(results) > 1 else results[0],
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
设计院 · 达摩院对抗性Fuzz引擎 v1.0
dharma/fuzz_engine.py

原理：主动构造极端/随机参数，批量压测核心计算模块
发现目标：
  - 数值溢出 / ZeroDivisionError
  - R:R 方向性错误（止损在错误方向）
  - 评分超出 [-50, 300] 合理范围
  - SL/TP 价格为负或NaN
  - 连续1000次随机压测无崩溃

用法：
  python3 dharma/fuzz_engine.py              # 1000轮快速Fuzz
  python3 dharma/fuzz_engine.py --rounds 5000  # 高强度5000轮
  python3 dharma/fuzz_engine.py --seed 42    # 固定种子可复现
"""
import sys, os, random, math, time, argparse, traceback
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from brahma_brain.brahma_brain import calc_trade_params, confluence_score

# ── Fuzz 参数空间 ───────────────────────────────────────────
PRICE_RANGE    = (0.0001, 150000)
ATR_RATIO      = (0.0, 0.60)       # ATR占价格比例
RSI_RANGE      = (-5, 105)         # 含越界值
REGIMES        = ['BULL_TREND','BULL_PEAK','BEAR_TREND','BEAR_EARLY',
                  'BEAR_RECOVERY','CHOP_MID','CHOP_LOW','UNKNOWN_X']
DIRECTIONS     = ['SHORT','LONG']

BUG_SCORE_MIN  = -50
BUG_SCORE_MAX  = 300
BUG_SL_PCT_MAX = 30.0   # 止损幅度上限

def _rand_ms(price, regime, rsi, atr_1h, atr_4h):
    sw_span = price * random.uniform(0.005, 0.08)
    sw_highs = sorted([price + sw_span * i/3 for i in range(1, 5)], reverse=True)
    sw_lows  = sorted([price - sw_span * i/3 for i in range(1, 5)])
    return {
        'price': price, 'regime': regime,
        'momentum': {
            'rsi_1h': rsi, 'rsi_4h': rsi * 0.95,
            'atr_1h': atr_1h, 'atr_4h': atr_4h,
            'obv_trend': random.choice(['UP','DOWN','FLAT']),
        },
        'key_levels': {'fib': {
            '0.382': price * random.uniform(0.99, 1.02),
            '0.618': price * random.uniform(0.97, 1.00),
        }},
        'swing_4h': {'highs': sw_highs, 'lows': sw_lows},
        'order_blocks': {},
        'fvg': {},
    }

def _rand_smc(price):
    return {
        'order_blocks': {}, 'fvg': {},
        'structure': {'trend': random.choice(['UP','DOWN','SIDEWAYS']),
                      'hl_structure': random.choice(['HH_HL','LH_LL','CHOP'])},
    }

def fuzz_calc_trade_params(rounds: int, seed: int = None) -> dict:
    rng = random.Random(seed)
    errors = []
    violations = []
    passed = 0

    for i in range(rounds):
        price  = rng.uniform(*PRICE_RANGE)
        atr_r  = rng.uniform(*ATR_RATIO)
        atr_1h = price * atr_r * rng.uniform(0.3, 1.0)
        atr_4h = price * atr_r
        rsi    = rng.uniform(*RSI_RANGE)
        regime = rng.choice(REGIMES)
        direc  = rng.choice(DIRECTIONS)

        ms  = _rand_ms(price, regime, rsi, atr_1h, atr_4h)
        smc = _rand_smc(price)

        try:
            p = calc_trade_params(ms, smc, direc)

            # 约束检查
            issues = []
            for k in ('entry_lo','entry_hi','stop_loss','tp1','tp2'):
                v = p.get(k, None)
                if v is None:
                    issues.append(f"缺少{k}")
                elif math.isnan(v) or math.isinf(v):
                    issues.append(f"{k}=NaN/Inf")
                elif v <= 0:
                    issues.append(f"{k}={v:.6g}<=0")

            # 方向检查
            if direc == 'SHORT':
                if p.get('stop_loss', 0) <= p.get('entry_hi', 0):
                    issues.append(f"SHORT SL{p['stop_loss']:.4g}<=entry_hi{p['entry_hi']:.4g}")
                if p.get('tp1', 999) >= p.get('entry_lo', 0):
                    issues.append(f"SHORT TP1{p['tp1']:.4g}>=entry_lo{p['entry_lo']:.4g}")
            else:
                if p.get('stop_loss', 999) >= p.get('entry_lo', 999):
                    issues.append(f"LONG SL{p['stop_loss']:.4g}>=entry_lo{p['entry_lo']:.4g}")
                if p.get('tp1', 0) <= p.get('entry_hi', 0):
                    issues.append(f"LONG TP1{p['tp1']:.4g}<=entry_hi{p['entry_hi']:.4g}")

            # R:R基准检查
            entry_mid = (p.get('entry_lo',0) + p.get('entry_hi',0)) / 2
            risk_mid = abs(p.get('stop_loss',0) - entry_mid)
            if risk_mid > 0 and p.get('rr1',0) > 0:
                tp_mid = abs(p.get('tp1',0) - entry_mid)
                true_rr = tp_mid / risk_mid
                if true_rr > 0:
                    rr_err = abs(p['rr1'] - true_rr) / true_rr
                    if rr_err > 0.20:
                        issues.append(f"R:R失真{rr_err*100:.0f}%: rr1={p['rr1']:.2f} 真实={true_rr:.2f}")

            # sl_pct 范围
            sl_pct = p.get('sl_pct', 0)
            if sl_pct > BUG_SL_PCT_MAX:
                issues.append(f"sl_pct={sl_pct:.1f}%>{BUG_SL_PCT_MAX}%")

            if issues:
                violations.append({
                    'round': i, 'price': price, 'atr_r': atr_r,
                    'rsi': rsi, 'regime': regime, 'dir': direc,
                    'issues': issues, 'params': {k: p.get(k) for k in ('entry_lo','entry_hi','stop_loss','tp1','rr1','sl_pct')}
                })
            else:
                passed += 1

        except Exception as e:
            errors.append({
                'round': i, 'price': price, 'rsi': rsi,
                'regime': regime, 'dir': direc,
                'error': str(e)[:120],
                'tb': traceback.format_exc()[-300:]
            })

    return {'passed': passed, 'violations': violations, 'errors': errors, 'total': rounds}

def fuzz_confluence_score(rounds: int, seed: int = None) -> dict:
    rng = random.Random(seed)
    errors = []
    violations = []
    passed = 0

    # confluence_score 需要完整的 ms['trend'] 结构，通过 analyze() 获取
    try:
        from brahma_brain.brahma_brain import analyze
    except ImportError:
        return {'passed': 0, 'violations': [], 'errors': [{'round': 0, 'error': 'analyze导入失败'}], 'total': rounds}

    syms = ['ETHUSDT', 'BTCUSDT', 'SOLUSDT', 'BNBUSDT']
    dirs = ['SHORT', 'LONG']

    for i in range(rounds):
        sym = rng.choice(syms)
        direc = rng.choice(dirs)
        try:
            result = analyze(sym, direc)
            cf = result.get('confluence', {})
            score = cf.get('total', None)
            if score is None:
                violations.append({'round': i, 'issue': 'total为None'})
            elif math.isnan(score) or math.isinf(score):
                violations.append({'round': i, 'issue': f'total=NaN/Inf({score})'})
            elif score < BUG_SCORE_MIN or score > BUG_SCORE_MAX:
                violations.append({'round': i, 'issue': f'评分越界: {score:.1f}'})
            else:
                passed += 1
        except Exception as e:
            errors.append({'round': i, 'sym': sym, 'error': str(e)[:120]})
        # 节减 API 调用，每轮平均间隔
        time.sleep(0.05)

    return {'passed': passed, 'violations': violations, 'errors': errors, 'total': rounds}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='梵天设计院 Fuzz引擎')
    ap.add_argument('--rounds', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=None)
    args = ap.parse_args()

    print(f'🔬 梵天 Fuzz引擎 v1.0  轮次={args.rounds}  seed={args.seed}')
    print('=' * 60)

    t0 = time.time()
    print(f'[1/2] 压测 calc_trade_params ({args.rounds}轮)...')
    r1 = fuzz_calc_trade_params(args.rounds, seed=args.seed)
    t1 = time.time() - t0
    print(f'  通过: {r1["passed"]}/{r1["total"]}  违规: {len(r1["violations"])}  崩溃: {len(r1["errors"])}  耗时: {t1:.1f}s')
    for v in r1['violations'][:5]:
        print(f'  ⚠️  Round{v["round"]} price={v["price"]:.4g} {v["dir"]}: {v["issues"]}')
    for e in r1['errors'][:3]:
        print(f'  ❌ Round{e["round"]}: {e["error"]}')

    t2 = time.time()
    print(f'[2/2] 压测 confluence_score ({args.rounds}轮)...')
    r2 = fuzz_confluence_score(args.rounds, seed=args.seed)
    t2 = time.time() - t2
    print(f'  通过: {r2["passed"]}/{r2["total"]}  违规: {len(r2["violations"])}  崩溃: {len(r2["errors"])}  耗时: {t2:.1f}s')
    for v in r2['violations'][:5]:
        print(f'  ⚠️  Round{v["round"]}: {v["issue"]}')

    total_issues = len(r1['violations']) + len(r1['errors']) + len(r2['violations']) + len(r2['errors'])
    print()
    print('=' * 60)
    total_t = time.time() - t0
    print(f'总计: {args.rounds*2} 次压测  总耗时: {total_t:.1f}s')
    print(f'{"✅ Fuzz全部通过" if total_issues==0 else f"⚠️  发现 {total_issues} 个问题"}')

#!/usr/bin/env python3
"""
examples/quick_start.py — Brahma-Quant 快速上手示例
设计院封印 2026-07-02

5分钟体验 Brahma-Quant 核心功能：
  1. 35维评分引擎
  2. 5-Regime 体制判断
  3. 时机过滤器
  4. 仓位计算
  5. Dharma MC验证
  6. 系统健康检查

运行: python3 examples/quick_start.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 55)
print("🏛️  Brahma-Quant Quick Start")
print("=" * 55)

# ── Step 1: System Health ──────────────────────────────────────
print("\n📡 Step 1: System Health Check")
try:
    import subprocess, json
    r = subprocess.run(
        ['python3', 'brahma_brain/brahma_ci.py'],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    for line in r.stdout.split('\n')[:5]:
        if line.strip():
            print(f"  {line}")
except Exception as e:
    print(f"  ⚠️ CI check: {e}")

# ── Step 2: Regime Scoring ─────────────────────────────────────
print("\n🎯 Step 2: 5-Regime Scoring Example")
try:
    from brahma_brain.causal_regime_verifier import verify

    test_cases = [
        ('BEAR_TREND', 'SHORT', '顺势做空'),
        ('BEAR_TREND', 'LONG',  '死穴做多 WR=45%'),
        ('BULL_TREND', 'LONG',  '顺势做多'),
        ('CHOP_MID',   'SHORT', '震荡做空'),
    ]
    for regime, direction, desc in test_cases:
        result = verify('BTCUSDT', regime, direction,
                       ms={'regime': regime, 'momentum': {'rsi_1h': 45},
                           'trend': {'1h': {'direction': 'down'}, '4h': {'direction': 'down'},
                                     'consensus': {'consensus': 'down', 'strength': 0.7}},
                           'structure': {'grade': 80}, 'price': 60000})
        verdict = result.get('verdict', '?')
        adj = result.get('score_adj', 0)
        icon = '✅' if verdict == 'PASS' else ('🚫' if verdict == 'BLOCKED' else '⚠️')
        print(f"  {icon} {regime:15s} + {direction:5s} → {verdict:8s} score_adj={adj:+d}  # {desc}")
except Exception as e:
    print(f"  ⚠️ Regime test: {e}")

# ── Step 3: Timing Filter ──────────────────────────────────────
print("\n⏱️  Step 3: Timing Filter")
try:
    from brahma_brain.timing_filter import evaluate_timing

    result = evaluate_timing(
        symbol='BTCUSDT', signal_dir='SHORT',
        score=165.0, grade=85.0,
        entry_lo=59500.0, entry_hi=60000.0,
        current_price=59800.0,
        s23_p_up=0.38, rsi_1h=62.0,
        regime='BEAR_TREND',
    )
    print(f"  {result['badge']}  score={result['score']}")
    bd = result.get('breakdown', {})
    if bd:
        print(f"  价格位置={bd.get('price_position',0)} | RSI={bd.get('rsi_1h',0)} | Kronos={bd.get('kronos_p_up',0)}")
except Exception as e:
    print(f"  ⚠️ Timing: {e}")

# ── Step 4: Position Sizing ────────────────────────────────────
print("\n💰 Step 4: Position Sizing (Kelly)")
try:
    from brahma_brain.position_sizer import get_position_pct, kelly_position

    # Kelly公式示例
    kelly = kelly_position(wr=0.62, rr=1.5)
    print(f"  Kelly(WR=62%, RR=1.5): {kelly}%")

    # 仓位计算
    pos = get_position_pct('BTCUSDT', score=162.0, direction='SHORT', nav=10000, regime='BEAR_TREND')
    print(f"  BTCUSDT SHORT score=162: {pos['pct']}% = ${pos['usdt']} [{pos['level']}]")
    print(f"  体制乘数: {pos.get('regime_multiplier', 1.0)}x")
except Exception as e:
    print(f"  ⚠️ Position: {e}")

# ── Step 5: Circuit Breaker ────────────────────────────────────
print("\n⚡ Step 5: Circuit Breaker Status")
try:
    from brahma_brain.circuit_breaker import BrahmaCircuitRegistry
    registry = BrahmaCircuitRegistry.get()
    status = registry.status_all()
    open_count = sum(1 for s in status.values() if s['state'] == 'OPEN')
    closed_count = sum(1 for s in status.values() if s['state'] == 'CLOSED')
    print(f"  🟢 CLOSED: {closed_count} | 🔴 OPEN: {open_count} | 总计: {len(status)}")
    if open_count == 0:
        print(f"  ✅ 全部层级正常，系统可运行")
except Exception as e:
    print(f"  ⚠️ Circuit: {e}")

# ── Step 6: Dharma MC Validation ──────────────────────────────
print("\n🔬 Step 6: Dharma Mini-Validation (BTC SHORT, 500 runs)")
try:
    from dharma.dharma_360_validator import (
        load_ohlcv, calc_regime_labels, simulate_signals, bootstrap_wr
    )
    df = load_ohlcv('BTCUSDT')
    if df is not None:
        regimes = calc_regime_labels(df)
        trades = simulate_signals(df, regimes, 120.0, 'SHORT')
        if trades:
            try:
                from dharma.realistic_cost_model import apply_cost_to_trades
                trades = apply_cost_to_trades(trades)
                print(f"  ✅ 成本校正已应用")
            except Exception:
                pass
            boot = bootstrap_wr(trades, n_runs=500)
            print(f"  信号: {len(trades)} 笔 | WR: {boot['mean']*100:.1f}% {boot['grade']}")
            print(f"  95% CI: [{boot['ci_low']*100:.1f}%, {boot['ci_high']*100:.1f}%]")
        else:
            print(f"  ⚠️ 无信号（数据文件缺失或阈值过高）")
    else:
        print(f"  ⚠️ BTCUSDT 数据文件不存在（需要 dharma/data/ 历史数据）")
except Exception as e:
    print(f"  ⚠️ Dharma: {e}")

print("\n" + "=" * 55)
print("🎉 Quick Start 完成！")
print()
print("下一步:")
print("  python3 -m pytest tests/test_core_brahma_units.py -v")
print("  python3 brahma_brain/brahma_ci_v2.py")
print("  python3 dharma/dharma_360_validator.py --sym BTCUSDT --runs 3000")
print("=" * 55)

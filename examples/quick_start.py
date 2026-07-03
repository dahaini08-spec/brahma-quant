#!/usr/bin/env python3
"""
Brahma-Quant Quick Start Demo
Run: python examples/quick_start.py
"""
import sys, os, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

print("=" * 60)
print("🏛️  Brahma-Quant — Live Demo")
print("=" * 60)

# Step 1: Market data
print("\n📡 Step 1: Fetching real-time market data...")
import requests
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
market = {}
for sym in SYMS:
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym}', timeout=5).json()
    market[sym] = {'price': float(r['lastPrice']), 'chg': float(r['priceChangePercent'])}
    print(f"  {sym:<12} ${market[sym]['price']:>10,.2f}  ({market[sym]['chg']:+.2f}%)")

# Step 2: 35-dim scoring
print("\n🧠 Step 2: Running 35-dimensional scoring engine...")
from brahma_brain.brahma_orchestrator import analyze

signals = []
for sym in SYMS:
    for direction in ['LONG', 'SHORT']:
        print(f"  Analyzing {sym} {direction}...", end='', flush=True)
        t0 = time.time()
        result = analyze(sym, signal_dir=direction, deep=False)
        elapsed = time.time() - t0
        regime = result.get('regime', '?')
        grade  = result.get('grade', 0)
        blocked = result.get('blocked', False)
        print(f" {elapsed:.1f}s | regime={regime} grade={grade} {'🚫BLOCKED' if blocked else '✅'}")
        if not blocked:
            signals.append({'sym': sym, 'dir': direction, 'regime': regime, 'grade': grade, 'price': market[sym]['price']})

# Step 3: Signal output
print("\n📊 Step 3: Signal summary")
print(f"  {'Symbol':<12} {'Direction':<8} {'Regime':<18} {'Grade':>6}")
print(f"  {'-'*50}")
for s in sorted(signals, key=lambda x: x['grade'], reverse=True):
    icon = '🔴' if s['grade'] < 70 else '🟡' if s['grade'] < 85 else '🟢'
    print(f"  {icon} {s['sym']:<10} {s['dir']:<8} {s['regime']:<18} {s['grade']:>6}")

print("\n✅ Demo complete. See brahma_brain/ for full pipeline.")
print("=" * 60)

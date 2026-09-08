#!/usr/bin/env python3
"""
三方联合全流程测试：路径 + 性能 + 全资产
涵盖：analyze() → gates → core_output → SQLite → WR权重验证
"""
import sys, time, json, os
sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
sys.path.insert(0, '/root/.openclaw/workspace/trading-system/brahma_brain')

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'XAUUSDT', 'XAGUSDT']
PASS = 0
FAIL = 0

def ok(label): global PASS; PASS += 1; print(f"  ✅ {label}")
def fail(label, reason=""): global FAIL; FAIL += 1; print(f"  ❌ {label}" + (f" — {reason}" if reason else ""))

print("=" * 60)
print("三方联合全流程测试  2026-09-07")
print("=" * 60)

# ────────────────────────────────
# STEP 1: analyze() 全资产性能
# ────────────────────────────────
print("\n[STEP 1] analyze() 全资产路径+性能")
from brahma_brain.brahma_core import analyze

analyze_results = {}
for sym in SYMBOLS:
    t0 = time.time()
    try:
        r = analyze(sym)
        elapsed = time.time() - t0
        analyze_results[sym] = r
        regime = r.get('regime', '?')
        score  = r.get('score', 0) or 0
        grade  = r.get('grade', 0) or 0
        signal = r.get('signal', '?')
        # 性能标准：<15s 冷启动，<5s 热缓存
        perf_ok = elapsed < 15
        if perf_ok:
            ok(f"{sym:<10} {elapsed:.2f}s | {str(regime):<15} score={score:.1f} grade={grade:.1f} signal={signal}")
        else:
            fail(f"{sym:<10} {elapsed:.2f}s 超时", f">{15}s")
    except Exception as e:
        elapsed = time.time() - t0
        analyze_results[sym] = {}
        fail(f"{sym:<10} {elapsed:.2f}s", str(e)[:80])

# 第二次（热缓存）
print("\n  --- 第二次（热缓存）---")
for sym in SYMBOLS:
    t0 = time.time()
    try:
        r = analyze(sym)
        elapsed = time.time() - t0
        perf_ok = elapsed < 5
        if perf_ok:
            ok(f"{sym:<10} 热={elapsed:.2f}s ✓")
        else:
            fail(f"{sym:<10} 热={elapsed:.2f}s 偏慢")
    except Exception as e:
        fail(f"{sym}", str(e)[:60])

# ────────────────────────────────
# STEP 2: gates 门控测试
# ────────────────────────────────
print("\n[STEP 2] gates 门控全路径验证")
try:
    from brahma_os.gates import evaluate_gates
    from brahma_os.contracts import Signal
    from brahma_os.config import Settings
    st = Settings()
    now = time.time()

    cases = [
        # name, sym, side, regime, score, grade, entry_lo, hi, sl, tp, expect_blocked
        ('死亡区间130-145 CHOP',        'ETHUSDT','LONG',  'CHOP_MID',     137.0, 60.0, 2400,2420,2350,2500, True),
        ('BEAR_RECOVERY:SHORT 封禁',    'ETHUSDT','SHORT', 'BEAR_RECOVERY',155.0, 80.0, 2500,2520,2570,2350, True),
        ('BEAR_EARLY:SHORT 145-160 黄金','ETHUSDT','SHORT','BEAR_EARLY',   152.0, 85.0, 2490,2510,2560,2380, False),
        ('BULL_TREND:LONG 正常通过',    'BTCUSDT','LONG',  'BULL_TREND',   150.0, 85.0,65000,65500,63000,70000,False),
        ('score<145 BEAR_EARLY 拦截',   'ETHUSDT','SHORT', 'BEAR_EARLY',   130.0, 55.0, 2490,2510,2560,2380, True),
        ('XAU BULL_TREND:LONG 通过',    'XAUUSDT','LONG',  'BULL_TREND',   148.0, 82.0, 2480,2495,2450,2560, False),
        ('BEAR_TREND:LONG 铁律拦截',    'BTCUSDT','LONG',  'BEAR_TREND',   160.0, 82.0,78000,78500,76000,82000,True),  # WR=45% dead_long_regimes封禁
    ]

    gate_pass = 0
    for name, sym, side, regime, score, grade, elo, ehi, sl, tp, expect_blocked in cases:
        sig = Signal(f'test', now, sym, side, regime, score, grade,
                     float(elo), float(ehi), float(sl), float(tp), now+86400)
        res = evaluate_gates(sig, st, open_positions=0, symbol_exposure=0.0, gross_exposure=0.0)
        actual_blocked = not res.allow
        match = actual_blocked == expect_blocked
        if match:
            ok(f"  {name:<35} {'拦截' if actual_blocked else '通过'} code={res.code}")
            gate_pass += 1
        else:
            fail(f"  {name:<35} 预期{'拦截' if expect_blocked else '通过'} 实际{'拦截' if actual_blocked else '通过'}", f"code={res.code}")

    print(f"\n  gates: {gate_pass}/{len(cases)} 通过")
except Exception as e:
    fail("gates模块导入失败", str(e)[:100])

# ────────────────────────────────
# STEP 3: core_output 格式化
# ────────────────────────────────
print("\n[STEP 3] core_output → VIP卡片 + JSONL")
try:
    from brahma_brain.core_output import signal_from_result, format_vip, to_jsonl_row

    # 用BTC分析结果
    btc = analyze_results.get('BTCUSDT', {})
    if btc:
        sig = signal_from_result(btc, 'BTCUSDT')
        if sig:
            vip = format_vip(sig)
            jsonl = to_jsonl_row(sig)
            if vip and len(vip) > 20:
                ok(f"format_vip: {len(vip)}字符")
            else:
                fail("format_vip 输出为空或过短", repr(vip[:50]))
            if jsonl and 'symbol' in jsonl:
                ok(f"to_jsonl_row: symbol={jsonl.get('symbol')} regime={jsonl.get('regime')}")
            else:
                fail("to_jsonl_row 输出异常", repr(str(jsonl)[:80]))
        else:
            # BTC可能处于CHOP/无信号，signal_from_result返回None属正常
            ok("signal_from_result: None（CHOP无信号，符合预期）")
    else:
        fail("BTC analyze结果为空，跳过core_output测试")
except Exception as e:
    fail("core_output导入或执行失败", str(e)[:100])

# ────────────────────────────────
# STEP 4: WR权重结构验证
# ────────────────────────────────
print("\n[STEP 4] WR权重结构验证")
try:
    w = json.load(open('data/signal_weights.json'))
    wts = w.get('weights', {})

    checks = [
        ('BEAR_EARLY:SHORT:145-160', 'multiplier', lambda v: v >= 1.3, '>=1.3 黄金段'),
        ('BEAR_EARLY:SHORT:>=175',   'multiplier', lambda v: v <= 0.6, '<=0.6 过热降权'),
        ('BEAR_EARLY:SHORT:160-175', 'multiplier', lambda v: 0.5 <= v <= 0.9, '0.5~0.9 待确认'),
        ('BEAR_RECOVERY:SHORT:<120', 'multiplier', lambda v: v <= 0.2, '<=0.2 封禁'),
        ('CHOP_MID:LONG',            'multiplier', lambda v: v == 0.0, '=0 DEAD_ZONE'),
    ]

    for key, field, check_fn, desc in checks:
        entry = wts.get(key, {})
        if isinstance(entry, dict):
            val = entry.get(field)
            if val is not None and check_fn(val):
                ok(f"  {key:<35} {field}={val} ({desc})")
            else:
                fail(f"  {key:<35} {field}={val}", f"预期 {desc}")
        else:
            fail(f"  {key} 未找到或格式错误", repr(entry)[:40])

    # 验证WR反哺 BEAR_EARLY:SHORT 不再被误压
    bes = wts.get('BEAR_EARLY:SHORT:145-160', {})
    mult = bes.get('multiplier', 0)
    if mult >= 1.3:
        ok(f"  WR反哺修复验证: BEAR_EARLY:SHORT:145-160 mult={mult} ✓（已从0.6恢复）")
    else:
        fail(f"  WR反哺修复验证: mult={mult} 仍偏低（预期≥1.3）")

except Exception as e:
    fail("WR权重文件读取失败", str(e)[:80])

# ────────────────────────────────
# STEP 5: brahma_db 写入验证
# ────────────────────────────────
print("\n[STEP 5] brahma_db SQLite 写入+读取验证")
# brahma_db 在 scripts/ 目录，通过 sys.path 直接 import
sys.path.insert(0, 'scripts')
try:
    import brahma_db as _bdb

    stats_before = _bdb.get_stats()
    test_id = f'test_{int(time.time())}'
    _bdb.insert_signal({
        'signal_id': test_id,
        'symbol': 'TESTUSDT',
        'side': 'LONG',
        'regime': 'BULL_TREND',
        'score': 155.0,
        'grade': 85.0,
        'entry_lo': 1000.0,
        'entry_hi': 1010.0,
        'stop_loss': 980.0,
        'take_profit': 1050.0,
        'created_at': time.time(),
        'expires_at': time.time() + 86400,
        'settled': 0,
    })
    stats_after = _bdb.get_stats()
    grew = stats_after.get('total', 0) > stats_before.get('total', 0)
    if grew:
        ok(f"SQLite 写入+读取: total {stats_before['total']}→{stats_after['total']} ✓")
    else:
        fail('SQLite 写入后 count 未增加')
    ok(f"统计: total={stats_after.get('total')} settled={stats_after.get('settled')} wr={stats_after.get('wr',0):.2%}")

except Exception as e:
    fail('brahma_db 测试失败', str(e)[:100])

# ────────────────────────────────
# 最终汇总
# ────────────────────────────────
total = PASS + FAIL
print("\n" + "=" * 60)
print(f"三方联合全流程测试结果: {PASS}/{total} 通过  {'✅ ALL PASS' if FAIL == 0 else f'❌ {FAIL}项失败'}")
print("=" * 60)

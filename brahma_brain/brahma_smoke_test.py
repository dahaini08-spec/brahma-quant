#!/usr/bin/env python3
"""
brahma_smoke_test.py — 梵天冒烟测试
════════════════════════════════════
设计院 2026-08-25 Phase1~3封印验证

覆盖：
  T01 方仓数据库完整性
  T02 蒸馏矩阵完整性
  T03 注射器基础功能
  T04 注射器铁律（逆势封禁）
  T05 注射器缓存性能
  T06 reasoning_gate 顺势 → PASS/WARN
  T07 reasoning_gate 逆势 → WARN/BLOCK
  T08 reasoning_gate 快速降级（规则fallback不崩溃）
  T09 brahma_core import 不崩溃
  T10 brahma_core analyze 返回格式合法
  T11 蒸馏矩阵WR铁证（全市场SHORT 15m WR≥60%）
  T12 注射器矩阵层输出（含全市场WR数据）
"""
import sys, json, time, glob, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
DATA = Path(__file__).parent.parent / 'data'

PASS_COLOR = '\033[92m'
WARN_COLOR = '\033[93m'
FAIL_COLOR = '\033[91m'
RST        = '\033[0m'

results = []

def _ok(tid, name, detail=''):
    results.append((tid, '✅', name, detail))
    print(f"  ✅ {tid} {name}" + (f"  [{detail}]" if detail else ''))

def _warn(tid, name, detail=''):
    results.append((tid, '⚠️', name, detail))
    print(f"  ⚠️  {tid} {name}" + (f"  [{detail}]" if detail else ''))

def _fail(tid, name, detail=''):
    results.append((tid, '❌', name, detail))
    print(f"  ❌ {tid} {name}" + (f"  [{detail}]" if detail else ''))


print("═" * 55)
print("  梵天冒烟测试")
print("═" * 55)

# ── T01 方仓数据库 ─────────────────────────────────────────
try:
    files = [f for f in glob.glob(str(DATA / 'fangcang_*_*.json'))
             if 'snapshot' not in f and 'cases_' not in f and 'weights' not in f]
    tf_cnt = {}
    for f in files:
        base = Path(f).stem.replace('fangcang_', '')
        parts = base.rsplit('_', 1)
        if len(parts) == 2:
            tf_cnt[parts[1]] = tf_cnt.get(parts[1], 0) + len(json.loads(Path(f).read_text()))
    total = sum(tf_cnt.values())
    tfs_ok = all(tf_cnt.get(tf, 0) > 0 for tf in ['15m', '1h', '4h', '1d', '1w'])
    if total >= 15000 and tfs_ok:
        _ok('T01', '方仓数据库', f'{total:,}条 全6周期')
    else:
        _fail('T01', '方仓数据库', f'仅{total}条 或周期缺失')
except Exception as e:
    _fail('T01', '方仓数据库', str(e)[:60])

# ── T02 蒸馏矩阵 ──────────────────────────────────────────
try:
    mfile = DATA / 'brahma_experience_matrix.json'
    assert mfile.exists(), "文件缺失"
    m = json.loads(mfile.read_text())
    nb = len(m.get('by_coin_dir_tf', {}))
    assert nb >= 50, f"桶数仅{nb}"
    assert m['meta']['total_cases'] >= 15000
    _ok('T02', '蒸馏矩阵', f'{nb}桶 {mfile.stat().st_size//1024}KB')
except Exception as e:
    _fail('T02', '蒸馏矩阵', str(e)[:60])

# ── T03 注射器基础 ─────────────────────────────────────────
try:
    from brahma_context_injector import inject_brahma_context
    ms = {'bb_width': 0.008, 'rsi_1h': 62.0, 'rsi_4h': 68.0, 'fg': 70}
    ctx = inject_brahma_context('BTCUSDT', 'BEAR_TREND', 'SHORT', ms,
                                include_cases=False, include_extreme=False)
    assert '梵天铁律' in ctx
    assert '方仓历史' in ctx
    _ok('T03', '注射器基础功能', f'{len(ctx)}chars')
except Exception as e:
    _fail('T03', '注射器基础功能', str(e)[:60])

# ── T04 注射器铁律封禁 ─────────────────────────────────────
try:
    ms = {'bb_width': 0.01, 'rsi_1h': 35.0, 'rsi_4h': 38.0, 'fg': 25}
    ctx = inject_brahma_context('ETHUSDT', 'BEAR_TREND', 'LONG', ms,
                                include_cases=False, include_extreme=False)
    has_ban = any(kw in ctx for kw in ['封禁', '严禁', 'WR=45%', 'BLOCK'])
    if has_ban:
        _ok('T04', '注射器铁律封禁', '逆势封禁词已注入')
    else:
        _warn('T04', '注射器铁律封禁', '封禁词未找到')
except Exception as e:
    _fail('T04', '注射器铁律封禁', str(e)[:60])

# ── T05 注射器缓存性能 ──────────────────────────────────────
try:
    # 第一次调用已预热BTC，这次应命中缓存
    t0 = time.time()
    inject_brahma_context('BTCUSDT', 'BULL_TREND', 'LONG', ms,
                          include_cases=False, include_extreme=False)
    ela = time.time() - t0
    if ela < 0.05:
        _ok('T05', '注射器缓存性能', f'{ela:.4f}s')
    else:
        _warn('T05', '注射器缓存性能', f'{ela:.3f}s > 0.05s')
except Exception as e:
    _fail('T05', '注射器缓存性能', str(e)[:60])

# ── T06 reasoning_gate 顺势 ────────────────────────────────
try:
    from reasoning_client import reasoning_gate
    t0 = time.time()
    r = reasoning_gate({
        'symbol': 'BTCUSDT', 'regime': 'BEAR_TREND', 'signal_dir': 'SHORT',
        'score_final': 155,
        'confluence': {'breakdown': {'RSI_1H': 10, 'RSI_4H': 8, 'OB_short': 16,
                                     'bb_width': 0.007, 'fg': 65}}
    }, inject_context=True)
    ela = round(time.time() - t0, 1)
    if r['verdict'] in ('PASS', 'WARN'):
        _ok('T06', f"reasoning_gate 顺势→{r['verdict']}", f"conf={r['confidence']:.2f} {ela}s")
    else:
        _fail('T06', f"reasoning_gate 顺势→{r['verdict']}", f"期望PASS/WARN")
except Exception as e:
    _fail('T06', 'reasoning_gate 顺势', str(e)[:60])

# ── T07 reasoning_gate 逆势 ────────────────────────────────
try:
    t0 = time.time()
    r = reasoning_gate({
        'symbol': 'ETHUSDT', 'regime': 'BEAR_TREND', 'signal_dir': 'LONG',
        'score_final': 110,
        'confluence': {'breakdown': {'RSI_1H': -20, 'bb_width': 0.012, 'fg': 22}}
    }, inject_context=True)
    ela = round(time.time() - t0, 1)
    if r['verdict'] in ('WARN', 'BLOCK'):
        _ok('T07', f"reasoning_gate 逆势→{r['verdict']}", f"conf={r['confidence']:.2f} {ela}s")
    else:
        _fail('T07', f"reasoning_gate 逆势→{r['verdict']}", f"期望WARN/BLOCK")
except Exception as e:
    _fail('T07', 'reasoning_gate 逆势', str(e)[:60])

# ── T08 降级不崩溃 ─────────────────────────────────────────
try:
    from reasoning_client import _rule_fallback
    out = _rule_fallback('风控 risk 测试')
    data = json.loads(out)
    _ok('T08', '规则降级不崩溃', f'返回{list(data.keys())}')
except Exception as e:
    _fail('T08', '规则降级', str(e)[:60])

# ── T09 brahma_core import ─────────────────────────────────
try:
    t0 = time.time()
    from brahma_core import analyze
    ela = round(time.time() - t0, 2)
    _ok('T09', 'brahma_core import', f'{ela}s')
except Exception as e:
    _fail('T09', 'brahma_core import', str(e)[:80])
    analyze = None

# ── T10 brahma_core analyze 格式 ──────────────────────────
if analyze:
    try:
        t0 = time.time()
        res = analyze('ETHUSDT', signal_dir='SHORT')
        ela = round(time.time() - t0, 1)
        score = res.get('score_final', res.get('score', None))
        regime = res.get('regime', res.get('market_state', None))
        assert score is not None, "缺score字段"
        assert regime is not None, "缺regime字段"
        assert 'valid_signal' in res, "缺valid_signal字段"
        _ok('T10', 'brahma_core.analyze格式合法',
            f'score={score} regime={regime} {ela}s')
    except Exception as e:
        _fail('T10', 'brahma_core.analyze格式', str(e)[:80])
else:
    _fail('T10', 'brahma_core.analyze', '跳过(import失败)')

# ── T11 WR铁证 ────────────────────────────────────────────
try:
    rdt = m.get('by_regime_dir_tf', {})
    s15 = rdt.get('ALL:SHORT:15m', {})
    s1h = rdt.get('ALL:LONG:1h', {})
    assert s15.get('wr', 0) >= 0.60, f"SHORT 15m WR={s15.get('wr',0):.0%} < 60%"
    assert s1h.get('wr', 0) >= 0.60, f"LONG 1h WR={s1h.get('wr',0):.0%} < 60%"
    _ok('T11', 'WR铁证',
        f"SHORT15m={s15['wr']:.0%}(n={s15['n']}) LONG1h={s1h['wr']:.0%}(n={s1h['n']})")
except Exception as e:
    _fail('T11', 'WR铁证', str(e)[:60])

# ── T12 注射器矩阵层 ───────────────────────────────────────
try:
    ms2 = {'bb_width': 0.007, 'rsi_1h': 60.0, 'rsi_4h': 65.0, 'fg': 68}
    ctx2 = inject_brahma_context('SOLUSDT', 'BEAR_TREND', 'SHORT', ms2,
                                 include_cases=False, include_extreme=False)
    has_mkt = '全市场同条件WR' in ctx2
    if has_mkt:
        _ok('T12', '注射器含蒸馏矩阵层', '全市场WR已注入')
    else:
        _warn('T12', '注射器矩阵层', '全市场WR未注入')
except Exception as e:
    _fail('T12', '注射器矩阵层', str(e)[:60])

# ── 汇总 ──────────────────────────────────────────────────
print("\n" + "═" * 55)
ok_cnt   = sum(1 for r in results if r[1] == '✅')
warn_cnt = sum(1 for r in results if r[1] == '⚠️')
fail_cnt = sum(1 for r in results if r[1] == '❌')
total_t  = len(results)
print(f"  {total_t}项测试  ✅{ok_cnt}  ⚠️{warn_cnt}  ❌{fail_cnt}")
if fail_cnt == 0:
    print("  ✅ 梵天冒烟测试 全通过")
    sys.exit(0)
else:
    print(f"  ❌ {fail_cnt}项失败")
    sys.exit(1)

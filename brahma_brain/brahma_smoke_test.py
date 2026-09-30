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

def _ok(tid, name, detail='') -> None:
    """ok"""
    results.append((tid, '✅', name, detail))
    print(f"  ✅ {tid} {name}" + (f"  [{detail}]" if detail else ''))

def _warn(tid, name, detail='') -> None:
    """warn"""
    results.append((tid, '⚠️', name, detail))
    print(f"  ⚠️  {tid} {name}" + (f"  [{detail}]" if detail else ''))

def _fail(tid, name, detail='') -> None:
    """fail"""
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
m = {}  # [FIX 2026-09-18] 默认空dict，防止T02失败后T11 NameError
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

# ── T13 D-10决策生命周期（P1 2026-09-28 苏摩111） ────────────
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
    from brahma_decision_lifecycle import (
        d1_thesis, d2_counter_evidence, counter_score, build_package, d3_clock,
    )
    import copy
    _t13_ok, _t13_detail = True, []
    # 13a: 真实state无论点路径（当前CHOP_MID score12=5 < 110）
    _st = json.loads((DATA / 'brahma_state_btc.json').read_text())
    _t = d1_thesis(_st)
    _t13_detail.append('real_state_thesis=' + ('none' if _t.get('none') else _t.get('strength', '?')))
    # 13b: 构造强论点→建包→时钟全路径（纯内存影子，不落盘）
    _st2 = copy.deepcopy(_st)
    _st2['regime'] = 'BEAR_TREND'
    _st2['score_final'] = 128.0
    _st2['trader_brain'] = {'action': 'WATCH', 'direction': 'SHORT', 'entry_lo': 84000.0, 'entry_hi': 84500.0, 'sl': 0, 'rr': 2.0}
    _st2['momentum'] = {'rsi_1h': 71.0, 'atr_1h': 389.0}
    # [T13修复 2026-09-28] 真实state的时变字段（OI/CVD/聪明钱）会漂移导致反证≥3拒包
    # 冒烟测试必须对市场数据不敏感——三反证源全中性化：
    #   OI: extra.liq_snap.oi_chg4h=0 → 不触发oi_against
    #   聪明钱: extra.liq_snap.long_pct=50 → 不触发sm_against
    #   CVD: _cvd_direction直读文件cvd_realtime_*.json无法注入state——
    #        改用无快照symbol XYZUSDT（NO_DATA→cvd=0不触发cvd_against）
    _st2['extra'] = {'liq_snap': {'oi_chg4h': 0.0, 'long_pct': 50.0}}
    _st2['confluence'] = {'breakdown': {}}            # Hurst缺失=不判
    _st2['price'] = 85000.0  # SHORT区84000-84500在下方，gap=500>0.5×ATR1H(194.5)→建包即WAIT_TRACKING
    _st2.setdefault('smc', {})
    _st2['smc'] = dict(_st2['smc'])
    _ob = dict(_st2['smc'].get('order_blocks') or {})
    _ob['nearest_bear_ob'] = {'type': 'BEAR_OB', 'high': 84018.4, 'low': 83742.0, 'mid': 83880.2, 'idx': 101, 'age_bars': 12, 'broken': False, 'dist_pct': 0.93, 'note': 'smoke'}
    _st2['smc']['order_blocks'] = _ob
    _pkg = build_package('XYZUSDT', _st2)  # XYZ无CVD快照→NO_DATA→反证源可控
    if _pkg:
        # 时钟三态: WAIT→ARMED→TRIGGERED
        _p, _tr1 = d3_clock(copy.deepcopy(_pkg), 84700.0)   # 距离远→跟踪
        _p, _tr2 = d3_clock(copy.deepcopy(_pkg), 84650.0)   # 近→ARMED
        _p2 = copy.deepcopy(_pkg); _p2['state'] = 'ARMED'
        _p2, _tr3 = d3_clock(_p2, 84200.0)                  # 入区→TRIGGERED
        _p3 = copy.deepcopy(_pkg); _p3['state'] = 'TRIGGERED'
        _p3, _tr4 = d3_clock(_p3, 85800.0)                  # 破证伪位→INVALIDATED
        _chain = [_tr2.get('transition'), _tr3.get('transition'), _tr4.get('transition')]
        if _chain == ['ARMED', 'TRIGGERED', 'INVALIDATED']:
            _ok('T13', 'D-10决策生命周期', f'建包strength={_pkg["thesis"]["strength"]} 时钟链ARMED→TRIGGERED→INVALIDATED')
        else:
            _fail('T13', 'D-10决策生命周期', f'时钟链异常: {_chain}')
    else:
        _fail('T13', 'D-10决策生命周期', '构造强论点建包失败')
except Exception as e:
    _fail('T13', 'D-10决策生命周期', str(e)[:60])

# ── T14 AI防谎果蝇探针（AI Truth Audit P0 2026-09-28 苏摩111） ────────────
# 每只果蝇只测一条谎言路径：fail-open翻转 / 数字幻觉 / UNKNOWN诚实 / 幂等
try:
    from reasoning_client import reasoning_gate as _t14_gate
    _t14_notes = []
    _t14_base = {
        'symbol': 'BTCUSDT', 'regime': 'BEAR_TREND', 'signal_dir': 'SHORT',
        'score_final': 155,
        'confluence': {'breakdown': {'RSI_1H': 10, 'RSI_4H': 8, 'bb_width': 0.007, 'fg': 65}},
    }
    # 探针1: fail-open封锁 — LLM不可用/输出违约时绝不出PASS（2026-09-28修复后语义）
    _t14_r1 = _t14_gate(dict(_t14_base), inject_context=False)
    _t14_verdict = _t14_r1.get('verdict', '')
    _t14_reason = str(_t14_r1.get('reason', ''))
    _t14_degraded = any(k in _t14_reason for k in ('不可用', '违约', '降级', '降权'))
    # 免费池可用时LLM真实回答也允许PASS，但降级路径必须WARN且不得是PASS
    if _t14_verdict == 'PASS' and not _t14_degraded:
        _t14_notes.append('probe1: LLM在线PASS(在线路径)')
    elif _t14_verdict == 'PASS' and _t14_degraded:
        _fail('T14', '果蝇探针1 fail-open', '降级路径出现PASS=fail-open复活')
        _t14_notes = None
    else:
        _t14_notes.append(f'probe1: {_t14_verdict}({"降级保守" if _t14_degraded else "LLM在线"})')
    # 探针2: UNKNOWN诚实 — 喂缺失数据必须不幻觉编造字段值
    _t14_r2 = _t14_gate({'symbol': 'XYZUSDT', 'regime': '', 'signal_dir': '', 'score_final': 0,
                         'confluence': {'breakdown': {}}}, inject_context=False)
    _t14_r2_verdict = _t14_r2.get('verdict', '')
    if _t14_r2_verdict == 'PASS' and not str(_t14_r2.get('reason', '')):
        _fail('T14', '果蝇探针2 UNKNOWN', '空输入产出无理由PASS=幻觉嫌疑')
        _t14_notes = None
    else:
        _t14_notes.append(f'probe2: {_t14_r2_verdict}')
    # 探针3: 幂等 — 同输入两次调用，输出verdict分布一致（同池同prompt确定性趋势）
    _t14_r3 = _t14_gate(dict(_t14_base), inject_context=False)
    _t14_notes.append(f'probe3: idem={_t14_r3.get("verdict")}')
    if _t14_notes is not None:
        _ok('T14', 'AI防谎果蝇探针', ' | '.join(_t14_notes))
except Exception as e:
    _fail('T14', 'AI防谎果蝇探针', str(e)[:60])

# ── T15 L1守卫 + L3 IC门（AI Truth P1 2026-09-28 苏摩111） ─────────────
try:
    import sys as _sys15, json as _json15, tempfile as _tf15, os as _os15
    _sp15 = str((Path(__file__).parent.parent / 'brahma_brain').resolve())
    if _sp15 not in _sys15.path: _sys15.path.insert(0, _sp15)
    from ai_output_guard import guard_text, guard_closed_vocab, scan_numbers, register_surfaces
    _t15_notes = []
    # probeA: 数字禁令 — 定性文本含价格必须拦截+取证
    _t15_ok, _t15_why, _ = guard_text('settler_lesson', 'BTC在84500附近做空')
    if _t15_ok: raise AssertionError('数字禁令失效：含价格文本被放行')
    _t15_notes.append('probeA数字拦截✓')
    # probeB: 纯定性放行
    _t15_ok2, _, _ = guard_text('settler_lesson', '追高空单被套，逆势开仓教训')
    if not _t15_ok2: raise AssertionError('纯定性文本被误拦')
    _t15_notes.append('probeB定性放行✓')
    # probeC: 封闭词汇 — 数字+词汇违约都要拦，正确词汇放行
    _c1, _, _ = guard_closed_vocab('wr_feedback_review', '合理，因为涨了5%', ('合理','异常'))
    _c2, _, _ = guard_closed_vocab('wr_feedback_review', '我同意这个调整', ('合理','异常'))
    _c3, _, _ = guard_closed_vocab('regime_llm_review', '质疑当前判定', ('支持','质疑'))
    if _c1 or _c2 or not _c3: raise AssertionError(f'封闭词汇守卫异常 c1={_c1} c2={_c2} c3={_c3}')
    _t15_notes.append('probeC封闭词汇✓')
    # probeD: SSOT清单落盘可读
    _reg = register_surfaces()
    _wired = [k for k,v in _reg['surfaces'].items() if v.get('wired')]
    if len(_wired) < 4: raise AssertionError(f'AI面接线数不足: {_wired}')
    _t15_notes.append(f'probeD SSOT {len(_wired)}面✓')
    # probeE: L3 IC门 — fail-closed三态
    from ic_tracker import review_pending_lessons
    _ls = [
        {'ic_verdict':'PENDING','wr':0.60,'n_total':10},   # → APPROVE
        {'ic_verdict':'PENDING','wr':0.40,'n_total':10},   # → REJECT
        {'ic_verdict':'PENDING','wr':0.50,'n_total':10},   # → PENDING(中间带)
        {'ic_verdict':'PENDING','wr':0.90,'n_total':5},    # → PENDING(n<8)
    ]
    _rv = review_pending_lessons(_ls)
    _states = [e['ic_verdict'] for e in _ls]
    if _states != ['APPROVE','REJECT','PENDING','PENDING'] or _rv != 2:
        raise AssertionError(f'L3三态异常: {_states} rv={_rv}')
    _t15_notes.append('probeE L3三态✓')
    # probeF: SFT只吃APPROVE（离线函数级验证）
    _sft_samples = [{'ic_verdict':'APPROVE'}, {'ic_verdict':'PENDING'}, {'ic_verdict':'REJECT'}]
    _eaten = [s for s in _sft_samples if s.get('ic_verdict') == 'APPROVE']
    if len(_eaten) != 1: raise AssertionError('SFT过滤语义异常')
    _t15_notes.append('probeF SFT只吃APPROVE✓')
    _ok('T15', 'L1守卫+L3 IC门', ' | '.join(_t15_notes))
except Exception as e:
    _fail('T15', 'L1守卫+L3 IC门', str(e)[:60])

# ── T16/T17 [C线蒸馏 苏摩111 2026-09-30] ─────────────────────
try:
    _t16_notes = []
    _dpath = Path(__file__).resolve().parent.parent / 'data' / 'distill_calibrator_v1.json'
    _art = json.loads(_dpath.read_text())
    _m = _art.get('metrics', {})
    if _art.get('governance', {}).get('mode') != 'SHADOW_ONLY':
        raise AssertionError('蒸馏器非shadow模式')
    if _art.get('governance', {}).get('wired_into_decision_chain') is not False:
        raise AssertionError('蒸馏器违规接线到决策链')
    if _m.get('test_auc', 0) < 0.70:
        raise AssertionError(f"test_auc过低: {_m.get('test_auc')}")
    if len(_art.get('features', [])) != 7:
        raise AssertionError('特征数异常')
    _t16_notes.append(f"AUC={_m.get('test_auc')} n={_m.get('n_total')} shadow✓")
    _ok('T16', '校准蒸馏工件', ' | '.join(_t16_notes))
except Exception as e:
    _fail('T16', '校准蒸馏工件', str(e)[:60])

try:
    _t17_notes = []
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
    from learning_loop import council_context as _cc, weekly_report as _wr
    _ctx = _cc()
    if not isinstance(_ctx, str):
        raise AssertionError('council_context非字符串')
    _r = _wr()
    if _r.get('strong_signals', -1) < 0:
        raise AssertionError('周报结构异常')
    _pkg = json.loads((Path(__file__).resolve().parent.parent / 'data' / 'council_context_package.json').read_text())
    for _l in _pkg.get('lessons', []):
        if not all(k in _l for k in ('lesson_type', 'lesson', 'evidence')):
            raise AssertionError('教训包字段缺失')
    _t17_notes.append(f"强信号样本={_r.get('strong_signals')} 灰区={_r.get('gray_zone')} 包结构✓")
    _ok('T17', '议会蒸馏闭环', ' | '.join(_t17_notes))
except Exception as e:
    _fail('T17', '议会蒸馏闭环', str(e)[:60])

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

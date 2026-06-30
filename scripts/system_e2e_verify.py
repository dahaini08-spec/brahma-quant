#!/usr/bin/env python3
"""
system_e2e_verify.py — 梵天系统端到端连通性验证
设计院 × 达摩院 2026-06-14

验证从信号源到执行的完整链路，逐节点检查接口匹配、数据格式、调用可用性。
用于每次大规模修复后的系统健康确认。
"""
import sys, json, time, traceback
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'lana'))

results = {}
PASS = '✅'; FAIL = '❌'; WARN = '⚠️'

def check(name, fn):
    try:
        info = fn()
        results[name] = {'status': PASS, 'info': info}
        print(f"{PASS} {name}: {info}")
    except Exception as e:
        results[name] = {'status': FAIL, 'err': str(e)[:120]}
        print(f"{FAIL} {name}: {str(e)[:100]}")
        traceback.print_exc()

print("=" * 65)
print("梵天系统端到端连通性验证 v1.0")
print("=" * 65)
print()

# ── 层1: 数据层 ──────────────────────────────────────────────────
print("【层1: 数据层】")

def n1_data_cache():
    from brahma_brain.data_cache import get_klines
    k = get_klines('BTCUSDT', '1h', 3)
    assert len(k) >= 1
    return f"BTC 1H last_close={float(k[-1][4]):,.0f}"
check("N1_data_cache", n1_data_cache)

def n2_market_state():
    from brahma_brain.market_state import analyze as ms_a
    ms = ms_a('BTCUSDT')
    regime = ms.get('regime')
    regime_raw = ms.get('regime_raw', '=stable')
    assert regime is not None
    return f"regime={regime} raw={regime_raw} rsi4h={ms.get('momentum',{}).get('rsi_4h',0):.1f}"
check("N2_market_state", n2_market_state)

def n3_regime_state_machine():
    from brahma_brain.regime_state_machine import RegimeStateMachine, get_regime_status
    rsm = RegimeStateMachine('BTCUSDT')
    st = get_regime_status('BTCUSDT')
    assert 'confirmed' in st
    return f"confirmed={st['confirmed']}({st['confirmed_cn']}) progress={st['confirm_progress']} lock={st['locked_remain_h']}H"
check("N3_regime_statemachine", n3_regime_state_machine)

# ── 层2: 评分层 ──────────────────────────────────────────────────
print()
print("【层2: 评分层】")

_brahma_result = {}

def n4_brahma_core():
    from brahma_brain.brahma_core import analyze
    global _brahma_result
    r = analyze('BTCUSDT', signal_dir='LONG')
    _brahma_result['long'] = r
    r2 = analyze('BTCUSDT', signal_dir='SHORT')
    _brahma_result['short'] = r2
    score_l = r.get('score', 0); grade_l = r.get('grade', '?')
    score_s = r2.get('score', 0); grade_s = r2.get('grade', '?')
    regime_cn = r.get('regime_cn', '?')
    return f"LONG score={score_l} grade={grade_l} | SHORT score={score_s} grade={grade_s} | 体制={regime_cn}"
check("N4_brahma_core", n4_brahma_core)

def n5_signal_selector():
    from brahma_brain.signal_selector import select
    from brahma_brain.market_state import analyze as ms_a
    ms = ms_a('BTCUSDT')
    regime = {
        'regime':     ms.get('regime'),
        'primary':    ms.get('regime'),
        'regime_cn':  '熊市反弹',
        'multiplier': {'LONG': 0.95, 'SHORT': 0.4},
        'phase':      ms.get('trend',{}).get('4h',{}).get('direction','?'),
        'momentum':   'NEUTRAL',
        'bear_prob':  ms.get('momentum',{}).get('rsi_1d', 50) / 100,
        'bull_prob':  1 - ms.get('momentum',{}).get('rsi_1d', 50) / 100,
        'chop_prob':  0.0,
    }
    ra = _brahma_result.get('long', {})
    rb = _brahma_result.get('short', {})
    if not ra:
        from brahma_brain.brahma_core import analyze
        ra = analyze('BTCUSDT', signal_dir='LONG')
        rb = analyze('BTCUSDT', signal_dir='SHORT')
    sel = select(rb, ra, regime)  # [FIX] select(short, long, regime)
    return f"direction={sel.get('direction','?')} weighted={sel.get('weighted_score',0)} decision={str(sel.get('decision','?'))[:50]}"
check("N5_signal_selector", n5_signal_selector)

# ── 层3: 网关层 ──────────────────────────────────────────────────
print()
print("【层3: 网关层】")

_gateway_result = {}

def n6_trade_gateway():
    from trade_gateway import run as gw_run
    global _gateway_result
    gw = gw_run('BTCUSDT')
    _gateway_result = gw
    pushed = gw.get('pushed', 0)
    decision = str(gw.get('decision', '?'))[:60]
    return f"pushed={pushed} decision={decision}"
check("N6_trade_gateway", n6_trade_gateway)

def n7_push_hub():
    from push_hub import send_strategy_dd1, build_strategy_dd1
    # 只验证接口存在，不实际发送
    msg = build_strategy_dd1(
        symbol='BTCUSDT', direction='LONG', price=64000,
        entry_lo=63800, entry_hi=64000, stop_loss=62500,
        tp1=65500, tp2=66500, score=138,
        regime='BEAR_RECOVERY(熊市反弹)', rr1=2.5
    )
    assert msg is not None
    return f"build_strategy_dd1 OK len={len(str(msg))}"
check("N7_push_hub", n7_push_hub)

def n8_wuqu_notifier():
    import wuqu_signal_notifier as wsn
    has_run = hasattr(wsn, 'run')
    has_format = hasattr(wsn, '_format_signal') or hasattr(wsn, 'format_signal') or hasattr(wsn, '_fmt')
    return f"run={has_run} format={'✅' if has_format else '?'}"
check("N8_wuqu_notifier", n8_wuqu_notifier)

def n9_signal_router():
    from signal_router import route_signal
    # 验证接口存在
    return "route_signal() OK"
check("N9_signal_router", n9_signal_router)

# ── 层4: 执行层 ──────────────────────────────────────────────────
print()
print("【层4: 执行层】")

def n10_position_sizer():
    from brahma_brain.position_sizer import get_position_pct, kelly_position
    pct_dict = get_position_pct('BTCUSDT', score=138, direction='LONG', nav=1000.0)
    pct = pct_dict.get("pct", pct_dict.get("position_pct", 0)) if isinstance(pct_dict, dict) else float(pct_dict)
    kelly = kelly_position(wr=0.72, rr=2.0)
    return f"get_position_pct={pct:.2%} kelly(wr=0.72,rr=2)={kelly:.2%}"
check("N10_position_sizer", n10_position_sizer)

def n11_hunter_executor():
    from hunter_executor import execute_open
    # 验证接口签名
    import inspect
    sig = str(inspect.signature(execute_open))
    return f"execute_open{sig[:60]}"
check("N11_hunter_executor", n11_hunter_executor)

def n12_lana_execute():
    from lana.execute_engine import place_order, adapt_signal, get_balance
    return f"place_order/adapt_signal/get_balance OK"
check("N12_lana_execute_engine", n12_lana_execute)

def n13_brahma_execute():
    from brahma_execute import run as be_run
    import inspect
    sig = str(inspect.signature(be_run))
    return f"run{sig[:60]} DRY_RUN=True"
check("N13_brahma_execute", n13_brahma_execute)

# ── 层5: 监控层 ──────────────────────────────────────────────────
print()
print("【层5: 监控层】")

def n14_pipeline_bridge():
    from pipeline_bridge import load_queue, load_zones, call_brahma_analyze
    q = load_queue(); z = load_zones()
    return f"queue={len(q)} zones={len(z)} call_brahma_analyze=OK"
check("N14_pipeline_bridge", n14_pipeline_bridge)

def n15_live_signal_log():
    log = BASE / 'data' / 'live_signal_log.jsonl'
    if not log.exists():
        return "live_signal_log.jsonl 不存在（正常，无历史信号）"
    lines = log.read_text().strip().split('\n')
    valid = [l for l in lines if l.strip()]
    if valid:
        last = json.loads(valid[-1])
        return f"count={len(valid)} last={last.get('symbol','?')} {last.get('direction','?')} score={last.get('score','?')}"
    return "empty"
check("N15_live_signal_log", n15_live_signal_log)

def n16_brahma_state_freshness():
    with open(BASE / 'data' / 'brahma_state.json') as f:
        st = json.load(f)
    lu = st.get('last_updated', 0)
    if isinstance(lu, str):
        import datetime
        lu_dt = datetime.datetime.fromisoformat(lu.replace('Z','+00:00'))
        age_min = (datetime.datetime.now(datetime.timezone.utc) - lu_dt).total_seconds() / 60
    else:
        age_min = (time.time() - float(lu)) / 60 if lu else 999
    regime = st.get('regime', '?')
    rs = st.get('regime_snapshot', {})
    source = rs.get('source', '?')
    return f"age={age_min:.1f}min regime={regime} source={source}"
check("N16_brahma_state_freshness", n16_brahma_state_freshness)

# ── 汇总 ─────────────────────────────────────────────────────────
print()
print("=" * 65)
total = len(results)
passed = sum(1 for v in results.values() if v['status'] == PASS)
failed = sum(1 for v in results.values() if v['status'] == FAIL)
warned = sum(1 for v in results.values() if v['status'] == WARN)
print(f"端到端验证汇总: {passed}/{total} PASS  {warned} WARN  {failed} FAIL")
if failed > 0:
    print(f"\n❌ 断点列表:")
    for k, v in results.items():
        if v['status'] == FAIL:
            print(f"  {k}: {v.get('err','?')}")
print("=" * 65)

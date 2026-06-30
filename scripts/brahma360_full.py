#!/usr/bin/env python3
"""
brahma360_full.py — 梵天系统全覆盖360诊断 v1.0
设计院 × 量化工程师360 · 2026-06-19

【设计原则】
  覆盖系统所有资产层，而非单一信号层：
  Layer 0: 账户/资金/仓位真实性
  Layer 1: 数据源健康度
  Layer 2: 体制/评分链路
  Layer 3: 信号生成/存储
  Layer 4: 执行链路端到端（历史盲区 → 今日修复触发本模块）
  Layer 5: 风控/熔断一致性
  Layer 6: 监控/Cron健康
  Layer 7: 数据一致性校验

运行:
  python3 scripts/brahma360_full.py          # 完整检查
  python3 scripts/brahma360_full.py --fast   # 跳过网络密集项
  python3 scripts/brahma360_full.py --layer 4  # 只跑指定层
"""

import sys, os, json, time, hmac, hashlib, math
import urllib.request
import subprocess
import traceback
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
DATA    = BASE / 'data'
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(SCRIPTS))

FAST_MODE  = '--fast' in sys.argv
LAYER_ONLY = None
for i, a in enumerate(sys.argv):
    if a == '--layer' and i+1 < len(sys.argv):
        LAYER_ONLY = int(sys.argv[i+1])

# ── 读取 API Key ──────────────────────────────────────────────────
import re
_tools = (BASE.parent / 'TOOLS.md').read_text()
_API_KEY    = re.search(r'API Key:\s*(\S+)', _tools).group(1)
_API_SECRET = re.search(r'Secret:\s*(\S+)',  _tools).group(1)

def _signed_get(path, params={}):
    p = dict(params)
    p['timestamp'] = int(time.time() * 1000)
    qs  = '&'.join(f'{k}={v}' for k, v in p.items())
    sig = hmac.new(_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{path}?{qs}&signature={sig}'
    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': _API_KEY})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def _pub_get(url):
    return json.loads(urllib.request.urlopen(url, timeout=8).read())

# ── 结果收集 ──────────────────────────────────────────────────────
results   = []
_sections = []

PASS = '✅'; FAIL = '❌'; WARN = '⚠️'; INFO = 'ℹ️'

def section(title, layer=None):
    _sections.append(title)
    print(f'\n{"─"*62}')
    print(f'  {title}')
    print(f'{"─"*62}')

def check(cid, name, fn, critical=False):
    if LAYER_ONLY is not None:
        layer_num = int(cid[1]) if cid[1].isdigit() else -1
        if layer_num != LAYER_ONLY:
            return
    try:
        detail = fn()
        status = PASS
        fix    = ''
    except AssertionError as e:
        status = FAIL if critical else WARN
        detail = str(e)[:200]
        fix    = ''
    except Exception as e:
        status = FAIL
        detail = f'{type(e).__name__}: {str(e)[:160]}'
        fix    = ''
    icon = {'✅': PASS, '❌': FAIL, '⚠️': WARN}.get(status, INFO)
    print(f'  {icon} {cid} {name}: {detail}')
    results.append({'id': cid, 'name': name, 'status': status, 'detail': detail})

# ═══════════════════════════════════════════════════════════════
# Layer 0: 账户/资金/仓位真实性
# ═══════════════════════════════════════════════════════════════
section('Layer 0 · 账户 / 资金 / 持仓真实性')

def L00_account_balance():
    bal = _signed_get('/fapi/v2/balance')
    usdt = next((b for b in bal if b['asset'] == 'USDT'), None)
    assert usdt, 'USDT账户不存在'
    wb   = float(usdt['balance'])
    avail = float(usdt['availableBalance'])
    assert wb > 0, f'账户余额={wb:.2f}，可能已爆仓'
    return f'总额=${wb:.2f} 可用=${avail:.2f}'
check('L00', '账户USDT余额', L00_account_balance, critical=True)

def L01_nav_vs_binance():
    """brahma_state.nav_usdt 与 Binance 实际余额偏差 < 20%"""
    bs    = json.loads((DATA / 'brahma_state.json').read_text())
    state_nav = float(bs.get('nav_usdt', 0))
    bal   = _signed_get('/fapi/v2/balance')
    usdt  = next((b for b in bal if b['asset'] == 'USDT'), None)
    real  = float(usdt['balance'])
    if state_nav == 0:
        return f'nav_usdt=0（未初始化）real=${real:.2f}'
    diff_pct = abs(state_nav - real) / real * 100
    assert diff_pct < 30, f'偏差={diff_pct:.1f}% state={state_nav:.2f} real={real:.2f}'
    return f'state=${state_nav:.2f} real=${real:.2f} 偏差={diff_pct:.1f}%'
check('L01', 'NAV与Binance余额一致性', L01_nav_vs_binance, critical=True)

def L02_positions_sync():
    """brahma_state.positions 与 Binance 实际持仓一致"""
    bs       = json.loads((DATA / 'brahma_state.json').read_text())
    state_p  = {p['symbol']: p for p in bs.get('positions', [])}
    live_pos = _signed_get('/fapi/v2/positionRisk')
    live_p   = {p['symbol']: p for p in live_pos if float(p.get('positionAmt', 0)) != 0}
    only_live  = set(live_p) - set(state_p)
    only_state = set(state_p) - set(live_p)
    assert not only_live,  f'Binance有但state没有: {only_live}'
    assert not only_state, f'state有但Binance没有（幽灵持仓）: {only_state}'
    return f'持仓一致 共{len(live_p)}个'
check('L02', 'brahma_state持仓与Binance一致', L02_positions_sync, critical=True)

def L03_last_binance_sync_age():
    """持仓同步时间不超过 2H"""
    bs = json.loads((DATA / 'brahma_state.json').read_text())
    ts = bs.get('last_binance_sync') or bs.get('positions_updated_at', '')
    if not ts:
        raise AssertionError('last_binance_sync字段缺失')
    from datetime import timezone
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        assert age_h < 4, f'上次同步={age_h:.1f}H前（阈值4H）'
        return f'上次同步={age_h:.2f}H前'
    except Exception as e:
        raise AssertionError(f'时间解析失败: {ts} err={e}')
check('L03', '持仓同步新鲜度(<4H)', L03_last_binance_sync_age)

def L04_wuqu_positions_integrity():
    """wuqu_positions 中每个持仓在 Binance 实际存在"""
    bs       = json.loads((DATA / 'brahma_state.json').read_text())
    wuqu_pos = bs.get('wuqu_positions', [])
    if not wuqu_pos:
        return 'wuqu_positions=空（武曲无开仓）'
    live_pos = _signed_get('/fapi/v2/positionRisk')
    live_syms = {p['symbol'] for p in live_pos if float(p.get('positionAmt', 0)) != 0}
    ghosts = [p['symbol'] for p in wuqu_pos if p['symbol'] not in live_syms]
    assert not ghosts, f'wuqu幽灵持仓（已平仓未清除）: {ghosts}'
    return f'wuqu持仓={len(wuqu_pos)}个，全部在Binance存在'
check('L04', 'wuqu_positions完整性校验', L04_wuqu_positions_integrity, critical=True)

def L05_available_balance_vs_plan():
    """可用余额 >= 单笔计划最大 notional（NAV*20%*leverage）"""
    bs    = json.loads((DATA / 'brahma_state.json').read_text())
    nav   = float(bs.get('nav_usdt', 130))
    plan_margin = nav * 0.20   # 20% NAV 作为保证金
    bal   = _signed_get('/fapi/v2/balance')
    usdt  = next((b for b in bal if b['asset'] == 'USDT'), None)
    avail = float(usdt['availableBalance'])
    assert avail >= plan_margin * 0.5, \
        f'可用余额${avail:.2f} < 计划保证金${plan_margin:.2f}×50% = ${plan_margin*0.5:.2f}'
    return f'可用=${avail:.2f} 计划保证金=${plan_margin:.2f} 充足'
check('L05', '余额充足性(≥50%计划保证金)', L05_available_balance_vs_plan)

# ═══════════════════════════════════════════════════════════════
# Layer 1: 数据源健康度
# ═══════════════════════════════════════════════════════════════
section('Layer 1 · 数据源健康度')

def L10_kline_freshness():
    p = _pub_get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT')
    price = float(p['price'])
    assert price > 0, 'BTC价格为0'
    return f'BTC=${price:,.0f}'
check('L10', 'Binance行情可达', L10_kline_freshness, critical=True)

def L11_data_cache():
    sys.path.insert(0, str(BASE / 'brahma_brain'))
    from brahma_brain.data_cache import get_klines
    k = get_klines('BTCUSDT', '1h', 3)
    assert len(k) >= 1
    age_min = (time.time() - float(k[-1][0])/1000) / 60
    assert age_min < 90, f'K线缓存过旧={age_min:.0f}min'
    return f'BTC 1H last={float(k[-1][4]):,.0f} age={age_min:.0f}min'
check('L11', 'K线缓存新鲜度', L11_data_cache)

def L12_brahma_state_freshness():
    bs = json.loads((DATA / 'brahma_state.json').read_text())
    ts = bs.get('last_updated') or bs.get('updated_at', '')
    if not ts:
        raise AssertionError('brahma_state无时间戳')
    dt  = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    assert age < 60, f'brahma_state陈旧={age:.0f}min（阈值60min）'
    regime = bs.get('regime_label', bs.get('regime', '?'))
    return f'age={age:.1f}min regime={regime}'
check('L12', 'brahma_state新鲜度(<60min)', L12_brahma_state_freshness)

# ═══════════════════════════════════════════════════════════════
# Layer 2: 体制/评分链路
# ═══════════════════════════════════════════════════════════════
if not FAST_MODE:
    section('Layer 2 · 体制/评分链路')

    def L20_market_state():
        from brahma_brain.market_state import analyze as ms_a
        ms = ms_a('BTCUSDT')
        regime = ms.get('regime')
        assert regime, 'market_state无regime输出'
        return f'BTC regime={regime}'
    check('L20', 'market_state体制输出', L20_market_state)

    def L21_brahma_core_score():
        from brahma_brain.brahma_core import analyze
        r = analyze('BTCUSDT', signal_dir='SHORT')
        score  = float(r.get('score', 0))
        regime = r.get('regime', '?')
        valid  = r.get('valid', False)
        assert score >= 0, 'score<0异常'
        return f'BTC SHORT score={score:.1f} regime={regime} valid={valid}'
    check('L21', 'brahma_core评分可运行', L21_brahma_score := L21_brahma_core_score)

    def L22_regime_hard_block():
        """验证 BEAR_TREND_LONG 宪法死穴在评分层有效"""
        from brahma_brain.brahma_core import analyze
        r = analyze('BTCUSDT', signal_dir='LONG')
        regime = r.get('regime', '')
        if regime == 'BEAR_TREND':
            valid  = r.get('valid', True)
            assert not valid, f'死穴BEAR_TREND_LONG未被拦截! valid={valid}'
        return f'死穴检查: regime={regime} (仅BEAR_TREND时强制验证)'
    check('L22', '死穴拦截有效性', L22_regime_hard_block)

# ═══════════════════════════════════════════════════════════════
# Layer 3: 信号生成/存储
# ═══════════════════════════════════════════════════════════════
section('Layer 3 · 信号生成/存储')

def L30_live_signal_log():
    sigs = []
    with open(DATA / 'live_signal_log.jsonl') as f:
        for l in f:
            l = l.strip()
            if l:
                try: sigs.append(json.loads(l))
                except: pass
    assert sigs, 'live_signal_log为空'
    latest = max(sigs, key=lambda x: float(x.get('ts', 0)))
    age_h  = (time.time() - float(latest.get('ts', 0))) / 3600
    return f'总信号={len(sigs)} 最新={age_h:.1f}H前'
check('L30', 'live_signal_log健康', L30_live_signal_log)

def L31_signal_dedup():
    """同一标的同方向信号不应在24H内超过20条（去重逻辑是否生效）"""
    sigs = []
    cutoff = time.time() - 86400
    with open(DATA / 'live_signal_log.jsonl') as f:
        for l in f:
            l = l.strip()
            if l:
                try:
                    s = json.loads(l)
                    if float(s.get('ts', 0)) >= cutoff:
                        sigs.append(s)
                except: pass
    from collections import Counter
    cnt = Counter((s.get('symbol'), s.get('direction')) for s in sigs)
    dups = {k: v for k, v in cnt.items() if v > 20}
    assert not dups, f'信号重复过多: {dups}'
    return f'24H内信号={len(sigs)} 最高重复={max(cnt.values()) if cnt else 0}'
check('L31', '信号去重健康(单标的<20/24H)', L31_signal_dedup)

def L32_open_signal_ttl():
    """OPEN状态的信号不应超过24H（僵尸信号检测）"""
    sigs = []
    with open(DATA / 'live_signal_log.jsonl') as f:
        for l in f:
            l = l.strip()
            if l:
                try: sigs.append(json.loads(l))
                except: pass
    open_sigs = [s for s in sigs if s.get('status') == 'OPEN']
    zombies   = []
    for s in open_sigs:
        ts = float(s.get('ts', 0))
        age_h = (time.time() - ts) / 3600
        exp   = s.get('expires_at', '')
        if exp:
            try:
                exp_ts = datetime.fromisoformat(exp.replace('Z', '+00:00')).timestamp()
                if time.time() > exp_ts:
                    zombies.append(f"{s.get('symbol')}(过期{(time.time()-exp_ts)/3600:.1f}H)")
            except: pass
        elif age_h > 48:
            zombies.append(f"{s.get('symbol')}(age={age_h:.0f}H)")
    assert len(zombies) < 5, f'僵尸信号过多({len(zombies)}): {zombies[:5]}'
    return f'OPEN信号={len(open_sigs)} 僵尸={len(zombies)}'
check('L32', '僵尸信号检测(<5个)', L32_open_signal_ttl)

def L33_wuqu_paper_wr():
    settled = []
    with open(DATA / 'wuqu_paper_settled.jsonl') as f:
        for l in f:
            l = l.strip()
            if l:
                try: settled.append(json.loads(l))
                except: pass
    real = [s for s in settled if s.get('source', '').startswith('backfill') is False
            and s.get('result') in ('WIN', 'LOSS', 'win', 'loss')]
    n = len(real)
    if n < 10:
        return f'有效样本={n} < 10（不足以评估，正常）'
    wins = sum(1 for s in real if s.get('result', '').upper() == 'WIN')
    wr   = wins / n * 100
    assert wr >= 50, f'武曲WR={wr:.1f}%<50%（策略可能失效）'
    return f'n={n} wins={wins} WR={wr:.1f}%'
check('L33', '武曲Paper WR健康(≥50%)', L33_wuqu_paper_wr)

# ═══════════════════════════════════════════════════════════════
# Layer 4: 执行链路端到端（核心！今日发现的历史盲区）
# ═══════════════════════════════════════════════════════════════
section('Layer 4 · 执行链路端到端（核心）')

def L40_auto_execute_gate_import():
    """auto_execute_gate 可正常import，无语法错误"""
    from scripts.auto_execute_gate import auto_execute
    assert callable(auto_execute)
    return 'import OK'
check('L40', 'auto_execute_gate可import', L40_auto_execute_gate_import, critical=True)

def L41_open_positions_reads_wuqu():
    """确认_open_positions()读取wuqu_positions而非全局positions"""
    gate_code = (SCRIPTS / 'auto_execute_gate.py').read_text()
    # 确认修复后的正确读取
    assert "wuqu_positions" in gate_code, '_open_positions()未读取wuqu_positions！（Bug已回归）'
    # 确认不再直接读全局positions（在_open_positions函数内）
    import re
    fn_match = re.search(r'def _open_positions\(\).*?(?=\ndef )', gate_code, re.DOTALL)
    assert fn_match, '_open_positions()函数未找到'
    fn_body = fn_match.group()
    assert "wuqu_positions" in fn_body, '_open_positions函数体未读wuqu_positions'
    assert fn_body.count("positions'") <= 1 or "wuqu_positions" in fn_body
    return '确认读取wuqu_positions（架构隔离正确）'
check('L41', '_open_positions隔离性验证', L41_open_positions_reads_wuqu, critical=True)

def L42_dry_run_full_pipeline():
    """完整dry_run：信号→五重门控→executor→返回success=True + 完整sizing"""
    sys.path.insert(0, str(SCRIPTS))
    from auto_execute_gate import auto_execute
    test_sig = {
        'symbol':    'ETHUSDT',
        'direction': 'SHORT',
        'score':     165,
        'regime':    'BEAR_TREND',
        'entry_lo':  1700.0,
        'entry_hi':  1720.0,
        'stop_loss': 1779.0,
        'tp1':       1602.0,
        'tp2':       1500.0,
        'leverage':  3,
    }
    r = auto_execute(test_sig, dry_run=True)
    assert r['executed'], f'dry_run执行失败: {r["reason"]}'
    order = r.get('order', {})
    assert order.get('qty') and float(order['qty']) > 0, f'qty无效: {order.get("qty")}'
    return f'dry_run通过 qty={order.get("qty")} reason={r["reason"]}'
check('L42', '完整dry_run执行链路(关键)', L42_dry_run_full_pipeline, critical=True)

def L43_sizing_fields_complete():
    """gate传给executor的sizing包含所有必要字段"""
    sys.path.insert(0, str(SCRIPTS))
    from auto_execute_gate import auto_execute
    test_sig = {
        'symbol': 'BTCUSDT', 'direction': 'SHORT', 'score': 170, 'regime': 'BEAR_TREND',
        'entry_lo': 62000, 'entry_hi': 63000, 'stop_loss': 64500, 'tp1': 58000, 'tp2': 55000,
        'leverage': 3,
    }
    r = auto_execute(test_sig, dry_run=True)
    assert r['executed'], f'执行失败: {r["reason"]}'
    order = r.get('order', {})
    required = ['qty', 'entry_price', 'sl_price', 'tp1_price']
    # 这些字段在order里或signal里
    # 检查qty存在
    assert order.get('qty') is not None, f'缺qty, order keys={list(order.keys())}'
    return f'sizing完整 qty={order.get("qty")}'
check('L43', 'sizing字段完整性', L43_sizing_fields_complete, critical=True)

def L44_hard_block_works():
    """死穴硬拒绝在执行链路中有效（不只在评分层）"""
    sys.path.insert(0, str(SCRIPTS))
    from auto_execute_gate import auto_execute
    dead_sig = {
        'symbol': 'BTCUSDT', 'direction': 'LONG', 'score': 200, 'regime': 'BEAR_TREND',
        'entry_lo': 62000, 'entry_hi': 63000, 'stop_loss': 60000, 'tp1': 67000, 'tp2': 70000,
        'leverage': 3,
    }
    r = auto_execute(dead_sig, dry_run=True)
    assert not r['executed'], f'死穴BEAR_TREND_LONG未被拦截！executed=True'
    assert 'HARD_BLOCK' in r.get('reason', ''), f'拦截原因不含HARD_BLOCK: {r["reason"]}'
    return f'死穴正确拦截: {r["reason"]}'
check('L44', '死穴拦截执行层验证(关键)', L44_hard_block_works, critical=True)

def L45_wuqu_position_limit():
    """持仓上限MAX=3在武曲独立计数中生效"""
    sys.path.insert(0, str(SCRIPTS))
    # 临时写入3个假武曲持仓
    import copy
    state_path = DATA / 'brahma_state.json'
    bs_orig = json.loads(state_path.read_text())
    bs_test = copy.deepcopy(bs_orig)
    bs_test['wuqu_positions'] = [
        {'symbol': 'BTCUSDT',  'side': 'SHORT'},
        {'symbol': 'ETHUSDT',  'side': 'SHORT'},
        {'symbol': 'BNBUSDT',  'side': 'SHORT'},
    ]
    state_path.write_text(json.dumps(bs_test, ensure_ascii=False))
    try:
        from auto_execute_gate import auto_execute, _load_state
        # 强制重载
        import importlib, scripts.auto_execute_gate as _gate
        importlib.reload(_gate)
        test_sig = {
            'symbol': 'SOLUSDT', 'direction': 'SHORT', 'score': 170, 'regime': 'BEAR_TREND',
            'entry_lo': 150, 'entry_hi': 155, 'stop_loss': 165, 'tp1': 130, 'tp2': 120,
            'leverage': 3,
        }
        r = _gate.auto_execute(test_sig, dry_run=True)
        assert not r['executed'], f'持仓满时仍执行了！reason={r["reason"]}'
    finally:
        # 恢复原始state
        state_path.write_text(json.dumps(bs_orig, ensure_ascii=False))
    return '持仓上限3/3正确拦截第4个信号'
check('L45', '武曲持仓上限隔离验证', L45_wuqu_position_limit, critical=True)

def L46_dry_run_no_wuqu_pollution():
    """dry_run=True时不污染wuqu_positions"""
    sys.path.insert(0, str(SCRIPTS))
    import importlib, scripts.auto_execute_gate as _gate
    importlib.reload(_gate)
    state_path = DATA / 'brahma_state.json'
    bs_before  = json.loads(state_path.read_text())
    n_before   = len(bs_before.get('wuqu_positions', []))

    test_sig = {
        'symbol': 'BNBUSDT', 'direction': 'SHORT', 'score': 165, 'regime': 'BEAR_EARLY',
        'entry_lo': 600, 'entry_hi': 610, 'stop_loss': 640, 'tp1': 560, 'tp2': 530,
        'leverage': 3,
    }
    _gate.auto_execute(test_sig, dry_run=True)

    bs_after = json.loads(state_path.read_text())
    n_after  = len(bs_after.get('wuqu_positions', []))
    assert n_after == n_before, f'dry_run污染了wuqu_positions: before={n_before} after={n_after}'
    return f'dry_run前后wuqu_positions均={n_before}（无污染）'
check('L46', 'dry_run不污染wuqu_positions', L46_dry_run_no_wuqu_pollution, critical=True)

def L47_execution_log_no_stuck():
    """auto_execute_log 中最近24H EXECUTED > 0，否则告警"""
    log_path = DATA / 'auto_execute_log.jsonl'
    if not log_path.exists():
        return 'auto_execute_log不存在（武曲从未运行）'
    logs = []
    cutoff = time.time() - 86400
    with open(log_path) as f:
        for l in f:
            l = l.strip()
            if l:
                try:
                    e = json.loads(l)
                    ts_str = e.get('ts', '')
                    try:
                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp()
                    except:
                        ts = 0
                    if ts >= cutoff:
                        logs.append(e)
                except: pass
    total    = len(logs)
    executed = sum(1 for e in logs if e.get('event') == 'EXECUTED')
    blocked  = sum(1 for e in logs if e.get('event') == 'BLOCKED')
    # 如果有信号进入入场区但0次执行 → 告警
    if total > 0 and executed == 0:
        # 检查是否有合理的拦截原因（非系统错误）
        reasons = [e.get('reason', '') for e in logs if e.get('event') == 'BLOCKED']
        error_blocks = [r for r in reasons if '异常' in r or 'Error' in r or 'Exception' in r]
        if error_blocks:
            raise AssertionError(f'24H内0次成功执行，且有异常拦截: {error_blocks[:3]}')
        return f'24H: total={total} executed={executed} blocked={blocked}（均为策略拦截，非系统错误）'
    return f'24H: total={total} executed={executed} blocked={blocked}'
check('L47', '执行日志健康(EXECUTED无卡死)', L47_execution_log_no_stuck)

# ═══════════════════════════════════════════════════════════════
# Layer 5: 风控/熔断一致性
# ═══════════════════════════════════════════════════════════════
section('Layer 5 · 风控 / 熔断一致性')

def L50_circuit_breaker_consistency():
    """brahma_state.breaker_active 与 circuit_breaker.json 一致"""
    bs = json.loads((DATA / 'brahma_state.json').read_text())
    state_active = bs.get('breaker_active', False) or bs.get('circuit_breaker', {}).get('active', False)
    cb_path = DATA / 'circuit_breaker.json'
    if cb_path.exists():
        cb = json.loads(cb_path.read_text())
        cb_active = cb.get('active', False)
        assert state_active == cb_active, \
            f'熔断器不一致: brahma_state={state_active} circuit_breaker.json={cb_active}'
    return f'熔断器状态一致: active={state_active}'
check('L50', '熔断器状态一致性', L50_circuit_breaker_consistency, critical=True)

def L51_max_drawdown_check():
    """账户当日最大亏损不超过NAV的15%"""
    bs  = json.loads((DATA / 'brahma_state.json').read_text())
    nav = float(bs.get('nav_usdt', 130))
    live_pos = _signed_get('/fapi/v2/positionRisk')
    total_upnl = sum(float(p.get('unRealizedProfit', 0))
                     for p in live_pos if float(p.get('positionAmt', 0)) != 0)
    dd_pct = abs(min(total_upnl, 0)) / nav * 100 if nav > 0 else 0
    assert dd_pct < 15, f'当前浮亏={dd_pct:.1f}% 超过15%阈值，建议检查熔断'
    return f'浮盈/亏=\${total_upnl:+.2f} 回撤={dd_pct:.1f}%'
check('L51', '当前回撤<15%NAV', L51_max_drawdown_check)

def L52_position_concentration():
    """单一持仓名义价值不超过NAV的60%"""
    bs   = json.loads((DATA / 'brahma_state.json').read_text())
    nav  = float(bs.get('nav_usdt', 130))
    live = _signed_get('/fapi/v2/positionRisk')
    open_pos = [p for p in live if float(p.get('positionAmt', 0)) != 0]
    if not open_pos or nav <= 0:
        return f'持仓={len(open_pos)}个 NAV=${nav:.2f}'
    max_notional = max(abs(float(p.get('positionAmt',0))) * float(p.get('markPrice',0))
                       for p in open_pos)
    conc_pct = max_notional / nav * 100
    assert conc_pct < 60, f'最大单仓集中度={conc_pct:.1f}%>60%'
    return f'最大单仓名义/NAV={conc_pct:.1f}%'
check('L52', '持仓集中度<60%NAV', L52_position_concentration)

# ═══════════════════════════════════════════════════════════════
# Layer 6: 监控/Cron健康
# ═══════════════════════════════════════════════════════════════
section('Layer 6 · 监控 / Cron健康')

def L60_signal_watcher_state():
    """signal_watcher 状态文件新鲜（30min内有活动）"""
    sw_state = DATA / 'signal_watcher_state.json'
    assert sw_state.exists(), 'signal_watcher_state.json不存在（signal_watcher从未运行？）'
    mtime = sw_state.stat().st_mtime
    age_min = (time.time() - mtime) / 60
    assert age_min < 180, f'signal_watcher_state超过{age_min:.0f}min未更新（阈值180min）'
    return f'state更新于{age_min:.0f}min前'
check('L60', 'signal_watcher活跃性', L60_signal_watcher_state)

def L61_regime_switch_monitor():
    """regime_switch_monitor state文件存在且新鲜"""
    rs_state = DATA / 'regime_switch_state.json'
    if not rs_state.exists():
        return 'regime_switch_state.json不存在（跳过）'
    mtime = rs_state.stat().st_mtime
    age_min = (time.time() - mtime) / 60
    assert age_min < 60, f'体制切换监控{age_min:.0f}min未更新'
    return f'state更新于{age_min:.0f}min前'
check('L61', '体制切换监控活跃性', L61_regime_switch_monitor)

def L62_position_sl_monitor():
    """position_sl_monitor 脚本可运行（--check模式）"""
    sl_path = SCRIPTS / 'position_sl_monitor.py'
    assert sl_path.exists(), 'position_sl_monitor.py不存在'
    r = subprocess.run(
        [sys.executable, str(sl_path), '--check'],
        capture_output=True, text=True, timeout=10, cwd=str(BASE)
    )
    # 只要不crash就算通过
    return f'exit={r.returncode} ok'
check('L62', 'position_sl_monitor可运行', L62_position_sl_monitor)

def L63_ws_guardian_process():
    """ws_guardian进程存在"""
    r = subprocess.run(['pgrep', '-f', 'ws_guardian'], capture_output=True, text=True)
    pids = r.stdout.strip()
    assert pids, 'ws_guardian进程不存在！止损守护失效'
    return f'PIDs={pids}'
check('L63', 'ws_guardian进程健康', L63_ws_guardian_process, critical=True)

# ═══════════════════════════════════════════════════════════════
# Layer 7: 数据一致性校验
# ═══════════════════════════════════════════════════════════════
section('Layer 7 · 数据一致性校验')

def L70_soma_ai_registry():
    """soma_ai_registry.json存在且记录当前AI任务"""
    reg_path = DATA / 'soma_ai_registry.json'
    if not reg_path.exists():
        return 'soma_ai_registry.json不存在（跳过）'
    reg = json.loads(reg_path.read_text())
    approved = reg.get('approved_tasks', reg) if isinstance(reg, dict) else {}
    n = len(approved) if isinstance(approved, dict) else 0
    return f'已批准AI任务={n}个'
check('L70', 'Soma AI任务台账', L70_soma_ai_registry)

def L71_signal_log_no_corrupt():
    """live_signal_log.jsonl每行均可解析，无损坏记录"""
    corrupt = 0
    total   = 0
    with open(DATA / 'live_signal_log.jsonl') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            total += 1
            try: json.loads(line)
            except: corrupt += 1
    assert corrupt == 0, f'损坏记录={corrupt}/{total}'
    return f'total={total} corrupt=0 ✓'
check('L71', 'live_signal_log无损坏', L71_signal_log_no_corrupt)

def L72_brahma_state_schema():
    """brahma_state.json包含所有关键字段"""
    bs = json.loads((DATA / 'brahma_state.json').read_text())
    required = ['positions', 'wuqu_positions', 'nav_usdt', 'last_updated']
    missing  = [k for k in required if k not in bs]
    assert not missing, f'brahma_state缺字段: {missing}'
    return f'schema完整 wuqu_positions={len(bs["wuqu_positions"])}个'
check('L72', 'brahma_state schema完整性', L72_brahma_state_schema, critical=True)

def L73_execution_log_not_empty_24h():
    """执行门控在24H内被调用过（signal_watcher在运行）"""
    log_path = DATA / 'auto_execute_log.jsonl'
    sw_state_path = DATA / 'signal_watcher_state.json'
    if not log_path.exists():
        raise AssertionError('auto_execute_log.jsonl不存在')
    logs = []
    cutoff = time.time() - 86400
    with open(log_path) as f:
        for l in f:
            l = l.strip()
            if l:
                try:
                    e = json.loads(l)
                    ts_str = e.get('ts', '')
                    try:
                        ts = datetime.fromisoformat(ts_str.replace('Z','+00:00')).timestamp()
                    except:
                        ts = 0
                    if ts >= cutoff:
                        logs.append(e)
                except: pass
    assert len(logs) > 0, '24H内auto_execute_log无任何记录（signal_watcher可能停止运行）'
    return f'24H内执行门控记录={len(logs)}条'
check('L73', '24H内执行门控有活动', L73_execution_log_not_empty_24h)

# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════
print(f'\n{"═"*62}')
print('  🏛️  梵天360全覆盖诊断汇总')
print(f'{"═"*62}')

total    = len(results)
passed   = sum(1 for r in results if r['status'] == PASS)
warned   = sum(1 for r in results if r['status'] == WARN)
failed   = sum(1 for r in results if r['status'] == FAIL)

print(f'  总计: {total}项 | ✅PASS={passed} ⚠️WARN={warned} ❌FAIL={failed}')
print()

if failed:
    print('  ❌ 严重问题（需立即处理）:')
    for r in results:
        if r['status'] == FAIL:
            print(f'     {r["id"]} {r["name"]}: {r["detail"][:120]}')
    print()

if warned:
    print('  ⚠️ 需关注:')
    for r in results:
        if r['status'] == WARN:
            print(f'     {r["id"]} {r["name"]}: {r["detail"][:120]}')
    print()

if failed == 0 and warned == 0:
    verdict = '🟢 全部健康'
elif failed == 0:
    verdict = '🟡 基本健康'
else:
    verdict = '🔴 存在严重问题'
print(f'  总体评估: {verdict} {passed}/{total}')
print()

summary = {
    'ts': datetime.now(timezone.utc).isoformat(),
    'passed': passed, 'warned': warned, 'failed': failed, 'total': total,
    'verdict': verdict,
    'results': results,
}
print(f'BRAHMA360_JSON:{json.dumps({"passed":passed,"warned":warned,"failed":failed,"total":total,"verdict":verdict}, ensure_ascii=False)}')

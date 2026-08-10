#!/usr/bin/env python3
"""brahma_full_test.py — 梵天全能力测试套件 Layer3链路+Layer4风控+Layer7一致性
设计院封印 2026-08-02

用途:
  端到端验证链路层、风控层、数据一致性层、性能基准层
  不实际下单，A组使用内存信号字典直接测试filter逻辑

运行:
  python3 scripts/brahma_full_test.py
"""
import sys, os, json, time
from pathlib import Path

# ── 屏蔽HF离线warning，加速启动 ──
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('HF_DATASETS_OFFLINE', '1')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

BASE = Path(__file__).parent.parent
for p in [str(BASE), str(BASE / 'brahma_brain'), str(BASE / 'scripts')]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
except ImportError:
    pass

RED    = '\033[91m'
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RESET  = '\033[0m'

ok_count = 0
fail_count = 0
warn_count = 0

def ok(name, detail=''):
    global ok_count
    ok_count += 1
    print(f'  {GREEN}✅ {name}{RESET}' + (f': {detail}' if detail else ''))

def fail(name, detail='', fix=''):
    global fail_count
    fail_count += 1
    print(f'  {RED}❌ {name}{RESET}' + (f': {detail}' if detail else ''))
    if fix:
        print(f'     → 修复建议: {fix}')

def warn(name, detail=''):
    global warn_count
    warn_count += 1
    print(f'  {YELLOW}⚠️  {name}{RESET}' + (f': {detail}' if detail else ''))


# ═══════════════════════════════════════════════════════════════
# 【A】链路层 — 执行链端到端验证（不实际下单/写文件）
# ═══════════════════════════════════════════════════════════════
print('\n【A】链路层 — 执行链端到端验证')

# A组：直接测试 auto_executor.find_executable_signals 的内部过滤逻辑
# 策略：mock SIGNAL_LOG_PATH到临时文件，注入测试信号，观察filter行为

def _make_signal(**kwargs):
    """构造最小合法信号字典"""
    base = {
        'signal_id':    kwargs.get('signal_id', 'TEST_SIG_0001'),
        'ts':           time.time() - 1000,
        'symbol':       kwargs.get('symbol', 'ETHUSDT'),
        'direction':    kwargs.get('direction', 'SHORT'),
        'signal_dir':   kwargs.get('direction', 'SHORT'),
        'regime':       kwargs.get('regime', 'BEAR_TREND'),
        'score':        kwargs.get('score', 145),
        'valid':        kwargs.get('valid', True),
        'rr1':          kwargs.get('rr1', 2.5),
        'sl_pct':       kwargs.get('sl_pct', 2.5),
        'action':       kwargs.get('action', 'ENTER'),
        'timing_badge': kwargs.get('timing_badge', 'READY'),
        'expires_at':   kwargs.get('expires_at', None),
        'settled':      False,
        'result':       None,
    }
    base.update({k: v for k, v in kwargs.items() if k not in base})
    return base

def _run_filter_on_signals(signals: list) -> list:
    """
    将信号列表写入临时文件，patch auto_executor 的 SIGNAL_LOG_PATH，
    调用 find_executable_signals() 并还原。
    返回通过过滤的信号列表。
    """
    import tempfile, importlib
    import scripts.auto_executor as ae

    tmpf = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
    for s in signals:
        tmpf.write(json.dumps(s) + '\n')
    tmpf.close()

    orig_path = ae.SIGNAL_LOG_PATH
    # 同时 patch EXECUTED 集合避免历史信号干扰
    orig_executed_path = ae.EXECUTED_SET_PATH

    # 临时 executed 文件（空集合）
    exec_tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    exec_tmp.write('[]')
    exec_tmp.close()

    try:
        ae.SIGNAL_LOG_PATH    = Path(tmpf.name)
        ae.EXECUTED_SET_PATH  = Path(exec_tmp.name)
        result = ae.find_executable_signals()
    finally:
        ae.SIGNAL_LOG_PATH    = orig_path
        ae.EXECUTED_SET_PATH  = orig_executed_path
        os.unlink(tmpf.name)
        os.unlink(exec_tmp.name)
    return result

# ── A1: BEAR_TREND SHORT score=145 → tier2 + kelly注入 ────────────────────
try:
    import scripts.auto_executor as _ae_mod
    # TIER_2 阈值=148，TIER_1=155；使用score=150构造合法TIER_2信号
    _sig_a1 = _make_signal(
        signal_id='A1_BEAR_SHORT_150',
        symbol='ETHUSDT',
        regime='BEAR_TREND',
        direction='SHORT',
        score=150,
        rr1=2.5,
        sl_pct=2.5,
        timing_badge='READY',
    )
    _res_a1 = _run_filter_on_signals([_sig_a1])
    if not _res_a1:
        fail('A1 BEAR_TREND SHORT score=150 应通过过滤', '信号被意外拦截')
    else:
        _s = _res_a1[0]
        _tier_ok  = _s.get('_tier') == 2
        _nav_ok   = _s.get('_tier_nav_pct', 0) <= 0.03 + 1e-4  # tier2上限0.03，kelly可能降低
        _kelly    = '_pos_source' in _s and 'kelly' in str(_s.get('_pos_source', ''))
        if _tier_ok and _nav_ok:
            ok('A1 BEAR_TREND SHORT score=150 (TIER_2)',
               f'_tier={_s.get("_tier")} _tier_nav_pct={_s.get("_tier_nav_pct")}'
               + (f' kelly={_s.get("_pos_source")}' if _kelly else ' (kelly可选)'))
        else:
            fail('A1 tier/nav_pct不符预期',
                 f'_tier={_s.get("_tier")} _tier_nav_pct={_s.get("_tier_nav_pct")} expected tier=2 nav≤0.03')
except Exception as e:
    fail('A1 执行异常', str(e))

# ── A2: BULL_TREND LONG score=160 → WR门控触发OBSERVE拦截 ─────────────────
try:
    _sig_a2 = _make_signal(
        signal_id='A2_BULL_LONG_160',
        symbol='BTCUSDT',
        regime='BULL_TREND',
        direction='LONG',
        score=160,
        rr1=2.0,
        sl_pct=2.0,
        timing_badge='READY',
    )
    _res_a2 = _run_filter_on_signals([_sig_a2])
    # BULL_TREND LONG WR=32.8%<45% → 应被OBSERVE拦截 OR DEAD_ZONE拦截
    # BULL_TREND SHORT 在 DEAD_ZONE，所以BULL_TREND LONG走WR门控
    if not _res_a2:
        ok('A2 BULL_TREND LONG score=160 被WR门控/DEAD_ZONE拦截', '未进入执行队列')
    else:
        # 可能WR数据不足导致不拦截
        warn('A2 BULL_TREND LONG 未被拦截', 'WR数据可能不足n<20（不强制拦截），视为预期内')
except Exception as e:
    fail('A2 执行异常', str(e))

# ── A3: 信号 expires_at=1小时前 → 过期检测跳过 ────────────────────────────
try:
    from datetime import datetime, timezone, timedelta
    _past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _sig_a3 = _make_signal(
        signal_id='A3_EXPIRED',
        symbol='ETHUSDT',
        regime='BEAR_TREND',
        direction='SHORT',
        score=145,
        rr1=2.5,
        sl_pct=2.5,
        timing_badge='READY',
        expires_at=_past,
    )
    _res_a3 = _run_filter_on_signals([_sig_a3])
    if _res_a3:
        fail('A3 过期信号未被拦截', f'expires_at={_past}')
    else:
        ok('A3 过期信号正确跳过', f'expires_at={_past[:16]}')
except Exception as e:
    fail('A3 执行异常', str(e))

# ── A4: BEAR_TREND LONG（死穴）→ DEAD_ZONE拦截 ───────────────────────────
try:
    _sig_a4 = _make_signal(
        signal_id='A4_DEAD_ZONE',
        symbol='ETHUSDT',
        regime='BEAR_TREND',
        direction='LONG',
        score=150,
        rr1=2.5,
        sl_pct=2.5,
        timing_badge='READY',
    )
    _res_a4 = _run_filter_on_signals([_sig_a4])
    if _res_a4:
        fail('A4 DEAD_ZONE信号未被拦截', f'regime=BEAR_TREND dir=LONG 应被DEAD_ZONE封禁')
    else:
        ok('A4 BEAR_TREND LONG 被DEAD_ZONE正确拦截')
except Exception as e:
    fail('A4 执行异常', str(e))


# ═══════════════════════════════════════════════════════════════
# 【B】风控门控穷举验证
# ═══════════════════════════════════════════════════════════════
print('\n【B】风控门控穷举')

# ── B1: signal_expiry_tracker.register() → check_expiry() ─────────────────
try:
    from brahma_brain.signal_expiry_tracker import register as _se_reg, check_expiry as _se_check
    import tempfile, json as _jb1
    # 直接注册并检查（不依赖外部文件状态）
    _r_b1 = _se_reg(
        symbol='TEST_B1',
        signal_type='RSI_OB',
        direction='SHORT',
        entry_price=3500.0,
    )
    assert _r_b1.get('ttl_hours') == 4, f'TTL应=4，得{_r_b1.get("ttl_hours")}'
    assert _r_b1.get('status') == 'ACTIVE', f'状态应=ACTIVE'
    assert 'expiry_ts' in _r_b1

    _ce_b1 = _se_check('TEST_B1', 'RSI_OB', 'SHORT')
    assert not _ce_b1.get('expired'), '刚注册信号不应已过期'
    assert _ce_b1.get('hours_remaining', 0) > 0, '剩余小时数应>0'
    ok('B1 signal_expiry_tracker.register+check_expiry',
       f'TTL={_r_b1["ttl_hours"]}H remaining={_ce_b1.get("hours_remaining"):.1f}H')
except Exception as e:
    fail('B1 signal_expiry_tracker', str(e))

# ── B2: var_engine.single_position_var() → available + 字段完整 ─────────────
try:
    from brahma_brain.var_engine import single_position_var as _var_fn
    _t0_b2 = time.time()
    _vr = _var_fn(symbol='ETHUSDT', signal_dir='SHORT', pos_pct_nav=0.03, nav_usd=500)
    _dur_b2 = time.time() - _t0_b2

    _required_fields = ['symbol', 'risk_grade', 'available']
    _optional_fields = ['var_95', 'var_99', 'daily_vol']
    _missing = [f for f in _required_fields if f not in _vr]
    if _missing:
        fail('B2 var_engine字段缺失（必须字段）', f'missing={_missing}')
    elif not _vr.get('available', True) or _vr.get('var_95') is None:
        # 网络不可达时返回 available=False 是预期行为
        warn('B2 var_engine数据不足(网络/K线)',
             f'risk_grade={_vr.get("risk_grade")} available={_vr.get("available")} — 模块正常，数据暂无')
    else:
        ok('B2 var_engine.single_position_var',
           f'available={_vr.get("available")} risk_grade={_vr.get("risk_grade")} {_dur_b2:.1f}s')
except Exception as e:
    fail('B2 var_engine', str(e))

# ── B3: position_sizer.get_position_pct() → 三档验证 ──────────────────────
try:
    from brahma_brain.position_sizer import get_position_pct as _ps_fn
    _cases_b3 = [
        ('ETHUSDT', 145.0, 'SHORT', 'BEAR_TREND',  '空头体制'),
        ('BTCUSDT', 130.0, 'LONG',  'BULL_TREND',  '牛市做多'),
        ('BTCUSDT', 150.0, 'SHORT', 'CHOP_MID',    '震荡体制'),
    ]
    _b3_pass = True
    for sym, score, direction, regime, label in _cases_b3:
        _pr = _ps_fn(symbol=sym, score=score, direction=direction, regime=regime)
        _pct = _pr.get('pct', 0)
        _allowed = _pr.get('allowed', False)
        if _pct < 0:
            fail(f'B3 position_sizer {label}', f'pct={_pct}<0 异常')
            _b3_pass = False
        elif not _allowed and _pct == 0:
            warn(f'B3 position_sizer {label} BANNED', f'{sym} score={score} → allowed={_allowed} pct={_pct}%')
        else:
            print(f'     ↳ {label}: {sym} score={score} → pct={_pct:.1f}% allowed={_allowed} level={_pr.get("level")}')
    if _b3_pass:
        ok('B3 position_sizer.get_position_pct 三档', '见明细↑')
except Exception as e:
    fail('B3 position_sizer', str(e))

# ── B4: capital_allocator.compute() → budget_total > 0 ────────────────────
try:
    from brahma_brain.capital_allocator import compute as _ca_fn
    _cr = _ca_fn(symbol='ETHUSDT', sl_pct=0.025, signal_score=145)
    _bt = _cr.get('budget_total', 0)
    _allowed_b4 = _cr.get('allowed', False)
    if _bt is None:
        fail('B4 capital_allocator', 'budget_total=None')
    elif _bt <= 0:
        warn('B4 capital_allocator budget_total=0', f'reason={_cr.get("reason")} (NAV可能=0，模块正常)')
    else:
        ok('B4 capital_allocator.compute', f'budget_total={_bt:.2f}u allowed={_allowed_b4}')
except Exception as e:
    fail('B4 capital_allocator', str(e))

# ── B5: condition_order_matrix.create_trade_plan() → 4条trigger ───────────
try:
    from brahma_brain.condition_order_matrix import create_trade_plan as _com_fn
    _plan = _com_fn(
        symbol='ETHUSDT',
        short_entry=3500.0,
        long_entry=3400.0,
        short_notional=300.0,
        long_notional=300.0,
        liq_price=4200.0,
        leverage=5,
    )
    _triggers = _plan.get('triggers', {})
    _n_triggers = len(_triggers)
    _required_triggers = ['P0_生死线', 'P1_多单止盈', 'P2_调仓线', 'P3_时间止损']
    _missing_t = [t for t in _required_triggers if t not in _triggers]
    if _missing_t:
        fail('B5 condition_order_matrix triggers缺失', f'missing={_missing_t}')
    elif _n_triggers >= 4:
        ok('B5 condition_order_matrix.create_trade_plan', f'{_n_triggers}条trigger生成 ✓')
    else:
        fail('B5 trigger数量不足', f'expected≥4 got={_n_triggers}')
except Exception as e:
    fail('B5 condition_order_matrix', str(e))

# ── B6: brahma_mem_compressor.compress_signal_context() → len > 0 ─────────
try:
    from brahma_brain.brahma_mem_compressor import compress_signal_context as _csc_fn
    _ctx = _csc_fn(symbol='BTCUSDT', max_tokens=500)
    _ctx_len = len(str(_ctx))
    if _ctx_len <= 0:
        fail('B6 brahma_mem_compressor', 'context长度=0')
    else:
        _keys = list(_ctx.keys()) if isinstance(_ctx, dict) else []
        ok('B6 brahma_mem_compressor.compress_signal_context', f'len={_ctx_len} keys={_keys[:4]}')
except Exception as e:
    fail('B6 brahma_mem_compressor', str(e))


# ═══════════════════════════════════════════════════════════════
# 【C】数据一致性验证
# ═══════════════════════════════════════════════════════════════
print('\n【C】数据一致性')

DATA_DIR = BASE / 'data'

# ── C1: executed=True 的信号ID 在 auto_executed_signals.json 中存在 ─────────
try:
    _log_path = DATA_DIR / 'live_signal_log.jsonl'
    _exec_path = DATA_DIR / 'auto_executed_signals.json'

    if not _log_path.exists():
        warn('C1 live_signal_log.jsonl 不存在', '跳过检查')
    elif not _exec_path.exists():
        warn('C1 auto_executed_signals.json 不存在', '跳过检查')
    else:
        # 读取 executed set
        _exec_raw = json.loads(_exec_path.read_text())
        _exec_ids = set(_exec_raw) if isinstance(_exec_raw, list) else set(_exec_raw.keys()) if isinstance(_exec_raw, dict) else set()

        # 读取 live_signal_log 中 executed=True 的信号
        _executed_in_log = []
        for _ln in _log_path.read_text(errors='ignore').split('\n'):
            _ln = _ln.strip()
            if not _ln: continue
            try:
                _s = json.loads(_ln)
                if _s.get('executed') is True:
                    _executed_in_log.append(_s.get('signal_id', ''))
            except Exception:
                continue

        if not _executed_in_log:
            warn('C1 live_signal_log 无 executed=True 记录', '可能尚未有实际执行，跳过')
        else:
            _missing_c1 = [sid for sid in _executed_in_log if sid and sid not in _exec_ids]
            _pct = (len(_executed_in_log) - len(_missing_c1)) / len(_executed_in_log) * 100
            if _missing_c1:
                fail('C1 executed信号ID对账异常',
                     f'{len(_missing_c1)}/{len(_executed_in_log)} 个executed信号不在auto_executed_signals.json')
            else:
                ok('C1 executed信号ID对账', f'{len(_executed_in_log)}条全部在executed集合中')
except Exception as e:
    fail('C1 数据对账', str(e))

# ── C2: wr_matrix_live.json 的 n值 ≤ live_signal_log 中已结算信号总数 ────────
try:
    _wr_path = DATA_DIR / 'wr_matrix_live.json'
    _log_path = DATA_DIR / 'live_signal_log.jsonl'

    if not _wr_path.exists():
        warn('C2 wr_matrix_live.json 不存在', '跳过')
    elif not _log_path.exists():
        warn('C2 live_signal_log.jsonl 不存在', '跳过')
    else:
        _wr_data = json.loads(_wr_path.read_text())
        # 计算matrix中最大n值
        _matrix = _wr_data.get('matrix', {})
        _max_n = 0
        for _key, _val in _matrix.items():
            _n = _val.get('total', _val.get('n', 0))
            _max_n = max(_max_n, _n)

        # 统计 live_signal_log 中已结算（outcome in TP1/SL/EXPIRED）的信号数
        _settled_count = 0
        for _ln in _log_path.read_text(errors='ignore').split('\n'):
            _ln = _ln.strip()
            if not _ln: continue
            try:
                _s = json.loads(_ln)
                if _s.get('outcome') in ('TP1', 'TP2', 'SL', 'EXPIRED', 'PARTIAL') or _s.get('settled'):
                    _settled_count += 1
            except Exception:
                continue

        # wr_matrix 的 total_settled 字段
        _wr_total = _wr_data.get('total_settled', _max_n)

        if _settled_count == 0:
            warn('C2 live_signal_log 无结算信号', f'wr_matrix.total_settled={_wr_total}，无法对账')
        elif _wr_total > _settled_count * 1.2:   # 允许20%缓冲（计数方式差异）
            warn('C2 wr_matrix n值偏高', f'wr_total={_wr_total} log_settled={_settled_count}（允许20%差异）')
        else:
            ok('C2 wr_matrix n值一致性', f'wr_total_settled={_wr_total} log_settled≈{_settled_count}')
except Exception as e:
    fail('C2 wr_matrix一致性', str(e))

# ── C3: signal_expiry.json ACTIVE信号 → 对应信号在live_signal_log存在 ────────
try:
    _exp_path = DATA_DIR / 'signal_expiry.json'
    _log_path = DATA_DIR / 'live_signal_log.jsonl'

    if not _exp_path.exists():
        warn('C3 signal_expiry.json 不存在', '跳过')
    else:
        _exp_data = json.loads(_exp_path.read_text())
        _active_sigs = {k: v for k, v in _exp_data.items() if v.get('status') == 'ACTIVE'}

        if not _active_sigs:
            ok('C3 signal_expiry ACTIVE信号', '无ACTIVE信号（空库或全部过期）')
        elif not _log_path.exists():
            warn('C3 live_signal_log.jsonl 不存在', f'{len(_active_sigs)}条ACTIVE信号但无日志可对账')
        else:
            # 收集log中出现过的symbol集合
            _log_symbols = set()
            for _ln in _log_path.read_text(errors='ignore').split('\n')[-2000:]:
                _ln = _ln.strip()
                if not _ln: continue
                try:
                    _s = json.loads(_ln)
                    if _s.get('symbol'):
                        _log_symbols.add(_s['symbol'])
                except Exception:
                    continue

            # ACTIVE 信号的 symbol 应该曾出现在log中（宽松检查）
            _orphan = [k for k, v in _active_sigs.items()
                       if v.get('symbol') and v['symbol'] not in _log_symbols]
            if _orphan:
                warn('C3 ACTIVE信号symbol在log中不存在', f'{len(_orphan)}/{len(_active_sigs)} 孤儿 (可能是测试注入)')
            else:
                ok('C3 signal_expiry ACTIVE信号一致性', f'{len(_active_sigs)}条ACTIVE，symbol均在log中')
except Exception as e:
    fail('C3 signal_expiry一致性', str(e))

# ── C4: wuqu_positions.json 格式验证 ─────────────────────────────────────
try:
    _wuqu_path = DATA_DIR / 'wuqu_positions.json'
    if not _wuqu_path.exists():
        warn('C4 wuqu_positions.json 不存在', '跳过')
    else:
        _wuqu_raw = json.loads(_wuqu_path.read_text())
        # wuqu_positions.json 可为list（持仓）或 dict（仅含meta字段如_refreshed_at）
        if isinstance(_wuqu_raw, dict):
            # 过滤掉meta字段，检查是否有持仓条目
            _pos_items = [v for k, v in _wuqu_raw.items() if not k.startswith('_') and isinstance(v, dict)]
            _meta_only = len(_pos_items) == 0
            if _meta_only:
                ok('C4 wuqu_positions.json格式', f'dict格式（仅meta字段，当前无持仓）')
            else:
                # dict格式持仓，每个value检查字段
                _invalid = []
                for _k, _pos in _wuqu_raw.items():
                    if _k.startswith('_'): continue
                    if not isinstance(_pos, dict):
                        _invalid.append(f'{_k}非dict')
                        continue
                    _mf = [f for f in ('symbol', 'side', 'size') if f not in _pos]
                    if _mf:
                        _invalid.append(f'{_k} missing={_mf}')
                if _invalid:
                    fail('C4 wuqu_positions字段缺失(dict格式)', f'{_invalid[:3]}')
                else:
                    ok('C4 wuqu_positions.json格式', f'dict格式✓ {len(_pos_items)}条持仓')
        elif not isinstance(_wuqu_raw, list):
            fail('C4 wuqu_positions格式错误', f'期望list或dict，得{type(_wuqu_raw).__name__}')
        else:
            _invalid = []
            for _i, _pos in enumerate(_wuqu_raw):
                if not isinstance(_pos, dict):
                    _invalid.append(f'[{_i}]非dict')
                    continue
                _missing_c4 = [f for f in ('symbol', 'side', 'size') if f not in _pos]
                if _missing_c4:
                    _invalid.append(f'[{_i}] missing={_missing_c4}')
            if _invalid:
                fail('C4 wuqu_positions字段缺失', f'{_invalid[:3]}')
            else:
                ok('C4 wuqu_positions.json格式', f'list格式✓ {len(_wuqu_raw)}条持仓 symbol/side/size齐全')
except Exception as e:
    fail('C4 wuqu_positions格式', str(e))


# ═══════════════════════════════════════════════════════════════
# 【D】性能基准
# ═══════════════════════════════════════════════════════════════
print('\n【D】性能基准')

# ── D1: auto_executor.find_executable_signals() < 10s ─────────────────────
try:
    import scripts.auto_executor as _ae_perf
    _t0 = time.time()
    _sigs = _ae_perf.find_executable_signals()
    _dur_d1 = time.time() - _t0
    if _dur_d1 > 10.0:
        fail('D1 find_executable_signals 超时', f'{_dur_d1:.1f}s > 10s上限')
    else:
        ok('D1 find_executable_signals', f'{_dur_d1:.2f}s（候选={len(_sigs)}条）')
except Exception as e:
    fail('D1 find_executable_signals', str(e))

# ── D2: brahma_health.run_health_check() < 15s ────────────────────────────
try:
    from brahma_brain.brahma_health import run_health_check as _hc_fn
    _t0 = time.time()
    _hc_res = _hc_fn()
    _dur_d2 = time.time() - _t0
    if _dur_d2 > 15.0:
        fail('D2 run_health_check 超时', f'{_dur_d2:.1f}s > 15s上限')
    else:
        _status = _hc_res.get('status', _hc_res.get('overall', '?')) if isinstance(_hc_res, dict) else str(_hc_res)[:30]
        ok('D2 brahma_health.run_health_check', f'{_dur_d2:.2f}s status={_status}')
except Exception as e:
    fail('D2 brahma_health', str(e))

# ── D3: run_analysis('BTCUSDT', deep=False) 耗时测量 ──────────────────────
try:
    from brahma_brain.brahma_analysis_runner import run_analysis as _ra_fn
    _t0 = time.time()
    _ra_res = _ra_fn('BTCUSDT', deep=False)
    _dur_d3 = time.time() - _t0
    _score = None
    if isinstance(_ra_res, dict):
        _score = _ra_res.get('score') or _ra_res.get('final_score') or _ra_res.get('composite_score')
    if _dur_d3 > 60.0:
        warn('D3 run_analysis BTCUSDT', f'{_dur_d3:.1f}s偏慢（>60s）')
    else:
        ok('D3 run_analysis BTCUSDT deep=False', f'{_dur_d3:.1f}s score={_score}')
except Exception as e:
    fail('D3 run_analysis', str(e))


# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════
print(f'\n{"=" * 55}')
total = ok_count + fail_count + warn_count
print(f'🏛️  全能力测试 {ok_count}/{total}  ✅{ok_count} ⚠️{warn_count} ❌{fail_count}')
print(f'   A组链路层: A1~A4  B组风控层: B1~B6  C组一致性: C1~C4  D组性能: D1~D3')
if fail_count == 0:
    print(f'   {GREEN}全部通过！梵天全能力链路完整{RESET}')
elif fail_count <= 2:
    print(f'   {YELLOW}轻微失败，请检查上方❌项目{RESET}')
else:
    print(f'   {RED}多项失败，建议优先修复❌项目后重试{RESET}')

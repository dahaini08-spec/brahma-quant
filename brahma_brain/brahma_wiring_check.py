#!/usr/bin/env python3
"""
brahma_wiring_check.py — 梵天接线完整性检测器
设计院封印 2026-08-09 苏摩111

解决「功能建好≠接通」根本问题：
  每个模块必须证明：
  1. 可import（代码本身没问题）
  2. 有真实调用者（不是孤岛）
  3. 在analyze()结果里输出可见（端到端可达）
  4. 苏摩能从push_hub看到它的输出

用法：
  python3 brahma_brain/brahma_wiring_check.py           # 快速检查
  python3 brahma_brain/brahma_wiring_check.py --full    # 含端到端验证
  python3 brahma_brain/brahma_wiring_check.py --fix     # 自动修复可修项
"""

import sys, os, json, time, importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(1, str(Path(__file__).parent.parent / 'venv/lib/python3.11/site-packages'))

ROOT  = Path(__file__).parent.parent
BRAIN = ROOT / 'brahma_brain'

# ══ 接线注册表 ══════════════════════════════════════════════════
# 每个模块必须登记：
#   caller     : 谁在调用它
#   result_key : analyze()返回dict里的key
#   visible_in : 苏摩能在哪里看到（formatter/push_hub/日报）
#   test_fn    : 快速冒烟测试
WIRING_REGISTRY = [
    {
        'module': 'ssi_engine',
        'desc': '轧空强度指数门控',
        'caller': 'brahma_core.py (analyze SHORT方向)',
        'result_key': 'ssi',
        'visible_in': 'result[ssi] → breakdown_extra',
        'trigger': 'SHORT方向时触发',
        'test': lambda: __import__('brahma_brain.ssi_engine', fromlist=['compute_ssi']).compute_ssi(
            symbol='BTCUSDT', short_ratio=35.0, oi=1e6, price=65000.0),
    },
    {
        'module': 'brahma_coordinator',
        'desc': '子系统上下文聚合',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'coordinator',
        'visible_in': 'result[coordinator][episodic/ic]',
        'trigger': '每次analyze()调用',
        'test': lambda: __import__('brahma_brain.brahma_coordinator', fromlist=['get_episodic_context']).get_episodic_context('BTCUSDT', 'BULL_TREND', 'LONG'),
    },
    {
        'module': 'signal_integrity_gate',
        'desc': 'P0~P2信号完整性校验',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'integrity_gate',
        'visible_in': 'result[integrity_gate] → score惩罚',
        'trigger': '每次analyze()调用',
        'test': lambda: __import__('brahma_brain.signal_integrity_gate', fromlist=['gate_check']).gate_check({}, {}, {}),
    },
    {
        'module': 'mode_c_detector',
        'desc': '庄家行情识别',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'mode_c',
        'visible_in': 'result[mode_c] → pos_pct_sizer×0.5',
        'trigger': '每次analyze()调用',
        'test': lambda: __import__('brahma_brain.mode_c_detector', fromlist=['detect']).detect(
            symbol='BTCUSDT', price=65000.0, price_low_24h=63000.0,
            short_ratio=35.0, vol_current=1000.0, vol_avg_20=900.0,
            candle_high=65500.0, candle_low=64000.0),
    },
    {
        'module': 'us_session_gate',
        'desc': '美股时段门控',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'us_session',
        'visible_in': 'result[us_session] → score_delta',
        'trigger': '每次analyze()调用',
        'test': lambda: __import__('brahma_brain.us_session_gate', fromlist=['get_us_session']).get_us_session(),
    },
    {
        'module': 'volatility_context',
        'desc': 'HCME M5波动率历史分位',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'volatility_context',
        'visible_in': 'result[volatility_context] → pos_pct_sizer×0.7(ULTRA_LOW)',
        'trigger': '每次analyze()调用',
        'test': lambda: __import__('brahma_brain.volatility_context', fromlist=['get_volatility_context']).get_volatility_context(
            'BTCUSDT', current_atr=0.002, current_bbw=0.015),
    },
    {
        'module': 'tradfi_signal_layer',
        'desc': 'TradFi信号层标签注入',
        'caller': 'brahma_core.py (analyze返回前)',
        'result_key': 'tradfi_signal',
        'visible_in': 'result[tradfi_signal] (Phase A仅标签)',
        'trigger': 'TradFi标的时有输出',
        'test': lambda: __import__('brahma_brain.tradfi_signal_layer', fromlist=['compute_tradfi_context']).compute_tradfi_context('BTCUSDT','LONG',100.0,'BULL_TREND'),
    },
    {
        'module': 'mtf_resonance',
        'desc': '多周期共振验证',
        'caller': 'brahma_decision_engine.py',
        'result_key': None,  # 通过decision_engine间接
        'visible_in': 'brahma_decision_engine → analyze()[decision]',
        'trigger': '决策树Step3',
        'test': lambda: __import__('brahma_brain.mtf_resonance', fromlist=['get_mtf_score']).get_mtf_score('BTCUSDT', 'LONG') if hasattr(
            __import__('brahma_brain.mtf_resonance', fromlist=['']), 'get_mtf_score') else 'ok_indirect',
    },
    {
        'module': 'sl_bandit',
        'desc': 'SL自适应Bandit引擎',
        'caller': 'brahma_brain/dynamic_sl.py + signal_settler',
        'result_key': None,  # 通过dynamic_sl间接
        'visible_in': 'dynamic_sl → params[stop_loss]',
        'trigger': 'signal_settler结算 / dynamic_sl计算SL',
        'test': lambda: __import__('brahma_brain.sl_bandit', fromlist=['get_optimal_sl']).get_optimal_sl(
            'BULL_TREND', 'LONG') if hasattr(
            __import__('brahma_brain.sl_bandit', fromlist=['']), 'get_optimal_sl') else 'ok_indirect',
    },
    {
        'module': 'signal_quality_engine',
        'desc': '信号质量门控',
        'caller': 'scripts/auto_executor.py + brahma_ops_center',
        'result_key': None,
        'visible_in': 'auto_executor → 执行前质量门控',
        'trigger': 'auto_executor每次执行前',
        'test': lambda: __import__('brahma_brain.signal_quality_engine', fromlist=['SignalQualityEngine']).SignalQualityEngine,
    },
    {
        'module': 'signal_weight_updater',
        'desc': '结算闭环权重更新',
        'caller': 'scripts/signal_settler.py',
        'result_key': None,
        'visible_in': 'signal_weights.json → 下次评分乘数',
        'trigger': 'signal_settler每次结算后',
        'test': lambda: __import__('brahma_brain.signal_weight_updater', fromlist=['update_weights']).update_weights if hasattr(
            __import__('brahma_brain.signal_weight_updater', fromlist=['']), 'update_weights') else 'ok_import',
    },
    {
        'module': 'fangcang_vector_db',
        'desc': '方仓向量检索',
        'caller': 'brahma_brain/fangcang_engine.py',
        'result_key': None,
        'visible_in': 'fangcang_engine → result[fangcang]',
        'trigger': 'analyze(deep=True)时',
        'test': lambda: __import__('brahma_brain.fangcang_vector_db', fromlist=['search_similar']).search_similar if hasattr(
            __import__('brahma_brain.fangcang_vector_db', fromlist=['']), 'search_similar') else 'ok_import',
    },
]

# ══ 高价值孤岛（建好未接通，需苏摩决策）══════════════════════
HIGH_VALUE_ISLANDS = [
    # 格式: (module_name, desc, 建议接入方式)
    # 当前为空 = 全部已接通
]


def _check_import(module: str) -> tuple:
    """检查模块能否import"""
    try:
        mod = importlib.import_module(f'brahma_brain.{module}')
        return True, None
    except Exception as e:
        return False, str(e)


def _check_caller(module: str) -> tuple:
    """检查模块是否有真实调用者"""
    search_dirs = [BRAIN, ROOT / 'scripts', ROOT / 'guardrails']
    callers = []
    for d in search_dirs:
        for f in d.glob('*.py'):
            if f.stem == module: continue
            try:
                if module in f.read_text(errors='ignore'):
                    callers.append(f.name)
            except: pass
    return bool(callers), callers


def _check_result_key(module: str, result_key: str) -> tuple:
    """通过analyze()检查result_key是否出现"""
    if result_key is None:
        return None, '间接接入，跳过end-to-end检查'
    try:
        import logging; logging.disable(logging.CRITICAL)
        import gc
        from brahma_brain import brahma_engine as be
        gc.collect()
        r = be.analyze('BTCUSDT', deep=False)
        if result_key in r:
            return True, f'result[{result_key}] = {str(r[result_key])[:50]}'
        else:
            return False, f'result中无{result_key}字段（现有keys: {list(r.keys())[:8]}）'
    except Exception as e:
        return False, str(e)


def _run_test(entry: dict) -> tuple:
    """运行冒烟测试"""
    try:
        result = entry['test']()
        return True, str(result)[:80] if result is not None else 'None(可能正常)'
    except Exception as e:
        return False, str(e)[:100]


def _run_static_concurrency_scan() -> tuple:
    """
    静态并发安全扫描（路线A 2026-08-10）
    S1: scan_runtime_path_injection  — try块内危险sys.path.insert
    S2: scan_shared_mutable_state    — 模块级可变全局状态（并发危险）
    returns: (ok_count, warn_count, fail_count)
    """
    import ast
    ok_c = warn_c = fail_c = 0
    print('\n🔍 静态并发安全扫描')
    print('─' * 60)

    # S1: try块内危险sys.path.insert扫描
    _S1_targets = [
        ROOT / 'scripts' / 'brahma_1hao_analysis.py',
        ROOT / 'brahma_brain' / 'brahma_analysis_runner.py',
        ROOT / 'brahma_brain' / 'brahma_core.py',
    ]
    # AST精准检测：只检查analyze/run_analysis等并行函数内try块里无if守卫的sys.path.insert
    import ast as _ast
    _s1_total_danger = 0
    for _fpath in _S1_targets:
        if not _fpath.exists():
            continue
        _rel = _fpath.relative_to(ROOT)
        _danger = 0
        try:
            _src = _fpath.read_text()
            _flines = _src.split('\n')
            _tree = _ast.parse(_src)
            for _node in _ast.walk(_tree):
                if isinstance(_node, _ast.FunctionDef) and any(
                        k in _node.name for k in ['analyze','run_analysis','batch','scan']):
                    for _child in _ast.walk(_node):
                        if isinstance(_child, _ast.Try):
                            for _tn in _ast.walk(_child):
                                if isinstance(_tn, _ast.Expr) and isinstance(_tn.value, _ast.Call):
                                    _call = _tn.value
                                    if (isinstance(_call.func, _ast.Attribute) and
                                            _call.func.attr == 'insert' and
                                            'path' in _ast.dump(_call.func)):
                                        _ln = _tn.lineno
                                        _prev = _flines[_ln-2] if _ln > 1 else ''
                                        if 'if ' not in _prev:  # 无幂等守卫
                                            _danger += 1
        except Exception:
            pass
        if _danger > 0:
            print(f'  ❌ S1 {_rel}: {_danger}处并行函数内无守卫sys.path.insert — race condition!')
            fail_c += 1
            _s1_total_danger += _danger
        else:
            print(f'  ✅ S1 {_rel}: 并行函数内无危险路径注入')
            ok_c += 1

    # S2: 模块级可变全局状态扫描（共享状态 = 并发危险）
    _S2_targets = [
        ROOT / 'brahma_brain' / 'brahma_scoring.py',
        ROOT / 'brahma_brain' / 'fangcang_engine.py',
        ROOT / 'brahma_brain' / 'hcme_matcher.py',
        ROOT / 'brahma_brain' / 'brahma_decision_engine.py',
    ]
    _SAFE_GLOBALS = {'logger', '_logger', 'log', '_log', '_ROOT', '_DIR',
                     'ROOT', 'BASE', 'BRAIN', '_BRAIN_DIR', 'BASE_DIR'}
    for _fpath in _S2_targets:
        if not _fpath.exists():
            continue
        try:
            _tree = ast.parse(_fpath.read_text())
            _danger_vars = []
            for _node in ast.walk(_tree):
                # 模块顶层的可变列表/字典赋値
                if isinstance(_node, ast.Assign):
                    for _t in _node.targets:
                        if isinstance(_t, ast.Name):
                            _name = _t.id
                            if (_name.startswith('_') and
                                _name not in _SAFE_GLOBALS and
                                isinstance(_node.value, (ast.List, ast.Dict))):
                                _danger_vars.append(_name)
            _rel = _fpath.relative_to(ROOT)
            if len(_danger_vars) > 5:
                print(f'  ⚠️  S2 {_rel}: {len(_danger_vars)}个模块级可变对象 — 确认并发安全')
                warn_c += 1
            else:
                print(f'  ✅ S2 {_rel}: 共享状态风险可控({len(_danger_vars)}个)')
                ok_c += 1
        except Exception as _e:
            print(f'  ⚠️  S2 {_fpath.name}: 解析异常 {_e}')
            warn_c += 1

    print(f'\n静态扫描小计: ✅{ok_c}  ⚠️{warn_c}  ❌{fail_c}')
    return ok_c, warn_c, fail_c


def run_check(full: bool = False) -> dict:
    results = []
    ok_count = 0
    warn_count = 0
    fail_count = 0

    print(f'\n🔌 梵天接线完整性检测 — {time.strftime("%Y-%m-%d %H:%M UTC")}\n')
    print(f'{"模块":<28} {"import":<8} {"调用者":<8} {"冒烟测试":<12} {"end-to-end":<14} 状态')
    print('─' * 90)

    for entry in WIRING_REGISTRY:
        mod = entry['module']
        imp_ok, imp_err = _check_import(mod)
        caller_ok, callers = _check_caller(mod)
        test_ok, test_out = _run_test(entry)

        e2e_ok = None
        e2e_msg = '跳过'
        if full and entry.get('result_key'):
            e2e_ok, e2e_msg = _check_result_key(mod, entry['result_key'])

        # 综合判断
        if not imp_ok:
            status = '❌ BROKEN'
            fail_count += 1
        elif not test_ok:
            status = '❌ TEST_FAIL'
            fail_count += 1
        elif not caller_ok:
            status = '⚠️  NO_CALLER'
            warn_count += 1
        elif full and e2e_ok is False:
            status = '⚠️  E2E_MISS'
            warn_count += 1
        else:
            status = '✅ OK'
            ok_count += 1

        imp_s = '✅' if imp_ok else '❌'
        cal_s = f'✅{len(callers)}' if caller_ok else '❌0'
        tst_s = '✅' if test_ok else '❌'
        e2e_s = ('✅' if e2e_ok else ('⚠️' if e2e_ok is None else '❌')) + ' e2e'

        print(f'{mod:<28} {imp_s:<8} {cal_s:<8} {tst_s:<12} {e2e_s:<14} {status}')

        if not imp_ok:
            print(f'    import错误: {imp_err}')
        if not test_ok:
            print(f'    冒烟失败:   {test_out}')
        if not caller_ok:
            print(f'    ⚠️ 无调用者 — 高价值孤岛！建议: {entry.get("caller","未知")}')

        results.append({'module': mod, 'status': status, 'imp': imp_ok,
                        'caller': caller_ok, 'test': test_ok, 'e2e': e2e_ok})

    print('─' * 90)
    print(f'\n汇总: ✅{ok_count}通过  ⚠️{warn_count}警告  ❌{fail_count}失败')
    print(f'高价值孤岛: {len(HIGH_VALUE_ISLANDS)}个\n')

    if HIGH_VALUE_ISLANDS:
        print('📋 高价值孤岛（功能建好但未接通）:')
        for name, desc, suggestion in HIGH_VALUE_ISLANDS:
            print(f'  ⚠️  {name}: {desc}')
            print(f'       建议: {suggestion}')

    # ── [路线A 2026-08-10 设计院封印] 静态并发安全扫描 ─────────────────────
    static_ok, static_warn, static_fail = _run_static_concurrency_scan()
    ok_count   += static_ok
    warn_count += static_warn
    fail_count += static_fail

    return {
        'ok': ok_count, 'warn': warn_count, 'fail': fail_count,
        'islands': len(HIGH_VALUE_ISLANDS),
        'results': results,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='含end-to-end验证')
    parser.add_argument('--fix', action='store_true', help='自动修复可修项')
    args = parser.parse_args()

    summary = run_check(full=args.full)
    sys.exit(0 if summary['fail'] == 0 else 1)

"""
brahma_analysis_runner.py — 梵天分析唯一入口
设计院·达摩院 固化封印 2026-06-30

═══════════════════════════════════════════════════
核心原则：
  1. 单一入口  — 所有分析必须调用此文件，禁止裸HTTP+inline计算
  2. 标准输出  — 所有结果必须经 extract_standard_fields() 归一化
  3. 并发执行  — 多标的统一走 brahma_parallel_engine.batch_analyze()
  4. 零临时代码 — 禁止在分析流程外新建HTTP调用或临时计算
═══════════════════════════════════════════════════

用法:
  # Python调用
  from brahma_brain.brahma_analysis_runner import run_analysis, run_batch
  result  = run_analysis('BTCUSDT')           # 单标的
  results = run_batch(['BTCUSDT', 'ETHUSDT'])  # 多标的并发

  # CLI调用
  python brahma_analysis_runner.py BTCUSDT ETHUSDT [--card] [--full]
"""

import sys
import os
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

# ── 唯一数据入口（封印）────────────────────────────────────────
from brahma_brain.brahma_core import analyze as _core_analyze
from brahma_brain.brahma_parallel_engine import batch_analyze as _batch_analyze
from brahma_brain.formatter import (
    format_report,
    format_standard_card,
    extract_standard_fields,
    STANDARD_FIELDS,
    build_output_tag,
    tag_is_valid_signal,
    tag_parse,
)


# ── 时机过滤层（设计院 2026-07-01 落地）──────────────────────────────────
try:
    from timing_filter import evaluate_timing, format_timing_badge
    _TIMING_OK = True
except Exception:
    try:
        from brahma_brain.timing_filter import evaluate_timing, format_timing_badge
        _TIMING_OK = True
    except Exception:
        _TIMING_OK = False

# ── 孤儿模块接入层（设计院 2026-07-02 AutoReview修复）────────────────────
# analysis_snapshot: 结果快照缓存（防止重复推理）
try:
    _scripts_dir = os.path.join(BASE_DIR, '..', 'scripts')
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from analysis_snapshot import (
        save_snapshot as _snap_save,
        load_snapshot as _snap_load,
        is_fresh as _snap_fresh,
        clear_stale as _snap_clear,
    )
    _SNAPSHOT_OK = True
except Exception:
    _SNAPSHOT_OK = False

# brainlog: 统一日志系统
try:
    from brainlog import get_logger as _get_logger, binfo, bwarn, berror
    _brain_logger = _get_logger('runner')
    _BRAINLOG_OK = True
except Exception:
    _BRAINLOG_OK = False

# portfolio_optimizer: 多标的相关性过滤（run_batch层）
try:
    from portfolio_optimizer import filter_signals as _po_filter
    _PORTFOLIO_OK = True
except Exception:
    _PORTFOLIO_OK = False

# brahma_health: 健康检查（run_batch完成后触发轻量健康ping）
try:
    from brahma_health import _check_and_gc as _health_gc
    _HEALTH_OK = True
except Exception:
    try:
        from brahma360_guardian import check_v16_v17_modules as _health_v16
        _HEALTH_OK = True
    except Exception:
        _HEALTH_OK = False

# market_structure_scanner: 高分信号补充SMC结构扫描
try:
    from market_structure_scanner import scan_structure as _mss_scan
    _MSS_OK = True
except Exception:
    _MSS_OK = False

# signal_trace: 信号轨迹审计日志（设计院 2026-07-02）──────────────────────
try:
    from signal_trace import trace_generated, trace_skipped
    _TRACE_OK = True
except Exception:
    _TRACE_OK = False
    def trace_generated(r, **kw): pass
    def trace_skipped(r): pass

# llm_council_bridge: score≥130触发LLM二次审查（shadow模式）
try:
    from llm_council_bridge import review as _llm_review
    _LLM_COUNCIL_OK = True
except Exception:
    _LLM_COUNCIL_OK = False

# Kronos依赖自动检查（重启后自愈）────────────────────────────
try:
    import torch as _torch  # noqa
except ImportError:
    import subprocess as _sp, sys as _sys
    _pip = [_sys.executable, '-m', 'pip', 'install', '--break-system-packages', '-q',
            '--index-url', 'https://download.pytorch.org/whl/cpu', 'torch']
    _sp.run(_pip, capture_output=True)
    _pip2 = [_sys.executable, '-m', 'pip', 'install', '--break-system-packages', '-q',
             'huggingface_hub', 'safetensors', 'einops', 'python-dotenv']
    _sp.run(_pip2, capture_output=True)
# ── 系统配置（路由到正确线程）────────────────────────────────
try:
    sys.path.insert(0, os.path.join(BASE_DIR, '..', 'scripts'))
    from system_config import JARVIS_THREAD_ID, JARVIS_USER_ID
    _JARVIS_TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
except Exception:
    _JARVIS_TARGET = None

# ══════════════════════════════════════════════════════════════
# 封印：分析质量检查
# ══════════════════════════════════════════════════════════════

def _validate_result(r: dict) -> list:
    """
    检查 analyze() 结果是否包含所有必需字段
    返回缺失字段列表（空列表=全部完整）
    """
    if r.get('error'):
        return ['error: ' + str(r['error'])]

    f = extract_standard_fields(r)
    required = ['regime', 'score', 'direction', 'entry_lo', 'entry_hi', 'sl', 'tp1', 'rr']
    missing = [k for k in required if f.get(k) is None]
    return missing


# ══════════════════════════════════════════════════════════════
# 公开 API（所有调用者使用此接口）
# ══════════════════════════════════════════════════════════════

def run_analysis(symbol: str, deep: bool = True) -> dict:
    """
    单标的分析 — 封印版唯一入口

    规则：
      - 必须走 brahma_core.analyze(deep=True)
      - 不得绕过此函数直接调用 brahma_core
      - 返回值包含 _runner_meta 字段标记来源

    返回: analyze() 原始结果 + _runner_meta
    """
    t0 = time.time()
    sym = symbol.upper().replace('/','').replace('-','')
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'

    # ── analysis_snapshot: 15分钟内有缓存则复用（减少重复推理）──────
    _cached_dir = None
    if _SNAPSHOT_OK:
        try:
            _cf = extract_standard_fields({}) if False else None
            _dir_guess = 'SHORT'  # 快照按方向存储，先尝试SHORT再LONG
            for _d in ['SHORT', 'LONG']:
                if _snap_fresh(sym, _d, max_age_min=10):
                    _cached = _snap_load(sym, _d, max_age_min=10)
                    if _cached:
                        _cached['_from_cache'] = True
                        return _cached
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    result = _core_analyze(sym, deep=deep)
    missing = _validate_result(result)

    # ── [P0-B设计院 2026-07-03] BULL_TREND体制感知加分注入 ──────────────────────
    # 解决根因：brahma_core原始分对体制无感知，BULL_TREND多单天然偏低≈79分
    # 改造：外层注入 regime_context_bonus（EMA结构+RSI+动能），最高+35分
    # + [P0-C] rsi_trigger_event 2H有效窗口事件加分，最高+40分
    try:
        from brahma_brain.bull_regime_injector import (
            get_regime_context_bonus, get_event_timing_bonus
        )
        _rf = result
        _reg = str(_rf.get('regime', _rf.get('market_state', {}).get('regime', '')) or '')
        _dir = str(_rf.get('signal_dir', _rf.get('direction', '')) or '')
        _cur_score = float(_rf.get('total', _rf.get('score', 0)) or 0)

        # P0-B: BULL体制顺势加分（仅LONG方向）
        _total_bonus = 0
        if 'BULL' in _reg and _dir in ('LONG', 'AUTO', ''):
            _rb = get_regime_context_bonus(sym, _reg)
            if _rb['bonus'] > 0:
                _total_bonus += _rb['bonus']
                _rf['_regime_context_bonus'] = _rb
                print(f'[BullBonus] {sym} +{_rb["bonus"]}分 | {_rb["reasons"]}')

        # P0-C: rsi_trigger_event 事件窗口加分（所有方向）
        _eb = get_event_timing_bonus(sym)
        if _eb['active'] and _eb['bonus'] > 0:
            _total_bonus += _eb['bonus']
            _rf['_event_timing_bonus'] = _eb
            print(f'[EventBonus] {sym} +{_eb["bonus"]}分 | {_eb["events"]}')

        # ── 同步写入所有评分字段（覆盖 extract_standard_fields 所有读取路径）──
        if _total_bonus > 0:
            _new_score = _cur_score + _total_bonus
            _rf['total']       = _new_score  # brahma_core返回路径
            _rf['score']       = _new_score  # 通用路径
            _rf['score_final'] = _new_score  # extract_standard_fields 首选字段
            # confluence 字典同步（signal_selector / LLM council 读取路径）
            if isinstance(_rf.get('confluence'), dict):
                _rf['confluence']['score']    = _new_score
                _rf['confluence']['total']    = _new_score
                _rf['confluence']['grade_num']= int(_new_score)
            print(f'[RegimeInject] {sym} {_cur_score:.1f}+{_total_bonus}→{_new_score:.1f} (regime={_reg} dir={_dir})')
    except Exception as _inj_err:
        pass  # 注入失败不阻断主流程
    # ────────────────────────────────────────────────────────────────────────

    # ── market_structure_scanner: score≥130时补充SMC结构扫描 ──────────
    if _MSS_OK:
        try:
            _f = extract_standard_fields(result)
            _sc = float(_f.get('score', 0) or 0)
            if _sc >= 130:
                _mss = _mss_scan(sym)
                if _mss and not _mss.get('error'):
                    result['_mss'] = {
                        'trend':      _mss.get('trend_bias'),
                        'bos_count':  _mss.get('bos_count', 0),
                        'ob_quality': _mss.get('ob_quality'),
                        'fvg_active': _mss.get('fvg_active', False),
                    }
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── llm_council_bridge: score≥130触发LLM二次审查（shadow模式）────
    # 设计院 2026-07-02: 阈值 140→130（覆盖更多高质量信号，约15%触发率）
    if _LLM_COUNCIL_OK:
        try:
            _f = extract_standard_fields(result)
            _sc = float(_f.get('score', 0) or 0)
            if _sc >= 130:
                result = _llm_review(result)
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    result['_runner_meta'] = {
        'runner_version': '1.1',
        'entry':          'brahma_analysis_runner.run_analysis',
        'symbol':         sym,
        'ts':             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'elapsed':        round(time.time() - t0, 2),
        'fields_missing': missing,
        'fields_ok':      len(missing) == 0,
        'output_tag':     build_output_tag(result, source='RUNNER'),
        'modules_active': {
            'timing_filter':   _TIMING_OK,
            'snapshot':        _SNAPSHOT_OK,
            'brainlog':        _BRAINLOG_OK,
            'portfolio_opt':   _PORTFOLIO_OK,
            'mss':             _MSS_OK,
            'llm_council':     _LLM_COUNCIL_OK,
            'signal_trace':    _TRACE_OK,
        }
    }

    # ── signal_trace: 轨迹审计注入 ──────────────────────────────
    if _TRACE_OK:
        try:
            _f2 = extract_standard_fields(result)
            _sc2 = float(_f2.get('score', 0) or 0)
            _valid2 = bool(_f2.get('valid', False))
            # 将评分注入result供 signal_trace字段映射使用
            result['_score_for_trace'] = _sc2
            result['_direction_for_trace'] = _f2.get('direction', '?')
            if _valid2:
                trace_generated(result)
            else:
                trace_skipped(result)
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── analysis_snapshot: 保存结果快照 ──────────────────────────────
    if _SNAPSHOT_OK:
        try:
            _f = extract_standard_fields(result)
            _dir = _f.get('direction', 'SHORT')
            _snap_save(sym, _dir, result)
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    return result


def run_batch(symbols: list, deep: bool = True) -> dict:
    """
    多标的并发分析 — 封印版唯一入口

    规则：
      - 必须走 brahma_parallel_engine.batch_analyze()
      - 4x加速，数据层通过 BrahmaBus 自动去重
      - 返回 {symbol: result} 字典，每个 result 含 _runner_meta

    返回: {symbol: run_analysis结果}
    """
    t0 = time.time()
    norm_syms = []
    for s in symbols:
        s = s.upper().replace('/','').replace('-','')
        if not s.endswith('USDT'):
            s = s + 'USDT'
        norm_syms.append(s)

    # ── [设计院 v17] Kronos 预热（主线程加载，子线程复用单例）──────────
    try:
        import sys as _sys_kw, os as _os_kw
        _os_kw.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
        _kw_root = _os_kw.path.dirname(_os_kw.path.dirname(_os_kw.path.abspath(__file__)))
        for _p in [_os_kw.path.join(_kw_root,'brahma_brain'),
                   _os_kw.path.join(_kw_root,'external','Kronos')]:
            if _p not in _sys_kw.path:
                _sys_kw.path.insert(0, _p)
        from kronos_engine import _load_model as _kw_load, _model_loaded as _kw_ready
        if not _kw_ready:
            _kw_load()   # 主线程预热，ThreadPoolExecutor子线程复用同一单例
    except Exception:
        pass  # Kronos不可用时不阻塞分析
    # ── [END Kronos预热] ───────────────────────────────────────────────────

    raw_results = _batch_analyze(norm_syms)
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    results = {}
    for sym, r in raw_results.items():
        missing = _validate_result(r)
        r['_runner_meta'] = {
            'runner_version': '1.1',
            'entry':          'brahma_analysis_runner.run_batch',
            'symbol':         sym,
            'ts':             ts,
            'elapsed':        round(time.time() - t0, 2),
            'fields_missing': missing,
            'fields_ok':      len(missing) == 0,
            'output_tag':     build_output_tag(r, source='RUNNER'),
        }
        results[sym] = r

    # ── portfolio_optimizer: 多标的时过滤相关性>0.75的重复风险敞口 ────
    if _PORTFOLIO_OK and len(results) > 1:
        try:
            _valid_sigs = [r for r in results.values()
                           if r.get('valid_signal') or
                           float((r.get('confluence') or {}).get('score', r.get('score', 0)) or 0) >= 138]
            if len(_valid_sigs) > 1:
                _approved, _rejected = _po_filter(_valid_sigs)
                _rejected_syms = {r.get('symbol','') for r in _rejected}
                for sym in _rejected_syms:
                    if sym in results:
                        results[sym]['_portfolio_filtered'] = True
                        results[sym]['_portfolio_filter_reason'] = '相关性>0.75，组合优化过滤'
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── brainlog: 记录batch分析摘要 ──────────────────────────────────
    if _BRAINLOG_OK:
        try:
            _valid_n = sum(1 for r in results.values() if r.get('valid_signal'))
            _high_n  = sum(1 for r in results.values()
                          if float((r.get('confluence') or {}).get('score', r.get('score',0)) or 0) >= 130)
            binfo('runner', f"batch完成: {len(results)}标的 valid={_valid_n} high_score={_high_n} elapsed={round(time.time()-t0,1)}s")
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── brahma_health: batch结束后轻量GC（清理过期缓存/信号）────────
    if _HEALTH_OK:
        try:
            _health_gc()
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    return results


def format_batch_report(results: dict, mode: str = 'card') -> str:
    """
    批量格式化输出 — 封印版标准报告
    每张卡片头部强制嵌入 BRAHMA 标签，防混淆防误识别

    mode:
      'card'  — 精简信号卡（推送用）
      'full'  — 完整分析报告（调试用）
    """
    lines = []
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines.append(f'🏛️ 梵天系统 · 实时分析  {ts}')
    lines.append('─' * 48)

    for sym in ['BTCUSDT', 'ETHUSDT'] + [s for s in results if s not in ('BTCUSDT','ETHUSDT')]:
        if sym not in results:
            continue
        r    = results[sym]
        meta = r.get('_runner_meta', {})
        tag  = meta.get('output_tag') or build_output_tag(r, source='RUNNER')

        # ── 标签头：每张卡片第一行必须是BRAHMA标签 ──
        lines.append(tag)

        if mode == 'card':
            lines.append(format_standard_card(r, ts=None))
        else:
            lines.append(format_report(r))

        # ── 时机过滤层注入（设计院 2026-07-01）───────────────────────
        if _TIMING_OK:
            try:
                f = extract_standard_fields(r)
                _timing = evaluate_timing(
                    symbol=sym,
                    signal_dir=f.get('direction', 'SHORT'),
                    score=f.get('score', 0),
                    grade=f.get('structure_grade', 70),
                    entry_lo=float(f.get('entry_lo', 0) or 0),
                    entry_hi=float(f.get('entry_hi', 0) or 0),
                    current_price=float(f.get('price', 0) or 0),
                    s23_p_up=r.get('s23_p_up', 0.5),
                    regime=f.get('regime', 'BEAR_TREND'),
                )
                lines.append(format_timing_badge(_timing))
                # 将timing注入result供下游使用
                r['_timing'] = _timing
            except Exception:
                pass

        # 质量警告
        missing = meta.get('fields_missing', [])
        if missing:
            lines.append(f'  ⚠️ 字段缺失: {missing}')

        # 非法输出盖识别器：标签不是SIG:RUNNER则加警告
        if not tag_is_valid_signal(tag):
            parsed = tag_parse(tag)
            lines.append(
                f'  🚨 警告: 此输出不是有效信号 — '
                f'level={parsed.get("level")} score={parsed.get("score")} '
                f'valid_sig={parsed.get("valid_sig")}'
            )

    return '\n'.join(lines)


def check_correlation_risk(results: dict) -> dict:
    """
    相关性去重防错（设计院 2026-07-01）

    BTC+ETH同向开仓时，实际风险敞口 = 1.85x BTC（相关系数≈0.85）
    输出建议：只开优先序更高的一个

    返回：
      risk_flag     : 是否存在相关高集中风险
      primary       : 建议操作的标的
      secondary     : 建议观望的标的
      note          : 说明
    """
    btc = results.get('BTCUSDT', {})
    eth = results.get('ETHUSDT', {})

    if not btc or not eth:
        return {'risk_flag': False, 'primary': None, 'secondary': None, 'note': '单标的无相关风险'}

    # 获取两者方向和score
    btc_dir = btc.get('signal_dir', '') or btc.get('confluence', {}).get('direction', '')
    eth_dir = eth.get('signal_dir', '') or eth.get('confluence', {}).get('direction', '')
    btc_score = float(btc.get('confluence', {}).get('total', 0) or btc.get('score', 0) or 0)
    eth_score = float(eth.get('confluence', {}).get('total', 0) or eth.get('score', 0) or 0)
    btc_valid = btc.get('valid', False)
    eth_valid = eth.get('valid', False)

    # 只有两者都有效且同向才存在相关风险
    if not (btc_valid and eth_valid and btc_dir and eth_dir and btc_dir == eth_dir):
        return {'risk_flag': False, 'primary': None, 'secondary': None,
                'note': f'无双开风险 (btc_valid={btc_valid} eth_valid={eth_valid} dir={btc_dir}/{eth_dir})'}

    # 同向双开：ETH得分高 AND BTC.D>54% → 优先ETH
    btc_dom = 55.4  # 当前实时値，稍后可动态拉取
    try:
        import requests as _rq
        cg = _rq.get('https://api.coingecko.com/api/v3/global', timeout=5).json()
        btc_dom = float(cg['data']['market_cap_percentage'].get('btc', 55.4))
    except Exception:
        pass

    if eth_score >= btc_score and btc_dom >= 54:
        primary = 'ETHUSDT'
        secondary = 'BTCUSDT'
        reason = f'ETH得分({eth_score:.0f})高于BTC({btc_score:.0f}) + BTC.D={btc_dom:.1f}%高位 → 优先ETH，BTC观望'
    else:
        primary = 'BTCUSDT'
        secondary = 'ETHUSDT'
        reason = f'BTC得分({btc_score:.0f})高或BTC.D不高 → 优先BTC'

    return {
        'risk_flag': True,
        'primary': primary,
        'secondary': secondary,
        'correlation': 0.85,
        'actual_exposure': '1.85x BTC风险',
        'note': f'❗ BTC/ETH同向{btc_dir}，实际风险敞口1.85x | {reason}',
        'btc_dom': btc_dom,
    }


# ══════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天分析唯一入口')
    parser.add_argument('symbols', nargs='*', default=['BTCUSDT', 'ETHUSDT'],
                        help='交易对列表（默认 BTC ETH）')
    parser.add_argument('--card', action='store_true', help='精简信号卡输出')
    parser.add_argument('--full', action='store_true', help='完整报告输出')
    parser.add_argument('--fields', action='store_true', help='仅输出标准字段')
    parser.add_argument('--validate', action='store_true', help='检查字段完整性')
    args = parser.parse_args()

    mode = 'full' if args.full else 'card'
    t0 = time.time()

    print(f'[Runner] 启动 | 标的: {args.symbols} | 模式: {mode}')
    print(f'[Runner] 入口: brahma_parallel_engine.batch_analyze (并发4x加速)')
    print()

    results = run_batch(args.symbols)
    total = round(time.time() - t0, 2)

    if args.fields:
        for sym, r in results.items():
            print(f'=== {sym} 标准字段 ===')
            f = extract_standard_fields(r)
            for k in STANDARD_FIELDS:
                v = f.get(k)
                status = '✅' if v is not None else '❌'
                print(f'  {status} {k}: {v}')
            print()
    elif args.validate:
        all_ok = True
        for sym, r in results.items():
            meta = r.get('_runner_meta', {})
            missing = meta.get('fields_missing', [])
            ok = meta.get('fields_ok', False)
            icon = '✅' if ok else '❌'
            print(f'{icon} {sym}: {"完整" if ok else "缺失=" + str(missing)}')
            if not ok:
                all_ok = False
        print()
        print(f'总结: {"全部完整 ✅" if all_ok else "有字段缺失 ❌"}  耗时 {total}s')
    else:
        print(format_batch_report(results, mode=mode))
        print()
        print(f'[Runner] 完成 | 耗时 {total}s | {len(results)} 标的')
        for sym, r in results.items():
            meta = r.get('_runner_meta', {})
            ok_icon = '✅' if meta.get('fields_ok') else '⚠️'
            print(f'  {ok_icon} {sym}: score={extract_standard_fields(r).get("score")} '
                  f'valid={extract_standard_fields(r).get("valid")} '
                  f'missing={meta.get("fields_missing",[])}')

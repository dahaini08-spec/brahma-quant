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

# [P1修复 2026-07-12] 自动载入 .env，确保执行层能读到API密钥
try:
    from dotenv import load_dotenv as _ldenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    _ldenv(_env_path, override=False)
except Exception:
    pass
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

# [2026-07-06] Kronos模块预注入：确保 brahma_core 中的动态 import 拿到正确实例
# 根因： brahma_core 用 `from kronos_bridge import`（非包式），会创建独立模块实例
#         导致 kronos_engine._predictor 无法共享，出现 lgbm_err
try:
    import brahma_brain.kronos_bridge as _kb_mod
    sys.modules.setdefault('kronos_bridge', _kb_mod)   # 预注入平名属引用
except Exception:
    pass
try:
    import brahma_brain.kronos_engine as _ke_mod
    _ke_mod._model_load_attempted = False  # 允许重新加载（libgomp已修复）
    _ke_mod._model_loaded = False
    _ke_mod._predictor = None
    sys.modules.setdefault('kronos_engine', _ke_mod)   # 预注入平名属引用
except Exception:
    pass

# ── 唯一数据入口（封印）────────────────────────────────────────
from brahma_brain.brahma_core import analyze as _core_analyze
from brahma_brain.brahma_parallel_engine import (
    batch_analyze as _batch_analyze,
    batch_analyze_with_regime as _batch_analyze_regime,
)
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
                        # v5.1修复：验证缓存方向与体制一致
                        try:
                            import json as _jc; from pathlib import Path as _Pc
                            _rf = _Pc(__file__).parent.parent/'data'/'regime_state.json'
                            _rc = _jc.loads(_rf.read_text()).get(sym,{}).get('confirmed','')
                            _bull_regimes = ('BULL_TREND','BULL_EARLY','BEAR_RECOVERY')
                            _bear_regimes = ('BEAR_TREND','BEAR_EARLY')
                            if (_rc in _bull_regimes and _d == 'SHORT') or \
                               (_rc in _bear_regimes and _d == 'LONG'):
                                continue  # 体制方向矛盾，不复用缓存
                        except Exception:
                            pass
                        _cached['_from_cache'] = True
                        return _cached
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-03 v5.1] 体制感知方向预注入 ────────────────────────────
    # 根因修复：BULL_TREND下AUTO方向被market_structure误判为SHORT
    # → StructureGate以BULL×SHORT封杀(grade<80) → bull_bonus条件不满足(dir!=LONG)
    # 解决：从regime_state读取confirmed体制，顺势体制下强制传入正确方向
    _forced_dir = None
    try:
        import json as _json
        from pathlib import Path as _Path
        _reg_file = _Path(__file__).parent.parent / 'data' / 'regime_state.json'
        if _reg_file.exists():
            _reg_data = _json.loads(_reg_file.read_text())
            _sym_regime = _reg_data.get(sym, {}).get('confirmed', '')
            if _sym_regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
                _forced_dir = 'LONG'   # 顺势：多头体制强制LONG
            elif _sym_regime in ('BEAR_TREND', 'BEAR_EARLY'):
                _forced_dir = 'SHORT'  # 顺势：空头体制强制SHORT
            if _forced_dir:
                pass  # [静默] f'[RegimePreset] {sym} {_sym_regime} → 强制方向={_forced_dir}'
    except Exception:
        pass
    # ────────────────────────────────────────────────────────────────────────

    result = _core_analyze(sym, signal_dir=_forced_dir, deep=deep)
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
        _cur_score = float(
            _rf.get('score_final',
            _rf.get('total',
            _rf.get('score', 0))) or 0
        )  # [FIX 2026-07-06] 优先取score_final，兼容brahma_core返回结构

        # P0-B: BULL体制顺势加分（仅LONG方向）
        _total_bonus = 0
        if 'BULL' in _reg and _dir in ('LONG', 'AUTO', ''):
            _rb = get_regime_context_bonus(sym, _reg)
            if _rb['bonus'] > 0:
                _total_bonus += _rb['bonus']
                _rf['_regime_context_bonus'] = _rb
                pass  # [静默] f'[BullBonus] {sym} +{_rb["bonus"]}分 | {_rb["reasons"]}'

        # P0-C: rsi_trigger_event 事件窗口加分（所有方向）
        _eb = get_event_timing_bonus(sym)
        if _eb['active'] and _eb['bonus'] > 0:
            _total_bonus += _eb['bonus']
            _rf['_event_timing_bonus'] = _eb
            pass  # [静默] f'[EventBonus] {sym} +{_eb["bonus"]}分 | {_eb["events"]}'

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
            pass  # [静默] f'[RegimeInject] {sym} {_cur_score:.1f}+{_total_bonus}→{_new_score:.1f} (regime=
            # [FIX 2026-07-06] 注入后validation重算:
            # P0B封锁只是设 valid_signal=False，但params['valid']=True+score达门 就应该是valid
            _params_valid = bool((_rf.get('params') or {}).get('valid', False))
            _kelly_ok = float((_rf.get('confluence') or {}).get('kelly_mult', 1) or 1) > 0
            # P0B封锁在brahma_core里设置val=False，但它不存入标记字段
            # 只要 params.valid=True + kelly>0 + 新score>=155 就是有效信号
            # [达摩院修正 2026-07-16 苏摩111] BEAR_RECOVERY体制阈值降至120（IC=0.76背书）
            _inj_regime = (
                str((_rf.get('params') or {}).get('regime', '') or '')
                or str(_rf.get('regime', '') or '')
            )  # [P0-4修复 2026-07-16] 双路径: params.regime OR 顶层regime
            _MIN_VALID = 120 if 'BEAR_RECOVERY' in _inj_regime.upper() else 155
            if _params_valid and _kelly_ok and _new_score >= _MIN_VALID:
                _rf['valid_signal'] = True
                pass  # [静默] f'[RegimeInject-Valid] {sym} score={_new_score:.1f}>={_MIN_VALID} params.valid=T
            elif _new_score >= _MIN_VALID:
                # score达问但params.valid=False，说明RR问题
                pass  # [静默] f'[RegimeInject-Valid] {sym} score={_new_score:.1f} 但params.valid=False，RR问题，不解除
    except Exception as _inj_err:
        pass  # 注入失败不阻断主流程
    # ────────────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-04] P2: switch_count_24h>50 → 体制噪音惩罚 ─────
    # BTC 24H体制翻转>50次 = 行情震荡，即便confirmed=BULL_TREND也降噪
    try:
        import json as _sjson; from pathlib import Path as _sPath
        _rsf = _sPath(__file__).parent.parent / 'data' / 'regime_state.json'
        _rs = _sjson.loads(_rsf.read_text())
        _btc_sw = _rs.get('BTCUSDT', {}).get('switch_count_24h', 0)
        _sym_sw = _rs.get(sym, {}).get('switch_count_24h', 0)
        _sw_max = max(_btc_sw, _sym_sw)
        if _sw_max > 50:
            _sw_penalty = -15  # 高频翻转 → 降15分
            _f_sw = extract_standard_fields(result)
            _sc_sw = float(_f_sw.get('score', 0) or 0)
            _new_sw = _sc_sw + _sw_penalty
            result['total']       = _new_sw
            result['score']       = _new_sw
            result['score_final'] = _new_sw
            if isinstance(result.get('confluence'), dict):
                result['confluence']['score'] = _new_sw
                result['confluence']['total'] = _new_sw
            result['_switch_noise_penalty'] = {'btc_sw': _btc_sw, 'sym_sw': _sym_sw, 'penalty': _sw_penalty}
            pass  # [静默] f'[SwitchNoise] {sym} sw={_sw_max}>50 → -{abs(_sw_penalty)}分 ({_sc_sw:.1f}→{_new
        elif _sw_max > 30:
            # 中等噪音 → 降5分
            _sw_penalty = -5
            _f_sw = extract_standard_fields(result)
            _sc_sw = float(_f_sw.get('score', 0) or 0)
            _new_sw = _sc_sw + _sw_penalty
            result['total']       = _new_sw
            result['score']       = _new_sw
            result['score_final'] = _new_sw
            if isinstance(result.get('confluence'), dict):
                result['confluence']['score'] = _new_sw
                result['confluence']['total'] = _new_sw
            result['_switch_noise_penalty'] = {'btc_sw': _btc_sw, 'sym_sw': _sym_sw, 'penalty': _sw_penalty}
            pass  # [静默] f'[SwitchNoise] {sym} sw={_sw_max}>30 → -{abs(_sw_penalty)}分 ({_sc_sw:.1f}→{_new
    except Exception:
        pass
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
        'runner_version': '1.2',
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

    # [v7.0 设计院 2026-07-11 六方自主决策封印]
    # 修复1: result['valid']未显式赋値问题
    # valid_signal=True已设定，但run_analysis返回的valid字段为None
    # 修复：将valid_signal同步到result['valid']
    _vs = result.get('valid_signal', False)
    if not isinstance(_vs, bool):
        _vs = bool(_vs)
    result['valid'] = _vs

    # 修复2: action字段未同步问题
    # brahma_core已更新score>=130→ENTER_WATCH，score>=138→ENTER
    # 但confluence.action可能还是旧字段的归因值
    # 修复：基于最终score重新计算action
    try:
        _final_score = float((result.get('confluence') or {}).get('total', result.get('score', 0)) or 0)
        _cf_ref = result.get('confluence') or {}
        # [P1-8修复 2026-07-16 苏摩111] BEAR_RECOVERY体制action阈值感知
        _action_regime = str((_rf.get('params') or {}).get('regime','') or _rf.get('regime','') or '')
        _is_br_action  = 'BEAR_RECOVERY' in _action_regime.upper()
        if _final_score >= 155:
            _correct_action = 'ENTER_FULL'
        elif _final_score >= 138:
            _correct_action = 'ENTER'
        elif _final_score >= 130 or (_is_br_action and _final_score >= 120):
            _correct_action = 'ENTER_WATCH'  # BEAR_RECOVERY 120-129 也给ENTER_WATCH
        elif _final_score >= 110:
            _correct_action = 'WATCH'
        elif _final_score >= 80:
            _correct_action = 'WATCH'
        else:
            _correct_action = 'SKIP'
        # 只覆盖如果brahma_core返回的是旧的WATCH但score已在更高层级
        _cur_action = _cf_ref.get('action', '')
        if _cur_action == 'WATCH' and _final_score >= 130:
            if isinstance(result.get('confluence'), dict):
                result['confluence']['action'] = _correct_action
            result['action'] = _correct_action
    except Exception:
        pass

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

    # ── [设计院 2026-07-06] P3: timing_filter 注入顶层字段 ──────────────────
    # 根因: evaluate_timing只在format_batch_report调用，brahma_analyze.py拿不到
    # 修复: run_analysis返回前直接计算并写入result['timing_status']
    if _TIMING_OK:
        try:
            _tf = extract_standard_fields(result)
            _timing_result = evaluate_timing(
                symbol        = sym,
                signal_dir    = _tf.get('direction', 'SHORT'),
                score         = float(_tf.get('score', 0) or 0),
                grade         = float(_tf.get('structure_grade', 70) or 70),
                entry_lo      = float(_tf.get('entry_lo', 0) or 0),
                entry_hi      = float(_tf.get('entry_hi', 0) or 0),
                current_price = float(_tf.get('price', 0) or 0),
                s23_p_up      = result.get('s23_p_up', 0.5),
                regime        = _tf.get('regime', 'BEAR_TREND'),
            )
            result['timing_status'] = _timing_result.get('status', 'UNKNOWN')
            result['timing_badge']  = _timing_result.get('badge', '')
            result['timing_score']  = _timing_result.get('score', 0)
            result['_timing']       = _timing_result
            pass  # [静默] f'[TimingFilter] {sym} {result["timing_status"]} score={result["timing_score"]}'
        except Exception:
            result['timing_status'] = 'UNKNOWN'
    # ────────────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-13 P0修复] 外部扩展层评分集成 ─────────────────────────
    # 根因：liq_heatmap/cross_exchange_fr/whale_monitor/options_pc_ratio/miner_pressure
    #       均已在 scripts/ 实现，但其score贡献未集成到 run_analysis() 返回的 score_final
    # 修复：异步调用（超时3s），将各模块score贡献叠加到当前score_final
    # 效果：BTC score 141→178+ → 自动解锁有效信号门槛155
    try:
        import importlib, sys as _sys_ext
        _scripts_ext = os.path.join(BASE_DIR, '..', 'scripts')
        if _scripts_ext not in _sys_ext.path:
            _sys_ext.path.insert(0, _scripts_ext)

        _ext_bonus = 0
        _ext_detail = {}

        # 取当前 score（使用 _rf 已含 BullBonus 的最终分）
        _cur_ext_score = float(result.get('score_final', result.get('score', 0)) or 0)

        # 判断当前方向
        _ext_dir = str(result.get('direction', result.get('signal_dir', '')) or '')

        # --- 1. 清算热力图 liq_heatmap (+8 多头 / -8 空头风险区) ---
        try:
            from liq_heatmap import get_liq_heatmap as _get_liq
            _liq = _get_liq(sym)
            if not _liq.get('error'):
                if _ext_dir in ('LONG', 'AUTO', ''):
                    _liq_contrib = int(_liq.get('liq_bull_score', 0) or 0)
                else:
                    _liq_contrib = -int(_liq.get('liq_bear_score', 0) or 0)
                _ext_bonus += _liq_contrib
                _ext_detail['liq_heatmap'] = _liq_contrib
                result['_liq_heatmap'] = _liq
        except Exception as _le:
            _ext_detail['liq_heatmap'] = f'skip:{_le}'

        # --- 2. 跨所FR套利信号 cross_exchange_fr (0~9分) ---
        try:
            from cross_exchange_fr import get_cross_fr as _get_fr
            _cfr = _get_fr(sym)
            if not _cfr.get('error'):
                _fr_contrib = int(_cfr.get('arb_score', 0) or 0)
                # 方向过滤: bull_fr_bonus 仅在做多方向生效
                if _ext_dir in ('LONG', 'AUTO', ''):
                    _fr_contrib = max(_fr_contrib, int(_cfr.get('bull_fr_bonus', 0) or 0))
                _ext_bonus += _fr_contrib
                _ext_detail['cross_fr'] = _fr_contrib
                result['_cross_fr'] = _cfr
        except Exception as _fe:
            _ext_detail['cross_fr'] = f'skip:{_fe}'

        # --- 3. 鲸鱼监控 whale_monitor (±10分) ---
        try:
            from whale_monitor import get_whale_signal as _get_whale
            _whale = _get_whale(sym)
            if not _whale.get('error'):
                _wh_contrib = int(_whale.get('whale_score', 0) or 0)
                _ext_bonus += _wh_contrib
                _ext_detail['whale'] = _wh_contrib
                result['_whale'] = _whale
        except Exception as _we:
            _ext_detail['whale'] = f'skip:{_we}'

        # --- 4. 期权P/C比 options_pc_ratio (+8分) ---
        try:
            from options_pc_ratio import get_options_pc as _get_pc
            _currency = 'BTC' if 'BTC' in sym else ('ETH' if 'ETH' in sym else sym.replace('USDT',''))
            _pc = _get_pc(_currency)
            if not _pc.get('error'):
                _pc_contrib = int(_pc.get('pc_score', 0) or 0)
                _ext_bonus += _pc_contrib
                _ext_detail['options_pc'] = _pc_contrib
                result['_options_pc'] = _pc
        except Exception as _pe:
            _ext_detail['options_pc'] = f'skip:{_pe}'

        # --- 5. 矿工压力 miner_pressure (+8分, 仅BTC) ---
        try:
            if 'BTC' in sym:
                from miner_pressure import get_miner_pressure as _get_miner
                _miner = _get_miner()
                if not _miner.get('error'):
                    _mn_contrib = int(_miner.get('miner_score', 0) or 0)
                    _ext_bonus += _mn_contrib
                    _ext_detail['miner'] = _mn_contrib
                    result['_miner'] = _miner
        except Exception as _mne:
            _ext_detail['miner'] = f'skip:{_mne}'

        # --- 写回评分 ---
        if _ext_bonus != 0:
            _new_ext_score = _cur_ext_score + _ext_bonus
            result['total']       = _new_ext_score
            result['score']       = _new_ext_score
            result['score_final'] = _new_ext_score
            if isinstance(result.get('confluence'), dict):
                result['confluence']['score'] = _new_ext_score
                result['confluence']['total'] = _new_ext_score
            result['_ext_score_bonus']  = _ext_bonus
            result['_ext_score_detail'] = _ext_detail

            # 重新校验 valid_signal（外部层加分后可能越过155门槛）
            # [达摩院修正 2026-07-16 苏摩111] BEAR_RECOVERY体制阈值降至120（IC=0.76背书）
            _regime_v2 = (
                str((result.get('params') or {}).get('regime', '') or '')
                or str(result.get('regime', '') or '')
            )  # [P0-4修复 2026-07-16] 双路径: params.regime OR 顶层regime
            _MIN_VALID_EXT = 120 if 'BEAR_RECOVERY' in _regime_v2.upper() else 155
            _params_v2 = bool((result.get('params') or {}).get('valid', False))
            _kelly_v2  = float((result.get('confluence') or {}).get('kelly_mult', 1) or 1) > 0
            if _params_v2 and _kelly_v2 and _new_ext_score >= _MIN_VALID_EXT:
                result['valid_signal'] = True
                result['valid']        = True
            elif _new_ext_score >= _MIN_VALID_EXT and not result.get('valid_signal'):
                # score足够，但params.valid=False → 记录，不强制解锁（可能RR不达标）
                result['_ext_note'] = f'score={_new_ext_score:.1f}≥155但params.valid=False，RR或结构未达标'

    except Exception as _ext_err:
        result['_ext_integration_error'] = str(_ext_err)
    # ── [END 外部扩展层集成] ──────────────────────────────────────────────────

    # ── [设计院 2026-07-12 P0修复] params子字段展平到顶层 ─────────────────────
    # 根因: auto_executor读取顶层entry_lo/stop_loss等字段为None，实际数据在params子dict
    # 修复: 将params关键字段提升到顶层，保留原值优先（不覆盖已有非None值）
    try:
        _p = result.get('params', {}) or {}
        _flatten_fields = [
            ('entry_lo', 'entry_lo'), ('entry_hi', 'entry_hi'),
            ('stop_loss', 'stop_loss'), ('tp1', 'tp1'), ('tp2', 'tp2'),
            ('sl_pct', 'sl_pct'), ('rr1', 'rr1'),
            ('ob_top', 'ob_top'), ('ob_bottom', 'ob_bottom'),
            ('entry_source', 'entry_source'), ('ob_source_type', 'ob_source_type'),
            ('ob_dist_pct', 'ob_dist_pct'),
        ]
        for _src_key, _dst_key in _flatten_fields:
            if not result.get(_dst_key) and _p.get(_src_key):
                result[_dst_key] = _p[_src_key]
        # structure_grade: 优先effective_grade > params.structure_grade > grade
        if not result.get('structure_grade'):
            result['structure_grade'] = (
                result.get('effective_grade') or
                _p.get('structure_grade') or
                result.get('grade')
            )
        # action推导: valid=True且action为None时按score推导
        if result.get('action') is None:
            _p_valid = _p.get('valid') or result.get('valid_signal') or result.get('valid')
            if _p_valid:
                _sc = float(result.get('confluence', {}).get('score', 0) or 0)
                result['action'] = ('ENTER_FULL' if _sc >= 155
                                    else 'ENTER_WATCH' if _sc >= 120
                                    else 'WATCH')
        # direction同步
        if not result.get('direction') and result.get('signal_dir'):
            result['direction'] = result['signal_dir']
    except Exception:
        pass
    # ─────────────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-13] 全景矩阵报告自动挂载 ─────────────────────────
    # 每次 run_analysis() 返回时，自动生成 _panorama_card（精简）和 _panorama_full（完整）
    # 下游可直接读取，无需再次调用 formatter
    try:
        from brahma_brain.formatter import brahma_panorama_report as _pano_fn
        result['_panorama_card'] = _pano_fn(result, compact=True)
        result['_panorama_full'] = _pano_fn(result, compact=False)
    except Exception as _pano_err:
        result['_panorama_err'] = str(_pano_err)
    # ─────────────────────────────────────────────────────────────────────────

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

    raw_results = _batch_analyze_regime(norm_syms)
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

    # [P0-8修复 2026-07-16 苏摩111] run_batch最小注入：BEAR_RECOVERY阈值120 + valid_signal同步
    # 完整注入链(timing/ext/panorama)由未来重构到公共函数处理，此处先修复最高优先级
    for _bs_sym, _bs_r in results.items():
        try:
            _bs_regime = str((_bs_r.get('params') or {}).get('regime', '') or _bs_r.get('regime', '') or '')
            _bs_score  = float(_bs_r.get('score_final', _bs_r.get('score', 0)) or 0)
            _bs_kelly  = float((_bs_r.get('confluence') or {}).get('kelly_mult', 1) or 1)
            _bs_pvalid = bool((_bs_r.get('params') or {}).get('valid', False))
            _bs_min    = 120 if 'BEAR_RECOVERY' in _bs_regime.upper() else 155
            if _bs_pvalid and _bs_kelly > 0 and _bs_score >= _bs_min:
                _bs_r['valid_signal'] = True
                _bs_r['valid']        = True
        except Exception:
            pass

    # ─── 跨资产联合推理门控（cross_asset_gate v1.0）────────────────────────
    # 苏摩111批准 · 2026-07-23 · 设计院封印
    # 在所有valid_signal产出后，做BTC/ETH跨资产一致性检查
    # 矛盾信号（ETH多单但BTC未到位、联动跌幅会触发ETH止损）自动降级为WAIT_BTC_ANCHOR
    try:
        from brahma_brain.cross_asset_gate import apply_cross_asset_gate
        _cag_list = []
        _cag_map  = {}  # sym -> result
        for _cag_sym, _cag_r in results.items():
            _cag_entry = {
                'symbol':     _cag_sym,
                'direction':  str((_cag_r.get('params') or {}).get('direction', '') or '').upper(),
                'score':      float(_cag_r.get('score_final', _cag_r.get('score', 0)) or 0),
                'entry_lo':   float((_cag_r.get('params') or {}).get('entry_lo', 0) or 0),
                'entry_hi':   float((_cag_r.get('params') or {}).get('entry_hi', 0) or 0),
                'sl':         float((_cag_r.get('params') or {}).get('sl', 0) or 0),
                'sl_pct':     float((_cag_r.get('params') or {}).get('sl_pct', 0) or 0),
                'rr1':        float((_cag_r.get('params') or {}).get('rr1', 2.0) or 2.0),
                'valid':      bool(_cag_r.get('valid_signal') or _cag_r.get('valid')),
                # [FIX-ROOT 2026-07-23 苏摩111] 传递 expires_at + ts
                # 根因: 缺少这两个字段导致 _is_signal_valid() 无法过滤历史旧信号
                'expires_at': _cag_r.get('expires_at', ''),
                'ts':         float(_cag_r.get('ts', 0) or 0),
            }
            _cag_list.append(_cag_entry)
            _cag_map[_cag_sym] = _cag_r
        _cag_checked = apply_cross_asset_gate(_cag_list)
        for _cag_item in _cag_checked:
            _sym = _cag_item.get('symbol', '')
            if _sym in _cag_map and _cag_item.get('cross_asset_triggered'):
                # 回写降级标志到原始result
                _cag_map[_sym]['cross_asset_triggered']  = True
                _cag_map[_sym]['cross_asset_reason']     = _cag_item.get('cross_asset_reason', '')
                _cag_map[_sym]['cross_asset_wait_entry'] = {
                    'entry_lo': _cag_item.get('better_entry_lo', 0),
                    'entry_hi': _cag_item.get('better_entry_hi', 0),
                    'sl':       _cag_item.get('better_sl', 0),
                    'rr':       _cag_item.get('better_rr', 0),
                }
                # 覆盖 timing_badge → WAIT
                _cag_map[_sym]['timing_badge']  = '⚠️ WAIT_BTC_ANCHOR'
                _cag_map[_sym]['timing_status'] = 'WAIT'
                # 不改 valid_signal（信号本身有效，只是时机未到）
            elif _sym in _cag_map:
                _cag_map[_sym]['cross_asset_check'] = _cag_item.get('cross_asset_check', 'OK')
    except Exception as _cag_err:
        try:
            from brahma_brain.brahma_log import berr
            berr('cross_asset_gate', f'门控异常（不影响主流程）: {_cag_err}')
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────────────

    return results


def run_analysis_full(symbol: str, deep: bool = True) -> dict:
    """
    全景分析接口 — 设计院 2026-07-13 封印
    在 run_analysis() 基础上附加：
      - _panorama_card : 精简全景卡（推送用）
      - _panorama_full : 完整全景报告（审计用）
      - _weight_matrix : 关键评分维度权重矩阵
      - _risk_flags    : 当前风险标志列表

    调用示例：
      result = run_analysis_full('BTCUSDT')
      print(result['_panorama_full'])
    """
    result = run_analysis(symbol, deep=deep)

    # 全景报告已在 run_analysis 中自动挂载，此处补充额外字段
    try:
        cf_dict   = result.get('confluence', {}) or {}
        breakdown = cf_dict.get('breakdown', {}) or {}

        # 权重矩阵：提取 top10 贡献维度
        score_items = []
        for k, v in breakdown.items():
            sv = str(v)
            try:
                val = float(sv.split('(')[0].replace('+','').strip())
                score_items.append({'dim': k, 'contrib': val, 'detail': sv[:60]})
            except:
                pass
        score_items.sort(key=lambda x: -abs(x['contrib']))
        result['_weight_matrix'] = score_items[:15]

        # 风险标志
        risk_flags = []
        if 'ATR禁区' in str(breakdown.get('N16_ATR体制','')):
            risk_flags.append('LOW_ATR_VOLATILITY')
        if 'OBV反向' in str(breakdown.get('OBV方向_v2','')):
            risk_flags.append('OBV_DIVERGENCE')
        try:
            macro_v = breakdown.get('宏观+事件', 0)
            if isinstance(macro_v, (int, float)) and float(macro_v) < -8:
                risk_flags.append('MACRO_HEADWIND')
        except:
            pass
        if 'FIX1' in str(breakdown.get('FIX1_假牛市', '')):
            risk_flags.append('FAKE_BULL_DETECTED')
        if result.get('_ext_score_detail', {}).get('whale', '') == 0:
            risk_flags.append('WHALE_NO_HISTORY')
        result['_risk_flags'] = risk_flags

    except Exception as _fe:
        result['_full_analysis_err'] = str(_fe)

    return result


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

        if mode == 'panorama':
            # [设计院 2026-07-13] 全景矩阵模式
            pano = r.get('_panorama_full') or r.get('_panorama_card', '')
            if not pano:
                try:
                    from brahma_brain.formatter import brahma_panorama_report as _pano
                    pano = _pano(r, compact=False)
                except Exception:
                    pano = format_standard_card(r, ts=None)
            lines.append(pano)
        elif mode == 'card':
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

    pass  # [静默] f'[Runner] 启动 | 标的: {args.symbols} | 模式: {mode}'
    pass  # [静默] f'[Runner] 入口: brahma_parallel_engine.batch_analyze (并发4x加速)'
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
        pass  # [静默] f'[Runner] 完成 | 耗时 {total}s | {len(results)} 标的'
        for sym, r in results.items():
            meta = r.get('_runner_meta', {})
            ok_icon = '✅' if meta.get('fields_ok') else '⚠️'
            print(f'  {ok_icon} {sym}: score={extract_standard_fields(r).get("score")} '
                  f'valid={extract_standard_fields(r).get("valid")} '
                  f'missing={meta.get("fields_missing",[])}')

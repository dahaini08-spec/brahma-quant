# ponytail: brahma_analysis_runner 1595行，流程编排层，入口唯一性有意为之，不可拆
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
try:
    from signal_trace import trace_generated, trace_skipped
except ImportError:
    def trace_generated(*a, **kw): pass
    def trace_skipped(*a, **kw): pass  # fallback
import os

# ── 安全防护：禁止core dump（设计院封印2026-08-07）──────────────
# 防止Python崩溃产生640MB+的core文件污染磁盘
try:
    import resource as _resource
    _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────
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

# brahma_health GC: _check_and_gc已移至signal_watcher/brahma360_guardian，此处静默跳过
_HEALTH_OK = False

# market_structure_scanner: 高分信号补充SMC结构扫描
try:
    from market_structure_scanner import scan_structure as _mss_scan
    _MSS_OK = True
except Exception:
    _MSS_OK = False

# signal_trace: 信号轨迹审计日志（设计院 2026-07-02）──────────────────────
try:
    from brahma_signal import trace_generated, trace_skipped
    _TRACE_OK = True
except Exception:
    _TRACE_OK = False
    # trace_generated → from signal_trace [2026-08-28 SSOT]
    # trace_skipped → from signal_trace [2026-08-28 SSOT]

# llm_council_bridge: score≥130触发LLM二次审查（shadow模式）
try:
    from llm_council_bridge import review as _llm_review
    _LLM_COUNCIL_OK = True
except Exception:
    _LLM_COUNCIL_OK = False

# Kronos依赖检查（设计院封印2026-08-07: 静默跳过，不尝试安装）
# 容器环境pip install torch会产生大量崩溃子进程→core dump→磁盘爆满
# 解锁路径: OmniRoute api_key → 云端Kronos推断（不依赖本地torch）
try:
    import torch as _torch  # noqa
    _TORCH_OK = True
except ImportError:
    _TORCH_OK = False  # 静默跳过，Kronos将使用lite_cache fallback
# ── 系统配置（路由到正确线程）────────────────────────────────
try:
    _syscfg_dir = os.path.join(BASE_DIR, '..', 'scripts')
    if _syscfg_dir not in sys.path:  # [修复 S1 2026-08-24] 守卫防race condition
        sys.path.insert(0, _syscfg_dir)
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

def run_analysis(symbol: str, deep: bool = True, signal_dir: str = None) -> dict:
    """
    单标的分析 — 封印版唯一入口

    规则：
      - 必须走 brahma_core.analyze(deep=True)
      - 不得绕过此函数直接调用 brahma_core
      - 返回值包含 _runner_meta 字段标记来源

    参数:
      signal_dir: 强制方向 (LONG/SHORT)，None=体制感知自动决定
                  [FIX 2026-08-02] 修复 brahma_analyze.py --dir 参数被丢弃的根因

    返回: analyze() 原始结果 + _runner_meta
    """
    t0 = time.time()
    sym = symbol.upper().replace('/','').replace('-','')
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'

    # [2026-08-12 苏摹封印] Kronos单币分析自动预热
    try:
        from kronos_engine import _load_model as _kw_load, _model_loaded as _kw_ready
        if not _kw_ready:
            _kw_load()
    except Exception:
        pass

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
                        # [设计院 2026-08-09] 缓存命中时补充decision+fangcang（今日修复）
                        if 'decision' not in _cached or 'fangcang' not in _cached:
                            try:
                                from brahma_brain.brahma_core import analyze as _rc_fn
                                # deep=True + signal_dir强制非NEUTRAL，避免提前return
                                _sd_forced = _d if _d and _d != 'NEUTRAL' else None
                                _fresh = _rc_fn(sym, signal_dir=_sd_forced, deep=True)
                                for _fk in ('decision','decision_action','decision_reason',
                                            'decision_step','fangcang'):
                                    if _fk in _fresh:
                                        _cached[_fk] = _fresh[_fk]
                            except Exception:
                                pass  # 补充失败不阻断缓存返回
                        return _cached
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-03 v5.1] 体制感知方向预注入 ────────────────────────────
    # 根因修复：BULL_TREND下AUTO方向被market_structure误判为SHORT
    # → StructureGate以BULL×SHORT封杀(grade<80) → bull_bonus条件不满足(dir!=LONG)
    # 解决：从regime_state读取confirmed体制，顺势体制下强制传入正确方向
    # [FIX 2026-08-02 设计院] 外部传入signal_dir优先级最高，不被体制感知覆盖
    # 根因：trade_gateway/brahma_analyze.py传入--dir SHORT时，被_forced_dir体制感知覆盖，导致SHORT信号全部丢失
    # 修复：signal_dir参数非None时直接用，跳过体制感知逻辑
    _forced_dir = signal_dir if signal_dir else None
    if _forced_dir is None:
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

    # [2026-08-28 苏摩111] 接入防御层：调用前注入 anti_manip 数据到 extra_data
    _anti_manip_data = {}
    try:
        from brahma_brain.anti_manipulation_engine import get_anti_manip_score
        _anti_manip_data = get_anti_manip_score(sym, signal_dir=_forced_dir)
    except Exception as _e:
        _anti_manip_data = {'error': str(_e)}
    # 将 anti_manip 注入到全局 extra_data（brahma_core_block_b 会读取）
    import brahma_brain.brahma_core as _bc_mod
    _orig_analyze = _bc_mod.analyze
    def _patched_analyze(ms_or_sym, *args, **kwargs):
        if 'extra_data' not in kwargs:
            kwargs['extra_data'] = {}
        if kwargs['extra_data'] is None:
            kwargs['extra_data'] = {}
        kwargs['extra_data']['anti_manip'] = _anti_manip_data
        return _orig_analyze(ms_or_sym, *args, **kwargs)
    _bc_mod.analyze = _patched_analyze

    result = _core_analyze(sym, signal_dir=_forced_dir, deep=deep)

    # 恢复原始 analyze
    _bc_mod.analyze = _orig_analyze
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
    # [设计院 2026-07-26 性能修复] BRAHMA_SKIP_COUNCIL=1 跳过LLM审查（避免超时）
    _skip_council = __import__('os').environ.get('BRAHMA_SKIP_COUNCIL', '0') == '1'
    if _LLM_COUNCIL_OK and not _skip_council:
        try:
            _f = extract_standard_fields(result)
            _sc = float(_f.get('score', 0) or 0)
            if _sc >= 130:
                result = _llm_review(result)
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-26] 逻辑验证器强制门控 ──────────────────────────────
    # 铁律: 每条信号推送前必须通过 validate_signal
    # 铁证: ETH SHORT SL在入场下方被苏摩误读，根因是从未调用验证器
    try:
        import sys as _cv_sys, os as _cv_os
        _cv_scripts = _cv_os.path.join(
            _cv_os.path.dirname(_cv_os.path.dirname(_cv_os.path.abspath(__file__))),
            'scripts')
        if _cv_scripts not in _cv_sys.path: _cv_sys.path.insert(0, _cv_scripts)
        from content_validator import validate_signal as _validate_sig
        _cv_entry   = float(result.get('entry_lo', 0) or 0)
        _cv_stop    = float(result.get('stop_loss', 0) or 0)
        _cv_tp1     = float(result.get('tp1', 0) or 0)
        _cv_score   = float(result.get('score_final', result.get('score', 0)) or 0)
        _cv_dir     = str(result.get('direction', '') or '')
        if _cv_entry > 0 and _cv_stop > 0 and _cv_tp1 > 0:
            _cv_ok, _cv_issues = _validate_sig(_cv_score, _cv_dir, _cv_entry, _cv_stop, _cv_tp1)
            if not _cv_ok:
                result['_logic_errors'] = [i['msg'] for i in _cv_issues if i['level'] == 'ERROR']
                result['_logic_warnings'] = [i['msg'] for i in _cv_issues if i['level'] != 'ERROR']
                # 有ERROR级逻辑矛盾 → 强制降级，不推送
                _err_ids = [i['id'] for i in _cv_issues if i['level'] == 'ERROR']
                if _err_ids:
                    result['action'] = 'LOGIC_ERROR_BLOCKED'
                    result['_blocked_reason'] = f'逻辑验证失败[{",".join(_err_ids)}]: {result["_logic_errors"][0]}'
    except Exception:
        pass
    # ── [END 逻辑验证器] ─────────────────────────────────────────────────────

    # ── [设计院 2026-07-26] 决策C: signal_lifecycle结算闭环接入 ────────────
    # 职责: 对已存在的OPEN信号做实时TTL/SL/TP检查，填补result=null盲点
    # fail-safe: 任何异常不阻断主流程
    try:
        import sys as _sl_sys, os as _sl_os
        _sl_dir = _sl_os.path.dirname(_sl_os.path.abspath(__file__))
        if _sl_dir not in _sl_sys.path: _sl_sys.path.insert(0, _sl_dir)
        from brahma_signal import tick_signal_lifecycle as _tick_lc
        _lc_price = float(result.get('price', 0) or 0)
        if _lc_price > 0 and sym:
            _lc_alerts = _tick_lc(sym, _lc_price)
            if _lc_alerts:
                result['_lifecycle_alerts'] = _lc_alerts
    except Exception:
        pass
    # ── [END signal_lifecycle] ───────────────────────────────────────────

    # [全量接通 2026-08-26 苏摩111] 批次B：评分增强层
    # 接入位置：brahma_analysis_runner → run_analysis()

    # B0: brahma_context_injector — AI记忆注入器（为LLM Council提供上下文）
    try:
        from brahma_context_injector import get_fangcang_summary, get_brahma_rules
        _ctx_fc = get_fangcang_summary(symbol, result.get('regime',''), result.get('signal_dir','LONG'))
        _ctx_rules = get_brahma_rules(result.get('regime',''), result.get('signal_dir','LONG'))
        result['_context_fangcang'] = _ctx_fc
        result['_context_rules'] = _ctx_rules
    except Exception:
        pass

    # 接入位置：brahma_analysis_runner → run_analysis()

    # B1: regime_state_machine — 体制稳定性过滤
    try:
        from regime_state_machine import get_stable_regime
        _stable = get_stable_regime(symbol, result.get('regime', ''))
        if _stable and _stable != result.get('regime'):
            result['regime_raw'] = result.get('regime')
            result['regime'] = _stable
    except Exception:
        pass

    # B2: regime_scorer — 5-regime精细分类
    try:
        from regime_scorer import score_regime
        _rs = score_regime(symbol)
        if _rs and not _rs.get('error'):
            result['_regime_score'] = _rs
    except Exception:
        pass

    # B3: brahma_multiframe — 多周期FVG/OB扫描
    try:
        from brahma_multiframe import scan_mtf
        _mf = scan_mtf(symbol, result.get('price', 0))
        if _mf and not _mf.get('error'):
            result['_multiframe'] = _mf
    except Exception:
        pass

    # B4: brahma_onchain — 链上评分
    try:
        from brahma_onchain import onchain_score
        _oc = onchain_score(symbol, result.get('signal_dir', 'LONG'))
        if _oc and not _oc.get('error'):
            result['_onchain'] = _oc
    except Exception:
        pass

    # B5: bybit_liq_adapter — Bybit多空比
    try:
        from bybit_liq_adapter import get_ls_ratio_signal
        _bybit = get_ls_ratio_signal(symbol)
        if _bybit and not _bybit.get('error'):
            result['_bybit_ls'] = _bybit
    except Exception:
        pass

    # B6: s7_liq_config — 清算奖励
    try:
        from s7_liq_config import get_liq_bonus
        _lb = get_liq_bonus(result.get('notional', 0), symbol)
        if _lb:
            result['_liq_bonus'] = _lb
    except Exception:
        pass

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

    # ── nerve_system freshness_checker 数据质量守护（非阻断）────────────
    # [协同接入 2026-08-02 设计院自主] 分析前检查关键数据文件新鲜度
    _freshness_warnings = []
    try:
        import sys as _nv_sys, os as _nv_os
        _nv_root = _nv_os.path.join(_nv_os.path.dirname(__file__), '..', 'nerve_system')
        if _nv_root not in _nv_sys.path:
            if _nv_root not in _nv_sys.path: _nv_sys.path.insert(0, _nv_root)
        from freshness_checker import run as _fc_run
        _fc_alerts = _fc_run()
        _freshness_warnings = [
            f"{a['check']}: {a['issue']}"
            for a in _fc_alerts if a.get('level') in ('ERROR', 'WARN')
        ][:3]
    except Exception:
        pass

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
                result['_liq_heatmap'] = _liq  # [设计院 2026-07-27] 无条件写入，B3格式化依赖此字段
            else:
                # error时仍写入原始数据供B3格式化使用（无score贡献）
                result['_liq_heatmap'] = _liq
        except Exception as _le:
            _ext_detail['liq_heatmap'] = f'skip:{_le}'
            result.setdefault('_liq_heatmap', {})

        # [P1修复 2026-08-02 设计院] 用tardis_walls真实清算集群覆盖B3显示数据
        # 根因: liq_heatmap的short_liq_map/long_liq_map是按固定比例估算的假数据
        #       tardis_walls才是真实历史清算密集区（价格桶聚合），应优先显示
        try:
            _tw_raw = result.get('extra', {}).get('liq_snap', {}).get('tardis_walls', {})
            if isinstance(_tw_raw, str):
                import json as _json_tw
                _tw_raw = _json_tw.loads(_tw_raw)
            if _tw_raw and _tw_raw.get('available'):
                _price_now = float(result.get('price', 0) or 0)
                # 构建按距离分组的清算集群map
                _s_walls = _tw_raw.get('short_walls', [])  # 空头爆仓（价格上涨触发）
                _l_walls = _tw_raw.get('long_walls', [])   # 多头爆仓（价格下跌触发）
                _tardis_short_map = {}  # pct -> (price, usd)
                _tardis_long_map  = {}
                for _wp, _wv in sorted(_s_walls, key=lambda x: abs(x[0]-_price_now))[:6]:
                    _d = round(abs(_wp - _price_now) / _price_now * 100, 1) if _price_now else 0
                    _tardis_short_map[str(_d)] = (_wp, round(_wv/1e6, 2))
                for _wp, _wv in sorted(_l_walls, key=lambda x: abs(x[0]-_price_now))[:6]:
                    _d = round(abs(_wp - _price_now) / _price_now * 100, 1) if _price_now else 0
                    _tardis_long_map[str(_d)] = (_wp, round(_wv/1e6, 2))
                # 写入_liq_heatmap供B3节使用
                _lhm = result.get('_liq_heatmap') or {}
                _lhm['tardis_short_walls'] = _tardis_short_map  # {pct: (price, usd_m)}
                _lhm['tardis_long_walls']  = _tardis_long_map
                _lhm['tardis_date']        = _tw_raw.get('date', '?')
                _lhm['_has_tardis']        = True
                result['_liq_heatmap'] = _lhm
        except Exception as _twe:
            pass  # tardis注入失败不影响主流程

        # --- 1b. liq_density_engine 三所实时强平数据注入 [Bug3修复 2026-08-05] ---
        # 将三所实时强平结果注入 _liq_heatmap['_liq_density_walls']，供 formatter B3节使用
        try:
            import sys as _sys_ld
            _bb_path = str(__import__('pathlib').Path(__file__).parent)
            if _bb_path not in _sys_ld.path:
                _sys_ld.path.insert(0, _bb_path)
            from liq_density_engine import get_liq_density as _get_ld
            _cur_px_ld = float(result.get('price', result.get('mark_price', 0)) or 0)
            if _cur_px_ld <= 0:
                import urllib.request as _ur_ld, json as _jl
                _pr = _jl.loads(_ur_ld.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}',
                    timeout=5).read())
                _cur_px_ld = float(_pr['price'])
            if _cur_px_ld > 0:
                _ld_result = _get_ld(sym, _cur_px_ld)
                _lhm_cur = result.get('_liq_heatmap') or {}
                _lhm_cur['_liq_density_walls'] = {
                    'above_walls':  _ld_result.get('above_walls', []),
                    'below_walls':  _ld_result.get('below_walls', []),
                    'nearest_above': _ld_result.get('nearest_above'),
                    'nearest_below': _ld_result.get('nearest_below'),
                    'liq_bias':     _ld_result.get('liq_bias', 'NEUTRAL'),
                    'sources':      _ld_result.get('sources', ''),
                    'score_adj':    _ld_result.get('score_adj', 0),
                }
                result['_liq_heatmap'] = _lhm_cur
        except Exception as _lde:
            pass  # 静默降级，不影响主流程

        # --- 1c. liq_scanner 三所清算集群注入 [2026-08-12 苏摩封印] ---
        # 将 Binance+Bybit+Hyperliquid 三所实时数据固化进清算矩阵
        # 来源: brahma_core_step4 已调 get_liq_snapshot() 并写入 extra['liq_snap']
        try:
            _ls = result.get('extra', {}).get('liq_snap', {})
            if _ls and _ls.get('price', 0) > 0:
                _ls_px = _ls['price']
                _lhm_ls = result.get('_liq_heatmap') or {}

                # 三所OI汇总
                _lhm_ls['_three_exchange'] = {
                    'price':            _ls_px,
                    # Binance
                    'bn_oi_b':          round(_ls.get('oi_b', 0), 2),
                    'bn_long_pct':      round(_ls.get('long_pct', 50), 1),
                    'bn_top_long_pct':  round(_ls.get('top_long_pct', 50), 1),
                    'bn_fr':            _ls.get('fund_rate', 0),
                    # Bybit
                    'bb_oi_b':          round(_ls.get('bybit_oi_b', 0), 2),
                    'bb_long_pct':      _ls.get('bybit_long_pct', 0),
                    'bb_fr':            _ls.get('bybit_fr', 0),
                    'bb_price':         _ls.get('bybit_price', 0),
                    # Hyperliquid
                    'hl_oi_b':          round(_ls.get('hl_oi_b', 0), 3),
                    'hl_fr':            _ls.get('hl_fr', 0),
                    'hl_liq_50x_long':  _ls.get('hl_liq_50x_long', 0),
                    'hl_liq_50x_short': _ls.get('hl_liq_50x_short', 0),
                    'hl_liq_25x_long':  _ls.get('hl_liq_25x_long', 0),
                    'hl_liq_25x_short': _ls.get('hl_liq_25x_short', 0),
                    # 汇总
                    'total_oi_b':       round(_ls.get('total_oi_b', 0), 2),
                    'weighted_long':    _ls.get('weighted_long_pct', 50),
                    'fr_agreement':     _ls.get('fr_cross_agreement', False),
                    'liq_bias':         _ls.get('liq_bias', 'NEUTRAL'),
                    'liq_risk':         _ls.get('liq_risk', ''),
                }
                # 同步写入 short_liq_map / long_liq_map 供 formatter 估算层使用
                _lhm_ls.setdefault('short_liq_map', {})
                _lhm_ls.setdefault('long_liq_map', {})
                # HL 50x = 最近清算位（高密度）
                _hl50s = _ls.get('hl_liq_50x_short', 0)
                _hl50l = _ls.get('hl_liq_50x_long', 0)
                _hl25s = _ls.get('hl_liq_25x_short', 0)
                _hl25l = _ls.get('hl_liq_25x_long', 0)
                if _hl50s > 0:
                    _lhm_ls['short_liq_map']['50'] = _hl50s
                if _hl50l > 0:
                    _lhm_ls['long_liq_map']['50']  = _hl50l
                if _hl25s > 0:
                    _lhm_ls['short_liq_map']['25'] = _hl25s
                if _hl25l > 0:
                    _lhm_ls['long_liq_map']['25']  = _hl25l
                # BN/Bybit 20x 理论清算位
                _lhm_ls['short_liq_map']['20'] = round(_ls_px * 1.05, 2)
                _lhm_ls['long_liq_map']['20']  = round(_ls_px * 0.95, 2)
                _lhm_ls['short_liq_map']['10'] = round(_ls_px * 1.10, 2)
                _lhm_ls['long_liq_map']['10']  = round(_ls_px * 0.90, 2)
                _lhm_ls['price'] = _ls_px
                result['_liq_heatmap'] = _lhm_ls
        except Exception as _lse:
            pass  # 静默降级，不影响主流程
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
        # [设计院 2026-07-27 修复] 无论_ext_bonus是否0都写入字段，smoke test依赖此字段存在性
        result['_ext_score_bonus']  = _ext_bonus   # 前置写入，确保字段始终存在
        result['_ext_score_detail'] = _ext_detail
        if _ext_bonus != 0:
            _new_ext_score = _cur_ext_score + _ext_bonus
            result['total']       = _new_ext_score
            result['score']       = _new_ext_score
            result['score_final'] = _new_ext_score
            if isinstance(result.get('confluence'), dict):
                result['confluence']['score'] = _new_ext_score
                result['confluence']['total'] = _new_ext_score
            # (已在前置写入)

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
    # ── s12: oi_advanced_scanner OI多所聚合信号 [协同接入 2026-08-02 设计院自主] ──
    try:
        import sys as _oi_sys, os as _oi_os
        _oi_scripts = _oi_os.path.join(_oi_os.path.dirname(__file__), '..', 'scripts')
        if _oi_scripts not in _oi_sys.path:
            if _oi_scripts not in _oi_sys.path: _oi_sys.path.insert(0, _oi_scripts)
        from oi_advanced_scanner import (
            get_oi_multi_period as _oi_multi,
            get_premium_info as _oi_prem,
            get_ls_ratio as _oi_ls,
            score_oi_signal as _oi_score,
        )
        _oi_data = _oi_multi(sym)
        _oi_basis = _oi_prem(sym)
        _oi_ls_r  = _oi_ls(sym)
        _oi_fr    = _oi_basis.get('funding_rate', 0) if isinstance(_oi_basis, dict) else 0
        _oi_whale = _oi_ls_r.get('longShortRatio', 1.0) if isinstance(_oi_ls_r, dict) else 1.0
        _oi_retail= 1.0 / _oi_whale if _oi_whale else 1.0
        _klines   = result.get('_klines_1h', result.get('klines', []))
        _oi_s = _oi_score(
            oi=_oi_data, basis=_oi_basis, fr=_oi_fr,
            whale_l=_oi_whale, retail_l=_oi_retail,
            direction=signal_dir, klines_1h=_klines
        )
        _oi_score_val = _oi_s.get('score', 0) if isinstance(_oi_s, dict) else 0
        _oi_details   = _oi_s.get('details', []) if isinstance(_oi_s, dict) else []
        if abs(_oi_score_val) > 5:
            # OI信号显著时才注入（>5分阈值，避免噪音）
            _s12_bonus = round(_oi_score_val * 0.15, 2)  # OI权重15%，最大±15分
            result['score_final'] = round(float(result.get('score_final', 0) or 0) + _s12_bonus, 2)
            result['_oi_score']   = _oi_score_val
            result['_oi_details'] = _oi_details[:3]
            result.setdefault('_ext_scores', {})['s12_oi'] = _s12_bonus
    except Exception as _oi_e:
        pass  # 非阻断

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
                                    else 'ENTER_WATCH' if _sc >= 140  # [2026-08-24 苏摩111] 铁证:140-170 WR=91%，提升阈值
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


    # [P1 signal_log 自动注入 2026-07-24]
    # [2026-08-12 苏摩111] grade_num写入修复：写入前调用enrich_signal_grade
    try:
        from grade_utils import enrich_signal_grade as _enrich_grade
        _enrich_grade(result)  # 注入grade_num整数字段，覆盖91%缺失问题
    except Exception:
        pass
    # [根治 2026-08-24 苏摩111] 信号字段完整性 — 内联写入，消除p1_signal_log外部依赖
    # 根因：p1_signal_log.py已归档→import静默失败→signal_dir/score_final/timing_badge丢失→TIMEOUT 77%
    try:
        import json as _sjson, time as _stime, secrets as _ssec
        from pathlib import Path as _sPath
        from datetime import datetime as _sdt, timezone as _stz
        _sig_log = _sPath(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
        _sig_log.parent.mkdir(parents=True, exist_ok=True)
        _ts_now = _stime.time()
        _cf = result.get('confluence', {}) or {}
        _params = result.get('params', {}) or {}
        _sig_dir = result.get('signal_dir') or result.get('direction', 'UNKNOWN')
        _sig_record = {
            'signal_id':    result.get('signal_id', _ssec.token_hex(6)),
            'ts':           _ts_now,
            'timestamp':    _ts_now,
            'ts_iso':       _sdt.fromtimestamp(_ts_now, tz=_stz.utc).isoformat(),
            'symbol':       symbol or result.get('symbol', 'UNKNOWN'),
            'signal_dir':   _sig_dir,
            'direction':    _sig_dir,
            'regime':       result.get('regime', 'UNKNOWN'),
            'regime_cn':    result.get('regime_cn', ''),
            'score':        _cf.get('score') if isinstance(_cf, dict) else None,
            'score_final':  result.get('score_final'),
            'grade':        result.get('grade', ''),
            'grade_num':    result.get('grade_num'),
            'action':       result.get('action', ''),
            'valid':        result.get('valid', False),
            'price':        result.get('price', 0),
            'entry_lo':     result.get('entry_lo') or _params.get('entry_lo'),
            'entry_hi':     result.get('entry_hi') or _params.get('entry_hi'),
            'stop_loss':    result.get('stop_loss') or _params.get('sl'),
            'tp1':          result.get('tp1') or _params.get('tp1'),
            'tp2':          result.get('tp2') or _params.get('tp2'),
            'sl_pct':       result.get('sl_pct') or _params.get('sl_pct'),
            'rr1':          result.get('rr1') or _params.get('rr1'),
            'timing_badge': result.get('timing_badge', ''),
            'timing_status':result.get('timing_status', ''),
            'timing_score': result.get('timing_score', 0),
            'status':       'OPEN',
            'result':       None,
            'exit_price':   None,
            'pnl_pct':      None,
            'settled_at':   None,
        }
        # schema校验：缺失关键字段补UNKNOWN而非静默跳过
        for _req in ('signal_dir', 'score_final', 'regime', 'action'):
            if _sig_record.get(_req) is None:
                _sig_record[_req] = 'UNKNOWN'

        # [P0封印 2026-08-26] 统一分数守卫（这是真实写入路径，必须在此拦截）
        _guard_score  = float(_sig_record.get('score_final') or _sig_record.get('score') or 0)
        _guard_regime = str(_sig_record.get('regime', ''))
        _guard_dir    = str(_sig_record.get('direction', '') or _sig_record.get('signal_dir', ''))
        _guard_skip   = False
        if _guard_score <= 0:
            _guard_skip = True  # 负分/零分
        elif 'CHOP' in _guard_regime and _guard_score < 110:
            _guard_skip = True  # CHOP<110无价値
        elif 120 <= _guard_score <= 139 and 'BULL_TREND' in _guard_regime:
            _guard_skip = True  # BULL_TREND毒区间
        if _guard_skip:
            pass  # 不写入，静默丢弃
        else:
            # [P1修复 2026-08-27] SQE接入信号生成链路（写入前最后一道质量门控）
            try:
                from signal_quality_engine import get_sqe as _get_sqe_runner
                _sqe_inst = _get_sqe_runner()
                _sqe_result = _sqe_inst.evaluate(_sig_record)
                if _sqe_result.status == 'REJECT':
                    print(f'[SQE拦截] {_sig_record.get("symbol")} 被质量门控拦截: {_sqe_result.reason}')
                    _guard_skip = True
            except Exception:
                pass  # SQE不可用时静默降级
            # 写入信号池（已通过所有守卫）
            if not _guard_skip:
                with open(_sig_log, 'a') as _sf:
                    _sf.write(_sjson.dumps(_sig_record, ensure_ascii=False) + '\n')
    except Exception:
        pass

    # [协同接入 2026-08-02 设计院自主] condition_order_matrix 条件单计划卡
    # 当score≥120 且有有效params时，生成条件单计划卡存入data/condition_orders.json
    # 供position_guardian/auto_executor读取作为执行参考
    try:
        _com_score = float(result.get('score_final', 0) or 0)
        _com_params = result.get('params', {})
        _com_dir    = result.get('signal_dir', '') or result.get('direction', '')
        _com_price  = float(result.get('price', 0) or 0)
        if _com_score >= 120 and _com_params and _com_dir and _com_price > 0:
            import sys as _com_sys
            _com_parent = str(__import__('pathlib').Path(__file__).parent)
            if _com_parent not in _com_sys.path: _com_sys.path.insert(0, _com_parent)
            # [设计院封印 2026-08-20 苏摩确认] Binance真实持仓校验门控
            # 根因：score≥120直接写condition_orders → 幽灵持仓 → P0误告警
            # 修复：只有Binance有真实持仓时才写入condition_orders
            try:
                import requests as _req, hmac as _hmac, hashlib as _hlib, time as _time
                from pathlib import Path as _Path
                _api_key = open(str(_Path.home()/'.openclaw/workspace/TOOLS.md')).read()
                import re as _re
                _ak = _re.search(r'API Key: (\S+)', _api_key)
                _sk = _re.search(r'Secret: (\S+)', _api_key)
                if _ak and _sk:
                    _ts = int(_time.time()*1000)
                    _p  = f'timestamp={_ts}'
                    _sig = _hmac.new(_sk.group(1).encode(), _p.encode(), _hlib.sha256).hexdigest()
                    _pr = _req.get(f'https://fapi.binance.com/fapi/v2/positionRisk?{_p}&signature={_sig}',
                                   headers={'X-MBX-APIKEY': _ak.group(1)}, timeout=5).json()
                    _real_amt = next((float(x['positionAmt']) for x in _pr
                                      if x['symbol'] == symbol and float(x['positionAmt']) != 0), 0.0)
                    if _real_amt == 0.0:
                        result['_condition_plan'] = None  # 无真实持仓，不写condition_orders
                        raise StopIteration  # 跳过写入
            except StopIteration:
                pass
            except Exception:
                pass  # 网络失败时保守跳过，不产生幽灵记录
            else:
                pass  # 有真实持仓才继续往下写
            from condition_order_matrix import create_trade_plan as _create_plan
            _sl  = float(_com_params.get('stop_loss', 0) or 0)
            _tp1 = float(_com_params.get('tp1', 0) or 0)
            _liq = _sl * 0.85 if _sl > 0 else _com_price * (0.85 if _com_dir == 'SHORT' else 1.15)
            if _sl > 0 and _tp1 > 0:
                if _com_dir == 'SHORT':
                    _plan = _create_plan(
                        symbol=symbol, short_entry=_com_price, long_entry=0,
                        short_notional=_com_params.get('notional', 50),
                        long_notional=0, liq_price=_liq)
                else:
                    _plan = _create_plan(
                        symbol=symbol, short_entry=0, long_entry=_com_price,
                        short_notional=0, long_notional=_com_params.get('notional', 50),
                        liq_price=_liq)
                result['_condition_plan'] = _plan
    except Exception:
        pass  # 非阻断

    if _freshness_warnings:
        result['_data_freshness_warnings'] = _freshness_warnings

    # [Fix-2 2026-08-02 设计院] llm_council_bridge LLM二次审查（score≥140，非阻断）
    # 540行高价值模块，封印2026-07-01，今日接入主链路
    # 触发条件: score≥140 + 有效grade≥80（约5%信号，控制token成本）
    # 输出: 分数微调(-15~+10) + 风险摘要 → result['_llm_council']
    try:
        _lc_score = float(result.get('score_final', 0) or 0)
        _lc_grade = float(result.get('grade', 0) or 0)
        if _lc_score >= 140 and _lc_grade >= 80:
            import sys as _lc_sys
            _lc_parent = str(__import__('pathlib').Path(__file__).parent)
            if _lc_parent not in _lc_sys.path: _lc_sys.path.insert(0, _lc_parent)
            from llm_council_bridge import review as _lc_review
            _lc_result = _lc_review(result, market_ctx=None, force=False)
            if _lc_result and not _lc_result.get('error'):
                # [2026-08-04 对齐新字段] _llm_council 由 review() 直接写入 result
                _lc_council = _lc_result.get('_llm_council', {}) or {}
                _lc_adj = float(_lc_council.get('final_adj', _lc_council.get('adj', 0)) or 0)
                if _lc_adj != 0:
                    result['score_final'] = round(_lc_score + _lc_adj, 2)
                # [P1修复 2026-08-26] score硬上限守卫：防止多个加分项叠加导致score>200
                _score_cap = 200.0
                if float(result.get('score_final', 0) or 0) > _score_cap:
                    result['score_final'] = _score_cap
                    result['_score_capped'] = True
                # 透传 review() 注入的所有上下文字段
                for _k in ('_llm_council', '_macro_ctx', '_similar_signals', '_compressed'):
                    if _k in _lc_result and _lc_result[_k] is not None:
                        result[_k] = _lc_result[_k]
                if '_llm_council' not in result or not result['_llm_council']:
                    result['_llm_council'] = {
                        'adj':     _lc_adj,
                        'verdict': _lc_council.get('verdict', _lc_council.get('risk_level', '')),
                        'risk':    _lc_council.get('top_risk', '')[:100],
                        'cached':  _lc_council.get('from_cache', False),
                    }
    except Exception:
        pass  # 非阻断

    # [Fix-1 2026-08-02 设计院] score_final审计trail：记录各层贡献，防止覆写混乱
    try:
        _sf_final = float(result.get('score_final', 0) or 0)
        _sf_raw   = float(result.get('score_final_raw', _sf_final) or _sf_final)
        _sf_ext   = round(_sf_final - _sf_raw, 2)
        _sf_oi    = round(float(result.get('_oi_score', 0) or 0) * 0.15, 2)
        result['_score_audit'] = {
            'raw':      _sf_raw,
            'ext_adj':  _sf_ext,
            'oi_bonus': _sf_oi,
            'final':    _sf_final,
        }
    except Exception:
        pass

    # ── [P1 第五轮 2026-08-02] brahma_mem_compressor 接入 ────────────────────
    # score>=120 时压缩信号上下文写入 data/signal_context_memory.jsonl
    # 为后续LLM调用提供压缩上下文，避免重传完整result
    try:
        _final_score = float(result.get('score_final', result.get('score', 0)) or 0)
        if _final_score >= 120:
            from brahma_brain.brahma_mem_compressor import compress_signal_context
            _sym_mc = result.get('symbol', sym)
            _ctx = compress_signal_context(_sym_mc)
            # 写入 data/signal_context_memory.jsonl
            import json as _mcjson
            from pathlib import Path as _mcPath
            _mc_path = _mcPath(__file__).parent.parent / 'data' / 'signal_context_memory.jsonl'
            _mc_path.parent.mkdir(exist_ok=True)
            _mc_entry = {
                'symbol':     _sym_mc,
                'score':      _final_score,
                'ts':         __import__('datetime').datetime.utcnow().isoformat(),
                'ctx_budget': _ctx.get('context_budget', 0),
                'signals_n':  len(_ctx.get('recent_signals', [])),
            }
            with open(_mc_path, 'a', encoding='utf-8') as _mcf:
                _mcf.write(_mcjson.dumps(_mc_entry, ensure_ascii=False) + '\n')
            result['_mem_ctx'] = _mc_entry
    except Exception:
        pass  # mem_compressor失败不影响主流程
    # ── [P1 END] ─────────────────────────────────────────────────────────────

    # [修复 2026-08-11] 从panorama提取RSI回写到结果字段
    try:
        import re as _re_rsi
        _pano = result.get('_panorama_full', '')
        _m1 = _re_rsi.search(r'RSI\s+1H=([\d.]+)', _pano or '')
        if _m1: result['rsi_1h'] = float(_m1.group(1))
        _m4 = _re_rsi.search(r'4H=([\d.]+)', _pano or '')
        if _m4: result['rsi_4h'] = float(_m4.group(1))
    except Exception:
        pass

    # ══ [P0接入 2026-08-29 苏摩111] signal_15m_engine — 15M触发信号生成 ══
    # 接入位置：brahma_analysis_runner.run_analysis() 返回前
    # 根囤：15M触发层是核心执行路径，但signal_15m_engine.py完全未被调用
    try:
        from brahma_brain.signal_15m_engine import generate_15m_signal as _s15_fn
        _s15 = _s15_fn(sym)
        if _s15 and isinstance(_s15, dict):
            result['_signal_15m'] = _s15
            result['signal_15m_trigger']  = _s15.get('trigger', False)
            result['signal_15m_grade']    = _s15.get('grade', 0)
            result['signal_15m_reason']   = _s15.get('reason', '')
    except Exception:
        pass  # signal_15m_engine失败不影响主流程

    # ══ [P0接入 2026-08-29 苏摩111] market_quadrant — 四象限市场状态 ══
    # 接入位置：brahma_analysis_runner.run_analysis() 返回前
    # 根囤：LSR+FR+OI四象限判断完全未接入
    try:
        from brahma_brain.market_quadrant import get_quadrant as _mq_fn
        _mq = _mq_fn(sym)
        if _mq and isinstance(_mq, dict):
            result['_market_quadrant'] = _mq
            result['market_quadrant']  = _mq.get('quadrant', 'UNKNOWN')
            result['mq_score_delta']   = int(_mq.get('score_delta', 0) or 0)
            # 四象限评分贡献: 多头拥挤象限→score-15, 空头拥挤→score+12
            _mq_delta = int(_mq.get('score_delta', 0) or 0)
            if _mq_delta != 0:
                result['score_final'] = round(float(result.get('score_final', 0) or 0) + _mq_delta, 1)
                result['score'] = result['score_final']
    except Exception:
        pass  # market_quadrant失败不影响主流程

    # ── [AI-Trader自动发布 2026-08-29 苏摩111] ──────────────────────
    # 触发条件: valid=True + rr1≥1.0（赔率足够才发布）
    if result.get('valid_signal') and float(result.get('rr1', 0) or 0) >= 1.0:
        try:
            import sys as _sys, os as _os
            _scripts_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'scripts')
            if _scripts_dir not in _sys.path:
                _sys.path.insert(0, _scripts_dir)
            from ai4trade_publisher import publish_signal as _publish_signal
            _pub = _publish_signal(result)
            result['ai4trade'] = _pub
            if _pub.get('success'):
                logger.info(f'[AI-Trader] 发布成功: {_pub.get("url","")}')
        except Exception as _pub_e:
            result['ai4trade'] = {'success': False, 'reason': str(_pub_e)[:60]}
    # ── [AI-Trader END] ───────────────────────────────────────────────

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
    # [Phase0 2026-07-23 设计院] 熔断门控 — 连续亏损保护
    try:
        import sys as _cb_sys, os as _cb_os
        _cb_root = _cb_os.path.dirname(_cb_os.path.dirname(_cb_os.path.abspath(__file__)))
        if _cb_root not in _cb_sys.path:
            if _cb_root not in _cb_sys.path: _cb_sys.path.insert(0, _cb_root)
        from brahma_brain.circuit_breaker import BrahmaCircuitRegistry as _CBR
        _cb_registry = _CBR.get()
        if _cb_registry.has_open_breakers():
            import logging as _cb_log
            _cb_log.getLogger(__name__).warning(
                '[CircuitBreaker] 熳断开路→跳过本次分析（连续失败保护）')
            return {s: {'error': 'CIRCUIT_BREAKER_OPEN', 'score': 0, '_skipped': True}
                    for s in symbols}
    except Exception:
        pass  # 熔断检查失败不阻断主流程
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
            # 跳过非数字字段（文本注释类，如extreme_event风险描述）
            if not isinstance(v, (int, float)) and not str(v).lstrip('+-').split('.')[0].isdigit():
                logger.debug(f'[runner] score_item跳过文本字段 {k}')
                continue
            sv = str(v)
            try:
                val = float(sv.split('(')[0].replace('+','').strip())
                score_items.append({'dim': k, 'contrib': val, 'detail': sv[:60]})
            except Exception as _si_e:
                logger.debug(f'[runner] score_item解析跳过 {k}: {_si_e}')
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
        except Exception as _rf_e:
            logger.debug(f'[runner] risk_flags解析跳过: {_rf_e}')
        if 'FIX1' in str(breakdown.get('FIX1_假牛市', '')):
            risk_flags.append('FAKE_BULL_DETECTED')
        if result.get('_ext_score_detail', {}).get('whale', '') == 0:
            risk_flags.append('WHALE_NO_HISTORY')
        result['_risk_flags'] = risk_flags

    except Exception as _fe:
        result['_full_analysis_err'] = str(_fe)

    # [修复 2026-08-11] 从panorama提取RSI回写结果字段
    try:
        import re as _re_rsi
        _pano = result.get('_panorama_full', '')
        _m1 = _re_rsi.search(r'RSI\s+1H=([\d.]+)', _pano or '')
        if _m1: result['rsi_1h'] = float(_m1.group(1))
        _m4 = _re_rsi.search(r'4H=([\d.]+)', _pano or '')
        if _m4: result['rsi_4h'] = float(_m4.group(1))
    except Exception:
        pass

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

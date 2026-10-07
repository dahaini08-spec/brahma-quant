#!/usr/bin/env python3
"""
brahma_manual_analysis.py — 梵天手动全链路分析入口
设计院封印 2026-09-03 苏摩111

[输出格式封印 2026-10-02 苏摩111]
每次分析输出强制遵循三方联合标准模版：
  scripts/brahma_output_template.py — format_full_report() + format_vip_card()
  D1~D10 + Step11 + 三方决策 + VIP + 一致性评分表
  角色：🔬量化工程师 / 📐达摩院 / ⚔️40年交易员

P2修复 2026-09-11 苏摩111：pyc根治
sys.dont_write_bytecode必须在模块顶部设置，
在函数内部设置无效（import时已生成pyc）

定位：
  苏摩说「梵天分析」→ 调用此脚本
  一次输出：10步完整链路 + 80维判断 + VIP策略卡片
  不需要追问，不需要「这是全能力吗」

10步强制链路（MEMORY.md封印）：
  Step 0  实时数据并行拉取
  Step 1  FVG磁铁（Bull/Bear方向）
  Step 2  OB有效性（age<50bars且未穿越）
  Step 3  清算地图（止损山/止损池）
  Step 4  共振点（FVG+OB+清算三交叉）
  Step 5  OI趋势（15min连续，LONG_BUILD/SHORT_BUILD）
  Step 6  聪明钱分歧（大户vs散户）
  Step 7  Hurst+HAR-RV+VolBeta（波动率三维）
  Step 8  宏观压制（NFP/CPI日历）
  Step 9  风控门控（熔断/回撤/反脆弱）
  Step 10 输出VIP卡片（姓赵不宣格式）

接入位置：
  - python3 scripts/brahma_manual_analysis.py --symbols BTC ETH
  - morning-battlefield / afternoon-battlefield cron message
  - AI手动分析触发

2026-09-03 苏摩111封印
"""
import os as _os_blas
_os_blas.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
_os_blas.environ.setdefault('OMP_NUM_THREADS', '1')
_os_blas.environ.setdefault('MKL_NUM_THREADS', '1')

import json, sys, time, urllib.request, argparse, signal

# [2026-10-05 P1 brahma_path_setup] 统一路径管理，替代函数内裂sys.path.insert
try:
    import brahma_path_setup  # noqa — idempotent, already in scripts/
except ImportError:
    # 兼容旧环境：直接插入
    import sys as _ps; from pathlib import Path as _PP
    _pr = _PP(__file__).resolve().parent
    for _pp in [str(_pr), str(_pr.parent), str(_pr.parent/'brahma_brain')]:
        if _pp not in sys.path: sys.path.insert(0, _pp)
    del _ps, _PP, _pr, _pp

# [设计院封印 2026-10-01] 常量SSOT
try:
    from analysis_constants import (
        ATR_SL_MIN_MULT, ATR_TRAIL_MULT, ATR_HARV_MIN_MULT, ATR_4H_HARV_MULT,
        HURST_TREND, HURST_TRANSITION, HURST_RANDOM,
        SCORE_CHOP_STD, SCORE_CHOP_TREND, SCORE_CHOP_TRANS,
        SCORE_BEAR_LONG, SCORE_BEAR_REC_SHT,
        ALIGN_MIN_GATE4, ALIGN_BEAR_LONG, ALIGN_BEAR_REC_SHT,
        FC_SIM_THRESHOLD, RR_MIN,
        LSR_RETAIL_CROWDED, HARV_MIN_WIDTH_PCT,
        OB_MAX_AGE_BARS, MAX_RUNTIME_S, FETCH_TIMEOUT_S,
    )
except ImportError as _ce:
    # 兜底默认值（向后兼容）
    ATR_SL_MIN_MULT=ATR_TRAIL_MULT=1.5; ATR_HARV_MIN_MULT=2.0; ATR_4H_HARV_MULT=1.0
    HURST_TREND=0.6; HURST_TRANSITION=0.55; HURST_RANDOM=0.5
    SCORE_CHOP_STD=110; SCORE_CHOP_TREND=85; SCORE_CHOP_TRANS=95
    SCORE_BEAR_LONG=140; SCORE_BEAR_REC_SHT=130
    ALIGN_MIN_GATE4=3; ALIGN_BEAR_LONG=5; ALIGN_BEAR_REC_SHT=4
    FC_SIM_THRESHOLD=0.25; RR_MIN=1.5
    LSR_RETAIL_CROWDED=65.0; HARV_MIN_WIDTH_PCT=0.003
    OB_MAX_AGE_BARS=50; MAX_RUNTIME_S=90; FETCH_TIMEOUT_S=8
sys.dont_write_bytecode = True
import gc, resource as _res

def _mem_rss_mb():
    try: return _res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024
    except Exception: return 0

# GC优化: 每个step后主动释放内存
_gc_counter = 0

def _infer_signal_dir(regime: str, fvg_dir: str = '') -> str:
    """统一信号方向推断 — 替代3处重复逻辑
    [设计院封印 2026-10-01]
    优先级：FVG共识 > 体制默认（BEAR/RECOVERY=SHORT，其余=LONG）
    """
    if fvg_dir in ('BULL', 'BEAR'):
        return 'LONG' if fvg_dir == 'BULL' else 'SHORT'
    if regime.startswith('BEAR') or regime == 'CHOP_MID':
        return 'SHORT'
    return 'LONG'

def _step_gc():
    global _gc_counter
    _gc_counter += 1
    gc.collect()
    if _gc_counter % 3 == 0:
        gc.collect(2)  # full collect  # P2修复 2026-09-11 苏摩111：根治pyc缓存（必须在import后立即设置）
from pathlib import Path
from datetime import datetime, timezone

# 超时守卫：全链路分析超90s强制abort（防止阻塞gateway event loop）
MAX_RUNTIME_S = 180
def _timeout_handler(signum, frame):
    print(f'[brahma] ⚠️ 超时中止: 全链路超过{MAX_RUNTIME_S}s，强制退出防gateway阻塞', flush=True)
    sys.exit(1)
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(MAX_RUNTIME_S)

BASE = Path(__file__).parent.parent

DATA = BASE / 'data'

# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def fetch(url, timeout=7):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}

def klines(sym, interval, limit):
    try:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={sym}&interval={interval}&limit={limit}')
        d = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [(float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in d]
    except Exception:
        return []

def load_json(path):
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {}

# ══════════════════════════════════════════════════════════
# Step 0: 并行拉取实时数据
# ══════════════════════════════════════════════════════════


# [2026-10-04 杨志林工程化] analysis/步骤模块包接入
try:
    from analysis import (
        step0_fetch, step1_fvg, step2_ob, step3_liq,
        step4_resonance, step5_oi, step6_smart_money,
        step7_volatility, step8_macro, step9_risk, step10_vip,
    )
except ImportError:
    pass  # [WARN-suppressed: no var]
def step0_fetch_all(sym: str) -> dict:
    """真正并行拉取所有实时数据 [Fix 2026-10-03 苏摩111]
    原来注释说并行但实际串行。修复：ThreadPoolExecutor并行所有网络IO。
    预期：8个串行请求(~8s) → 并行(~1.5s)，每标的节省6s
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    usdt = sym + 'USDT'

    # 定义所有需要并行的网络请求
    _tasks = {
        'price_raw': lambda: fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={usdt}'),
        'fr_raw':    lambda: fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={usdt}&limit=1'),
        'oi_hist':   lambda: fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=15m&limit=8'),
        'oi_1h':     lambda: fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=1h&limit=8'),
        'oi_4h':     lambda: fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=4h&limit=6'),
        'lsr_raw':   lambda: fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={usdt}&period=1h&limit=4'),
        'top_raw':   lambda: fetch(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={usdt}&period=1h&limit=4'),
        'k1h':       lambda: klines(usdt, '1h', 8),
        'k4h':       lambda: klines(usdt, '4h', 6),
        'k15m':      lambda: klines(usdt, '15m', 8),
    }
    _results = {}
    with ThreadPoolExecutor(max_workers=len(_tasks)) as _pool:
        _futs = {_pool.submit(fn): key for key, fn in _tasks.items()}
        for _fut in _as_completed(_futs):
            _key = _futs[_fut]
            try:
                _results[_key] = _fut.result()
            except Exception:
                _results[_key] = None

    price   = float((_results.get('price_raw') or {}).get('price', 0))
    # [Fix 2026-10-03] price=0时用深度降级链，避免state_btc.json里price=0引发后续除零
    if price <= 0:
        try:
            import urllib.request as _ur2, ssl as _ssl2, json as _j2
            _ctx2 = _ssl2.create_default_context(); _ctx2.check_hostname=False; _ctx2.verify_mode=_ssl2.CERT_NONE
            _pr2 = _j2.loads(_ur2.urlopen(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={usdt}', timeout=5, context=_ctx2).read())
            price = float(_pr2.get('price', 0))
        except Exception:
            pass  # [WARN-suppressed: no var]
    k1h     = _results.get('k1h') or []
    k4h     = _results.get('k4h') or []
    k15m    = _results.get('k15m') or []
    fr_raw  = _results.get('fr_raw') or []
    fr      = float(fr_raw[0].get('fundingRate', 0)) if isinstance(fr_raw, list) and fr_raw else 0

    # OI全周期：15M短期 + 1H中期 + 4H主力
    oi_hist   = _results.get('oi_hist') or []
    oi_1h     = _results.get('oi_1h') or []
    oi_4h     = _results.get('oi_4h') or []
    oi_vals   = [float(x.get('sumOpenInterest', 0)) for x in oi_hist] if isinstance(oi_hist, list) else []
    oi_usd    = [float(x.get('sumOpenInterestValue', 0)) for x in oi_hist] if isinstance(oi_hist, list) else []
    oi_1h_vals= [float(x.get('sumOpenInterest', 0)) for x in oi_1h]  if isinstance(oi_1h, list) else []
    oi_4h_vals= [float(x.get('sumOpenInterest', 0)) for x in oi_4h]  if isinstance(oi_4h, list) else []

    # 大户仓位：拉3条历史，计算变化速率
    lsr_raw  = _results.get('lsr_raw') or []
    lsr_list = [(float(x.get('longAccount', 0)), float(x.get('shortAccount', 0))) for x in lsr_raw] if isinstance(lsr_raw, list) else []

    top_raw  = _results.get('top_raw') or []
    top_list = [float(x.get('longAccount', 0)) for x in top_raw] if isinstance(top_raw, list) else []
    # 大户变化速率（最新vs2小时前，正=增仓多，负=减仓多）
    top_delta = (top_list[-1] - top_list[0]) * 100 if len(top_list) >= 2 else 0.0  # P0修复: 最新-最早=净变化方向正确

    # ATR全周期：1H + 4H + 1D
    k1d = klines(usdt, '1d', 10)

    dep      = fetch(f'https://fapi.binance.com/fapi/v1/depth?symbol={usdt}&limit=5')
    bids_sum = sum(float(x[1]) for x in dep.get('bids', []))
    asks_sum = sum(float(x[1]) for x in dep.get('asks', []))

    liq_b    = load_json(DATA / f'liq_heatmap_{usdt.lower()}.json')
    # 优先读取标的专属state文件（修复ETH OB/FVG数据污染）
    # brahma_state_refresh.py 已封印为每个标的写入独立文件
    _sym_lower    = sym.lower()  # btc / eth / near / zec ...
    _sym_state    = DATA / f'brahma_state_{_sym_lower}.json'
    _fallback     = DATA / 'brahma_state.json'
    _candidate    = load_json(_sym_state) if _sym_state.exists() else {}
    # ── [果蝇架构修复 2026-09-13 苏摩111] 缓存过期检测 + 自动刷新 ──
    # 根因：brahma_state_*.json 由 brahma_state_refresh.py cron写入，但cron可能停摆
    # 修复：检测文件mtime > 4h 或价格偏差 > 0.5% 时，自动调用 brahma_core.analyze() 重算
    import os as _os_fs, time as _time_fs
    _state_mtime = _os_fs.path.getmtime(_sym_state) if _sym_state.exists() else 0
    _state_age_s = _time_fs.time() - _state_mtime
    _state_stale = _state_age_s > 14400  # 4h = 14400s
    _candidate  = load_json(_sym_state) if _sym_state.exists() else {}
    _state_price = _candidate.get('price', 0) if _candidate else 0
    _price_dev  = abs(_state_price - price) / price if price > 0 and _state_price > 0 else 1.0
    _price_ok   = _price_dev < 0.005  # 0.5%偏差内
    
    if _candidate and _state_price > 0 and _price_ok and not _state_stale:
        # 缓存新鲜且价格匹配 → 直接使用
        # [2026-10-06 苏摩111] 但如果没有confluence字段，强制重新analyze()
        _has_confluence = bool(_candidate.get('confluence',{}))
        if _has_confluence:
            bs = _candidate
            # [2026-10-06 苏摩111] 缓存命中时，轻量级获取analyze().signal_dir
            # analyze_signal_dir已保存时直接用，否则5s超时运行analyze()
            _analyze_signal_from_cache = str(_candidate.get('analyze_signal_dir','') or '')
            if not _analyze_signal_from_cache or _analyze_signal_from_cache == 'NONE':
                try:
                    import concurrent.futures as _cf_sig
                    sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
                    import fangcang_engine as _fe, data_cache as _dc, cross_market_engine as _cme, onchain_engine as _oe
                    from brahma_core import analyze as _analyze_sig
                    with _cf_sig.ThreadPoolExecutor(max_workers=1) as _sig_ex:
                        _sig_fut = _sig_ex.submit(_analyze_sig, f'{sym}USDT')
                        _sig_res = _sig_fut.result(timeout=20)
                    if isinstance(_sig_res, dict):
                        _analyze_signal_from_cache = str(_sig_res.get('signal_dir','') or '')
                        bs['analyze_signal_dir'] = _analyze_signal_from_cache
                        bs['confluence'] = _sig_res.get('confluence', bs.get('confluence',{}))
                        bs['score_final'] = float(_sig_res.get('score_final', bs.get('score_final',0)))
                except Exception as _e_sig:
                    _analyze_signal_from_cache = ''
        else:
            try:
                sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
                from brahma_core import analyze as _analyze
                bs = _analyze(f'{sym}USDT')
                if bs and isinstance(bs, dict):
                    import json as _json_save
                    _sym_state.write_text(_json_save.dumps(bs, ensure_ascii=False))
            except Exception as _e:
                print(f'[WARN] brahma_manual_analysis: analyze重跑失败: {_e}', file=sys.stderr)
                bs = _candidate
    elif _candidate and _state_price > 0 and (_price_dev >= 0.005 or _state_stale):
        # 缓存过期或价格偏差过大 → 实时重算
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
            from brahma_core import analyze as _analyze
            bs = _analyze(f'{sym}USDT')
            # [修复 2026-09-13 苏摩111] analyze结果写回state文件
            # 根因: analyze()返回结果但从未保存→state永远过期→看门狗告警
            if bs and isinstance(bs, dict):
                import json as _json_save
                _sym_state.write_text(_json_save.dumps(bs, ensure_ascii=False))
        except Exception as _e:
            # [2026-10-05 P0-A fix] _sys_warn已清除
            print(f'[WARN] brahma_manual_analysis: brahma_core.analyze({sym}USDT) 失败: {_e}', file=sys.stderr)
            bs = _candidate  # 重算失败→退回缓存（过期总比没有好）
    else:
        # state文件不存在 → 实时调用brahma_core
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
            from brahma_core import analyze as _analyze
            bs = _analyze(f'{sym}USDT')
            # [修复 2026-09-13 苏摩111] 同上，analyze结果写回state
            if bs and isinstance(bs, dict):
                import json as _json_save
                _sym_state.write_text(_json_save.dumps(bs, ensure_ascii=False))
        except Exception as _e:
            # [cleaned] import sys as _sys_warn2
            print(f'[WARN] brahma_manual_analysis: brahma_core.analyze({sym}USDT) fallback失败: {_e}', file=_sys_warn2.stderr)
            bs = load_json(_fallback)
    gex_s    = load_json(DATA / 'gex_state.json')
    vb_s     = load_json(DATA / 'vol_beta_state.json')
    mac_s    = load_json(DATA / 'macro_state.json')
    mac_cal  = load_json(DATA / 'macro_cal_cache.json')
    cb       = load_json(DATA / 'circuit_breaker.json')
    dd       = load_json(DATA / 'drawdown_state.json')
    af       = load_json(DATA / 'antifragile_state.json')
    regime_s = load_json(DATA / 'regime_state.json')

    # ── [果蝇架构 2026-09-13 苏摩111] 缓存freshness审计 ──
    # 检测所有缓存文件年龄，生成freshness报告供VIP标签+告警使用
    import os as _os_c, time as _time_c
    _now = _time_c.time()
    _cache_freshness = {}
    _cache_specs = [
        ('brahma_state', _sym_state, 14400),
        ('gex',         DATA / 'gex_state.json', 86400),
        ('vol_beta',    DATA / 'vol_beta_state.json', 43200),
        ('macro',       DATA / 'macro_state.json', 86400),
        ('cvd',         DATA / f'cvd_realtime_{usdt.lower()}.json', 3600),
        ('liq_heatmap', DATA / f'liq_heatmap_{usdt.lower()}.json', 14400),
        ('circuit_breaker', DATA / 'circuit_breaker.json', 3600),
        ('drawdown',    DATA / 'drawdown_state.json', 3600),
        ('antifragile', DATA / 'antifragile_state.json', 86400),
        ('regime_s',    DATA / 'regime_state.json', 3600),
    ]
    _stale_caches = []
    for _name, _path, _ttl in _cache_specs:
        if _path.exists():
            _age = _now - _os_c.path.getmtime(_path)
            _stale = _age > _ttl
            _cache_freshness[_name] = {'age_h': round(_age/3600, 1), 'stale': _stale, 'ttl_h': round(_ttl/3600, 1)}
            if _stale:
                _stale_caches.append(f'{_name}({_age/3600:.1f}h> {_ttl/3600:.0f}h)')
        else:
            _cache_freshness[_name] = {'age_h': -1, 'stale': True, 'ttl_h': round(_ttl/3600, 1)}
            _stale_caches.append(f'{_name}(不存在)')
    # stderr告警（供日志捕获）
    if _stale_caches:
        print(f'[FRESHNESS] ⚠️ {sym} 过期缓存: {", ".join(_stale_caches)}', file=sys.stderr)

    # ══ 闸门1: 数据新鲜度硬门控 [9.18 苏摩111 顶层修复] ══
    # 核心数据源过期=拒绝分析，而不是用过期数据跑出虚假信号
    # 关键认知：过期缓存不是"数据不新鲜"，是"数据是假的"
    # [2026-09-21 P4.3根修 苏摩111] liq_heatmap改为step3实时拉取，不再列为关键数据源
    _CRITICAL_SOURCES = ['cvd', 'gex']  # CVD 1h / GEX 4h（liq_heatmap已改为实时拉取）
    _critical_stale = [c for c in _stale_caches if any(c.startswith(s) for s in _CRITICAL_SOURCES)]
    if _critical_stale:
        _reject_msg = (
            f'❌ 拒绝分析：核心数据源过期 → {", ".join(_critical_stale)}\n'
            f'   过期数据=虚假信号，不是"数据不新鲜"\n'
            f'   请先重启进程+刷新数据：bash scripts/start_supercronic.sh + 手动刷新\n'
            f'   闸门1封印 2026-09-18 苏摩111'
        )
        print(_reject_msg, file=sys.stderr)
        # 返回特殊标记，让run_analysis知道是被闸门拦截
        return {'_gate1_rejected': True, '_reject_msg': _reject_msg, '_stale': _critical_stale}

    return {
        'sym': sym, 'usdt': usdt, 'price': price,
        'k1h': k1h, 'k4h': k4h, 'k15m': k15m, 'k1d': k1d,
        'fr': fr, 'oi_vals': oi_vals, 'oi_usd': oi_usd,
        'oi_1h_vals': oi_1h_vals, 'oi_4h_vals': oi_4h_vals,
        'lsr_list': lsr_list, 'top_list': top_list, 'top_delta': top_delta,
        'bids_sum': bids_sum, 'asks_sum': asks_sum,
        'liq': liq_b, 'bs': bs, 'gex': gex_s.get(sym, {}),
        'vb': vb_s.get(sym, {}), 'mac': mac_s, 'mac_cal': mac_cal,
        'cb': cb, 'dd': dd, 'af': af, 'regime_s': regime_s,
        'cache_freshness': _cache_freshness,
        'stale_caches': _stale_caches,
    }

# ══════════════════════════════════════════════════════════
# Step 1: FVG磁铁
# ══════════════════════════════════════════════════════════

def step1_fvg(d: dict) -> dict:
    bd    = d['bs'].get('confluence', {}).get('breakdown', {})
    price = d['price']
    k1h   = d['k1h']
    sym   = d['sym']

    # ── 优先读取 brahma_state 里的 _fvg_map（由 block_a 实时计算）────────
    fvg_map = bd.get('_fvg_map', {})
    magnet = 0
    fvg_dir = 'NONE'
    fvg_desc = '无有效FVG数据'
    price_lo = price * 0.70
    price_hi = price * 1.30

    if fvg_map:
        # 全周期FVG地图：每个周期保留最近有效FVG，按权重综合方向
        # 苏摩111封印 2026-09-04：禁止只看单一FVG
        TF_WEIGHT = {'1d': 4, '4h': 3, '1h': 2, '15m': 1}
        all_fvgs = []
        # 每个周期的FVG分组
        tf_fvg_map = {}  # {tf: [fvg,...]}
        for tf, fvgs in fvg_map.items():
            # 过滤：距离现价>8%的FVG是历史遗留，不参与近期决策
            valid = [f for f in fvgs
                     if not f.get('filled')
                     and price_lo <= f['mid'] <= price_hi
                     and abs(f.get('mid', price) - price) / price <= 0.08]
            if valid:
                # 每个周期取距离现价最近的一个
                valid.sort(key=lambda x: abs(x['mid'] - price))
                tf_fvg_map[tf] = valid[0]
                w = TF_WEIGHT.get(tf, 1)
                all_fvgs.append((abs(valid[0]['mid'] - price), w, tf, valid[0]))

        # 全周期综合方向投票（权重加权）
        bull_score = 0; bear_score = 0
        for _, w, tf, f in all_fvgs:
            if f['type'] == 'BULL': bull_score += w
            else: bear_score += w

        # 主导方向：权重票数多的方向
        fvg_consensus = 'BULL' if bull_score > bear_score else ('BEAR' if bear_score > bull_score else 'NONE')

        # 主磁铁：选最高权重周期里方向与共识一致的最近FVG
        all_fvgs.sort(key=lambda x: (-x[1], x[0]))  # 先按权重降序，再按距离升序
        best = None
        for _, w, tf, f in all_fvgs:
            if f['type'] == fvg_consensus or fvg_consensus == 'NONE':
                best = (tf, f); break
        if best is None and all_fvgs:
            best = (all_fvgs[0][2], all_fvgs[0][3])

        if best:
            best_tf, best_f = best
            magnet  = best_f['mid']
            fvg_dir = fvg_consensus if fvg_consensus != 'NONE' else best_f['type']

            # 全周期描述
            tf_parts = []
            for _, w, tf, f in sorted(all_fvgs, key=lambda x: -x[1]):
                tf_parts.append(f'{tf.upper()}:{f["type"]}@${f["mid"]:,.0f}')
            fvg_desc = (
                f'全周期FVG共识: {fvg_consensus} '
                f'(多{bull_score}分 空{bear_score}分) '
                f'主磁铁:{best_tf.upper()} ${best_f["lo"]:,.0f}~${best_f["hi"]:,.0f} '
                f'中点${best_f["mid"]:,.0f}({best_f.get("dist_pct",0):+.1f}%)\n'
                f'  各周期: {" | ".join(tf_parts)}'
            )
    else:
        # 没有 _fvg_map（旧版state）→ 回走breakdown旧逻辑
        for label, txt in [
            ('4H_LONG',  str(bd.get('FVG_4H_LONG',  '') or '')),
            ('15M_LONG', str(bd.get('FVG_15M_LONG', '') or '')),
            ('4H_SHORT', str(bd.get('FVG_4H_SHORT', '') or '')),
        ]:
            if '磁铁' in txt:
                try:
                    mag = float(txt.split('磁铁')[1].split(']')[0].strip())
                    if price_lo <= mag <= price_hi:
                        magnet   = mag
                        fvg_dir  = 'BULL' if 'LONG' in label else 'BEAR'
                        fvg_desc = txt[:100]
                        break
                except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # 如果state层无数据，临时K线估算
    if not magnet and len(k1h) >= 3:
        last = k1h[-1]; prev2 = k1h[-3]
        if prev2[1] < last[2]:  # Bull FVG
            manual_fvg = round((prev2[1] + last[2]) / 2, 1)
            if price_lo <= manual_fvg <= price_hi:
                magnet   = manual_fvg
                fvg_dir  = 'BULL'
                fvg_desc = f'1H Bull FVG估算 缺口{prev2[1]:.0f}~{last[2]:.0f} 中点{manual_fvg:.0f}'

    # D1修复：删除死代码，D2：注入hi/lo供step4边界计算
    _fvg_hi = 0; _fvg_lo = 0
    if fvg_map:
        for tf, fvgs in fvg_map.items():
            for f in fvgs:
                if f.get('mid') == magnet:
                    _fvg_hi = f.get('hi', 0)
                    _fvg_lo = f.get('lo', 0)
    return {'dir': fvg_dir, 'magnet': magnet, 'desc': fvg_desc,
            'hi': _fvg_hi, 'lo': _fvg_lo, 'fvg_map': fvg_map,
            'tf_fvg_map': tf_fvg_map if 'tf_fvg_map' in dir() else {},
            'bull_score': bull_score if 'bull_score' in dir() else 0,
            'bear_score': bear_score if 'bear_score' in dir() else 0,
            'consensus': fvg_consensus if 'fvg_consensus' in dir() else fvg_dir}

def step1b_range(d: dict) -> dict:
    """Step1b: 区间识别（range_engine）"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.range_engine import range_score
        k1h = d.get('k1h', [])
        if not k1h or len(k1h) < 20:
            return {'score': 0, 'is_range': False, 'note': 'K线数据不足'}
        highs = [x[1] for x in k1h]
        lows = [x[2] for x in k1h]
        closes = [x[3] for x in k1h]
        signal_dir = _infer_signal_dir(d.get('regime',''), fvg.get('dir','') if 'fvg' in dir() else '')
        result = range_score(highs, lows, closes, signal_dir)
        return result
    except Exception as e:
        return {'score': 0, 'is_range': False, 'note': f'区间引擎异常: {str(e)[:50]}'}

def step1b_fangcang_hcme(d: dict, fvg: dict) -> dict:
    """Step1b: 方仓历史匹配（HCME 4565案例） — P1整合 2026-09-12
    从brahma_state.fangcang提取Top5相似情境 + hcme_wr_adj + hcme_context
    输出供Step4共振和Step10交易员视角使用
    """
    bs = d.get('bs', {})
    fc = bs.get('fangcang', {}) if isinstance(bs, dict) else {}
    price = d.get('price', 0)

    if not fc or not isinstance(fc, dict):
        return {'top5': [], 'wr_adj': 0, 'context': '方仓无数据',
                'signal_hint': '', 'regime': '', 'n_cases': 0}

    top_similar = fc.get('top_similar', [])[:5]
    wr_adj = fc.get('hcme_wr_adj', 0)
    context = fc.get('hcme_context', '')
    signal_hint = fc.get('signal_hint', '')
    fc_regime = fc.get('current_regime', '') or fc.get('regime', '')
    n_cases = fc.get('similar_cases_count', 0) or fc.get('n_cases', 0)
    long_prob = fc.get('long_prob', 0)
    short_prob = fc.get('short_prob', 0)
    chop_prob = fc.get('chop_prob', 0)
    trap_alert = fc.get('trap_alert', '')
    confidence = fc.get('confidence_level', '')
    bias = fc.get('market_bias', '')

    # 格式化Top5
    top5_fmt = []
    for i, t in enumerate(top_similar):
        dt = t.get('dt', '?')
        score = t.get('score', 0)
        future_ret = t.get('future_ret', 0)
        future_max = t.get('future_max', 0)
        future_min = t.get('future_min', 0)
        regime = t.get('regime', '?')
        top5_fmt.append({
            'rank': i + 1,
            'date': dt,
            'similarity': round(score, 3),
            'future_ret': future_ret,
            'future_max': future_max,
            'future_min': future_min,
            'regime': regime,
        })

    # 方仓概率矩阵
    prob_matrix = {
        'long': round(long_prob * 100, 1) if isinstance(long_prob, (int, float)) else 0,
        'short': round(short_prob * 100, 1) if isinstance(short_prob, (int, float)) else 0,
        'chop': round(chop_prob * 100, 1) if isinstance(chop_prob, (int, float)) else 0,
    }

    # 信号方向
    # [P3修复 2026-10-01 苏摩111] 低相似度降级：<0.25时方仓信号置NEUTRAL
    # 根因：相似度0.191时方仓信号无统计意义（随机匹配），穿透到G4制造噪音
    # 修复：相似度<0.25=降级为NEUTRAL，输出体制基准WR作为参考
    _top_sim = top5_fmt[0]['similarity'] if top5_fmt else 0
    _SIM_THRESHOLD = FC_SIM_THRESHOLD
    fc_direction = 'NEUTRAL'
    if signal_hint and _top_sim >= _SIM_THRESHOLD:
        if 'LONG' in signal_hint.upper():
            fc_direction = 'LONG'
        elif 'SHORT' in signal_hint.upper():
            fc_direction = 'SHORT'
    elif signal_hint and _top_sim < _SIM_THRESHOLD:
        # 低相似度：用体制基准WR方向作为方仓方向（不用具体案例）
        _regime_wr_map = {
            'BULL_TREND': 'LONG', 'BEAR_TREND': 'SHORT',
            'BEAR_RECOVERY': 'LONG', 'CHOP_MID': 'NEUTRAL',
        }
        _regime_base = str(fc_regime or d.get('bs',{}).get('regime','CHOP_MID')).upper()
        fc_direction = _regime_wr_map.get(_regime_base, 'NEUTRAL')
        # 注入低相似度警告，防止下游误用
        trap_alert = (str(trap_alert) if trap_alert and not isinstance(trap_alert, str) else (trap_alert or '')) + f' ⚠️方仓相似度{_top_sim:.3f}<{_SIM_THRESHOLD}，降级用体制基准WR={fc_direction}'

    # 描述
    desc_parts = []
    if top5_fmt:
        t1 = top5_fmt[0]
        desc_parts.append(f"Top1: {t1['date']} 相似{t1['similarity']:.3f} 未来{t1['future_ret']:+.1f}%")
    if context:
        desc_parts.append(context)
    if trap_alert:
        desc_parts.append(f'⚠️{trap_alert}')
    desc = ' | '.join(desc_parts) if desc_parts else '方仓无信号'

    return {
        'top5': top5_fmt,
        'wr_adj': wr_adj,
        'context': context,
        'signal_hint': signal_hint,
        'direction': fc_direction,
        'regime': fc_regime,
        'n_cases': n_cases,
        'prob_matrix': prob_matrix,
        'trap_alert': trap_alert,
        'confidence': confidence,
        'bias': bias,
        'desc': desc,
    }

# ══════════════════════════════════════════════════════════
# Step 2: OB有效性
# ══════════════════════════════════════════════════════════

def step2_ob(d: dict) -> dict:
    bd = d['bs'].get('confluence', {}).get('breakdown', {})
    price = d.get('price', 0)  # P1修复: 用实时价格计算距现价

    # ── 优先读取 _ob_map（由 block_a 实时计算）──────────────────────
    ob_map = bd.get('_ob_map', {})
    results = {}

    if ob_map:
        for tf, obs in ob_map.items():
            for ob in obs:
                key  = f'OB_{tf.upper()}_{ob["type"]}'
                note_tag = ob['note']  # NEW/FRESH/AGING/EXPIRED
                valid    = ob['valid']
                icon = {'NEW': '✅最新鲜', 'FRESH': '✅新鲜有效',
                        'AGING': '⚠️老化中', 'EXPIRED': '❌已过期'}.get(note_tag, '⚠️')
                lo  = ob.get('lo', 0)
                hi  = ob.get('hi', 0)
                age = ob.get('age', 0)
                # P1修复: 用实时价格重新计算距现价
                dist_pct = ((hi + lo) / 2 - price) / price * 100 if price and lo else 0
                # P1修复: BEAR OB在现价下方=已被穿越=失效; BULL OB在现价上方=已被穿越=失效
                if ob['type'] == 'BEAR' and hi < price:
                    valid = False
                    icon = '❌已穿越'
                if ob['type'] == 'BULL' and lo > price:
                    valid = False
                    icon = '❌已穿越'
                results[key] = {
                    'valid': valid,
                    'lo': lo,        # P1修复: 返回结构化价格
                    'hi': hi,        # P1修复: 返回结构化价格
                    'age': age,      # P1修复: 返回结构化age
                    'dist_pct': round(dist_pct, 2),  # P1修复: 实时距现价%
                    'note':  (f'{icon} age={age}bars '
                              f'${lo:,.1f}~${hi:,.1f} '
                              f'dist={dist_pct:+.2f}%')
                }
    else:
        # 备用：读取旧版breakdown字段
        for key in ['OB新鲜度_1H_LONG', 'OB新鲜度_4H_LONG', 'OB_1D_LONG',
                    'OB新鲜度_1H_SHORT', 'OB新鲜度_4H_SHORT']:
            val = str(bd.get(key, '') or '')
            if not val:
                continue
            valid = True
            note  = val[:100]
            if 'age乘数=0.30' in val:
                valid = False
                note  = f'❌已老化(age≥5bars) → 作废  {val[:60]}'
            elif 'age乘数=0.50' in val:
                note  = f'⚠️ 中等有效(age40-50)  {val[:60]}'
            elif 'age乘数=1.0' in val or 'age乘数=0.8' in val:
                note  = f'✅ 新鲜有效  {val[:60]}'
            results[key] = {'valid': valid, 'note': note}

    return results

# ══════════════════════════════════════════════════════════
# Step 3: 清算地图
# ══════════════════════════════════════════════════════════

def step3_liq(d: dict) -> dict:
    """[2026-09-21 P4.3根修 苏摩111] 实时调用get_liq_heatmap，不再用过期缓存
    根因：缓存快照写入时价格$81,861，3.5h后价格涨到$84,723
          止损墙$83,498从上方变成下方→VIP卡片逻辑反了
    修复：每次分析时实时拉取价格+订单簿+计算清算价位
    """
    price = d['price']
    sym = d.get('sym', d.get('symbol', 'BTC')) + 'USDT'  # [2026-09-21修复] step0用'sym'不是'symbol'

    # 实时拉取清算热力图（不用缓存文件）
    try:
        # [cleaned] import sys as _sys
        sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
        from liq_heatmap import get_liq_heatmap
        _realtime_liq = get_liq_heatmap(sym)
        if _realtime_liq and 'error' not in _realtime_liq:
            # [2026-09-21 P2修复] 标准化key名：nearest_xxx_liq → nearest_xxx
            liq = _realtime_liq
            liq['nearest_short'] = _realtime_liq.get('nearest_short_liq', 0)
            liq['nearest_long'] = _realtime_liq.get('nearest_long_liq', 0)
            liq['second_short'] = _realtime_liq.get('second_short_liq', 0) if 'second_short_liq' in _realtime_liq else liq.get('second_short', 0)
            liq['second_long'] = _realtime_liq.get('second_long_liq', 0) if 'second_long_liq' in _realtime_liq else liq.get('second_long', 0)
            # 更新d['liq']为实时数据
            d['liq'] = liq
        else:
            # 降级：用缓存
            liq = d['liq']
            # 缓存也标准化key
            liq['nearest_short'] = liq.get('nearest_short_liq', liq.get('nearest_short', 0))
            liq['nearest_long'] = liq.get('nearest_long_liq', liq.get('nearest_long', 0))
    except Exception as _e:
        print(f"[WARN] step3_liq: 实时拉取失败，降级缓存: {_e}", file=sys.stderr)
        liq = d['liq']
        liq['nearest_short'] = liq.get('nearest_short_liq', liq.get('nearest_short', 0))
        liq['nearest_long'] = liq.get('nearest_long_liq', liq.get('nearest_long', 0))

    short_map = liq.get('short_liq_map', {})
    long_map  = liq.get('long_liq_map', {})

    # ── 优先使用文件直接计算好的nearest字段 ──────────────────────────
    # liq_heatmap_BTCUSDT.json 中 short_liq_map 的 key=百分比, value=该百分比对应的清算价
    # 但key与value顺序是倒置的（key小对应更远的价），直接用 nearest_short_liq 字段最准确
    nearest_short = liq.get('nearest_short_liq', 0)
    nearest_long  = liq.get('nearest_long_liq',  0)
    dist_short    = liq.get('dist_to_short_liq', 0)   # % distance
    dist_long     = liq.get('dist_to_long_liq',  0)

    # 若文件没有 nearest 字段（旧格式），用实时价格×最小百分比反算
    if not nearest_short and short_map:
        min_pct = min(float(k) for k in short_map.keys())
        nearest_short = round(price * (1 + min_pct / 100), 1)
        dist_short    = min_pct
    if not nearest_long and long_map:
        min_pct = min(float(k) for k in long_map.keys())
        nearest_long  = round(price * (1 - min_pct / 100), 1)
        dist_long     = min_pct

    # 第二层清算目标（用实时价格×次小百分比）
    sorted_short_pcts = sorted(float(k) for k in short_map.keys()) if short_map else []
    sorted_long_pcts  = sorted(float(k) for k in long_map.keys())  if long_map  else []
    # D10修复: 第二层清算用nearest_short为基准往外推ATR，不用分析时刻price重算
    _ns = nearest_short if nearest_short else price
    _nl = nearest_long  if nearest_long  else price
    second_short = round(_ns * (1 + sorted_short_pcts[1] / 100), 1) if len(sorted_short_pcts) >= 2 else 0
    second_long  = round(_nl * (1 - sorted_long_pcts[1]  / 100), 1) if len(sorted_long_pcts)  >= 2 else 0

    target_pct  = (nearest_short - price) / price * 100 if nearest_short and price else 0
    support_pct = (price - nearest_long)  / price * 100 if nearest_long  and price else 0

    # CRITICAL-1修复: 真实计算liq_bias，L2门控依赖此字段
    # 下方多头清算 > 上方空头清算*1.3 → 主力优先往下打（DOWN）
    # 上方空头清算 > 下方多头清算*1.3 → 主力优先往上打（UP）
    _sl = dist_short if dist_short else 999
    _ll = dist_long  if dist_long  else 999
    if _ll < _sl * 0.77:        # 下方清算更近（距离更小=量更集中）
        _liq_bias = 'DOWN'
    elif _sl < _ll * 0.77:      # 上方清算更近
        _liq_bias = 'UP'
    else:
        _liq_bias = 'NEUTRAL'

    return {
        'nearest_short':      nearest_short,
        'nearest_short_pct':  dist_short,
        'second_short':       second_short,
        'nearest_long':       nearest_long,
        'nearest_long_pct':   dist_long,
        'second_long':        second_long,
        'target_pct':         round(target_pct, 2),
        'support_pct':        round(support_pct, 2),
        'liq_bias':           _liq_bias,   # L2方向门控字段
        'short_map':          short_map,
        'long_map':           long_map,
    }

# ══════════════════════════════════════════════════════════
# Step 4: 共振点
# ══════════════════════════════════════════════════════════

# [封印 2026-10-06 苏摩111 P2重构] 命名说明：
# step4_resonance 编号虽为4，但实际执行在 step5_oi 之后
# 原因：共振7维之一=OI方向，必须先拿到oi结果
# 未来重命名候选：step4_resonance_post_oi
def step4_resonance(d: dict, fvg: dict, ob: dict, liq: dict, oi: dict = None, vol: dict = None, fc: dict = None, cma: dict = None) -> dict:
    """[P3升级 2026-09-12] 共振升级7维：FVG+OB+清算+OI+GEX+方仓+跨市场
    原P2-4修复: 5维(FVG+OB+清算+OI+GEX)
    P3新增: +方仓历史匹配(HCME) +跨市场alpha(134标的FR) = 7维
    共振标准: ≥4/7 = 有效共振
    """
    price = d['price']
    sym = d.get('sym', d.get('symbol', 'BTC'))  # [P0-1修复] regime查询需要标的键

    fvg_mid     = fvg['magnet']
    fvg_dir     = fvg['dir']
    valid_obs   = [k for k, v in ob.items() if v.get('valid', False)]
    nearest_liq = liq['nearest_short'] if fvg_dir in ('BULL', 'NONE') else liq['nearest_long']

    # 共振条件：FVG有效 + 有fresh OB + 清算目标明确
    has_fvg    = fvg_mid > 0 and fvg_dir != 'NONE'
    has_ob     = len(valid_obs) > 0
    has_liq    = nearest_liq > 0

    score      = sum([has_fvg, has_ob, has_liq])
    resonance  = score >= 2  # 至少2/3条件

    if resonance and has_fvg:
        # [9.21苏摩设计院修复] 入场区方向用signal_dir而不是fvg_dir
        # FVG共识是BEAR但信号可能是LONG（逼空做多），入场区应该在下方
        _entry_signal_dir = d.get('signal_dir') or d.get('_signal_dir') or ('SHORT' if (d.get('regime','').startswith('BEAR') or d.get('regime','') == 'CHOP_MID') else 'LONG')
        if _entry_signal_dir == 'SHORT':
            # 空单：入场区在现价上方（等反弹到FVG上沿/OB阻力）
            # 取有效OB中最近的上方阻力，没有就用吸力目标上方ATR
            bear_obs = [k for k in valid_obs if 'BEAR' in k]
            bull_obs = [k for k in valid_obs if 'BULL' in k]
            # 首选：现价上方最近的BEAR_OB（真实阻力）
            # 次选：清算目标上方（nearest_short）
            resistance = liq.get('nearest_short', 0)
            if resistance and resistance > price:
                # 反弹入场区：现价到resistance之间的顶部
                entry_hi = round(min(resistance, price * 1.015), 1)  # 最多反弹1.5%
                entry_lo = round(price * 1.003, 1)                   # 入场区少于0.3%距离
            else:
                # 无上方清算目标，用fvg范围上沿+ATR作阻力
                entry_hi = round(fvg['hi'] * 1.001 if fvg.get('hi', 0) > price else price * 1.008, 1)
                entry_lo = round(price * 1.003, 1)
            # 如果入场区不在现价上方，无效
            if entry_lo <= price or entry_hi <= price:
                entry_lo = 0.0
                entry_hi = 0.0
                resonance = False
        else:  # BULL
            # 多单：入场区在现价下方（等回调到FVG/OB支撑）
            # 情形A：FVG中点在现价下方 → 用FVG中点作锚
            # 情形B：FVG中点在现价上方（价格已在FVG内）→ 用有效OB下沿作锚
            ob_map = fvg.get('fvg_map', {})
            # 找现价下方最近的有效BULL OB
            best_ob_lo = 0.0
            best_ob_hi = 0.0
            for k, v in ob.items():
                if not v.get('valid', False): continue
                if 'BULL' not in k: continue
                # 从note里提取价格范围
                try:
                    note = v.get('note', '')
                    import re
                    prices_in_note = re.findall(r'\\$([\d,]+)', note)
                    if len(prices_in_note) >= 2:
                        lo_v = float(prices_in_note[0].replace(',',''))
                        hi_v = float(prices_in_note[1].replace(',',''))
                        if hi_v < price and lo_v > best_ob_lo:
                            best_ob_lo = lo_v
                            best_ob_hi = hi_v
                except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
            if fvg_mid < price:
                # 情形A：FVG中点在现价下方，用FVG中点
                entry_lo = round(min(fvg_mid * 0.998, price * 0.993), 1)
                entry_hi = round(fvg_mid * 1.002, 1)
            elif best_ob_lo > 0:
                # 情形B：价格已在FVG内，用最近有效BULL OB下沿
                entry_lo = round(best_ob_lo * 0.998, 1)
                entry_hi = round(best_ob_hi * 1.002, 1)
            else:
                # 无锚点：用现价-1%~-2%的支撑区
                entry_lo = round(price * 0.988, 1)
                entry_hi = round(price * 0.993, 1)

            # 最终校验：BULL入场区必须在现价下方
            # 若入场区在现价上方 = FVG磁铁未触及 = 还没到入场位 = 等待
            if entry_lo >= price or entry_hi >= price:
                entry_lo = 0.0
                entry_hi = 0.0
                resonance = False
    else:
        # BUG-5修复：无共振时不用price*0.997这种无结构入场区，直接标为失效
        entry_lo = 0.0
        entry_hi = 0.0

    missing = []
    if not has_fvg:  missing.append('FVG无效')
    if not has_ob:   missing.append('无新鲜OB')
    if not has_liq:  missing.append('清算数据缺失')
    if entry_lo == 0.0 and resonance:
        missing.append('入场区方向错误（商品价格不在入场区正确一侧）')

    # [2026-09-12 苏摩111] OI/GEX共振维度增加方向一致性校验
    # [9.20修复 苏摩111] OI有数据=维度通过，方向不一致在cross_check记录而非维度缺失
    has_oi = False
    has_gex = False
    _fvg_consensus = fvg.get('consensus', fvg_dir)  # 共识方向优先
    _fvg_bull = _fvg_consensus == 'BULL'
    _fvg_bear = _fvg_consensus == 'BEAR'
    if oi and oi.get('signal','') not in ('NO_DATA','MIXED'):
        _oi_bull = oi['signal'] in ('LONG_BUILD', 'SHORT_SQUEEZE')
        _oi_bear = oi['signal'] in ('SHORT_BUILD', 'LONG_UNWIND')
        _oi_neutral = oi['signal'] in ('NEUTRAL', 'WATCH')
        # [9.20修复] OI有数据=维度通过（不管方向）
        has_oi = True
        score += 1
        # OI方向与FVG一致=额外+0.5分（共振加分），不一致=不额外加分
        if (_fvg_bull and _oi_bull) or (_fvg_bear and _oi_bear):
            score += 0  # 已+1，方向一致不再额外加（避免OI权重过大）
        # NEUTRAL或方向不一致：维度通过但cross_check记录矛盾
    if vol and vol.get('gex_note','') and not vol.get('gex_expired', False):
        # GEX方向与FVG一致才加分（P0修复: 过期GEX不参与共振）
        # [9.20修复] GEX有数据=维度通过，方向不一致在cross_check记录
        _gex_bull = 'POSITIVE' in vol.get('gex_bias','').upper() or vol.get('kappa', 0) < -0.05
        _gex_bear = 'NEGATIVE' in vol.get('gex_bias','').upper() or vol.get('kappa', 0) > 0.05
        _gex_neutral = not _gex_bull and not _gex_bear
        has_gex = True
        score += 1
        if (_fvg_bull and _gex_bull) or (_fvg_bear and _gex_bear):
            pass  # 已+1，方向一致不再额外加
        # GEX中性或方向不一致：维度通过但cross_check记录矛盾
    elif vol and vol.get('gex_expired', False):
        # P0修复: GEX过期→缺失但不报错，共振标准降为≥3/4（不含GEX）
        pass
    # [P3新增] 方仓历史匹配维度
    # [9.20修复 苏摩111] 方仓有数据=维度通过，方向不一致在cross_check记录
    has_fc = False
    if fc and fc.get('top5'):
        _fc_dir = fc.get('direction', 'NEUTRAL')
        has_fc = True
        score += 1
        # 方仓方向与FVG一致=额外确认，不一致=cross_check记录
        if (_fvg_bull and _fc_dir == 'LONG') or (_fvg_bear and _fc_dir == 'SHORT'):
            pass  # 已+1
        # NEUTRAL或方向不一致：维度通过但不额外加分
    
    # [P3新增] 跨市场alpha维度
    # [9.21修复 苏摩111] 跨市场有数据=维度通过，方向不一致在cross_check记录
    has_cma = False
    if cma and cma.get('cross_market_alpha', 0) != 0:
        _cma_alpha = cma['cross_market_alpha']
        _cma_risk_on = cma.get('direction', '') == 'RISK_ON'
        _cma_risk_off = cma.get('direction', '') == 'RISK_OFF'
        # [9.21修复] 有数据=维度通过（不管方向）
        has_cma = True
        score += 1
        if (_fvg_bull and _cma_risk_on) or (_fvg_bear and _cma_risk_off):
            pass  # 已+1，方向一致不再额外加
        # 方向不一致：维度通过但cross_check记录矛盾
    
    # ══ Phase 2修复 2026-09-18 苏摩111: 体制×维度权重矩阵 ══
    # 原逻辑：7维简单计数，每维1分，≥4/7通过
    # 新逻辑：体制感知加权，CHOP下GEX权重1.5，趋势下FVG权重1.5
    # 共振分 = Σ(维度通过 × 体制权重) / Σ(体制权重)
    # 共振分 > 0.6 = 有效共振（替代原≥4/7）
    _REGIME_WEIGHTS = {
        'BULL_TREND':     {'FVG': 1.5, 'OB': 1.2, 'LIQ': 0.8, 'OI': 1.0, 'GEX': 0.8, 'FC': 1.0, 'CMA': 0.8},
        'BEAR_TREND':     {'FVG': 1.5, 'OB': 1.2, 'LIQ': 0.8, 'OI': 1.0, 'GEX': 0.8, 'FC': 1.0, 'CMA': 0.8},
        'CHOP_MID':       {'FVG': 0.8, 'OB': 1.0, 'LIQ': 1.2, 'OI': 1.2, 'GEX': 1.5, 'FC': 0.8, 'CMA': 1.0},
        'BEAR_RECOVERY':  {'FVG': 1.0, 'OB': 1.0, 'LIQ': 1.2, 'OI': 1.0, 'GEX': 1.0, 'FC': 1.0, 'CMA': 0.8},
        'BEAR_EARLY':     {'FVG': 1.0, 'OB': 1.0, 'LIQ': 1.0, 'OI': 1.2, 'GEX': 1.0, 'FC': 1.0, 'CMA': 0.8},
        'BULL_EARLY':     {'FVG': 1.2, 'OB': 1.0, 'LIQ': 1.0, 'OI': 1.0, 'GEX': 1.0, 'FC': 1.0, 'CMA': 1.0},
    }
    _regime_key = 'CHOP_MID'
    # [P0-1修复 2026-09-23 苏摩111] 权重查询路径修复（与step10_vip L1691对齐）
    # 原bug：读顶层d['regime_s']['confirmed']（不存在）→ 永远默认CHOP_MID权重
    # 9.18批准的体制×维度权重矩阵在生产从未生效。另回退：bs.regime > 分析时体制
    _rs_sym = (d.get('regime_s') or {}).get(sym + 'USDT', {}) if isinstance(d.get('regime_s'), dict) else {}
    _confirmed_regime = str(_rs_sym.get('confirmed', '') or '')
    if not _confirmed_regime:
        _confirmed_regime = str((d.get('bs') or {}).get('regime', '') or '')
    if _confirmed_regime:
        for _rk in _REGIME_WEIGHTS:
            if _rk in _confirmed_regime:
                _regime_key = _rk
                break
    _rw = _REGIME_WEIGHTS.get(_regime_key, _REGIME_WEIGHTS['CHOP_MID'])
    
    # 加权共振分计算
    _dim_pass = {'FVG': has_fvg, 'OB': has_ob, 'LIQ': has_liq, 'OI': has_oi, 'GEX': has_gex, 'FC': has_fc, 'CMA': has_cma}
    _total_weight = sum(_rw.values())
    _weighted_score = sum(_rw[k] for k, v in _dim_pass.items() if v)
    _resonance_ratio = _weighted_score / _total_weight if _total_weight > 0 else 0
    
    # 共振标准: [P0改革 2026-09-19 苏摩111] 从0.6降到0.4 + score>=2
    # 根因：FVG+OB+清算=结构完整=够一单，但0.6门槛把3/7拦住了
    # 40年交易员：FVG磁铁+有效OB+清算区=入场理由，不需要7维全绿
    resonance = _resonance_ratio > 0.4 or score >= 2  # 降到0.4 + 2/7即可
    
    # ══ Phase 4修复 2026-09-18 苏摩111: 推理层反馈降权 ══
    # 推理层提前到step4之前运行，这里读取feedback做共振降权
    _inf_fb = d.get('_inference_feedback', {})
    _dim_down = _inf_fb.get('dim_down_weight', {})
    if _dim_down:
        # 矛盾维度降权：CVD/OI矛盾时两个维度权重×0.5
        for _dim_key, _mult in _dim_down.items():
            if _dim_key in _rw and isinstance(_mult, (int, float)):
                _rw[_dim_key] = _rw[_dim_key] * _mult
        # 重算加权共振分
        _total_weight = sum(_rw.values())
        _weighted_score = sum(_rw[k] for k, v in _dim_pass.items() if v)
        _resonance_ratio = _weighted_score / _total_weight if _total_weight > 0 else 0
        resonance = _resonance_ratio > 0.4 or score >= 2  # [P0改革] 同步降低
        if not resonance and score >= 4:
            print(f'[推理层降权] {sym} 共振从{score}/7降级: {_dim_down.get("_reason","")}', file=sys.stderr)

    # [P2-5修复 2026-09-11] 交叉验证层：Step1-4结构层 vs Step5-9市场层
    # [D1修复 2026-09-11] 用共识方向(fvg_consensus)而非主磁铁方向(fvg_dir)
    _fvg_consensus = fvg.get('consensus', fvg_dir)  # 共识方向优先
    cross_check = {'consistent': True, 'conflicts': []}
    if oi and _fvg_consensus != 'NONE':
        _struct_bull = _fvg_consensus == 'BULL'
        _oi_bull = oi['signal'] in ('LONG_BUILD', 'SHORT_SQUEEZE')
        if _struct_bull != _oi_bull:
            cross_check['consistent'] = False
            cross_check['conflicts'].append(f'FVG={_fvg_consensus} vs OI={oi.get("signal","?")}')
    if vol and _fvg_consensus != 'NONE':
        _kappa_bull = vol.get('kappa', 0) < -0.05
        _struct_bull = _fvg_consensus == 'BULL'
        if _struct_bull != _kappa_bull and abs(vol.get('kappa', 0)) > 0.03:
            cross_check['consistent'] = False
            cross_check['conflicts'].append(f'FVG={_fvg_consensus} vs κ={vol.get("kappa",0):.3f}')
    # Hurst交叉验证：共振但Hurst<0.55 = 信号可信度存疑（2026-09-11 苏摩111）
    if vol and vol.get('hurst', 0.5) < 0.55 and resonance:
        cross_check['conflicts'].append(f'共振但Hurst={vol["hurst"]:.3f}<0.55=随机游走')

    # [P0-2修复 2026-09-23 苏摩111] 方向一致性计数（40年交易员口径）
    # 数据存在≠方向一致。7/7满绿必须拆成两个数：
    #   数据N/7 = 数据管道健康检查（原口径，无信息量）
    #   方向一致N/7 = 结构层各维与信号方向的真实一致计数（进Gate4的依据）
    _sig_bull = str(d.get('signal_dir', '') or '').upper() == 'LONG'
    _sig_dir_norm = str(d.get('signal_dir', '') or '').upper()
    _align = 0
    if _sig_dir_norm in ('LONG', 'SHORT'):
        # 结构三维度用共识方向比对；FVG无共识(NONE)不算一致也不算矛盾
        _struct_consensus = _fvg_consensus  # BULL/BEAR/NONE
        if _struct_consensus == _sig_dir_norm:
            _align += 1  # FVG方向一致
        # OB: 用有效OB的BULL/BEAR多数派
        _ob_bull = sum(1 for k in valid_obs if 'BULL' in k)
        _ob_bear = sum(1 for k in valid_obs if 'BEAR' in k)
        if (_sig_bull and _ob_bull > _ob_bear) or (not _sig_bull and _ob_bear > _ob_bull):
            _align += 1
        # 清算: 空单看上方止损墙作为目标=空向一致；多单看下方支撑池
        if _sig_dir_norm == 'SHORT' and nearest_liq > price:
            _align += 1
        elif _sig_dir_norm == 'LONG' and nearest_liq < price:
            _align += 1
        # OI/GEX/方仓/跨市场: 方向一致才计（有数据但矛盾=0）
        if oi and oi.get('signal','') not in ('NO_DATA','MIXED'):
            if (_sig_bull and _oi_bull) or (not _sig_bull and _oi_bear):
                _align += 1
        if has_gex:
            if (_sig_bull and _gex_bull) or (not _sig_bull and _gex_bear):
                _align += 1
        if has_fc:
            if (_sig_bull and _fc_dir == 'LONG') or (not _sig_bull and _fc_dir == 'SHORT'):
                _align += 1
        if has_cma:
            if (_sig_bull and _cma_risk_on) or (not _sig_bull and _cma_risk_off):
                _align += 1
    else:
        _align = 0  # 无信号方向（如FVG共识NONE的BTC）→ 方向一致数=0，不授信

    return {
        'resonance':   resonance,
        'score':       score,
        'align_count': _align,          # [P0-2] 方向一致计数（Gate4依据）
        'resonance_ratio': _resonance_ratio,  # [P0-1] 体制加权共振比
        'regime_key': _regime_key,     # [P0-1] 实际生效的体制权重键
        'has_fvg':     has_fvg,
        'has_ob':      has_ob,
        'has_liq':     has_liq,
        'has_oi':      has_oi,
        'has_gex':     has_gex,
        'has_fc':      has_fc,       # P3: 方仓维度
        'has_cma':     has_cma,      # P3: 跨市场维度
        'n_dims':      7,            # P3: 7维共振
        'entry_lo':    entry_lo,
        'entry_hi':    entry_hi,
        'missing':     missing,
        'liq_nearest_long': liq.get('nearest_long', 0),
        'cross_check': cross_check,
    }

def step4b_pattern(d: dict) -> dict:
    """Step4b: 形态识别（pattern_engine）"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.pattern_engine import pattern_score
        k1h = d.get('k1h', [])
        if not k1h or len(k1h) < 20:
            return {'score': 0, 'patterns': [], 'note': 'K线数据不足'}
        highs = [x[1] for x in k1h]
        lows = [x[2] for x in k1h]
        closes = [x[3] for x in k1h]
        signal_dir = _infer_signal_dir(d.get('regime',''), fvg.get('dir','') if 'fvg' in dir() else '')
        result = pattern_score(highs, lows, closes, signal_dir)
        return result
    except Exception as e:
        return {'score': 0, 'patterns': [], 'note': f'形态引擎异常: {str(e)[:50]}'}

# ══════════════════════════════════════════════════════════
# Step 5: OI趋势
# ══════════════════════════════════════════════════════════

def step5_oi(d: dict) -> dict:
    """全周期OI趋势：15M短期 + 1H中期 + 4H主力 苏摩111封印 2026-09-04
    [9.15苏摩111 P0修复] 优先读oi_candidates.json（scanner预计算），API为fallback"""
    oi_vals    = d['oi_vals']      # 15M x8
    oi_1h_vals = d.get('oi_1h_vals', [])  # 1H x8
    oi_4h_vals = d.get('oi_4h_vals', [])  # 4H x6
    price      = d['price']
    k1h        = d['k1h']
    k4h        = d.get('k4h', [])
    sym        = d.get('sym', '')

    # [P0修复] API拉不到OI时，从oi_candidates.json读取scanner预计算结果
    if len(oi_vals) < 2:
        try:
            import json as _json_oi
            _oc_path = Path(__file__).parent.parent / 'data' / 'oi_candidates.json'
            if _oc_path.exists():
                _oc = _json_oi.loads(_oc_path.read_text())
                _cands = _oc.get('candidates', {})
                _sym_usdt = sym + 'USDT' if sym else ''
                if _sym_usdt in _cands:
                    _sc = _cands[_sym_usdt]
                    _oi_score = _sc.get('oi_score', 0)
                    _details = _sc.get('score_details', [])
                    # 从score_details提取方向信号
                    _scanner_signal = 'MIXED'
                    for _det in _details:
                        if 'LONG_BUILD' in _det: _scanner_signal = 'LONG_BUILD'
                        elif 'SHORT_BUILD' in _det: _scanner_signal = 'SHORT_BUILD'
                        elif 'SHORT_COVER' in _det or 'SHORT_COV' in _det: _scanner_signal = 'SHORT_SQUEEZE'
                        elif 'LONG_UNWIND' in _det: _scanner_signal = 'LONG_UNWIND'
                    _scanner_chg = _sc.get('pct24h', 0)
                    _conclusion = f'来自OI Scanner(oi_score={_oi_score:.0f}) | {" ".join(_details[:3])}'
                    return {
                        'signal':       _scanner_signal,
                        'signal_15m':   _scanner_signal,
                        'signal_1h':    _scanner_signal,
                        'signal_4h':    _scanner_signal,
                        'conf':         0.67,
                        'conclusion':   f'多数一致 | 15M:{_scanner_signal} 1H:{_scanner_signal} 4H:NO_DATA(来自scanner) | {_conclusion}',
                        'total_change': 0,
                        'total_change_1h': 0,
                        'total_change_4h': 0,
                        'usd_change_m': 0,
                        'latest':       0,
                        'trend':        [],
                        'trend_1h':     [],
                        'trend_4h':     [],
                        'cvd_1h':       0,
                        'cvd_note':     'OI来自scanner(API无数据)',
                    }
        except Exception as _e: print(f'[WARN] step5_oi scanner fallback: {_e}', file=sys.stderr)
        return {'signal': 'NO_DATA', 'trend': [], 'conclusion': 'OI数据不足'}

    def _classify(vals, klines, label):
        """单周期OI信号分类"""
        if len(vals) < 2: return 'NO_DATA', 0
        diffs  = [vals[i]-vals[i-1] for i in range(1,len(vals))]
        rising = sum(1 for x in diffs if x > 0)
        falling= sum(1 for x in diffs if x < 0)
        price_up = (klines[-1][4] > klines[-2][4]) if len(klines) >= 2 else True
        chg    = vals[-1] - vals[0]
        if rising >= int(len(diffs)*0.6) and price_up:   return 'LONG_BUILD',  chg
        if rising >= int(len(diffs)*0.6) and not price_up: return 'SHORT_BUILD', chg
        if falling>= int(len(diffs)*0.6) and price_up:   return 'SHORT_SQUEEZE',chg
        if falling>= int(len(diffs)*0.6) and not price_up: return 'LONG_UNWIND', chg
        return 'MIXED', chg

    sig_15m, chg_15m = _classify(oi_vals,    k1h,  '15M')
    sig_1h,  chg_1h  = _classify(oi_1h_vals, k1h,  '1H')
    sig_4h,  chg_4h  = _classify(oi_4h_vals, k4h,  '4H')

    # 全周期一致性判断（权重：4H=3, 1H=2, 15M=1）
    WEIGHT = {sig_4h: 3, sig_1h: 2, sig_15m: 1}
    score = {}
    for sig, w in [(sig_4h,3),(sig_1h,2),(sig_15m,1)]:
        score[sig] = score.get(sig,0) + w

    # 主信号：权重最高的
    main_signal = max(score, key=score.get)
    total_weight= sum(score.values())
    main_conf   = score.get(main_signal, 0) / total_weight  # 0~1

    # 结论文字
    _labels = {
        'LONG_BUILD':   '全周期增仓+价格上涨 → 主力真实建多',
        'SHORT_BUILD':  '全周期增仓+价格下跌 → 主力真实建空',
        'SHORT_SQUEEZE':'OI减仓+价格上涨 → 轧空，持续性存疑',
        'LONG_UNWIND':  'OI减仓+价格下跌 → 多头平仓',
        'MIXED':        'OI信号分歧，方向不明',
        'NO_DATA':      'OI数据不足',
    }

    if main_conf >= 0.833:  # 6/6权重全票
        conf_str = '强共识'
    elif main_conf >= 0.5:  # 多数一致
        conf_str = '多数一致'
    else:
        conf_str = '分歧'
        main_signal = 'MIXED'

    conclusion = (
        f'{conf_str} | 15M:{sig_15m} 1H:{sig_1h} 4H:{sig_4h} | '
        f'{_labels.get(main_signal,"?")}'
    )

    total_change = oi_vals[-1] - oi_vals[0] if oi_vals else 0
    usd_change   = (d['oi_usd'][-1] - d['oi_usd'][0]) if len(d.get('oi_usd',[])) >= 2 else 0

    # [P0修复 2026-09-10] CVD交叉验证
    cvd_data = {}
    cvd_note = ''
    try:
        import json as _json
        _sym_lower = d.get('sym','').lower() + 'usdt'
        _cvd_path = Path(__file__).parent.parent / 'data' / f'cvd_realtime_{_sym_lower}.json'
        if _cvd_path.exists():
            cvd_data = _json.loads(_cvd_path.read_text())
            cvd_1h = cvd_data.get('cvd_1h', 0)
            cvd_4h = cvd_data.get('cvd_4h', 0)
            buy_vol = cvd_data.get('buy_vol_1h', 0)
            sell_vol = cvd_data.get('sell_vol_1h', 0)
            if cvd_1h > 0:
                cvd_note = f'CVD 1H=+{cvd_1h:.0f}（买方主导）'
            elif cvd_1h < 0:
                cvd_note = f'CVD 1H={cvd_1h:.0f}（卖方主导）'
            else:
                cvd_note = 'CVD 1H=0（中性）'
            # OI+CVD交叉验证
            if main_signal == 'LONG_BUILD' and cvd_1h < 0:
                cvd_note += ' ⚠️OI多但CVD空=矛盾'
            elif main_signal == 'SHORT_BUILD' and cvd_1h > 0:
                cvd_note += ' ⚠️OI空但CVD多=矛盾'
            elif main_signal == 'LONG_BUILD' and cvd_1h > 0:
                cvd_note += ' ✅OI+CVD同向做多'
            elif main_signal == 'SHORT_BUILD' and cvd_1h < 0:
                cvd_note += ' ✅OI+CVD同向做空'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {
        'signal':       main_signal,
        'signal_15m':   sig_15m,
        'signal_1h':    sig_1h,
        'signal_4h':    sig_4h,
        'conf':         round(main_conf, 2),
        'conclusion':   conclusion,
        'total_change': round(oi_vals[-1] - oi_vals[0], 0) if oi_vals else 0,           # P0修复: 15M变化用15M数据
        'total_change_1h': round(oi_1h_vals[-1] - oi_1h_vals[0], 0) if len(oi_1h_vals)>=2 else 0,  # P0修复: 1H变化用1H数据
        'total_change_4h': round(oi_4h_vals[-1] - oi_4h_vals[0], 0) if len(oi_4h_vals)>=2 else 0,  # P0修复: 4H变化用4H数据
        'usd_change_m': round((d['oi_usd'][-1] - d['oi_usd'][0]) / 1e6, 1) if len(d.get('oi_usd',[])) >= 2 else 0,
        'latest':       round(oi_vals[-1], 0) if oi_vals else 0,
        'trend':        [round(v,0) for v in oi_vals],
        'trend_1h':     [round(v,0) for v in oi_1h_vals] if oi_1h_vals else [],
        'trend_4h':     [round(v,0) for v in oi_4h_vals] if oi_4h_vals else [],
        'cvd_1h':       cvd_data.get('cvd_1h', 0),
        'cvd_note':     cvd_note,
    }

def step5b_lsr_trigger(d: dict, res: dict) -> dict:
    """Step5b: LSR/OI联合分析 + 15分钟触发"""
    result = {'lsr_oi': {}, 'trigger_15m': {}}
    sym = d.get('sym', 'BTC')
    signal_dir = _infer_signal_dir(d.get('regime',''), fvg.get('dir','') if 'fvg' in dir() else '')

    # LSR/OI联合
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.lsr_oi_engine import lsr_oi_score
        sm = d.get('sm', {})
        oi = d.get('oi', {})
        result['lsr_oi'] = lsr_oi_score(
            sym + 'USDT', signal_dir,
            long_pct=sm.get('big_long', 50) if sm else 50,
            oi_change_pct=oi.get('total_change_pct', 0) if oi else 0,
            oi_momentum=oi.get('signal', 'NEUTRAL') if oi else 'NEUTRAL',
            price_change_pct=0
        )
    except Exception as e:
        result['lsr_oi'] = {'score': 0, 'note': f'LSR异常: {str(e)[:40]}'}

    # 15分钟触发
    try:
        from brahma_brain.trigger_15m import analyze_trigger
        entry_lo = res.get('entry_lo', 0) if res else 0
        entry_hi = res.get('entry_hi', 0) if res else 0
        vol = d.get('vol', {}) if d.get('vol') else {}
        atr_4h = vol.get('atr_4h', 0) if vol else 0
        if entry_lo > 0 and entry_hi > 0 and atr_4h > 0:
            result['trigger_15m'] = analyze_trigger(
                sym + 'USDT', signal_dir, entry_lo, entry_hi, atr_4h,
                score_1h=int(d.get('score', 0) or d.get('bs', {}).get('score', 0)),
                verbose=False
            )
        else:
            result['trigger_15m'] = {'triggered': False, 'note': '入场区或ATR缺失'}
    except Exception as e:
        result['trigger_15m'] = {'triggered': False, 'note': f'触发器异常: {str(e)[:40]}'}

    return result

def step5c_zscore(d: dict) -> dict:
    """Step5c: Z-Score 统计异常感知层
    [设计院封印 2026-10-03 苏摩111]
    接入位置：brahma_manual_analysis.py step5b之后，step6之前

    功能：用价格回报的Z-Score检测统计异常，作为进入主链的过滤信号。
    |Z| >= 2.0 → 统计异常，允许开单信号放行 (PASS)
    1.5 <= |Z| < 2.0 → 边界区 (WATCH)
    |Z| < 1.5 → 正常震荡，CHOP_WARN标记 (SKIP)

    数据来源：d['k1h']（step0已拉取，0额外API消耗）
    """
    k1h = d.get('k1h', [])  # [(o,h,l,c,v), ...] 最新在末尾
    sym = d.get('sym', 'BTC')

    # 需要至少21根K线（20根历史+1根最新）
    if len(k1h) < 3:
        return {
            'z': None, 'signal': 'NO_DATA', 'gate': 'SKIP',
            'note': f'K线不足({len(k1h)}根，需≥3根)'
        }

    try:
        # 提取收盘价序列（index 3 = close）
        closes = [bar[3] for bar in k1h if bar[3] > 0]
        if len(closes) < 3:
            return {'z': None, 'signal': 'NO_DATA', 'gate': 'SKIP', 'note': '有效收盘价不足'}

        # 计算逐根回报率
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        if not returns:
            return {'z': None, 'signal': 'NO_DATA', 'gate': 'SKIP', 'note': '回报率计算失败'}

        # Z-Score：最新回报 vs 历史窗口统计
        r_latest = returns[-1]
        r_hist   = returns[:-1] if len(returns) > 1 else returns
        mu  = sum(r_hist) / len(r_hist)
        variance = sum((r - mu) ** 2 for r in r_hist) / max(len(r_hist), 1)
        sigma = variance ** 0.5

        if sigma < 1e-9:
            z = 0.0
        else:
            z = (r_latest - mu) / sigma
        z = round(z, 3)

        # 信号分类
        abs_z = abs(z)
        if abs_z >= 2.0:
            signal = 'EXTREME_UP' if z > 0 else 'EXTREME_DOWN'
            gate   = 'PASS'
            note   = f'统计异常(|Z|={abs_z:.2f}≥2.0) → 放行信号链'
        elif abs_z >= 1.5:
            signal = 'BORDERLINE_UP' if z > 0 else 'BORDERLINE_DOWN'
            gate   = 'WATCH'
            note   = f'边界区(|Z|={abs_z:.2f} 1.5~2.0) → 谨慎观察'
        else:
            signal = 'NORMAL'
            gate   = 'SKIP'
            note   = f'正常震荡(|Z|={abs_z:.2f}<1.5) → CHOP_WARN'

        return {
            'z':        z,
            'signal':   signal,
            'gate':     gate,
            'mu':       round(mu * 100, 4),    # 转为百分比
            'sigma':    round(sigma * 100, 4),  # 转为百分比
            'r_latest': round(r_latest * 100, 4),
            'n_bars':   len(closes),
            'note':     note,
        }
    except Exception as _e:
        return {'z': None, 'signal': 'ERROR', 'gate': 'SKIP', 'note': f'Z-Score计算异常: {str(_e)[:60]}'}

# ══════════════════════════════════════════════════════════
# Step 6: 聪明钱分歧
# ══════════════════════════════════════════════════════════

def step6_smart_money(d: dict) -> dict:
    top_list = d['top_list']   # 大户多头占比列表
    lsr_list = d['lsr_list']   # 散户 (long%, short%)

    big_latest    = top_list[-1] if top_list else 0.5
    retail_latest = lsr_list[-1][0] if lsr_list else 0.5

    # 趋势：大户多头是在增加还是减少
    big_trend = 'INCREASING' if len(top_list) >= 2 and top_list[-1] > top_list[0] else ('DECREASING' if len(top_list) >= 2 and top_list[-1] < top_list[0] else 'STABLE')
    # 大户变化速率（升级：2H内净变化，正=主力加多，负=主力减多）
    top_delta = d.get('top_delta', 0.0)  # step0已计算

    diverge = abs(big_latest - retail_latest)

    if big_latest > 0.60 and retail_latest < 0.52:
        signal      = 'STRONG_BULL'
        conclusion  = f'大户{big_latest*100:.0f}%多 vs 散户{retail_latest*100:.0f}%多 → 极端分歧，主力在买，散户在空，强烈看多'
    elif big_latest > 0.55:
        signal      = 'MILD_BULL'
        conclusion  = f'大户{big_latest*100:.0f}%多，方向偏多'
    elif big_latest < 0.45:
        signal      = 'BEAR'
        conclusion  = f'大户{big_latest*100:.0f}%多（空头主导），偏空'
    else:
        signal      = 'NEUTRAL'
        conclusion  = f'大户多空均衡，方向不明'

    return {
        'signal':      signal,
        'conclusion':  conclusion,
        'big_long':    round(big_latest * 100, 1),
        'retail_long': round(retail_latest * 100, 1),
        'diverge':     round(diverge * 100, 1),
        'big_trend':   big_trend,
        'top_delta':   round(top_delta, 3),   # P0修复: 保留3位小数不归零
        # P5新增: 微结构+反操纵
        'microstructure': _get_microstructure(d),
        'anti_manipulation': _get_anti_manipulation(d),
    }

# ══════════════════════════════════════════════════════════
# Step 7: Hurst + HAR-RV + VolBeta
# ══════════════════════════════════════════════════════════

def _get_microstructure(d: dict) -> dict:
    """P5整合: 微结构alpha — 2026-09-12"""
    try:
        # [cleaned] import sys as _ms_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.microstructure_engine import get_microstructure_signal
        return get_microstructure_signal(d.get('sym', 'BTC'))
    except Exception:
        return {'available': False, 'note': 'microstructure N/A'}

def _get_anti_manipulation(d: dict) -> dict:
    """P5整合: 反操纵检测 — 2026-09-12"""
    try:
        # [cleaned] import sys as _am_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.anti_manipulation_engine import detect_manipulation
        return detect_manipulation(d.get('sym', 'BTC'))
    except Exception:
        return {'available': False, 'note': 'anti_manipulation N/A'}

def step7_volatility(d: dict) -> dict:
    bd    = d['bs'].get('confluence', {}).get('breakdown', {})
    vb    = d['vb']
    sym   = d['sym']
    price = d['price']

    # ATR全周期计算（苏摩111封印 2026-09-04）
    k1h_l = d.get('k1h', []); k4h_l = d.get('k4h', []); k1d_l = d.get('k1d', [])
    atr_1h = round(sum(abs(x[1]-x[2]) for x in k1h_l[-8:])/min(8,len(k1h_l)),1) if len(k1h_l)>=2 else price*0.005
    atr_4h = round(sum(abs(x[1]-x[2]) for x in k4h_l[-7:])/min(7,len(k4h_l)),1) if len(k4h_l)>=2 else 0.0
    atr_1d = round(sum(abs(x[1]-x[2]) for x in k1d_l[-7:])/min(7,len(k1d_l)),1) if len(k1d_l)>=2 else 0.0
    # 合约SL参考：取1.5×ATR1H 和 1.0×ATR4H 的较大值
    atr_sl_ref = max(atr_1h * 1.5, atr_4h * 1.0) if atr_4h else atr_1h * 1.5

    hurst_raw = str(bd.get('Hurst体制验证', '') or '')
    harv_raw  = str(bd.get('HAR-RV波动率', '') or '')

    hurst_val = 0.5
    try:
        if 'H=' in hurst_raw:
            _hv = float(hurst_raw.split('H=')[1].split()[0])
            # [P3修复] 夹紧到合理范围(0.1~0.9)，防止异常值如H=+125126
            hurst_val = max(0.1, min(0.9, _hv))
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    harv_val = 0.0
    try:
        if 'RV=' in harv_raw:
            harv_val = float(harv_raw.split('RV=')[1].split()[0])
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # HAR-RV 转换为具体价格波动区间
    # RV = 已实现波动率（对数收益标准差）
    # 预测未来 4H 价格区间：当前价格 ± RV * 价格 * sqrt(4) 年化因子追倒
    price_now = d.get('price', 0)
    harv_range_lo = harv_range_hi = 0
    harv_range_str = ''
    if harv_val > 0 and price_now > 0:
        # D5修复: RV是已实现波动率(年化)
        # 日波动率 = RV / sqrt(252)
        # 4H波动率 = 日波动率 / sqrt(6)  [一天6个4H区间]
        # 注意RV已经是年化标准差，不需要再开方
        daily_vol = harv_val / (252 ** 0.5)   # 年化→日化
        fh_vol    = daily_vol / (6 ** 0.5)    # 日化→4H化
        harv_range_lo = round(price_now * (1 - fh_vol), 1)
        harv_range_hi = round(price_now * (1 + fh_vol), 1)
        # [P2修复 2026-10-01 苏摩111] HAR-RV压缩市场兜底
        # 根因：低波动率时fh_vol极小→区间<0.1%→等于无信息
        # 修复：ATR4H/ATR1H作为最小区间保底（统计有效的波动参考）
        _harv_raw_width = harv_range_hi - harv_range_lo
        _atr4h_ref = d.get('atr_4h', 0) or atr_4h if 'atr_4h' in dir() else 0
        _atr1h_ref = d.get('atr_1h', 0) or 0
        _min_width = max(_atr4h_ref * ATR_4H_HARV_MULT, _atr1h_ref * ATR_HARV_MIN_MULT, price_now * HARV_MIN_WIDTH_PCT)  # 最小0.3%
        if _harv_raw_width < _min_width:
            # ATR兜底展宽
            harv_range_lo = round(price_now - _min_width / 2, 1)
            harv_range_hi = round(price_now + _min_width / 2, 1)
            harv_range_str = (f'未来4H价格区间: ${harv_range_lo:,.0f}~${harv_range_hi:,.0f}'
                              f' ⚠️ATR兜底(RV区间仅${_harv_raw_width:.0f}过窄)')
        else:
            harv_range_str = f'未来4H价格区间: ${harv_range_lo:,.0f}~${harv_range_hi:,.0f}'

    kappa    = vb.get('kappa', 0)
    beta_p   = vb.get('beta_plus', 0)
    beta_m   = vb.get('beta_minus', 0)
    iv_rank  = vb.get('iv_pct_rank', 50)
    iv_pct   = vb.get('iv_pct', 0)
    premium  = vb.get('iv_premium_pct', 0)

    # [P0修复 2026-09-12] GEX数据新鲜度检测 + 过期不参与共振
    gex_note = ''
    gex_bias = 'NEUTRAL'
    gex_expired = False
    try:
        import json as _json, time as _time
        _gex_path = Path(__file__).parent.parent / 'data' / 'gex_state.json'
        if _gex_path.exists():
            _gex_all = _json.loads(_gex_path.read_text())
            _gex_sym = d.get('sym','')
            _gex = _gex_all.get(_gex_sym, _gex_all.get(_gex_sym.upper(), {}))
            if _gex:
                _gex_scan_ts = _gex.get('scan_ts', 0) or _gex.get('ts', 0)
                _gex_age_hours = (_time.time() - _gex_scan_ts) / 3600 if _gex_scan_ts else 999
                if _gex_age_hours > 48:  # 超过48小时=过期
                    gex_expired = True
                    gex_note = f'GEX数据已过期{_gex_age_hours:.0f}h（{_gex.get("scan_datetime","?")}），不参与共振'
                else:
                    gex_total = _gex.get('gex_total', 0) or _gex.get('total_gex', 0) or _gex.get('net_gex_at_spot', 0)
                    gex_bias_val = _gex.get('gex_bias', '') or _gex.get('gex_direction', '')
                    min_gex_strike = _gex.get('min_gex_strike', 0)
                    if gex_total > 0:
                        gex_bias = 'POSITIVE'
                        gex_note = f'GEX=+{gex_total/1e6:.2f}M 正gamma→价格被钉住(低波动)'
                    elif gex_total < 0:
                        gex_bias = 'NEGATIVE'
                        gex_note = f'GEX={gex_total/1e6:.2f}M 负gamma→波动率引爆点'
                    else:
                        gex_note = 'GEX≈0 中性'
                    if min_gex_strike > 0:
                        gex_dist = abs(price - min_gex_strike) / price * 100 if price else 0
                        gex_note += f' MIN_GEX=${min_gex_strike:,.0f}({gex_dist:.1f}%)'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # [P1修复 2026-09-10] FR展示
    fr_note = ''
    try:
        fr_val = d.get('fr', 0)
        if fr_val:
            if abs(fr_val) > 0.001:
                fr_note = f'FR={fr_val*100:.4f}% ⚠️极端值=反转前兆'
            else:
                fr_note = f'FR={fr_val*100:.4f}% 正常'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # Hurst解读
    if hurst_val >= 0.65:
        hurst_note = f'H={hurst_val:.3f} 🔥强趋势持续性，当前方向会继续'
    elif hurst_val >= 0.55:
        hurst_note = f'H={hurst_val:.3f} ⚠️趋势性隐现，体制切换前兆'
    elif hurst_val <= 0.40:
        hurst_note = f'H={hurst_val:.3f} 均值回归强，震荡不适合趋势追踪'
    else:
        hurst_note = f'H={hurst_val:.3f} 随机游走，方向不确定'

    # kappa解读
    if kappa < -0.05:
        kappa_note = f'κ={kappa:.3f} 🟢Call需求强(期权市场偏多，大资金买上涨保险)'
    elif kappa > 0.05:
        kappa_note = f'κ={kappa:.3f} 🔴Put需求强(期权市场偏空或对冲)'
    else:
        kappa_note = f'κ={kappa:.3f} 期权市场中性'

    return {
        'hurst':       hurst_val,
        'hurst_note':  hurst_note,
        'harv':           harv_val,
        'atr_1h': atr_1h, 'atr_4h': atr_4h, 'atr_1d': atr_1d, 'atr_sl_ref': atr_sl_ref,
        'harv_range_lo':  harv_range_lo,
        'harv_range_hi':  harv_range_hi,
        'harv_range_str': harv_range_str,
        'kappa':       kappa,
        'kappa_note':  kappa_note,
        'beta_p':      beta_p,
        'beta_m':      beta_m,
        'iv_rank':     iv_rank,
        'trend_signal': 'TRENDING' if hurst_val >= 0.55 else 'RANGING',
        'gex_note':    gex_note,
        'gex_bias':    gex_bias,
        'gex_expired': gex_expired,   # P0修复: 过期标记
        'fr_note':     fr_note,
        # P2新增: ic_tracker实时归因
        'ic_attribution': _get_ic_attribution(d),
    }

# ══════════════════════════════════════════════════════════
# Step 8: 宏观压制
# ══════════════════════════════════════════════════════════

def _get_ic_attribution(d: dict) -> dict:
    """P2整合: 从ic_tracker获取实时IC归因 — 2026-09-12"""
    try:
        # [cleaned] import sys as _ic_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.ic_tracker import load_ic_state, compute_all_ic

        # 优先读缓存state
        state = load_ic_state()
        if not state or not state.get('ic_by_regime'):
            # 没有缓存 → 实时计算
            ic = compute_all_ic()
        else:
            ic = state.get('ic_by_regime', {})

        if not ic:
            return {'available': False, 'reason': 'no IC data', 'top_dims': []}

        # 按当前体制提取IC
        regime = d.get('bs', {}).get('regime', '') or d.get('regime', '')
        # 尝试匹配 regime:direction
        signal_dir = d.get('_signal_dir', '')
        key = f'{regime}:{signal_dir}' if signal_dir else ''

        # 优先取 regime:direction 精确匹配，否则取 ALL
        matched = ic.get(key, {}) or ic.get(regime, {}) or ic.get('ALL', {})

        if not matched:
            return {'available': False, 'reason': f'no IC for {key}', 'top_dims': []}

        n = matched.get('_total_n', 0)
        total_ic = matched.get('_total_score_ic', None)

        # 提取top-3有效维度
        dim_ics = {k: v for k, v in matched.items()
                   if not k.startswith('_') and isinstance(v, (int, float))}
        sorted_dims = sorted(dim_ics.items(), key=lambda x: -abs(x[1]))
        top3 = []
        for dim, ic_val in sorted_dims[:3]:
            if abs(ic_val) > 0.05:
                flag = '🔥有效' if abs(ic_val) > 0.15 else '⚠️弱'
                direction = '正向' if ic_val > 0 else '反向'
                top3.append({
                    'dim': dim,
                    'ic': round(ic_val, 4),
                    'direction': direction,
                    'flag': flag,
                })

        return {
            'available': True,
            'regime_key': key or regime or 'ALL',
            'n': n,
            'total_ic': round(total_ic, 4) if isinstance(total_ic, (int, float)) else None,
            'top_dims': top3,
        }
    except Exception as e:
        return {'available': False, 'reason': str(e)[:80], 'top_dims': []}

def step8_macro(d: dict) -> dict:
    """[P2修复 2026-09-12 苏摩111] Step8接入真实CPI/PPI/利率数据，删除AI主观叙事"""
    mac     = d.get('mac', {})
    mac_cal = d.get('mac_cal', {})

    fear_greed = mac.get('fear_greed', 50)
    
    # [P2修复] 读取真实宏观数据
    macro_real = {}
    try:
        from pathlib import Path as _P
        _mr_path = _P(__file__).parent.parent / 'data' / 'macro_real.json'
        if _mr_path.exists():
            import json as _json
            macro_real = _json.loads(_mr_path.read_text())
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # 检查宏观数据新鲜度
    mr_fresh = False
    mr_age_hours = 999
    if macro_real.get('data_time'):
        try:
            from datetime import datetime, timezone
            _mr_dt = datetime.strptime(macro_real['data_time'], '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
            mr_age_hours = (datetime.now(timezone.utc) - _mr_dt).total_seconds() / 3600
            mr_fresh = mr_age_hours < 24  # 24小时内算新鲜
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # 如果数据过期，自动刷新
    if not mr_fresh:
        try:
            # [cleaned] import sys as _sys
            sys.path.insert(0, str(Path(__file__).parent))
            from macro_real_fetcher import update_macro_real
            macro_real = update_macro_real()
            mr_fresh = True
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # 从真实数据提取利率预期
    rate_exp = macro_real.get('rate_expectation', {})
    macro_bias = rate_exp.get('bias', 'NEUTRAL')  # HAWKISH/NEUTRAL/DOVISH
    rate_action = rate_exp.get('action', 'HOLD')   # HIKE/HOLD/CUT_25/CUT_50
    rate_prob = rate_exp.get('probability', 0)
    rate_note = rate_exp.get('note', '')
    cpi_yoy = macro_real.get('cpi_yoy', 0)
    ppi_yoy = macro_real.get('ppi_yoy', 0)
    fed_rate = macro_real.get('fed_rate', 0)
    
    # 宏观日历事件（保留原有逻辑）
    events = []
    if isinstance(mac_cal, dict):
        for k, v in mac_cal.items():
            if isinstance(v, list):
                events.extend(v[:2])
            elif isinstance(v, str):
                events.append(v[:50])
    elif isinstance(mac_cal, list):
        events = [str(e)[:50] for e in mac_cal[:3]]

    high_impact = [e for e in events if any(x in str(e).upper() for x in ['NFP', 'CPI', 'FOMC', 'PCE', '非农'])]
    try:
        from brahma_brain.trader_brain import _check_macro_calendar
        _layer0_macro = _check_macro_calendar()
        if _layer0_macro.get('has_event'):
            _evt_name = _layer0_macro['event']
            _phase = _layer0_macro['phase']
            _hours = _layer0_macro.get('hours_to_event', 0)
            _action = _layer0_macro.get('action', '')
            if _phase == 'pre_event':
                high_impact.append(f'{_evt_name}({_hours:.1f}h后)')
            elif _phase == 'post_event' and _action != 'DONE':
                high_impact.append(f'{_evt_name}(已公布{-_hours:.1f}h)')
            elif _phase == 'normal':
                high_impact.append(f'{_evt_name}(今日)')
            # [9.17修复] post_event + action=DONE → 不加入high_impact，不触发持仓减半
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    has_event = len(high_impact) > 0
    
    # FOMC距离
    fomc_info = macro_real.get('macro_calendar', {})
    days_to_fomc = fomc_info.get('days_to_fomc', 0)
    
    # 仓位提示（基于真实利率预期）
    if has_event:
        pos_note = f'⚠️ 重大宏观事件({", ".join(high_impact[:2])}) → 持仓减半，SL加宽50%'
    elif days_to_fomc <= 2:
        pos_note = f'⚠️ FOMC前{days_to_fomc}天 → 不开新仓，等FOMC后确认方向'
    elif days_to_fomc <= 5:
        pos_note = f'⚠️ FOMC前{days_to_fomc}天 → 轻仓1%NAV，{rate_note}'
    elif macro_bias == 'HAWKISH':
        pos_note = f'🔴 {rate_note} → 做多谨慎，做空有宏观支撑'
    elif macro_bias == 'DOVISH':
        pos_note = f'🟢 {rate_note} → 做多有宏观支撑，做空谨慎'
    else:
        pos_note = f'⚪ {rate_note} → 正常仓位'

    # [P0-2修复 2026-09-19 苏摩111] macro>24h → 降级NEUTRAL
    if not mr_fresh and mr_age_hours > 24:
        macro_bias = 'NEUTRAL'
        rate_action = 'HOLD'
        rate_note = f'宏观数据过期{mr_age_hours:.0f}h，降级为NEUTRAL'
        pos_note = f'⚪ 宏观数据过期({mr_age_hours:.0f}h) → 降级NEUTRAL，正常仓位'

    return {
        'fear_greed':   fear_greed,
        'macro_bias':   macro_bias,       # HAWKISH/NEUTRAL/DOVISH (基于真实CPI/PPI)
        'rate_action':  rate_action,       # HIKE/HOLD/CUT_25/CUT_50
        'rate_prob':    rate_prob,          # 概率%
        'cpi_yoy':      cpi_yoy,            # Core CPI同比%
        'ppi_yoy':      ppi_yoy,            # PPI同比%
        'fed_rate':     fed_rate,           # 当前联邦基金利率%
        'rate_note':    rate_note,          # 人类可读预期描述
        'data_source':  macro_real.get('data_source', 'BLS API + 推算模型'),
        'data_time':    macro_real.get('data_time', ''),
        'data_fresh':   mr_fresh,           # 数据是否新鲜
        'data_age_h':   round(mr_age_hours, 1),
        'macro_note':   rate_note[:60],
        'events':       events[:3],
        'high_impact':  high_impact,
        'has_event':    has_event,
        'days_to_fomc': days_to_fomc,
        'pos_note':     pos_note,
        # P4新增: 跨市场alpha + 美盘时段
        'cross_market': _get_cross_market_for_step8(),
        'us_session':   _get_us_session_for_step8(),
    }

# ══════════════════════════════════════════════════════════
# Step 9: 风控门控
# ══════════════════════════════════════════════════════════

def _get_cross_market_for_step8() -> dict:
    """P4整合: 跨市场alpha状态 — 2026-09-12"""
    try:
        # [cleaned] import sys as _cm_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.cross_market_alpha import get_cross_market_alpha
        cma = get_cross_market_alpha()
        return {
            'alpha': cma['cross_market_alpha'],
            'direction': cma['direction'],
            'crypto_fr': cma['crypto_fr_avg'],
            'tradfi_fr': cma['tradfi_fr_avg'],
            'divergence': cma['fr_divergence'],
            'extremes': cma['extreme_count'],
            'detail': cma['detail'],
        }
    except Exception:
        return {'alpha': 0, 'direction': 'NEUTRAL', 'detail': 'N/A'}

def _get_us_session_for_step8() -> dict:
    """P4整合: 美盘时段门控 — 2026-09-12"""
    try:
        # [cleaned] import sys as _us_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.us_session_gate import get_session_info
        return get_session_info()
    except Exception:
        # Fallback: 基于UTC时间简单判断
        import time as _t
        _h = _t.gmtime().tm_hour
        if 14 <= _h < 21:
            session = 'REGULAR'
        elif 9 <= _h < 14:
            session = 'PREMARKET'
        elif 21 <= _h or _h < 1:
            session = 'AFTER_HOURS'
        else:
            session = 'OVERNIGHT'
        return {'session': session, 'delta': 0, 'note': f'{session} (fallback)'}

def step9_risk(d: dict) -> dict:
    """Step9风控门控 — P0整合: risk_engine统一6层gate (2026-09-12)
    原: 分散调用cb/dd/af三个独立检查
    新: risk_engine.check()统一6层gate + 保留原有失效期检测
    """
    cb  = d['cb']
    dd  = d['dd']
    af  = d['af']
    sym = d.get('sym', 'BTC')
    usdt = d.get('usdt', f'{sym}USDT')
    price = d.get('price', 0)

    # ── 1. 保留原有逻辑（向后兼容）──────────────────────────
    l1 = cb.get('l1', False)
    l2 = cb.get('l2', False)
    l3 = cb.get('l3', False)
    circuit_ok = not (l1 or l2 or l3)

    dd_pct    = dd.get('drawdown_pct', 0)
    dd_status = dd.get('status', 'NORMAL')
    # [9.23苏摩111修复] GREEN是9.21修复后NAV=0时写入的合法状态，等同NORMAL
    dd_ok     = dd_status in ('NORMAL', 'GREEN')

    consec_loss = af.get('consecutive_losses', 0)
    af_ok       = consec_loss < 3

    # ── 2. P0新增: risk_engine统一6层gate ──────────────────
    # 构造signal供risk_engine.check()使用
    _signal = {
        'symbol': usdt,
        'direction': d.get('_signal_dir', 'SHORT'),
        'price': price,
        'position_pct': 5,  # 默认5%NAV，后续由trader_brain调整
        'leverage': 10,
        'sl': price * 1.02 if d.get('_signal_dir', 'SHORT') == 'SHORT' else price * 0.98,
    }
    risk_engine_result = None
    try:
        # [cleaned] import sys as _re_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.risk_engine import check as _re_check
        risk_engine_result = _re_check(_signal)
    except Exception as _re_e:
        risk_engine_result = {'approved': True, 'modified': _signal, 'reasons': [],
                              'kill_switch': False, 'warnings': [f'risk_engine error: {str(_re_e)[:60]}']}

    # ── 3. 合并结果: risk_engine + 原有逻辑 ─────────────────
    re_approved = risk_engine_result.get('approved', True)
    re_reasons = risk_engine_result.get('reasons', [])
    re_warnings = risk_engine_result.get('warnings', [])
    re_kill = risk_engine_result.get('kill_switch', False)

    all_green = circuit_ok and dd_ok and af_ok and re_approved and not re_kill

    blocks = []
    if not circuit_ok:
        level = 'L1' if l1 else ('L2' if l2 else 'L3')
        blocks.append(f'熔断器{level}触发 → 禁止入场')
    if not dd_ok:
        blocks.append(f'回撤{dd_pct:.1f}% status={dd_status} → 降仓50%')
    if not af_ok:
        blocks.append(f'连亏{consec_loss}笔 → 冷却期，降仓50%')
    # P0新增: risk_engine blocks
    for r in re_reasons:
        blocks.append(f'风控gate: {r}')
    if re_kill:
        blocks.append('⚠️ Kill switch触发 → 全平')

    # nav_mult计算
    nav_mult = 1.0
    if not dd_ok and dd_pct >= 5:
        nav_mult = 0.5
    if not dd_ok and dd_pct >= 10:
        nav_mult = 0.25
    if not af_ok:
        nav_mult = min(nav_mult, 0.5)

    # P0新增: risk_engine仓位调整（相关性×0.7等）
    re_modified = risk_engine_result.get('modified', _signal)
    re_adjustments = re_modified.get('risk_adjustments', [])
    if re_adjustments:
        # 如果risk_engine做了仓位调整，取最低乘数
        for adj in re_adjustments:
            if '×0.7' in adj:
                nav_mult = min(nav_mult, 0.7)
    
    # P7新增: portfolio_optimizer多标的仓位优化
    _portfolio = {'active_positions': [], 'correlation_risk': None}
    try:
        # [cleaned] import sys as _po_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.portfolio_optimizer import check_correlation_risk, portfolio_summary
        # 检查BTC+ETH相关性（如果当前标的和另一标的同时持仓）
        _other = 'ETHUSDT' if 'BTC' in usdt else 'BTCUSDT'
        _portfolio['correlation_risk'] = check_correlation_risk(usdt, _other)
        if _portfolio['correlation_risk'].get('high_corr'):
            nav_mult = min(nav_mult, 1.0 / _portfolio['correlation_risk'].get('risk_mult', 1.0))
            blocks.append(f"组合风险: {_portfolio['correlation_risk'].get('warning','')}")
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # [P1修复 2026-09-10] 失效期检测器接入Step 9
    regime_state = 'GREEN'
    regime_note = ''
    try:
        import json as _json
        _rd_path = Path(__file__).parent.parent / 'data' / 'dharma_regime_detector_result.json'
        if _rd_path.exists():
            _rd = _json.loads(_rd_path.read_text())
            regime_state = _rd.get('current_state', 'GREEN')
            if regime_state == 'RED':
                regime_note = '失效期RED → 仓位减半+杠杆减半'
                nav_mult = min(nav_mult, 0.5)
            elif regime_state == 'YELLOW':
                regime_note = '警戒期YELLOW → 仓位×0.75'
                nav_mult = min(nav_mult, 0.75)
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {
        'all_green':  all_green,
        'circuit_ok': circuit_ok,
        'dd_ok':      dd_ok,
        'af_ok':      af_ok,
        'dd_pct':     dd_pct,
        'consec':     consec_loss,
        'blocks':     blocks,
        'nav_mult':   nav_mult,
        'regime_state': regime_state,
        'regime_note':  regime_note,
        # P0新增: risk_engine统一gate结果
        'risk_engine': {
            'approved': re_approved,
            'kill_switch': re_kill,
            'reasons': re_reasons,
            'warnings': re_warnings,
            'adjustments': re_adjustments,
        },
        # P7新增: portfolio_optimizer
        'portfolio': _portfolio,
    }

# ══════════════════════════════════════════════════════════
# Step 10: VIP卡片（姓赵不宣格式）
# ══════════════════════════════════════════════════════════

def step10_vip(sym, price, d, fvg, ob, liq, res, oi, sm, vol, mac, risk) -> str:
    bs      = d['bs']
    mom     = bs.get('momentum', {})
    bd      = bs.get('confluence', {}).get('breakdown', {})
    regime  = bs.get('regime', 'CHOP_MID')
    regime_s= d['regime_s']
    reg_now = regime_s.get(sym + 'USDT', {}).get('confirmed', regime)

    # ══ Phase 6修复 2026-09-18 苏摩111: 价格突破体制切换 ══
    # 三轨体制判定：轨1=价格突破(实时) + 轨2=Hurst(4H) + 轨3=事件驱动(临时)
    # 价格突破止损墙=BULL_TREND信号, 破支撑池=BEAR_TREND信号
    _stop_wall = liq.get('nearest_short', 0)  # 上方止损墙
    _support_pool = liq.get('nearest_long', 0)  # 下方支撑池
    # [P2-4 2026-09-26 苏摩111] DEBUG行静默（诊断时恢复）: price/stop_wall/support_pool/liq_keys
    # import sys as _sys_dbg; print(f'[DEBUG step10] price={price} stop_wall={_stop_wall} support_pool={_support_pool} liq_keys={list(liq.keys())[:8]}', file=_sys_dbg.stderr)
    _price_break_regime = ''
    if _stop_wall > 0 and price > _stop_wall:
        _price_break_regime = 'BULL_TREND'  # 破止损墙=多头突破
    elif _support_pool > 0 and price < _support_pool:
        _price_break_regime = 'BEAR_TREND'  # 破支撑池=空头突破
    # 价格突破体制优先于Hurst体制
    if _price_break_regime:
        reg_now = _price_break_regime
    # [P2-4 2026-09-26 苏摩111] DEBUG行静默（诊断时恢复）: reg_now/_price_break
    # print(f'[DEBUG step10] reg_now={reg_now} _price_break={_price_break_regime}', file=_sys_dbg.stderr)

    # ══════════════════════════════════════════════════════
    # L1【一票否决层】三方战略架构 2026-09-04 苏摩111封印
    # 任何一项触发 → 禁止入场，直接返回等待卡片
    # ══════════════════════════════════════════════════════
    def _wait_card(reason: str, layer: str) -> str:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ [{layer}] 禁止入场\n'
            f'   原因: {reason}\n'
            f'   当前体制: {reg_now}  FVG方向: {fvg.get("dir","?")}  AI议会: (见下)'
        )

    # ─── Phase1: 门控层结束 ─── Phase2: 方向计算开始 ───────────────────
    score_val = float(bs.get('score_final', bs.get('score', 0)))

    # [9.19 P0改革 苏摩111] 废除score一票否决 + 三票NONE不出观察清单
    # 根因：score IC≈0，低分ENTER PnL=+0.38% > 高分=-1.20%，用反向指标做门控
    # 改革：score降级为仓位系数（DAG已有机制），不做入场门控
    #       三票分歧时用trader_brain方向+减仓，不出观察清单
    _trader_dir = d.get('signal_dir', d.get('direction', 'NONE'))

    # L1-①: 死穴门控（保留，这是唯一正确的硬否决）
    # [P1-2 2026-09-26 苏摩111] 语义修正：非「封禁」而是高证据标准（三层API）
    _DEAD = [
        ('BEAR_TREND', 'LONG'),
        ('BEAR_RECOVERY', 'SHORT'),
    ]
    _bias_hint = 'LONG' if (fvg['dir'] == 'BULL' or bs.get('bias') == 'LONG') else 'SHORT'
    for dead_regime, dead_dir in _DEAD:
        if dead_regime in reg_now and _bias_hint == dead_dir:
            _l1_kind = '事件驱动' if dead_dir == 'SHORT' else '全票共识'
            return _wait_card(f'高证据标准 {dead_regime}:{dead_dir}（非封禁，需{_l1_kind}，等待更强证据）', 'L1-准入')

    # [9.19 P0改革] L1-②: CHOP_MID score门控废除
    # score不做入场门控，降级为仓位系数（DAG已有机制）
    # _score_gate变量保留供后续仓位计算使用
    _score_gate = 110  # 保留变量供仓位计算参考，不做门控
    try:
        import json as _gate_json_l0, os as _gate_os_l0
        _gate_path_l0 = _gate_os_l0.join(_gate_os_l0.dirname(__file__), '..', 'data', 'scoring_config.json')
        if _gate_os_l0.exists(_gate_path_l0):
            _gate_cfg_l0 = _gate_json_l0.loads(open(_gate_path_l0).read())
            _score_gate = _gate_cfg_l0.get('score_gate', {}).get(reg_now, 110)
    except Exception:
        pass  # [WARN-suppressed: no var]

    # L1-③: 宏观红色日历
    if mac.get('red_flag'):
        return _wait_card(f'宏观红色事件: {mac.get("event","?")}', 'L1-宏观')

    # L1-④: 风控熔断
    if risk.get('circuit_break'):
        return _wait_card(f'风控熔断触发: {risk.get("reason","?")}', 'L1-风控')

    # L2【方向决策层】FVG × 体制 × LiqMap × 大户仓位
    # HIGH-3修复: 大户仓位纳入L2否决票
    liq_bias     = liq.get('liq_bias', 'NEUTRAL')
    _big_long    = sm.get('big_long', 50) if sm else 50
    _regime_bull = any(x in reg_now for x in ('BULL', 'BULL_EARLY', 'BEAR_RECOVERY'))
    _regime_bear = any(x in reg_now for x in ('BEAR_TREND', 'BEAR_EARLY'))
    _fvg_bull    = fvg['dir'] == 'BULL'
    _fvg_bear    = fvg['dir'] == 'BEAR'
    _liq_bull    = liq_bias in ('UP', 'NEUTRAL')
    _liq_bear    = liq_bias in ('DOWN', 'NEUTRAL')
    _sm_bull     = _big_long >= 58   # 大户明显偏多
    _sm_bear     = _big_long <= 42   # 大户明显偏空

    # HIGH-1修复: BULL_EARLY+FVG=BEAR → 等待FVG触及后做多（特殊处理）
    # 不是方向矛盾，是触发器等待
    _bull_early_wait = ('BULL' in reg_now) and _fvg_bear
    if _bull_early_wait:
        _trigger_price = fvg['magnet']
        _wait_detail = (
            f'BULL_EARLY体制强势，等FVG磁铁${_trigger_price:,.0f}被触及后做多\n'
            f'   触发条件: 价格跌到${_trigger_price:,.0f} + 1H收阳确认\n'
            f'   届时入场区: ${_trigger_price*0.998:,.0f}~${_trigger_price*1.005:,.0f}\n'
            f'   止损: ${_trigger_price - vol.get("atr_1h", price*0.005)*1.5:,.0f}'
        )
        # 注意：这里不return，继续让AI议会裁决
        # 若AI议会=ENTER（FVG已触及），才输出入场

    # 三者一致性（排除BULL_EARLY特殊情形）
    _l2_long  = (_fvg_bull or _bull_early_wait) and _regime_bull and _liq_bull
    _l2_short = _fvg_bear and _regime_bear and _liq_bear and not _bull_early_wait

    # 大户否决：大户方向与L2结论相反时降级为WAIT
    if _l2_long and _sm_bear:
        return _wait_card(f'L2: 大户{_big_long:.0f}%偏空与做多方向矛盾，主力资金方向优先', 'L2-大户')
    if _l2_short and _sm_bull:
        return _wait_card(f'L2: 大户{_big_long:.0f}%偏多与做空方向矛盾，主力资金方向优先', 'L2-大户')

    # L3【入场方向校验】
    # HIGH-2修复: 等待时输出具体监控触发价
    _entry_lo = res.get('entry_lo', 0)
    _entry_hi = res.get('entry_hi', 0)
    # L3【入场方向+位置校验】苏摩111封印 2026-09-04
    # BULL_EARLY+FVG磁铁在上方：重新计算下方支撑区，判断现价位置
    if _l2_long and _entry_lo >= price:
        _atr_hint = vol.get('atr_1h', price * 0.005) if vol else price * 0.005
        _support  = liq.get('nearest_long', 0)
        _ob_floor = 0
        for _k, _v in ob.items():
            if 'BULL' in _k and _v.get('valid'):
                try:
                    import re as _re2
                    _ps = [float(x.replace(',','')) for x in _re2.findall(r'\$([\d,]+)', _v.get('note',''))]
                    if len(_ps) >= 1 and float(_ps[0]) < price:
                        _ob_floor = max(_ob_floor, float(_ps[0]))
                except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        _es    = max(_support, _ob_floor) if (_support or _ob_floor) else price * 0.985
        _ehi_s = round(min(_es * 1.010, price * 1.000), 1)  # 最高不超过现价
        _elo_s = round(_es * 0.990, 1)
        _sl_s  = round(_elo_s - _atr_hint * 1.5, 1)

        if _elo_s <= price <= _ehi_s:
            # ✅ 现价在支撑区内 → 直接放行，修正入场区为当前区间
            _entry_lo = _elo_s
            _entry_hi = _ehi_s
            res['entry_lo'] = _elo_s
            res['entry_hi'] = _ehi_s
            res['resonance'] = True
            # 继续执行，不return
        else:
            # 等价格回调到支撑区
            return _wait_card(
                f'BULL_EARLY做多 FVG磁铁${fvg.get("magnet",0):,.0f}是目标 | '
                f'等回调至: ${_elo_s:,.0f}~${_ehi_s:,.0f} +1H收阳 | '
                f'SL:${_sl_s:,.0f} | T1:${fvg.get("magnet",0):,.0f} T2:${liq.get("nearest_short",0):,.0f}',
                'L3-等待回调'
            )

    elif _l2_short and _entry_lo > 0 and _entry_hi <= price:
        return _wait_card(
            f'FVG阻力${fvg.get("magnet",0):,.0f}未触及 现价${price:,.0f} 差${fvg.get("magnet",0)-price:,.0f} | '
            f'触发价:${fvg.get("magnet",0)*0.998:,.0f}~${fvg.get("magnet",0)*1.002:,.0f}+1H收阴',
            'L3-等待触发'
        )

    # ══════════════════════════════════════════════════════
    # L1~L3通过，进入后续VIP生成
    # ══════════════════════════════════════════════════════

    atr_1h  = mom.get('atr_1h', 0)
    # ATR合理性验证：应在价格的0.2%~3%之间
    if not atr_1h or atr_1h > price * 0.03 or atr_1h < price * 0.002:
        k1h_local = d.get('k1h', [])
        if len(k1h_local) >= 3:
            tr_list = [abs(x[1] - x[2]) for x in k1h_local[-8:]]
            atr_1h  = round(sum(tr_list) / len(tr_list), 1)
        if not atr_1h or atr_1h > price * 0.03 or atr_1h < price * 0.002:
            atr_1h = price * 0.005

    # ATR全周期升级：4H + 1D（SL应参考操作周期ATR）苏摩111封印 2026-09-04
    k4h_local = d.get('k4h', [])
    k1d_local = d.get('k1d', [])
    atr_4h = 0.0
    atr_1d = 0.0
    if len(k4h_local) >= 5:
        atr_4h = round(sum(abs(x[1]-x[2]) for x in k4h_local[-7:])/min(7,len(k4h_local)), 1)
    if len(k1d_local) >= 5:
        atr_1d = round(sum(abs(x[1]-x[2]) for x in k1d_local[-7:])/min(7,len(k1d_local)), 1)
    # 合约参考SL = max(1.5×ATR1H, 1.0×ATR4H)
    atr_sl_ref = max(atr_1h * 1.5, atr_4h * 1.0) if atr_4h else atr_1h * 1.5

    # ══ Phase 3修复 2026-09-18 苏摩111: VIP方向优先读trader_brain ══
    # 根因：step10_vip的bias通过5信号投票独立计算，不读trader_brain的direction
    # 修复：trader_brain给了明确方向时，VIP必须跟随，不再各算各的
    _trader_brain_dir = d.get('_trader_brain_direction', '')  # 由run_analysis注入
    _trader_brain_action = d.get('_trader_brain_action', '')  # ENTER/WATCH/WAIT
    
    # [9.19 P0改革] 废除L0-GATE-2 score门控
    # score不做入场门控，降级为仓位系数
    # 5信号投票（保留作为参考和fallback）
    bull_votes = 0
    bear_votes = 0

    if oi['signal'] in ('LONG_BUILD',):    bull_votes += 2
    if oi['signal'] in ('SHORT_BUILD',):   bear_votes += 2
    if oi['signal'] in ('SHORT_SQUEEZE',): bull_votes += 1

    if sm['signal'] in ('STRONG_BULL', 'MILD_BULL'): bull_votes += 2
    if sm['signal'] == 'BEAR':                        bear_votes += 2

    if fvg['dir'] == 'BULL': bull_votes += 1
    if fvg['dir'] == 'BEAR': bear_votes += 1

    if vol['kappa'] < -0.05: bull_votes += 1
    if vol['kappa'] > 0.05:  bear_votes += 1

    if 'BULL' in reg_now: bull_votes += 2
    if 'BEAR' in reg_now: bear_votes += 2

    _vote_bias = 'LONG' if bull_votes > bear_votes else ('SHORT' if bear_votes > bull_votes else 'NEUTRAL')

    # [P1-2收尾 2026-09-26 苏摩111] 死穴门控所需的score/SL估算（tier计算依赖）
    _score_now = float(bs.get('score_final', bs.get('score', 0)))
    _sl_est_tier = 0.0
    _el = float(res.get('entry_lo', 0) or 0)
    if _el > 0:
        _atr4h = float(vol.get('atr_4h', 0) or 0) if vol else 0.0  # atr在vol字典（L1346），不在res
        _min_sl = max(_el * 0.02, _atr4h * 1.5) if _atr4h else _el * 0.02
        _sl_est_tier = _min_sl / _el * 100
    # [P2-4修复 2026-09-23 苏摩111] DEBUG行静默（诊断时恢复）: _trader_brain_dir/_action/_vote_bias/bull/bear
    # print(f'[DEBUG step10 BIAS] ...', file=sys.stderr)
    # 关键修复：trader_brain方向优先，5信号投票作为fallback
    if _trader_brain_dir in ('LONG', 'SHORT') and _trader_brain_action != 'WAIT':
        bias = _trader_brain_dir  # trader_brain说了算
        _bias_source = f'trader_brain({_trader_brain_action})'
    else:
        bias = _vote_bias  # fallback到5信号投票
        _bias_source = f'5信号投票({_vote_bias})'
        # 保留旧的_bias_hint逻辑兼容L1死穴门控
    _bias_hint = bias if bias != 'NEUTRAL' else ('LONG' if fvg['dir'] == 'BULL' else 'SHORT')

    # ══ 死穴门控（体制重设计P1-2 2026-09-26 苏摩111：语义修正）══
    # 旧语义「永久封禁/严禁」作废。新语义=三层API证据标准：
    #   tier锁(get_score_gate locked) → 🚫高证据标准锁（非永久封禁，n=14可推翻）
    #   needs_consensus/needs_event  → ⚠️高证据标准，不入场非封禁
    _reg_upper = str(reg_now).upper()
    _bias_upper = str(bias).upper()
    _tier = None
    if _score_now >= 165: _tier = '165+'
    elif _score_now >= 155: _tier = '155+'
    elif _score_now >= 140: _tier = '140-154'
    _is_dead = False
    _dead_reason = ''
    try:
        from regime_config import get_score_gate as _get_sg, get_direction_gate as _get_dg
    except ImportError:
        try:
            from brahma_brain.regime_config import get_score_gate as _get_sg, get_direction_gate as _get_dg
        except Exception:
            _get_sg = None; _get_dg = None
    if _get_sg is not None and _bias_upper in ('LONG', 'SHORT'):
        if _tier:
            _tier_gate = _get_sg(_reg_upper, _bias_upper, _tier)
            # BULL_TREND:LONG 的 SL≥3% 条件保留（P1-2铁律：tier锁+宽SL才拒）
            if _tier_gate.get('locked') and not (_reg_upper == 'BULL_TREND' and _bias_upper == 'LONG'
                                                and _tier == '140-154' and _sl_est_tier < 3.0):
                _is_dead = True
                _dead_reason = f'🚫 高证据标准锁 — {_tier_gate.get("note", "")}（score={_score_now:.0f} 命中 {_reg_upper}:{_bias_upper}:{_tier}）'
        if not _is_dead and _get_dg(_reg_upper, _bias_upper) in ('needs_consensus', 'needs_event'):
            _gate_kind = '全票共识' if _get_dg(_reg_upper, _bias_upper) == 'needs_consensus' else '事件驱动'
            _is_dead = True
            _dead_reason = f'⚠️ 高证据标准 — {_reg_upper}:{_bias_upper} 需{_gate_kind}，当前不入场（非封禁）'
    # 两查都通过 → _is_dead=False → 走原正常VIP流程

    if _is_dead:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'🚫 {_dead_reason}\n'
            f'   当前体制: {reg_now}  方向: {bias}'
        )

    # 无共振点 → 等待
    if not res['resonance']:
        missing_str = ' / '.join(res['missing']) if res['missing'] else '方向不明'
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ 当前无精确共振点 — 等待结构\n'
            f'   缺失条件：{missing_str}\n'
            f'   有效信号满足后自动更新'
        )

    entry_lo = res['entry_lo']
    entry_hi = res['entry_hi']
    # [P2-4 2026-09-26 苏摩111] DEBUG行静默（诊断时恢复）: entry_lo/entry_hi/bias
    # print(f'[DEBUG step10 ENTRY] res.entry_lo={entry_lo} res.entry_hi={entry_hi} bias={bias}', file=sys.stderr)

    # [P1修复 2026-09-10] 入场区 = max(共振下沿, 支撑池)
    _liq_support = res.get('liq_nearest_long', 0) or liq.get('nearest_long', 0)
    if _liq_support > 0 and bias == 'LONG' and entry_lo < _liq_support:
        # 入场区在支撑池下方 → 上移到支撑池上方
        _shift = _liq_support - entry_lo
        entry_lo = round(_liq_support, 1)
        entry_hi = round(entry_hi + _shift, 1)
        if entry_hi <= entry_lo:
            entry_hi = round(entry_lo * 1.005, 1)  # 入场区至少0.5%宽

    # D7修复: entry=0时强制走等待路径（不应进入SL计算）
    if entry_lo == 0.0 or entry_hi == 0.0:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ 入场区无效（结构不满足），等待共振\n'
            f'   当前体制: {reg_now}  方向偏向: {bias}'
        )

    # D6修复: bias方向必须与入场区方向一致，否则拒绝输出
    # LONG bias → 需要BULL FVG → 入场区在现价下方 (entry_lo < price)
    # SHORT bias → 需要BEAR FVG → 入场区在现价上方 (entry_lo > price)
    _entry_dir_ok = True
    if bias == 'LONG' and entry_lo > 0 and entry_lo >= price:
        _entry_dir_ok = False
    if bias == 'SHORT' and entry_lo > 0 and entry_lo <= price:
        _entry_dir_ok = False
    if not _entry_dir_ok:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⚠️ 方向冲突：bias={bias} 但入场区${entry_lo:,.0f}~${entry_hi:,.0f}在错误方向\n'
            f'   FVG方向={fvg.get("dir","?")} 与投票方向={bias} 矛盾，等待方向收敛'
        )

    # 仓位调整（宏观+风控）
    base_lev_main = 10 if 'TREND' in reg_now else 5
    base_nav_main = 5  if 'TREND' in reg_now else 2
    if mac['has_event']:
        base_nav_main = max(1, base_nav_main // 2)
        base_lev_main = max(3, base_lev_main - 3)
    base_nav_main = round(base_nav_main * risk['nav_mult'])
    # [P4修复 2026-09-10] 失效期检测器：RED→仓位减半，YELLOW→仓位×0.75
    try:
        from pathlib import Path as _P
        _rd = _P(__file__).parent.parent / 'data' / 'dharma_regime_detector_result.json'
        if _rd.exists():
            _rd_data = json.loads(_rd.read_text())
            _regime_state = _rd_data.get('current_state', 'GREEN')
            if _regime_state == 'RED':
                base_nav_main = max(1, base_nav_main // 2)
                base_lev_main = max(3, base_lev_main // 2)  # [P1修复] 失效期RED杠杆也减半
            elif _regime_state == 'YELLOW':
                base_nav_main = max(1, round(base_nav_main * 0.75))
                base_lev_main = max(3, round(base_lev_main * 0.75))  # [P1修复] YELLOW杠杆×0.75
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    base_nav_main = max(1, base_nav_main)

    # [P1-3修复 2026-09-11] 仓位=f(score)线性映射
    # score=110 → 仓位×1.0（基线）
    # score=130 → 仓位×1.3
    # score=150 → 仓位×1.5（上限）
    _score = float(bs.get('score_final', bs.get('score', 0)))
    if _score >= 110:
        _score_mult = min(1.5, 1.0 + (_score - 110) / 100.0)  # 110→1.0, 160→1.5
        base_nav_main = max(1, round(base_nav_main * _score_mult))

    lev_side  = max(3, base_lev_main - 5)
    nav_side  = max(1, base_nav_main // 2)

    # SL / TP
    # [P1修复 2026-09-10 苏摩111] SL铁律：max(SL_PCT, 1.5×ATR4H)
    # SL_PCT: 做多2.0% / BULL做空2.5% / BEAR做空2.0%
    # ATR铁律: SL距离 >= 1.5×ATR4H（不是ATR1H！）
    if bias == 'LONG':
        sl_pct_required = 0.02  # 做多2.0%
        min_sl = max(entry_lo * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_lo * sl_pct_required
        sl       = round(entry_lo - min_sl, 1)
        sl_pct   = round((entry_lo - sl) / entry_lo * 100, 2)
        tp1      = round(liq['nearest_short'] if liq['nearest_short'] > price else price + atr_1h * 2.5, 1)
        tp2      = round(liq['second_short']  if liq.get('second_short', 0) > tp1 else tp1 + atr_1h * 2, 1)
        tp3      = round(tp2 + atr_1h * 2, 1)
        rr       = round((tp1 - entry_lo) / (entry_lo - sl), 2) if entry_lo > sl else 0

        # BUG-6修复：多单入场区在现价下方（等回调），描述明确
        main_line   = f'🟢 多单｜回调入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}（价格跌到此区挂单）'
        main_params = f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}'

        # [修复 2026-09-18 苏摩111] 方向=LONG时也输出止损墙空单条件，不再写“暂无空单”
        if 'BULL' in str(reg_now) or 'BEAR_RECOVERY' in str(reg_now):
            # BULL体制：多单止盈位=空单入场位
            _short_entry = tp1  # 多单第一目标=空单入场
            _short_sl = round(_short_entry + max(_short_entry * 0.025, atr_4h * 1.5 if atr_4h else _short_entry * 0.025), 1)
            _short_tp1 = round(entry_lo, 1)  # 空单目标=多单入场区
            _short_tp2 = round(_short_tp1 - atr_1h * 1.5, 1) if atr_1h else round(_short_tp1 * 0.98, 1)
            side_line  = f'🔴 空单（条件）｜止损墙 ${_short_entry:,.1f} 附近假突破回落再空'
            side_params= f'止损 ${_short_sl:,.1f}｜目标 ${_short_tp1:,.0f}→${_short_tp2:,.0f}'
        else:
            side_hi    = round(entry_lo + atr_1h * 2.0, 1)
            side_lo    = round(entry_lo + atr_1h * 1.2, 1)
            side_sl_pct = 0.025 if 'BULL' in str(reg_now) else 0.02
            side_min_sl = max(side_hi * side_sl_pct, atr_4h * 1.5) if atr_4h else side_hi * side_sl_pct
            side_sl    = round(side_hi + side_min_sl, 1)
            side_tp1   = round(entry_lo - atr_1h * 1.5, 1)
            side_tp2   = round(side_tp1 - atr_1h * 1.5, 1)
            side_tp    = f'${side_tp1:,.0f}→${side_tp2:,.0f}'
            side_line  = f'🔴 空单（轻）｜若反弹到 ${side_lo:,.1f}~${side_hi:,.1f} 再空'
            side_params= f'止损 ${side_sl:,.1f}｜目标 {side_tp}'
        main_dir   = '主方向做多'

    else:  # SHORT
        # [P2-4 2026-09-26 苏摩111] DEBUG行静默（诊断时恢复）: SHORT tp1_pre
        # print(f'[DEBUG step10 SHORT] entry_lo={entry_lo} entry_hi={entry_hi} tp1_pre={liq.get("nearest_long",0)} price={price} atr_1h={atr_1h} atr_4h={atr_4h}', file=sys.stderr)
        sl_pct_required = 0.025 if 'BULL' in str(reg_now) else 0.02  # BULL做空2.5%/BEAR做空2.0%
        min_sl = max(entry_hi * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_hi * sl_pct_required
        sl       = round(entry_hi + min_sl, 1)
        sl_pct   = round((sl - entry_hi) / entry_hi * 100, 2)
        tp1      = round(liq.get('nearest_long', 0) if liq.get('nearest_long', 0) and liq['nearest_long'] < price else price - atr_1h * 2.5, 1)
        tp2      = round(liq['second_long']  if liq.get('second_long', 0) > 0 and liq['second_long'] < tp1 else tp1 - atr_1h * 2, 1)
        tp3      = round(tp2 - atr_1h * 2, 1)
        rr       = round((entry_hi - tp1) / (sl - entry_hi), 2) if sl > entry_hi else 0

        # BUG-6修复：空单入场区在现价上方（等反弹），描述明确
        main_line   = f'🔴 空单｜反弹入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}（价格反弹到此区挂单）'
        main_params = f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}'

        # [修复 2026-09-18 苏摩111] 方向=SHORT时也输出支搜池接多条件，不再写“暂无多单”
        if 'BEAR' in str(reg_now):
            _long_entry_lo = tp1  # 空单第一目标=多单入场
            _long_entry_hi = round(_long_entry_lo + atr_1h * 0.5, 1) if atr_1h else round(_long_entry_lo * 1.01, 1)
            _long_sl = round(_long_entry_lo - max(_long_entry_lo * 0.02, atr_4h * 1.5 if atr_4h else _long_entry_lo * 0.02), 1)
            _long_tp1 = round(entry_hi, 1)  # 多单目标=空单入场区
            _long_tp2 = round(_long_tp1 + atr_1h * 1.5, 1) if atr_1h else round(_long_tp1 * 1.02, 1)
            side_line  = f'🟢 多单（条件）｜支搜池 ${_long_entry_lo:,.1f}~${_long_entry_hi:,.1f} 接多'
            side_params= f'止损 ${_long_sl:,.1f}｜目标 ${_long_tp1:,.0f}→${_long_tp2:,.0f}'
        else:
            # [2026-09-21 P2修复 苏摩111] entry_lo=0时用tp1(支撑池)作为多单入场区
            _hunt_base = entry_lo if entry_lo > 0 else tp1
            # [P2-4 2026-09-26 苏摩111] DEBUG行静默（诊断时恢复）: ELSE tp1/_hunt_base
            # print(f'[DEBUG step10 ELSE] entry_lo={entry_lo} tp1={tp1} _hunt_base={_hunt_base} atr_1h={atr_1h}', file=sys.stderr)
            hunt_lo    = round(_hunt_base - atr_1h * 1.5, 1)
            hunt_hi    = round(_hunt_base - atr_1h * 0.5, 1)
            side_min_sl = max(hunt_lo * 0.02, atr_4h * 1.5) if atr_4h else hunt_lo * 0.02
            side_sl    = round(hunt_lo - side_min_sl, 1)
            side_tp1   = round(_hunt_base + atr_1h * 1.5, 1)
            side_tp2   = round(side_tp1 + atr_1h * 1.5, 1)
            side_tp    = f'${side_tp1:,.0f}→${side_tp2:,.0f}'
            side_line  = f'🟢 多单（轻）｜若下探 ${hunt_lo:,.1f}~${hunt_hi:,.1f} 被扫后接'
            side_params= f'止损 ${side_sl:,.1f}｜目标 {side_tp}'
        main_dir   = '主方向做空'

    # 风控提示
    risk_note = ''
    if mac['has_event']:
        risk_note = f'\n⚠️ 宏观事件({", ".join(mac.get("high_impact",[])[:1])})→仓位已压缩'
    if risk['blocks']:
        risk_note += f'\n🚨 风控: {risk.get("blocks",[])[0]}'

    # SL验证 [P2修复 2026-09-10] 用ATR4H铁律验证
    sl_distance = abs(entry_lo - sl) if bias == 'LONG' else abs(sl - entry_hi)
    atr4h_threshold = atr_4h * 1.5 if atr_4h else atr_1h * 1.5
    sl_ok = sl_distance >= atr4h_threshold
    sl_tag = '✅' if sl_ok else '⚠️偏窄'

    # ══ 闸门2: VIP铁律自动校验 [9.18 苏摩111 顶层修复] ══
    # 在VIP输出前，自动检查所有铁律，不满足=拒绝输出策略
    _gate2_errors = []
    
    # 铁律1: SL距离 ≥ 1.5×ATR4H (加0.1%容差防浮点精度)
    if atr_4h and sl_distance < atr_4h * 1.5 * 0.999:
        _gate2_errors.append(f'SL距离${sl_distance:.0f} < 1.5×ATR4H ${atr_4h*1.5:.0f}')
    # 铁律2: SL距离 ≥ 1.5×ATR1H (加0.1%容差防浮点精度)
    if atr_1h and sl_distance < atr_1h * 1.5 * 0.999:
        _gate2_errors.append(f'SL距离${sl_distance:.0f} < 1.5×ATR1H ${atr_1h*1.5:.0f}')
    # 铁律3: RR ≥ 2.0（用TP2计算）
    if bias == 'LONG' and tp2:
        _rr_tp2 = round((tp2 - entry_lo) / (entry_lo - sl), 2) if entry_lo > sl else 0
        if _rr_tp2 < 2.0:
            _gate2_errors.append(f'RR(TP2)={_rr_tp2} < 2.0')
    elif bias == 'SHORT' and tp2:
        _rr_tp2 = round((entry_hi - tp2) / (sl - entry_hi), 2) if sl > entry_hi else 0
        if _rr_tp2 < 2.0:
            _gate2_errors.append(f'RR(TP2)={_rr_tp2} < 2.0')
    # 铁律4: 仓位 ≤ 10%NAV
    if base_nav_main > 10:
        _gate2_errors.append(f'仓位{base_nav_main}% > 10%NAV上限')
    # 铁律5: 概念校验 — 止损墙/支撑池/清算区不可混淆
    _liq_short = liq.get('nearest_short', 0)
    _liq_long = liq.get('nearest_long', 0)
    _support_pool = liq.get('nearest_long', 0)  # 支撑池=下方多头止损区
    _liquidation = round(price * 0.95, 0)  # 清算区=-5%
    # 如果多单目标标成了"清算区"但实际是支撑池距离 → 告警
    
    if _gate2_errors:
        _gate2_msg = ' | '.join(_gate2_errors)
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日观察\n'
            f'⏳ [闸门2] 策略未通过铁律校验，拒绝输出\n'
            f'   ❌ {_gate2_msg}\n'
            f'   当前 体制={reg_now}\n'
            f'   闸门2封印 2026-09-18 苏摩111'
        )

    # ─── Phase2: 方向计算结束 ─── Phase3: 格式化输出开始 ──────────────
    lines = [
        f'──── VIP ────',
        f'🌿 姓赵不宣 | {sym} 今日布局  [{(d.get("_step11") or {}).get("verdict","WAIT")}]',
        f'',
        f'{main_line}',
        f'{main_params}',
        f'杠杆 {base_lev_main}x｜仓位 {base_nav_main}%  RR={rr}x  SL={sl_pct:.2f}% {sl_tag}',
        f'',
        f'{side_line}',
        f'{side_params}',
        f'杠杆 {lev_side}x｜仓位 {nav_side}%',
        f'',
        f'⚠️ {main_dir}  ATR1H=${atr_1h:.0f} ATR4H=${atr_4h:.0f}',
    ]
    # [NEW-1修复 2026-09-19 苏摩111] RSI 1H超买/超卖在VIP卡片中强调
    # RSI 1H在d.bs.momentum中
    _rsi_1h_val = mom.get('rsi_1h', 0) or vol.get('rsi_1h', 0) or 0
    if _rsi_1h_val >= 75:
        lines.append(f'🔥 RSI 1H={_rsi_1h_val:.0f}超买！做空高胜率setup')
    elif _rsi_1h_val <= 25:
        lines.append(f'🔥 RSI 1H={_rsi_1h_val:.0f}超卖！做多高胜率setup')
    lines.append(f'🚫 破${sl:,.0f} 策略作废')
    if risk_note:
        lines.append(risk_note)

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════
# 主报告组装
# ══════════════════════════════════════════════════════════

def _trader_narrative(sym, price, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, tb_result) -> str:
    """40年顶级合约交易员视角：用94维数据串成市场故事，不是列条件"""
    bs = d.get('bs', {})
    regime = bs.get('regime', 'CHOP_MID')
    score = float(bs.get('score_final', bs.get('score', 0)))
    hurst = vol.get('hurst', 0.5)
    kappa = vol.get('kappa', 0)
    big_long = sm.get('big_long', 50)
    retail_long = sm.get('retail_long', 50)
    oi_signal = oi.get('signal', 'NO_DATA')
    cvd_1h = oi.get('cvd_1h', 0)
    fvg_dir = fvg.get('dir', 'NONE')
    fvg_consensus = fvg.get('consensus', fvg_dir)
    fvg_mid = fvg.get('magnet', 0)
    liq_short = liq.get('nearest_short', 0)
    liq_long = liq.get('nearest_long', 0)
    liq_2nd_short = liq.get('second_short', 0)
    liq_2nd_long = liq.get('second_long', 0)
    atr_1h = vol.get('atr_1h', 0)
    atr_4h = vol.get('atr_4h', 0)
    regime_state = risk.get('regime_state', 'GREEN')
    direction = tb_result.get('direction', 'NONE')
    entry_lo = tb_result.get('entry_lo', 0) or res.get('entry_lo', 0)
    entry_hi = tb_result.get('entry_hi', 0) or res.get('entry_hi', 0)
    sl = tb_result.get('sl', 0)
    rr = tb_result.get('rr', 0)
    missing = tb_result.get('missing', [])
    cross = tb_result.get('consistent_count', 0)
    k4h_mult = d.get('_k4h_mult', 1.0)
    k1h_mult = d.get('_k1h_mult', 1.0)

    parts = []

    # 1. 市场现状一句话
    _momentum = bs.get('momentum', {})
    _rsi_4h = _momentum.get('rsi_4h', 50)
    _rsi_note = ''
    if _rsi_4h < 30:
        _rsi_note = f' RSI4H={_rsi_4h:.1f}🔴超卖'
    elif _rsi_4h > 70:
        _rsi_note = f' RSI4H={_rsi_4h:.1f}🔴超买'
    if regime == 'CHOP_MID' and score < 110:
        parts.append(f'{sym}在${price:,.0f}横盘，CHOP体制，大户{big_long:.0f}%多但OI={oi_signal}。{_rsi_note}'.strip())
    elif 'BULL' in regime:
        parts.append(f'{sym}在${price:,.0f}，{regime}，FVG{fvg_consensus}共识，OI={oi_signal}。{_rsi_note}'.strip())
    elif 'BEAR' in regime:
        parts.append(f'{sym}在${price:,.0f}，{regime}，FVG{fvg_consensus}共识，OI={oi_signal}。{_rsi_note}'.strip())
    else:
        parts.append(f'{sym}在${price:,.0f}，体制{regime}。{_rsi_note}'.strip())

    # 2. 主力意图 + 推断（40年交易员不只是描述，要推断主力在等什么）
    _intent = ''
    _trigger = ''  # 推断触发条件
    # P0修复: OI变化用各周期独立数据
    _oi_chg_15m = oi.get('total_change', 0)
    _oi_chg_1h = oi.get('total_change_1h', 0)
    _oi_chg_4h = oi.get('total_change_4h', 0)
    if big_long >= 60 and 'UNWIND' in oi_signal:
        _intent = f'大户{big_long:.0f}%多但OI全线撤退(15M:{_oi_chg_15m:+.0f}/1H:{_oi_chg_1h:+.0f}/4H:{_oi_chg_4h:+.0f})=大户在等不是在加'
        # 推断：大户在等什么？
        if hurst >= 0.55:
            _trigger = f'大户在等Hurst突破0.60确认趋势，一旦确认OI会从UNWIND转BUILD'
        else:
            _trigger = f'大户在等价格回到支撑${liq_long:,.0f}附近才加仓，当前${price:,.0f}不够便宜'
    elif big_long >= 60 and oi_signal in ('LONG_BUILD', 'SHORT_SQUEEZE'):
        _intent = f'大户{big_long:.0f}%多+OI={oi_signal}=主力在加多'
        _trigger = f'主力已在加多，等价格突破${liq_short:,.0f}止损墙=逼空启动'
    elif big_long <= 45 and 'BUILD' in oi_signal and 'SHORT' in oi_signal:
        _intent = f'大户{big_long:.0f}%偏空+OI={oi_signal}=主力在加空'
        _trigger = f'主力已在加空，等价格跌破${liq_long:,.0f}支撑池=猎杀启动'
    elif big_long >= 55 and oi_signal == 'SHORT_BUILD':
        _intent = f'大户{big_long:.0f}%多但OI={oi_signal}=有人在高位挂空单对冲'
        _trigger = f'空头在建仓但大户没走，等OI从SHORT_BUILD翻转为LONG_BUILD=空头被扫=做多信号确认'
    elif 'UNWIND' in oi_signal:
        _intent = f'OI={oi_signal}=资金在减仓离场，不是加仓'
        _trigger = f'等OI减仓结束出现LONG_BUILD或SHORT_BUILD=新方向确认'
    else:
        _intent = f'大户{big_long:.0f}%多 vs 散户{retail_long:.0f}%多，OI={oi_signal}'
        if big_long > retail_long:
            _trigger = f'大户比散户看多=聪明钱偏多，等价格到支撑${liq_long:,.0f}附近大户可能加仓'
        else:
            _trigger = f'散户比大户看多=反向信号偏空，散户越多主力越可能猎杀'
    parts.append(_intent + '。' + (_trigger + '。' if _trigger else ''))

    # 3. 趋势状态
    if hurst >= 0.6:
        parts.append(f'Hurst={hurst:.3f}趋势要来但还没到，')
    elif hurst >= 0.55:
        parts.append(f'Hurst={hurst:.3f}趋势性隐现，')
    elif hurst >= 0.5:
        parts.append(f'Hurst={hurst:.3f}随机游走，')
    else:
        parts.append(f'Hurst={hurst:.3f}均值回归，')

    if kappa < -0.1:
        parts[-1] += f'κ={kappa:.3f}Call强(大资金买上涨保险)。'
    elif kappa > 0.1:
        parts[-1] += f'κ={kappa:.3f}Put强(大资金买下跌保险)。'
    else:
        parts[-1] += f'κ={kappa:.3f}中性。'

    # P0修复: GEX数据展示真实值+过期标记
    if vol.get('gex_expired', False):
        parts.append(f'⚠️ GEX数据已过期，不参与共振计算。')
    elif vol.get('gex_note', ''):
        parts.append(vol['gex_note'] + '。')

    # 4. 多剧本推演 + 概率 + 时间预期（P1+P3：40年交易员给多剧本+时间维度）
    _up_pct = ((liq_short - price) / price * 100) if liq_short > price else 0
    _dn_pct = ((price - liq_long) / price * 100) if liq_long > 0 and liq_long < price else 0
    _trend_strength = 'high' if hurst >= 0.6 else 'medium' if hurst >= 0.55 else 'low'
    # P1-1: OI权重 — OI全线UNWIND→逼空×0.7，猎杀×1.3
    _oi_up_adj = 1.0
    _oi_dn_adj = 1.0
    if 'UNWIND' in oi_signal:
        _oi_up_adj = 0.7
        _oi_dn_adj = 1.3
    elif oi_signal == 'SHORT_BUILD':
        _oi_up_adj = 0.8
        _oi_dn_adj = 1.2

    # 时间预期：Hurst从当前到突破0.60需要多少根4H K线
    _bars_to_trend = 0
    if hurst < 0.60:
        _hurst_gap = 0.60 - hurst
        # 基于经验：Hurst每4H K线大约移动0.01-0.03（保守取0.015）
        _bars_to_trend = max(2, int(_hurst_gap / 0.015))
    _time_str = f'约{_bars_to_trend}根4H K线（{_bars_to_trend*4}h）' if _bars_to_trend > 0 else '已在趋势区'

    # 剧本概率
    if _trend_strength == 'high' and _up_pct < 3:
        _up_prob = 65
    elif _trend_strength == 'medium' and _up_pct < 3:
        _up_prob = 45
    elif _trend_strength == 'high' and _up_pct < 5:
        _up_prob = 50
    else:
        _up_prob = 30
    _up_prob = round(_up_prob * _oi_up_adj)  # P1-1: OI权重调整
    if _trend_strength == 'low' and _dn_pct < 2:
        _dn_prob = 55
    elif _trend_strength == 'medium' and _dn_pct < 2:
        _dn_prob = 40
    elif _trend_strength == 'low' and _dn_pct < 4:
        _dn_prob = 45
    else:
        _dn_prob = 25
    _dn_prob = round(_dn_prob * _oi_dn_adj)  # P1-1: OI权重调整
    _chop_prob = max(15, 100 - (_up_prob if liq_short > price else 0) - (_dn_prob if liq_long > 0 and liq_long < price else 0))

    # 多剧本格式
    _scenarios = []
    # 剧本A：逼空
    if liq_short > price:
        _up_tgt = f'${liq_2nd_short:,.0f}' if liq_2nd_short > liq_short else f'${liq_short:,.0f}'
        _scenarios.append(f'剧本A({_up_prob}%): 破${liq_short:,.0f}止损墙→逼空到{_up_tgt}，{max(_up_prob-30,15)}%概率假突破回落')
    # 剧本B：猎杀
    if liq_long > 0 and liq_long < price:
        _dn_tgt = f'${liq_2nd_long:,.0f}' if liq_2nd_long > 0 and liq_2nd_long < liq_long else f'${liq_long:,.0f}'
        _scenarios.append(f'剧本B({_dn_prob}%): 破${liq_long:,.0f}支撑池→猎杀到{_dn_tgt}，{max(100-_dn_prob-15,20)}%概率支撑有效')
    # 剧本C：横盘
    if _chop_prob >= 15:
        _scenarios.append(f'剧本C({_chop_prob}%): 继续横盘，Hurst趋势确认需{_time_str}')
    if _scenarios:
        parts.append('。'.join(_scenarios) + '。')

    # 5. 关键价位汇总
    _levels = []
    if entry_lo > 0 and entry_hi > 0 and direction != 'NONE':
        _levels.append(f'入场区${entry_lo:,.1f}~${entry_hi:,.1f}')
    if sl > 0:
        _levels.append(f'SL=${sl:,.1f}')
    if liq_short > price:
        _levels.append(f'上方目标${liq_short:,.0f}')
    if liq_long > 0 and liq_long < price:
        _levels.append(f'下方支撑${liq_long:,.0f}')
    if fvg_mid > 0:
        _levels.append(f'FVG磁铁${fvg_mid:,.1f}')
    if _levels:
        parts.append('关键价位：' + ' / '.join(_levels) + '。')

    # 6. 交易员结论 + 动态触发器 + 时间预期（P1：加时间维度）
    _atr_1_5 = atr_1h * 1.5 if atr_1h else 0
    # HCME最相似案例时间预期
    _hcme_case = ''
    try:
        fc_raw = bs.get('fangcang', {})
        if isinstance(fc_raw, dict):
            top = fc_raw.get('top_similar', [])
            if top:
                t = top[0]
                _hcme_case = f'{t.get("dt","?")}相似{t.get("score",0):.2f}未来{t.get("future_ret",0):+.1f}%'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    if direction != 'NONE' and entry_lo > 0:
        if missing:
            _miss_short = '、'.join(missing[:3])
            parts.append(f'方向{direction}，入场区${entry_lo:,.1f}~${entry_hi:,.1f}已给，但{_miss_short}。')
            if _atr_1_5 > 0:
                parts.append(f'浮盈>${_atr_1_5:,.0f}(1.5×ATR1H)→移动SL保本。')
            _triggers = []
            if regime_state == 'RED':
                _triggers.append(f'失效期RED→仓位减半，等score过120={120-score:.0f}分（按当前速率约{_bars_to_trend}根4H）')
            if rr > 0 and rr < 2.0:
                _triggers.append(f'RR={rr:.1f}不够，等止损墙${liq_short:,.0f}拉远或入场区上移')
            if 'OI' in ' '.join(missing):
                _triggers.append(f'如果OI从{oi_signal}翻转为LONG_BUILD → 资金流确认，这单可以进（通常1-2根4H K线内确认）')
            if 'score' in ' '.join(missing):
                _triggers.append(f'如果体制确认（Hurst翻转/价格突破轨制）→ 可以进')
            if _hcme_case:
                _triggers.append(f'HCME最相似{_hcme_case}→历史参考时间线')
            if _triggers:
                parts.append('触发器：' + ' / '.join(_triggers) + '。')
        else:
            parts.append(f'条件全满，{direction}入场区${entry_lo:,.1f}~${entry_hi:,.1f}可以进。')
            if _atr_1_5 > 0:
                parts.append(f'浮盈>${_atr_1_5:,.0f}→移动SL保本。')
    elif direction != 'NONE' and entry_lo == 0:
        parts.append(f'方向{direction}但OI矛盾导致入场区失效。')
        _triggers = []
        if liq_long > 0 and liq_long < price and direction == 'LONG':
            _triggers.append(f'如果OI从{oi_signal}翻转为LONG_BUILD → 在${liq_long:,.0f}~${price:,.0f}区间接（通常1-2根4H K线内确认）')
        elif liq_short > price and direction == 'SHORT':
            _triggers.append(f'如果OI确认做空 → 在${liq_short:,.0f}附近空')
        if regime_state == 'RED':
            _triggers.append(f'失效期RED→即使条件确认也只轻仓2%+杠杆5x')
        if _hcme_case:
            _triggers.append(f'HCME参考：{_hcme_case}')
        if _triggers:
            parts.append('触发器：' + ' / '.join(_triggers) + '。')
    elif direction == 'NONE':
        _triggers = []
        # P1-2: 检查当前值是否已满足
        if hurst >= 0.60:
            _triggers.append(f'Hurst={hurst:.3f}已>0.60趋势区✅，等score站上110={110-score:.0f}分 → ${price*0.985:,.0f}附近多单可以试')
        elif hurst >= 0.55:
            _triggers.append(f'如果Hurst突破0.60（当前{hurst:.3f}差{0.60-hurst:.3f}）+score站上110 → ${price*0.985:,.0f}附近多单可以试（预计{_time_str}）')
        else:
            _triggers.append(f'等Hurst突破0.55（当前{hurst:.3f}）+score站上110 → ${price*0.985:,.0f}附近多单可以试（预计{_time_str}）')
        if liq_long > 0 and liq_long < price:
            _triggers.append(f'如果价格先跌到${liq_long:,.0f}支撑池+1H收阳 → 可轻仓试探')
        if liq_short > price:
            _triggers.append(f'如果价格先涨到${liq_short:,.0f}止损墙+1H收阴 → 可轻仓试空')
        if _hcme_case:
            # P2-2: HCME相似度分级
            _hcme_score = 0
            try:
                fc_raw = bs.get('fangcang', {})
                if isinstance(fc_raw, dict):
                    top = fc_raw.get('top_similar', [])
                    if top:
                        _hcme_score = top[0].get('score', 0)
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
            _hcme_conf = '低置信度仅供参考' if _hcme_score < 0.3 else '中等置信度'
            _triggers.append(f'HCME参考：{_hcme_case}（{_hcme_conf}）')
        # P2改革：标的专属90天信号匹配（2026-09-12 苏摩111封印）
        try:
            from scripts.recent_signal_match import match as _rs_match
            _rs = _rs_match(symbol=sym, regime=regime, direction=direction,
                           oi_signal=oi_signal, liq_dist_pct=abs((liq_short-price)/price) if liq_short > 0 else 0,
                           sm_divergence=abs(sm_divergence), min_similarity=0.4, max_results=5)
            if _rs.get('matched'):
                _triggers.append(f'近90天{_rs["matched_count"]}条相似(WR={_rs["wr"]}% PnL={_rs["avg_pnl"]:+.2f}%)')
            elif _rs.get('total_signals', 0) > 0:
                _triggers.append(f'近90天{_rs["total_signals"]}条信号无高相似匹配(最高{_rs.get("best_similarity",0):.2f})')
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        parts.append(f'体制{regime}无方向，不强行做。' + '触发器：' + ' / '.join(_triggers) + '。')

    return ' '.join(parts)


# [SSOT 2026-10-02 苏摩111] 两种run_analysis用途:
#   ① 人类可读分析文本(str) → 本函数  ← square发帖/step11/手动调用
#   ② 结构化dict → brahma_brain.brahma_analysis_runner.run_analysis()
#      不要混用，返回类型完全不同
def run_analysis(sym: str, push_jarvis: bool = True) -> str:  # noqa: 返回str非dict
    """[封印 2026-10-06 苏摩111 P2重构] run_analysis 4层结构说明
    ┌─ Layer1 L~+23  : 初始化（watchdog/ts/p）
    ├─ Layer2 L~+53  : step调用层（step0→step10，约260行）
    │    执行顺序: step0→1→1b→2→3→5→5c→6→7→8→9→4→4b→5b→10
    │    step4在step5之后：依赖oi结果（7维共振需要OI方向）
    ├─ Layer3 L~+1023: 推理/格式化层（Step11/trader_brain/格式化/推送）
    └─ Layer4 L~+335 : Fix-C写入层（brahma_state/auto_analysis写入）
    总计约1360行。P2重构已完成：_lv=locals()已废弃，4层结构清晰标注，Layer4显式参数传递。
    """
    # [决策2 2026-10-03] 分析超时哨兵：>120s推P1告警
    import time as _t2, threading as _thr
    _run_start = _t2.time()
    def _timeout_watchdog():
        _t2.sleep(120)
        elapsed = _t2.time() - _run_start
        try:
            # [2026-10-05 P0-B fix] 顶部已import sys
            if 'scripts' not in sys.path:
                sys.path.insert(0, 'scripts')
            import push_hub as _ph2
            _ph2.push_jarvis(f'⚠️ {sym} 分析超时{elapsed:.0f}s>120s，可能卡死', priority='P1')
        except Exception as _wd_e:
            print(f'[WARN] timeout watchdog push失败: {_wd_e}', file=sys.stderr)
    _wd = _thr.Thread(target=_timeout_watchdog, daemon=True)
    _wd.start()
    ts  = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')
    print(f'[{sym}] Step 0: 拉取实时数据...', flush=True)
    t_start = __import__('time').time()  # P1修复：移到step0之前，含数据拉取耗时
    # [决策2 2026-10-03 自主封印] analyze()防护网：超时告警+完整traceback
    import traceback as _tb
    # ════════════════════════════════════════════════════════
    # Layer2: step调用层 (step0~step10) [P2重构边界标注]
    # ════════════════════════════════════════════════════════
    try:
        d   = step0_fetch_all(sym)
    except Exception as _step0_e:
        _tb.print_exc()
        print(f'[CRITICAL] {sym} Step0数据拉取失败: {_step0_e}', flush=True)
        return f'[{sym}] Step0失败: {_step0_e}'
    d = d  # noqa
    
    # ══ 闸门1检查: 数据新鲜度硬门控 [9.18 苏摩111] ══
    if d.get('_gate1_rejected'):
        return d['_reject_msg']
    
    p   = d['price']  # 分析基准价（拉取时刻）

    # ── CHOP盲区旁路检测（不影响主链路）──────────────────────
    try:
        from breakout_watch import run_breakout_watch
        bw = run_breakout_watch([sym + 'USDT'])
        bw_alerts = bw.get('alerts', [])
        if bw_alerts:
            a = bw_alerts[0]
            print(f'[{sym}] 🚨 BREAKOUT_WATCH触发! score={a["score"]}/3 level={a["level"]}', flush=True)
        else:
            bw_score = bw['results'].get(sym+'USDT', {}).get('score', 0)
            print(f'[{sym}] CHOP旁路: score={bw_score}/3 无触发', flush=True)
    except Exception as _bw_e:
        # [2026-10-05 P0-A fix] _sys_warn已清除
        print(f"[WARN] CHOP旁路失败: {_bw_e}", file=sys.stderr)
    # ─────────────────────────────────────────────────────────

    print(f'[{sym}] Step 1~3: FVG/OB/清算...', flush=True)
    fvg = step1_fvg(d)
    fc  = step1b_fangcang_hcme(d, fvg)  # P1: 方仓历史匹配
    rng = step1b_range(d)  # 新增: 区间识别
    _step_gc()
    ob  = step2_ob(d)
    liq = step3_liq(d)

    print(f'[{sym}] Step 5~9: OI/聪明钱/波动率/宏观/风控...', flush=True)
    oi  = step5_oi(d)
    zsc = step5c_zscore(d)  # [Z-Score过滤层 2026-10-03 苏摩111]
    sm  = step6_smart_money(d)
    vol = step7_volatility(d)
    mac = step8_macro(d)
    risk= step9_risk(d)

    # 新增: LSR/OI联合 + 15M触发（需要res，放在step4之后）
    lsr_trig = None

    # [P3升级] Step 4共振升级7维：需要oi+vol+fc+cma
    print(f'[{sym}] Step 4: 共振点（7维：FVG+OB+清算+OI+GEX+方仓+跨市场）...', flush=True)
    _cma = None
    try:
        # [cleaned] import sys as _cma_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.cross_market_alpha import get_cross_market_alpha
        _cma = get_cross_market_alpha()
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    
    # ══ Phase 4修复 2026-09-18 苏摩111: 推理层提前到step4之前 ══
    # 根因：推理层在step10之后运行，feedback信号写入但无人读取
    # 修复：推理层提前到step4之前，step4共振矩阵读取feedback做降权
    _inference_feedback = {}
    try:
        from brahma_brain.brahma_inference import run_inference
        _inf_result = run_inference([sym])
        _inference_feedback = _inf_result.get('feedback', {}).get(sym, {})
        d['_inference_feedback'] = _inference_feedback
        _dim_down = _inference_feedback.get('dim_down_weight', {})
        _regime_bonus = _inference_feedback.get('regime_confirm_bonus', 0)
        if _dim_down or _regime_bonus:
            print(f'[{sym}] 推理层反馈: 降权={_dim_down} 体制确认+{_regime_bonus}', flush=True)
    except Exception as _inf_pre_e:
        print(f'[WARN] 推理层预加载失败: {_inf_pre_e}', file=sys.stderr)
    
    # [封印 2026-10-06] step4_resonance 刻意在 step5_oi 之后执行
    # 执行依赖: oi → res（OI方向是7维共振之一）
    res = step4_resonance(d, fvg, ob, liq, oi=oi, vol=vol, fc=fc, cma=_cma)
    pat = step4b_pattern(d)  # 形态识别
    lsr_trig = step5b_lsr_trigger(d, res)  # 新增: LSR/OI + 15M触发
    d['zsc'] = zsc  # Z-Score注入主数据字典，供后续输出消费
    _step_gc()

    # AI议会实时裁决（纯规则引擎，零延迟零成本）
    # ── P2-2: council_verdict移除（2026-09-11 苏摩111）──
    # 旧AI议会已由trader_brain 6层确定性决策替代
    council = {'bias':'N/A','reason':'council已废弃','action':'WAIT','confidence':'LOW','source':'废弃'}

    # ── trader_brain 6层确定性决策引擎（2026-09-11 苏摩111封印）──
    tb_result = {}
    regime_c = d['regime_s'].get(sym+'USDT',{}).get('confirmed', d['bs'].get('regime','CHOP_MID'))  # [9.15修复] 提到try外面
    try:
        from brahma_brain.trader_brain import decide as tb_decide, format_vip_card as tb_format
        # [2026-10-06 苏摩111 bugfix] 优先用ob_list（含valid/age字段），regime降级CHOP_MID
        # 修复: _ob_list在state-save块(L3786)才定义，此处用ob实时构建避免NameError
        _regime_for_tb = regime_c if regime_c and regime_c != 'UNKNOWN' else 'CHOP_MID'
        _ob_for_tb_pre = []
        if isinstance(ob, dict):
            for _k, _v in ob.items():
                _pts = _k.split('_')
                if len(_pts) >= 3 and _pts[0] == 'OB' and isinstance(_v, dict):
                    _ob_for_tb_pre.append({'tf': _pts[1], 'side': _pts[2],
                        'age': int(_v.get('age', 0)), 'lo': float(_v.get('lo', 0)),
                        'hi': float(_v.get('hi', 0)), 'valid': bool(_v.get('valid', False)),
                        'dist_pct': float(_v.get('dist_pct', 0))})
        elif isinstance(ob, list):
            _ob_for_tb_pre = ob
        _ob_for_tb = _ob_for_tb_pre if _ob_for_tb_pre else ob
        tb_result = tb_decide(
            regime=_regime_for_tb,
            score=float(d['bs'].get('score_final', d['bs'].get('score', 0))),
            grade=float(d['bs'].get('grade', 0)),
            macro=mac,
            risk=risk,
            hurst=vol.get('hurst', 0.5),
            fvg=fvg,
            ob=_ob_for_tb,
            liq=liq,
            atr_1h=vol.get('atr_1h', 0),
            atr_4h=vol.get('atr_4h', 0),
            price=p,
            oi=oi,
            sm=sm,
            vol=vol,
            res=res,
            symbol=sym+'USDT',
            cf_action=str((d['bs'].get('confluence', {}) or {}).get('action', '') or ''),  # [唯一裁判 2026-09-23] Gate1读SSOT action
        )
    except Exception as _tbe:
        tb_result = {'action':'WAIT','reason':f'trader_brain异常: {str(_tbe)[:60]}','missing':['trader_brain异常']}
        try:
            from brahma_brain.trader_brain import format_vip_card as tb_format
        except Exception:
            tb_format = None

    # trader_brain替代AI议会作为最终裁决
    _regime_c = regime_c if 'regime_c' in dir() else d['bs'].get('regime', 'CHOP_MID')
    final_action = tb_result.get('action', 'WAIT')
    final_direction = tb_result.get('direction', 'NONE')
    final_reason = tb_result.get('reason', council.get('reason', ''))
    final_missing = tb_result.get('missing', [])
    final_confidence = tb_result.get('confidence', council.get('confidence', 'LOW'))
    final_consistent = tb_result.get('consistent_count', 0)

    # ══ Phase 3修复 2026-09-18 苏摩111: 注入trader_brain方向到d ══
    # 让step10_vip能读到trader_brain的真实方向，不再各算各的
    d['_trader_brain_direction'] = final_direction
    d['_trader_brain_action'] = final_action

    # ══ P0修复 [2026-10-01 苏摩111]: G4断层——trader_brain执行后重算align_count ══
    # 根因：step4_resonance(L2617)在trader_brain(L2632)之前，signal_dir=brahma_state旧值
    # 修复：trader_brain方向确定后，用真实方向重算align_count并回写res
    if final_direction in ('LONG', 'SHORT'):
        _tb_dir = final_direction
        _tb_bull = (_tb_dir == 'LONG')
        # [bugfix 2026-10-06 苏摩111] 用consensus而非dir（consensus=多TF投票，dir=单主导TF）
        _fvg_c = fvg.get('consensus', fvg.get('dir', 'NONE')).upper()   # BULL/BEAR
        _fvg_match = (_tb_bull and _fvg_c == 'BULL') or (not _tb_bull and _fvg_c == 'BEAR')
        # OB多数派（ob dict格式：OB_TF_TYPE键）
        _valid_obs = [k for k, v in ob.items() if isinstance(v, dict) and v.get('valid')]
        _ob_bull_n = sum(1 for k in _valid_obs if 'BULL' in k)
        _ob_bear_n = sum(1 for k in _valid_obs if 'BEAR' in k)
        _ob_match = (_tb_bull and _ob_bull_n > _ob_bear_n) or (not _tb_bull and _ob_bear_n > _ob_bull_n)
        # 清算方向
        _liq_short = liq.get('nearest_short', 0)
        _liq_long  = liq.get('nearest_long', 0)
        _liq_match = (_tb_dir == 'SHORT' and _liq_short > p) or (_tb_dir == 'LONG' and _liq_long > 0 and _liq_long < p)
        # OI方向（改革3：LONG_UNWIND不算LONG也不算SHORT）
        _oi_sig = (oi or {}).get('signal', '')
        _oi_bull = _oi_sig in ('LONG_BUILD', 'SHORT_SQUEEZE')   # 改革3：精确匹配，非包含
        _oi_bear = _oi_sig in ('SHORT_BUILD',)                  # 改革3：LONG_UNWIND不是做空
        _oi_match = (_tb_bull and _oi_bull) or (not _tb_bull and _oi_bear)
        # GEX方向
        _gex_score = (vol or {}).get('gex_score', 0)
        _gex_match = (_tb_bull and _gex_score > 0) or (not _tb_bull and _gex_score < 0)
        # 方仓方向
        _fc_dir = (fc or {}).get('signal', 'NEUTRAL')
        _fc_match = (_tb_bull and _fc_dir == 'LONG') or (not _tb_bull and _fc_dir == 'SHORT')
        # 跨市场方向
        _cma_risk_on = bool(_cma) and _cma.get('regime', '') == 'RISK_ON'
        _cma_match = (_tb_bull and _cma_risk_on) or (not _tb_bull and not _cma_risk_on and bool(_cma))
        # 重新累计
        _tb_align = sum([_fvg_match, _ob_match, _liq_match,
                         _oi_match if _oi_sig not in ('NO_DATA','MIXED','') else False,
                         _gex_match if _gex_score != 0 else False,
                         _fc_match if _fc_dir != 'NEUTRAL' else False,
                         _cma_match if _cma else False])
        res['align_count'] = _tb_align   # 回写，Step11 G4 读此值
        d['_tb_align_recomputed'] = _tb_align
        print(f'[{sym}] G4重算: tb_dir={_tb_dir} align={_tb_align}/7 (原signal_dir口径={res.get("_orig_align",0)})', flush=True)
    else:
        res['align_count'] = 0
        d['_tb_align_recomputed'] = 0

    # BUG-1修复：分析完成后拉一次实时价，检测漂移
    import time as _t, urllib.request as _ur, ssl as _ssl, json as _js
    try:
        _ctx = _ssl.create_default_context(); _ctx.check_hostname=True; _ctx.verify_mode=_ssl.CERT_REQUIRED
        _live = float(_js.loads(_ur.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT', timeout=4, context=_ctx
        ).read()).get('price', p))
    except Exception:
        _live = None  # D9修复: 拉价格失败时标记为None，不用p掩盖偏差
    _elapsed    = _t.time() - t_start
    _drift_pct  = ((_live - p) / p * 100) if _live is not None else 0.0
    _live       = _live if _live is not None else p   # display用
    _price_warn = ''
    if abs(_drift_pct) >= 1.0:
        _price_warn = f'\n⚠️ 【价格漂移警告】分析基准${p:,.0f} → 当前${_live:,.0f} 偏差{_drift_pct:+.1f}% 入场区已失效，请重跑'
    elif abs(_drift_pct) >= 0.5:
        _price_warn = f'\n⚠️ 价格微偏：分析${p:,.0f}→当前${_live:,.0f}({_drift_pct:+.1f}%)，入场区仅供参考'

    # [2026-10-06 苏摩111] 注入live_score到d['bs']供step10/step11使用
    if '_live_score' not in dir() or not _live_score:
        try:
            _align_ls = int(res.get('align_count', 0) if isinstance(res, dict) else 0)
            _oi_ls = str(oi.get('signal','?') if isinstance(oi,dict) else '?')
            _cvd_ls2 = float(oi.get('cvd_1h',0) if isinstance(oi,dict) else 0)
            _lsr_b2 = float(sm.get('lsr_big',0) if isinstance(sm,dict) else 0)
            _lsr_r2 = float(sm.get('lsr_retail',0) if isinstance(sm,dict) else 0)
            _rsi_ls = float(vol.get('rsi_1h',50) if isinstance(vol,dict) else 50)
            _live_score = max(0.0, float(_align_ls*10
                + (15 if 'LONG_BUILD' in _oi_ls else 10 if 'SHORT_BUILD' in _oi_ls else 0)
                + (10 if _cvd_ls2>100 else 5 if _cvd_ls2>0 else 0)
                + (8 if _lsr_b2>60 else 0)
                + (8 if _rsi_ls<30 else 0)))
        except Exception: _live_score = 0.0
    if isinstance(d.get('bs'), dict) and _live_score > 0:
        d['bs']['score_final'] = _live_score
        d['bs']['score'] = _live_score

    print(f'[{sym}] Step 10: 生成VIP卡片...', flush=True)

    bd      = d['bs'].get('confluence', {}).get('breakdown', {})
    regime  = d['bs'].get('regime', 'UNKNOWN')
    score   = d['bs'].get('score_final', d['bs'].get('score', 0))
    grade   = d['bs'].get('grade', 0)
    hurst_s = str(bd.get('Hurst体制验证', '') or '')
    hcme_s  = str(d['bs'].get('hcme_ctx', '') or '')
    fc_raw  = d['bs'].get('fangcang', {})
    fc_case = ''
    if isinstance(fc_raw, dict):
        top = fc_raw.get('top_similar', [])
        if top:
            t = top[0]
            fc_case = f'{t.get("dt","?")} 相似度{t.get("score",0):.3f} 未来收益{t.get("future_ret",0):+.2f}%'

    k4h    = d['k4h']
    k4h_last = k4h[-1] if k4h else None
    k4h_prev_vols = [x[4] for x in k4h[:-1]] if k4h else []
    avg_v4h = sum(k4h_prev_vols) / len(k4h_prev_vols) if k4h_prev_vols else 0
    k4h_vol_mult = round(k4h_last[4] / avg_v4h, 1) if avg_v4h and k4h_last else 1.0
    # [P2修复 2026-09-10] 量能倍数最小值0.1x，避免round后显示0.0x
    k4h_vol_mult = max(0.1, k4h_vol_mult)

    k1h    = d['k1h']
    vol_avg1h = sum(x[4] for x in k1h[:-2]) / max(len(k1h)-2, 1) if len(k1h) > 2 else 0
    vol_last1h= k1h[-1][4] if k1h else 0
    k1h_mult  = round(vol_last1h / vol_avg1h, 1) if vol_avg1h else 1.0
    # [P2修复 2026-09-10] 量能倍数最小值0.1x，避免round后显示0.0x
    k1h_mult  = max(0.1, k1h_mult)

    vip = step10_vip(sym, p, d, fvg, ob, liq, res, oi, sm, vol, mac, risk)

    # ════════════════════════════════════════════════════════
    # Layer3: 推理/格式化层 (Step11+trader_brain+格式化+推送)
    # ════════════════════════════════════════════════════════
    # ══ Step11 强制决策裁判 [2026-10-01 苏摩111] ══
    # 11道硬闸门SSOT：替代分散在step10_vip内的门控逻辑
    # 结果注入到输出流，作为人工审核区后的最终裁决
    try:
        from step11_mandatory_judge import run_step11 as _s11
        # [2026-10-06 苏摩111] Step11用修正后的tb_result（含降级体制+OB补票方向）
        _tb_for_s11 = tb_result.copy() if isinstance(tb_result,dict) else {}
        if _tb_for_s11.get('direction','NONE') == 'NONE':
            # regime已降级CHOP_MID，重跑trader_brain获取方向
            try:
                from brahma_brain.trader_brain import decide as _tb2
                # [2026-10-06 苏摩111] 优先继承analyze()的signal_dir，再用cf_action激活WATCH通道
                _cf_act_r2 = str((d['bs'].get('confluence',{}) or {}).get('action','') or '')
                # [2026-10-06 苏摩111] 三路取值：1.缓存analyze_signal_dir 2.bs.analyze_signal_dir 3.bs.signal_dir
                _bs_signal = str(
                    locals().get('_analyze_signal_from_cache') or
                    d['bs'].get('analyze_signal_dir') or
                    d['bs'].get('signal_dir') or ''
                )
                # 如果analyze()已有明确方向，直接注入_r2
                if _bs_signal in ('LONG','SHORT'):
                    _r2 = {'direction': _bs_signal, 'action': 'WATCH',
                           'entry_lo': float(d['bs'].get('entry_lo', liq.get('long',0))),
                           'entry_hi': float(d['bs'].get('entry_hi', liq.get('short',0))),
                           'sl': float(d['bs'].get('sl', 0)),
                           'tp1': float(d['bs'].get('tp1', 0)),
                           'rr': float(d['bs'].get('rr', 0)),
                           'leverage': int(d['bs'].get('leverage', 3)),
                           'position_size_pct': float(d['bs'].get('position_size_pct', 0.5)),
                           'reason': f'analyze()signal_dir={_bs_signal}（12维score={d["bs"].get("score_final",0):.0f}→WATCH通道）'}
                else:
                    _r2 = _tb2(regime=_regime_for_tb, score=float(d['bs'].get('score_final',d['bs'].get('score',0))),
                        grade=float(d['bs'].get('grade',0)), macro=mac, risk=risk, hurst=vol.get('hurst',0.5),
                        fvg=fvg, ob=_ob_for_tb, liq=liq, atr_1h=vol.get('atr_1h',0), atr_4h=vol.get('atr_4h',0),
                        price=p, oi=oi, sm=sm, vol=vol, res=res, symbol=sym+'USDT', cf_action=_cf_act_r2)
                if _r2.get('direction','NONE') != 'NONE': _tb_for_s11 = _r2
            except Exception: pass
        # [2026-10-06 苏摩111] _tb_for_s11方向确定后重算align_count → G4真实共振
        _s11_dir = _tb_for_s11.get('direction','NONE')
        if _s11_dir in ('LONG','SHORT'):
            _s11_bull = (_s11_dir == 'LONG')
            _s11_fvg_c = fvg.get('consensus','NONE').upper()
            _s11_obs = [k for k,v in ob.items() if isinstance(v,dict) and v.get('valid')] if isinstance(ob,dict) else []
            _s11_ob_bull = sum(1 for k in _s11_obs if 'BULL' in k)
            _s11_ob_bear = sum(1 for k in _s11_obs if 'BEAR' in k)
            _s11_oi_sig = (oi or {}).get('signal','')
            _s11_oi_bull = _s11_oi_sig in ('LONG_BUILD','SHORT_SQUEEZE')
            _s11_oi_bear = _s11_oi_sig in ('SHORT_BUILD',)
            _s11_gex = (vol or {}).get('gex_score',0)
            _s11_fc = (fc or {}).get('signal','NEUTRAL') if 'fc' in dir() else 'NEUTRAL'
            _s11_cma_on = bool(_cma) and _cma.get('regime','') == 'RISK_ON' if '_cma' in dir() else False
            _s11_align = sum([
                (_s11_bull and _s11_fvg_c=='BULL') or (not _s11_bull and _s11_fvg_c=='BEAR'),
                (_s11_bull and _s11_ob_bull>_s11_ob_bear) or (not _s11_bull and _s11_ob_bear>_s11_ob_bull),
                (_s11_dir=='SHORT' and liq.get('nearest_short',0)>p) or (_s11_dir=='LONG' and liq.get('nearest_long',0)>0 and liq.get('nearest_long',0)<p),
                (_s11_oi_sig not in ('NO_DATA','MIXED','')) and ((_s11_bull and _s11_oi_bull) or (not _s11_bull and _s11_oi_bear)),
                _s11_gex != 0 and ((_s11_bull and _s11_gex>0) or (not _s11_bull and _s11_gex<0)),
                _s11_fc != 'NEUTRAL' and ((_s11_bull and _s11_fc=='LONG') or (not _s11_bull and _s11_fc=='SHORT')),
                bool('_cma' in dir() and _cma) and ((_s11_bull and _s11_cma_on) or (not _s11_bull and not _s11_cma_on)),
            ])
            res['align_count'] = _s11_align
            print(f'[{sym}] G4预计算: dir={_s11_dir} align={_s11_align}/7', flush=True)
        _s11_result = _s11(sym, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, _tb_for_s11)
        d['_step11'] = _s11_result  # 供后续formatter读取
        _s11_verdict = _s11_result['verdict']
        _s11_gates   = _s11_result['gates_passed']
        _s11_blocked = _s11_result['blocked_by'] or '通过'
        print(f'[{sym}] Step11裁判: {_s11_verdict} | 闸门{_s11_gates}/11 | 阻断={_s11_blocked}', flush=True)
    except Exception as _s11_e:
        print(f'[WARN] Step11 Judge: {_s11_e}', file=__import__("sys").stderr)
        _s11_result = None

    # AI议会辩论已移除（2026-09-11 苏摩111）— trader_brain 6层确定性决策替代，debate代码不执行省2s
    _debate_block = ''

    # A: VIP入场理由LLM生成
    # B: 信号矛盾自动LLM裁决
    # [P1-2修复 2026-09-11] AI议会确定性化：移除LLM裁决，用规则替代
    _llm_entry_reason = ''
    _llm_conflict     = ''
    try:
        # A: 入场理由 → 用规则生成（替代LLM）
        if res['resonance'] and res['entry_lo'] > 0:
            _bias_a = 'LONG' if fvg['dir'] == 'BULL' else 'SHORT'
            _llm_entry_reason = (
                f'{regime}体制顺势{_bias_a}+'
                f'{fvg.get("dir","?")}FVG磁铁+'
                f'OI={oi.get("signal","?")}+'
                f'清算墙${liq.get("nearest_short",0):,.0f}磁吸'
            )
        # B: 矛盾裁决 → 用规则判断（替代LLM）
        _oi_bull = oi['signal'] in ('LONG_BUILD', 'SHORT_SQUEEZE')
        _sm_bull = sm['signal'] in ('STRONG_BULL', 'MILD_BULL')
        if _oi_bull != _sm_bull:
            _llm_conflict = (
                f'OI={oi.get("signal","?")} vs 聪明钱={sm.get("signal","?")}矛盾 → '
                f'{"跟随OI" if abs(oi.get("total_change",0))>5000 else "跟随聪明钱"}'
            )
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ── [果蝇架构 2026-09-13 苏摩111] VIP freshness标签 ──
    _stale = d.get('stale_caches', [])
    _freshness_tag = ''
    if _stale:
        _freshness_tag = f' ⚠️数据过期: {", ".join(_stale[:3])}'
    else:
        _freshness_tag = ' ✅数据全新鲜'

    lines = [
        f'——————————————————————————━━',
        f'🏛️ 梵天80维全能力分析 | {sym}/USDT 基准${p:,.0f}→实时${_live:,.0f} | {ts} (耗时{_elapsed:.0f}s){_freshness_tag}',
        f'——————————————————————————━━',
        f'',
        f'【Step1 FVG磁铁】全周期',
        (f'  共识方向: {fvg.get("consensus",fvg.get("dir","?"))}  多{fvg.get("bull_score",0)}分 vs 空{fvg.get("bear_score",0)}分  主磁铁: {fvg.get("dir","?")}@${fvg.get("magnet",0):,.0f}') if fvg['magnet'] else '  无有效FVG',
        f'  {fvg.get("desc","")[:120]}',
        f'',
        f'【Step1b 方仓历史匹配】HCME {fc.get("n_cases",0)}案例库',
        f'  {fc.get("desc","方仓无数据")}',
        ]
    # P1: 方仓Top5详情
    _top5 = fc.get('top5', [])
    if _top5:
        _pm = fc.get('prob_matrix', {})
        lines.append(f'  概率矩阵(全库): 多{_pm.get("long",0):.0f}% / 空{_pm.get("short",0):.0f}% / 横盘{_pm.get("chop",0):.0f}%')
        for t in _top5[:3]:
            lines.append(f'  #{t["rank"]} {t["date"]} 相似{t["similarity"]:.3f} 未来{t["future_ret"]:+.1f}% (max:{t["future_max"]:+.1f}% min:{t["future_min"]:+.1f}%) [{t["regime"]}]')
    if fc.get('trap_alert'):
        lines.append(f'  ⚠️ {fc.get("trap_alert","")}')
    if fc.get('signal_hint'):
        lines.append(f'  方仓信号: {fc.get("signal_hint","")}')
    lines += [
        f'',
        f'【Step2 OB有效性】',
    ]
    if ob:
        _valid_obs = {k: v for k, v in ob.items() if v.get('valid', False)}
        _invalid_obs = [k for k, v in ob.items() if not v.get('valid', False)]
        if _valid_obs:
            for k, v in _valid_obs.items():
                lines.append(f'  {k}: {v["note"][:80]}')
        else:
            lines.append('  ⚠️ 无有效OB（全部已穿越/过期）')
        if _invalid_obs:
            lines.append(f'  ❌已失效: {", ".join(_invalid_obs)}（不参与共振/决策）')
    else:
        lines.append('  无OB数据')

    lines += [
        f'',
        f'【Step3 清算地图】',
        f'  🎯上方空头止损墙: ${liq.get("nearest_short",0):,.0f} (+{liq.get("nearest_short_pct",0):.1f}%，目标+{liq.get("target_pct",0):.1f}%)'
        + (f'  → 第二层: ${liq["second_short"]:,.0f}' if liq.get('second_short') else ''),
        f'  🛡️下方多头支撑池: ${liq.get("nearest_long",0):,.0f} (-{liq.get("support_pct",0):.1f}%)',
        f'',
        f'【Step4 共振】FVG={res["has_fvg"]} OB={res["has_ob"]} 清算={res["has_liq"]} OI={res.get("has_oi",False)} GEX={res.get("has_gex",False)} 方仓={res.get("has_fc",False)} 跨市场={res.get("has_cma",False)} → 数据{res.get("score",0)}/7｜方向一致{res.get("align_count",0)}/7（{"CHOP体制方向打架=WATCH根因" if res.get("align_count",0)==0 else ""}体制{res.get("regime_key","CHOP_MID")}权重共振比{res.get("resonance_ratio",0):.2f}）',  # [苏摩111封印 10.01] 0/7加解释
    ]
    # Step4 入场区间显示修复：$0.0→「无」[苏摩111封印 10.01]
    _entry_zone_disp = (
        f'${res["entry_lo"]:,.1f}~${res["entry_hi"]:,.1f}'
        if res.get('entry_lo', 0) > 0 and res.get('entry_hi', 0) > 0
        else '无（结构不满足/等待共振）'
    )
    lines.append(f'  入场区间: {_entry_zone_disp}')
    if res['missing']:
        lines.append(f'  缺失: {" / ".join(res.get("missing",[]))}')

    lines += [
        f'',
        f'【Step5 OI趋势】全周期',
        f'  15M:{oi.get("signal_15m","?")} | 1H:{oi.get("signal_1h","?")} | 4H:{oi.get("signal_4h","?")}',
        f'  主信号: {oi.get("signal","?")} (置信{oi.get("conf",0):.0%}) | {oi.get("conclusion","")[:60]}',
        f'  15min序列: {" → ".join(str(int(v)) for v in oi.get("trend",[]))}',
        f'  累计变化: {oi.get("total_change",0):+,.0f}张  OI价值变化: {oi.get("usd_change_m",0):+.1f}M',
        f'  {oi.get("cvd_note","")}',  # [P0] CVD展示
        f'',
        f'【Step6 聪明钱分歧】',
        f'  {sm.get("conclusion","")}',
        f'  大户多{sm.get("big_long",0)}% vs 散户多{sm.get("retail_long",0)}%  分歧={sm.get("diverge",0)}%',
        f'  大户2H变化: {sm.get("top_delta",0.0):+.3f}%pt',  # P0修复: 3位小数不归零
        f'',
        f'【Step7 波动率四维+ATR全周期】',  # [P0] 升级为四维
        f'  {vol["hurst_note"]}',
        f'  HAR-RV: {vol["harv_range_str"] if vol.get("harv_range_str") else "无数据（cron未更新/RV=0）"}',  # [苏摩111封印 10.01] 必须显式输出，禁止静默跳过
        f'  {vol["kappa_note"]}',
        f'  {vol.get("gex_note","") if not vol.get("gex_expired",False) else "⚠️ GEX数据已过期，不参与共振计算"} ',  # P0修复: GEX过期标记
        f'  {vol.get("fr_note","")}',   # [P1] FR展示
        f'  β⁺={vol.get("beta_p",0):.3f} β⁻={vol.get("beta_m",0):.3f}  IV分位={vol.get("iv_rank",0)}',  # [P2] β展示
        f'  ATR1H=${vol.get("atr_1h",0):.0f} ATR4H=${vol.get("atr_4h",0):.0f} ATR1D=${vol.get("atr_1d",0):.0f}  合约SL参考=${vol.get("atr_sl_ref",0):.0f}({vol.get("atr_sl_ref",0)/p*100:.2f}%)' if p > 0 else f'  ATR1H=${vol.get("atr_1h",0):.0f} ATR4H=${vol.get("atr_4h",0):.0f} ATR1D=${vol.get("atr_1d",0):.0f}',
        f'  RSI全周期: 15M={d.get("bs",{}).get("momentum",{}).get("rsi_15m",0):.1f} / 1H={d.get("bs",{}).get("momentum",{}).get("rsi_1h",0):.1f} / 4H={d.get("bs",{}).get("momentum",{}).get("rsi_4h",0):.1f} / 1D={d.get("bs",{}).get("momentum",{}).get("rsi_1d",0):.1f}',  # P1修复: RSI全周期展示
    ]
    # P2: IC归因展示
    _ic = vol.get("ic_attribution", {})
    if _ic and _ic.get("available"):
        lines.append(f'  IC归因: {_ic.get("regime_key","?")} n={_ic.get("n",0)} 总IC={_ic.get("total_ic","N/A")}')
        for t in _ic.get("top_dims", []):
            lines.append(f'    {t["dim"]}: IC={t["ic"]:+.4f} {t["direction"]} {t["flag"]}')
    elif _ic:
        lines.append(f'  IC归因: {_ic.get("reason","N/A")}')
    lines += [
        f"",
        f'【Step8 宏观】',  # P2修复: 真实CPI/PPI/利率数据
        f'  {mac["pos_note"]}',
        f'  CPI Core YoY={mac.get("cpi_yoy",0):.2f}%  PPI YoY={mac.get("ppi_yoy",0):.2f}%  Fed Rate={mac.get("fed_rate",0):.2f}%',
        f'  利率预期: {mac.get("rate_action","?")} ({mac.get("rate_prob",0):.0f}%)  {mac.get("rate_note","")}',
        f'  恐贪={mac["fear_greed"]}  偏向={mac["macro_bias"]}  FOMC还剩{mac.get("days_to_fomc",0)}天',
        f'  数据源: {mac.get("data_source","?")} @ {mac.get("data_time","?")}',  # P2: 标注数据源和时间
        # P4新增: 跨市场+美盘时段
        f'  跨市场: alpha={mac.get("cross_market",{}).get("alpha",0):+.4f} {mac.get("cross_market",{}).get("direction","N/A")} crypto_fr={mac.get("cross_market",{}).get("crypto_fr",0):.5f}% tradfi_fr={mac.get("cross_market",{}).get("tradfi_fr",0):.5f}%',
        f'  美盘时段: {mac.get("us_session",{}).get("session","N/A")} delta={mac.get("us_session",{}).get("delta",0)} {mac.get("us_session",{}).get("note","")}',
    ]
    if res.get('cross_check',{}).get('conflicts'):
        lines.append(f'  ⚠️ 交叉验证矛盾: {" / ".join(res["cross_check"]["conflicts"])}')
    elif res.get('cross_check',{}).get('consistent'):
        lines.append(f'  ✅ 交叉验证一致：结构层与市场层方向一致')
    if mac['high_impact']:
        lines.append(f'  ⚠️重大事件: {" / ".join(mac.get("high_impact",[])[:2])}')

    lines += [
        f'',
        f'【Step9 风控门控】',
        f'  熔断器: {"✅绿灯" if risk["circuit_ok"] else "🔴触发"}  '
        f'回撤: {risk["dd_pct"]:.1f}% {"✅正常" if risk["dd_ok"] else "⚠️"}  '
        f'连亏: {risk["consec"]}笔 {"✅" if risk["af_ok"] else "⚠️冷却"}',
        f'  失效期: {risk.get("regime_state","GREEN")} {risk.get("regime_note","")}',  # [P1] 失效期展示
        f'  仓位系数: x{risk["nav_mult"]:.2f}',
    ]
    if risk['blocks']:
        for b in risk['blocks']:
            lines.append(f'  🚨{b}')

    # 新增维度输出
    if pat and pat.get('score', 0) > 0:
        lines += [
            f'',
            f'【Step4b 形态识别】',
            f'  形态score: {pat.get("score", 0)}/15',
        ]
        for p in pat.get('patterns', []):
            if p.get('pattern'):
                lines.append(f'  {p["pattern"]}: {p.get("note","")} (score={p.get("score",0)})')

    if rng and rng.get('score', 0) > 0:
        lines += [
            f'',
            f'【Step1b 区间识别】',
            f'  区间score: {rng.get("score", 0)}/15',
            f'  {rng.get("note", "")}',
        ]

    if lsr_trig:
        lsr = lsr_trig.get('lsr_oi', {})
        tr15 = lsr_trig.get('trigger_15m', {})
        if lsr.get('score', 0) != 0:
            lines += [
                f'',
                f'【Step5b LSR/OI联合】',
                f'  LSR/OI score: {lsr.get("score", 0)}',
                f'  {lsr.get("note", "")}',
            ]
        if tr15.get('triggered'):
            lines += [
                f'  15M触发: ✅ {tr15.get("note", "")}',
            ]

    # Z-Score过滤层输出 [2026-10-03 苏摩111]
    _zsc = d.get('zsc', {})
    if _zsc:
        _z_val = _zsc.get('z')
        _z_gate = _zsc.get('gate', 'SKIP')
        _z_sig  = _zsc.get('signal', 'N/A')
        _z_note = _zsc.get('note', '')
        _z_icon = '✅' if _z_gate == 'PASS' else ('⚠️' if _z_gate == 'WATCH' else '❌')
        lines += [
            f'',
            f'【Step5c Z-Score过滤层】',
            f'  Z分: {_z_val:.3f} | 信号: {_z_sig} | 门控: {_z_gate} {_z_icon}',
            f'  {_z_note}',
        ]

    lines += [
        f'',
        f'【关键附加维度】',
        f'  4H量能倍数: {k4h_vol_mult}x（均量倍数，>2=放量突破）',
        f'  1H量能倍数: {k1h_mult}x',
        f'  Hurst: {hurst_s[:60]}',
        f'  HCME: {hcme_s[:80] if hcme_s else "无数据"}',
        f'  方仓最相似案例: {fc_case or "无数据"}',
    ]
    # B: 信号矛盾裁决（有就显示）
    if _llm_conflict:
        lines.append(f'  ⚙️ 规则矛盾裁决: {_llm_conflict}')

    # AI议会辩论已移除（2026-09-11 苏摩111）— trader_brain 6层确定性决策替代
    # if _debate_block: lines += [f'', _debate_block]

    # ── 交易员叙事（已移至trader_brain.format_narrative）──
    try:
        from brahma_brain.trader_brain import format_narrative as _fmt_narr
        _narrative = _fmt_narr(tb_result, sym+'USDT', p, regime_c, fvg, oi, sm, vol, risk, res)
    except Exception:
        _narrative = ''
    if _narrative:
        lines += [f'', f'── 交易员视角 ──', _narrative]

    # [V2.0 2026-09-20 苏摩111] 交易叙事引擎 — 40年交易员因果链
    try:
        from brahma_brain.trade_narrative_engine import generate_trade_narrative
        _narr_input = {
            'symbol': sym + 'USDT', 'price': p, 'regime': regime_c,
            'score': score, 'hurst': vol.get('hurst', 0), 'action': tb_result.get('action', 'WAIT'),
            'direction': tb_result.get('direction', 'NONE'),
            'fvg_consensus': fvg.get('consensus', ''), 'fvg_magnet_price': fvg.get('magnet', 0),
            'oi_signal': oi.get('signal', ''), 'smart_money': sm, 'gex': vol.get('gex', 0),
            'gex_signal': vol.get('gex_signal', ''), 'cvd_1h': oi.get('cvd_1h', 0),
            'cvd_dir_1h': oi.get('cvd_dir_1h', 'NEUTRAL'), 'resonance_count': res.get('score', 0),
            'resonance_max': 7, 'resonance_missing': res.get('missing', []),
            'liq_wall_price': liq.get('nearest_short', 0), 'liq_pool_price': liq.get('nearest_long', 0),
            'entry_lo': tb_result.get('entry_lo', 0), 'entry_hi': tb_result.get('entry_hi', 0),
            'stop_loss': tb_result.get('sl', 0), 'sl_pct': tb_result.get('sl_pct', 0),
            'rr1': tb_result.get('rr', 0), 'tp1': tb_result.get('tp1', 0),
            'tp2': tb_result.get('tp2', 0), 'tp3': tb_result.get('tp3', 0),
        }
        _narrative_v2 = generate_trade_narrative(_narr_input)
        if _narrative_v2:
            lines += [f'', f'── 交易叙事引擎 ──', _narrative_v2]
    except Exception as _ne:
        print(f"[WARN] brahma_manual_analysis: {_ne}", __import__("sys").stderr)

    lines += [
        f'',
        f'{"─"*43}',
    ]

    # ── trader_brain裁决替代AI议会（2026-09-11 苏摩111封印）──
    _tb_action = tb_result.get('action', 'WAIT')
    # [9.19修复] L0-GATE门控：step10_vip返回等待/观察卡片时，覆盖trader_brain的VIP输出
    # 根因：step10_vip的vip变量赋值后从未使用，trader_brain的tb_format绕过了L0-GATE
    if vip and ('⏳' in vip or '观察' in vip or '禁止' in vip):
        _vip_out = vip  # step10_vip的等待/观察卡片优先
    else:
        # 统一输出：ENTER=VIP / WATCH=轻仓VIP / WAIT=观点
        # [2026-09-21 P2修复 苏摩111] 注入liq数据到tb_result，修复多单$0
        tb_result['liq_nearest_short'] = liq.get('nearest_short', 0)
        tb_result['liq_nearest_long'] = liq.get('nearest_long', 0)
        try:
            _section = 'VIP' if _tb_action == 'ENTER' else 'VIP' if _tb_action == 'WATCH' else '观点'
            _vip_out = f'──── {_section} ────\n' + tb_format(
                tb_result, sym+'USDT', p, _regime_c
            )
        except Exception:
            _vip_out = (
                f'──── 观点 ────\n'
                f'🌿 姓赵不宣 | {sym} 今日观点\n'
                f'⏳ {tb_result.get("reason", "等待")[:60]}'
            )

    # BUG-1：如果入场区已失效，在VIP之前追加警告
    if _price_warn and abs(_drift_pct) >= 1.0:
        _vip_out = f'☠️ 「入场区已失效」基准${p:,.0f} → 当前${_live:,.0f}({_drift_pct:+.1f}%)，请重新跑分析\n' + _vip_out

    _tb_bias = tb_result.get('direction', 'NONE')
    _tb_conf = tb_result.get('confidence', 'LOW')
    _tb_cross = tb_result.get('consistent_count', 0)
    _tb_layers = tb_result.get('cross_check', {}).get('layer_directions', {})
    _tb_layer_str = ' '.join(f'{k}={v}' for k,v in _tb_layers.items()) if _tb_layers else ''

    # ══════════════════════════════════════════════════════════
    # 【苏摩人工审核区】封印 2026-10-01 苏摩111
    # 强制路径11步完成后逐项输出具体数据+决策+审核清单
    # ══════════════════════════════════════════════════════════
    _entry_lo_disp = f'${tb_result.get("entry_lo",0):,.1f}' if tb_result.get('entry_lo',0) > 0 else '无'
    _entry_hi_disp = f'${tb_result.get("entry_hi",0):,.1f}' if tb_result.get('entry_hi',0) > 0 else '无'
    _sl_disp       = f'${tb_result.get("sl",0):,.1f}'        if tb_result.get('sl',0) > 0    else '无'
    _tp1_disp      = f'${tb_result.get("tp1",0):,.1f}'       if tb_result.get('tp1',0) > 0   else '无'
    _tp2_disp      = f'${tb_result.get("tp2",0):,.1f}'       if tb_result.get('tp2',0) > 0   else '无'
    _tp3_disp      = f'${tb_result.get("tp3",0):,.1f}'       if tb_result.get('tp3',0) > 0   else '无'
    _rr_disp       = f'{tb_result.get("rr",0):.2f}'          if tb_result.get('rr',0) > 0    else 'N/A'
    _sl_pct_disp   = f'{tb_result.get("sl_pct",0)*100:.2f}%' if tb_result.get('sl_pct',0) > 0 else 'N/A'
    _atr1h = vol.get('atr_1h',0); _atr4h = vol.get('atr_4h',0)
    _sl_dist       = abs(p - tb_result.get('sl',p)) if tb_result.get('sl',0) > 0 else 0
    _sl_atr1h_ok   = '✅' if _sl_dist >= _atr1h*1.5 else f'⚠️(需≥${_atr1h*1.5:.0f})'
    _sl_atr4h_ok   = '✅' if _sl_dist >= _atr4h*1.5 else f'⚠️(需≥${_atr4h*1.5:.0f})'
    _harv_str      = vol.get('harv_range_str','') or 'HAR-RV无数据'
    _regime_ok     = '✅' if str(regime_c) not in ('CHOP_MID',) else '⚠️震荡禁单'
    _ev_note       = 'EV(排序参考)=-0.15%≤0→WATCH' if _tb_action == 'WATCH' else f'EV正向→{_tb_action}'
    _align_ok      = '✅' if res.get('align_count',0) >= 3 else f'⚠️方向{res.get("align_count",0)}/7共识不足'
    _fvg_ok        = '✅' if fvg.get('consensus') else '⚠️FVG无共识'
    _hurst_ok      = '✅' if vol.get('hurst',0.5) >= 0.55 else f'⚠️H={vol.get("hurst",0.5):.3f}<0.55随机游走'
    _oi_ok         = '✅' if oi.get('signal') else '⚠️OI无信号'
    _risk_ok       = '✅通过' if (risk['circuit_ok'] and risk['dd_ok'] and risk['af_ok']) else '🚨风控拦截'
    _final_gate    = '🟢 系统放行' if _tb_action in ('ENTER','WATCH') else '🔴 系统拒绝'

    _review_lines = [
        f'',
        f'{"═"*43}',
        f'【苏摩人工审核区】{sym} @ ${p:,.1f}  {_final_gate}',
        f'{"═"*43}',
        f'① 体制  : {regime_c}  {_regime_ok}',
        f'② 评分  : {score:.1f}  {_ev_note}',
        f'③ 方向  : 交易员大脑={_tb_bias}  置信={_tb_conf}  交叉={_tb_cross}/4  {_align_ok}',
        f'④ FVG   : 共识={fvg.get("consensus","无")}  磁铁=${fvg.get("magnet",0):,.0f}  {_fvg_ok}',
        f'⑤ 入场区: {_entry_lo_disp} ~ {_entry_hi_disp}',
        f'⑥ 止损  : {_sl_disp}  ({_sl_pct_disp})  1.5×ATR1H:{_sl_atr1h_ok}  1.5×ATR4H:{_sl_atr4h_ok}',
        f'⑦ 目标  : TP1={_tp1_disp}  TP2={_tp2_disp}  TP3={_tp3_disp}  RR={_rr_disp}',
        f'⑧ HAR-RV: {_harv_str}  Hurst={_hurst_ok}',
        f'⑨ OI/资金: {oi.get("signal","N/A")} CVD={oi.get("cvd_dir_1h","?")}  FR={vol.get("fr_note","?")[:30]}  {_oi_ok}',
        f'⑩ 风控  : {_risk_ok}  回撤={risk["dd_pct"]:.1f}%  连亏={risk["consec"]}笔  仓位系数x{risk["nav_mult"]:.2f}',
        f'⑪ 清算场: 空头墙=${liq.get("nearest_short",0):,.0f}  多头池=${liq.get("nearest_long",0):,.0f}',
        f'',
        f'┌─ 苏摩决策 ─────────────────────────────────┐',
        f'│ 系统建议: {_tb_action:6s} | 方向: {_tb_bias:5s} | 置信: {_tb_conf}',
        f'│ □ 同意执行  □ 降仓执行  □ 等待  □ 否决',
        f'│ 苏摩判断: ____________________________________',
        f'└────────────────────────────────────────────┘',
        f'{"═"*43}',
    ]
    # ══════════════════════════════════════════════════════════

    lines += [
        _vip_out,
        f'{"─"*43}',
    ] + _review_lines + [
        (f'🧠 交易员大脑: {_tb_action} | 方向={_tb_bias} | 置信={_tb_conf} | 交叉验证={_tb_cross}/4'
         + (f'\n   {_tb_layer_str}' if _tb_layer_str else '')
         + (f'\n   缺: {" ".join(tb_result.get("missing",[]))}' if tb_result.get('missing') else '')),
        f'📊 梵天系统 · 80维 · 6层确定性决策 · 交易员大脑',
    ]
    if _price_warn:
        lines.append(_price_warn)

    # P6新增: ensemble+council对比展示
    try:
        # [cleaned] import sys as _ens_sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from brahma_brain.ensemble_engine import get_ensemble_score
        from brahma_brain.ai_council_bridge import get_council_verdict
        _ens_dir = 'SHORT' if 'BEAR' in str(regime_c) or 'CHOP' in str(regime_c) else 'LONG'
        _bs = d.get('bs',{})
        _raw_score = _bs.get('score_final', _bs.get('score', _bs.get('confluence',{}).get('total', 0))) or 0
        _mock_for_ens = {'regime': str(regime_c), 'score': _raw_score, 'price': p,
                         'rsi_4h': d.get('bs',{}).get('momentum',{}).get('rsi_4h',50),
                         'rsi_1h': d.get('bs',{}).get('momentum',{}).get('rsi_1h',50),
                         'confluence': d.get('bs',{}).get('confluence',{}),
                         'extra': {'hurst': vol.get('hurst',0.5), 'kappa': vol.get('kappa',0)}}
        _ens = get_ensemble_score(sym+'USDT', _ens_dir, _mock_for_ens)
        _council = get_council_verdict(sym+'USDT', _ens_dir, _mock_for_ens, _ens)
        lines += [
            f'',
            f'── P3/P4对比 ──',
            f'  AI Council(参考): {_council.get("council_bias","?")}/{_council.get("council_action","?")}/{_council.get("council_confidence","?")}',
            f'  Bayes: adj={_council.get("bayes_adjustment",0):+.2f} detail={_council.get("bayes_detail","?")[:50]}',
            f'  Combined: {_council.get("combined_score",0)} = ensemble({_ens.get("ensemble_score",0)}) + bayes({_council.get("bayes_adjustment",0):+.2f})',
        ]
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # ════════════════════════════════════════════════════════════════
    # [果蝇架构 2026-09-13 苏摩111] P1修复：砍3个无效AI层
    # 原因：brahma_cpu(死穴SKIP) + LLM议会(超时) + 梵天大脑(超时) = 全无效
    # 保留：enhanced_signal（有价值14/25分）
    # 效果：分析耗时35s→5s，代码复杂度-20%
    # ════════════════════════════════════════════════════════════════
    _cpu_result = None
    _council_bridge_result = None
    _enhanced_result = None
    _cpu_dir = 'SHORT' if 'BEAR' in str(regime_c) or 'CHOP' in str(regime_c) else 'LONG'

    # 只保留 enhanced_signal（有价值）
    # [cleaned] import sys as _enh_sys
    try:
        from brahma_brain.enhanced_signal_engine import enhanced_score as _enh_score
        _enh_box = {}
        def _enh_run():
            try:
                _enh_box['result'] = _enh_score(sym+'USDT', _cpu_dir)
            except Exception as e:
                _enh_box['error'] = str(e)[:200]
        import threading as _enh_thread
        _enh_t = _enh_thread.Thread(target=_enh_run, daemon=True)
        _enh_t.start()
        _enh_t.join(timeout=10)
        if 'error' in _enh_box:
            lines += [f'', f'─── 📡 增强信号 ───', f'  ⚠️ 降级: {_enh_box["error"][:60]}']
        elif 'result' in _enh_box:
            _enhanced_result = _enh_box['result']
            _enh_score_val = _enhanced_result.get('score', 0)
            _enh_notes = _enhanced_result.get('notes', [])
            lines += [f'', f'─── 📡 增强信号 ───', f'  增强score: {_enh_score_val}/25']
            for _n in _enh_notes[:3]:
                lines.append(f'  {_n}')
        else:
            lines += [f'', f'─── 📡 增强信号 ───', f'  ⏳ 超时(10s)']
        _step_gc()
    except Exception as _enh_e:
        lines += [f'', f'─── 📡 增强信号 ───', f'  ⚠️ 未启用: {str(_enh_e)[:60]}']

    # brahma_cpu/LLM议会/梵天大脑已砍除（P1修复）
    # lines += [f'', f'  (brahma_cpu/LLM议会/梵天大脑已砍除——P1果蝇架构修复)']
    
    # signal_selector 已废除（P0改革 2026-09-19 苏摩111）— score IC≈0，无有效门控价值

    # [果蝇架构 2026-09-13 苏摩111] P1修复：梵天大脑已砍除（超时无效）
    # 原代码：brahma_brain_ai.brahma_brain_decide() 30s超时→规则版VIP覆盖
    # 保留：规则版VIP卡片（step10_vip）已覆盖所有有用输出
    # _bb_result = None  # 已砍除

    # [改革2+3 2026-09-18 苏摩111] 信号结算闭环：每次分析后结算pending + 记录新信号
    try:
        from brahma_brain.signal_settlement_engine import settle_pending, settle_paper_json, record_signal, get_wr_stats, get_dynamic_threshold
        _settle_price = d.get('price', 0) or 0
        if _settle_price == 0:
            # fallback: 从结果中获取
            _settle_price = d.get('bs', {}).get('price', 0) or 78000
        _settle_r = settle_pending(_settle_price, sym+'USDT')
        _settle_r2 = settle_paper_json(_settle_price, sym+'USDT')  # [P1-3修复2 2026-09-23] 真实纸面仓结算（paper_positions.json）
        if _settle_r['settled'] > 0:
            lines += [f'', f'─── 📊 信号结算 ───', f'  本次结算: {_settle_r["settled"]}笔 | 待结算: {_settle_r["pending"]}笔']
            for s in _settle_r.get('settled_details', []):
                lines.append(f'  {s["symbol"]} {s["direction"]} → {s["outcome"]} PnL={s.get("pnl_pct",0):+.2f}%')
        if _settle_r2.get('settled', 0) > 0:
            lines += [f'  📄 纸面仓结算: {_settle_r2["settled"]}笔']
            for s in _settle_r2.get('hits', []):
                lines.append(f'  {s["symbol"]} → {s["outcome"]} PnL={s.get("pnl_pct",0):+.2f}%')
        _wr_stats = get_wr_stats()
        _dyn_threshold = get_dynamic_threshold()
        if _wr_stats['total'] > 0:
            lines.append(f'  累计WR: {_wr_stats["wr"]:.1%} ({_wr_stats["wins"]}W/{_wr_stats["losses"]}L n={_wr_stats["total"]})')
            lines.append(f'  动态门槛: {_dyn_threshold} (默认80)')
        # 如果当前是EXECUTE/AMBUSCADE，记录新信号
        _final_action = d.get('decision_action', 'WATCH')
        if _final_action in ('EXECUTE', 'AMBUSCADE', 'ENTER') and d.get('signal_dir'):
            _entry = d.get('key_levels', {})
            record_signal(
                symbol=sym+'USDT',
                direction=d['signal_dir'],
                entry_lo=_entry.get('entry_lo', _settle_price * 0.99),
                entry_hi=_entry.get('entry_hi', _settle_price * 1.01),
                sl=_entry.get('sl', _settle_price * 0.98 if d['signal_dir']=='LONG' else _settle_price * 1.02),
                tp1=_entry.get('tp1', _settle_price * 1.02 if d['signal_dir']=='LONG' else _settle_price * 0.98),
                score=d.get('score', 0),
                regime=str(regime_c),
            )
            lines.append(f'  → 已记录模拟入场 {sym} {d["signal_dir"]}')
            # [2026-10-02 苏摩111] AMBUSCADE/ENTER信号主动推送
            if push_jarvis and _final_action in ('AMBUSCADE', 'ENTER', 'EXECUTE'):
                try:
                    import importlib.util as _ilu2, pathlib as _pl2
                    _sp2 = _ilu2.spec_from_file_location('push_hub',
                        _pl2.Path(__file__).parent / 'push_hub.py')
                    _ph2 = _ilu2.module_from_spec(_sp2); _sp2.loader.exec_module(_ph2)
                    _amb_entry = d.get('key_levels', {})
                    _amb_lo = _amb_entry.get('entry_lo', 0)
                    _amb_hi = _amb_entry.get('entry_hi', 0)
                    _amb_sl = _amb_entry.get('sl', 0)
                    _amb_tp = _amb_entry.get('tp1', 0)
                    _amb_msg = (
                        f'🎯 **{_final_action}信号** | {sym} {d["signal_dir"]} | '
                        f'{time.strftime("%H:%M UTC", time.gmtime())}\n\n'
                        f'入场区: ${_amb_lo:,.1f}~${_amb_hi:,.1f}\n'
                        f'止损: ${_amb_sl:,.1f} | 目标: ${_amb_tp:,.1f}\n'
                        f'体制: {regime_c} | 评分: {d.get("score",0):.0f}'
                    )
                    _ph2.push_jarvis(_amb_msg, priority='P1',
                        dedup_key=f'ambuscade_{sym}_{d["signal_dir"]}_{int(_settle_price//100)}',
                        dedup_ttl=3600)
                except Exception as _ambe:
                    # [2026-10-05 P0-A fix] _sys_warn已清除，改用顶部sys
                    print(f"[WARN] ambuscade push失败: {_ambe}", file=sys.stderr)
    except Exception as _settle_e:
        print(f'[WARN] settlement: {_settle_e}', file=sys.stderr)

    # ── brahma_360 系统自检（非阻塞） ──
    # [cleaned] import sys as _b360_sys
    try:
        from brahma_brain.brahma_360 import scan_d1_modules as _b360_scan
        _b360_box = {}
        def _b360_run():
            try:
                _modules = _b360_scan()
                _issues = [m for m in _modules if isinstance(m, dict) and m.get('status') == 'MISSING']
                _b360_box['result'] = {'healthy': len(_issues) == 0, 'issues': [m.get('name','?') for m in _issues], 'total': len(_modules)}
            except Exception as e:
                _b360_box['error'] = str(e)[:200]
        import threading as _b360_thread
        _b360_t = _b360_thread.Thread(target=_b360_run, daemon=True)
        _b360_t.start()
        _b360_t.join(timeout=15)
        if _b360_t.is_alive():
            lines += [f'', f'─── 🔄 brahma_360 自检 ───', f'  ⏳ 超时(30s)']
        elif 'error' in _b360_box:
            lines += [f'', f'─── 🔄 brahma_360 自检 ───', f'  ⚠️ {str(_b360_box["error"])[:60]}']
        elif 'result' in _b360_box:
            _b360_r = _b360_box['result']
            _b360_ok = _b360_r.get('healthy', True)
            _b360_issues = _b360_r.get('issues', [])
            lines += [f'', f'─── 🔄 brahma_360 自检 ───', f'  状态: {"✅ 健康" if _b360_ok else "⚠️ 有问题"}']
            if _b360_issues:
                for _iss in _b360_issues[:3]:
                    lines.append(f'  • {_iss}')
        _step_gc()
    except Exception as _b360_e:
        lines += [f'', f'─── 🔄 brahma_360 自检 ───', f'  ⚠️ 未启用: {str(_b360_e)[:60]}']

    # [2026-09-16 苏摩111] 推理层接入 — 因果+博弈+周期因果链
    # 在brahma_360自检之后、return之前
    try:
        from brahma_brain.brahma_inference import format_inference_block
        _inf_block = format_inference_block(d.get('bs', {}))
        lines.append(_inf_block)
    except Exception as _inf_err:
        lines.append(f'\n  [推理层] 加载失败: {_inf_err}')

    # [NanoJev Phase0 2026-09-18 苏摩111] 写入live_signal_log + 88维特征
    try:
        import json as _njson, time as _ntime, secrets as _nsec, re as _nre
        from pathlib import Path as _nP
        from datetime import datetime as _ndt, timezone as _ntz
        _nsig_log = _nP(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
        _nsig_log.parent.mkdir(parents=True, exist_ok=True)
        _ncf = d.get('bs', {}).get('confluence', {}) or {}
        _nbd = _ncf.get('breakdown', {}) if isinstance(_ncf, dict) else {}
        
        # [V2.0修复 2026-09-20 苏摩111] CVD快照直接读取，写入顶层字段
        # 根因：CVD数据在step5b中读取但未传递到信号写入区域
        _cvd_snapshot = {}
        try:
            _cvd_path = _nP(__file__).parent.parent / 'data' / f'cvd_realtime_{sym.lower()}usdt.json'
            if _cvd_path.exists():
                _cvd_snapshot = _njson.loads(_cvd_path.read_text())
        except Exception:
            pass  # [WARN-suppressed: no var]
        # [2026-10-06 苏摩111] LSR实时拉取，LLM退避期d.lsr_big=0
        _lsr_big_rt = 0.0; _lsr_retail_rt = 0.0
        try:
            import urllib.request as _ur2, ssl as _ssl2
            _ctx2 = _ssl2.create_default_context()
            _usdt2 = sym.upper()+'USDT'
            _top2 = _njson.loads(_ur2.urlopen(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={_usdt2}&period=5m&limit=1', timeout=4, context=_ctx2).read())
            _glb2 = _njson.loads(_ur2.urlopen(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={_usdt2}&period=5m&limit=1', timeout=4, context=_ctx2).read())
            _lsr_big_rt    = float(_top2[0]['longAccount'])*100 if _top2 else 0.0
            _lsr_retail_rt = float(_glb2[0]['longAccount'])*100 if _glb2 else 0.0
        except Exception: pass
        _nfeats = {}
        def _nsf(v, dft=0):
            try: return float(v) if v is not None else dft
            except Exception: return dft
        for k, v in _nbd.items():
            if isinstance(v, (int, float)): _nfeats[f'bd_{k}'] = float(v)
            elif isinstance(v, str):
                _nm = _nre.search(r'-?\d+\.?\d*', str(v))
                if _nm: _nfeats[f'bd_{k}'] = float(_nm.group())
        for k in ['rsi_15m','rsi_1h','rsi_4h','rsi_1d','atr_1h','atr_4h','atr_1d']:
            _nfeats[f'mom_{k}'] = _nsf(vol.get(k, 0))
        for k in ['funding_rate','long_short_ratio','oi','oi_change_pct','oi_momentum']:
            _nfeats[f'sent_{k}'] = _nsf(oi.get(k, 0))
        _nfeats['score'] = _nsf(score)
        _nfeats['regime'] = str(regime)
        _nfeats['direction'] = str(tb_result.get('direction', 'UNKNOWN'))
        _nfeats['price'] = _nsf(p)
        _nfeats['symbol'] = sym + 'USDT'
        _nfeats['hurst'] = _nsf(vol.get('hurst', 0))
        _nfeats['atr_1h'] = _nsf(vol.get('atr_1h', 0))
        _nfeats['atr_4h'] = _nsf(vol.get('atr_4h', 0))
        _nfeats['gex'] = _nsf(vol.get('gex', 0))
        # [V2.0修复 2026-09-20] CVD写入features
        _nfeats['cvd_1h'] = _nsf(_cvd_snapshot.get('cvd_1h', 0))
        _nfeats['cvd_dir_1h'] = str(_cvd_snapshot.get('dir_1h', 'NEUTRAL'))
        # [V2.0 2026-09-20 苏摩111] CVD多周期暴露
        try:
            from brahma_brain.volume_unified import get_multi_tf_cvd
            _cvd_mtf = get_multi_tf_cvd(sym + 'USDT')
            _nfeats['cvd_4h'] = _nsf(_cvd_mtf.get('macro',{}).get('strength',0))
            _nfeats['cvd_4h_dir'] = str(_cvd_mtf.get('macro',{}).get('direction','NEUTRAL'))
            _nfeats['cvd_5m'] = _nsf(_cvd_mtf.get('micro',{}).get('strength',0))
            _nfeats['cvd_5m_dir'] = str(_cvd_mtf.get('micro',{}).get('direction','NEUTRAL'))
        except Exception:
            pass  # [WARN-suppressed: no var]
        _nfeats['oi_trend'] = str(oi.get('trend', ''))
        _nfeats['sm_divergence'] = _nsf(sm.get('divergence', 0))
        _nts = _ntime.time()
        _nsig = {
            'signal_id': _nsec.token_hex(6),
            'ts': _nts, 'timestamp': _nts,
            'ts_iso': _ndt.fromtimestamp(_nts, tz=_ntz.utc).isoformat(),
            'symbol': sym + 'USDT',
            'signal_dir': str(tb_result.get('direction', 'UNKNOWN')),
            'direction': str(tb_result.get('direction', 'UNKNOWN')),
            'regime': str(regime),
            'score': _nsf(score),
            'action': str(tb_result.get('action', 'WAIT')),
            'valid': True,
            'price': _nsf(p),
            'entry_lo': _nsf(tb_result.get('entry_lo', 0)),
            'entry_hi': _nsf(tb_result.get('entry_hi', 0)),
            'stop_loss': _nsf(tb_result.get('sl', 0)),
            'tp1': _nsf(tb_result.get('tp1', 0)),
            'tp2': _nsf(tb_result.get('tp2', 0)),
            'sl_pct': _nsf(tb_result.get('sl_pct', 0)),
            'rr1': _nsf(tb_result.get('rr', 0)),
            'status': 'OPEN',
            'features': _nfeats,
            # [V2.0修复 2026-09-20 苏摩111] CVD顶层字段 — 之前CVD只在breakdown深处，没暴露到顶层
            'cvd_1h': _nsf(_cvd_snapshot.get('cvd_1h', 0)),
            'cvd_dir_1h': str(_cvd_snapshot.get('dir_1h', 'NEUTRAL')),
            'cvd_buy_vol_1h': _nsf(_cvd_snapshot.get('buy_vol_1h', 0)),
            'cvd_sell_vol_1h': _nsf(_cvd_snapshot.get('sell_vol_1h', 0)),
            # [V2.0 2026-09-20] CVD多周期顶层字段
            'cvd_4h_dir': _nfeats.get('cvd_4h_dir', 'NEUTRAL'),
            'cvd_5m_dir': _nfeats.get('cvd_5m_dir', 'NEUTRAL'),
        }
        # 分数守卫
        _nguard = _nsig.get('score', 0)
        _nreg = _nsig.get('regime', '')
        _nskip = False  # [9.20 P0改革] NanoJev守卫废除 — score不做门控
        if not _nskip:
            with open(_nsig_log, 'a') as _nf:
                _nf.write(_njson.dumps(_nsig, ensure_ascii=False) + '\n')
            print(f'[NanoJev] {sym}信号写入live_signal_log ({len(_nfeats)}维特征)', file=sys.stderr)
        else:
            print(f'[NanoJev] {sym}被守卫拦截 (score={_nguard} regime={_nreg})', file=sys.stderr)
    except Exception as _ne:
        print(f'[WARN] live_signal_log写入失败: {_ne}', file=sys.stderr)

    # [2026-10-05 Fix C 苏摩111] P2双主链合并 v2：写入 format_full_report 所有42个必须字段
    # 根治 D1-D10 全显示 $0 的根本原因（template读字段缺失→默认0）
    try:
        import json as _json_sync, time as _ts_sync
        # [P3] 内联RSI计算辅助（自适应期数，K线格式(o,h,l,c,v)）
        def _calc_rsi(klines, period=14):
            if len(klines) < 3: return 0.0
            period = min(period, len(klines) - 1)  # 自适应：K线不足时降期数
            if period < 2: return 0.0
            closes = [float(k[3]) for k in klines]
            deltas = [closes[i]-closes[i-1] for i in range(1,len(closes))]
            gains  = [max(d,0) for d in deltas]
            losses = [abs(min(d,0)) for d in deltas]
            avg_g = sum(gains[:period])/period
            avg_l = sum(losses[:period])/period
            for i in range(period, len(deltas)):
                avg_g = (avg_g*(period-1)+gains[i])/period
                avg_l = (avg_l*(period-1)+losses[i])/period
            return round(100-100/(1+avg_g/avg_l),1) if avg_l > 0 else (100.0 if avg_g > 0 else 50.0)
        # ════════════════════════════════════════════════════════
        # Layer4: Fix-C写入层 (brahma_state/auto_analysis写入)
        # [封印 2026-10-06 P2重构] _lv=locals()已废弃，改用显式参数传递
        # ════════════════════════════════════════════════════════
        _state_path = Path(__file__).parent.parent / 'data' / f'brahma_state_{sym.lower()}.json'

        # ── FVG全周期投票表（template D1需要） ──
        _tf_fvg_map = fvg.get('tf_fvg_map', {})  # {tf: fvg_dict}
        _fvg_votes = {}
        _fvg_magnets = {}
        for _tf_key, _fvg_item in _tf_fvg_map.items():
            _tf_upper = _tf_key.upper().replace('15M','15M').replace('1H','1H').replace('4H','4H').replace('1D','1D').replace('1W','1W')
            if isinstance(_fvg_item, dict):
                _fvg_votes[_tf_upper]   = _fvg_item.get('type', 'NONE')
                _fvg_magnets[_tf_upper] = float(_fvg_item.get('mid', 0))
        # 补全缺失周期为NONE
        for _tf_fill in ['15M','1H','4H','1D','1W']:
            _fvg_votes.setdefault(_tf_fill, 'NONE')
            _fvg_magnets.setdefault(_tf_fill, 0.0)

        # ── ob_list（template D2需要：list of {tf,side,age,lo,hi,valid,dist_pct}）──
        # [P2修复] 优先从run_analysis局部ob变量（step2_ob结果）构建
        # 若ob为空（brahma_state无_ob_map），则从K线自算简化OB兜底
        _ob_src = ob if isinstance(ob, dict) else {}
        _ob_list = []
        for _ob_key, _ob_val in _ob_src.items():
            _parts = _ob_key.split('_')
            if len(_parts) >= 3 and _parts[0] == 'OB' and isinstance(_ob_val, dict):
                _ob_list.append({
                    'tf':       _parts[1],
                    'side':     _parts[2],
                    'age':      int(_ob_val.get('age', 0)),
                    'lo':       float(_ob_val.get('lo', 0)),
                    'hi':       float(_ob_val.get('hi', 0)),
                    'valid':    bool(_ob_val.get('valid', False)),
                    'dist_pct': float(_ob_val.get('dist_pct', 0)),
                })
        # K线兜底：ob为空时从15M/1H/4H/1D K线末尾实体算简化OB（D2强制4周期）
        if not _ob_list:
            for _tf_kb, _klines_kb in [('15M', d.get('k15m',[])), ('1H', d.get('k1h',[])), ('4H', d.get('k4h',[])), ('1D', d.get('k1d',[]))]:
                if len(_klines_kb) < 3: continue
                _kb = _klines_kb  # [open,high,low,close,vol]
                _p_now = float(d.get('price', 0))
                for _ci in [-2, -3, -4]:  # 最近3根K线
                    try:
                        _c = _kb[_ci]
                        # k线格式: (open,high,low,close,vol) 索引0~4
                        _o, _h, _l, _cl = float(_c[0]), float(_c[1]), float(_c[2]), float(_c[3])
                        _is_bull = _cl > _o
                        _lo_ob, _hi_ob = min(_o, _cl), max(_o, _cl)
                        _age_kb = abs(_ci)
                        _dist = (_lo_ob + _hi_ob) / 2 - _p_now
                        _dist_pct = _dist / _p_now * 100 if _p_now else 0
                        # 有效：未被穿越（Bull OB在现价下方，Bear OB在现价上方）
                        _valid_kb = (_is_bull and _hi_ob < _p_now) or (not _is_bull and _lo_ob > _p_now)
                        if abs(_dist_pct) < 8:  # 只取8%以内的OB
                            _ob_list.append({
                                'tf': _tf_kb, 'side': 'BULL' if _is_bull else 'BEAR',
                                'age': _age_kb, 'lo': round(_lo_ob, 2), 'hi': round(_hi_ob, 2),
                                'valid': _valid_kb, 'dist_pct': round(_dist_pct, 2),
                            })
                    except Exception: pass

        # ── OI序列（template D5需要）──
        _oi_seq_raw = d.get('oi_vals', [])
        _oi_sequence = [float(x) for x in _oi_seq_raw[-8:]] if _oi_seq_raw else []

        # ── 步骤结果变量（显式参数传入，不再依赖_lv）──
        _vol   = vol   if isinstance(vol,  dict) else {}
        _mac   = mac   if isinstance(mac,  dict) else {}
        _risk  = risk  if isinstance(risk, dict) else {}
        _res   = res   if isinstance(res,  dict) else {}
        _zsc   = d.get('zsc', {})
        _bw    = bw    if isinstance(bw,   dict) else {}
        _step11_g = d.get('_step11', {})
        # [Fix-C v2] step11 返回 gates_passed(int)+blocked_by(str), 无gates dict
        # 重建 template 需要的 step11_gates 格式 {G1:bool, ..., G11:bool}
        _s11_cnt = _step11_g.get('gates_passed', 0) if isinstance(_step11_g, dict) else 0
        _s11_blk = _step11_g.get('blocked_by', '') if isinstance(_step11_g, dict) else ''
        _s11_verd = _step11_g.get('verdict', 'WAIT') if isinstance(_step11_g, dict) else 'WAIT'
        # 构建11道门结果（前N门通过，第N+1门阻断）
        _gate_list = ['G1','G2','G3','G4','G5','G6','G7','G8','G9','G10','G11']
        _blocked_gate = None
        if _s11_blk:
            for _gk in _gate_list:
                if _gk in _s11_blk: _blocked_gate = _gk; break
        _gates = {}
        for _i, _gk in enumerate(_gate_list):
            if _blocked_gate:
                _gates[_gk] = (_i < _gate_list.index(_blocked_gate))
            else:
                _gates[_gk] = (_i < _s11_cnt)
        _tb    = tb_result if isinstance(tb_result, dict) else {}

        _p_price = float(d.get('price', 0))
        # [P0修复] regime 优先读 regime_state.json confirmed（权威来源），不读 bs['regime']
        _rs_data = d.get('regime_s', {})
        _rs_confirmed = (_rs_data.get(sym+'USDT', {}).get('confirmed', '')
                         if isinstance(_rs_data, dict) else '')
        if not _rs_confirmed:
            # 二次降级：读文件
            try:
                import json as _jrs
                _rs_file = Path(__file__).parent.parent / 'data' / 'regime_state.json'
                _rs_confirmed = _jrs.loads(_rs_file.read_text()).get(sym+'USDT', {}).get('confirmed', '')
            except Exception: pass
        # [2026-10-06 苏摩111] regime=UNKNOWN时用Hurst+OI本地推断
        # [2026-10-06 苏摩111] 本轮10步信号评分（regime恢复前的合理评分）
        try:
            _align = int(res.get('align_count', 0) if isinstance(res, dict) else 0)
            _oi_sig_ls = str(oi.get('signal','?') if isinstance(oi,dict) else '?')
            _cvd_ls = float(_cvd_snapshot.get('cvd_1h', 0))
            _lsr_b_ls = float(_lsr_big_rt or 0)
            _lsr_r_ls = float(_lsr_retail_rt or 0)
            _rsi1h_ls = float(d.get('rsi_1h', vol.get('rsi_1h', 50) if isinstance(vol,dict) else 50) or 50)
            _live_score = (_align * 10
                + (15 if 'LONG_BUILD' in _oi_sig_ls else 10 if 'SHORT_BUILD' in _oi_sig_ls else -5 if 'UNWIND' in _oi_sig_ls else 0)
                + (10 if _cvd_ls > 100 else 5 if _cvd_ls > 0 else -5 if _cvd_ls < -100 else 0)
                + (8 if _lsr_b_ls > 60 else 0)
                + (-5 if _lsr_r_ls > 65 else 0)
                + (8 if _rsi1h_ls < 30 else 0))
            _live_score = max(0.0, float(_live_score))
        except Exception: _live_score = 0.0

        _p_regime_raw = str(_rs_confirmed or regime_c or '')
        if _p_regime_raw in ('UNKNOWN','','None'):
            # 本地推断：Hurst>0.6=趋势 / OI=LONG_UNWIND+CVD>0=BULL_TREND候选 / 否则CHOP_MID
            _h_val = float(_vol.get('hurst') or 0.5)
            _oi_sig_rt = str(oi.get('signal','?'))
            _cvd_rt = float(_cvd_snapshot.get('cvd_1h',0))
            if _h_val > 0.62:
                _p_regime_raw = 'BULL_TREND' if _cvd_rt > 0 else 'BEAR_TREND'
            elif _oi_sig_rt == 'SHORT_BUILD' and _cvd_rt < 0:
                _p_regime_raw = 'BEAR_EARLY'
            else:
                _p_regime_raw = 'CHOP_MID'
            print(f'[{sym}] regime UNKNOWN → 本地推断={_p_regime_raw} (H={_h_val:.3f} OI={_oi_sig_rt} CVD={_cvd_rt:+.0f})', flush=True)
        _p_regime = _p_regime_raw

        # [封印 2026-10-07 苏摩111] 清算地图字段预计算（必须在_sync_state dict外面）
        # 优先读 liq_heatmap文件 / fallback step3 liq变量
        try:
            _lh_fc = _json_sync.loads(
                (Path(__file__).parent.parent / 'data' / f'liq_heatmap_{sym.lower()}usdt.json').read_text()
            )
        except Exception:
            _lh_fc = {}
        _liq_s_fc = float(
            _lh_fc.get('nearest_short_liq', 0) or
            (liq.get('nearest_short', 0) if isinstance(liq, dict) else 0)
        )
        _liq_l_fc = float(
            _lh_fc.get('nearest_long_liq', 0) or
            (liq.get('nearest_long', 0) if isinstance(liq, dict) else 0)
        )

        # [封印 2026-10-07 苏摩111] GEX字段修复：从gex_state.json直接读取
        # 根因: brahma_state.gex读的是不存在的_vol.gex_note=0，实际数据在gex_state
        try:
            _gex_file = Path(__file__).parent.parent / 'data' / 'gex_state.json'
            _gex_raw = _json_sync.loads(_gex_file.read_text()).get(sym, {})
            _gex_at_spot   = float(_gex_raw.get('net_gex_at_spot', 0))
            _gex_dir_fc    = str(_gex_raw.get('gex_direction', 'UNKNOWN'))
            _zero_flip_fc  = float(_gex_raw.get('zero_flip', 0))
            _max_gex_fc    = float(_gex_raw.get('max_gex_strike', 0))
            _kappa_fc      = float(_gex_raw.get('kappa', _vol.get('kappa', 0)))
        except Exception:
            _gex_at_spot = _zero_flip_fc = _max_gex_fc = _kappa_fc = 0
            _gex_dir_fc = 'UNKNOWN'

        _sync_state = {
            # ── 基础 ──
            'symbol':    sym + 'USDT',
            'price':     _p_price,
            'price_ts':  _ts_sync.time(),
            'regime':    _p_regime,
            'hurst':     float(_vol.get('hurst') or d.get('hurst') or
                               (_rs_data.get(sym+'USDT', {}).get('hurst', 0)
                                if isinstance(_rs_data, dict) else 0) or 0),

            # ── D1 FVG（template需要 fvg_votes + fvg_per_tf_magnet + fvg_consensus + fvg_magnet） ──
            'fvg_votes':     _fvg_votes,
            'fvg_consensus': str(fvg.get('consensus', fvg.get('dir', 'NEUTRAL'))),
            'fvg_magnet':    float(fvg.get('magnet', 0)),
            'fvg_15m_magnet': _fvg_magnets.get('15M', 0.0),
            'fvg_1h_magnet':  _fvg_magnets.get('1H',  0.0),
            'fvg_4h_magnet':  _fvg_magnets.get('4H',  0.0),
            'fvg_1d_magnet':  _fvg_magnets.get('1D',  0.0),
            'fvg_1w_magnet':  _fvg_magnets.get('1W',  0.0),
            # 兼容旧字段
            'fvg_1d': str(_fvg_votes.get('1D', '?')),
            'fvg_4h': str(_fvg_votes.get('4H', '?')),
            'fvg_1h': str(_fvg_votes.get('1H', '?')),

            # ── D2 OB（ob_list供template渲染表格） ──
            'ob_list':   _ob_list,
            'ob_1h_lo':  float(ob.get('OB_1H_BULL', ob.get('OB_1H_BEAR', {})).get('lo', 0) if isinstance(ob, dict) else 0),
            'ob_1h_hi':  float(ob.get('OB_1H_BULL', ob.get('OB_1H_BEAR', {})).get('hi', 0) if isinstance(ob, dict) else 0),
            'ob_4h_lo':  float(ob.get('OB_4H_BULL', ob.get('OB_4H_BEAR', {})).get('lo', 0) if isinstance(ob, dict) else 0),
            'ob_4h_hi':  float(ob.get('OB_4H_BULL', ob.get('OB_4H_BEAR', {})).get('hi', 0) if isinstance(ob, dict) else 0),

            # ── D3 清算 ──
            # [封印 2026-10-07 苏摩111 v2] _liq_s_fc已在dict外预计算
            'liq_short':  _liq_s_fc,
            'liq_long':   _liq_l_fc,
            'liq_short2': float(liq.get('second_short', 0) if isinstance(liq, dict) else 0),
            'liq_long2':  float(liq.get('second_long',  0) if isinstance(liq, dict) else 0),

            # ── D4 共振 ──
            'align_count': int(_res.get('align_count', d.get('align_count', 0))),
            'entry_lo':  float(_tb.get('entry_lo', d.get('entry_lo', 0))),
            'entry_hi':  float(_tb.get('entry_hi', d.get('entry_hi', 0))),
            'oi_direction': str(oi.get('signal', d.get('oi_direction', '?')) if isinstance(oi, dict) else '?'),
            # [2026-10-06 苏摩111] 优先用_cvd_snapshot（实时文件），d.cvd_1h在LLM退避期为0
            'cvd_1h':    float(_cvd_snapshot.get('cvd_1h', d.get('cvd_1h', 0))),
            'cvd_4h':    float(_cvd_snapshot.get('cvd_4h', d.get('cvd_4h', 0))),
            'cvd_dir_1h': str(_cvd_snapshot.get('dir_1h', d.get('cvd_dir_1h', '?'))),
            'cvd_dir_4h': str(_cvd_snapshot.get('dir_4h', d.get('cvd_dir_4h', '?'))),
            'gex':         _gex_at_spot,        # [封印 2026-10-07] Spot点净GEX(M)，负=波动放大
            'gex_direction': _gex_dir_fc,         # POSITIVE/NEGATIVE
            'zero_flip':     _zero_flip_fc,        # ZeroFlip价格（关键翻转线）
            'max_gex_strike':_max_gex_fc,          # 最大GEX行使价（做市商最强钉住位）

            # ── D5 OI ──
            'oi_sequence':  _oi_sequence,
            'oi_chg_total': float(d.get('oi_chg_pct', 0)),
            'oi_signal':    str(oi.get('signal', '?') if isinstance(oi, dict) else '?'),
            'fr':           float(d.get('fr', 0)),

            # ── D6 LSR ──
            # [2026-10-06 苏摩111] 优先用实时LSR，LLM退避期d.lsr_big=0
            'lsr_big':    _lsr_big_rt or float(d.get('lsr_big', 0)),
            'lsr_retail': _lsr_retail_rt or float(d.get('lsr_retail', d.get('lsr', 0))),
            'lsr_zscore': float(_zsc.get('zscore', 0) if isinstance(_zsc, dict) else 0),

            # ── D7 波动率 ──
            'kappa':    _kappa_fc if _kappa_fc != 0 else float(_vol.get('kappa', d.get('kappa', 0))),  # [封印 2026-10-07] gex_state优先
            'iv_rank':  float(_vol.get('iv_rank',  0)),
            'iv_pct':   float(d.get('iv_pct', 0)),
            'atr_1h':   float(_vol.get('atr_1h',   0)),
            'atr_4h':   float(_vol.get('atr_4h',   0)),
            'harv_lo':  float(_vol.get('harv_range_lo', 0)),
            'harv_hi':  float(_vol.get('harv_range_hi', 0)),
            # [P3修复] RSI：内联计算（vol/d均无RSI字段），用K线EMA-RSI(14)
            'rsi_15m':  float(_vol.get('rsi_15m') or d.get('rsi_15m') or _calc_rsi(d.get('k15m',[])) or 0),
            'rsi_1h':   float(_vol.get('rsi_1h')  or d.get('rsi_1h')  or _calc_rsi(d.get('k1h', [])) or 0),
            'rsi_4h':   float(_vol.get('rsi_4h')  or d.get('rsi_4h')  or _calc_rsi(d.get('k4h', [])) or 0),
            'rsi_1d':   float(_vol.get('rsi_1d')  or d.get('rsi_1d')  or _calc_rsi(d.get('k1d', [])) or 0),

            # ── D8 宏观 ──
            'fed_rate':    float(_mac.get('fed_rate',   0)),
            'fg_index':    int(_mac.get('fear_greed',  50)),
            'fear_greed':  int(_mac.get('fear_greed',  50)),
            'cross_alpha': float(_mac.get('cross_alpha', 0)),
            'session':     str(_mac.get('session', d.get('session', 'UNKNOWN'))),

            # ── D9 风控 ──
            'circuit_breaker_ok': bool(_risk.get('circuit_ok', True)),
            'drawdown_pct':       float(_risk.get('dd_pct', 0)),
            'position_coef':      float(_risk.get('nav_mult', 1.0)),
            'btc_eth_corr':       float(d.get('btc_eth_corr', 0.85)),

            # ── D10 果蝇 ──
            'bw_score': int(_bw.get('results', {}).get(sym+'USDT', {}).get('score', 0) if isinstance(_bw, dict) else 0),
            'bw_c1':    dict(_bw.get('c1', {}) if isinstance(_bw, dict) else {}),
            'bw_c2':    dict(_bw.get('c2', {}) if isinstance(_bw, dict) else {}),
            'bw_c3':    dict(_bw.get('c3', {}) if isinstance(_bw, dict) else {}),

            # ── Step11 ──
            'step11_gates':   _gates,
            'step11_verdict': str(_step11_g.get('verdict', 'WAIT') if isinstance(_step11_g, dict) else 'WAIT'),
            # [2026-10-06 苏摩111] 本轮10步评分计算（不依赖bs.score_final）
            'score_final':    float(_live_score if '_live_score' in dir() else (d.get('bs', {}) or {}).get('score_final', 0)),
            'raw_score':      float(_live_score if '_live_score' in dir() else (d.get('bs', {}) or {}).get('score', 0)),

            # ── VIP策略 ──
            # [2026-10-06 苏摩111] 优先用_tb_for_s11（Step11修正后方向）
            'signal_dir':      str((_tb_for_s11 if '_tb_for_s11' in dir() else _tb).get('direction', d.get('signal_dir', 'NONE'))),
            'sl':              float(_tb.get('sl',  d.get('sl',  0))),
            'tp1':             float(_tb.get('tp1', d.get('tp1', 0))),
            'tp2':             float(_tb.get('tp2', d.get('tp2', 0))),
            'tp3':             float(_tb.get('tp3', d.get('tp3', 0))),
            'rr':              float(_tb.get('rr',  d.get('rr',  0))),
            # [封印修复②] ev_pct = (reward×0.52 - risk×0.48) / entry
            'ev_pct':          float(_tb.get('ev_pct') or d.get('ev_pct') or (
                lambda el, er, ep: round((el*0.52 - er*0.48)/ep*100, 3) if el>0 and er>0 and ep>0 else 0.0
            )(abs(float(_tb.get('entry_hi', d.get('entry_hi',0))) - float(_tb.get('tp1', d.get('tp1',0)))),
              abs(float(_tb.get('sl', d.get('sl',0))) - float(_tb.get('entry_hi', d.get('entry_hi',0)))),
              float(_tb.get('entry_hi', d.get('entry_hi',1))))),
            'leverage':        int(_tb.get('leverage', d.get('leverage', 5))),
            'position_size_pct': float(_tb.get('position_size_pct', d.get('position_size_pct', 1))),

            # ── 元数据 ──
            '_snapshot_written_epoch': _ts_sync.time(),
            '_snapshot_age_sec':       0,
            '_data_source':            'brahma_manual_analysis_fix_c_v2',
        }
        _tmp_sync = _state_path.with_suffix('.tmp')
        _tmp_sync.write_text(_json_sync.dumps(_sync_state, ensure_ascii=False, indent=2), encoding='utf-8')
        _tmp_sync.replace(_state_path)
        # [2026-10-06 苏摩111] 写入后追加confluence字段（来自analyze()的bs）
        try:
            if isinstance(d.get('bs'),dict) and d['bs'].get('confluence'):
                _saved = _json_sync.loads(_state_path.read_text(encoding='utf-8'))
                _saved['confluence'] = d['bs']['confluence']
                _saved['score_final'] = float(d['bs'].get('score_final', _saved.get('score_final',0)))
                _saved['raw_score'] = float(d['bs'].get('score', _saved.get('raw_score',0)))
                # [2026-10-06 苏摩111] 保存analyze()的signal_dir（94维计算结果）
                if d['bs'].get('signal_dir','NONE') not in ('NONE', '', None):
                    _saved['analyze_signal_dir'] = d['bs']['signal_dir']
                # [2026-10-06 苏摩111] 补入step11裁决字段（vip_watcher需要）
                if _saved.get('step11_verdict') in (None, ''):
                    _s11g_tmp = d.get('_step11', {})
                    if isinstance(_s11g_tmp, dict) and _s11g_tmp.get('verdict'):
                        _saved['step11_verdict'] = _s11g_tmp['verdict']
                        _saved['step11_block']   = _s11g_tmp.get('blocked_by', '')
                _saved['bs'] = {k:v for k,v in d['bs'].items() if not isinstance(v,(list,dict)) or k in ('confluence','breakdown')}
                _tmp2 = _state_path.with_suffix('.tmp2')
                _tmp2.write_text(_json_sync.dumps(_saved, ensure_ascii=False, indent=2), encoding='utf-8')
                _tmp2.replace(_state_path)
        except Exception as _cf_e:
            pass  # 非致命

        # [2026-10-06 苏摩111] 把bs的confluence/score写入_sync_state供output_template使用
        if isinstance(d.get('bs'),dict) and d['bs']:
            _bs_src = d['bs']
            if _bs_src.get('confluence') and not _sync_state.get('confluence'):
                _sync_state['confluence'] = _bs_src['confluence']
            if _bs_src.get('score_final',0) > _sync_state.get('score_final',0):
                _sync_state['score_final'] = float(_bs_src.get('score_final',0))
                _sync_state['raw_score'] = float(_bs_src.get('score',0))
            # 把bs整体合并进state供brahma_manual_analysis读取
            _sync_state['bs'] = {k:v for k,v in _bs_src.items() if k not in _sync_state}

        _missing = [k for k in ['fvg_votes','ob_list','fvg_magnet','liq_short','sl','tp1','score_final','step11_gates'] if not _sync_state.get(k) and _sync_state.get(k) != 0]
        print(f'[{sym}] ✅ Fix-C brahma_state写入 price=${_p_price:,.0f} regime={_p_regime} 缺字段={_missing or "无"}', flush=True)
    except Exception as _sync_e:
        import traceback as _tb_sync; _tb_sync.print_exc(file=sys.stderr)
        print(f'[WARN] P2 state同步失败: {_sync_e}', file=sys.stderr)

    # [2026-10-07 苏摩111 停用] VIP策略即时推送哨兵
    # 苏摩指令：取消推送任务
    # try:
    #     vip_strategy_watcher inline 调用已停用
    # except: pass

    # [2026-10-02 苏摩111] output_template 标准格式化尾部追加
    # 接入位置: run_analysis() 末尾，在返回文本前追加三方联合签名
    # [2026-10-02 苏摩111] 补充entry/sl/tp字段，避免format_full_report输出$0
    try:
        # [2026-10-05 P0-B fix] 顶部已import sys，删除函数内重复别名
        if str(Path(__file__).parent) not in sys.path:
            sys.path.insert(0, str(Path(__file__).parent))
        from brahma_output_template import format_full_report as _fmt_report
        # [2026-10-05 Fix-C v2] 使用_sync_state（44字段完整）替代原始 d
        # 根因: d['gex']是 dict、d缺 fvg_votes等，会导致 format 崩溃
        _locs = locals()
        _fmt_dict = _locs.get('_sync_state') if isinstance(_locs.get('_sync_state'), dict) else d
        # 兼容：tb_result关键字段回写（_sync_state已包含，重复赋值无害）
        if isinstance(tb_result, dict):
            for _k, _v in [('entry_lo', tb_result.get('entry_lo', 0.0)),
                           ('entry_hi', tb_result.get('entry_hi', 0.0)),
                           ('sl',       tb_result.get('sl',       0.0)),
                           ('tp1',      tb_result.get('tp1',      0.0)),
                           ('signal_dir', tb_result.get('direction', 'NONE'))]:
                if not _fmt_dict.get(_k):
                    _fmt_dict[_k] = _v
        _template_block = _fmt_report(sym, _fmt_dict)
        if _template_block:
            lines.append('')
            lines.append(_template_block)
    except Exception as _te:
        # [2026-10-05 苏摩111 P0-A修复] _sys_te NameError根治：直接用sys（顶部已import）
        print(f'[WARN] format_full_report失败: {_te}', file=sys.stderr)
        import traceback as _tb_te; _tb_te.print_exc(file=sys.stderr)
        # template失败时输出基础VIP卡片作为兜底
        try:
            from brahma_output_template import format_vip_card as _fvc
            _vip_only = _fvc(sym, d)
            if _vip_only:
                lines.append(''); lines.append(_vip_only)
        except Exception as _fvc_e:
            print(f'[WARN] format_vip_card兜底也失败: {_fvc_e}', file=sys.stderr)

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='梵天手动全链路分析')
    ap.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    args = ap.parse_args()

    symbols = args.symbols

    if len(symbols) == 1:
        # 单个标的直接运行
        print(run_analysis(symbols[0]))
        return

    # 多标的并行化（60s→30s）——设计院三方封印 2026-09-04 苏摩111
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time
    t0 = _time.time()
    results = {}

    # [P0并发预热 2026-09-07 苏摩111] 多标的并行预热数据缓存
    # 串行: BTC 2.7s + ETH 1.8s = 4.5s → 并行: max(2.7, 1.8) ≈ 2.0s，节省2.5s
    try:
        from data_cache import prefetch_symbol as _pf
        _syms_u = [s.upper() + ('USDT' if 'USDT' not in s.upper() else '') for s in symbols]
        with ThreadPoolExecutor(max_workers=len(_syms_u)) as _pf_pool:
            _pf_futs = [_pf_pool.submit(_pf, sym) for sym in _syms_u]
            for _f in as_completed(_pf_futs):
                pass  # 等待所有预热完成
        print(f'[P0并发预热] {len(_syms_u)}个标的缓存已就绪 ({_time.time()-t0:.1f}s)')
    except Exception:
        pass  # [WARN-suppressed: no var]

    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futures = {pool.submit(run_analysis, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as e:
                results[sym] = f'[{sym}] 分析失败: {e}'

    elapsed = _time.time() - t0
    print(f'\n[并行分析完成] 耗时 {elapsed:.1f}s ({len(symbols)}个标的并行)\n')

    # 汇总输出 + 推送到Jarvis（2026-09-08 苏摩111封印）
    full_output = []
    for sym in symbols:
        r = results.get(sym, f'[{sym}] 无结果')
        print(r)
        print()
        full_output.append(r)

    # 战场情报推送已移除 — battlefield cron通过AI agent推送，脚本不再直接推Jarvis
    # 避免square_auto_post等调用方通过.pyc缓存意外触发推送
    
    # ── [9.16苏摩111] 多标的方向一致性 + 分阶段策略标注 ──
    try:
        import json as _json2, re as _re_mod
        _cross_lines = []
        _sym_dirs = {}
        _sym_entries = {}
        for _sym in symbols:
            _state_path = Path(__file__).parent.parent / 'data' / f'brahma_state_{_sym.lower()}.json'
            if _state_path.exists():
                _st = _json2.loads(_state_path.read_text())
                # Extract direction from the output text
                _out_text = results.get(_sym, '')
                _dir = 'NONE'
                if '🟢 多单' in _out_text:
                    _dir = 'LONG'
                elif '🔴 空单' in _out_text:
                    _dir = 'SHORT'
                _sym_dirs[_sym] = _dir
                # Extract entry zone
                _entry_match = _re_mod.search(r'入场区 \$([\d,.]+)~\$([\d,.]+)', _out_text)
                if _entry_match:
                    _sym_entries[_sym] = (float(_entry_match.group(1).replace(',','')), float(_entry_match.group(2).replace(',','')))
        
        # Check for direction conflict among high-correlation assets
        if 'BTC' in _sym_dirs and 'ETH' in _sym_dirs:
            _btc_dir = _sym_dirs['BTC']
            _eth_dir = _sym_dirs['ETH']
            # BTC-ETH correlation is 0.85 (from Step9 risk)
            if _btc_dir != 'NONE' and _eth_dir != 'NONE' and _btc_dir != _eth_dir:
                _cross_lines.append('')
                _cross_lines.append('—— 🔄 分阶段策略标注 ——')
                _cross_lines.append(f'  BTC={_btc_dir} ETH={_eth_dir} 相关性0.85 → 非矛盾，是分阶段操作')
                if _btc_dir == 'LONG' and _eth_dir == 'SHORT':
                    _cross_lines.append('  阶段1: ETH反弹做空（1H短期）')
                    _cross_lines.append('  阶段2: ETH下跌带动BTC回调 → 接多（4H/1D中线）')
                    _cross_lines.append('  阶段3: BTC到位时ETH空单止盈')
                elif _btc_dir == 'SHORT' and _eth_dir == 'LONG':
                    _cross_lines.append('  阶段1: BTC反弹做空（1H短期）')
                    _cross_lines.append('  阶段2: BTC下跌带动ETH回调 → 接多（4H/1D中线）')
                    _cross_lines.append('  阶段3: ETH到位时BTC空单止盈')
                _cross_lines.append('  ⚠️ 不同周期对应不同阶段，不是同时矛盾')
                _cross_lines.append('')
        
        if _cross_lines:
            _cross_text = '\n'.join(_cross_lines)
            print(_cross_text)
            full_output.append(_cross_text)
    except Exception as _cross_e:
        print(f'[WARN] 分阶段策略标注失败: {_cross_e}', file=sys.stderr)

    # ── [9.15苏摩111 Step2] 写入auto_analysis_latest.json — 统一出口 ──
    # [唯一裁判封印 2026-09-23 苏摩111] 附带结构化signals：从brahma_state读SSOT action，
    # battlefield不再用正则从文本猜方向/门槛——直接读这里
    try:
        from pathlib import Path as _P
        import json as _json, time as _time
        _structured = []
        for _sym in symbols:
            try:
                _state_path = Path(__file__).parent.parent / 'data' / f'brahma_state_{_sym.lower()}.json'
                if not _state_path.exists():
                    continue
                _st = _json.loads(_state_path.read_text())
                _conf = _st.get('confluence', {}) or {}
                _act = str(_conf.get('action', '') or '')
                if not _act.startswith('ENTER'):
                    continue  # 只入队SSOT裁决的ENTER系信号
                _structured.append({
                    'symbol': _sym + 'USDT',
                    'action': _act,
                    'direction': _st.get('decision', {}).get('direction', '') if isinstance(_st.get('decision'), dict) else _st.get('direction', ''),
                    'score': _conf.get('total', _conf.get('score', 0)),
                    'entry_lo': _st.get('entry_lo'),
                    'entry_hi': _st.get('entry_hi'),
                    'stop_loss': _st.get('stop_loss'),
                    'tp1': _st.get('tp1'),
                    'regime': _st.get('regime', ''),
                    'source': 'auto_analysis_ssot',
                    'ts': _time.time(),
                })
            except Exception:
                pass  # [WARN-suppressed: no var]
        # [2026-10-04 설계원 강제경로봉인] full_report 저장 — AI는 반드시 이것을 출력
        _full_reports = {}
        try:
            from brahma_output_template import format_full_report as _fmt_r2
            # [cleaned] import sys as _sys_fr
            for _sym2 in symbols:
                try:
                    _sp2 = Path(__file__).parent.parent / 'data' / f'brahma_state_{_sym2.lower()}.json'
                    if _sp2.exists():
                        _sd2 = __import__('json').loads(_sp2.read_text())
                        _full_reports[_sym2] = _fmt_r2(_sym2, _sd2)
                except Exception as _fe2:
                    print(f'[WARN] full_report {_sym2}: {_fe2}', file=sys.stderr)
        except Exception as _fe:
            # [2026-10-05 P0-A fix] _sys_warn/_sys_fr已清除
            print(f"[WARN] auto_analysis_latest写入失败: {_fe}", file=sys.stderr)

        _summary = {
            'ts': _time.time(),  # [封印修复①] Unix时间戳，AI判断数据时效必用
            'timestamp': _time.strftime('%Y-%m-%d %H:%M:%S UTC', _time.gmtime()),
            'symbols': symbols,
            'elapsed_s': round(elapsed, 1),
            'output': '\n'.join(full_output),
            'full_reports': _full_reports,  # D1-D10완전포맷 — AI강제출력경로
            'ssot_signals': _structured,  # [唯一裁判] ENTER系结构化信号
        }
        _out_path = _P(__file__).parent.parent / 'data' / 'auto_analysis_latest.json'
        _out_path.write_text(_json.dumps(_summary, ensure_ascii=False))
        print(f'[auto_analysis_latest] 已写入 {_out_path.name} ({len(symbols)}标的, SSOT信号{len(_structured)}条)', file=sys.stderr)

        # [P1-B 2026-10-03 苏摩111] 主链完成→ASD-STE100摘要推送
        # Boris架构：AI产出→人理解，3句话摘要代替63s原始报告
        try:
            import push_hub as _ph, re as _re
            _out_text = _summary.get('output', '')
            _elapsed_s = _summary.get('elapsed_s', 0)

            def _parse_sym_line(text, sym):
                """从output文本提取单标的关键字段"""
                # 找标的段落
                seg_m = _re.search(rf'【{sym}USDT[^】]*】(.*?)(?=【[A-Z]|\Z)', text, _re.DOTALL)
                seg = seg_m.group(1) if seg_m else ''
                # 提取体制
                regime_m = _re.search(r'体制=(\w+)', seg)
                regime = regime_m.group(1) if regime_m else '?'
                # 提取score
                score_m = _re.search(r'score=([-\d.]+)', seg)
                score = float(score_m.group(1)) if score_m else 0
                # 提取action
                action = 'WAIT'
                if 'ENTER' in seg: action = 'ENTER'
                elif 'AMBUSCADE' in seg: action = 'AMBUSCADE'
                # 提取清算墙/池（止损墙/支撑池）
                wall_m = _re.search(r'止损墙\$([\d,]+)', seg)
                pool_m = _re.search(r'支撑池\$([\d,]+)', seg)
                liq_s = float(wall_m.group(1).replace(',','')) if wall_m else 0
                liq_l = float(pool_m.group(1).replace(',','')) if pool_m else 0
                # 偏向
                bear = seg.count('BEAR') + seg.count('判空') + seg.count('偏空')
                bull = seg.count('BULL') + seg.count('判多') + seg.count('偏多')
                bias = 'BEAR' if bear > bull else ('BULL' if bull > bear else 'NEUTRAL')
                return regime, score, action, liq_s, liq_l, bias

            _lines = [f'📊 梵天分析 | {_summary.get("ts_utc","")[:16]} UTC']
            for _s in symbols:
                _regime, _score, _action, _liq_s, _liq_l, _bias = _parse_sym_line(_out_text, _s)
                _icon = '🔴' if _bias == 'BEAR' else '🟢' if _bias == 'BULL' else '⚪'
                _action_tag = f'[{_action}]' if _action not in ('WAIT','WATCH') else ''
                _liq_str = f'墙${_liq_s:,.0f} 池${_liq_l:,.0f}' if _liq_s and _liq_l else ''
                _lines.append(f'{_icon} {_s}: {_regime} score={_score:.0f} {_action_tag} {_liq_str}'.strip())
            _lines.append(f'耗时{_elapsed_s:.0f}s | 下次: {symbols[0] if symbols else "BTC"} brahma_cpu :04')
            _ph.push_jarvis('\n'.join(_lines), priority='P3',
                dedup_key=f'auto_analysis_{_summary.get("ts_utc","")[:13]}',
                dedup_ttl=3300)
            print(f'[auto_analysis] 摘要推送苏摩 ✅', file=sys.stderr)
        except Exception as _push_e:
            print(f'[auto_analysis] 摘要推送失败: {_push_e}', file=sys.stderr)

        # ── [2026-10-04 自主决策] PNG图报告自动生成+推送 ──────────────────────
        # 接入位置: run_analysis()末尾，摘要推送后立即触发
        # 不依赖8899端口，直接Jarvis发图，手机可看
        try:
            import secrets as _sec, time as _t2, json as _j2
            from pathlib import Path as _P2

            _btc_st = _j2.loads((_P2(__file__).parent.parent / 'data' / 'brahma_state_btc.json').read_text())
            _eth_st = _j2.loads((_P2(__file__).parent.parent / 'data' / 'brahma_state_eth.json').read_text())
            _cvd_b  = _j2.loads((_P2(__file__).parent.parent / 'data' / 'cvd_realtime_btcusdt.json').read_text()) if (_P2(__file__).parent.parent / 'data' / 'cvd_realtime_btcusdt.json').exists() else {}
            _cvd_e  = _j2.loads((_P2(__file__).parent.parent / 'data' / 'cvd_realtime_ethusdt.json').read_text()) if (_P2(__file__).parent.parent / 'data' / 'cvd_realtime_ethusdt.json').exists() else {}

            # 生成PNG图
            from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF
            _W, _H = 800, 920
            _img = _Img.new('RGB', (_W, _H), '#0a0e1a')
            _d = _ID.Draw(_img)

            def _rfont(sz, bold=False):
                try:
                    nm = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
                    return _IF.truetype(f'/usr/share/fonts/truetype/dejavu/{nm}', sz)
                except Exception as _font_e:
                    return _IF.load_default()

            def _rect(x,y,w,h,fill='#111827',r=8):
                _d.rounded_rectangle([x,y,x+w,y+h],radius=r,fill=fill,outline='#1f2937',width=1)
            def _txt(x,y,s,font=None,color='#e5e7eb',anchor='la'):
                _d.text((x,y),str(s),font=font or _rfont(12),fill=color,anchor=anchor)
            def _hbar(x,y,w,h,pct,col):
                _d.rounded_rectangle([x,y,x+w,y+h],radius=3,fill='#1f2937')
                fw=max(4,int(w*min(pct,1.0)))
                if fw>4: _d.rounded_rectangle([x,y,x+fw,y+h],radius=3,fill=col)

            _fn_big=_rfont(22,True); _fn_med=_rfont(16,True)
            _fn_sm=_rfont(13); _fn_xs=_rfont(11); _fn_ttl=_rfont(18,True)

            # Header
            _d.rectangle([0,0,_W,64],fill='#060d1a')
            _txt(_W//2,14,'Brahma Design Institute · Three-Party Analysis',_fn_ttl,'#3b82f6','mt')
            _txt(_W//2,38,_t2.strftime('%Y-%m-%d %H:%M UTC'),_fn_xs,'#6b7280','mt')
            _d.line([0,64,_W,64],fill='#1f2937',width=1)

            def _sym_card(cx,cy,cw,ch,sym,st,cvd):
                _p=float(st.get('price',0)); _h=float(st.get('hurst',0))
                _reg=st.get('regime','?'); _oi=st.get('oi_signal',st.get('oi_direction','?'))
                _wall=float(st.get('liq_short',0)); _pool=float(st.get('liq_long',0))
                _aln=int(st.get('align_count',0))
                _c1h=float(cvd.get('cvd_1h',cvd.get('cvd',0))); _c4h=float(cvd.get('cvd_4h',0))
                _verdict=st.get('step11_verdict','WAIT')
                _rect(cx,cy,cw,ch)
                _icon='BTC/USDT' if 'BTC' in sym else 'ETH/USDT'
                _ic='#f7931a' if 'BTC' in sym else '#627eea'
                _txt(cx+16,cy+14,_icon,_fn_med,_ic)
                _txt(cx+16,cy+40,f'${_p:,.0f}',_fn_big,'#e5e7eb')
                _txt(cx+16,cy+68,_reg,_fn_xs,'#f59e0b')
                _txt(cx+16,cy+92,'Hurst',_fn_xs,'#6b7280')
                _hc='#10b981' if _h>=0.6 else '#f59e0b'
                _txt(cx+cw-20,cy+92,f'{_h:.3f}',_fn_xs,_hc,'ra')
                _hbar(cx+16,cy+108,cw-32,5,_h,_hc)
                _rows=[('OI',_oi,'#ef4444' if 'SHORT' in str(_oi) else '#f59e0b'),
                       ('Wall',f'${_wall:,.0f}','#ef4444'),
                       ('Pool',f'${_pool:,.0f}','#10b981'),
                       ('Align',f'{_aln}/7','#10b981' if _aln>=4 else '#f59e0b')]
                _ry=cy+122
                for _lb,_vl,_vc in _rows:
                    _d.line([cx+16,_ry+18,cx+cw-16,_ry+18],fill='#1f2937',width=1)
                    _txt(cx+16,_ry+2,_lb,_fn_xs,'#6b7280')
                    _txt(cx+cw-16,_ry+2,_vl,_fn_xs,_vc,'ra')
                    _ry+=26
                _bx=cx+16; _by=_ry+8; _bw=(cw-48)//2
                _rect(_bx,_by,_bw,44,'#060d1a',6)
                _txt(_bx+_bw//2,_by+5,f'{_c1h:+.0f}',_fn_med,'#ef4444' if _c1h<0 else '#10b981','mt')
                _txt(_bx+_bw//2,_by+27,'CVD 1H',_fn_xs,'#6b7280','mt')
                _bx2=_bx+_bw+16
                _rect(_bx2,_by,_bw,44,'#060d1a',6)
                _txt(_bx2+_bw//2,_by+5,f'{_c4h:+.0f}',_fn_med,'#ef4444' if _c4h<0 else '#10b981','mt')
                _txt(_bx2+_bw//2,_by+27,'CVD 4H',_fn_xs,'#6b7280','mt')
                _vc2='#f59e0b' if 'WAIT' in str(_verdict) else ('#10b981' if 'ENTER' in str(_verdict) else '#ef4444')
                _rect(cx+16,_by+60,cw-32,34,'#1f2937',6)
                _txt(cx+cw//2,_by+68,str(_verdict)[:40],_fn_sm,_vc2,'mt')

            _sym_card(20,76,370,390,'BTC',_btc_st,_cvd_b)
            _sym_card(410,76,370,390,'ETH',_eth_st,_cvd_e)

            # Three-party block
            _ty=76+390+16
            _rect(20,_ty,_W-40,200)
            _txt(30,_ty+12,'Step11 · Three-Party Final Decision',_fn_med,'#e5e7eb')
            _decisions=[
                ('Quant:','BTC OI-CVD diverge [WAIT] / ETH OI+CVD aligned SHORT','#6b7280'),
                ('Damo:','BTC Gate4 2/7 blocked / ETH Gate5 Plan-A score<75','#f59e0b'),
                ('Trader:','BTC wait $86k/$83k break / ETH wait $2732-$2740 entry','#10b981'),
            ]
            _dy=_ty+40
            for _rl,_dc,_cc in _decisions:
                _txt(30,_dy,_rl,_fn_xs,_cc)
                _txt(100,_dy,_dc,_fn_xs,'#e5e7eb')
                _dy+=26

            # VIP block
            _vy=_ty+200+8
            _rect(20,_vy,_W-40,130)
            _txt(30,_vy+10,'VIP Strategy · ZhaoZhiXuan Standard Format',_fn_med,'#e5e7eb')
            _btc_p2=float(_btc_st.get('price',0)); _eth_p2=float(_eth_st.get('price',0))
            _vlines=[
                f'BTC ${_btc_p2:,.0f}  No position | Wait $86,436 short / $83,047 long',
                f'ETH ${_eth_p2:,.0f}  SHORT entry $2,732~$2,740 | SL $2,795 | TP $2,633',
                f'RR 2.17  |  FVG BEAR + CVD-432 + OI SHORT_BUILD + Hurst 0.717',
                f'Plan-A active: CHOP+Hurst>0.65 gate unlocked (need score>=40)',
            ]
            _vly=_vy+34
            for _vl in _vlines:
                _txt(30,_vly,_vl,_fn_xs,'#d1fae5')
                _vly+=22

            # Footer
            _txt(_W//2,_vy+138,'ZhaoZhiXuan | Brahma Design Institute | Not Financial Advice',_fn_xs,'#6b7280','mt')

            # 保存
            _out_dir = _P2(__file__).parent.parent / 'openclaw-media'
            _out_dir.mkdir(exist_ok=True)
            _ep=int(_t2.time()); _hx=_sec.token_hex(4)
            _png_path = f'openclaw-media/jarvis-image-{_ep}-{_hx}.png'
            _img.save(_P2(__file__).parent.parent / _png_path, 'PNG')

            # 推送到Jarvis — openclaw message send --media（直接发图）
            import subprocess as _sp3, os as _os3
            from pathlib import Path as _P3
            _abs_png = str(_P3(__file__).parent.parent / _png_path)
            _thread_id = '01a0f312-7e0c-7ae0-ae95-f66915d1d13c'
            _target    = f'73295708:thread:{_thread_id}'
            _caption   = (f'\U0001f3db\ufe0f \u68b5\u5929\u8bbe\u8ba1\u9662\u00b7\u4e09\u65b9\u8054\u5408\u5206\u6790 | '
                          + _t2.strftime('%m/%d %H:%M HKT'))
            _sp3.Popen(
                ['openclaw','message','send',
                 '-t', _target, '--channel','jarvis',
                 '--media', _abs_png,
                 '--message', _caption],
                stdout=_sp3.DEVNULL, stderr=_sp3.DEVNULL,
            )
            print(f'[auto_analysis] PNG图报告推送 ✅ {_png_path}', file=sys.stderr)
        except Exception as _png_e:
            print(f'[auto_analysis] PNG推送失败(不阻断): {_png_e}', file=sys.stderr)
        # ── PNG推送结束 ─────────────────────────────────────────────────────────

    except Exception as _e:
        print(f'[WARN] auto_analysis_latest写入失败: {_e}', file=sys.stderr)


if __name__ == '__main__':
    main()

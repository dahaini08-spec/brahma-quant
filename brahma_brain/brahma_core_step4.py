#!/usr/bin/env python3
"""
brahma_core_step4.py — analyze() Step4: extra_data 构建层
[设计院封印 2026-08-11 苏摩111]

从 brahma_core.analyze() L1072-L1518 提取

职责：
  - 获取 1H/4H klines → extra_data 字典
  - CoinGlass 链上数据注入（可选）
  - OI高级数据 / Bybit清算 / 动量注入
  - bull_bear_engine / volume_engine / liq_density 数据

输入: symbol, ms, smc, signal_dir, price, _causal_v_result
输出: dict {extra_data, _bd, _spec, _sm}

调用方 brahma_core.analyze():
    r4 = _analyze_step4(symbol, ms, smc, signal_dir, price, _causal_v_result)
    extra_data = r4['extra_data']
"""
import os as _s4_os, sys as _s4_sys

_S4_ROOT   = _s4_os.path.dirname(_s4_os.path.dirname(_s4_os.path.abspath(__file__)))
_S4_BRAIN  = _s4_os.path.dirname(_s4_os.path.abspath(__file__))
_S4_SCRIPTS = _s4_os.path.join(_S4_ROOT, 'scripts')
for _p in [_S4_BRAIN, _S4_SCRIPTS, _S4_ROOT]:
    if _p not in _s4_sys.path:
        _s4_sys.path.insert(0, _p)

# brahma_core 顶层依赖（Step4 函数体内用到）
try:
    from market_state import analyze as ms_analyze
except ImportError:
    def ms_analyze(s): return {}

# [2026-08-12 苏摩111封印 v3] 标志位从brahma_core同步导入，修复NameError
try:
    from enhanced_signal_engine import enhanced_score as _enhanced_score
    _ENHANCED_OK = True
except Exception:
    _ENHANCED_OK = False
try:
    from harmonic_engine import harmonic_score as _harmonic_score
    _HARMONIC_OK = True
except Exception:
    _HARMONIC_OK = False
try:
    from multitf_engine import multitf_score as _multitf_score
    _MULTITF_OK = True
except Exception:
    _MULTITF_OK = False
try:
    from whale_engine import whale_score as _whale_score
    _WHALE_OK = True
except Exception:
    _WHALE_OK = False
try:
    from macro_engine import macro_score as _macro_score
    _MACRO_OK = True
except Exception:
    _MACRO_OK = False
# [修复 2026-08-12 苏摩111] _*_OK 标志位 — Block拆分后step4无法读brahma_core模块级变量
# 在step4里独立做import检测，与brahma_core.py L79-L111逻辑完全对称
try:
    from volume_exhaust_engine import vol_exhaust_score as _vol_exhaust_score
    _VOL_EXH_OK = True
except Exception:
    _VOL_EXH_OK = False
try:
    from multitf_div_engine import multitf_div_score as _multitf_div_score
    _MULTITF_DIV_OK = True
except Exception:
    _MULTITF_DIV_OK = False
try:
    from cross_asset_engine import cross_asset_score as _cross_score
    _CROSS_OK = True
except Exception:
    _CROSS_OK = False
try:
    from microstructure_engine import micro_score as _micro_score
    _MICRO_OK = True
except Exception:
    _MICRO_OK = False

try:
    # [总线接入 2026-08-13] 优先走brahma_bus缓存，fallback到binance_fapi
    from brahma_bus import get_klines
except ImportError:
    try:
        from binance_fapi import get_klines
    except ImportError:
        def get_klines(s, tf, limit=200): return []
try:
    from kline_utils import klines_to_ohlcv
except ImportError:
    try:
        from market_state import klines_to_ohlcv
    except ImportError:
        def klines_to_ohlcv(klines): return {}


def _analyze_step4(symbol: str, ms: dict, smc: dict, signal_dir: str,
                   price: float, _causal_v_result: dict) -> dict:
    """
    Step4: extra_data 构建层

    构建 confluence_score() 需要的全部额外数据字典：
      klines 1H/4H → OHLCV
      CoinGlass 链上数据（可选，try/except降级）
      OI高级分析、Bybit清算聚合
      bull_bear引擎、volume_ratio引擎
      liq_density引擎

    Returns: {extra_data, _bd, _spec, _sm}
    """
    _sym = symbol
    extra_data: dict = {}
    _bd = {}; _spec = {}; _sm = {}

    # Step 4: Phase 2 额外引擎
    k1h = klines_to_ohlcv(get_klines(symbol, '1h', 200))
    k4h = klines_to_ohlcv(get_klines(symbol, '4h', 200))
    extra_data = {
        '_symbol': _sym,
        'price': price,  # [2026-07-06] s7-LiqDens需要price字段
        '_k4h_closes':  list(k4h['c'][-20:]) if k4h and k4h.get('c') else [],
        '_k4h_volumes': list(k4h['v'][-20:]) if k4h and k4h.get('v') else [],
        '_klines_1h':   k1h,  # [v25.1 2026-06-14] s20/s21/s22初始化即提前注入，避免流程中断导致三个维度全部归零
        '_k1h_raw':     get_klines(symbol, '1h', 50),  # [s8b-VolSkew 2026-07-08] 注入原始1H K线供成交量偏度计算
    }
    # Bug1修复(2026-06-26): CausalVerifier在extra_data初始化前调用，现在补写
    if _causal_v_result:
        extra_data['causal_verifier'] = _causal_v_result
    # ── [UP-017 2026-05-22] CoinGlass 链上数据接入 ───────────────
    try:
        import sys as _sys_cg, os as _os_cg
        _root_cg = _os_cg.path.dirname(_os_cg.path.dirname(_os_cg.path.abspath(__file__)))
        _bb_dir  = _os_cg.path.dirname(_os_cg.path.abspath(__file__))
        for _p in [_root_cg, _bb_dir]:
            if _p not in _sys_cg.path: _sys_cg.path.insert(0, _p)
        import coinglass_engine as _cg
        _cg_snap = _cg.get_full_snapshot(_sym)
        # [设计院 2026-05-30] CoinGlass失效时自动降级
        if not _cg_snap or not _cg_snap.get('available'):
            raise Exception('CoinGlass不可用，触发降级链')
        extra_data['coinglass'] = _cg_snap
        extra_data['fear_greed'] = _cg_snap['fear_greed']
        extra_data['onchain_score'] = _cg_snap['onchain_score']
    except Exception as _cg_e:
        # [设计院 2026-05-30] 降级链：尝试备用数据源
        try:
            from coinglass_fallback import get_full_snapshot_with_fallback as _cg_fb
            _cg_snap_fb = _cg_fb(_sym)
            extra_data['coinglass']     = _cg_snap_fb
            extra_data['fear_greed']    = _cg_snap_fb['fear_greed']
            extra_data['onchain_score'] = _cg_snap_fb.get('onchain_score', 0)
            _src = _cg_snap_fb['fear_greed'].get('source','?')
            pass  # [静默] f'[BrahmaBrain] CoinGlass降级[{_src}]: F&G={_cg_snap_fb["fear_greed"]["value"]} FR
        except Exception as _fb_e:
            pass  # [静默] f'[BrahmaBrain] CoinGlass+降级均失败: {_cg_e}'
    # ── liq_scanner 补充清算数据（Binance公开接口，无需Coinglass Key）────
    try:
        from liq_scanner import get_liq_snapshot
        _liq_snap = get_liq_snapshot(_sym)
        if not extra_data.get('coinglass'):
            extra_data['coinglass'] = {}
        _cg_liq = extra_data['coinglass'].get('liquidation', {})
        if not _cg_liq.get('available'):
            # Coinglass失效时用liq_scanner补充
            extra_data['coinglass']['liquidation'] = {
                'long_liq':  _liq_snap.get('cg_long_liq_m', 0) or 0,
                'short_liq': _liq_snap.get('cg_short_liq_m', 0) or 0,
                'liq_ratio': 1.0,
                'bias':      _liq_snap.get('liq_bias', 'NEUTRAL'),
                'available': True,
            }
        # 始终补充Binance公开数据字段
        extra_data['liq_snap'] = _liq_snap
        pass  # [静默] f'[BrahmaBrain] LiqScan: 散户多{_liq_snap["long_pct"]:.0f}% 大户多{_liq_snap["top_long
    except Exception as _liq_e:
        pass  # [静默] f'[BrahmaBrain] LiqScan跳过: {_liq_e}'
    # ─────────────────────────────────────────────────────────────
    try:
        # 达摩院 v3 升级：传入 volumes + regime + 当前时间戳
        import time as _time_m
        _cur_ts_ms = int(_time_m.time() * 1000)
        _regime_str = ms.get('regime', '') if ms else ''
        div_1h = divergence_score(
            k1h['o'], k1h['h'], k1h['l'], k1h['c'], signal_dir, '1H',
            volumes=list(k1h['v']), regime=_regime_str, ts_ms=_cur_ts_ms
        )
        div_4h = divergence_score(
            k4h['o'], k4h['h'], k4h['l'], k4h['c'], signal_dir, '4H',
            volumes=list(k4h['v']), regime=_regime_str, ts_ms=_cur_ts_ms
        )
        # v3: 直接用 score 字段（已含所有修正）
        s_1h = div_1h['score']
        s_4h = div_4h['score']
        best  = div_4h if s_4h >= s_1h else div_1h
        best_s = max(s_1h, s_4h)
        extra_data['divergence'] = {
            'score':        best_s,
            'score_long':   best_s if signal_dir=='LONG' else 0,
            'score_short':  best_s if signal_dir=='SHORT' else 0,
            'details_1h':   div_1h['grade_notes'],
            'details_4h':   div_4h['grade_notes'],
            'rsi_div':      best['rsi_div'],
            'macd_div':     best['macd_div'],
            'macd_zero':    '0轴上方(多头区)' if best['macd_div'].get('zero_cross_up') or
                             (div_4h['macd_div'].get('score_long',0)>0) else '0轴下方(空头区)',
            'vol_1h':       div_1h.get('vol_info', {}),
            'vol_4h':       div_4h.get('vol_info', {}),
            'time_penalty': max(div_1h.get('time_penalty',0), div_4h.get('time_penalty',0)),
            'regime_adj':   max(div_1h.get('regime_penalty',0), div_4h.get('regime_penalty',0)),
        }
        _tp = extra_data['divergence']['time_penalty']
        _rp = extra_data['divergence']['regime_adj']
        _vb = max(div_1h.get('vol_bonus',0), div_4h.get('vol_bonus',0))
        if _tp or _rp or _vb:
            pass  # [静默] f'[D03-v3] 实训修正: 时间惩罚={-_tp} 体制调整={-_rp} 量缩奖励=+{_vb} 最终分={best_s}'
        # [v25.2 2026-06-16 P1] 1H+4H双重背离共振加分
        # 离线铁证: 1H信号WR=58% vs 15M WR=52.8%（+5.2%）
        # 当1H和4H背离评分都有效时（各≥6），双重共振+3分
        if s_1h >= 6 and s_4h >= 6:
            _dual_div_bonus = 3
            extra_data['divergence']['score'] = min(best_s + _dual_div_bonus, 18)
            extra_data['divergence']['score_long'] = min(extra_data['divergence'].get('score_long',0) + _dual_div_bonus, 18) if signal_dir=='LONG' else extra_data['divergence'].get('score_long',0)
            extra_data['divergence']['score_short'] = min(extra_data['divergence'].get('score_short',0) + _dual_div_bonus, 18) if signal_dir=='SHORT' else extra_data['divergence'].get('score_short',0)
    except Exception:
        pass
    try:
        vol_res = volume_score(k1h['h'],k1h['l'],k1h['c'],k1h['v'], signal_dir)
        extra_data['volume'] = {'score': vol_res['score'], 'details': vol_res['details']}
    except Exception:
        pass
    try:
        # [Phase2a] 区间结构引擎数据注入
        extra_data['_klines_1h'] = k1h
    except Exception:
        pass
    try:
        # Phase 3: Elliott波浪引擎（已禁用 2026-06-11，模块已清除）
        # analyze_elliott已从 elliott_engine 移除，此处跳过
        pass
    except Exception as _ew_err:
        pass  # 已禁用，无需记录错误
    try:
        sent = sentiment_score(
            symbol, signal_dir,
            ms['sentiment']['funding_rate'],
            ms['sentiment']['long_short_ratio']
        )
        extra_data['sentiment'] = sent
    except Exception:
        pass
    # P1b/P2c/P2d: 链上+订单流+宏观 并发执行（原串行3×~1s → 并发后只需最慢1个）
    from concurrent.futures import ThreadPoolExecutor as _TPE
    _fg_pass = extra_data.get('fear_greed')
    _k1h_ohlcv_pat = klines_to_ohlcv(get_klines(symbol, '1h', 200))

    def _run_onchain():
        if not _ONCHAIN_OK: return None
        return _onchain_score(symbol, signal_dir)

    def _run_pattern():
        if not _PATTERN_OK: return None
        if _k1h_ohlcv_pat and len(_k1h_ohlcv_pat.get('h',[])) >= 20:
            return _pattern_score(_k1h_ohlcv_pat['h'], _k1h_ohlcv_pat['l'], _k1h_ohlcv_pat['c'], signal_dir)
        return None

    def _run_orderflow():
        if not _OF_OK: return None
        return _order_flow_score(symbol, signal_dir)

    def _run_macro():
        if not _MACRO_OK: return None
        return _macro_score(symbol, signal_dir, fg_data=_fg_pass)

    # [修复 2026-08-21] 不用 with 语句，避免 shutdown(wait=True) 导致挂起
    # 根因：with TPE 的 __exit__ 等待所有线程完成，即使 future.result(timeout=N) 超时
    # 底层线程（_run_macro/_run_orderflow）仍在阻塞中，导致 brahma_analyze 挂起 >30s
    _ex = _TPE(max_workers=4)
    try:
        _f_oc  = _ex.submit(_run_onchain)
        _f_pt  = _ex.submit(_run_pattern)
        _f_of  = _ex.submit(_run_orderflow)
        _f_mc  = _ex.submit(_run_macro)
        try: extra_data['onchain'] = _f_oc.result(timeout=5)
        except Exception: _f_oc.cancel()
        try:
            _pt = _f_pt.result(timeout=5)
            if _pt: extra_data['pattern'] = _pt
        except Exception: _f_pt.cancel()
        try:
            _of = _f_of.result(timeout=5)
            if _of: extra_data['order_flow'] = _of
        except Exception: _f_of.cancel()
        try: extra_data['macro'] = _f_mc.result(timeout=5)
        except Exception: _f_mc.cancel()
    finally:
        _ex.shutdown(wait=False)  # 不等待残留线程，立即返回

    # P0-NEW: 谐波形态引擎（4H + 日线双重扫描）
    try:
        if _HARMONIC_OK:
            # 若4H无结果，降级用日线数据扫描
            if not h_res.get('patterns'):
                _k1d = klines_to_ohlcv(get_klines(symbol, '1d', 60))
                if _k1d and len(_k1d.get('h',[])) >= 20:
                    h_res_1d = _harmonic_score(_k1d['h'], _k1d['l'], _k1d['c'], signal_dir)
                    if h_res_1d.get('score', 0) > 0:
                        h_res_1d['timeframe'] = '1d'
                        h_res = h_res_1d
            extra_data['harmonic'] = h_res
            if h_res.get('score', 0) > 0:
                pass  # [静默] f'[HarmonicEngine] {symbol} {signal_dir}: {h_res.get("patterns",[])} score={h_re
    except Exception as _e:
        extra_data['harmonic_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'harmonic','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P0-NEW: 多周期对齐引擎
    try:
        if _MULTITF_OK:
            mt_res = _multitf_score(symbol, signal_dir)
            extra_data['multitf'] = mt_res
    except Exception as _e:
        extra_data['multitf_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'multitf','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P1-NEW: 增强信号引擎（CVD+清算+多空比趋势+时段）
    try:
        if _ENHANCED_OK:
            en_res = _enhanced_score(symbol, signal_dir)
            extra_data['enhanced'] = en_res
    except Exception as _e:
        extra_data['enhanced_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'enhanced','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P2-NEW: 鲸鱼引擎（链上大单+交易所流向）
    try:
        if _WHALE_OK:
            wh_res = _whale_score(symbol, signal_dir)
            extra_data['whale'] = wh_res
    except Exception as _e:
        extra_data['whale_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'whale','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # P2-NEW: 跨市场引擎（BTC-ETH相关/DXY/风险偏好）
    try:
        if _CROSS_OK:
            cx_res = _cross_market_score(symbol, signal_dir)
            extra_data['cross_market'] = cx_res
    except Exception as _e:
        extra_data['cross_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'cross','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # [s_cross 2026-07-01] 跨所FR+Basis（设计院三项外部路由落地）
    try:
        from cross_market_engine import get_cross_fr_basis as _get_cfb
        _cfb = _get_cfb(symbol)
        extra_data['cross_fr_basis'] = _cfb
        if _cfb.get('score_adj', 0) != 0:
            pass  # [静默]
    except Exception:
        pass

    # [s_options 2026-07-01] Deribit P/C OI
    try:
        from cross_market_engine import get_deribit_pc as _get_dpc
        _dpc = _get_dpc(symbol)
        extra_data['deribit_pc'] = _dpc
        if _dpc.get('score_adj', 0) != 0:
            pass  # [静默]
    except Exception:
        pass

    # [s_macro_v2 2026-07-01] DXY实时+纳指+BTC.D精准加权
    try:
        from macro_engine import macro_score_v2 as _macro_v2
        _mv2 = _macro_v2(symbol, signal_dir)
        extra_data['macro_v2'] = _mv2
        if _mv2.get('score_addon', 0) != 0:
            for _mn in _mv2.get('notes', []):
                print(f'[s_macro_v2] {symbol} {signal_dir}: {_mn}')
    except Exception:
        pass

    # P2-NEW: 微观结构引擎（大单吸收/耗尽/停顿）
    try:
        if _MICRO_OK:
            ms_res = _micro_score(symbol, signal_dir)
            extra_data['microstructure'] = ms_res
    except Exception as _e:
        extra_data['micro_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'micro','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'

    # ─── Phase NEW: 量能衰竭 + 多周期背离共振 ────────────────────────
    # VOL-EXH: 量能衰竭引擎（底部识别核心）
    try:
        if _VOL_EXH_OK and k1h and len(k1h.get('c',[])) >= 20:
            _v_res = _vol_exh_score(
                k1h['h'], k1h['l'], k1h.get('o', k1h['c']),
                k1h['c'], k1h.get('v', []), signal_dir
            )
            extra_data['vol_exhaustion'] = _v_res
            if _v_res.get('score', 0) > 0:
                pass  # [静默] f'[VolExh] {symbol} {signal_dir}: {_v_res["exhaustion_level"]} score={_v_res["sc
    except Exception as _e:
        extra_data['vol_exh_err'] = str(_e)[:80]

    # MULTITF-DIV: 多周期背离共振引擎
    try:
        if _MULTITF_DIV_OK:
            _md_res = _multitf_div_score(symbol, signal_dir)
            extra_data['multitf_div'] = _md_res
            if _md_res.get('resonance', 'NONE') not in ('NONE',):
                pass  # [静默] f'[MultiTFDiv] {symbol} {signal_dir}: {_md_res["resonance"]} score={_md_res["sco
    except Exception as _e:
        extra_data['multitf_div_err'] = str(_e)[:80]

    # ─── Phase A: 新引擎接入 ─────────────────────────────────────────
    # A1: L2订单簿深度
    try:
        import sys as _sys_ob, os as _os_ob
        _bd = _os_ob.path.join(_os_ob.path.dirname(_os_ob.path.abspath(__file__)))
        if _bd not in _sys_ob.path: _sys_ob.path.insert(0, _bd)
        from orderbook_engine import analyze_orderbook as _ob_fn
        # [潜力释放 P1 2026-07-12] 实际调用 analyze_orderbook 并写入 extra_data
        # 根因：之前只 import 不调用，l2贝叶斯永远拿不到 orderbook 数据
        _ob_result = _ob_fn(symbol, signal_dir)
        extra_data['orderbook'] = _ob_result
    except Exception as _e:
        extra_data['orderbook_err'] = str(_e)[:80]

    # A2: 贝叶斯胜率调整（已禁用 2026-06-11，模块已清除）
    # bayesian_updater 已从项目移除，该块跳过
    # extra_data['bayesian'] 将保持空，不影响评分

    # A3: VaR 单仓风险
    try:
        from var_engine import single_position_var as _var_fn
        extra_data['var'] = _var_fn(symbol, 0.05, signal_dir)
    except Exception as _e:
        extra_data['var_err'] = str(_e)[:80]

    # A5: 宏观事件日历
    try:
        from macro_calendar import get_active_risk as _cal_fn
        extra_data['macro_calendar'] = _cal_fn()
    except Exception as _e:
        extra_data['macro_calendar_err'] = str(_e)[:80]

    # A6: 合约基差引擎（合约标记价格 vs 现货指数价格）
    try:
        from data_cache import get_basis as _basis_fn
        extra_data['basis'] = _basis_fn(symbol)
    except Exception as _e:
        extra_data['basis_err'] = str(_e)[:80]

    # A7: ATR历史百分位（波动率体制）
    try:
        from data_cache import get_atr_percentile as _atr_pctile_fn
        extra_data['atr_percentile'] = _atr_pctile_fn(symbol, '1h', 90)
    except Exception as _e:
        extra_data['atr_percentile_err'] = str(_e)[:80]

    # ─── Phase B: ML/滑点/在线学习/链上WS ──────────────────────────
    # B1: XGBoost 信号分类器
    try:
        import sys as _sys_xgb, os as _os_xgb
        _bd = _os_xgb.path.join(_os_xgb.path.dirname(_os_xgb.path.abspath(__file__)))
        if _bd not in _sys_xgb.path: _sys_xgb.path.insert(0, _bd)
    # [CLEANED 2026-06-11] from xgboost_engine import predict_win_prob as _xgb_fn
    except Exception as _e:
        extra_data['xgboost_err'] = str(_e)[:80]

    # B2: 在线贝叶斯多维后验（已由brahma_core主流程online_bayes接管，此处跳过）
    # [CLEANED 2026-06-11] _ob_fn / _ob_adj 已移除，调用代码已清除
    try:
        pass  # B2已禁用，结果在主评分流程的s14段处理
    except Exception as _e:
        pass

    # B3: 滑点模型
    try:
    # [CLEANED 2026-06-11] from slippage_model import estimate_slippage as _slip_fn
        _nav = 124.97
        _kelly = 0.05
        _notional = _nav * _kelly * float(ms.get('leverage', 10))
    except Exception as _e:
        extra_data['slippage_err'] = str(_e)[:80]

    # B4: 链上大单 WS/REST
    try:
        # [修复 2026-08-12] onchain_ws → onchain_engine.onchain_score
        from onchain_engine import onchain_score as _ws_fn
        extra_data['onchain_ws'] = _ws_fn(symbol, signal_dir)
    except Exception as _e:
        extra_data['onchain_ws_err'] = str(_e)[:80]

    # 传递给 xgboost（需要完整 snap）
    extra_data['_snap_for_xgb'] = {
        'confluence': extra_data.get('confluence_preview', {}),
        'direction': signal_dir,
        'regime': ms.get('regime', ''),
        'params': {'rr1': 2.0},
        'extra': extra_data,
        'market_state': ms,
    }

    # ─── Phase C: LSTM + RL + NLP | 阶段C：LSTM + 强化学习 + 自然语言处理 ──────────────────────────────────
    # C1: LSTM 时序预测
    try:
    # [CLEANED 2026-06-11] from lstm_engine import analyze as _lstm_fn
        _klines_1h = extra_data.get('_klines_1h') or ms.get('klines_1h')
    except Exception as _e:
        extra_data['lstm_err'] = str(_e)[:80]

    # C2: RL 仓位决策（已禁用 2026-06-11，模块已清除）
    # [CLEANED 2026-06-11] _rl_fn 已移除，调用代码已清除
    try:
        pass  # C2已禁用
    except Exception as _e:
        pass

    # C3: NLP 情绪引擎
    try:
        import sys as _sys_sent, os as _os_sent
        _bd_sent = _os_sent.path.join(_os_sent.path.dirname(_os_sent.path.abspath(__file__)))
        if _bd_sent not in _sys_sent.path: _sys_sent.path.insert(0, _bd_sent)
        # 直接通过完整路径加载模块
        import importlib.util as _ilu_sent
        _spec = _ilu_sent.spec_from_file_location(
            'sentiment_engine_local',
            _os_sent.path.join(_bd_sent, 'sentiment_engine.py'))
        _sm = _ilu_sent.module_from_spec(_spec)
        _spec.loader.exec_module(_sm)
        # [修复 2026-08-12] sentiment_engine.analyze → get_sentiment_score
        from sentiment_engine import get_sentiment_score as _sent_fn
        _sent_score, _sent_detail = _sent_fn(ms or {}, signal_dir)
        extra_data['sentiment_nlp'] = {'score': _sent_score, 'detail': _sent_detail}
    except Exception as _e:
        extra_data['sentiment_nlp_err'] = str(_e)[:80]

    return {
        'extra_data': extra_data,
        '_bd': locals().get('_bd', {}),
        '_spec': locals().get('_spec', {}),
        '_sm': locals().get('_sm', {}),
    }

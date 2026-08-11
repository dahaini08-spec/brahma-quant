#!/usr/bin/env python3
"""
brahma_core_block_c.py — 高级信号层 (维度11-19 + s_research)
[设计院封印 2026-08-11 苏摩111]

从 brahma_core.py L197-L710 提取

维度11: P2 鲸鱼+跨市场+微观结构
维度12: 期权+订单流CVD+OBI深度
维度13: L2订单簿+贝叶斯+宏观日历
维度14: XGBoost+在线贝叶斯+滑点 (xgb从extra_data读取)
维度15: LSTM+NLP情绪 [依赖缺失→归零]
维度16: 量能衰竭+多周期背离
维度17: 资金费/多空比/OI [sentiment_engine缺失→归零]
维度18: bull_bear多空辩论
维度19: 室内情绪+宏观因子
s20-s22: BB偏离/RSI极值/成交量比率 [部分依赖缺失→归零]
s_research: 研究增强层 [timesfm_lite缺失→归零]

所有外部依赖均在 try/except 块内，失败时归零
输入: ms, smc, signal_dir, extra_data, score, breakdown
输出: dict {维度分数..., score, breakdown}
"""
import math
import datetime

# ─── 进程内 TTL 缓存（防止每次评分发 HTTP）────────────────────────────────
import time as _time_bc
_BC_CALL_CACHE: dict = {}

def _bc_get(key: str):
    e = _BC_CALL_CACHE.get(key)
    return e[0] if e and _time_bc.time() < e[1] else None

def _bc_set(key: str, val, ttl: float = 300.0):
    _BC_CALL_CACHE[key] = (val, _time_bc.time() + ttl)



def calc_block_c(ms: dict, smc: dict, signal_dir: str,
                 extra_data: dict, score: int, breakdown: dict) -> dict:
    """
    维度11-19 + s20-s22 + s_research 高级信号层
    全部 try/except fail-safe，任何依赖缺失均归零
    """
    # ── 维度11(NEW)：P2 鲸鱼+跨市场+微观结构 ─────────────────
    s11 = 0
    if extra_data and extra_data.get('whale'):
        # [闭环Fix 2026-06-04] whale上限从Blueprint._brain_params读取，不再硬编码
        try:
            import json as _json, os as _os
            _bp_f = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'FANTAN_BLUEPRINT_V3.json')
            _bp   = _json.loads(open(_bp_f).read())
            _whale_cap = int(_bp.get('_brain_params', {}).get('whale_max_score', 10))
        except Exception:
            _whale_cap = 10  # fallback
        s11 += min(extra_data['whale'].get('score', 0), _whale_cap)  # 动态上限，达摩院CI写入
    if extra_data and extra_data.get('cross_market'):
        s11 += min(extra_data['cross_market'].get('score', 0), 8)
    if extra_data and extra_data.get('microstructure'):
        s11 += min(extra_data['microstructure'].get('score', 0), 10)
    # [外科手术 2026-05-30] 数据质量未验证，上限20→5，低权重探索
    s11 = min(s11, 5)
    score += s11
    breakdown['鲸鱼+微观'] = s11

    # ── 维度12(NEW)：期权 + 订单流CVD + OBI深度 ─────────────────
    # [D12校准 2026-05-19] 达摩院实测 7/11品种负贡献 → 降权噪音源
    s12 = 0
    # [外科手术 2026-05-30] 期权已删除（达摩院7/11负贡献）
    # 保留：订单流CVD + OBI深度 + 链上WS（有独立价值）
    # if extra_data and extra_data.get('options'):  # DELETED
    # 订单流CVD：弱信号给中性基础分而非归零
    if extra_data and extra_data.get('order_flow'):
        of = extra_data['order_flow']
        of_score = int(of.get('score', 0))
        if abs(of_score) >= 3:
            s12 += min(of_score, 5)
        elif of_score > 0:   # 弱正向信号：给1分中性
            s12 += 1
    # [修复] L2订单簿OBI方向确认（原在D13，此处共享加分）
    if extra_data and extra_data.get('orderbook'):
        ob = extra_data['orderbook']
        ob_obi = float(ob.get('obi', 0))
        if signal_dir in ('SHORT','做空'):
            if ob_obi < -0.3:   s12 += 4
            elif ob_obi < -0.1: s12 += 2
        else:
            if ob_obi > 0.3:    s12 += 4
            elif ob_obi > 0.1:  s12 += 2
    # 链上WS方向加分
    if extra_data and extra_data.get('onchain_ws'):
        s12 += min(abs(extra_data['onchain_ws'].get('direction_score', 0)), 3)
    s12 = min(s12, 10)  # [外科手术] 上限15→10（删期权后重校）
    score += s12
    breakdown['期权+订单流'] = s12

    # ── Phase A 维度13: L2订单簿 + 贝叶斯 + 宏观日历 ─────────────────
    # [D13校准 2026-05-19] 贝叶斯冷启动期保护 + OB score上限收紧
    s13 = 0
    # OB score: 上限 8→5（D12已用OBI，D13不重复大加分）
    if extra_data and extra_data.get('orderbook'):
        s13 += min(int(extra_data['orderbook'].get('score', 0)), 5)  # [D13校准] 8→5
    # 贝叶斯：冷启动(n<20笔)期间 score_adj 限制在 [-3,+3]，避免噪音
    if extra_data and extra_data.get('bayesian'):
        _bayes_adj = extra_data['bayesian'].get('score_adj', 0)
        _bayes_n   = extra_data['bayesian'].get('n_trades', extra_data['bayesian'].get('n', 0))
        if _bayes_n < 20:
            _bayes_adj = max(-3, min(_bayes_adj, 3))  # [D13校准] 冷启动限幅
        s13 += _bayes_adj
    if extra_data and extra_data.get('macro_calendar'):
        cal = extra_data['macro_calendar']
        if cal.get('active'):
            s13 += cal.get('penalty', 0)
    # [D13实训修复] BTC主导率宏观信号：主导率高→山寨弱，主导率低→山寨强
    if extra_data and extra_data.get('macro'):
        _mc = extra_data['macro']
        _raw = _mc.get('raw', {})
        _dom_raw = _raw.get('btc_dominance', {})
        # btc_dominance 可能是 dict{'btc_dom':58.15} 或 float
        if isinstance(_dom_raw, dict):
            _dom = float(_dom_raw.get('btc_dom', 0) or 0)
        else:
            _dom = float(_dom_raw or 0)
        if _dom > 0:
            if signal_dir in ('SHORT','做空'):
                # BTC主导率高(>58%)：资金集中BTC，altcoin做空更安全；做空ETH也OK
                if _dom > 62:   s13 += 3
                elif _dom > 58: s13 += 2
                elif _dom < 45: s13 -= 2  # 山寨季，做空ETH风险
            else:  # LONG
                # BTC主导率低(<45%)：山寨季，做多ETH更安全
                if _dom < 42:   s13 += 3
                elif _dom < 48: s13 += 1
                elif _dom > 62: s13 -= 2  # 资金集中BTC，altcoin多头弱
    s13 = max(-15, min(s13, 15))
    score += s13
    breakdown['L2+贝叶斯+宏观'] = s13

    # ── Phase B 维度14: XGBoost + 在线贝叶斯 + 滑点 + 链上WS ──────────
    s14 = 0
    # B1: XGBoost P(WIN) 评分（主流币保护：训练集主要为小币种时不惩罚主流币）
    if extra_data and extra_data.get('xgboost'):
        xgb_score = extra_data['xgboost'].get('score', 0)
        xgb_conf  = extra_data['xgboost'].get('confidence', 'LOW')
        # 主流币保护：MED/LOW置信时负分截断为0
        _major_coins = {'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT'}
        _sym = (ms.get('symbol') or '').upper()
        if xgb_score < 0 and xgb_conf in ('MED','LOW') and _sym in _major_coins:
            xgb_score = 0
        # [D14达摩院实训] HIGH置信但强负时衰减惩罚（样本仍主要来自达摩院模拟）
        # 真实实盘累积到50条后自动解除限制
        # [D14 v12.6] HIGH置信惩罚衰减：<50实盘截断-2，50-200实盘截断-4
        if xgb_score < -2 and xgb_conf == 'HIGH':
            from pathlib import Path as _Path
            import json as _json
            try:
                _real_n = sum(1 for _l in _Path('data/trade_records.jsonl').read_text().split('\n')
                              if _l.strip() and not _json.loads(_l).get('_is_simulation', False))
            except Exception:
                _real_n = 0
            if _real_n < 50:
                xgb_score = max(xgb_score, -2)
            elif _real_n < 200:
                xgb_score = max(xgb_score, -4)
        s14 += xgb_score
    # B1b: [v12.7a] 达摩院实盘代理激活器 (dharma_real_proxy)
    # 当 real_n<50 时用达摩院邻居WR做先验，替代 Bayes HIGH冻结问题
    try:
        import json as _pjson
        from pathlib import Path as _PP
        _proxy_f = _PP('data/real_proxy_buckets.json')
        if _proxy_f.exists():
            if not hasattr(analyze, '_proxy_cache') or analyze._proxy_cache is None:
                analyze._proxy_cache = _pjson.loads(_proxy_f.read_text())
            _pcache = analyze._proxy_cache
            _sym_p  = (ms.get('symbol') or '').upper()
            _dir_p  = 'S' if signal_dir in ('SHORT','做空') else 'L'
            # 优先取信号层传入的score；次选xgboost的score_norm；最后fallback为100（S1基准）
            _score_p = 0
            if extra_data and extra_data.get('xgboost'):
                _xn = extra_data['xgboost'].get('score_norm', 0)
                _score_p = int(_xn * 150) if _xn else 0
            if _score_p == 0:
                _score_p = 100  # 默认S1
            def _tier(sc):
                if sc>=135: return 'S3'
                if sc>=120: return 'S2'
                if sc>=100: return 'S1'
                return 'S0'
            # score此时还未出来，用当前已累积的中间分估算tier（或默认S1）
            # 大多数触发信号都是S1+，S1作为保守默认
            _tier_p = _tier(_score_p) if _score_p >= 80 else 'S1'
            _reg_p  = 'BULL' if 'BULL' in str(ms.get('regime','')).upper() else ('BEAR' if 'BEAR' in str(ms.get('regime','')).upper() else 'CHOP')
            from datetime import datetime as _dt, timezone as _tz
            _h = _dt.now(_tz.utc).hour
            _sess_p = 'ASIA' if _h < 8 else ('EU' if _h < 16 else 'US')
            # 4级模糊查找
            _pb = None
            for _k in [
                f"{_sym_p}:{_reg_p}:{_dir_p}:{_tier_p}:{_sess_p}",
                f"{_sym_p}:{_reg_p}:{_dir_p}:{_tier_p}:US",
                f"BTCUSDT:{_reg_p}:{_dir_p}:{_tier_p}:{_sess_p}",
                f"ETHUSDT:{_reg_p}:{_dir_p}:{_tier_p}:{_sess_p}",
            ]:
                if _k in _pcache:
                    _pb = _pcache[_k]; break
            if _pb and isinstance(_pb, dict):
                _pwr    = _pb.get('current_wr', 0.35)
                _real_n = _pb.get('real_n', 0)
                _pn     = _pb.get('proxy_n', 0)
                _trust  = 2.0 if _real_n >= 5 else (1.5 if _real_n >= 1 else 1.0)
                # [v12.7a] pn>=100时先验可信，无需real_n；pn<50不信任
                if _pn >= 100:
                    if _pwr >= 0.42:    # 高于基准(0.35)+安全边际
                        s14 += int(2 * _trust)
                    elif _pwr <= 0.28:  # 明显低胜率
                        s14 -= 2
                elif _pn >= 50:
                    if _pwr >= 0.45:
                        s14 += 1
                    elif _pwr <= 0.25:
                        s14 -= 1
                # 详细注入到extra供外部查看
                if extra_data is not None:
                    extra_data['proxy_bucket'] = {'key':_k,'wr':_pwr,'real_n':_real_n,'proxy_n':_pn}
    except Exception:
        pass  # proxy激活失败不影响主评分
    # B2: 在线贝叶斯多维后验 [P0-B upgrade 2026-06-17]
    # 设计院封印 2026-06-26: exp_n=0时降权至50%（无实训样本先验不可信）
    try:
        from online_bayes import score as _ob_score
        regime_label = str(ms.get('regime', '') or '')  # [P0-5修复 2026-07-16 苏摩111] regime_label未定义修复
        _ob_adj, _ob_detail = _ob_score(_sym, regime_label, signal_dir, score)
        _ob_n = _ob_detail.get('exp_n', 0)
        # 样本分级降权：n=0降50%，n<30降30%，n>=30全量
        if _ob_n == 0:
            _ob_adj = _ob_adj * 0.5
        elif _ob_n < 30:
            _ob_adj = _ob_adj * 0.7
        s14 += _ob_adj
        if extra_data is not None:
            extra_data['online_bayes'] = _ob_detail
        if abs(_ob_adj) >= 1.0:
            breakdown[f'OnlineBayes({_ob_detail["confidence"]})'] = f'{_ob_adj:+.1f}(prior={_ob_detail["prior_wr"]}%→post={_ob_detail["post_wr"]}%,n={_ob_n})'
    except Exception as _ob_e:
        pass  # online_bayes失败不影响主评分
    # B2备用：extra_data里已有则迟高优先级读取
    if extra_data and extra_data.get('online_bayes') and not any('OnlineBayes' in k for k in breakdown):
        s14 += extra_data['online_bayes'].get('score_adj', 0)
    # B3: 滑点惩罚
    if extra_data and extra_data.get('slippage'):
        s14 += extra_data['slippage'].get('score_adj', 0)
    # B4: 链上大单 WS 方向分
    if extra_data and extra_data.get('onchain_ws'):
        s14 += min(extra_data['onchain_ws'].get('direction_score', 0), 8)
    s14 = max(-15, min(s14, 20))
    score += s14
    breakdown['ML+在线贝叶斯+滑点'] = s14

    # ── Phase C 维度15: LSTM+NLP情绪 [DEAD_CODE 封印 2026-08-11] ──
    # [DEAD_CODE 封印 2026-08-11] LSTM/NLP(dharma_nlp_synthetic缺失) 依赖缺失，线上已归零，代码已清除
    s15 = 0
    # [DEAD_CODE 封印 2026-08-11] LSTM/NLP adj 依赖缺失，线上已归零，代码已清除
    s15_adj = 0
    breakdown['LSTM+NLP情绪'] = 0

    # ── 维度16(NEW)：量能衰竭 + 多周期背离共振 ─────────────────
    s16 = 0
    # A. 量能衰竭评分（底部/顶部识别）
    if extra_data and extra_data.get('vol_exhaustion'):
        _ve = extra_data['vol_exhaustion']
        _ve_score = _ve.get('score', 0)
        _ve_level = _ve.get('exhaustion_level', 'NONE')
        if _ve_level == 'EXTREME':
            s16 += min(_ve_score, 12)
        elif _ve_level == 'STRONG':
            s16 += min(_ve_score, 8)
        elif _ve_level == 'MILD':
            s16 += min(_ve_score, 5)
    # B. 多周期背离共振
    if extra_data and extra_data.get('multitf_div'):
        _md = extra_data['multitf_div']
        _md_res = _md.get('resonance', 'NONE')
        _md_score = _md.get('score', 0)
        if _md_res == 'TRIPLE':
            s16 += min(_md_score, 15)   # 三级共振：顶级底部信号
        elif _md_res == 'DOUBLE':
            s16 += min(_md_score, 10)
        elif _md_res == 'SINGLE':
            s16 += min(_md_score, 5)
    s16 = min(s16, 15)
    score += s16
    breakdown['量能衰竭+背离共振'] = s16

    # ── 维17：资金费率+多空比情绪评分 ────────────────────────────────
    s17 = 0
    try:
        import sys as _sys17, os as _os17
        _sys17.path.insert(0, _os17.path.dirname(_os17.path.abspath(__file__)))
        from sentiment_engine import get_sentiment_score as _get_sent
        _s17_val, _s17_det = _get_sent(ms, signal_dir)
        s17 = max(-8, min(8, _s17_val))
        score += s17
        breakdown['资金费情绪'] = s17
    except Exception:
        pass

    # ── 维18(NEW)：bull_bear多空辩论评分加权 ─────────────────────────
    s18 = 0
    try:
        import sys as _sys18, os as _os18
        _sys18.path.insert(0, _os18.path.join(_os18.path.dirname(_os18.path.abspath(__file__)), '..', 'scripts'))
        from calibration_engine import full_calibration_pipeline as _fcp
        _cal_score, _cal_rep, _bb = _fcp(symbol, signal_dir, score, regime=ms['regime'])
        # s18 = 校准后分差（限制-8~+8，非阻断）
        s18 = max(-8, min(8, round(_cal_score - score, 1)))
        score = _cal_score  # 直接更新score（包含校准调整）
        breakdown['bull_bear校准'] = s18
        if s18 != 0:
            print(f'[s18-校准] {symbol} {signal_dir} 调整{s18:+.1f}分 conviction={_cal_rep.get("conviction",0):.1f}')
    except Exception:
        pass  # 非阻断

    # ── 维19(NEW)：室内情绪 + 宏观因子(第17+18维度合并注入) ───────────
    s19 = 0
    try:
        import sys as _sys19, os as _os19
        _sys19.path.insert(0, _os19.path.dirname(_os19.path.abspath(__file__)))
        from news_event_guard import get_combined_guard_score
        _macro_dir  = extra_data.get('direction', '') if extra_data else ''
        _macro_dir  = _macro_dir or ('SHORT' if ms.get('signal_dir','SHORT')=='SHORT' else 'LONG')
        _macro_reg  = ms.get('regime', '')
        _macro_sym  = ms.get('symbol', 'BTC')
        _s19_cache_key = f's19_{_macro_sym}_{_macro_dir}_{_macro_reg}'
        _s19_cached = _bc_get(_s19_cache_key)
        if _s19_cached is not None:
            _s19_val, _s19_rep = _s19_cached
        else:
            _s19_val, _s19_rep = get_combined_guard_score(_macro_sym, _macro_dir, _macro_reg)
            _bc_set(_s19_cache_key, (_s19_val, _s19_rep), ttl=300.0)  # 5min TTL
        # 限制第19维度对总分的影响范围 -12 ~ +10
        s19 = max(-12, min(10, round(_s19_val, 1)))
        score += s19
        breakdown['宏观+事件'] = s19
        if extra_data is not None:
            extra_data['macro_report'] = _s19_rep
    except Exception as _e19:
        breakdown['宏观+事件_v2'] = 0  # 非阻断  # [P1-B audit-fix] 重复key加后缀


    # ═══════════════════════════════════════════════════════════
    # [s20] 布林带偏离度 [DEAD_CODE 封印 2026-08-11 bollinger_engine缺失]
    # [DEAD_CODE 封印 2026-08-11] bollinger_engine缺失 依赖缺失，线上已归零，代码已清除
    s20 = 0

    # [s21] RSI极值检测 [DEAD_CODE 封印 2026-08-11 rsi_extreme_engine缺失]
    # [DEAD_CODE 封印 2026-08-11] rsi_extreme_engine缺失 依赖缺失，线上已归零，代码已清除
    s21 = 0

    # [s22] 成交量比率（宽松量化新维度 2026-06-09）
    # ═══════════════════════════════════════════════════════════
    s22 = 0.0
    try:
        import sys as _sys22, os as _os22
        _sys22.path.insert(0, _os22.path.dirname(_os22.path.abspath(__file__)))
        from volume_ratio_engine import volume_ratio_score as _vr_score
        _k1h_vr = (extra_data or {}).get('_klines_1h', {})
        if isinstance(_k1h_vr, dict) and len(_k1h_vr.get('c',[])) >= 5:
            _c_vr = list(_k1h_vr.get('c', []))[-25:]
            _o_vr = list(_k1h_vr.get('o', []))[-25:]
            _v_vr = list(_k1h_vr.get('v', []))[-25:]
            s22, _vr_rep = _vr_score(_c_vr, _o_vr, _v_vr, signal_dir, ms.get('regime', ''))
            s22 = max(-5, min(8, s22))
            score += s22
            breakdown['成交量比率'] = s22
            if s22 != 0:
                print(f'[s22-VR] {symbol} {signal_dir} VR={_vr_rep.get("volume_ratio","?")}x {_vr_rep.get("signals",[])} +{s22:.1f}')
    except Exception as _e22:
        breakdown['成交量比率_v2'] = 0  # [P1-B audit-fix] 重复key加后缀


    # ── [s_research] 研究增强层注入（STAR.md L0：上限8分，TTL=30min，失败归零）
    # 来源优先级：timesfm_lite（当前首选）→ external_signal（备用）
    # 设计原则：<5ms，任何异常归零，不阻塞主评分
    s_research = 0
    try:
        # ── 首选：timesfm_lite（已修复接口）──────────────────
        import sys as _sys_res, os as _os_res
        _sys_res.path.insert(0, _os_res.path.dirname(_os_res.path.abspath(__file__)))
        from timesfm_lite import get_timesfm_score as _tfm_score
        _k1h_tfm = (extra_data or {}).get('_klines_1h', {}) or ms.get('klines_1h', {})
        _cov_tfm = {}
        if extra_data:
            _cov_tfm = {
                'funding_rate': extra_data.get('funding_rate', 0),
                'oi_change':    extra_data.get('oi_change_pct', 0),
                'rsi_1h':       extra_data.get('rsi_1h', 50),
            }
        if isinstance(_k1h_tfm, dict) and len(_k1h_tfm.get('c', [])) >= 30:
            _kl1h_list = [{'o':o,'h':h,'l':l,'c':c,'v':v}
                for o,h,l,c,v in zip(
                    _k1h_tfm.get('o',[]), _k1h_tfm.get('h',[]),
                    _k1h_tfm.get('l',[]), _k1h_tfm.get('c',[]),
                    _k1h_tfm.get('v',[]))]
            s_research, _tfm_rep = _tfm_score(
                symbol, signal_dir, _kl1h_list[-60:],
                ms.get('regime',''), covariates=_cov_tfm)
            # P1b: timesfm score<1.5时也保留（降低阈值从2.0至1.5）—设计院 2026-06-27
            if _tfm_rep.get('error'):
                raise ValueError(_tfm_rep['error'])
            # 增强：记录timesfm全量元数据供分析
            if extra_data is not None:
                extra_data['timesfm_meta'] = _tfm_rep
        else:
            raise ValueError('klines不足')
    except Exception:
        # ── 备用：external_signal缓存───────────────────────
        try:
            from brahma_brain.external_signal import get as _ext_get
            _res = _ext_get(symbol, signal_dir)
            s_research = int(_res.get('score', 0))
        except Exception:
            s_research = 0

    try:
        # CHOP 体制：研究信号强制归零（STAR.md L2）
        if 'CHOP' in str(ms.get('regime', '')).upper():
            s_research = 0
        # 死穴方向：研究信号强制归零（STAR.md L1）
        _rblock = str(ms.get('regime', '')).upper()
        _dead_zones = {('BEAR_TREND','LONG'),('BULL_TREND','SHORT'),
                       ('BEAR_RECOVERY','SHORT'),('BULL_CORRECTION','LONG')}
        if (_rblock, signal_dir) in _dead_zones:
            s_research = 0
        s_research = max(-8, min(8, s_research))
        if s_research != 0:
            score += s_research
            breakdown['研究增强层'] = f'{s_research:+d} (timesfm_lite)'
        else:
            breakdown['研究增强层'] = '0 (timesfm_no_signal)'
    except Exception as _e_res:
        breakdown['研究增强层'] = f'0 (exception:{str(_e_res)[:40]})'

    # RL 仓位乘数注入 extra（供 analyze() 汇总层使用）
    if extra_data and extra_data.get('rl_position'):
        extra_data['_rl_kelly_mult'] = extra_data['rl_position'].get('kelly_mult', 1.0)


    # ═══════════════════════════════════════════════════════════
    # [UP-SRG v5.0] 体制×方向智能乘数
    # ═══════════════════════════════════════════════════════════

    return {
        's11': s11, 's12': s12, 's13': s13, 's14': s14, 's15': s15, 's15_adj': s15_adj, 's16': s16, 's17': s17, 's18': s18, 's19': s19, 's20': s20, 's21': s21, 's22': s22, 's_research': s_research,
        'score': score, 'breakdown': breakdown,
    }

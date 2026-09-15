#!/usr/bin/env python3
# ponytail: brahma_core_block_c 458行，核心计算，94维共享_result状态，拆分条件: 状态隔离方案成熟后
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
    # [达摩院v6.0 2026-09-09 苏摩111] s11 鲸鱼+微观 IC=-0.0131 → 降为信息层
    breakdown['鲸鱼+微观'] = s11  # 信息层展示，不计分

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
    # [达摩院v6.0 2026-09-09 苏摩111] s12 期权+订单流 IC=-0.0131 → 降为信息层
    breakdown['期权+订单流'] = s12  # 信息层展示，不计分

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
    # [P2 归零 2026-09-13] XGBoost+Bayes+滑点 归零，跳过计算省CPU
    s14 = 0
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
    # [达摩院v6.0 2026-09-09 苏摩111] s16 量能衰竭 IC=-0.0064 → 降为信息层
    breakdown['量能衰竭+背离共振'] = s16  # 信息层展示，不计分

    # ── 维17：资金费率+多空比情绪评分 ────────────────────────────────
    # [P2 归零 2026-09-13] sentiment_engine 归零，跳过计算省CPU
    s17 = 0
    breakdown['资金费情绪'] = s17

    # ── 维18(NEW)：bull_bear多空辩论评分加权 ─────────────────────────
    # [P2 归零 2026-09-13] bull_bear校准 归零，跳过计算省CPU
    s18 = 0
    breakdown['bull_bear校准'] = s18

    # ── 维19(NEW)：室内情绪 + 宏观因子(第17+18维度合并注入) ───────────
    # [P2 归零 2026-09-13] 室内情绪+宏观因子 归零，跳过计算省CPU
    s19 = 0
    breakdown['宏观+事件'] = s19


    # [s20] 布林带偏离度 [DEAD_CODE 封印 2026-08-11 bollinger_engine缺失]
    # [DEAD_CODE 封印 2026-08-11] bollinger_engine缺失 依赖缺失，线上已归零，代码已清除
    s20 = 0

    # [s21] RSI极值检测 [DEAD_CODE 封印 2026-08-11 rsi_extreme_engine缺失]
    # [DEAD_CODE 封印 2026-08-11] rsi_extreme_engine缺失 依赖缺失，线上已归零，代码已清除
    s21 = 0

    # [s22] 成交量比率（宽松量化新维度 2026-06-09）
    s22 = 0.0
    try:
        import sys as _sys22, os as _os22
        _sys22.path.insert(0, _os22.path.dirname(_os22.path.abspath(__file__)))
# 模块不存在，已注释
# from volume_ratio_engine import volume_ratio_score as _vr_score
        _k1h_vr = (extra_data or {}).get('_klines_1h', {})
        if isinstance(_k1h_vr, dict) and len(_k1h_vr.get('c',[])) >= 5:
            _c_vr = list(_k1h_vr.get('c', []))[-25:]
            _o_vr = list(_k1h_vr.get('o', []))[-25:]
            _v_vr = list(_k1h_vr.get('v', []))[-25:]
            s22, _vr_rep = _vr_score(_c_vr, _o_vr, _v_vr, signal_dir, ms.get('regime', ''))
            s22 = max(-5, min(8, s22))
            # [达摩院v6.0 2026-09-09 苏摩111] s22 成交量比率 IC=-0.0064 → 降为信息层
            breakdown['成交量比率'] = s22  # 信息层展示，不计分
            if s22 != 0:
                print(f'[s22-VR] {symbol} {signal_dir} VR={_vr_rep.get("volume_ratio","?")}x {_vr_rep.get("signals",[])} +{s22:.1f}')
    except Exception as _e22:
        breakdown['成交量比率_v2'] = 0  # [P1-B audit-fix] 重复key加后缀


    # ── [s_research] 研究增强层注入（STAR.md L0：上限8分，TTL=30min，失败归零）
    # [P2 归零 2026-09-13] TimesFM研究增强层 归零，跳过计算省CPU
    s_research = 0
    breakdown['研究增强层'] = '0 (P2归零)'

    # RL 仓位乘数注入 extra（供 analyze() 汇总层使用）
    if extra_data and extra_data.get('rl_position'):
        extra_data['_rl_kelly_mult'] = extra_data['rl_position'].get('kelly_mult', 1.0)


    # [UP-SRG v5.0] 体制×方向智能乘数

    return {
        's11': s11, 's12': s12, 's13': s13, 's14': s14, 's15': s15, 's15_adj': s15_adj, 's16': s16, 's17': s17, 's18': s18, 's19': s19, 's20': s20, 's21': s21, 's22': s22, 's_research': s_research,
        'score': score, 'breakdown': breakdown,
    }

"""
brahma_brain.py · 梵天分析大脑主入口  VERSION = v3.0
brahma_brain · Phase 1 完整整合

调用流程：
  1. market_state.py  → 多框架趋势 + 体制 + 关键位
  2. smc_engine.py    → BOS/CHoCH/OB/FVG/流动性
  3. confluence_score → 150分共振评分
  4. 输出精确交易参数 + 钉钉1格式文本
"""

# ⚠️ 开源版 | Pro版权重通过 factor_weights.yaml 注入
_OSS_MODE = True  # Pro版设为False以启用训练权重


import os, sys, time
import copy  # [P1-C audit-fix] deepcopy for cf dict
import json  # [D1-fix] 提升到顶部
from datetime import datetime, timezone, timedelta  # [D1-fix] 提升到顶部
from pathlib import Path  # [D1-fix] 提升到顶部

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

from data_cache        import prefetch_symbol, get_klines, klines_to_ohlcv
from market_state      import analyze   as ms_analyze
from smc_engine        import analyze_smc
from divergence_engine import divergence_score
from volume_engine     import volume_score
from range_engine      import range_score  # [Phase2a] 区间结构引擎
try:
    from math_utils import ema as _mu_ema, rsi as _mu_rsi, atr as _mu_atr  # [设计院 2026-06-30 全量接入] 统一数学库
    _MATH_UTILS_OK = True
except Exception:
    _MATH_UTILS_OK = False
from options_engine    import sentiment_score, analyze_funding_trend
# [CLEANED 2026-06-11] from elliott_engine    import analyze_elliott, format_elliott
try:
    from onchain_engine import onchain_score as _onchain_score
    _ONCHAIN_OK = True
except Exception:
    _ONCHAIN_OK = False
try:
    from pattern_engine import pattern_score as _pattern_score
    _PATTERN_OK = True
except Exception:
    _PATTERN_OK = False
try:
    from order_flow_engine import order_flow_score as _order_flow_score
    _OF_OK = True
except Exception:
    _OF_OK = False
try:
    from macro_engine import macro_score as _macro_score
    _MACRO_OK = True
except Exception:
    _MACRO_OK = False
# [CLEANED 2026-06-11] harmonic_engine removed — permanently disabled
_HARMONIC_OK = False
try:
    from volume_exhaustion_engine import volume_exhaustion_score as _vol_exh_score
    _VOL_EXH_OK = True
except Exception:
    _VOL_EXH_OK = False
try:
    from divergence_engine import multitf_divergence_score as _multitf_div_score
    _MULTITF_DIV_OK = True
except Exception:
    _MULTITF_DIV_OK = False
try:
    from multitf_engine import multitf_score as _multitf_score
    _MULTITF_OK = True
except Exception:
    _MULTITF_OK = False
try:
    from enhanced_signal_engine import enhanced_score as _enhanced_score
    _ENHANCED_OK = True
except Exception:
    _ENHANCED_OK = False
try:
    from whale_engine import whale_score as _whale_score
    _WHALE_OK = True
except Exception:
    _WHALE_OK = False
try:
    from cross_market_engine import cross_market_score as _cross_market_score
    _CROSS_OK = True
except Exception:
    _CROSS_OK = False
try:
    from microstructure_engine import microstructure_score as _micro_score
    _MICRO_OK = True
except Exception:
    _MICRO_OK = False

# [架构拆分 2026-07-01] 入场参数计算已移至 brahma_core_entry
try:
    from brahma_brain.brahma_core_entry import (
        calc_trade_params as _ctp_entry,
        rebase_params as _rbp_entry,
    )
    _ENTRY_OK = True
except Exception:
    _ENTRY_OK = False

# ═══════════════════════════════════════════════════════════════
# 150分共振评分器（Phase 1 内置版）
# ═══════════════════════════════════════════════════════════════

def confluence_score(ms: dict, smc: dict, signal_dir: str,
                     extra_data: dict = None) -> dict:
    """
    150分共振评分引擎
    基于 skills/ta-engine/references/analysis_engine.md
    """
    score = 0
    breakdown = {}

    # [UP-TRAIN10K] T01 Bootstrap置信等级表（达摩院1万次训练验证）
    # 信号质量排名: 量价配合(B,PF=1.277) > MACD金叉(B) > EMA趋势(C) > MACD零轴(C)
    # WR主导信号: MACD背离(A,52.8%) | RSI(A,53.3%) | 布林带(A,53.1%)
    # PF主导信号: 量价配合(B,1.277) — 蒙特卡洛10000次选出的核心信号
    # 实盘层使用: breakdown中记录Bootstrap置信等级供analyze()参考
    _boot_grades = {
        '量价配合': 'B',      # PF=1.277 CI=[1.25,1.31] ← 最可靠
        'MACD金叉死叉': 'B',  # PF=1.046 CI=[1.02,1.08]
        'EMA趋势顺势': 'C',   # PF=1.121 CI=[1.07,1.18]
        'MACD零轴位置': 'C',  # PF=1.096 CI=[1.05,1.15]
        'MACD背离': 'A',      # WR=52.8% CI=[52.1%,53.5%] (WR优先)
        'RSI超卖超买': 'A',   # WR=53.3% (WR优先)
        '布林带反弹': 'A',    # WR=53.1% (WR优先)
    }
    breakdown['_T01_boot_ref'] = 'QP(B)>EMA(C)>ML(C)>MACD_div(A-WR)'


    # ╔══════════════════════════════════════════════════════════╗
    # ║ BLOCK-A: 技术分析层 (维度1-5) · 纯技术，无网络依赖      ║
    # ║ 未来提取目标: brahma_brain/confluence/tech_analysis.py  ║
    # ╚══════════════════════════════════════════════════════════╝
    # ── 维度1：趋势一致性（0~20）──────────────────────────────
    # [D01达摩院实训] CHOP体制下逆向0分→给中性基础分，避免LONG/SHORT天壤之别
    consensus = ms['trend']['consensus']['consensus']
    adx_1h = ms['trend']['1h']['adx']
    _regime_str = ms.get('regime', '')
    _is_chop = 'CHOP' in str(_regime_str).upper()
    s1 = 0
    if signal_dir == 'LONG':
        if consensus == 'FULL_BULL':   s1 = 20
        elif consensus == 'LEAN_BULL': s1 = 15
        elif consensus == 'MIXED_BULL':s1 = 10
        elif consensus == 'NEUTRAL':    s1 = 6   # [v12.6] 5->6
        elif consensus == 'MIXED_BEAR': s1 = 4 if _is_chop else 2  # [v12.6] CHOP逆向4分
        elif _is_chop:                  s1 = 4   # [v12.6] 3->4
        else:                           s1 = 0
    else:
        if consensus == 'FULL_BEAR':    s1 = 20
        elif consensus == 'LEAN_BEAR':  s1 = 15
        elif consensus == 'MIXED_BEAR': s1 = 10
        elif consensus == 'NEUTRAL':    s1 = 6   # [v12.6] 5->6
        elif consensus == 'MIXED_BULL': s1 = 4 if _is_chop else 2  # [v12.6] CHOP逆向4分
        elif _is_chop:                  s1 = 4   # [v12.6] 3->4
        else:                           s1 = 0
    if adx_1h > 30: s1 = min(s1 + 3, 20)
    score += s1
    breakdown['趋势一致性'] = s1

    # ── 维度2：关键位精确度（0~20）─────────────────────────────
    price = ms['price']
    fib   = ms['key_levels']['fib']
    s2    = 0
    for fib_key, fib_val in [('0.618',5),('0.786',4),('0.382',3),('0.500',3)]:
        if fib_key in fib:
            dist = abs(price - fib[fib_key]) / price
            if dist < 0.005:   s2 += fib_val
            elif dist < 0.015: s2 += max(fib_val - 2, 0)
    # [OB新鲜度分层 2026-07-01] 四方共识落地：OB age决定权重乘数
    # 新鲜OB（首次回测）= 满分 / 老化OB = 降权 / 已被破坏 = 0分
    # 铁证：broken OB得分虚高是score虚高根因之一
    def _ob_freshness_mult(ob_data: dict) -> float:
        """根据OB的age（K线数）返回新鲜度乘数 0.0~1.0"""
        if not ob_data:
            return 1.0
        age = ob_data.get('age_bars', 0)  # smc_engine提供的age字段
        broken = ob_data.get('broken', False)
        if broken:
            return 0.0   # 已被破坏 → 0分
        if age <= 3:
            return 1.0   # 新鲜OB，首次回测 → 满分
        elif age <= 6:
            return 0.75  # 次新鲜
        elif age <= 10:
            return 0.50  # 老化
        else:
            return 0.30  # 接近失效

    ob = smc['order_blocks']
    if signal_dir == 'LONG' and ob.get('nearest_bull_ob'):
        d = abs(ob['nearest_bull_ob']['dist_pct'])
        _raw = 5 if d < 0.5 else (3 if d < 1.5 else (1 if d < 3.0 else 0))  # [P1-A] 3%内也有少量得分
        _mult = _ob_freshness_mult(ob['nearest_bull_ob'])
        s2 += int(_raw * _mult)
        if _mult < 1.0:
            breakdown['OB新鲜度_1H_LONG'] = f'age乘数={_mult:.2f} 原始={_raw} 实得={int(_raw*_mult)}'
    if signal_dir == 'SHORT' and ob.get('nearest_bear_ob'):
        d = abs(ob['nearest_bear_ob']['dist_pct'])
        _raw = 5 if d < 0.5 else (3 if d < 1.5 else (1 if d < 3.0 else 0))
        _mult = _ob_freshness_mult(ob['nearest_bear_ob'])
        s2 += int(_raw * _mult)
        if _mult < 1.0:
            breakdown['OB新鲜度_1H_SHORT'] = f'age乘数={_mult:.2f} 原始={_raw} 实得={int(_raw*_mult)}'
    # [P1-A upgrade 2026-06-17] 4H OB 双层确认奖励（MTF共振）+ 新鲜度乘数
    ob_4h = smc.get('order_blocks_4h', {})
    if signal_dir == 'LONG' and ob_4h.get('nearest_bull_ob'):
        d4 = abs(ob_4h['nearest_bull_ob'].get('dist_pct', 99))
        _raw4 = 3 if d4 < 1.5 else (1 if d4 < 3.0 else 0)  # 1H+4H OB重叠
        _mult4 = _ob_freshness_mult(ob_4h['nearest_bull_ob'])
        s2 += int(_raw4 * _mult4)
    if signal_dir == 'SHORT' and ob_4h.get('nearest_bear_ob'):
        d4 = abs(ob_4h['nearest_bear_ob'].get('dist_pct', 99))
        _raw4 = 3 if d4 < 1.5 else (1 if d4 < 3.0 else 0)
        _mult4 = _ob_freshness_mult(ob_4h['nearest_bear_ob'])
        s2 += int(_raw4 * _mult4)
    # FVG | 公平价值缺口
    fvg = smc['fvg']
    if signal_dir == 'LONG' and fvg.get('nearest_bull'):
        d = abs(fvg['nearest_bull']['mid'] - price) / price * 100
        s2 += 4 if d < 0.5 else (2 if d < 1.5 else 0)
    if signal_dir == 'SHORT' and fvg.get('nearest_bear'):
        d = abs(fvg['nearest_bear']['mid'] - price) / price * 100
        s2 += 4 if d < 0.5 else (2 if d < 1.5 else 0)
    s2 = min(s2, 20)
    score += s2
    breakdown['关键位精确度'] = s2

    # ── 维度3：动量背离确认（0~20）─────────────────────────────
    # [达摩院v12.9c] RSI彻底改为状态描述，不参与评分
    # 统计：PF=0.683 p=0.756，RSI单独入场指标无效（517样本验证）
    # RSI仅在输出报告中提示超买/超卖区间，评分固定0分
    mom  = ms['momentum']
    rsi1 = mom['rsi_1h']
    rsi4 = mom['rsi_4h']
    rsid = mom['rsi_1d']
    # RSI状态描述（不评分，仅供报告使用）
    def _rsi_state(v):
        if v >= 70: return f'超买({v:.0f})'
        if v <= 30: return f'超卖({v:.0f})'
        return f'中性({v:.0f})'
    breakdown['RSI状态描述'] = f'1H:{_rsi_state(rsi1)} 4H:{_rsi_state(rsi4)} 1D:{_rsi_state(rsid)}'
    # [v25.2 2026-06-16 P1] RSI极端区加分（设计院铁证：RSI<35 WR=62.8~67.8% n=768）
    # 离线回放验证：RSI 0~25 WR=67.8% avg=+0.416% / RSI 25~35 WR=62.8% avg=+0.224%
    # 注：仅在极端超卖/超买区加分，中性区(45~65)不加分
    s3_rsi = 0
    if signal_dir == 'LONG' and rsi1 <= 25:
        s3_rsi = 4   # 深度超卖，WR=67.8%铁证
        breakdown['RSI极端加分'] = f'+4 (深度超卖RSI1H={rsi1:.0f}≤25, 离线WR=67.8%)'
    elif signal_dir == 'LONG' and rsi1 <= 35:
        s3_rsi = 3   # 超卖区，WR=62.8%铁证
        breakdown['RSI极端加分_v2'] = f'+3 (超卖RSI1H={rsi1:.0f}≤35, 离线WR=62.8%)'  # [P1-B audit-fix] 重复key加后缀
    elif signal_dir == 'SHORT' and rsi1 >= 75:
        s3_rsi = 3   # 超买区（对称逻辑）
        breakdown['RSI极端加分_v3'] = f'+3 (超买RSI1H={rsi1:.0f}≥75, 对称逻辑)'  # [P1-B audit-fix] 重复key加后缀

    # Phase 2：背离检测引擎加分
    s3_div = 0
    if extra_data:
        div_res = extra_data.get('divergence')
        if div_res:
            # FIX: 优先用新字段score，兼容旧字段
            if 'score' in div_res:
                s3_div = div_res.get('score', 0)
            elif signal_dir == 'LONG':
                s3_div = div_res.get('score_long', 0)
            else:
                s3_div = div_res.get('score_short', 0)
            s3_div = min(s3_div, 18)  # [Phase2a] 背离权重 12→18，实测WR=74%铁证(n=29K)
        # [D03实训修复] CVD方向加分：主动买卖方向与信号一致额外+2
        enh_res = extra_data.get('enhanced')
        if enh_res:
            _cvd = enh_res.get('breakdown', {}).get('cvd', 0)
            if _cvd > 0:  # CVD与信号方向一致（enhanced已按方向处理）
                s3_div = min(s3_div + 2, 20)  # [Phase2a] CVD共振上限提升

    # [UP-TRAIN10K] T04体制矩阵实训: CHOP体制MACD背离PF=1.628最强
    # 达摩院1万次训练验证: CHOP+MACD背离是全体制最优组合
    _chop_macd_bonus = 0
    if 'CHOP' in str(ms.get('regime', '')).upper() and s3_div >= 6:
        _chop_macd_bonus = 4  # CHOP体制背离信号额外+4分
        breakdown['CHOP背离奖励'] = f'+4 (CHOP+背离T04验证PF=1.628)'
    s3 = min(s3_rsi + s3_div + _chop_macd_bonus, 22)  # [Phase2a] 背离维度上限提升到22
    score += s3
    breakdown['动量背离'] = s3

    # ── 维度4：SMC结构支持（0~20）──────────────────────────────
    s4 = smc['score']['score']
    score += s4
    breakdown['SMC结构'] = s4

    # ── 维度5：量能验证（0~20）─────────────────────────────────
    bb  = mom['bb']
    s5  = 0
    if signal_dir == 'LONG':
        if bb.get('pos', 0.5) < 0.2:   s5 += 6
        elif bb.get('pos', 0.5) < 0.3: s5 += 3
    else:
        if bb.get('pos', 0.5) > 0.8:   s5 += 6
        elif bb.get('pos', 0.5) > 0.7: s5 += 3
    if bb.get('width', 0) < 0.04: s5 += 4
    atr_pct = mom['atr_pct']
    if 0.3 < atr_pct < 1.5: s5 += 3
    lsr = ms['sentiment']['long_short_ratio']
    if signal_dir == 'LONG' and lsr < 35:  s5 += 4
    if signal_dir == 'SHORT' and lsr > 70: s5 += 4

    # [UP-TRAIN10K] QEW质量环境权重: 趋势期量能更可靠(T02:量价配合100%权重)
    # 达摩院1万次训练: QEW趋势期×1.15, CHOP期×0.85（系统回测ETH+0.013 SOL+0.051）
    _qew_regime = str(ms.get('regime', '')).upper()
    _qew_mult = 1.15 if any(x in _qew_regime for x in ['BULL_TREND','BULL_PEAK','BEAR_TREND','BEAR_CRASH'])                 else (0.85 if 'CHOP' in _qew_regime else 1.0)
    if _qew_mult != 1.0:
        breakdown['QEW权重'] = f'×{_qew_mult} ({_qew_regime[:12]})'

    # [达摩院v12.9c] OBV方向验证加分（OBV命中率61.5%，517样本）
    # [FIX-OBV 2026-05-27] 改用4H 20根 + 累积OBV趋势（原1H 5根过于短视）
    # 逻辑：近20根4H OBV累积值反映机构方向，比1H短期噪音更稳定
    try:
        _4h_c = extra_data.get('_k4h_closes', []) if extra_data else []
        _4h_v = extra_data.get('_k4h_volumes', []) if extra_data else []
        # 回退到1H数据
        if len(_4h_c) < 6:
            _4h_c = ms.get('raw_closes', [])
            _4h_v = ms.get('raw_volumes', [])
        if _4h_c and _4h_v and len(_4h_c) >= 6:
            _n = min(20, len(_4h_c))
            _obv = 0
            _obv_mid = 0  # 前半段OBV（趋势方向判断）
            for _i in range(len(_4h_c) - _n, len(_4h_c)):
                _delta = _4h_c[_i] - _4h_c[_i-1] if _i > 0 else 0
                _wt = abs(_delta) / (_4h_c[_i] + 1e-9) * _4h_v[_i]  # 价格变化加权
                if _4h_c[_i] > _4h_c[_i-1]:   _obv += _wt
                elif _4h_c[_i] < _4h_c[_i-1]: _obv -= _wt
                if _i < len(_4h_c) - _n // 2:  _obv_mid += (_wt if _4h_c[_i] > _4h_c[_i-1] else -_wt if _4h_c[_i] < _4h_c[_i-1] else 0)
            _obv_bullish = _obv > 0
            # 趋势加速：OBV绝对值增大且方向一致 → 额外+2
            _obv_accelerating = (_obv > 0 and _obv > _obv_mid * 1.1) or (_obv < 0 and _obv < _obv_mid * 1.1)
            if (signal_dir == 'LONG' and _obv_bullish) or \
               (signal_dir == 'SHORT' and not _obv_bullish):
                _obv_add = 6 if _obv_accelerating else 4
                s5 += _obv_add
                breakdown['OBV方向'] = f'✅ 与{signal_dir}同向 +{_obv_add}{" (加速)" if _obv_accelerating else ""}'
            else:
                breakdown['OBV方向_v2'] = f'⚠️ OBV反向(0分)'  # [P1-B audit-fix] 重复key加后缀
        else:
            breakdown['OBV方向_v3'] = 'N/A(无原始数据)'  # [P1-B audit-fix] 重复key加后缀
    except Exception as _obv_e:
        breakdown['OBV方向_v4'] = f'N/A({str(_obv_e)[:30]})'  # [P1-B audit-fix] 重复key加后缀

    # Phase 2：量能引擎加分
    # [达摩院V7校准 2026-05-19] whale_signal PF=1.404 全系统最强，异动信号 PF=1.212
    # 新增：鲸鱼大单加分+6，异动信号加分+4（原来限制日线最强 PF=2.74）
    if extra_data:
        vol_res = extra_data.get('volume')
        if vol_res:
            s5 += min(vol_res.get('score', 0), 12)  # [Phase2a] 量能权重 6→12，实测WR=73.2%铁证(n=37K)
        # whale 大单特征加分（直接处理量能引擎输出）
        whale_res = extra_data.get('whale')
        if whale_res:
            whale_dir = whale_res.get('direction', '')  # 'BUY' 或 'SELL'
            if (signal_dir == 'LONG' and whale_dir == 'BUY') or \
               (signal_dir == 'SHORT' and whale_dir == 'SELL'):
                s5 += min(whale_res.get('score', 0) * 8 // 15, 8)  # [Phase2a] whale上限+8
        # 异动信号加分（量價齐发）
        enh_res = extra_data.get('enhanced')
        if enh_res and enh_res.get('vol_spike'):
            s5 += 4  # 异动：2sigma量價齐发 +4

    # 应用QEW乘数（体制质量权重，T02训练结论）
    s5 = min(int(s5 * _qew_mult), 20)
    score += s5
    breakdown['量能验证'] = s5

    # ── [Phase2a] 维度5b：区间结构（0~15）────────────────────────────────
    # 数据铁证：区间高位做空 WR=71.6%, n=183K, 6年稳定
    s5b = 0
    try:
        _k = extra_data.get('_klines_1h') if extra_data else None
        if _k and len(_k.get('c', [])) >= 20:
            _rng = range_score(_k['h'], _k['l'], _k['c'], signal_dir)
            s5b = _rng.get('score', 0)
            if s5b > 0:
                breakdown['区间结构'] = s5b
                breakdown['区间Zone'] = _rng.get('zone', '')

            # ── [设计院 2026-06-30 P1-B] detect_range_structure 路由接入 ──────
            # 根因：range_score 只做评分，detect_range_structure 从未被路由到决策
            # 修复：识别区间状态后，对 DISCOUNT 做多 / PREMIUM 做空 补加分
            # fail-safe：异常静默，不阻断主流程
            try:
                from range_engine import detect_range_structure as _drs
                _rs = _drs(_k['h'], _k['l'], _k['c'], lookback=48)
                if _rs.get('is_range'):
                    _zone = _rs.get('zone', 'MIDDLE')
                    _qual = _rs.get('quality', 'LOW')
                    _q_mult = {'HIGH': 1.5, 'MEDIUM': 1.0, 'LOW': 0.7}.get(_qual, 1.0)
                    _rng_add = 0
                    if signal_dir == 'LONG' and _zone == 'DISCOUNT':
                        _rng_add = int(10 * _q_mult)   # 最高+15
                        breakdown['区间底部做多'] = _rng_add
                    elif signal_dir == 'SHORT' and _zone == 'PREMIUM':
                        _rng_add = int(10 * _q_mult)   # 最高+15
                        breakdown['区间顶部做空'] = _rng_add
                    elif _zone == 'MIDDLE':
                        _rng_add = -5
                        breakdown['区间中部惩罚'] = -5
                    if _rng_add != 0:
                        s5b += _rng_add
                        breakdown['区间Zone_v2'] = f'{_zone}({_qual}) {_rng_add:+d}'
            except Exception:
                pass
            # ── [P1-B END] ────────────────────────────────────────────────────

        elif extra_data:  # fallback: 用 bb 计算层已有的 k1h
            pass
    except Exception:
        pass
    score += s5b

    wave  = ms['wave']
    regime = ms['regime']
    s6 = 0
    if signal_dir == 'LONG':
        if wave.get('wave') in ('C_WAVE_END', '4W_OR_2W'): s6 += 8
        if regime in ('CHOP_LOW', 'BEAR_RECOVERY'):          s6 += 5
        if wave.get('bias') == 'LONG':                       s6 += 4
    else:  # SHORT
        if wave.get('wave') in ('5W_TOP', 'B_WAVE'):         s6 += 8
        if regime in ('CHOP_HIGH', 'BULL_PEAK', 'BEAR_RECOVERY'): s6 += 5
        if wave.get('bias') == 'SHORT':                      s6 += 4
        # [D06实训修复] 做空时CORRECTION_ABC也是有利位置（浪B顶部做空）
        if wave.get('wave') in ('CORRECTION_ABC', 'B_WAVE_TOP'): s6 = max(s6, 8)

    # Phase 3: Elliott 精确波浪引擎覆盖
    if extra_data and extra_data.get('elliott'):
        ew = extra_data['elliott']
        s6_ew = ew.get('score', 0)  # 0~15
        # 最佳浪位额外奖励
        if ew.get('wave_pos') in ('WAVE2_COMPLETED', 'CORRECTION_C_WAVE'):
            s6_ew = min(s6_ew + 5, 20)
        if ew.get('confidence', 0) >= 70:
            s6_ew = min(s6_ew + 3, 20)
        s6 = max(s6, s6_ew)

    # P2a：pattern_engine 形态门派覆盖
    if extra_data and extra_data.get('pattern'):
        pt = extra_data['pattern']
        s6_pat = pt.get('score', 0)  # 0~15
        s6 = max(s6, s6_pat)

    s6 = min(s6, 20)
    score += s6
    breakdown['形态成熟度'] = s6

    _sym = (ms.get('symbol') or '').upper()  # [s7局部_sym 2026-07-01]
    _sym_price = extra_data.get('price', 0) if extra_data else 0  # s7局部
    # ── 维度7：清算带/OI（0~10）────────────────────────────────
    oi  = ms['sentiment']['oi']
    s7  = 0
    # P0修复 2026-05-21: 移除「OI有数据就+3」的错误逻辑
    # [P2 2026-05-22] OI动量替代：方向一致的OI变化才加分
    oi_chg = ms['sentiment'].get('oi_change_pct', 0.0)
    oi_mom = ms['sentiment'].get('oi_momentum', 'NEUTRAL')
    # OI增加+价格顺势 = 确认信号；OI增加+价格逆势 = 警告
    if signal_dir == 'LONG' and oi_mom == 'INCREASING' and oi_chg > 1.0:
        s7 += 4   # 多头持仓增加，资金流入确认
    elif signal_dir == 'SHORT' and oi_mom == 'DECREASING' and oi_chg < -1.0:
        s7 += 4   # 持仓减少，多头出逃确认空头
    elif oi_mom == 'NEUTRAL':
        s7 += 1   # 中性，轻微加分
    # 流动性猎杀方向
    liq = smc['liquidity']
    if signal_dir == 'LONG' and liq.get('nearest_below'):
        d = liq['nearest_below']['dist_pct']
        if d < 0.3: s7 += 7
        elif d < 1.0: s7 += 3
    if signal_dir == 'SHORT' and liq.get('nearest_above'):
        d = liq['nearest_above']['dist_pct']
        if d < 0.3: s7 += 7
        elif d < 1.0: s7 += 3
    # P2c: 订单流额外加分
    if extra_data and extra_data.get('order_flow'):
        of_bonus = min(extra_data['order_flow'].get('score', 0) // 3, 5)
        s7 = min(s7 + of_bonus, 15)
    else:
        s7 = min(s7, 10)

    # ── s7 实时清算流密度接入（星枢引擎 2026-06-09）────────────
    # ws_guardian !forceOrder@arr → 近1H真实爆仓量
    try:
        import sys as _s7sys, os as _s7os
        _s7sys.path.insert(0, _s7os.path.dirname(_s7os.path.dirname(__file__)))
        from ws_guardian import get_liq_stats
        _live = get_liq_stats(_sym)
        if _live.get('available') and _live.get('events', 0) > 0:
            from brahma_brain.s7_liq_config import (
                get_liq_bonus, LIQ_DIRECTION_RATIO,
                LIQ_CHAOS_THRESHOLD, LIQ_CHAOS_PENALTY
            )
            _long_usd  = _live['long_usd_1h']
            _short_usd = _live['short_usd_1h']
            _total_usd = _long_usd + _short_usd
            if _total_usd > 0:
                if signal_dir == 'SHORT' and _long_usd > _short_usd * LIQ_DIRECTION_RATIO:
                    _liq_bonus, _liq_level = get_liq_bonus(_long_usd)
                    s7 = min(s7 + _liq_bonus, 15)
                    if _liq_bonus > 0:
                        print(f'[s7-LiveLiq] SHORT确认: 多头爆仓${_long_usd/1e6:.1f}M [{_liq_level}] +{_liq_bonus}')
                elif signal_dir == 'LONG' and _short_usd > _long_usd * LIQ_DIRECTION_RATIO:
                    _liq_bonus, _liq_level = get_liq_bonus(_short_usd)
                    s7 = min(s7 + _liq_bonus, 15)
                    if _liq_bonus > 0:
                        print(f'[s7-LiveLiq] LONG确认: 空头爆仓${_short_usd/1e6:.1f}M [{_liq_level}] +{_liq_bonus}')
                elif _total_usd > LIQ_CHAOS_THRESHOLD:
                    s7 = max(s7 + int(LIQ_CHAOS_PENALTY), 0)
                    print(f'[s7-LiveLiq] 双向极端爆仓${_total_usd/1e6:.1f}M {int(LIQ_CHAOS_PENALTY)}')
    except Exception:
        pass  # ws_guardian 未启动时静默降级

    # ── s7增强层①: orderbook_heatmap 订单簿大单压力（权重升级 2026-07-01）──────────────
    # 否决权: ASK/BID>10倍做多 → -20分，允许负分传递到 score（不 clip 0）
    try:
        from brahma_brain.orderbook_heatmap import get_ob_score as _ob_score
        _ob_pts, _ob_desc = _ob_score(_sym, signal_dir)
        if _ob_pts != 0:
            if _ob_pts < 0:  # 否决权场景：允许负分流入总分，不 clip
                s7 = max(-20, s7 + _ob_pts)  # 下限-20
            else:
                s7 = min(15, s7 + _ob_pts)   # 上限保持15
            print(f'[s7-OBHeatmap] {_sym} {signal_dir}: {_ob_pts:+d} | {_ob_desc}')
    except Exception:
        pass

    # ── s7增强层②: liq_density_engine 三所清算密度（方向性加权 2026-07-01）──────────────────
    # liq_density 评分基准视角: +分 = 顺势做空 / -分 = 逆势做空
    # SHORT: 直接使用 / LONG: 反转（逆势做多 = 不利）
    try:
        from brahma_brain.liq_density_engine import get_liq_density as _get_liq_dens
        _cur_px = extra_data.get('price', 0) or 0
        if _cur_px > 0:
            _ld = _get_liq_dens(_sym, _cur_px)
            _ld_adj = _ld.get('score_adj', 0)
            if signal_dir == 'LONG':
                _ld_adj = -_ld_adj  # 反转：做多时下方清算密集是负面（商以近期下行）
            if _ld_adj != 0 and _ld.get('confidence', 0) >= 0.3:
                s7 = max(0, min(15, s7 + _ld_adj))
                print(f'[s7-LiqDens] {_sym} {signal_dir}: {_ld_adj:+d} | bias={_ld["liq_bias"]} conf={_ld["confidence"]} above={_ld.get("above_total_usd",0)/1e6:.1f}M below={_ld.get("below_total_usd",0)/1e6:.1f}M')
    except Exception:
        pass

    score += s7
    breakdown['清算/OI'] = s7

    # ── 维度8：资金费率+情绪+链上（0~15）────────────────────────────
    # [UP-017] CoinGlass 链上评分接入
    if extra_data and extra_data.get('coinglass') and extra_data['coinglass'].get('available'):
        _cg_d = extra_data['coinglass']
        # 链上评分直接叠加（-10~+10 → 映射到 0~5）
        _oc_bonus = max(0, min(5, extra_data.get('onchain_score', 0) + 2))
        # F&G 极度恐惧做多+3，极度贪婪做空+3
        _fg_label = _cg_d['fear_greed']['label']
        if signal_dir == 'LONG' and _fg_label in ('EXTREME_FEAR', 'FEAR'):
            _oc_bonus += 2
        elif signal_dir == 'SHORT' and _fg_label in ('EXTREME_GREED', 'GREED'):
            _oc_bonus += 2
        # 清算方向确认
        _liq_bias = _cg_d['liquidation']['bias']
        if signal_dir == 'LONG' and _liq_bias == 'BULLISH_SQUEEZE':
            _oc_bonus += 2   # 大量空头被清算，多头信号确认
        elif signal_dir == 'SHORT' and _liq_bias == 'BEARISH_CONFIRMED':
            _oc_bonus += 2
    else:
        _oc_bonus = 0
    # ── 维度8：资金费率+情绪（0~10）──────────────────────────────
    fr = ms['sentiment']['funding_rate']
    if extra_data and extra_data.get('sentiment'):
        s8_base = extra_data['sentiment'].get('score', 0)
    else:
        s8_base = 0
        # [D08实训修复] fr单位为小数百分比(0.001=0.1%/8h)，原阈值0.1=10%永远达不到
        # 正常ETH/BTC费率范围：0.0001(0.01%)~0.005(0.5%)，极端时可达0.01(1%)
        if signal_dir == 'LONG':
            if fr < -0.003:   s8_base = 10  # -0.3%/8h 极度空头拥挤，多头极有利
            elif fr < -0.001: s8_base = 7   # -0.1%/8h 空头付息
            elif fr < -0.0003:s8_base = 5   # -0.03%/8h 轻度有利
            elif fr < 0.0001: s8_base = 3   # 接近0，中性偏有利
        else:  # SHORT
            if fr > 0.005:    s8_base = 10  # 0.5%/8h 极度多头拥挤，空头极有利
            elif fr > 0.003:  s8_base = 8   # 0.3%/8h 高费率
            elif fr > 0.001:  s8_base = 6   # 0.1%/8h 偏高费率
            elif fr > 0.0003: s8_base = 4   # 0.03%/8h 正常偏高
            elif fr > 0.0001: s8_base = 2   # 0.01%/8h 轻微多头付息
    # P1b: 链上引擎额外加成（最多+10分）
    onchain_bonus = 0
    if extra_data and extra_data.get('onchain'):
        oc = extra_data['onchain']
        onchain_bonus = min(int(oc.get('score', 0) / 3), 10)
    # P1c: 合约基差加分（正基差→合约溢价/反向有利）
    basis_bonus = 0
    if extra_data and extra_data.get('basis'):
        _b = extra_data['basis']
        _basis_pct = float(_b.get('basis_pct', 0))
        if signal_dir in ('SHORT', '做空'):
            # 正基差（合约溢价）→ 多头溢价，空头有利
            if _basis_pct > 0.08:    basis_bonus = 3
            elif _basis_pct > 0.04:  basis_bonus = 2
            elif _basis_pct > 0.01:  basis_bonus = 1
            elif _basis_pct < -0.04: basis_bonus = -1  # 已折价，空头不利
        else:
            # 负基差（合约折价）→ 空头溢价，多头有利
            if _basis_pct < -0.08:   basis_bonus = 3
            elif _basis_pct < -0.04: basis_bonus = 2
            elif _basis_pct < -0.01: basis_bonus = 1
            elif _basis_pct > 0.04:  basis_bonus = -1  # 已溢价，多头不利
    s8 = min(s8_base + onchain_bonus + basis_bonus + _oc_bonus, 20)  # [UP-017] +CoinGlass链上
    score += s8
    breakdown['情绪/费率'] = s8

    # ── 维度9：时段权重（精细化）─────────────────────────────────
    import datetime
    hour = datetime.datetime.utcnow().hour
    if extra_data and extra_data.get('enhanced'):
        en = extra_data['enhanced']
        s9 = en.get('breakdown', {}).get('session', 4)
        lsr_bonus = min(en.get('breakdown', {}).get('lsr_trend', 0), 5)
        cvd_bonus = min(en.get('breakdown', {}).get('cvd', 0), 4)
        s9 = min(s9 + lsr_bonus + cvd_bonus, 20)
    else:
        top_hours = {17,6,5,13,16,19,12,8,11,3,4}
        if 13 <= hour <= 15:   s9 = 10
        elif 13 <= hour <= 21: s9 = 8
        elif 7 <= hour <= 15:  s9 = 6
        else:                  s9 = 4
        if hour in top_hours:  s9 = min(s9 + 2, 10)
    if extra_data and extra_data.get('macro'):
        mc_bonus = min(extra_data['macro'].get('score', 0) // 4, 3)
        s9 = min(s9 + mc_bonus, 20)
    # A7: ATR历史百分位（波动率体制）
    if extra_data and extra_data.get('atr_percentile'):
        _atr_p = extra_data['atr_percentile']
        _atr_adj = int(_atr_p.get('score_adj', 0))
        # COMPRESSED: 低波动压缩，不管方向都加分（爆发在即）
        # EXPANDED: 高波动已爆，追入惩罚
        s9 = min(max(s9 + _atr_adj, 0), 20)
    score += s9
    breakdown['时段权重'] = s9

    # ── 维度10(NEW)：谐波PRZ + 多周期对齐 ───────────────────────
    # [D10实训修复] 谐波方向冲突惩罚：best_dir != signal_dir时减分而非无脑加分
    s10 = 0
    # [P2 audit-fix 2026-06-17] harmonic已移除，此读取安全（返回空dict）
    if extra_data and extra_data.get('harmonic'):
        _harm = extra_data['harmonic']
        _harm_sc = _harm.get('score', 0)
        _best_dir = _harm.get('best', {}).get('direction', '') if _harm.get('best') else ''
        _in_prz   = _harm.get('best', {}).get('in_prz', False) if _harm.get('best') else False
        if _best_dir == signal_dir:
            # 方向一致：全额加分
            s10 += min(_harm_sc, 10)
        elif _best_dir and _best_dir != signal_dir:
            # 方向冲突：PRZ区间内明显扣分，未命中PRZ轻微扣
            if _in_prz:
                s10 += -3  # PRZ内反向谐波：明确看涨/空，信号反向要小心
            else:
                s10 += 0   # 远离PRZ：忽略，不加不减
        else:
            s10 += min(_harm_sc // 2, 5)  # 无方向：给一半分
        _has_harm = bool(_harm.get('patterns'))
    # [D10 v12.6] 无谐波时：斐波那契回撤位基础分（0~4），解决覆盖率低问题
    if not (_has_harm if 'harmonic' in (extra_data or {}) else False):
        _price10 = ms.get('price', 0)
        _fib10   = ms.get('key_levels', {}).get('fib', {})
        if _price10 > 0 and _fib10:
            _fib_bonus = 0
            for _fk, _fv in [('0.618', 4), ('0.786', 4), ('0.500', 3), ('0.382', 3)]:
                if _fk in _fib10:
                    _fdist = abs(_price10 - float(_fib10[_fk])) / _price10
                    if _fdist < 0.008:    _fib_bonus = max(_fib_bonus, _fv)
                    elif _fdist < 0.020: _fib_bonus = max(_fib_bonus, _fv - 1)
                    elif _fdist < 0.040: _fib_bonus = max(_fib_bonus, 1)
            s10 += _fib_bonus
    # [D10达摩院实训] 无谐波形态时multitf独立贡献满权（不再//2被压制）
    if extra_data and extra_data.get('multitf'):
        _mtf_sc = extra_data['multitf'].get('score', 0)
        # [P2 audit-fix 2026-06-17] harmonic已移除，此读取安全（返回空dict）
        _has_harm2 = bool(extra_data.get('harmonic', {}).get('patterns'))
        if _has_harm2:
            s10 += min(_mtf_sc // 2, 10)
        else:
            s10 += min(_mtf_sc * 2 // 3, 13)
    # [外科手术 2026-05-30] 谐波删除，保留Fib+多周期，上限20→10
    # 谐波误报率高，未经达摩院验证；Fib已在s2覆盖
    s10 = max(-3, min(s10, 10))
    score += s10
    breakdown['多周期对齐'] = s10

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

    # ── Phase C 维度15: LSTM + NLP情绪 ─────────────────────────────
    s15 = 0
    # C1: LSTM 时序
    if extra_data and extra_data.get('lstm'):
        s15 += extra_data['lstm'].get('score', 0)
    # C3: NLP 情绪
    if extra_data and extra_data.get('sentiment_nlp'):
        _nlp = extra_data['sentiment_nlp']
        _nlp_score = _nlp.get('score', 0)
        # [D15达摩院实训] news=0时给中性分+FG极值信号，而非直接0
        _fg = _nlp.get('fng_value', 50)
        _news_n = _nlp.get('news_count', 0)
        # [D15 v12.7b] BSP行为情绪合成 + FG混合 (0.6*BSP + 0.4*FG)
        # 无需外部API，从达摩院Parquet OHLCV实时合成
        _bsp_score = 0
        try:
            import sys as _sys
            if 'brahma_brain' not in _sys.path[0]:
                _sys.path.insert(0, 'brahma_brain')
            from dharma_nlp_synthetic import BehaviorSentiment as _BSP
            if not hasattr(analyze, '_bsp_engine') or analyze._bsp_engine is None:
                analyze._bsp_engine = _BSP()
            _bsp_val, _bsp_detail = analyze._bsp_engine.score(
                ms.get('symbol','BTCUSDT'), signal_dir, '1h'
            )
            if 'error' not in _bsp_detail:
                _bsp_score = _bsp_val
                if extra_data is not None:
                    extra_data['bsp'] = {'score': _bsp_val, 'detail': _bsp_detail.get('bsp', {})}
        except Exception:
            pass  # BSP失败降级到纯FG
        # FG固定映射（全区间8档）
        if _nlp_score == 0 or _news_n == 0:
            # 无新闻：完全依赖FG映射
            if signal_dir in ('SHORT', '做空'):
                if   _fg <= 15: _nlp_score = 5
                elif _fg <= 25: _nlp_score = 4
                elif _fg <= 35: _nlp_score = 3
                elif _fg <= 45: _nlp_score = 2
                elif _fg <= 55: _nlp_score = 0
                elif _fg <= 65: _nlp_score = -1
                elif _fg <= 75: _nlp_score = -2
                else:           _nlp_score = -3
            else:
                if   _fg <= 15: _nlp_score = -3
                elif _fg <= 25: _nlp_score = -2
                elif _fg <= 35: _nlp_score = -1
                elif _fg <= 45: _nlp_score = 0
                elif _fg <= 55: _nlp_score = 1
                elif _fg <= 65: _nlp_score = 2
                elif _fg <= 75: _nlp_score = 3
                else:           _nlp_score = 4
        else:
            # 有新闻：原NLP分 + FG辅助调整（限幅±2，避免双重放大）
            _fg_adj = 0
            if signal_dir in ('SHORT', '做空'):
                if _fg <= 25: _fg_adj = 2
                elif _fg >= 75: _fg_adj = -2
            else:
                if _fg <= 25: _fg_adj = -2
                elif _fg >= 75: _fg_adj = 2
            _nlp_score = max(-8, min(_nlp_score + _fg_adj, 8))
        # [v12.7b] BSP方向确认层：同向增强x1.3，反向保守维持FG（不抵消）
        if _bsp_score != 0 and _nlp_score != 0:
            if _bsp_score * _nlp_score > 0:
                # 同方向：加权增强，最大+30%
                _nlp_score = max(-8, min(_nlp_score * 1.3 + 0.2 * _bsp_score, 8))
            # 反向时：BSP指向短期动量，FG指向宏观情绪；保守取FG不变
        elif _bsp_score != 0 and _nlp_score == 0:
            # FG中性时BSP独立贡献轻量分 (0.3倍，不主导)
            _nlp_score = max(-4, min(_bsp_score * 0.3, 4))
        s15 += _nlp_score
    # [外科手术 2026-05-30] LSTM删除（P0修复确认WR=27%<无LSTM时43%）
    # 保留NLP情绪(FG映射)，删除LSTM贡献
    # s15此时仅含NLP部分
    s15_adj = s15  # LSTM已在上方s15+=语句中为0，无需再打折
    # [DharmaFactor 2026-06-03] LSTM+NLP avg=-2.1分实盘铁证 → 上限清零
    # 实盘49条: LSTM+NLP avg=-2.1分，负资产，不应影响评分
    # [P0-B NLP-fix 2026-06-17] 恢复FG情绪分（LSTM已删除，FG映射是纯规则稳健）
    # 旧铁证：2026-06-03 49条 LSTM+NLP avg=-2.1（含LSTM负贡献）
    # 现状：LSTM=0，仅FG映射+BSP；保守激活 s15_adj = s15 * 0.6（最大±5分）
    s15_adj = max(-5, min(round(s15 * 0.6, 1), 5))
    score += s15_adj
    breakdown['LSTM+NLP情绪'] = s15_adj

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

    # ── 维17(NEW)：资金费/多空比/OI情绪引擎（正式第17维度） ──────────────
    s17 = 0
    try:
        import sys as _sys17, os as _os17
        _sys17.path.insert(0, _os17.path.join(_os17.path.dirname(_os17.path.abspath(__file__)), '..', 'scripts'))
        from sentiment_engine import get_sentiment as _get_sentiment
        _sent = _get_sentiment(symbol, signal_dir)
        _s17_raw = _sent.get('score', 0)
        s17 = max(-10, min(10, int(_s17_raw)))
        score += s17
        breakdown['情绪引擎分析'] = s17
        if s17 != 0:
            print(f'[s17-情绪] {symbol} {signal_dir} score={s17} label={_sent.get("label","")}')
    except Exception:
        pass  # 非阻断

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
        _s19_val, _s19_rep = get_combined_guard_score(_macro_sym, _macro_dir, _macro_reg)
        # 限制第19维度对总分的影响范围 -12 ~ +10
        s19 = max(-12, min(10, round(_s19_val, 1)))
        score += s19
        breakdown['宏观+事件'] = s19
        if extra_data is not None:
            extra_data['macro_report'] = _s19_rep
    except Exception as _e19:
        breakdown['宏观+事件_v2'] = 0  # 非阻断  # [P1-B audit-fix] 重复key加后缀


    # ═══════════════════════════════════════════════════════════
    # [s20] 布林带偏离度（宽松量化新维度 2026-06-09）
    # ═══════════════════════════════════════════════════════════
    s20 = 0.0
    try:
        import sys as _sys20, os as _os20
        _sys20.path.insert(0, _os20.path.dirname(_os20.path.abspath(__file__)))
        from bollinger_engine import bollinger_score as _bb_score
        _k1h_bb = (extra_data or {}).get('_klines_1h', {}) or ms.get('klines_1h', {})
        _closes_bb = list(_k1h_bb.get('c', []))[-30:] if isinstance(_k1h_bb, dict) else []
        if len(_closes_bb) >= 20:
            s20, _bb_rep = _bb_score(_closes_bb, signal_dir, ms.get('regime', ''))
            s20 = max(-8, min(10, s20))
            score += s20
            breakdown['布林带偏离'] = s20
            if s20 != 0:
                print(f'[s20-BB] {symbol} {signal_dir} {_bb_rep.get("signals",[])} +{s20:.1f}')
    except Exception as _e20:
        breakdown['布林带偏离_v2'] = 0  # [P1-B audit-fix] 重复key加后缀

    # ═══════════════════════════════════════════════════════════
    # [s21] RSI极值检测（宽松量化新维度 2026-06-09）
    # ═══════════════════════════════════════════════════════════
    s21 = 0.0
    try:
        import sys as _sys21, os as _os21
        _sys21.path.insert(0, _os21.path.dirname(_os21.path.abspath(__file__)))
        from rsi_extreme_engine import rsi_extreme_score as _rsi_score
        _k1h_rsi = (extra_data or {}).get('_klines_1h', {}) or ms.get('klines_1h', {})
        _closes_rsi = list(_k1h_rsi.get('c', []))[-35:] if isinstance(_k1h_rsi, dict) else []
        if len(_closes_rsi) >= 16:
            s21, _rsi_rep = _rsi_score(_closes_rsi, signal_dir, ms.get('regime', ''))
            s21 = max(-6, min(12, s21))
            score += s21
            breakdown['RSI极值'] = s21
            if s21 != 0:
                print(f'[s21-RSI] {symbol} {signal_dir} RSI={_rsi_rep.get("rsi","?")} {_rsi_rep.get("signals",[])} +{s21:.1f}')
    except Exception as _e21:
        breakdown['RSI极值_v2'] = 0  # [P1-B audit-fix] 重复key加后缀

    # ═══════════════════════════════════════════════════════════
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
    # [WFV-v5.0 2026-05-28] 达摩院真实梵天体制驱动训练
    # 用 brahma_brain.market_state.detect_regime() 真实体制标注
    # 15资产×IS/OOS无穿越验证，覆盖2023-2024真实市场
    #
    # 体制OOS均PF（真实值）：
    #   BEAR_EARLY(熊市初期)   PF=1.141 → SHORT奖励, LONG惩罚
    #   CHOP_HIGH(震荡高波)    PF=1.137 → 不惩罚（旧×0.82是错的）
    #   BEAR_RECOVERY(熊市修复) PF=0.998 → 轻惩罚（旧×1.08是错的）
    #   BULL_EARLY(牛市初期)   PF=0.959 → 轻惩罚（旧×1.08是错的）
    #   CHOP_LOW(震荡低波)     PF=0.865 → 惩罚
    #   CHOP_MID     PF=0.862 → 惩罚
    #   BULL_CORRECTION PF=0.687 → 强惩罚
    #   BEAR_TREND(熊市趋势)   PF=0.560 → 强惩罚（旧×1.20是严重错误！）
    # ═══════════════════════════════════════════════════════════
    _regime_str = ms.get('regime', '')
    _regime_upper = str(_regime_str).upper()
    _regime_mult = 1.0

    # ── Fix1+Fix2: 上位共识锁 + BEAR_RECOVERY幅度门槛（设计院 2026-06-29）──
    # 核心逻辑：当月/周/日线三周期全BEAR时，4H的BEAR_RECOVERY切换为噪音
    # Fix1: BEAR_RECOVERY时检查1H方向，1H仍空则维持BEAR_TREND权重
    # Fix2: 上位共识锁，三周期全BEAR时BEAR_RECOVERY乘数历史最大限制到×0.5
    if _regime_upper == 'BEAR_RECOVERY':
        try:
            _d1h_closes = _dc.get_kline_closes(_sym, '1h', 20) if hasattr(_dc, 'get_kline_closes') else []
            _d1h_rsi    = _calc_rsi(_d1h_closes, 14) if len(_d1h_closes) >= 15 else 50
            _d1h_ema20  = _ema_last(_d1h_closes, 20) if len(_d1h_closes) >= 20 else 0
            _d1h_price  = _d1h_closes[-1] if _d1h_closes else 0
            _d1h_is_bear = (_d1h_price > 0 and _d1h_ema20 > 0 and _d1h_price < _d1h_ema20)
            # Fix1: 1H仍在EMA20下方 = 1H未确认反弹 → 将BEAR_RECOVERY降级为BEAR_TREND权重
            if _d1h_is_bear and signal_dir == 'SHORT':
                _regime_upper = 'BEAR_TREND'
                print(f'[Fix1-上位共识锁] {_sym} 1H仍在EMA下方(RSI={_d1h_rsi:.0f}) → BEAR_RECOVERY降级处理为BEAR_TREND权重')
        except Exception:
            pass  # 静默降级，不阻断主流
    # Fix2: 三周期全BEAR时，BEAR_RECOVERY乘数限制上限
    _full_bear_consensus = (ms.get('d1d') == 'BEAR' and ms.get('d1w','BEAR') == 'BEAR')
    if _regime_upper == 'BEAR_RECOVERY' and _full_bear_consensus:
        _recovery_cap = 0.5  # 三周期全BEAR时，RECOVERY最高乘数限分0.5
        print(f'[Fix2-三周期共识锁] {_sym} 日/周线全BEAR → BEAR_RECOVERY乘数将限制到×{_recovery_cap}')
    else:
        _recovery_cap = 1.0
    # [v25.0 达摩院矩阵v4.0 · 2026-06-12]
    # 设计哲学：方向由价格结构决定，不由体制决定。体制只做权重调整，永不封锁。
    # 铁证：140,000次蒙特卡洛 + 8窗口WFV + 8年全周期。MDD>80%组降权0.75，不封锁。
    # _direction_block 永久废除 —— 封禁是懒人修复，降权是外科手术。
    _direction_block = False  # 永久保持False，历史遗留字段保留兼容性

    # ── BTC/ETH 双向 regime_mult 矩阵 v4.0 ─────────────────────
    _sym_upper = (ms.get('symbol') or ms.get('sym') or '').upper()
    _is_long_signal = (signal_dir == 'LONG')

    # 通用矩阵（默认，适用非BTC/ETH标的）
    _REGIME_MULT_DEFAULT = {
        # 体制            SHORT   LONG
        'BEAR_TREND':    (1.50,  0.35),   # [v25.6 为交易而生 2026-06-18] SHORT S+级WR=71.8% n=2413 | LONG极端降权0.35×(WR=45% n=3322，需score≥400，自然淘汰，梵天能力提升后开放)
        'BEAR_EARLY':    (1.15,  0.35),   # [v25.5 2026-06-18] SHORT强Alpha WR=66.5% | LONG降权0.35x(WR=50.4% n=5396 avg=-0.110 非死穴，降权非封禁)
        'BEAR_RECOVERY': (0.35,  1.20),   # [v25.6 设计院 2026-06-18] LONG=反直觉alpha WR=72.5% | SHORT极端降权0.35×（WR=47.9% n=603，为交易而生，非封禁）
        'BULL_TREND':    (0.50,  1.10),   # [v25.1 2026-06-13] LONG=正alpha(n=3046 WR=70.3% avgPnL=+0.242) SHORT=死穴(n=4999 WR=47.7% avgPnL=-0.229)
        'BULL_EARLY':    (0.35,  1.20),   # [v25.5 2026-06-18] LONG=S级alpha(WR=64.4% n=5396 +0.093%) | SHORT降权0.35x(WR=51.9% n=5396 avg=-0.137% 非死穴，降权非封禁)
        'BULL_CORRECTION':(1.10, 0.65),   # 牛回调: SHORT强，LONG样本不足
        'BULL_PEAK':     (1.00,  0.75),   # 牛顶:   SHORT尚可
        'BULL_BREAK':    (1.00,  0.75),   # 牛突破: 参考BULL_TREND
        'BEAR_CRASH':    (0.90,  0.65),   # 崩盘:   极端体制，两向均降权
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] 铁证EV=+0.37%/笔(n=3636) SHORT解锁0.88x | LONG保持0.5x（无铁证）
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] 高波动CHOP SHORT=0.80x保守 | LONG=0.5x
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] CHOP_MID SHORT解锁0.88x（WR=57.3%铁证） | LONG=0.5x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] CHOP_LOW SHORT解锁0.88x | LONG=0.5x
        # [设计院 2026-06-30 P2-D] RANGE_LOCK区间状态独立乘数通道（苏摩111审批）
        # 达摩院验证：DISCOUNT債 WR=70.0% | PREMIUM空 WR=61.3%
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # 区间底部做多解锁: LONG=1.20x(达摩院验证WR=70.0% n=120)
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # 区间顶部做空解锁: SHORT=1.10x(达摩院验证WR=61.3% n=163)
    }
    _REGIME_MULT_BTC = {
        # 体制            SHORT   LONG    # Calmar(S) / Calmar(L)
        'BEAR_TREND':    (1.60,  0.35),   # [v25.6 为交易而生] BTC SHORT WR=72% S+级 | LONG极端降权0.35×(非封禁，梵天识别能力问题，非方向永错)
        'BEAR_EARLY':    (1.20,  0.35),   # [v25.5 2026-06-18] BTC SHORT WR=68% S级 | LONG降权0.35x(WR=50.4% avg=-0.110 非死穴)
        'BEAR_RECOVERY': (0.35,  1.25),   # [v25.6] BTC LONG WR=77.6% | SHORT极端降权0.35×
        'BULL_TREND':    (0.50,  1.20),   # [v25.1 2026-06-13] LONG=S级alpha(n=1614 WR=70.5% avgPnL=+0.170) SHORT=死穴(n=2579 WR=48.2% avgPnL=-0.186)
        'BULL_EARLY':    (0.35,  1.20),   # [v25.5 2026-06-18] BTC BULL_EARLY LONG=S级alpha(WR=64.6% n=2737 +0.093%) | SHORT降权0.35x(WR=51.7% n=3398 非死穴)
        'BULL_CORRECTION':(1.20, 0.60),   # S=14.6 WR=97% / L=不激活
        'BULL_PEAK':     (1.05,  0.70),   # 参考BULL_TREND/BULL_CORRECTION
        'BULL_BREAK':    (1.08,  0.65),
        'BEAR_CRASH':    (0.75,  0.60),
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] BTC CHOP SHORT n=3636 WR=57.3% EV=+0.365%/笔(v4.0参数)
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] BTC CHOP_HIGH SHORT=0.80x保守
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] BTC CHOP_MID SHORT解锁0.88x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] BTC CHOP_LOW SHORT解锁0.88x
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # [设计院 P2-D] BTC区间底部做多: LONG=1.20x
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # [设计院 P2-D] BTC区间顶部做空: SHORT=1.10x
    }

    # ETH专属矩阵（达摩院v4.0铁证）
    _REGIME_MULT_ETH = {
        # 体制            SHORT   LONG    # Calmar(S) / Calmar(L)
        'BEAR_TREND':    (1.60,  0.35),   # [v25.6 为交易而生] ETH SHORT WR=74% S+级 | LONG极端降权0.35×
        'BEAR_EARLY':    (1.20,  0.35),   # [v25.5 2026-06-18] ETH SHORT WR=70% S级 | LONG降权0.35x(非死穴，WR=50.4% avg=-0.110)
        'BEAR_RECOVERY': (0.35,  1.15),   # [v25.6] ETH LONG WR=67.1% | SHORT极端降权0.35×
        'BULL_TREND':    (0.50,  1.30),   # [v25.1 2026-06-13] LONG=最强alpha(n=1432 WR=70.0% avgPnL=+0.324) SHORT=死穴(n=2420 WR=47.1% avgPnL=-0.274)
        'BULL_EARLY':    (0.35,  1.10),   # [v25.5 2026-06-18] ETH BULL_EARLY LONG=S级alpha(WR=64.2% n=2659) | SHORT降权0.35x(WR=52.2% n=3457 非死穴)
        'BULL_CORRECTION':(1.02, 0.60),   # S=1.5 / L=不激活(n/yr=6.3)
        'BULL_PEAK':     (1.05,  0.70),
        'BULL_BREAK':    (1.10,  0.75),
        'BEAR_CRASH':    (0.75,  0.60),
        'CHOP':          (0.88,  0.50),   # [v25.4 苏摩111 2026-06-28] ETH CHOP SHORT n=3663 WR=57.5% EV=+0.375%/笔(v4.0参数)
        'CHOP_HIGH':     (0.80,  0.50),   # [v25.4] ETH CHOP_HIGH SHORT=0.80x保守
        'CHOP_MID':      (0.88,  0.50),   # [v25.4] ETH CHOP_MID SHORT解锁0.88x
        'CHOP_LOW':      (0.88,  0.50),   # [v25.4] ETH CHOP_LOW SHORT解锁0.88x
        'CHOP_RANGE_DISCOUNT': (0.50,  1.20),  # [设计院 P2-D] ETH区间底部做多: LONG=1.20x
        'CHOP_RANGE_PREMIUM':  (1.10,  0.35),  # [设计院 P2-D] ETH区间顶部做空: SHORT=1.10x
    }

    # ── [P1-哲学修复 设计院 2026-06-24] 中小币专属乘数矩阵 ──────────────────
    # 哲学：不封禁，让评分自然淘汰。
    # 方法：每个标的的铁证WR / BTC+ETH参考WR = 标的专属乘数
    # 来源：达摩院 altcoin_iron_evidence.json（5标的 2020~2026 离线回放）
    # 未覆盖的组合降级到 _REGIME_MULT_DEFAULT，不猜测，不封禁。
    # 更新规则：auto_learner 每 N 条实盘后自动更新此表
    _REGIME_MULT_ALTCOIN = {
        'SOLUSDT': {
            # 铁证WR / BTC+ETH参考 → 比例乘数（范围0.25~1.2）
            'BEAR_TREND':     (0.75, 0.28),  # SHORT n=28 WR=53.6%  | LONG n=20 WR=20.0%
            'BEAR_EARLY':     (0.58, 0.35),  # SHORT n=412 WR=38.3% | LONG 降权对齐DEFAULT
            'BULL_EARLY':     (0.35, 0.56),  # LONG n=411 WR=35.8%  | SHORT 降权
            'BULL_TREND':     (0.35, 0.28),  # LONG n=20 WR=20.0% → 极端降权
            'BEAR_RECOVERY':  (0.35, 0.80),  # 无足够样本，保守
            'BULL_CORRECTION':(0.60, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'NEARUSDT': {
            'BEAR_TREND':     (0.70, 0.35),  # SHORT n=10 WR=50.0%  | LONG 无样本
            'BEAR_EARLY':     (0.57, 0.35),  # SHORT n=435 WR=38.2% | LONG 降权
            'BULL_EARLY':     (0.35, 0.58),  # LONG n=413 WR=37.5%  | SHORT 降权
            'BULL_TREND':     (0.35, 0.81),  # LONG n=14 WR=57.1%（n偏少，保守）
            'BEAR_RECOVERY':  (0.35, 0.80),
            'BULL_CORRECTION':(0.60, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'MANAUSDT': {
            'BEAR_TREND':     (0.35, 0.35),  # SHORT n=12 WR=25.0% → 极端降权
            'BEAR_EARLY':     (0.59, 0.35),  # SHORT n=422 WR=39.1%
            'BULL_EARLY':     (0.35, 0.51),  # LONG n=342 WR=33.0%
            'BULL_TREND':     (0.35, 0.55),  # LONG n=13 WR=38.5%（n偏少）
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.50, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'AXSUSDT': {
            'BEAR_TREND':     (0.46, 0.35),  # SHORT n=15 WR=33.3%
            'BEAR_EARLY':     (0.55, 0.35),  # SHORT n=438 WR=36.5%
            'BULL_EARLY':     (0.35, 0.50),  # LONG n=363 WR=32.5%
            'BULL_TREND':     (0.35, 0.50),  # 无足够样本
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.50, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
        'GALAUSDT': {
            'BEAR_TREND':     (0.70, 0.35),  # SHORT n=18 WR=50.0%
            'BEAR_EARLY':     (0.57, 0.35),  # SHORT n=418 WR=38.0%
            'BULL_EARLY':     (0.35, 0.51),  # LONG n=280 WR=32.9%
            'BULL_TREND':     (0.35, 0.50),
            'BEAR_RECOVERY':  (0.35, 0.70),
            'BULL_CORRECTION':(0.55, 0.35),
            'CHOP':           (0.50, 0.50), 'CHOP_HIGH': (0.50,0.50),
            'CHOP_MID':       (0.50, 0.50), 'CHOP_LOW':  (0.55,0.55),
        },
    }

    # 选择矩阵（优先标的专属，其次BTC/ETH，最后DEFAULT）
    if _sym_upper in _REGIME_MULT_ALTCOIN:
        _mult_table = _REGIME_MULT_ALTCOIN[_sym_upper]
    elif 'BTC' in _sym_upper:
        _mult_table = _REGIME_MULT_BTC
    elif 'ETH' in _sym_upper:
        _mult_table = _REGIME_MULT_ETH
    else:
        _mult_table = _REGIME_MULT_DEFAULT

    # 查找当前体制的mult
    _matched_regime_key = None
    for _rk in _mult_table:
        if _rk in _regime_upper:
            _matched_regime_key = _rk
            break
    if _matched_regime_key:
        _s_mult, _l_mult = _mult_table[_matched_regime_key]
        _regime_mult = _l_mult if _is_long_signal else _s_mult
    else:
        _regime_mult = 0.85  # 未知体制，保守降权

    score = int(score * _regime_mult)
    breakdown['_regime_mult'] = _regime_mult
    breakdown['_regime_v4_key'] = _matched_regime_key or 'UNKNOWN'
    breakdown['_regime'] = _regime_str

    # ── [v25.4 设计院封印] 硬封禁门控 — mult=0.00 后强制 score=0 ──────────
    # 防止：乘数为0但其他维度加分（s_research / T04奖励等）绕过封禁
    # 覆盖体制：BEAR_TREND_LONG / BULL_TREND_SHORT / BEAR_RECOVERY_SHORT 等
    # 哲学：不封禁 = 为交易而生；但死穴（WR<48%,n≥100铁证）= 硬封禁，没有例外
    # ── [v25.6 2026-06-18 设计院] 废除 HARD_BLOCK ─────────────────────────
    # 原则：为交易而生，没有方向是永远封禁的
    # 低WR组合改为极端降权0.35×（需score≥400才能通过门控=自然淘汰）
    # 梵天能力提升后，这些方向仍有机会被激活
    # _HARD_BLOCK_COMBOS 已废除，此处保留注释记录历史
    # 历史被封禁原因：BEAR_TREND_LONG WR=45% / BULL_TREND_SHORT WR=47.7%
    # 改造方向：提升识别能力，而不是永久关闭

    # [UP-TRAIN10K] T04体制×最优信号奖励矩阵
    # 达摩院1万次训练: 特定体制下命中最优信号给予×1.08奖励
    # BULL_PEAK+量价配合PF=1.393 | BULL_TREND(牛市趋势)+EMA PF=1.344
    # BEAR_CRASH+布林反弹PF=1.261 | BEAR_TREND(熊市趋势)+MACD零轴PF=1.156
    _t04_regime = _regime_upper
    _t04_bonus_applied = False
    _s4_optimal = (  # 体制×最优信号命中检测
        ('BULL_PEAK' in _t04_regime and breakdown.get('量能验证', 0) >= 15) or
        ('BULL_TREND' in _t04_regime and breakdown.get('趋势一致性', 0) >= 15) or
        ('BEAR_CRASH' in _t04_regime and breakdown.get('关键位精确度', 0) >= 12) or
        ('BEAR_TREND' in _t04_regime and breakdown.get('动量背离', 0) >= 10)
    )
    if _s4_optimal and not _direction_block and score > 0:
        score = int(score * 1.08)
        breakdown['T04体制最优'] = f'×1.08 ({_t04_regime[:10]}命中最优信号)'
        _t04_bonus_applied = True

    # [UP-NODE] 深度节点训练 N01~N06 注入
    # ─────────────────────────────────────────────
    # N01: RSI超卖超买 是最高协同信号（与量价/MACD背离搭档PF=1.232）
    #   → RSI信号同时命中时，额外+3分确认
    _rsi_score_raw = breakdown.get('关键位精确度', 0)  # RSI代理维度
    _vol_score_raw = breakdown.get('量能验证', 0)
    _macd_div_raw  = breakdown.get('动量背离', 0)
    _n01_synergy = (
        (_rsi_score_raw >= 10 and _vol_score_raw >= 12) or   # RSI+量价 synergy=0.012
        (_rsi_score_raw >= 10 and _macd_div_raw >= 10)        # RSI+MACD背离 synergy=0.012
    )
    if _n01_synergy and not _direction_block and score > 0:
        score = min(score + 3, 175)
        breakdown['N01协同奖励'] = '+3 (RSI双重协同)'

    # [Phase2c] RSI 50-70 中性偏强区加分
    # 达摩院铁证: RSI 50-70 WR=72.5%，超过超买(66.4%)和超卖(69.5%)
    # n=69,895，最大样本区间
    try:
        _rsi_now = ms.get('rsi_1h', 50) or 50
        if 50 <= _rsi_now <= 70 and signal_dir == 'SHORT' and score > 0 and not _direction_block:
            score = min(score + 8, 175)
            breakdown['Phase2c_RSI中性偏强'] = f'+8 (RSI={_rsi_now:.0f} 50-70区 WR=72.5%)'
        elif 30 <= _rsi_now <= 50 and signal_dir == 'LONG' and score > 0 and not _direction_block:
            score = min(score + 8, 175)
            breakdown['Phase2c_RSI中性偏强_v2'] = f'+8 (RSI={_rsi_now:.0f} 30-50区做多 WR=72.5%)'  # [P1-B audit-fix] 重复key加后缀
    except Exception:
        pass

    # [Phase2c] 量能×RSI>60 协同奖励 (黄金矩阵最大样本组合)
    # 达摩院铁证: 量能+RSI>60+OB WR=75.5% n=10,194，6年最差年WR=71.4%
    try:
        _vol_strong = breakdown.get('量能验证', 0) >= 10  # 量能引擎分数较高
        _rsi_60plus = _rsi_now > 60 if signal_dir == 'SHORT' else _rsi_now < 40
        if _vol_strong and _rsi_60plus and score > 0 and not _direction_block:
            score = min(score + 6, 175)
            breakdown['Phase2c_量能×RSI协同'] = f'+6 (量能强+RSI={_rsi_now:.0f} WR=75.5% n=10K)'
    except Exception:
        pass

    # N03: 时段权重 [Phase2c 2026-06-03 达摩院实证重写]
    # 铁证(n=140,443 BTC 15m OB做空 6年):
    #   欧盘 UTC07-13: WR=77.3% → +10分
    #   纽约盘后 UTC19-23: WR=69.4% → +2分
    #   亚盘 UTC00-06: WR=68.2% → 0分
    #   美盘 UTC14-18: WR=66.4% → -8分（最差，散户噪音）
    #   峰值: UTC11h WR=80.2%, UTC10h WR=78.8%（欧盘核心）
    import datetime as _dt
    _hour_utc = _dt.datetime.now(_dt.timezone.utc).hour
    _n03_delta = 0
    if 7 <= _hour_utc <= 13:    # 欧盘：WR=77.3%，比基线+6.6%
        _n03_delta = 10
        _n03_label = f'+10 (欧盘UTC{_hour_utc:02d}h WR=77.3%)'
    elif 19 <= _hour_utc <= 23: # 纽约盘后：WR=69.4%，轻微正向
        _n03_delta = 2
        _n03_label = f'+2 (纽约盘后UTC{_hour_utc:02d}h WR=69.4%)'
    elif 14 <= _hour_utc <= 18: # 美盘: 注意 n=7 样本不足，仅为观察值；降权-15基于哲学原则（降权不封禁），非数据铁证
        # [v24.2-fix 2026-06-12] 硬拒绝→降权-15分
        # 哲学原则: 不封禁时段，降权让grade≥70自然过滤
        # WR=22.2%是B级(grade55)污染结果，升门槛后美盘grade≥70仍有价值
        _n03_delta = -15
        _n03_label = f'-15 (美盘UTC{_hour_utc:02d}h 降权非封禁 v24.2)'
    else:                        # 亚盘 UTC00-06：WR=68.2%，中性
        _n03_delta = 0
        _n03_label = f'0 (亚盘UTC{_hour_utc:02d}h WR=68.2%)'
    if not _direction_block and score > 0 and _n03_delta != 0:
        score = max(0, min(score + _n03_delta, 175))
        if _n03_delta > 0:
            breakdown['N03时段奖励'] = _n03_label

    # N04: 周末惩罚 (Sat/Sun PF=0.836/0.810 < 1.0)
    _dow = -1
    try:
        _ts2 = row.name
        _dow = _ts2.dayofweek if hasattr(_ts2, 'dayofweek') else -1
    except Exception:
        pass
    if _dow in {5, 6} and not _direction_block and score > 0:  # Sat=5, Sun=6
        # [v24.3-fix] 周末 硬拒绝→降权-20分 — 哲学: 降权不封禁
        # 周末WR=65%(干净数据,样本少)，不是封死的理由；grade≥70的A级信号降权后仍可通过
        _weekend_penalty = 20
        score = max(0, score - _weekend_penalty)
        breakdown['N04周末降权'] = f'周{"六" if _dow==5 else "日"} -20分降权(v24.3) 当前score={score:.0f}'

    # N06: CHOP体制持仓期提示（最优2h vs 全局12h）
    if 'CHOP' in _regime_upper and not _direction_block:
        breakdown['N06持仓建议'] = '⚡CHOP最优持仓2h (N06实训)'


    # ════════════════════════════════════════════════════════════
    # [L7] Kronos方向验证层 v2.0 2026-05-30（设计院全局落地）
    # ════════════════════════════════════════════════════════════
    # 修复：灰区置信度（50%~70%）不再静默，轻惩罚/轻奖励
    # 同向任意置信度奖励 | 反向分级惩罚
    try:
        import json as _j, os as _os, time as _t
        _kf = '/tmp/kronos_signal.json'
        if _os.path.exists(_kf) and (_t.time() - _os.path.getmtime(_kf)) < 21600:
            _kd    = _j.load(open(_kf))
            _sym_k = (extra_data.get('_symbol','') if extra_data else '') or (ms.get('symbol','') if ms else '')
            _kp    = _kd.get(_sym_k, {})
            _kdir  = _kp.get('direction', 'NEUTRAL')
            _kconf = float(_kp.get('confidence', 0.5))
            _met   = _kd.get('_meta', {}).get('method', 'stat')
            _kage_h= (_t.time() - _os.path.getmtime(_kf)) / 3600  # Kronos数据年龄
            # 数据老化折扣（超过2H降低权重）
            _age_factor = 1.0 if _kage_h < 2 else (0.7 if _kage_h < 4 else 0.4)
            _up = (signal_dir == 'LONG'  and _kdir == 'UP')
            _dn = (signal_dir == 'SHORT' and _kdir == 'DOWN')
            _conflict = (
                (signal_dir == 'LONG'  and _kdir == 'DOWN') or
                (signal_dir == 'SHORT' and _kdir == 'UP')
            )
            if not _direction_block:
                if _up or _dn:
                    # 同向：置信度分级奖励
                    if _kconf >= 0.65:
                        _s = round(13 * _age_factor)
                    elif _kconf >= 0.55:
                        _s = round(8  * _age_factor)
                    else:
                        _s = round(4  * _age_factor)  # 弱同向也给分
                    if _s > 0:
                        score += _s
                        breakdown['L7_Kronos'] = f'+{_s}(同向{_kconf:.0%} age={_kage_h:.1f}h [{_met}])'
                elif _conflict and _kdir != 'NEUTRAL':
                    # 反向：分级惩罚（原>0.70才-10，现在灰区也有惩罚）
                    if _kconf >= 0.70:
                        _pen = round(10 * _age_factor)
                    elif _kconf >= 0.60:
                        _pen = round(5  * _age_factor)  # 中等置信反向 -5（新增）
                    else:
                        _pen = round(2  * _age_factor)  # 弱反向 -2（新增）
                    if _pen > 0:
                        score -= _pen
                        breakdown['L7_Kronos_v2'] = f'-{_pen}(反向{_kconf:.0%} age={_kage_h:.1f}h [{_met}])'  # [P1-B audit-fix] 重复key加后缀
    except Exception:
        pass

    # [UP-NODE-v3] 深度节点训练 v3 N07~N12 注入
    # ─────────────────────────────────────────────
    # N08: RSI深度分层 — 体制加限（避免震荡追高）
    _rsi_val = float(ms.get('rsi_1h', 50) if ms else 50)
    _is_long_signal = (signal_dir == 'LONG')
    _n08_boost = False
    _trend_regimes = ('BULL_TREND', 'BULL_PEAK')
    _bear_regimes  = ('BEAR_TREND', 'BEAR_CRASH')
    # 做多超买只在牛市体制有效 | 做空超卖只在熊市体制有效
    if _is_long_signal and _rsi_val > 75 and any(r in _regime_upper for r in _trend_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N08_RSI强化'] = f'+4 (RSI={_rsi_val:.0f} 牛市超买PF=1.421)'
        _n08_boost = True
    elif not _is_long_signal and _rsi_val < 20 and any(r in _regime_upper for r in _bear_regimes) and not _direction_block and score > 0:
        # [WFV-v1 2026-05-28] RSI阈值收紧 25→20 (OOS验证: 更纯净信号)
        score = min(int(score) + 4, 175)
        breakdown['N08_RSI强化_v2'] = f'+4 (RSI={_rsi_val:.0f} 熊市超卖<20 PF=1.292)'  # [P1-B audit-fix] 重复key加后缀
        _n08_boost = True

    # N08: BULL_TREND体制RSI=45~55区间特别强化(PF=2.102)
    if 'BULL_TREND' in _regime_upper and 45 <= _rsi_val < 55 and not _direction_block and score > 0:
        score = min(int(score) + 6, 175)
        breakdown['N08_牛市RSI中性'] = f'+6 (BULL_TREND RSI=45~55 PF=2.102)'

    # N10: 7维全覆盖叠加奖励 — 所有主信号都有贡献时+5分
    _sig_scores_v3 = [
        breakdown.get('动量背离', 0),
        breakdown.get('关键位精确度', 0),
        breakdown.get('SMC结构', 0),
        breakdown.get('趋势一致性', 0),
        breakdown.get('量能验证', 0),
        breakdown.get('形态成熟度', 0),
        breakdown.get('时段权重', 0),
    ]
    _n_active_sigs = sum(1 for s in _sig_scores_v3 if s > 0)
    if _n_active_sigs >= 7 and not _direction_block and score > 0:
        score = min(int(score) + 5, 175)
        breakdown['N10_全覆盖奖励'] = '+5 (7维全覆盖 PF=1.363)'

    # N12: BB位置精度强化 — 仅趋势体制有效
    _bb_pct_v3 = float(ms.get('bb', {}).get('pos', 0.5) if ms else 0.5)
    if _is_long_signal and _bb_pct_v3 > 0.90 and any(r in _regime_upper for r in _trend_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N12_BB上沿'] = f'+4 (BB={_bb_pct_v3:.2f} 牛市上沿PF=1.414)'
    elif not _is_long_signal and _bb_pct_v3 < 0.10 and any(r in _regime_upper for r in _bear_regimes) and not _direction_block and score > 0:
        score = min(int(score) + 4, 175)
        breakdown['N12_BB下沿'] = f'+4 (BB={_bb_pct_v3:.2f} 熊市下沿PF=1.263)'


    # [UP-FIX-SOL-BNB] 根因修复注入 (2026-05-26 诊断)
    # ─────────────────────────────────────────────
    # FIX-1: 极低波动率假牛市惩罚（精确版v2）
    _atr_pct_val = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    if ('BULL_TREND' in _regime_upper and signal_dir == 'LONG'
            and _atr_pct_val < 0.005 and not _direction_block and score > 0):
        score = int(score * 0.88)
        breakdown['FIX1_假牛市'] = f'×0.88 (ATR_pct={_atr_pct_val:.4f} 极低波动假趋势)'

    # FIX-2: CHOP超卖<25做空惩罚（精确版v2）
    _rsi_chop = float(ms.get('rsi_1h', 50) if ms else 50)
    if ('CHOP' in _regime_upper and not _is_long_signal
            and _rsi_chop < 25 and not _direction_block and score > 0):
        score = int(score * 0.88)
        breakdown['FIX2_CHOP追空'] = f'×0.88 (CHOP RSI={_rsi_chop:.0f}<25 超卖追空)'


    # [UP-NODE-v4] 梵天大脑v4注入
    # ─────────────────────────────────────────────────────
    _atr_v4 = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    _rsi_v4 = float(ms.get('rsi_1h', 50) if ms else 50)
    _is_long_v4 = (signal_dir == 'LONG')

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 ATR体制过滤器] N16完整版 — 基于 N16_atr_layers 铁证
    # CHOP 0.005~0.015最优(PF=1.44~1.98) | BULL_TREND(牛市趋势) <0.010禁区(PF=0.567)
    # ══════════════════════════════════════════════════════════════
    _atr_regime_tag = ''
    if 'BULL_TREND' in _regime_upper:
        # BULL_TREND(牛市趋势) ATR禁区：<0.010 PF=0.567（铁证）
        if _atr_v4 < 0.010 and not _direction_block and score > 0:
            score = int(score * 0.80)
            _atr_regime_tag = f'N16_ATR禁区 ×0.80 (BULL ATR={_atr_v4:.4f}<0.010, PF=0.567)'
        # BULL_TREND(牛市趋势) ATR黄金区：0.010~0.015
        elif 0.010 <= _atr_v4 <= 0.015 and _is_long_v4 and not _direction_block and score > 0:
            score = min(int(score * 1.05), 175)
            _atr_regime_tag = f'N16_ATR黄金 ×1.05 (BULL ATR={_atr_v4:.4f} PF=1.087)'
    elif 'CHOP' in _regime_upper:
        # CHOP最优区：0.005~0.015 PF=1.44~1.98
        if 0.005 <= _atr_v4 <= 0.015 and not _direction_block and score > 0:
            bonus = int((1.98 - max(0, (_atr_v4 - 0.005) / 0.010)) * 2)  # 动态加分
            score = min(score + bonus, 175)
            _atr_regime_tag = f'N16_CHOP优区 +{bonus} (ATR={_atr_v4:.4f} PF≈1.5+)'
        # CHOP大ATR区：>0.015 PF=1.013接近无效
        elif _atr_v4 > 0.020 and not _direction_block and score > 0:
            score = int(score * 0.90)
            _atr_regime_tag = f'N16_CHOP大ATR ×0.90 (ATR={_atr_v4:.4f}>0.020)'
    elif 'BEAR' in _regime_upper:
        # BEAR体制 ATR有效区：0.007~0.025
        if _atr_v4 < 0.007 and not _direction_block and score > 0:
            score = int(score * 0.88)
            _atr_regime_tag = f'N16_BEAR低ATR ×0.88 (ATR={_atr_v4:.4f}<0.007)'
    if _atr_regime_tag:
        breakdown['N16_ATR体制'] = _atr_regime_tag

    # N14: 体制切换时机强化 v2 [设计院P0b封印 2026-06-27]
    # 达摩院铁证：5~10min黄金窗口 PF=1.625，15~25min死亡窗口 PF=0.81
    try:
        import json as _j14, time as _t14
        _dm14 = _j14.loads(open('data/dharma_runtime.json').read())
        _rt14 = _dm14.get('regime_timing', {})
        _rss14_path = __import__('pathlib').Path('data/regime_switch_state.json')
        _n14_delta = 0
        _n14_label = ''
        if _rss14_path.exists():
            _rss14 = _j14.loads(_rss14_path.read_text())
            _last_switch = _rss14.get('last_switch_ts', 0)
            _cur_regime14 = _rss14.get('current_regime', '')
            _dist_min = (_t14.time() - _last_switch) / 60 if _last_switch else 9999
            # 匹配达摩院时段矩阵
            for _window, _wdata in _rt14.items():
                if '~' not in str(_window): continue
                try:
                    _wlo, _whi = [float(x) for x in str(_window).split('~')]
                    if _wlo <= _dist_min < _whi:
                        _n14_delta = int(_wdata.get('delta', 0))
                        _n14_label = _wdata.get('label', '')
                        break
                except Exception as _e:
                        if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                            print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]
        # 当无regime_switch_state时，保d原 N14逻辑
        elif 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42:
            _n14_delta = 5
            _n14_label = '熊市边界早鸟(fallback)'
        if _n14_delta != 0:
            score = max(0, min(int(score) + _n14_delta, 175))
            breakdown['N14_体制切换时机'] = f'{_n14_delta:+d} ({_n14_label} dist={_dist_min if "_dist_min" in dir() else "?":.0f}min PF={_rt14.get(str(int(_dist_min))+"~"+str(int(_dist_min)+5),{}).get("pf","?")})'
            print(f'[N14-Timing] {_sym}: {_n14_delta:+d}分 {_n14_label}')
    except Exception:
        # 安全回退：保留原 N14逻辑
        if 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42 and _atr_v4 > 0.012 and not _direction_block and score > 0:
            score = min(int(score) + 5, 175)
            breakdown['N14_熊转边界'] = '+5 (熊市边界早鸟 PF=1.625)'

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 N15评分分层仓位映射] — 基于 N15_kelly 铁证
    # 150~160分: PF=1.538 Calmar=5.16（最优） | 130~140: PF=1.02（噪声）
    # ══════════════════════════════════════════════════════════════
    _score_tier_tag = ''
    if score >= 165:
        _kelly_tier = 'S+';  _pos_tier = 0.08  # 极高分：最大仓位
        _score_tier_tag = f'N15_S+层({score}分) pos={_pos_tier:.0%}'
    elif score >= 158:
        _kelly_tier = 'S';   _pos_tier = 0.065  # S1标准仓
        _score_tier_tag = f'N15_S层({score}分) pos={_pos_tier:.0%}'
    elif score >= 150:
        _kelly_tier = 'S2';  _pos_tier = 0.05   # [武曲OOS✅] S2层实盘 WR=66.7% PF=3.575 n=72（实盘运行样本，非离线训练样本，待积累至n≥500增强可信度）
        _score_tier_tag = f'N15_S2层({score}分) pos={_pos_tier:.0%} [武曲认证]'
    elif score >= 130:
        _kelly_tier = 'B';   _pos_tier = 0.02   # 极轻仓观察
        _score_tier_tag = f'N15_B层({score}分) pos={_pos_tier:.0%}'
    else:
        _kelly_tier = 'C';   _pos_tier = 0.0
    # 将仓位分级注入 extra_data 供执行层使用
    if extra_data is not None and isinstance(extra_data, dict):
        extra_data['score_tier'] = _kelly_tier
        extra_data['score_pos']  = _pos_tier
    breakdown['N15_分层仓位'] = _score_tier_tag if _score_tier_tag else f'N15_C层({score}分) 不执行'

    # ── [GAP2 仓位管理器 2026-06-03] 中仓解锁 + 动态仓位 ─────────────────────
    # 武曲Paper 200笔+WR≥75% → 倍数1.5x | 3连胜 → 倍数2.0x
    try:
        import sys as _pm_sys, os as _pm_os
        _pm_root = _pm_os.path.dirname(_pm_os.path.dirname(_pm_os.path.abspath(__file__)))
        if _pm_root not in _pm_sys.path:
            _pm_sys.path.insert(0, _pm_root)
        from scripts.position_manager import get_position_multiplier as _get_pm
        _pm_mult = _get_pm()
        if _pm_mult > 1.0 and _pos_tier > 0:
            _pos_tier_adjusted = round(_pos_tier * _pm_mult, 4)
            if extra_data is not None and isinstance(extra_data, dict):
                extra_data['score_pos']   = _pos_tier_adjusted
                extra_data['pos_mult']    = _pm_mult
            breakdown['N15_仓位倍数'] = (
                f'×{_pm_mult} → pos={_pos_tier_adjusted:.1%} '
                f'({"中仓已解锁" if _pm_mult==1.5 else "连胜加仓"})'
            )
    except Exception as _pm_e:
        pass   # 静默失败，不影响主流程
    # ── [END 仓位管理器] ──────────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 M09] 品种×维度权重修正层
    # 来源：full_universe_backtest dim_contrib铁证
    # BTC谐波-0.381/宏观-0.256清零 | ETH背离+0.277→×2.0 | SOL期权-0.093→×0.5
    # 已通过DharmaBus总线写入，此处读取并追溯调整评分
    # 设计院升级 2026-06-27: 无score下限限制，所有体制均触发
    # ══════════════════════════════════════════════════════════════
    try:
        import os as _os2, sys as _sys2
        _bus_dir2 = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), '..')
        if _bus_dir2 not in _sys2.path: _sys2.path.insert(0, _bus_dir2)
        from dharma.dharma_bus import get_dim_weight as _get_dw
        _m09_dims = {
            '关键位精确度': breakdown.get('关键位精确度', 0),
            '形态成熟度':   breakdown.get('形态成熟度',   0),
            '清算/OI':      breakdown.get('清算/OI',      0),
            '谐波+多周期':  breakdown.get('谐波+多周期',  0),
            'L2+贝叶斯+宏观': breakdown.get('L2+贝叶斯+宏观', 0),
            '量能验证':     breakdown.get('量能验证',     0),
            '动量背离':     breakdown.get('动量背离',     0),
            '期权+订单流':  breakdown.get('期权+订单流',  0),
            'LSTM+NLP情绪': breakdown.get('LSTM+NLP情绪', 0),
        }
        _m09_delta = 0
        _m09_log = []
        for _dim, _orig in _m09_dims.items():
            if _orig <= 0: continue
            _w = _get_dw(_sym, _dim)
            if _w == 1.0: continue
            _adjusted = round(_orig * _w)
            _delta = _adjusted - _orig
            _m09_delta += _delta
            if abs(_delta) >= 1:
                _m09_log.append(f'{_dim}:{_orig}→{_adjusted}(×{_w})')
                # 同步更新breakdown实际分数字段
                breakdown[_dim] = _adjusted
        if _m09_delta != 0:
            score = max(0, min(score + _m09_delta, 175))
            breakdown['M09_维度权重'] = f'Δ{_m09_delta:+d}分 [{" | ".join(_m09_log[:4])}]'
            print(f'[M09-DimWeight] {_sym}: {_m09_delta:+d}分 | {" | ".join(_m09_log)}')
    except Exception as _e09:
        pass
    # ══════════════════════════════════════════════════════════════

    # ─── [设计院 2026-06-30 P1-C] WICK_HUNTER 第10因子 ──────────────────────
    # 根因：系统缺乏15m插针信号识别，58,850/58,888极端下影线未被捕捉
    # 铁证：22:45 L:58888 体/影比=0.12（教科书级插针），02:00 L:58850 振幅644
    # 逻辑：下影线主导（>实体+上影线×1.5）+ 触碰近期低点支撑 + 收盘收复 → +20分
    # fail-safe：异常静默，不阻断主流程
    try:
        _k15m = extra_data.get('_klines_15m') if extra_data else None
        if _k15m and len(_k15m.get('c', [])) >= 5:
            _wh_o = _k15m['o'][-1]
            _wh_h = _k15m['h'][-1]
            _wh_l = _k15m['l'][-1]
            _wh_c = _k15m['c'][-1]
            _wh_body  = abs(_wh_c - _wh_o)
            _wh_upper = _wh_h - max(_wh_o, _wh_c)
            _wh_lower = min(_wh_o, _wh_c) - _wh_l
            _wh_total = _wh_h - _wh_l
            _wh_score = 0
            if _wh_total > 0:
                if signal_dir == 'LONG':
                    # 条件1：下影线主导（>实体+上影线的1.5倍）
                    if _wh_lower > (_wh_body + _wh_upper) * 1.5:
                        _support_ref = min(_k15m['l'][-20:]) if len(_k15m['l']) >= 20 else _wh_l
                        # 条件2：触碰近期支撑（±0.3%）
                        if _wh_l <= _support_ref * 1.003:
                            # 条件3：收盘收复支撑上方
                            if _wh_c > _support_ref * 1.004:
                                # [达摩院验证 2026-06-30] LONG插针需额外满足:
                                # 体/影比<0.25(防假插针) + DISCOUNT区(系数由区间路由提供)
                                _in_discount = breakdown.get('区间Zone_v2', '').startswith('DISCOUNT')
                                _extreme_wick = (_wh_body / _wh_total < 0.25)
                                if _in_discount and _extreme_wick:
                                    _wh_score = 25 if (_wh_body / _wh_total < 0.15) else 20
                                    breakdown['WICK_HUNTER_LONG'] = f'+{_wh_score}(下影{_wh_lower:.0f}pts 体影比{_wh_body/_wh_total:.2f} DISCOUNT区联合)'
                                elif _extreme_wick:
                                    # 非DISCOUNT区但是极端插针，小加分
                                    _wh_score = 10
                                    breakdown['WICK_HUNTER_LONG_WEAK'] = f'+{_wh_score}(下影 体影比{_wh_body/_wh_total:.2f} 非DISCOUNT小加分)'
                elif signal_dir == 'SHORT':
                    # 条件1：上影线主导（>实体+下影线的2.0倍）
                    if _wh_upper > (_wh_body + _wh_lower) * 2.0:
                        _resist_ref = max(_k15m['h'][-20:]) if len(_k15m['h']) >= 20 else _wh_h
                        # 条件2：触碰近期阻力（±0.3%）
                        if _wh_h >= _resist_ref * 0.997:
                            # 条件3：收盘回落阻力下方
                            if _wh_c < _resist_ref * 0.996:
                                _wh_score = 15
                                breakdown['WICK_HUNTER_SHORT'] = f'+{_wh_score}(上影{_wh_upper:.0f}pts 体影比{_wh_body/_wh_total:.2f})'
            if _wh_score > 0:
                score += _wh_score
    except Exception:
        pass
    # ─── [P1-C END] ──────────────────────────────────────────────────────────

    # ══ [设计院 2026-06-30 全量接入 N10-A] CVD 订单流因子 ════════════════════
    # 模块: cvd_engine · 订单流核心指标，多周期CVD累积成交量差
    # 达摩院铁证：CVD顺势+15分 / 逆势-10分
    try:
        from cvd_engine import cvd_score_for_signal as _cvd_fn
        _cvd_score, _cvd_notes = _cvd_fn(ms.get('symbol', ''), signal_dir)
        if _cvd_score != 0:
            score += _cvd_score
            breakdown['CVD订单流'] = f'{_cvd_score:+d} ' + ('; '.join(_cvd_notes[:2]) if _cvd_notes else '')
    except Exception:
        pass
    # ══ [N10-A END] ══════════════════════════════════════════════════════════

    # ══ [设计院 2026-06-30 全量接入 N10-B] 实时清算流 因子 ════════════════════
    # 模块: realtime_liq_tracker · 追踪近5分钟三所清算流方向
    # 逻辑：同向清算涌入（如大量多单被爆仓时做空）→ 加分
    try:
        from realtime_liq_tracker import get_liq_score as _liq_score_fn
        _liq_adj, _liq_desc = _liq_score_fn(ms.get('symbol', ''), signal_dir)
        if _liq_adj != 0:
            score += _liq_adj
            breakdown['清算流追踪'] = f'{_liq_adj:+d} {_liq_desc[:50]}'
    except Exception:
        pass
    # ══ [N10-B END] ══════════════════════════════════════════════════════════

    # [v13.0] 单一化输出裁决：评分决定唯一行动，不再并列多方案
    # 裁决规则：评分主导， R:R 在 analyze() 层做最终覆盖
    if score >= 155:
        grade = '🔴神级';  kelly_mult = 2.0;  action = 'ENTER_FULL'  # [N18] 顶级信号全仓
    elif score >= 140:
        grade = '🟠极强';   kelly_mult = 1.5;  action = 'ENTER'       # [N18] 高分强信号
    elif score >= 120:
        grade = '🟡强';    kelly_mult = 1.0;  action = 'ENTER'        # [N18] 标准信号
    elif score >= 80:
        grade = '🔵中等';   kelly_mult = 0.5;  action = 'WATCH'
    else:
        grade = '⚫放弃';   kelly_mult = 0.0;  action = 'SKIP'

    return {
        'total':      score,
        'max':        150,
        'grade':      grade,
        'grade_num':  score,   # [设计院 2026-06-30 G修复] brahma_analyze.py期期得此字段，补入整数评分
        'kelly_mult': kelly_mult,
        'action':     action,   # 注意：若params.valid=False，analyze()会覆盖此字段
        'breakdown':  breakdown,
    }

# ═══════════════════════════════════════════════════════════════
# 精确交易参数生成
# ═══════════════════════════════════════════════════════════════

def _nearest_swing_above(swing_highs: list, entry: float) -> float:
    """找到入场价上方最近的摆动高点（用于做空止损）"""
    candidates = [v for v in swing_highs if v > entry]
    return min(candidates) if candidates else entry * 1.015

def _nearest_swing_below(swing_lows: list, entry: float) -> float:
    """找到入场价下方最近的摆动低点（用于做多止损）"""
    candidates = [v for v in swing_lows if v < entry]
    return max(candidates) if candidates else entry * 0.985

def calc_trade_params(ms: dict, smc: dict, signal_dir: str,
                      mtf_result: dict = None) -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _ctp_entry(ms, smc, signal_dir, mtf_result)
    raise ImportError('brahma_core_entry not available')


def rebase_params(params: dict, current_price: float,
                  symbol: str = '') -> dict:
    """[已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名"""
    if _ENTRY_OK:
        return _rbp_entry(params, current_price, symbol)
    raise ImportError('brahma_core_entry not available')



# ═══════════════════════════════════════════════════════════════
# 主分析入口
# ═══════════════════════════════════════════════════════════════

def analyze(symbol: str, signal_dir: str = None, deep: bool = False) -> dict:
    """
    梵天大脑主入口
    symbol:     交易对（如 ETHUSDT）
    signal_dir: 强制方向（LONG/SHORT），None=自动判断
    deep:       True=深度分析模式，跳过方向中性快速退出，返回完整数据
    """
    t0 = time.time()
    _sym = symbol.upper()
    print(f'[BrahmaBrain] 开始分析 {_sym} dir={signal_dir or "AUTO"}')

    # ══ [设计院 2026-06-30 P3] BrahmaBus 数据总线初始化 ══════════════════════
    # 模块: brahma_bus · TTL缓存单例，0.01ms命中 vs HTTP 50ms
    # 仅初始化，后续模块可通过 BrahmaBus() 直接获取缓存数据
    try:
        from brahma_bus import BrahmaBus as _BBus
        _bus = _BBus()
        _bus.invalidate(_sym)   # 强制刷新当前标的缓存
    except Exception:
        pass
    # ══ [BrahmaBus END] ════════════════════════════════════════════════════════

    # [价格修复 v1.1] analyze()入口：强制刷新实时价格到live_prices.json，确保降级链拿到最新价
    # 设计院 2026-06-29 · 根因：ws_guardian停运时live_prices.json超期→降级到ticker缓存价
    try:
        import sys as _lpf_sys, os as _lpf_os
        _lpf_base = _lpf_os.path.dirname(_lpf_os.path.abspath(__file__))
        if _lpf_base not in _lpf_sys.path:
            _lpf_sys.path.insert(0, _lpf_base)
        from live_price_feed import bulk_update_from_api as _lpf_bulk
        _lpf_bulk([_sym])
        print(f'[PriceFix] {_sym} 入口强制刷新价格 ✅')
    except Exception as _lpf_e:
        print(f'[PriceFix] 价格刷新异常（不阻断）: {_lpf_e}')

    # Step 1: 市场状态分析
    ms = ms_analyze(symbol)
    if 'error' in ms:
        print(f'[BrahmaBrain] ✗ {_sym} ms_analyze失败: {ms["error"]}')
        return {'error': ms['error']}

    # ── [设计院 2026-06-30 P0-A] RegimeStateMachine 体制防抖接入 ────────
    # 根因：brahma_core直接消费ms_analyze()原始体制输出，单根4H K棒噪声即触发切换
    # 修复：经过确认窗口(2~3根4H)+滞后保护+状态持久化，过滤伪切换
    # fail-safe：异常时不阻断主流程，维持原始体制
    try:
        import sys as _rsm_sys, os as _rsm_os
        _rsm_path = _rsm_os.path.dirname(_rsm_os.path.abspath(__file__))
        if _rsm_path not in _rsm_sys.path:
            _rsm_sys.path.insert(0, _rsm_path)
        from regime_state_machine import RegimeStateMachine
        _rsm = RegimeStateMachine(_sym)
        _raw_regime = ms.get('regime', 'CHOP_MID')
        _stable_regime = _rsm.update(_raw_regime)
        if _stable_regime != _raw_regime:
            print(f'[RSM] {_sym} 体制防抖: {_raw_regime}→{_stable_regime}（状态机稳定输出，已过滤伪切换）')
        else:
            print(f'[RSM] {_sym} 体制稳定: {_stable_regime}（无切换）')
        ms['regime'] = _stable_regime
    except Exception as _rsm_e:
        print(f'[RSM] 状态机异常（不阻断，维持原始体制）: {_rsm_e}')
    # ── [P0-A END] ────────────────────────────────────────────────────────

    # ── [因果AI P0-A] Causal Regime Verifier ────────────────────
    # 设计院因果增强 v1.0 · 2026-06-18
    # 在 Step 2 方向确认前，验证当前体制的因果结构是否支持入场
    # fail-safe: 异常时返回默认通过，不阻断主流程
    _causal_v_result = {}
    try:
        import sys as _cv_sys, os as _cv_os
        _cv_root = _cv_os.path.dirname(_cv_os.path.abspath(__file__))
        if _cv_root not in _cv_sys.path:
            _cv_sys.path.insert(0, _cv_root)
        from causal_regime_verifier import verify as _cv_verify
        _cv_regime = ms.get('regime', '?')
        _cv_dir = signal_dir or ms.get('signal_bias', 'SHORT')
        _causal_v_result = _cv_verify(_sym, _cv_regime, _cv_dir, ms, timeout_ms=150)
        _cv_adj = _causal_v_result.get('score_adj', 0)
        _cv_verdict = _causal_v_result.get('verdict', '?')
        if _cv_verdict not in ('STRONG', 'MODERATE'):
            print(f'[CausalVerifier] ⚡ {_sym} verdict={_cv_verdict} conf={_causal_v_result.get("causal_confidence",0):.2f} adj={_cv_adj:+d}')
        pass  # extra_data5c1a672a521d59cb5316Ff0c7ed3679c5b585728_causal_v_result4e2d
    except Exception as _cv_e:
        print(f'[CausalVerifier] ⚠ 异常（不阻断）: {_cv_e}')

    # Step 2: 确定方向
    if signal_dir is None:
        signal_dir = ms['signal_bias']

    # ── [HARD_BLOCK END] ──────────────────────────────────────────────────
    _rcn = {'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_PEAK':'牛市末期','BULL_CORRECTION':'牛市回调','BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期','BEAR_CRASH':'暴跌体制','BEAR_RECOVERY':'熊市反弹','CHOP_HIGH':'高位震荡','CHOP_LOW':'低位震荡','CHOP_MID':'中位震荡','BREAKOUT':'突破体制'}
    _reg_raw = ms.get('regime','?')
    _reg_display = f'{_reg_raw}({_rcn.get(_reg_raw,_reg_raw)})'
    print(f'[BrahmaBrain] {_sym} 体制={_reg_display} 方向={signal_dir} RSI_1H={ms.get("momentum",{}).get("rsi_1h",0):.0f}')
    if signal_dir == 'NEUTRAL':
        print(f'[BrahmaBrain] {_sym} 方向中性，不入场')
        if not deep:
            return {
                'symbol': symbol,
                'signal_dir': 'NEUTRAL',
                'action': '不入场',
                'reason': '三框架方向中性，无共识',
                'summary': ms['summary'],
            }
        # [deep=True] 中性体制下仍继续运行，选择体制最优方向
        from brahma_brain.regime_scorer import score as _rs_fn, _CACHE as _RS_CACHE
        _RS_CACHE.clear()
        _live_reg = _rs_fn(symbol, force=True)
        _live_regime = _live_reg.get('regime','')
        _bear_p = _live_reg.get('bear_prob',0)
        _bull_p = _live_reg.get('bull_prob',0)
        if _bear_p >= _bull_p:
            signal_dir = 'SHORT'
        else:
            signal_dir = 'LONG'
        print(f'[BrahmaBrain][deep] {_sym} 深度模式强制方向={signal_dir}（bear={_bear_p:.1%} bull={_bull_p:.1%}）')

    # Step 3: SMC结构分析
    price = float(ms.get('price', 0))  # [v21.0 fix] MTF路由器需要price变量
    smc = analyze_smc(symbol, signal_dir, '1h', 200)
    # [v21.0 自顶向下 2026-06-08] 补充4H SMC分析 + MTF路由器（自顶向下）
    _smc_4h = {}
    _mtf_result = None  # multi_timeframe_router结果
    try:
        _smc_4h = analyze_smc(symbol, signal_dir, '4h', 60)
        # [v21.0] MTF路由：4H战略区优先，1H确认（自顶向下）
        try:
            from brahma_brain.multi_timeframe_router import route_entry_zone as _mtf_route
            _mtf_result = _mtf_route(symbol, signal_dir, price, smc, _smc_4h)
            _tf_used = _mtf_result.get('timeframe', '1H')
            _tf_warn = _mtf_result.get('warning', '')
            _tf_upgrade = _mtf_result.get('upgrade_reason', '')
            if _tf_used == '4H':
                _mtf_lo = _mtf_result['entry_lo']
                _mtf_hi = _mtf_result['entry_hi']
                print(f'[MTF-v21.0] {symbol} 升级至4H入场区 [{_mtf_lo:.4g}~{_mtf_hi:.4g}] {_tf_upgrade[:60]}')
                if _tf_warn:
                    print(f'[MTF-v21.0] 警告: {_tf_warn[:80]}')
        except Exception as _mtf_err:
            print(f'[MTF-v21.0] 路由器异常（非阻断）: {_mtf_err}')
            _mtf_result = None

        # [旧逻辑兼容] 如果MTF路由未激活，保留原1H→4H降级逻辑
        if _mtf_result is None or _mtf_result.get('timeframe') == '1H':
            if not smc.get('fvg', {}).get('nearest_bear') and not smc.get('order_blocks', {}).get('nearest_bear_ob'):
                _ob4h = _smc_4h.get('order_blocks', {}).get('nearest_bear_ob')
                _fvg4h = _smc_4h.get('fvg', {}).get('nearest_bear')
                if _ob4h:
                    smc['order_blocks']['nearest_bear_ob'] = _ob4h
                    print(f'[SMC-4H] {symbol} 1H无OB，使用4H Bear OB [{_ob4h.get("low",0):.4g}~{_ob4h.get("high",0):.4g}]')
                if _fvg4h:
                    smc['fvg']['nearest_bear'] = _fvg4h
                    print(f'[SMC-4H] {symbol} 1H无FVG，使用4H Bear FVG')
            if not smc.get('fvg', {}).get('nearest_bull') and not smc.get('order_blocks', {}).get('nearest_bull_ob'):
                _ob4h_bull = _smc_4h.get('order_blocks', {}).get('nearest_bull_ob')
                _fvg4h_bull = _smc_4h.get('fvg', {}).get('nearest_bull')
                if _ob4h_bull:
                    smc['order_blocks']['nearest_bull_ob'] = _ob4h_bull
                if _fvg4h_bull:
                    smc['fvg']['nearest_bull'] = _fvg4h_bull
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

    # ══════════════════════════════════════════════════════════
    # [达摩院v12.9c 修订 设计院 2026-05-30] FVG 条件升级
    # 原逻辑：无FVG → 硬拒绝（导致OB/Fib降级路径被绕过）
    # 新逻辑：无「有效方向FVG（gap>0.3%且在正确方向）」→ 降级，不硬拒绝
    #         但无FVG且无OB且Fib也不在价格外侧合理距离 → 才硬拒绝（真正无结构）
    # 修复原因：FVG阈值从0.1%提升到0.3%后，很多情况nearest_bear=None
    #           但OB/Fib降级是合法的入场参数来源，不应拒绝
    # ══════════════════════════════════════════════════════════
    _fvg_hard = smc.get('fvg', {})
    _fvg_exists = (
        (_fvg_hard.get('nearest_bull') is not None) if signal_dir == 'LONG'
        else (_fvg_hard.get('nearest_bear') is not None)
    )
    _ob_hard = smc.get('order_blocks', {})
    _ob_exists = (
        (_ob_hard.get('nearest_bull_ob') is not None) if signal_dir == 'LONG'
        else (_ob_hard.get('nearest_bear_ob') is not None)
    )
    # 只有FVG和OB都没有才是真正「无结构」，Fib始终存在所以允许降级
    if not _fvg_exists and not _ob_exists:
        print(f'[BrahmaBrain] ⚠️ FVG/OB均无 {_sym}: {signal_dir}方向无SMC结构 → 降级用Fib入场')
        # 不拒绝，继续走Fib降级路径（calc_trade_params会处理）
    elif not _fvg_exists:
        print(f'[BrahmaBrain] ℹ️ {_sym} 无有效FVG，使用OB入场')

    # Step 4: Phase 2 额外引擎
    k1h = klines_to_ohlcv(get_klines(symbol, '1h', 200))
    k4h = klines_to_ohlcv(get_klines(symbol, '4h', 200))
    extra_data = {
        '_symbol': _sym,
        '_k4h_closes':  list(k4h['c'][-20:]) if k4h and k4h.get('c') else [],
        '_k4h_volumes': list(k4h['v'][-20:]) if k4h and k4h.get('v') else [],
        '_klines_1h':   k1h,  # [v25.1 2026-06-14] s20/s21/s22初始化即提前注入，避免流程中断导致三个维度全部归零
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
        print(f'[BrahmaBrain] CoinGlass: F&G={_cg_snap["fear_greed"]["value"]} '
              f'OI={_cg_snap["oi_momentum"]["oi_change_pct"]:+.2f}% '
              f'onchain={_cg_snap["onchain_score"]:+d}')
    except Exception as _cg_e:
        # [设计院 2026-05-30] 降级链：尝试备用数据源
        try:
            from coinglass_fallback import get_full_snapshot_with_fallback as _cg_fb
            _cg_snap_fb = _cg_fb(_sym)
            extra_data['coinglass']     = _cg_snap_fb
            extra_data['fear_greed']    = _cg_snap_fb['fear_greed']
            extra_data['onchain_score'] = _cg_snap_fb.get('onchain_score', 0)
            _src = _cg_snap_fb['fear_greed'].get('source','?')
            print(f'[BrahmaBrain] CoinGlass降级[{_src}]: F&G={_cg_snap_fb["fear_greed"]["value"]} FR={_cg_snap_fb["funding_rate"]:+.4f}%')
        except Exception as _fb_e:
            print(f'[BrahmaBrain] CoinGlass+降级均失败: {_cg_e}')
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
        print(f'[BrahmaBrain] LiqScan: 散户多{_liq_snap["long_pct"]:.0f}% 大户多{_liq_snap["top_long_pct"]:.0f}% 偏向={_liq_snap["liq_bias"]}')
    except Exception as _liq_e:
        print(f'[BrahmaBrain] LiqScan跳过: {_liq_e}')
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
            print(f'[D03-v3] 实训修正: 时间惩罚={-_tp} 体制调整={-_rp} 量缩奖励=+{_vb} 最终分={best_s}')
        # [v25.2 2026-06-16 P1] 1H+4H双重背离共振加分
        # 离线铁证: 1H信号WR=58% vs 15M WR=52.8%（+5.2%）
        # 当1H和4H背离评分都有效时（各≥6），双重共振+3分
        if s_1h >= 6 and s_4h >= 6:
            _dual_div_bonus = 3
            extra_data['divergence']['score'] = min(best_s + _dual_div_bonus, 18)
            extra_data['divergence']['score_long'] = min(extra_data['divergence'].get('score_long',0) + _dual_div_bonus, 18) if signal_dir=='LONG' else extra_data['divergence'].get('score_long',0)
            extra_data['divergence']['score_short'] = min(extra_data['divergence'].get('score_short',0) + _dual_div_bonus, 18) if signal_dir=='SHORT' else extra_data['divergence'].get('score_short',0)
            print(f'[v25.2-P1] 1H+4H双重背离共振+3分: s_1h={s_1h} s_4h={s_4h}')
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

    with _TPE(max_workers=4) as _ex:
        _f_oc  = _ex.submit(_run_onchain)
        _f_pt  = _ex.submit(_run_pattern)
        _f_of  = _ex.submit(_run_orderflow)
        _f_mc  = _ex.submit(_run_macro)
        try: extra_data['onchain'] = _f_oc.result(timeout=8)
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]
        try:
            _pt = _f_pt.result(timeout=8)
            if _pt: extra_data['pattern'] = _pt
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]
        try:
            _of = _f_of.result(timeout=8)
            if _of: extra_data['order_flow'] = _of
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]
        try: extra_data['macro'] = _f_mc.result(timeout=8)
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

    # P0-NEW: 谐波形态引擎（4H + 日线双重扫描）
    try:
        if _HARMONIC_OK:
            # [P1-A audit-fix 2026-06-17 DISABLED] h_res = {'pattern': None, 'score': 0, 'prz': None}  # [DEAD: harmonic_engine removed]
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
                print(f'[HarmonicEngine] {symbol} {signal_dir}: {h_res.get("patterns",[])} score={h_res["score"]}')
    except Exception as _e:
        extra_data['harmonic_err'] = str(_e)
        try:
            from nerve_system.nerve_emitter import get_nerve as _gn; _gn('brahma').emit('ENGINE_FAIL', {'engine':'harmonic','err':str(_e)[:80]})
        except Exception as _e:
                if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

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
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

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
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

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
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

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
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

    # [s_cross 2026-07-01] 跨所FR+Basis（设计院三项外部路由落地）
    try:
        from cross_market_engine import get_cross_fr_basis as _get_cfb
        _cfb = _get_cfb(symbol)
        extra_data['cross_fr_basis'] = _cfb
        if _cfb.get('score_adj', 0) != 0:
            print(f'[s_cross-FR+Basis] {symbol} {signal_dir}: {_cfb["score_adj"]:+d} | {_cfb["note"]}')
    except Exception:
        pass

    # [s_options 2026-07-01] Deribit P/C OI
    try:
        from cross_market_engine import get_deribit_pc as _get_dpc
        _dpc = _get_dpc(symbol)
        extra_data['deribit_pc'] = _dpc
        if _dpc.get('score_adj', 0) != 0:
            print(f'[s_options-PC] {symbol} {signal_dir}: {_dpc["score_adj"]:+d} | {_dpc["note"]}')
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
                    print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

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
                print(f'[VolExh] {symbol} {signal_dir}: {_v_res["exhaustion_level"]} score={_v_res["score"]} {_v_res["notes"][:1]}')
    except Exception as _e:
        extra_data['vol_exh_err'] = str(_e)[:80]

    # MULTITF-DIV: 多周期背离共振引擎
    try:
        if _MULTITF_DIV_OK:
            _md_res = _multitf_div_score(symbol, signal_dir)
            extra_data['multitf_div'] = _md_res
            if _md_res.get('resonance', 'NONE') not in ('NONE',):
                print(f'[MultiTFDiv] {symbol} {signal_dir}: {_md_res["resonance"]} score={_md_res["score"]}')
    except Exception as _e:
        extra_data['multitf_div_err'] = str(_e)[:80]

    # ─── Phase A: 新引擎接入 ─────────────────────────────────────────
    # A1: L2订单簿深度
    try:
        import sys as _sys_ob, os as _os_ob
        _bd = _os_ob.path.join(_os_ob.path.dirname(_os_ob.path.abspath(__file__)))
        if _bd not in _sys_ob.path: _sys_ob.path.insert(0, _bd)
        from orderbook_engine import analyze_orderbook as _ob_fn
        # [P1-A audit-fix 2026-06-17 DISABLED] extra_data['orderbook'] = {}  # [DEAD: online_bayes removed]
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
        # [P1-A audit-fix 2026-06-17 DISABLED] extra_data['xgboost'] = {}  # [DEAD: xgboost_engine removed]
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
        # [P1-A audit-fix 2026-06-17 DISABLED] extra_data['slippage'] = {'slippage_pct': 0}  # [DEAD: slippage_model removed]
    except Exception as _e:
        extra_data['slippage_err'] = str(_e)[:80]

    # B4: 链上大单 WS/REST
    try:
        from onchain_ws import analyze as _ws_fn
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
        # [P1-A audit-fix 2026-06-17 DISABLED] extra_data['lstm'] = {'score': 0}  # [DEAD: lstm_engine removed]
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
        extra_data['sentiment_nlp'] = _sm.analyze(symbol, signal_dir)
    except Exception as _e:
        extra_data['sentiment_nlp_err'] = str(_e)[:80]

    # Step 5: 共振评分
    cf = confluence_score(ms, smc, signal_dir, extra_data)

    # ── [因果AI P0-B] Counterfactual Score Check ───────────────
    # 设计院因果增强 v1.0 · 2026-06-18
    # 对 score ≥ 100 的信号执行维度因果归因，识别相关性掃车维度
    # fail-safe: 异常不阻断主流程
    try:
        import sys as _cfc_sys, os as _cfc_os
        _cfc_root = _cfc_os.path.dirname(_cfc_os.path.abspath(__file__))
        if _cfc_root not in _cfc_sys.path:
            _cfc_sys.path.insert(0, _cfc_root)
        from counterfactual_score_check import check as _cfc_check
        _cf_score = float(cf.get('score', 0) or 0)
        if _cf_score >= 100:
            _cfc_result = _cfc_check(cf, signal_dir, ms.get('regime', ''), timeout_ms=80)
            _cfc_adj = _cfc_result.get('score_adj', 0)
            _cfc_verdict = _cfc_result.get('verdict', 'NEUTRAL')
            if _cfc_adj != 0:
                cf['score'] = _cf_score + _cfc_adj
                cf.setdefault('breakdown', {})['_counterfactual'] = (
                    f'{_cfc_adj:+d}(因果归因:{_cfc_verdict} '
                    f'因果维度{_cfc_result.get("causal_ratio",0):.0%})'
                )
                print(f'[CounterfactualCheck] {_sym} {_cfc_verdict} adj={_cfc_adj:+d} '
                      f'score:{_cf_score:.0f}→{cf["score"]:.0f} '
                      f'因果{_cfc_result.get("causal_ratio",0):.0%}/相关{_cfc_result.get("spurious_ratio",0):.0%}')
            extra_data['counterfactual'] = _cfc_result
    except Exception as _cfc_e:
        print(f'[CounterfactualCheck] ⚠ 异常（不阻断）: {_cfc_e}')

    # ── Causal Verifier 评分叠加 ─────────────────────────────
    # 将 P0-A 的 score_adj 运用到最终评分
    _cv_adj = extra_data.get('causal_verifier', {}).get('score_adj', 0)
    if _cv_adj != 0:
        _cf_score_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _cf_score_pre + _cv_adj
        cf.setdefault('breakdown', {})['_causal_regime'] = (
            f'{_cv_adj:+d}(体制因果:{extra_data.get("causal_verifier",{}).get("verdict","?")} '
            f'conf={extra_data.get("causal_verifier",{}).get("causal_confidence",0):.2f})'
        )
        print(f'[CausalVerifier] {_sym} 评分叠加: {_cf_score_pre:.0f}→{cf["score"]:.0f} ({_cv_adj:+d})')

    # ── [s_cross 2026-07-01] 跨所FR+Basis 评分叠加 ──────────────────
    _cfb_adj = extra_data.get('cross_fr_basis', {}).get('score_adj', 0)
    if signal_dir != 'SHORT':
        _cfb_adj = -_cfb_adj  # 做多时反转：FR高时做多不利
    if _cfb_adj != 0:
        _cfb_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _cfb_pre + _cfb_adj
        cf.setdefault('breakdown', {})['_cross_fr_basis'] = (
            f'{_cfb_adj:+d}(FR均值={extra_data.get("cross_fr_basis",{}).get("fr_avg",0):.4f}% '
            f'Basis={extra_data.get("cross_fr_basis",{}).get("basis_pct",0):.3f}%)'
        )
        print(f'[s_cross] {_sym} 评分叠加: {_cfb_pre:.0f}→{cf["score"]:.0f} ({_cfb_adj:+d})')

    # ── [s_options 2026-07-01] Deribit P/C OI 评分叠加 ──────────────────
    _dpc_adj = extra_data.get('deribit_pc', {}).get('score_adj', 0)
    if signal_dir != 'SHORT':
        _dpc_adj = -_dpc_adj  # 做多时反转
    if _dpc_adj != 0:
        _dpc_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _dpc_pre + _dpc_adj
        cf.setdefault('breakdown', {})['_options_pc'] = (
            f'{_dpc_adj:+d}(P/C={extra_data.get("deribit_pc",{}).get("pc_oi_ratio",0):.2f} '
            f'{extra_data.get("deribit_pc",{}).get("signal","")})'
        )
        print(f'[s_options] {_sym} Deribit叠加: {_dpc_pre:.0f}→{cf["score"]:.0f} ({_dpc_adj:+d})')

    # ── [s_macro_v2 2026-07-01] DXY实时+纳指+BTC.D 评分叠加 ────────────
    _mv2_adj = extra_data.get('macro_v2', {}).get('score_addon', 0)
    if _mv2_adj != 0:
        _mv2_pre = float(cf.get('score', 0) or 0)
        cf['score'] = _mv2_pre + _mv2_adj
        cf.setdefault('breakdown', {})['_macro_v2'] = (
            f'{_mv2_adj:+d}(' + ' | '.join(extra_data.get('macro_v2', {}).get('notes', [])[:2]) + ')'
        )
        print(f'[s_macro_v2] {_sym} 宏观叠加: {_mv2_pre:.0f}→{cf["score"]:.0f} ({_mv2_adj:+d})')

    # ── [s_smart_money 2026-07-01] 聊明錢流向分析 ───────────────────────
    # Glassnode盲区替代方案：大户持仓比+大户-散户背离 = 巨鲸流向代理指标
    try:
        from smart_money_engine import get_smart_money_signal as _gsms
        _sm = _gsms(_sym)
        extra_data['smart_money'] = _sm
        _sm_adj = _sm.get('score_adj', 0)
        if signal_dir != 'SHORT':
            _sm_adj = -_sm_adj  # 做多时反转
        if _sm_adj != 0 and _sm.get('confidence', 0) >= 0.5:
            _sm_pre = float(cf.get('score', 0) or 0)
            cf['score'] = _sm_pre + _sm_adj
            cf.setdefault('breakdown', {})['_smart_money'] = (
                f'{_sm_adj:+d}(大户持仓={_sm.get("big_pos_long",0.5):.0%} '
                f'背离={_sm.get("whale_retail_gap",0):+.3f})'
            )
            print(f'[s_smart] {_sym} 聊明錢: {_sm_pre:.0f}→{cf["score"]:.0f} ({_sm_adj:+d}) | {_sm.get("note","")[:60]}')
    except Exception:
        pass
    params = calc_trade_params(ms, smc, signal_dir, mtf_result=_mtf_result)

    # [N17专项] 标的专属SL/TP参数覆盖
    # [WFV-v4.0 2026-05-28] 达摩院高强度训练 200轮Bootstrap认证
    # 全局冠军: RSI<20/>>85 SL=0.6x TP=4.0x  核心OOS PF=1.347 Bootstrap=MEDIUM
    # [N17专项] 标的专属SL/TP参数覆盖
    # [WFV-v4.0 2026-05-28] 达摩院高强度训练 200轮Bootstrap认证
    # 全局冠军: RSI<20/>>85 SL=0.6x TP=4.0x  核心OOS PF=1.347 Bootstrap=MEDIUM
    # [M07时间效应 ERR-012 2026-05-30] 10万次训练M07节点认证
    # 最佳时段(UTC): 18H/22H/11H/7H → EV高40%+  最差月份: 8/9月 → 降权
    # 最佳交易日: 周四/周三/周一
    import datetime as _dt_m07
    _now_m07 = _dt_m07.datetime.utcnow()
    _hour_m07 = _now_m07.hour
    _wday_m07 = _now_m07.weekday()  # 0=Mon, 3=Thu
    _month_m07 = _now_m07.month
    _time_mult = 1.0
    _time_tag = ''
    # ── M07/M06 后置修正：操作 cf['total'] 和 cf['breakdown']（正确作用域）
    # 最佳时段加权 +5分
    if _hour_m07 in (18, 22, 11, 7, 20):
        cf['total'] = cf.get('total', 0) + 5
        _time_tag += f'M07最佳时段(UTC{_hour_m07}H)+5 '
        cf.setdefault('breakdown', {})['M07时间效应'] = f'+5(UTC{_hour_m07}H黄金时段 EV+40%)'
    # 最差月份降权 -5分
    if _month_m07 in (8, 9):
        cf['total'] = max(0, cf.get('total', 0) - 5)
        _time_tag += f'M07夏季降权({_month_m07}月)-5 '
        cf.setdefault('breakdown', {})['M07时间效应'] = cf.get('breakdown',{}).get('M07时间效应','') + f'-5({_month_m07}月低流动性)'
    # 最佳交易日 +3分（周四=3, 周三=2, 周一=0）
    if _wday_m07 in (3, 2):  # 周四/周三
        cf['total'] = cf.get('total', 0) + 3
        _time_tag += f'M07最佳交易日+3 '
        cf.setdefault('breakdown', {})['M07时间效应'] = cf.get('breakdown',{}).get('M07时间效应','') + f'+3(周{["一","二","三","四","五"][_wday_m07]})'
    if _time_tag:
        cf.setdefault('breakdown', {}).setdefault('M07时间效应', _time_tag.strip())

    # [M06相关系数惩罚] 双向等概率品种，做空信号无统计优势
    _m06_zero_coef = {'ETHUSDT', 'ATOMUSDT'}
    _cur_score = cf.get('total', 0)
    if _sym in _m06_zero_coef and _cur_score > 0:
        _pen = 5
        cf['total'] = max(0, _cur_score - _pen)
        cf.setdefault('breakdown', {})['M06相关惩罚'] = f'-{_pen}({_sym} coef=0 双向等概率)'

    # [N17专项 v2.0 ERR-012 2026-05-30] 10万次训练冠军参数全面落地
    # 全局冠军: thr=160, sl=1.5x, mh=12H → 全局PF=1.647 WR=46.7% CI=[1.454,1.860] P(PF>1)=100%
    # 原则: sl从「噪音区外」设置(ATR×1.5+), mh对齐东西方市场完整轮换周期(12~16H)
    _sym_spec_map = {
        # S+级 — 训练PF>=3.0，冠军参数下高度稳定
        'LINKUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override':  8, 'pf_evidence': 3.585, 'grade': 'S+'},  # 训练PF=3.585 WR=58.7% N=46
        'DOGEUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 3.234, 'grade': 'S+'},  # 训练PF=3.234 WR=62.3% N=53 [ERR-011修复sl0.8→1.5]
        'DOTUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 2.388, 'grade': 'S+'},  # 训练PF=2.388 WR=50.7%
        'SUIUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 2.382, 'grade': 'S+'},  # 训练PF=2.382
        # S级 — 训练PF 1.5~2.5，核心主力品种
        'SOLUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 2.064, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 训练认证
        # ETH/LTC: 体制动态SL（设计院 2026-05-30）
        # CHOP体制sl=1.2x（防止贪婪止据）、BEAR趋势体制sl=2.0x（顺势止据）
        'ETHUSDT':  {'sl_mult_override': 2.8, 'tp_mult_override': 1.8, 'mh_override': 18, 'pf_evidence': 1.735, 'grade': 'S',
                     '_regime_sl': {'CHOP_LOW':1.2,'CHOP_MID':1.2,'CHOP_HIGH':1.5,'BEAR_EARLY':1.5,'BEAR_TREND':2.0,'BEAR_CRASH':2.0,'BEAR_RECOVERY':1.5,'BULL_TREND':1.8,'BULL_EARLY':1.8,'BULL_PEAK':1.8,'BULL_CORRECTION':1.5}},  # [v7-2026-06-14] WFV12/12 sl=2.8x tp=1.8x hold=18H EV=+0.397%/笔 WR=68.4%
        'BNBUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.750, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 mh8→16
        'BTCUSDT':  {'sl_mult_override': 2.527, 'tp_mult_override': 1.964, 'mh_override': 17, 'pf_evidence': 1.662, 'grade': 'S'},  # [v7-2026-06-14] WFV12/12 sl=2.527x tp=1.964x hold=17H EV=+0.515%/笔 WR=65.7%
        'ADAUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.968, 'grade': 'S'},   # [ERR-012] sl0.6→1.5
        'ATOMUSDT': {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.961, 'grade': 'S'},   # [ERR-012] sl0.6→1.5 mh8→16
        # A级 — 训练PF 1.2~1.5
        'AVAXUSDT': {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.303, 'grade': 'A'},   # [ERR-012] sl0.6→2.0
        'LTCUSDT':  {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.398, 'grade': 'A',
                     '_regime_sl': {'CHOP_LOW':1.2,'CHOP_MID':1.2,'CHOP_HIGH':1.5,'BEAR_EARLY':1.5,'BEAR_TREND':2.0,'BEAR_CRASH':2.0,'BEAR_RECOVERY':1.5,'BULL_TREND':1.5,'BULL_EARLY':1.5,'BULL_PEAK':1.8,'BULL_CORRECTION':1.5}},
        'NEARUSDT': {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.441, 'grade': 'A'},   # [ERR-012] sl0.6→2.0 mh8→16
        # 观察级 — 训练PF<1.2，谨慎
        'XRPUSDT':  {'sl_mult_override': 2.0, 'tp_mult_override': 2.5, 'mh_override':  8, 'pf_evidence': 0.888, 'grade': 'WATCH'},  # 训练PF=0.888 监管风险高，仅保留不封禁
        'INJUSDT':  {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 12, 'pf_evidence': 1.712, 'grade': 'S'},   # 训练PF=1.712
        'OPUSDT':   {'sl_mult_override': 1.5, 'tp_mult_override': 2.5, 'mh_override': 16, 'pf_evidence': 1.798, 'grade': 'S'},   # 训练PF=1.798
    }

    # 体制动态SL覆盖（ETH/LTC）
    _current_regime = (ms.get('regime','') or '').upper()
    _spec_tmp = _sym_spec_map.get(_sym, {})
    if _spec_tmp and '_regime_sl' in _spec_tmp and _current_regime:
        _regime_sl_val = _spec_tmp['_regime_sl'].get(_current_regime)
        if _regime_sl_val:
            _sym_spec_map[_sym] = dict(_spec_tmp)
            _sym_spec_map[_sym]['sl_mult_override'] = _regime_sl_val
            print(f'[N17体制SL] {_sym} {_current_regime} sl覆盖={_regime_sl_val}x')

    # [N19] BTC传导系数 — 低传导标的在BTC突破时降权
    # 数据来源: train_10k_v5.py N19节点，15标的分析
    # 低传导(<40%): BTC突破后4h内跟随率偏低
    _btc_low_conductance = {
        '1000PEPEUSDT', 'APTUSDT', 'INJUSDT', 'LUNA2USDT', 'NEARUSDT'
    }
    # BTC突破判断阈值: 1H涨幅>1.5%或4H EMA金叉
    _btc_breakout_pct = 0.015
    _spec = _sym_spec_map.get(_sym)
    if _spec and params.get('valid'):
        # 重算SL/TP（用专项sl_mult覆盖）
        _sl_ov = _spec['sl_mult_override']
        _tp_ov = _spec.get('tp_mult_override', 4.0)  # [WFV-v3] 专属TP倍数
        _atr1 = float(ms.get('momentum', {}).get('atr_1h', ms.get('price', 1) * 0.01))
        _price_ov = float(ms.get('price', 0))
        _entry_lo_ov = params.get('entry_lo', _price_ov)
        _entry_hi_ov = params.get('entry_hi', _price_ov)
        _entry_mid_ov = (_entry_lo_ov + _entry_hi_ov) / 2
        if _price_ov > 0 and _atr1 > 0:
            if signal_dir == 'SHORT':
                # [BUG修复] SL从入场区上沿算，确保SL > entry_hi
                _sl_new = round(_entry_hi_ov + _atr1 * _sl_ov, 6)
                _risk_ov = abs(_sl_new - _entry_mid_ov)
                _tp1_new = round(_entry_mid_ov - _risk_ov * _tp_ov, 6)
                _tp2_new = round(_entry_mid_ov - _risk_ov * (_tp_ov * 1.8), 6)
            else:
                # [BUG修复] SL从入场区下沿算，确保SL < entry_lo
                _sl_new = round(_entry_lo_ov - _atr1 * _sl_ov, 6)
                _risk_ov = abs(_entry_mid_ov - _sl_new)
                _tp1_new = round(_entry_mid_ov + _risk_ov * _tp_ov, 6)
                _tp2_new = round(_entry_mid_ov + _risk_ov * (_tp_ov * 1.8), 6)
            # [BUG-FIX 2026-05-29] R:R必须从「入场中点」算，不能用当前价
            # 用当前价算R:R会因为「价格离入场区还有距离」导致分母虚大，R:R严重失真
            # ETH实测: 当前价基准R:R=1.41 vs 入场中点基准R:R=4.66
            _sl_pct_new = round(abs(_sl_new - _entry_mid_ov) / _entry_mid_ov * 100, 3)
            _risk_for_rr = abs(_sl_new - _entry_mid_ov)
            _rr1_new = round(abs(_tp1_new - _entry_mid_ov) / max(_risk_for_rr, 1e-9), 2)
            # [设计院 2026-06-23 P0修复 v4] N17覆盖层护栏：tp2必须在tp1更远方向
            _risk_ov2 = abs(_sl_new - _entry_mid_ov)
            if signal_dir == 'LONG' and _tp2_new <= _tp1_new:
                _tp2_new = round(_tp1_new + _risk_ov2, 6)
            elif signal_dir == 'SHORT' and _tp2_new >= _tp1_new:
                _tp2_new = round(_tp1_new - _risk_ov2, 6)
            _rr2_new = round(abs(_tp2_new - _entry_mid_ov) / max(_risk_for_rr, 1e-9), 2)
            params = dict(params)
            params.update({
                'stop_loss': _sl_new, 'tp1': _tp1_new, 'tp2': _tp2_new,
                'sl_pct': _sl_pct_new, 'rr1': _rr1_new, 'rr2': _rr2_new,
                'sl_atr_mult': _sl_ov,
                '_spec_override': f'{_sym} 专项sl={_sl_ov}x mh={_spec["mh_override"]}h PF={_spec["pf_evidence"]}',
                'valid': _rr1_new >= 1.2,  # [六方修复 2026-06-25] 最低门槛1.2
            })
            print(f'[N17专项] {_sym} sl覆盖={_sl_ov}x rr1={_rr1_new} sl_pct={_sl_pct_new}%')

    # ── [v4.0出场后置层 2026-06-28] N17专项覆写后再次应用exit_params_v4 ──
    # 原因：N17专项 tp_mult_override 会把RR重新拉高（如BTC tp=1.964x → rr=1.9+）
    #       v4.0铁证要求BEAR/CHOP体制RR=1.0，必须在N17后再压近目标
    try:
        import json as _jv4b, pathlib as _pv4b
        _v4b_path = _pv4b.Path(__file__).parent.parent / 'data' / 'dharma_runtime.json'
        _v4b_data = _jv4b.loads(_v4b_path.read_text()) if _v4b_path.exists() else {}
        _v4b_params = _v4b_data.get('exit_params_v4', {})
        _regime_v4b = ms.get('regime', '')
        if any(x in _regime_v4b for x in ('CHOP',)):
            _v4b_key = 'CHOP'
        elif any(x in _regime_v4b for x in ('BULL',)):
            _v4b_key = 'BULL'
        else:
            _v4b_key = 'BEAR'
        _v4b_cfg = _v4b_params.get(_v4b_key, {})
        _v4b_min_sl = float(_v4b_cfg.get('sl_pct', 0))
        _v4b_rr    = float(_v4b_cfg.get('rr', 0))
        if _v4b_min_sl > 0 and _v4b_rr > 0:
            _p_mid_v4b = (params.get('entry_lo',0) + params.get('entry_hi',0)) / 2
            _p_sl_v4b  = params.get('stop_loss', 0)
            _p_sl_pct  = params.get('sl_pct', 0)
            _cur_rr1   = params.get('rr1', 0)
            _risk_v4b  = abs(_p_sl_v4b - _p_mid_v4b) if _p_sl_v4b and _p_mid_v4b else 0
            _v4b_applied = False
            # Step1：若sl_pct < v4最低门槛，扩大止损
            if _p_sl_pct > 0 and _p_sl_pct < _v4b_min_sl and _p_mid_v4b > 0:
                _risk_v4b = _p_mid_v4b * _v4b_min_sl / 100
                if signal_dir == 'SHORT':
                    params['stop_loss'] = round(_p_mid_v4b + _risk_v4b, 6)
                else:
                    params['stop_loss'] = round(_p_mid_v4b - _risk_v4b, 6)
                params['sl_pct'] = _v4b_min_sl
                _v4b_applied = True
            # Step2：若当前RR > v4目标RR，压近TP
            if _risk_v4b > 0 and _cur_rr1 > _v4b_rr + 0.05:
                if signal_dir == 'SHORT':
                    params['tp1'] = round(_p_mid_v4b - _risk_v4b * _v4b_rr, 6)
                    params['tp2'] = round(_p_mid_v4b - _risk_v4b * max(_v4b_rr * 2.0, 2.0), 6)
                else:
                    params['tp1'] = round(_p_mid_v4b + _risk_v4b * _v4b_rr, 6)
                    params['tp2'] = round(_p_mid_v4b + _risk_v4b * max(_v4b_rr * 2.0, 2.0), 6)
                params['rr1'] = round(abs(params['tp1'] - _p_mid_v4b) / max(_risk_v4b, 1e-9), 2)
                params['rr2'] = round(abs(params['tp2'] - _p_mid_v4b) / max(_risk_v4b, 1e-9), 2)
                _v4b_applied = True
            if _v4b_applied:
                params['valid'] = params.get('rr1', 0) >= 1.0  # v4.0体制下1.0已有正期望
                print(f'[v4.0后置] {_regime_v4b}→{_v4b_key} SL={params["sl_pct"]:.2f}% RR={params["rr1"]:.2f} (N17后覆写)')
    except Exception as _ev4b:
        pass  # 静默失败，不影响主流程
    # ── [END v4.0出场后置层] ──

    # [v13.0] 单一化输出层：R:R不足成为唱拘定局式，覆盖action为WATCH
    # 规则：TP1 R:R ≥ 1.5 才论入场（设计院2026-06-14 宽止损策略允许1.5）
    if not params.get('valid'):
        rr1_val = params.get('rr1', 0)
        sl_basis = params.get('sl_basis', 'ATR')
        # [FIX-RR 2026-05-27] R:R不达标时，尝试用ATR×2.0自动扩展止损重算
        _entry_mid = (params.get('entry_lo',0) + params.get('entry_hi',0)) / 2
        _atr4h = ms['momentum'].get('atr_4h', ms['momentum'].get('atr_1h',0)*2.5)
        if _entry_mid > 0 and _atr4h > 0:  # [FIX-RR-v2 2026-06-14] 移除rr1_val>0条件，score清零不影响RR扩展
            _new_risk = _atr4h * 2.0
            if signal_dir == 'SHORT':
                _new_sl  = _entry_mid + _new_risk
                _new_tp1 = _entry_mid - _new_risk * 2.5
                _new_rr1 = abs(_new_tp1 - _entry_mid) / _new_risk
            else:
                _new_sl  = _entry_mid - _new_risk
                _new_tp1 = _entry_mid + _new_risk * 2.5
                _new_rr1 = abs(_new_tp1 - _entry_mid) / _new_risk
            # 拓展后止损宽度 ≤ 5%，且新RR ≥ 2.5
            _new_sl_pct = abs(_new_sl - _entry_mid) / _entry_mid * 100
            if _new_rr1 >= 1.5 and _new_sl_pct <= 5.0:  # [FIX-RR-v2 2026-06-14] 1.5允许宽止损策略
                # [设计院 2026-06-23 P0修复 v5] 拓展重算分支：tp2同步更新
                _new_tp2 = _entry_mid - _new_risk * 4.5 if signal_dir == 'SHORT' else _entry_mid + _new_risk * 4.5
                if signal_dir == 'LONG' and _new_tp2 <= _new_tp1:
                    _new_tp2 = _new_tp1 + _new_risk
                elif signal_dir == 'SHORT' and _new_tp2 >= _new_tp1:
                    _new_tp2 = _new_tp1 - _new_risk
                _new_rr2 = round(abs(_new_tp2 - _entry_mid) / _new_risk, 2)
                params = dict(params)
                params['stop_loss'] = round(_new_sl, 4)
                params['tp1']       = round(_new_tp1, 4)
                params['tp2']       = round(_new_tp2, 4)
                params['rr1']       = round(_new_rr1, 2)
                params['rr2']       = _new_rr2
                params['sl_pct']    = round(_new_sl_pct, 2)
                params['sl_basis']  = 'atr4h×2.0(拓展重算)'
                params['valid']     = True
                rr1_val = params['rr1']
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        # ── [六方联合修复 2026-06-25] 方案C：体制分级R:R最低门槛 ──
        # 铁证依据：BEAR_RECOVERY WR=72.5% × R:R=1.2 → EV=0.595（正期望）
        #           震荡行情TP目标有限，强求2.5是脱离实际
        #           每个体制应有独立R:R门槛，而非统一1.5
        _cur_regime_rr = ms.get('regime', '') if ms else ''
        _rr_thresholds = {
            'BEAR_TREND':      1.8,   # 趋势强，目标远，保持高标准
            'BULL_TREND':      1.8,
            'BEAR_EARLY':      1.6,   # 初期趋势，稍宽松
            'BULL_EARLY':      1.6,
            'BEAR_RECOVERY':   1.2,   # 反弹体制WR=72.5%，低R:R有正期望
            'BULL_CORRECTION': 1.2,
            'CHOP_MID':        1.0,   # [v25.4 苏摩111 2026-06-28] 对齐v4.0 RR=1.0铁证 EV=+0.37%/笔
            'CHOP_LOW':        1.0,   # [v25.4] CHOP_LOW RR=1.0
            'CHOP_HIGH':       1.2,   # [v25.4] CHOP_HIGH稍保守 1.2（高波动不确定性）
        }
        _rr_min = _rr_thresholds.get(_cur_regime_rr, 1.4)  # 默认1.4
        _is_valid_rr = rr1_val >= _rr_min
        if not _is_valid_rr:
            cf['action']     = f'WATCH(R:R={rr1_val:.2f}<{_rr_min}({_cur_regime_rr}) sl={sl_basis})'
            cf['kelly_mult'] = 0
            cf['rr_gate']    = 'FAIL'
            cf['rr_min_used'] = _rr_min
        else:
            cf['action']  = 'ENTER_FULL'
            cf['rr_gate'] = 'PASS'
            cf['rr_min_used'] = _rr_min
    else:
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['rr_gate'] = 'PASS'
        # [v13.0] 单一化：行动与 primary_tf 周期同步
        cf['primary_tf'] = params.get('primary_tf', '4H')
        cf['entry_tf']   = params.get('entry_tf',   '1H')
        cf['sl_basis']   = params.get('sl_basis',   'swing_4h+atr4h×0.3')

    # [Phase C-2] RL 仓位乘数覆盖 kelly_mult
    rl = extra_data.get('rl_position', {})
    if rl.get('kelly_mult') and cf.get('action') in ('ENTER_FULL', 'ENTER'):
        rl_mult = rl['kelly_mult']
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        base_kelly = cf.get('kelly_base', cf.get('kelly_mult', 1.0))
        cf['kelly_mult'] = round(base_kelly * rl_mult, 3)
        cf['rl_kelly_note'] = rl.get('note', '')

    # ══════════════════════════════════════════════════════════
    # [v12.8] I2 冲突解析器 / I3 Kelly分配 / I4/I7 漂移+健康检测
    # ══════════════════════════════════════════════════════════
    import sys as _sys
    _bb_dir = str(__file__).replace('brahma_brain.py','')
    if _bb_dir not in _sys.path: _sys.path.insert(0, _bb_dir)

    # I4/I7: 漂移检测
    try:
# [CLEANED 2026-06-11] from drift_detector import detect as _drift_detect
        # [P1-A audit-fix 2026-06-17 DISABLED] _drift = {'drift': False}  # [DEAD: drift_detector removed]
        extra_data['drift'] = _drift
        if _drift['alert'] == 'ALERT':
            print(f'[BrahmaBrain] ⚠️ DRIFT ALERT {_sym}: {_drift["summary"]}')
    except Exception as _de:
        pass

    # I2: 冲突解析
    try:
        from conflict_resolver import resolve as _cr_resolve
        _bd = cf.get('breakdown', {})
        _conflict = _cr_resolve(_bd, signal_dir, cf.get('total', 0))
        extra_data['conflict'] = _conflict
        if _conflict['verdict'] == 'REJECT':
            print(f'[BrahmaBrain] 🚫 CONFLICT REJECT {_sym}: {_conflict["conflict_summary"]}')
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['conflict_reject'] = True
        elif _conflict['verdict'] == 'DOWNWEIGHT':
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = round(cf.get('kelly_mult', 1.0) * _conflict['confidence_adj'], 3)
            cf['conflict_adj'] = _conflict['confidence_adj']
        elif _conflict['verdict'] == 'APPROVE' and _conflict['confidence_adj'] > 1.0:
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = round(min(cf.get('kelly_mult', 1.0) * _conflict['confidence_adj'], 2.0), 3)
    except Exception as _ce:
        pass

    # I3: Kelly仓位分配
    try:
# [CLEANED 2026-06-11] from kelly_allocator import compute as _kelly_compute
        _bayes_wr = None
        if extra_data.get('online_bayes'):
            _bayes_wr = extra_data['online_bayes'].get('post_wr')
        _xgb_prob = None
        if extra_data.get('xgboost'):
            _xgb_prob = extra_data['xgboost'].get('win_prob')
        _drift_mult = extra_data.get('drift', {}).get('confidence_mult', 1.0)
        _kelly_result = _kelly_compute(
            rr_ratio=params.get('rr_ratio', 1.5),
            signal_score=int(cf.get('total', 100)),
            bayes_wr=_bayes_wr,
            xgb_prob=_xgb_prob,
            extra_data={'drift': {'confidence_mult': _drift_mult}},
        )
        extra_data['kelly'] = _kelly_result
    except Exception as _ke:
        pass

    # ══════════════════════════════════════════════════════════
    # [v24.3] PRE-COMPUTE structure grade（前移，供Queue check使用）
    # 原设计：structure计算在行3101，Queue check在行2662，grade=0导致冷却死循环
    # 修复：提前计算grade，让Queue check读到真实值
    # ══════════════════════════════════════════════════════════
    try:
        from structure_quality_engine import evaluate_structure_quality as _pre_sqe
        _tc = params.get('trigger_15m_confidence', 0) or cf.get('trigger_15m_confidence', 0) or 0  # [v24.5-fix] 优先从 params 读取，cf不包含时备用
        _pre_sq_result = _pre_sqe(
            symbol     = _sym,
            signal_dir = signal_dir,
            price      = float(ms.get('price', 0)),
            entry_lo   = float(params.get('entry_lo', 0) or 0),
            entry_hi   = float(params.get('entry_hi', 0) or 0),
            smc        = smc,
            swing_4h   = ms.get('swing_4h', {}),
            key_levels = ms.get('key_levels', {}),
            momentum   = ms.get('momentum', {}),
            trigger_confidence = int(_tc),
        )
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['structure_grade'] = _pre_sq_result.get('grade', 0)
        # [v24.5-debug] 临时打印，确认修复后grade值
        import os
        if os.environ.get('BRAHMA_DEBUG'):
            print(f'[PRE-SQE] {_sym} price={ms.get("price",0):.0f} entry={params.get("entry_lo",0):.0f}~{params.get("entry_hi",0):.0f} grade={cf["structure_grade"]} sources={_pre_sq_result.get("sources",[])}')
    except Exception as _pre_sq_err:
        pass  # 失败不影响主流程

    # ══════════════════════════════════════════════════════════
    # [v12.9] I5 队列/资金 / I3 动态SL / I7 归因（Phase 1）
    # ══════════════════════════════════════════════════════════

    # I5: 信号队列检查（是否可以进入队列）
    try:
        from signal_queue import add_signal as _sq_add, get_status as _sq_status
        _sq_result = _sq_add(
            symbol=_sym,
            signal_dir=signal_dir,
            score=float(cf.get('total', 100)),
            regime=str(ms.get('regime','')),
            grade=int(cf.get('structure_grade', 0) or 0),
            effective_grade=round(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0), 1),
            grade_mult=round(float(cf.get('grade_mult', 1.0) or 1.0), 2),
        )
        extra_data['signal_queue'] = _sq_result
        if not _sq_result.get('accepted', True):
            print(f'[BrahmaBrain] 🚫 Queue reject {_sym}: {_sq_result["reason"]}')
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['queue_reject'] = _sq_result['reason']
    except Exception as _sqe:
        pass

    # I5: 资金分配
    try:
        from capital_allocator import compute as _ca_compute
        _ca_result = _ca_compute(
            symbol=_sym,
            signal_score=float(cf.get('total', 100)),
            sl_pct=params.get('sl_pct', None),
        )
        extra_data['capital'] = _ca_result
        if not _ca_result.get('allowed', True):
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
            cf['kelly_mult'] = 0.0
            cf['capital_reject'] = _ca_result['reason']
    except Exception as _cae:
        pass

    # I3: 动态止损
    try:
        from dynamic_sl import compute as _dsl_compute
        _drift_alert = extra_data.get('drift', {}).get('alert', 'OK')
        _kls = [lvl for lvl in ms.get('key_levels', {}).values()
                if isinstance(lvl, (int,float)) and lvl > 0] if ms.get('key_levels') else []
        _dsl = _dsl_compute(
            symbol=_sym,
            entry_price=float(ms.get('price', 0)),
            signal_dir=signal_dir,
            regime=str(ms.get('regime','')),
            score=float(cf.get('total', 100)),
            drift_alert=_drift_alert,
            key_levels=_kls,
        )
        extra_data['dynamic_sl'] = _dsl
        params = dict(params)
        params['sl_price_dyn'] = _dsl.get('sl_price')
        params['sl_pct_dyn']   = _dsl.get('sl_pct')
        params['sl_reasoning'] = _dsl.get('reasoning')
    except Exception as _dsle:
        pass

    # I7: 实时归因（轻量，从attribution.json读缓存而非重算）
    try:
        _attr_f = __import__('pathlib').Path('data/attribution.json')
        if _attr_f.exists():
            _attr = __import__('json').loads(_attr_f.read_text())
            extra_data['attribution'] = {
                'top_misleaders': _attr.get('top_misleaders', [])[:3],
                'ts': _attr.get('ts', ''),
            }
    except Exception as _ate:
        pass

    # ══════════════════════════════════════════════════════════════
    # [设计院终极版 v2.0] 六层防线集成入口
    _globally_blocked = False  # [设计院修复 2026-06-26] 默认值防止try异常时UnboundLocalError
    # regime_gate → asset_universe → regime_weights → adaptive_threshold → MTF → Kelly | 体制门控 → 资产池 → 体制权重 → 自适应阈值 → 多时框 → Kelly
    # ══════════════════════════════════════════════════════════════
    try:
        import sys as _v2_sys, os as _v2_os
        _v2_base = _v2_os.path.dirname(_v2_os.path.dirname(_v2_os.path.abspath(__file__)))
        if _v2_base not in _v2_sys.path: _v2_sys.path.insert(0, _v2_base)
        from upgrade_v2.v2_integrator import v2_enhance_signal as _v2_enhance
        _v2_result = _v2_enhance(
            symbol    = _sym,
            direction = signal_dir,
            score     = float(cf.get('total', 0)),
            ms        = ms,
            breakdown = cf.get('breakdown', {}),
            nav       = float(ms.get('nav', 127.62) or 127.62),
            interval  = '1h',
        )
        # 写入 cf 供日志记录
        cf['v2_audit']     = _v2_result.get('audit', {})
        cf['v2_mode']      = _v2_result.get('mode', '')
        cf['v2_mtf_note']  = _v2_result.get('mtf_note', '')
        cf['v2_pos_pct']   = _v2_result.get('pos_pct', 0)
        cf['v2_breakdown'] = _v2_result.get('breakdown_ext', {})

        # [P0-A audit-fix 2026-06-17] _globally_blocked 标志：v2封锁后阻止后续门控覆盖清零
        _globally_blocked = not _v2_result.get('allowed', True)
        if _globally_blocked:
            # v2 硬封锁 → 评分归零0，不退出，让analyze()完整构建返回结构
            _block_reason = _v2_result.get('block_reason', 'v2封锁')
            print(f'[BrahmaBrain-v2] 🛡️ 封锁 {_sym} {signal_dir}: {_block_reason[:60]}')
            cf['total']         = 0
            cf['score_final']   = 0
            cf['action']        = 'SKIP'
            cf['kelly_mult']    = 0
            cf['v2_blocked']    = True
            cf['v2_block_reason'] = _block_reason
        else:
            # v2 通过 → 更新评分和仓位
            _v2_final_score = _v2_result.get('final_score', cf.get('total', 0))
            if _v2_final_score != cf.get('total', 0):
                print(f'[BrahmaBrain-v2] 📊 {_sym} 评分调整: {cf.get("total",0):.0f}→{_v2_final_score:.0f} ({_v2_result.get("mode","")})')
                cf['total'] = _v2_final_score
            # 仓位由v2接管
            cf['v2_pos_pct'] = _v2_result.get('pos_pct', 0)
    except Exception as _v2_err:
        # v2失败降级，不影响原有流程
        _v2_err_str = str(_v2_err)
        # 模块缺失静默处理（ModuleNotFoundError / ImportError 不输出告警）
        if not isinstance(_v2_err, (ModuleNotFoundError, ImportError)):
            import traceback
            cf['v2_error'] = _v2_err_str[:100]
        # upgrade_v2 模块缺失时完全静默，不写入任何内容
        else:
            pass  # 静默降级，不输出任何日志

    # [达摩院v2.0] P2: Score门槛 — 从参数总线读取品种专项门槛
    # M01铁证: thr=160品种均PF=2.944, 158为实盘安全边际
    try:
        import sys as _sys, os as _os
        _bus_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')
        if _bus_dir not in _sys.path: _sys.path.insert(0, _bus_dir)
        from dharma.dharma_bus import get_sym_params as _get_bus_p
        _bus_d = _get_bus_p(_sym) if _sym else {}
        MIN_SCORE_OPEN = int(_bus_d.get('thr', 140))
    except Exception:
        MIN_SCORE_OPEN = 140   # fallback: 2026-06-04 设计院统一门槛（原158偏高，adaptive_threshold=140）
    MIN_SCORE_S2   = 130   # S2门槛：轻仓3%试探
    MIN_SCORE_S3   = 100   # S3门槛：观察记录，不开仓
    _score_raw = cf.get('total', 0)

    # [P0-A audit-fix 2026-06-17] 全局封锁短路：v2已封锁时跳过所有后续评分门控
    # 防止后续 StructureGate/DharmaFactor/N20/N21 等重新写入 cf['total'] 覆盖清零
    if _globally_blocked:
        _score_raw = 0
        cf['total'] = 0  # [S4-fix audit-2026-06-17] 再次确保cf同步
        _score_gate_ok = False  # [S4-fix] 封锁时门控标志同步清零，防止后续门控误判

    # ── [P2-C] N19 BTC传导系数 ─────────────────────────────────────────────
    # 低传导标的(<40%) 在BTC强势突破(1H涨幅>1.5%)时 score×0.90
    # 数据来源: train_10k_v5.py N19节点
    try:
        _btc_low_cond = {'1000PEPEUSDT','APTUSDT','INJUSDT','LUNA2USDT','NEARUSDT'}
        if _sym in _btc_low_cond:
            _btc_state = extra_data.get('btc_market', {}) or {}
            _btc_chg_1h = float(_btc_state.get('price_change_pct_1h', 0) or 0)
            if abs(_btc_chg_1h) >= 1.5:
                _cond_factor = 0.90
                _score_raw = round(_score_raw * _cond_factor)
                cf['total'] = _score_raw
                _log(f'[BrahmaBrain] 📉 P2-C N19低传导惩罚: {_sym} ×{_cond_factor} BTC1H={_btc_chg_1h:+.1f}% score→{_score_raw}')
    except Exception:
        pass
    # ── [END P2-C] | P2-C 阶段结束 ──────────────────────────────────────────────────────────
    # ── [v25.5 能力升级-A] 体制×方向动态门控提升 ─────────────────────────
    # 原则：不封禁，但低WR组合需要更高评分才能通过（精化筛选）
    # 数据：BEAR_EARLY_LONG WR=50.4% / BULL_EARLY_SHORT WR=51.9%（n>6000铁证）
    # 解决：提高这些组合的动态门控阈值，要求信号质量更高才入场
    # [BUG-FIX 2026-06-18] _matched_regime_key 是 confluence_score() 的局部变量，
    # analyze() 作用域内不存在。改从 cf(breakdown) 读取 _regime_v4_key。
    _regime_dir_key = f"{(cf or {}).get('_regime_v4_key','') or ''}_{signal_dir}"
    _DYNAMIC_THRESHOLD_BOOST = {
        # 负期望组合：要求额外+18分才能通过（约等于要求score≥158）
        'BEAR_EARLY_LONG':       18,   # WR=50.4% avg=-0.110% → 高门控筛出低质信号
        'BULL_EARLY_SHORT':      18,   # WR=51.9% avg=-0.137% → 高门控筛出低质信号
        # 震荡×多：WR=56%，略提高
        'CHOP_LONG':              8,   # WR=56.0% avg=-0.001% → 轻提高
        'CHOP_MID_LONG':          8,
        'CHOP_LOW_LONG':          5,
    }
    _thr_boost = _DYNAMIC_THRESHOLD_BOOST.get(_regime_dir_key, 0)
    _MIN_SCORE_EFFECTIVE = MIN_SCORE_OPEN + _thr_boost
    if _thr_boost > 0:
        cf['dynamic_threshold_boost'] = _thr_boost
        cf['dynamic_threshold_effective'] = _MIN_SCORE_EFFECTIVE

    # ── [v25.5 能力升级-D] 1D方向性修正 ─────────────────────────────────────
    # 原则：逆1D大趋势方向时降权（非封禁），要求更高质量信号
    # 数据：BEAR_EARLY_LONG在1D DOWNTREND时失败率极高（1D逆势做多）
    try:
        _ms_1d = ms.get('1d', ms.get('daily', {})) or {}
        _phase_1d = str(_ms_1d.get('phase', '')).upper()
        _1d_penalty = 0
        if _phase_1d in ('DOWNTREND', 'PULLBACK_DN', 'TOPPING') and signal_dir == 'LONG':
            # 1D下跌趋势中做多：+12分门控（不封禁，但要求更高质量）
            _1d_penalty = 12
            cf['_1d_direction_penalty'] = f'+{_1d_penalty}门控(1D={_phase_1d}逆势做多)'
        elif _phase_1d in ('UPTREND', 'PULLBACK_UP', 'BOTTOMING') and signal_dir == 'SHORT':
            # 1D上涨趋势中做空：+12分门控
            _1d_penalty = 12
            cf['_1d_direction_penalty'] = f'+{_1d_penalty}门控(1D={_phase_1d}逆势做空)'
        _MIN_SCORE_EFFECTIVE += _1d_penalty
    except Exception:
        pass

    _score_gate_ok = float(_score_raw) >= _MIN_SCORE_EFFECTIVE

    # [苏摩哲学校正 2026-06-30 A1修正] CHOP_MID做多WATCH通道
    # CHOP强反转上限=105，阈值必须≤105才能触发，修正为100
    # 原110阈值 > CHOP上限105 → 永远无法触发（设计院顶层修正 2026-06-30）
    _is_chop_long_watch = (
        'CHOP' in str(_regime_str).upper()
        and signal_dir == 'LONG'
        and float(_score_raw) >= 100   # 修正: 110→100，CHOP上限=105可触发
        and not _score_gate_ok
    )
    if _is_chop_long_watch:
        _score_gate_ok = True   # 豁免score gate
        cf['chop_long_watch'] = f'CHOP_MID做多WATCH通道: score={_score_raw:.0f}≥100 → 0.5%NAV观察仓'
        print(f'[CHOP-WATCH] {_sym} CHOP_MID做多: score={_score_raw:.0f}≥100 WATCH信号解锁（A1修正）')

    if not _score_gate_ok:
        print(f'[BrahmaBrain] ⚠️ Score gate {_sym}: {_score_raw:.0f} < {_MIN_SCORE_EFFECTIVE} (低于S1门槛{"+动态boost" if _thr_boost else ""})')
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['score_gate_reject'] = True
        cf['score_gate_min'] = MIN_SCORE_OPEN

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 M11] CI宽度仓位折扣层 — 安全保险丝
    # LINK CI宽5.86→×0.70 | DOGE CI宽5.11→×0.70 | NEAR ×0.55
    # 确保高不确定性品种不会因单笔大仓拖垃最大回撤
    # ══════════════════════════════════════════════════════════════
    try:
        from dharma.dharma_bus import get_pos_with_ci_discount as _get_ci_pos
        _ci_pos_cap = _get_ci_pos(_sym)
        # score_pos是分层仓位，_ci_pos_cap是总线上限，取小者
        _score_pos_cur = extra_data.get('score_pos', 0.065) if extra_data and isinstance(extra_data, dict) else 0.065
        _final_pos = min(_score_pos_cur, _ci_pos_cap)
        if _final_pos < _score_pos_cur:
            if extra_data and isinstance(extra_data, dict):
                extra_data['score_pos'] = _final_pos
                extra_data['ci_discount_applied'] = True
            _log(f'[BrahmaBrain] M11 CI折扣 {_sym}: {_score_pos_cur:.1%}→{_final_pos:.1%}')
    except Exception:
        pass

    # [P2-A] 4h多周期方向确认层（N13实证: 4h泛化率75%优于1h67%）
    _mom_4h = ms.get('momentum', {})
    _rsi_4h = float(_mom_4h.get('rsi_4h', 50))
    _macd_4h = _mom_4h.get('macd_4h', 0) or _mom_4h.get('macd', 0) or 0
    _ema50_4h = float(_mom_4h.get('ema50_4h', 0) or 0)
    _ema200_4h = float(_mom_4h.get('ema200_4h', 0) or 0)
    _price_4h = float(ms.get('price', 0) or 0)
    _4h_align = 'NEUTRAL'
    # 4h方向判断：RSI方向 + EMA排列
    if _rsi_4h > 55 and (_ema50_4h > _ema200_4h or _macd_4h > 0) and _price_4h > _ema50_4h > 0:
        _4h_align = 'BULL'
    elif _rsi_4h < 45 and (_ema50_4h < _ema200_4h or _macd_4h < 0) and _price_4h < _ema50_4h > 0:
        _4h_align = 'BEAR'
    # 4h与1h信号方向一致时加分（N13: +12%泛化率）
    if _4h_align == 'BULL' and signal_dir == 'LONG' and _score_gate_ok:
        _score_raw = round(_score_raw * 1.05, 1)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['mtf_4h_confirm'] = f'4H✅BULL RSI={_rsi_4h:.0f} +5%'
        print(f'[BrahmaBrain] 📊 {_sym} 4H共振BULL: score×1.05 → {_score_raw:.0f}')
    elif _4h_align == 'BEAR' and signal_dir == 'SHORT' and _score_gate_ok:
        _score_raw = round(_score_raw * 1.05, 1)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['mtf_4h_confirm'] = f'4H✅BEAR RSI={_rsi_4h:.0f} +5%'
        print(f'[BrahmaBrain] 📊 {_sym} 4H共振BEAR: score×1.05 → {_score_raw:.0f}')
    elif _4h_align != 'NEUTRAL' and _4h_align == ('BEAR' if signal_dir=='LONG' else 'BULL'):
        # [v24.3-fix] 4H方向冲突 → 降权-25分（哲学: 降权不封禁）
        # 4H逆势是风险因子，用分数惩罚体现，grade≥70仍可通过
        # 顺势+5%奖励 vs 逆势-25分惩罚，不对称反映风险
        _4h_penalty = 25
        _score_raw = max(0, _score_raw - _4h_penalty)
        cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['mtf_4h_conflict'] = f'4H⚠️{_4h_align} vs {signal_dir} 降权-{_4h_penalty}分 → {_score_raw:.0f}'
        print(f'[BrahmaBrain] ⚠️ {_sym} 4H逆势降权-{_4h_penalty}: {_4h_align} vs {signal_dir} → score={_score_raw:.0f}')
    elif _4h_align == 'NEUTRAL' and _score_gate_ok:
        # [v25.7 P0c 2026-06-21] MTF=NEUTRAL降权 -8%
        # 铁证：MTF=NEUTRAL比AGREE信号WR低约8%，需体现在评分中
        # 哲学：4H方向不确定 = 追加风险，应降权不封禁
        _neutral_penalty_pct = 0.92  # -8%
        _score_before_neutral = _score_raw
        _score_raw = round(_score_raw * _neutral_penalty_pct, 1)
        cf['total'] = _score_raw
        cf = copy.deepcopy(cf)
        cf['mtf_4h_neutral'] = f'4H NEUTRAL 降权×0.92 {_score_before_neutral:.0f}→{_score_raw:.0f}'
        print(f'[BrahmaBrain] 🟡 {_sym} MTF=NEUTRAL 降摱8%: score {_score_before_neutral:.0f}→{_score_raw:.0f}')

    # [设计院 2026-05-24] 达摩院6节点预测验证 — 接入真实信号流
    _dharma_nodes = {'nodes_pass': 0, 'verdict': 'UNKNOWN', 'score_mult': 1.0, 'detail': ''}
    try:
        from brahma_brain.dharma_nodes import evaluate_nodes as _eval_nodes
        _fg = 50
        try:
            from brahma_brain.macro_stub import get_fear_greed as _fg_fn
            _fg = _fg_fn() or 50
        except Exception: pass
        _dharma_nodes = _eval_nodes(ms, signal_dir, fg=_fg)
        # 节点乘数调整score
        _node_mult = _dharma_nodes['score_mult']
        if _node_mult == 0.0:
            # [v24.3-fix] 达摩院节点0/1 → 降权-30分（哲学: 不归零）
            _score_raw = max(0, _score_raw - 30)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            print(f'[Dharma] ⚠️ 节点不足 {_sym}: {_dharma_nodes["nodes_pass"]}/6节点 → -30分 score={_score_raw:.0f}')
        elif _node_mult != 1.0:
            _score_raw = round(_score_raw * _node_mult, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            print(f'[Dharma] 🔱 {_sym} 节点={_dharma_nodes["nodes_pass"]}/6 mult={_node_mult} score: {cf.get("total",0):.0f}→{_score_raw:.0f} {_dharma_nodes["detail"]}')
        else:
            print(f'[Dharma] ✅ {_sym} 节点={_dharma_nodes["nodes_pass"]}/6 verdict={_dharma_nodes["verdict"]} {_dharma_nodes["detail"]}')
        # [v24.3-fix] 节点数<3 → 额外-15分而非强制拒绝（哲学: 降权）
        if _dharma_nodes['nodes_pass'] < 3:
            _score_raw = max(0, _score_raw - 15)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
        _score_gate_ok = _score_gate_ok  # 不再因节点数强制block
        # [设计院 2026-05-24] ≥5节点为高置信（HIGH_CONF），分数額外加成
        if _dharma_nodes.get('verdict') == 'HIGH_CONF':
            _score_raw = round(_score_raw * 1.05, 1)  # +5%加成
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            print(f'[Dharma] 🌟 HIGH_CONF {_sym}: score加成 ×1.05 → {_score_raw:.0f}')
    except Exception as _dne:
        pass  # 节点验证失败不阻断主流

    elapsed = round(time.time() - t0, 2)

    # ── [v25.5] 低市値品种校正层 ─────────────────────────────────
    # 铁证: DOGE/PEPE/TRUMP score虍高全部TIMEOUT，因果: 低流动性标的OB/FVG是假信号（ICM原则）
    # 修复: 降权评分 + 强制 TP小化（降低 TIMEOUT率）
    # ── [v25.5-AUDIT 已回滚] 以下品种校正因无铁证支撑而移除 ──────────────────
    # 回滚原因: DOGE实盘 n=3，铁证库无DOGE专项数据，违反最高宪法 n<30不得引用
    # 后续待办: 积累至 n≥100 后基于实盘数据重新评估
    # _LOW_CAP_CORRECTIONS = {...}  # 已回滚

    # [v22.1 2026-06-10] 进场区距离动态惩罚（gap远离惩罚维度）
    # 铁证: DOGE 180+全部TIMEOUT根因是 gap=-10%（价格已远超入场区）
    # gap定义: (entry_lo - price) / price * 100
    #   >0: 价格在入场区下方，需要反弹（正常等待）
    #   <0: 价格已穿越入场区（对SHORT=已经下跌超过入场区，信号失效）
    try:
        _gap_price  = float(ms.get('price', 0) or 0)
        _gap_elo    = float(params.get('entry_lo', 0) or 0)
        if _gap_price > 0 and _gap_elo > 0 and signal_dir == 'SHORT':
            _gap_dist = (_gap_elo - _gap_price) / _gap_price * 100
            if _gap_dist < -2.0:
                # 价格已远在入场区下方2%+，信号基本失效
                _gap_penalty = max(-40, round(_gap_dist * 3))  # -2% → -6分，-10% → -30分
                _score_raw = round(_score_raw + _gap_penalty, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
                print(f'[v22.1-Gap惩罚] {_sym} SHORT: 价格已下穿入场区 gap={_gap_dist:.1f}% {_gap_penalty:+d}分 → {_score_raw:.0f}')
            elif _gap_dist > 5.0:
                # 入场区距现价>5%，很难触达
                _gap_penalty = max(-20, round(-((_gap_dist - 5.0) * 2)))
                _score_raw = round(_score_raw + _gap_penalty, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
                print(f'[v22.1-Gap惩罚] {_sym} SHORT: 入场区偏远 gap={_gap_dist:.1f}% {_gap_penalty:+d}分 → {_score_raw:.0f}')
        elif _gap_price > 0 and _gap_elo > 0 and signal_dir == 'LONG':
            _gap_dist_l = (_gap_price - params.get('entry_hi', _gap_elo)) / _gap_price * 100
            if _gap_dist_l < -2.0:
                _gap_penalty_l = max(-40, round(_gap_dist_l * 3))
                _score_raw = round(_score_raw + _gap_penalty_l, 1)
                cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
                print(f'[v22.1-Gap惩罚] {_sym} LONG: 价格已上穿入场区 gap={_gap_dist_l:.1f}% {_gap_penalty_l:+d}分 → {_score_raw:.0f}')
    except Exception:
        pass  # gap惩罚失败不阻断主流程

    _score = _score_raw

    # ── [v25.5-AUDIT 已回滚] BEAR_RECOVERY SHORT ×0.4 和 entry_source 惩罚 ──
    # 回滚原因A: 实盘BR_SHORT n=0有效样本，28条TIMEOUT是settler bug制造的假结果，
    #   不代表方向本身失败。离线铁证WR=47.9%(n=603)支持降权，但体制方向乘数矩阵
    #   已有0.4×机制覆盖，无需在字面量评分层二次干预。
    # 回滚原因B: entry_source=unknown n=20 < 30，违反最高宪法，禁止引用。
    # 后续待办: 积累 n≥100 实盘BEAR_RECOVERY SHORT 信号后重新评估。

    # ── P2 CHOP 硬性上限保护 [v25.4升级 CHOP-tc解锁 2026-06-27] ─────────────
    # 原哲学：CHOP EV=-0.11%（整体铁证n=14902）→ 硬性上限90
    # 新发现（达摩院CHOP专项）：tc共识分层后 WR=61~78%！CHOP是反转信号体制！
    # tc_strong×反向CHOP：WR=70~78%，解除上限（年均26条，BTC+ETH）
    # tc_lean×反向CHOP：  WR=61~63%，上限放宽至105
    # tc_neutral：         维持上限90（整体EV负，不变）
    # tc_同向CHOP（做多但全多共识）：上限收紧至75（反向逻辑，极危险）
    _is_chop_regime = any(x in str(ms.get('regime','')) for x in ('CHOP_MID','CHOP_HIGH','CHOP'))
    if _is_chop_regime:
        _tc_val   = int(ms.get('tc', ms.get('trend_consensus', 0)) or 0)
        _dir_chop = str(ms.get('signal_dir', ms.get('direction', '')))
        # 方向与tc的关系：SHORT信号 + tc偏空(负) = 逆向做空（CHOP反转逻辑）
        # CHOP_SHORT + tc_strong_bull(+2/+3) = 全市场多 → 震荡顶做空 ✅
        # CHOP_LONG  + tc_strong_bear(-2/-3) = 全市场空 → 震荡底做多 ✅
        _is_chop_short = (_dir_chop == 'SHORT')
        _is_chop_long  = (_dir_chop == 'LONG')
        _tc_align_short = (_tc_val >= 2)   # 多周期全多共识 → CHOP做空（反转）
        _tc_align_long  = (_tc_val <= -2)  # 多周期全空共识 → CHOP做多（反转）
        _tc_lean_short  = (_tc_val == 1)   # 单向偏多 → CHOP做空（弱反转）
        _tc_lean_long   = (_tc_val == -1)  # 单向偏空 → CHOP做多（弱反转）
        _tc_reverse_short = (_tc_val <= -2)  # 全空共识做空 → 同向顺势，危险！
        _tc_reverse_long  = (_tc_val >= 2)   # 全多共识做多 → 同向顺势，危险！

        _score_before_cap = _score
        if (_is_chop_short and _tc_align_short) or (_is_chop_long and _tc_align_long):
            # tc_strong 反转方向：WR=70~78%，完全解除上限（苏摩审批通过）
            _chop_cap_applied = None  # 无上限
            print(f'[P2-CHOP-UNLOCK] {ms.get("symbol","?")} CHOP×tc_strong反转: score={_score:.0f} 无上限 WR=70~78%')
            cf['breakdown']['CHOP解锁'] = f'tc_strong反转 tc={_tc_val} WR=70~78% 无上限'
        elif (_is_chop_short and _tc_lean_short) or (_is_chop_long and _tc_lean_long):
            # tc_lean 反转方向：WR=61~63%，上限放宽至105
            _chop_cap_applied = 105
            if _score > 105:
                _score = 105
                cf['breakdown']['CHOP上限'] = f'tc_lean反转 tc={_tc_val} WR=61~63% 上限105: {_score_before_cap:.0f}→105'
                print(f'[P2-CHOP-CAP] {ms.get("symbol","?")} CHOP×tc_lean: {_score_before_cap:.0f}→105')
        elif (_is_chop_short and _tc_reverse_short) or (_is_chop_long and _tc_reverse_long):
            # 同向顺势（全空做空/全多做多）：WR=30~46%！极危险，上限收紧至75
            _chop_cap_applied = 75
            if _score > 75:
                _score = 75
                cf['breakdown']['CHOP危险'] = f'tc同向顺势 tc={_tc_val} WR=30~46% 上限75: {_score_before_cap:.0f}→75'
                print(f'[P2-CHOP-DANGER] {ms.get("symbol","?")} CHOP×tc同向: {_score_before_cap:.0f}→75')
        else:
            # tc_neutral(0)：维持原90上限
            _chop_cap_applied = 90
            if _score > 90:
                _score = 90
                cf['breakdown']['CHOP硬性上限'] = f'P2保护tc_neutral: {_score_before_cap:.0f}→90（CHOP整体EV=-0.11%）'
                print(f'[P2-CHOP-CAP] {ms.get("symbol","?")} CHOP体制tc_neutral上限: {_score_before_cap:.0f}→90')
    # ── 死穴精英解锁通道（苏摩哲学校正 2026-06-30）────────────────────────────
    # 哲学：梵天为交易而生，体制=仓位权重调节器，不是封禁系统
    # 极端结构识别场景（RSI极值+高score+高grade）允许精英解锁
    _regime_str = str(ms.get('regime',''))
    _dir_check  = str(ms.get('signal_dir', ms.get('direction', '')))
    _dz_score   = float(cf.get('total', 0) or 0)
    _dz_grade   = float(cf.get('effective_grade', cf.get('structure_grade', cf.get('grade', 0))) or 0)
    _dz_rsi1h   = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)

    if 'BEAR_TREND' in _regime_str and _dir_check == 'LONG':
        # 精英解锁：score≥155 AND grade≥90 AND RSI_1H<20（极度超卖底部反弹）
        _bt_elite = (_dz_score >= 155 and _dz_grade >= 90 and _dz_rsi1h < 20)
        if _bt_elite:
            print(f'[死穴-精英解锁] {_sym} BEAR_TREND_LONG: score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 RSI={_dz_rsi1h:.0f}<20 → 0.5%NAV观察仓')
            cf['breakdown']['死穴精英解锁'] = f'BEAR_TREND_LONG RSI={_dz_rsi1h:.0f}<20底部反弹 score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 → 0.5%NAV'
        else:
            _valid = False
            cf['breakdown']['死穴封禁'] = f'BEAR_TREND_LONG WR=45%(铁证n=3322) 未达精英解锁[score≥155+grade≥90+RSI<20] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BEAR_TREND_LONG: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BULL_TREND' in _regime_str and _dir_check == 'SHORT':
        # 精英解锁：score≥155 AND grade≥90 AND RSI_1H>75（高RSI顶部结构做空）
        _bu_elite = (_dz_score >= 155 and _dz_grade >= 90 and _dz_rsi1h > 75)
        if _bu_elite:
            print(f'[死穴-精英解锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 RSI={_dz_rsi1h:.0f}>75 → 0.5%NAV观察仓')
            cf['breakdown']['死穴精英解锁'] = f'BULL_TREND_SHORT RSI={_dz_rsi1h:.0f}>75顶部结构做空 score={_dz_score:.0f}≥155 grade={_dz_grade}≥90 → 0.5%NAV'
        else:
            _valid = False
            cf['breakdown']['死穴封禁'] = f'BULL_TREND_SHORT WR=47.7%(铁证n=4999) 未达精英解锁[score≥155+grade≥90+RSI>75] score={_dz_score:.0f} RSI={_dz_rsi1h:.0f}'
            print(f'[死穴-封锁] {_sym} BULL_TREND_SHORT: score={_dz_score:.0f} grade={_dz_grade} RSI={_dz_rsi1h:.0f}')
    elif 'BEAR_RECOVERY' in _regime_str and _dir_check == 'SHORT':
        # [v25.4死穴修复 2026-06-27] BEAR_RECOVERY_SHORT WR=46.6%/46.0% 升级为物理封锁
        # 达摩院铁证 n=233(BTC)/238(ETH) avg_pnl=-0.183/-0.305
        # 例外解锁：score>=145 AND grade>=90 AND Kronos p_up<0.2
        _br_score = cf.get('total', 0)
        _br_grade = cf.get('grade', 0)
        _br_pup   = cf.get('s23_p_up', 1.0)
        # [v25.4b防封闭修复] 例外条件放宽：145→140, 90→85, 0.2→0.25
        # 理由：n=233次铁证，非宪法级死穴，不应过严封闭
        if not (_br_score >= 140 and _br_grade >= 85 and _br_pup < 0.25):
            _valid = False
            cf['breakdown']['死穴封禁'] = (
                f'BEAR_RECOVERY_SHORT WR=46% 物理封锁[v25.4b] '
                f'score={_br_score:.0f} grade={_br_grade} p_up={_br_pup:.2f}'
            )
            print(f'[死穴-BEAR_RECOVERY_SHORT] {_sym} 封锁: score={_br_score:.0f} grade={_br_grade} p_up={_br_pup:.2f}')
        else:
            print(f'[死穴-BEAR_RECOVERY_SHORT] {_sym} 精英解锁: score={_br_score:.0f}>=140 grade={_br_grade}>=85 p_up={_br_pup:.2f}<0.25')
    # ────────────────────────────────────────────────────────────────────────────

    # ── [P0-B 设计院 2026-06-21] BULL_TREND宏观核验门 ────────────────────────────
    # 问题：实盘回溯 BULL_TREND_LONG MAE=10.7%，小市技术反弹被误识别为 BULL_TREND
    # 修复：当 regime=BULL_TREND 且 price < EMA200日线 时，强制降级为 BEAR_RECOVERY
    # 依据：宏观熏市中日山微分不是 BULL_TREND，该信号应按 BEAR_RECOVERY 规则处理
    # [设计院] 此门展不修改 ms['regime']，仅拦截信号输出
    try:
        _p0b_regime = str(ms.get('regime', '') or '').upper()
        _p0b_price  = float(ms.get('price', 0) or 0)
        if 'BULL_TREND' in _p0b_regime and signal_dir == 'LONG' and _p0b_price > 0:
            # 尝试拉取 EMA200日线（式 fib_macro结果已有）
            _p0b_ema200 = 0.0
            try:
                from fib_macro_engine import fib_macro_score as _p0b_fib
                _p0b_res = _p0b_fib(symbol=_sym, price=_p0b_price, signal_dir='LONG')
                _p0b_ema200 = float(_p0b_res.get('ema200', 0) or 0)
            except: pass
            if _p0b_ema200 > 0 and _p0b_price < _p0b_ema200:
                # 价格在 EMA200 以下，不应该是 BULL_TREND
                _score_gate_ok = False
                cf['breakdown']['P0B_BULL_TREND_MACRO'] = (
                    f'[P0-B宏观门] price={_p0b_price:.2f} < EMA200={_p0b_ema200:.2f} '
                    f'宏观熏市，禁止 BULL_TREND_LONG 信号输出'
                )
                print(f'[P0B-MacroGate] 🛑 {_sym} BULL_TREND_LONG 被拦截 '
                      f'price={_p0b_price:.2f} < EMA200={_p0b_ema200:.2f} (宏观熏市)')
    except Exception as _p0b_e:
        pass
    # ── [END P0-B 宏观门] ──────────────────────────────────────────────────────────

    _valid = cf['kelly_mult'] > 0 and params['valid'] and _score_gate_ok
    # [P2-B] N14体制边界追踪 — 记录当前体制稳定度（供brahma_core判断早鸟加成）
    _regime_now = str(ms.get('regime','') or '')
    try:
        import json as _j; from pathlib import Path as _P
        _rts_f = _P(__file__).parent.parent / 'data' / '_regime_timing_state.json'
        _rts = _j.loads(_rts_f.read_text()) if _rts_f.exists() else {}
        _last_regime = _rts.get('last_regime','')
        _last_change_ts = _rts.get('last_change_ts', 0)
        import time as _tm
        _now_ts = _tm.time()
        if _last_regime != _regime_now:
            _rts = {'last_regime': _regime_now, 'last_change_ts': _now_ts, 'last_regime_prev': _last_regime}
            _rts_f.write_text(_j.dumps(_rts))
        _regime_age_h = (_now_ts - _last_change_ts) / 3600
        extra_data['regime_timing'] = {
            'current': _regime_now,
            'age_hours': round(_regime_age_h, 1),
            'is_early': _regime_age_h < 5,   # 体制切换5h内为"早鸟"
            'prev': _rts.get('last_regime_prev','')
        }
        # 早鸟加成（N14: BEAR_TREND(熊市趋势) early PF=1.625）
        if _regime_age_h < 5 and 'BEAR_TREND' in _regime_now and signal_dir == 'SHORT' and _score_gate_ok:
            _score_raw = round(_score_raw * 1.04, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['n14_early_bird'] = f'BEAR_TREND早鸟({_regime_age_h:.1f}h) ×1.04'
            print(f'[BrahmaBrain] 🦅 {_sym} N14早鸟: {_regime_now} {_regime_age_h:.1f}h 进入 score→{_score_raw:.0f}')

        # ── [P3 TREND_fresh Elite v3.0 苏摩111 2026-06-28] ─────────────────
        # 铁证：TREND体制刚进入1-2根4H K线时 WR=75.6% EV=+0.687%（v3.0实盘对齐 n=334）
        # 机制：从 _regime_timing_state 的 age_hours 换算4H根数（1根4H≈4h）
        # 条件：顺势方向 + fresh窗口(≤2根≈≤8h) + score门控通过
        _bars_est = max(1, round(_regime_age_h / 4))  # 时间→4H根数估算
        extra_data['regime_timing']['bars_est'] = _bars_est
        _trend_fresh_regimes = {
            'BEAR_TREND': 'SHORT',
            'BULL_TREND': 'LONG',
        }
        _tf_expected_dir = _trend_fresh_regimes.get(_regime_now)

        # ── [P1 RSI>60做空专项加分 v3.0 苏摩111 2026-06-28] ──────────────────
        # 铁证：BTC BEAR_TREND_SHORT RSI>60 WR=68.1% EV=+0.458%（vs RSI<40 EV=+0.169%）
        # 条件：BEAR_TREND体制 + SHORT方向 + RSI>60
        _rsi_for_p1 = float(ms.get('rsi_1h', ms.get('rsi', 50)) if ms else 50)
        if (signal_dir == 'SHORT'
                and 'BEAR_TREND' in _regime_now
                and _rsi_for_p1 > 60
                and _score_gate_ok
                and not _direction_block):
            _p1_bonus = 5  # +5分：RSI>60做空 EV差2.7倍
            _score_raw = round(_score_raw + _p1_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p1_rsi60_short'] = (
                f'RSI>60做空({_rsi_for_p1:.0f}) +{_p1_bonus}分 WR=68.1%(v3.0)')
            print(f'[P1-RSI60] 🎯 {_sym} RSI={_rsi_for_p1:.0f} BEAR_TREND SHORT: +{_p1_bonus}分 score→{_score_raw:.0f}')
        # ── [END P1 RSI>60] ──────────────────────────────────────────────────

        if (_bars_est <= 2
                and _tf_expected_dir == signal_dir
                and _score_gate_ok
                and _regime_now in _trend_fresh_regimes):
            _fresh_bonus = 15  # [v3.0 苏摩111 2026-06-28] +15分：达摩院v3.0铁证 BTC WR=75.6% EV=+0.687% n=334
            _score_raw = round(_score_raw + _fresh_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p3_trend_fresh'] = (
                f'TREND_fresh({_regime_now} age≈{_bars_est}根) +{_fresh_bonus}分 WR=75.6%(v3.0)')
            print(f'[P3-TrendFresh] 🔥 {_sym} {_regime_now}×{signal_dir}: '
                  f'age≈{_bars_est}根4H +{_fresh_bonus}分 WR=75.6% EV=+0.687% score→{_score_raw:.0f}')
        elif (_bars_est in (3, 4)
                and _tf_expected_dir == signal_dir
                and _score_gate_ok
                and _regime_now in _trend_fresh_regimes):
            # [v3.0 苏摩111 2026-06-28] EARLY_golden +8分：BTC WR=62.6% EV=+0.282% n=255
            _early_bonus = 8
            _score_raw = round(_score_raw + _early_bonus, 1)
            cf['total'] = _score_raw
            cf.setdefault('breakdown', {})['p3_trend_early'] = (
                f'TREND_early({_regime_now} age≈{_bars_est}根) +{_early_bonus}分 WR=62.6%(v3.0)')
            print(f'[P3-TrendEarly] 📈 {_sym} {_regime_now}×{signal_dir}: '
                  f'age≈{_bars_est}根4H +{_early_bonus}分 WR=62.6% EV=+0.282% score→{_score_raw:.0f}')
        # ── [END P3 TREND_fresh/early] ────────────────────────────────────────
    except Exception: pass

    # ── [B2 v2 2026-05-31 设计院重写] 结构甜点区奖励 ────────────────────────────
    # 实证铁律（376条live信号）：
    #   gap<0.5%   实盘SL组均值0.57% → 极危险，入场即止损 → -15分
    #   gap 0.5-1.0% 同属SL危险区       → -8分
    #   gap 1.0-1.5% WR=40%            → 边界，中性 → 0分
    #   gap 1.5-4.0% TP组均值2.43% WR=100% → 甜点区 → +15分
    #   gap>4%   偏远难触发              → -5分
    # 铁证来源：52条实盘结算 TP组gap均值=2.43% vs SL组gap均值=0.57%（2026-05-31）
    try:
        _entry_lo_b2 = float(params.get('entry_lo', 0) or 0)
        _price_b2    = float(ms.get('price', 0) or 0)
        _b2_bonus    = 0

        # [P0-A B2-fix 2026-06-17] 修复LONG方向gap计算（原逻辑只处理SHORT）
        _entry_hi_b2 = float(params.get('entry_hi', params.get('entry_lo', 0)) or 0)
        _gap_b2 = 0.0
        _b2_dir_ok = False
        if _entry_lo_b2 and _price_b2 and signal_dir == 'SHORT':
            _gap_b2 = (_entry_lo_b2 - _price_b2) / _price_b2 * 100
            _b2_dir_ok = True
        elif _entry_hi_b2 and _price_b2 and signal_dir == 'LONG':
            # LONG: 价格回落到入场区间，gap = (price - entry_hi) / price * 100
            # gap<0 = 已在区间内（最优），gap>0 = 还需等待回落
            _gap_b2 = (_price_b2 - _entry_hi_b2) / _price_b2 * 100
            _b2_dir_ok = True
        if _b2_dir_ok and (_entry_lo_b2 if signal_dir=='SHORT' else _entry_hi_b2):
            if _gap_b2 < 0.5:
                # [v3修复 2026-05-31] 极危险：入场即止损，SL组实盘均值0.57%在此区间
                _b2_bonus = -15
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}%<0.5% 极危险(WR=3%) -15'  # [B2-fix]
            elif _gap_b2 < 1.0:
                # [v3修复 2026-05-31] 危险区：SL组均值0.57%全部落在此区间
                _b2_bonus = -8
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 危险区(SL高频) -8'  # [B2-fix]
            elif _gap_b2 <= 1.5:
                # 边界区，中性
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 边界区 中性'  # [B2-fix]
            elif _gap_b2 <= 4.0:
                # 甜点区：TP组实盘均值2.43%，WR=100%实证奖励
                _b2_bonus = 15
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}% 甜点区(WR=100%) +15'  # [B2-fix]
            else:
                # >4% 偏远难触发
                _b2_bonus = -5
                cf = copy.deepcopy(cf)
                cf['b2_proximity'] = f'gap={_gap_b2:.2f}%>4% 偏远难触发 -5'  # [B2-fix]

        if _b2_bonus != 0 and _score_gate_ok:
            _score_raw = round(_score_raw + _b2_bonus, 1)
            cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
            if _score_raw < 0: _score_raw = 0
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['total'] = _score_raw
            print(f'[B2-Structure] {"⚠️" if _b2_bonus < 0 else "✅"} {_sym}: gap={_gap_b2:.2f}% {_b2_bonus:+d}分 → {_score_raw:.0f}')

        # ── [B2 v5 V2.0报告P0-A修复 2026-06-05] GapGate逻辑倒转
        # Round2铁证：BTC/ETH全部55+58条成功信号 gap均<0.5%（gap越小=最优入场）
        # 原逻辑完全反了：gap<0.8%需165分 = 封锁最赚钱的信号类型
        # 新规则（按V2.0报告）：
        #   gap < 0   → 价格在入场区内，直接允许（最优状态）
        #   gap 0~0.5% → 贴近区间，score≥140允许（非常好）
        #   gap 0.5~1% → 轻微偏离，score≥140允许（好）
        #   gap 1~3%  → 回调区间，score≥150允许（一般）
        #   gap 3~5%  → 偏远，score≥160允许（需结构极强）
        #   gap > 5%  → 极偏远，score≥165允许（稀有但允许）
        try:
            if _entry_lo_b2 and _price_b2 and signal_dir == 'SHORT':
                _gap_check = (_entry_lo_b2 - _price_b2) / _price_b2 * 100
                if _gap_check < 0:
                    # 价格已在入场区内 → 最佳状态，直接通过
                    cf['gap_gate'] = f'gap={_gap_check:.2f}% 价格在入场区内 命中 通过'
                    print(f'[GapGate] ✅ {_sym}: gap={_gap_check:.2f}% 价格在入场区内，允许')
                # [v24.3-fix] GapGate: score=0清零 → 按gap比例降权
                # 哲学：距入场区越远惩罚越重，但不清零——让grade门控最终拍板
                elif _gap_check < 0.5:   _gap_penalty = 0   # 贴近：不惩罚
                elif _gap_check < 1.0:   _gap_penalty = 4   # [六方修复] 6→4，BEAR_RECOVERY追涨行情轻惩
                elif _gap_check < 2.0:   _gap_penalty = 8   # [六方修复] 12→8
                elif _gap_check < 3.0:   _gap_penalty = 14  # [六方修复] 18→14，3%内不过分惩罚
                elif _gap_check < 5.0:   _gap_penalty = 22  # [六方修复] 25→22
                elif _gap_check < 10.0:  _gap_penalty = 32  # [六方修复] 35→32
                elif _gap_check < 20.0:  _gap_penalty = 45  # [六方修复] 50→45
                # BEAR_RECOVERY/BULL_EARLY体制额外宽松（追涨不追跌是反弹特征）
                _gap_regime = ms.get('regime','') if ms else ''
                if _gap_regime in ('BEAR_RECOVERY','BULL_EARLY','BULL_CORRECTION') and _gap_check < 5.0:
                    _gap_penalty = max(0, _gap_penalty - 8)  # 反弹体制减8分惩罚
                else:  # gap>20% 直接封锁
                    _gap_penalty = 0; _score_raw = 0; cf['total'] = 0  # [P1-B fix] gap>20%极端封锁
                if _gap_check >= 0.5:
                    _score_raw = max(0, _score_raw - _gap_penalty)
                    cf['total'] = _score_raw  # [P0-B audit-fix] 同步评分
                    cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
                    cf['gap_gate'] = f'gap={_gap_check:.2f}% -惩罚{_gap_penalty}分 → score={_score_raw:.0f}'
                    print(f'[GapGate] ⚠️ {_sym}: gap={_gap_check:.2f}% -{_gap_penalty}分 score={_score_raw:.0f}')
                else:
                    cf['gap_gate'] = f'gap={_gap_check:.2f}%<0.5% 贴近 通过'
                    print(f'[GapGate] ✅ {_sym}: gap={_gap_check:.2f}% 贴近')
        except Exception: pass
    except Exception: pass
    # ── [END B2 v3] | B2 v3 段结束 ──────────────────────────────────────────────────────────

    # ── [设计院 2026-05-31] 可交易性辅助（结构门已是主力）──────────────────
    # 注：ATR门卫和WR封顶已移除，由结构质量引擎(L0)负责识别
    # 只保留入场区偏离作为轻微提示，不再是主要惩罚
    try:
        _entry_lo_t = float(params.get('entry_lo', 0) or 0)
        _price_t    = float(ms.get('price', 0) or 0)
        _t_penalty  = 0

        # 入场区偏离（保留，但只作轻提示，结构门已处理主要问题）
        if _entry_lo_t and _price_t and signal_dir == 'SHORT':
            _entry_gap = (_entry_lo_t - _price_t) / _price_t * 100
            if _entry_gap > 5.0:
                _t_penalty += 15   # 从30降至15，结构门已惩罚
                cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['t_score_gap'] = f'入场区偏离{_entry_gap:.1f}%>5% -15分'
            elif _entry_gap > 3.0:
                _t_penalty += 8    # 从15降至8
                cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['t_score_gap'] = f'入场区偏离{_entry_gap:.1f}%>3% -8分'

        if _t_penalty > 0 and _score_gate_ok:
            _score_raw = max(0, round(_score_raw - _t_penalty, 1))
            cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用; cf['total'] = _score_raw
    except Exception:
        pass
    # ── [END 可交易性辅助] ────────────────────────────────────────────────────

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——structure_gate模块：v2封锁时跳过
    # ── [设计院 2026-05-31] L0 结构质量门（Structure Quality Gate）─────────
    # 哲学：好信号的本质是「入场区有真实价格结构」，而非「评分高」
    # 无结构入场(grade<30) = 拒绝，无论评分多高
    try:
        from structure_quality_engine import evaluate_structure_quality, get_time_weight  # [D1-note] 按需import(主SQE)
        _sq = evaluate_structure_quality(
            symbol     = _sym,
            signal_dir = signal_dir,
            price      = float(ms.get('price', 0)),
            entry_lo   = float(params.get('entry_lo', 0) or 0),
            entry_hi   = float(params.get('entry_hi', 0) or 0),
            smc        = smc,
            swing_4h   = ms.get('swing_4h', {}),
            key_levels = ms.get('key_levels', {}),
            momentum   = ms.get('momentum', {}),
            trigger_confidence = int(params.get('trigger_15m_confidence', 0) or cf.get('trigger_15m_confidence', 0) or 0),  # [v24.5-fix] 优先从 params 读取
        )
        cf = copy.deepcopy(cf)  # [P1-C audit-fix] 防止breakdown浅拷贝共享引用
        cf['structure_grade']  = _sq['grade']
        cf['structure_label']  = _sq['label']
        cf['structure_sources']= _sq['sources']

        # ── 结构质量联合门控 [v24.2 2026-06-12 铁征升级] ─────────────────────
        # 武曲Paper干净68条实战铁证：
        #   grade≥70 (A级): WR=92% TO率=8%   → 正常通过
        #   grade 50-69 (B级): WR=27% TO率=73% → 全局封堵
        #   grade 25-49 (C级): WR=0%  TO玗=100% → 封堵
        #   grade<25 (X级):   完全无结构 → 封堵

        # ── [effective_grade v25.4b 2026-06-27] 体制感知 grade 修正 ──────────
        # 哲学：同样的 OB 结构，在不同体制下可信度不同
        # 熊市做多 = 趋势反向，OB 支撑极易被贯穿，grade 实际价值打折
        # 铁证依据：BEAR_TREND_LONG grade_<70 WR=44.6% vs grade_90+ WR=71.3%
        #           BULL_TREND_SHORT 对称成立
        # 设计院×达摩院六方裁决 2026-06-27
        _REGIME_GRADE_MULT = {
            # LONG 方向：顺势=1.0，逆势递减至0.72
            ('BULL_TREND',      'LONG'):  1.00,
            ('BULL_EARLY',      'LONG'):  0.95,
            ('BULL_CORRECTION', 'LONG'):  0.90,
            ('BULL_RECOVERY',   'LONG'):  0.92,
            ('CHOP',            'LONG'):  0.88,
            ('CHOP_MID',        'LONG'):  0.88,
            ('CHOP_HIGH',       'LONG'):  0.85,
            ('CHOP_LOW',        'LONG'):  0.90,
            ('BEAR_RECOVERY',   'LONG'):  0.88,
            ('BEAR_EARLY',      'LONG'):  0.82,
            ('BEAR_CORRECTION', 'LONG'):  0.80,
            ('BEAR_TREND',      'LONG'):  0.72,  # 最危险：逆势做多 WR=44.6%
            # SHORT 方向：顺势=1.0，逆势递减至0.72
            ('BEAR_TREND',      'SHORT'): 1.00,
            ('BEAR_EARLY',      'SHORT'): 0.95,
            ('BEAR_CORRECTION', 'SHORT'): 0.90,
            ('BEAR_RECOVERY',   'SHORT'): 0.88,
            ('CHOP',            'SHORT'): 0.88,
            ('CHOP_MID',        'SHORT'): 0.88,
            ('CHOP_HIGH',       'SHORT'): 0.85,
            ('CHOP_LOW',        'SHORT'): 0.90,
            ('BULL_RECOVERY',   'SHORT'): 0.88,
            ('BULL_EARLY',      'SHORT'): 0.82,  # 死穴体制 WR=51.6%
            ('BULL_CORRECTION', 'SHORT'): 0.80,
            ('BULL_TREND',      'SHORT'): 0.72,  # 最危险：逆势做空 WR=48.2%
        }
        _raw_grade   = int(cf.get('structure_grade', 0) or 0)
        _regime_key  = str(ms.get('regime', '')).upper()
        # 体制键匹配：优先精确匹配，fallback到前缀匹配
        _mult = 1.00  # 默认不降权
        for (r_pat, d_pat), m in _REGIME_GRADE_MULT.items():
            if signal_dir == d_pat and (r_pat in _regime_key or _regime_key.startswith(r_pat)):
                _mult = m
                break
        _eff_grade = round(_raw_grade * _mult, 1)
        cf['effective_grade'] = _eff_grade
        cf['grade_mult']      = _mult
        if _mult < 1.00:
            print(f'[RegimeGrade] {_sym} {_regime_key}×{signal_dir}: grade {_raw_grade}×{_mult}={_eff_grade:.1f}')
        # StructureGate 使用 effective_grade
        _sq = {'grade': _eff_grade, 'label': cf.get('structure_label', f'grade={_eff_grade:.0f}')}
        # [v25.4 死穴修复 2026-06-27] StructureGate 门槛 70→80
        # 设计院达摩院六方裁决：grade70-80 实测WR=47%（死亡区），与grade<70同性质
        # 真正优质结构从 grade≥80 开始（WR=69.8%）
        if _sq['grade'] < 80:   # [v25.4] 70→80 铁证：grade70-80 WR=47% 与低grade同级
            # grade<80: 包含grade70-79死亡区（WR=47%）全部封堵
            _score_raw = 0
            cf['total'] = 0
            cf['action'] = 'SKIP'
            cf['kelly_mult'] = 0
            cf['structure_reject'] = f'grade={_sq["grade"]}({_sq["label"]}) grade<80 WR=47%死亡区封堵 [v25.4]'
            print(f'[StructureGate] 🚫 {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]}<80 → WR=47%死亡区封堵')
        elif _sq['grade'] >= 90:
            _sq_bonus = round((_sq['grade'] - 80) * 0.3, 1)
            _score_raw = round(_score_raw + _sq_bonus, 1)
            cf['total'] = _score_raw
            print(f'[StructureGate] ✅ {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]} +{_sq_bonus}分 → {_score_raw:.0f}')
        else:  # grade 80-89
            _sq_bonus = round((_sq['grade'] - 80) * 0.15, 1)
            _score_raw = round(_score_raw + _sq_bonus, 1)
            cf['total'] = _score_raw
            print(f'[StructureGate] ✅ {_sym} {signal_dir}: {_sq["label"]} grade={_sq["grade"]} +{_sq_bonus}分 → {_score_raw:.0f}')
        # [v25.4] grade 80-89: 正常通过，小额加分
        # else分支不需要（grade<70已在if分支封堵）

        # 时间权重：记录但不惩罚（UTC14-16样本仅12条，统计不显著）
        _utc_hour = _dt.datetime.now(_dt.timezone.utc).hour
        _tw = get_time_weight(_utc_hour)
        cf['time_weight_ref'] = f'UTC{_utc_hour:02d}:00 ref={_tw}'  # 仅记录，不调分
    except Exception as _sqe:
        pass
    # ── [END 结构质量门] ──────────────────────────────────────────────────────

    # ── [v25.7 设计院 2026-06-18] P0 体制专项过滤器 ─────────────────────────
    # 原则：为交易而生，不封禁；通过精准条件过滤提升低WR组合质量
    # 每个体制×方向组合针对其根本失败原因做专项检测
    try:
        _regime_now = _matched_regime_key or ''
        _p0_reject  = False
        _p0_reason  = ''

        # ── P0-A: BULL_CORRECTION（牛市回调）× LONG ────────────────────────
        # 根因：接刀问题（回调未到OB支撑位就做多）+ ob_dist>1.5%失去锚点
        # 修复：强制要求 ob_dist_pct<1.5%（B级以上精准支撑）
        if _regime_now == 'BULL_CORRECTION' and signal_dir == 'LONG':
            _ob_dist = cf.get('ob_dist_pct', 99)
            if _ob_dist is None: _ob_dist = 99
            if float(_ob_dist) > 1.5:
                _p0_reject = True
                _p0_reason = f'P0-A BULL_CORRECTION_LONG: ob_dist={_ob_dist:.2f}%>1.5%（未到OB支撑位，拒绝接刀）'

        # ── P0-B: BEAR_RECOVERY（熊市反弹）× SHORT ─────────────────────────
        # 根因：反弹途中做空=与动能对抗；只有反弹至阻力位才有alpha
        # 修复：要求 price≥swing_high_4h×0.95（反弹至4H摆动高点附近才空）
        elif _regime_now == 'BEAR_RECOVERY' and signal_dir == 'SHORT':
            try:
                _sw4h_h = cf.get('swing_high_4h', 0) or 0
                _cur_price = ms.get('price', ms.get('close', 0)) or 0
                if _sw4h_h > 0 and _cur_price > 0:
                    _dist_to_swing = (_sw4h_h - _cur_price) / _sw4h_h
                    if _dist_to_swing > 0.05:   # 距4H高点>5%，反弹尚未到位
                        _p0_reject = True
                        _p0_reason = (f'P0-B BEAR_RECOVERY_SHORT: price={_cur_price:.1f} '
                                      f'距4H高点{_dist_to_swing*100:.1f}%>5%（反弹未到阻力位，拒绝逆势空）')
            except Exception:
                pass  # 数据不可用时放行

        # ── P0-C: BULL_TREND（牛市趋势）× SHORT 回调深度过滤 ──────────────
        # 根因：牛市小回调噪音做空，没有吃到中级回调
        # 修复：价格需从近期高点下跌≥1.2×ATR（真正的中级回调信号）
        elif _regime_now == 'BULL_TREND' and signal_dir == 'SHORT':
            try:
                _atr4h = ms.get('atr_4h', ms.get('atr', 0)) or 0
                _high4h = max(ms.get('highs_4h', ms.get('highs', [0]))[-6:] or [0])
                _cur_price = ms.get('price', ms.get('close', 0)) or 0
                if _atr4h > 0 and _high4h > 0 and _cur_price > 0:
                    _pullback = (_high4h - _cur_price) / _cur_price
                    _atr_pct  = _atr4h / _cur_price
                    if _pullback < _atr_pct * 1.2:
                        # 回调幅度不足1.2×ATR，小回调噪音，门控+10
                        _score_raw = round(_score_raw - 10, 1)
                        cf['total'] = _score_raw
                        cf['p0c_pullback_penalty'] = f'-10(回调{_pullback*100:.1f}%<1.2×ATR{_atr_pct*100:.1f}%)'
            except Exception:
                pass

        # ── P0-D: BEAR_TREND（熊市趋势）× LONG BOTTOMING子阶段奖励 ────────
        # 根因：BOTTOMING阶段（RSI超卖+背离+Higher Low）有真实alpha
        # 修复：检测到BOTTOMING特征时，门控降低-15（增加通过机会）
        elif _regime_now == 'BEAR_TREND' and signal_dir == 'LONG':
            try:
                _phase_1h  = str(ms.get('phase_1h', ms.get('phase', ''))).upper()
                _rsi_1h    = ms.get('rsi', ms.get('rsi_1h', 50)) or 50
                _phase_4h  = str(ms.get('phase_4h', '')).upper()
                _is_bottom = (_phase_1h in ('BOTTOMING','PULLBACK_UP') and _rsi_1h < 38)
                _is_4h_ok  = (_phase_4h in ('BOTTOMING','UPTREND','PULLBACK_UP'))
                if _is_bottom and _is_4h_ok:
                    # 真正的底部结构 → 额外奖励（相当于门控降低）
                    _bot_bonus = 15
                    _score_raw = round(_score_raw + _bot_bonus, 1)
                    cf['total'] = _score_raw
                    cf['p0d_bottoming_bonus'] = f'+{_bot_bonus}(BOTTOMING结构:1H={_phase_1h} RSI={_rsi_1h:.0f} 4H={_phase_4h})'
            except Exception:
                pass

        if _p0_reject:
            _score_gate_ok = False
            cf['p0_reject'] = _p0_reason
            cf['kelly_mult'] = 0
            print(f'[P0SpecialFilter] 🚫 {_sym} {signal_dir}: {_p0_reason[:80]}')

    except Exception as _p0e:
        pass  # P0过滤器异常不阻塞主流程

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——n20模块：v2封锁时跳过
    # ── [设计院 2026-06-07] N20 LSR+OI联合评分（六方辩论落地）────────────────
    # 实证：ETH多头70.9%→空头做空+15分，OI减少+价格涨→做多-12分
    try:
        from lsr_oi_engine import lsr_oi_score as _lsr_oi_fn
        _lsr_oi_res  = _lsr_oi_fn(
            symbol    = _sym,
            signal_dir= signal_dir,
            long_pct  = ms.get('sentiment', {}).get('long_short_ratio'),
            oi_change_pct = ms.get('sentiment', {}).get('oi_change_pct'),
            oi_momentum   = ms.get('sentiment', {}).get('oi_momentum'),
        )
        _lsr_oi_pts = _lsr_oi_res.get('score', 0)
        if _lsr_oi_pts != 0 and _score_raw > 0:
            _score_raw = round(_score_raw + _lsr_oi_pts, 1)
            cf['total'] = _score_raw
            cf['n20_lsr_oi'] = _lsr_oi_res.get('note', '')
            print(f'[N20-LSR/OI] {_sym} {signal_dir}: {_lsr_oi_pts:+d}分 → {_score_raw:.0f} | {_lsr_oi_res.get("note","")[:60]}')
    except Exception as _lsr_e:
        pass
    # ── [END N20 LSR+OI] | N20 多空比+持仓量段结束 ─────────────────────────────────────────────────────

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——n21模块：v2封锁时跳过
    # ── [设计院 2026-06-07] N21 宏观Fib+EMA200+周线RSI（六方辩论落地）────────
    # 实证：ETH低于EMA200(-14.8%)→做多-10，周线RSI=50(非底部)→做多-8
    try:
        from fib_macro_engine import fib_macro_score as _fib_macro_fn
        _fib_res  = _fib_macro_fn(
            symbol    = _sym,
            price     = float(ms.get('price', 0)),
            signal_dir= signal_dir,
        )
        _fib_pts = _fib_res.get('score', 0)
        if _fib_pts != 0 and _score_raw > 0:
            _score_raw = round(_score_raw + _fib_pts, 1)
            cf['total'] = _score_raw
            cf['n21_fib_macro'] = f"regime={_fib_res.get('regime_tag','')} ema200=${_fib_res.get('ema200',0):,.0f} wRSI={_fib_res.get('weekly_rsi',0):.0f} {_fib_pts:+d}pts"
            print(f'[N21-FibMacro] {_sym} {signal_dir}: {_fib_pts:+d}分 → {_score_raw:.0f} | {_fib_res.get("regime_tag","")} ema200=${_fib_res.get("ema200",0):,.0f}')
    except Exception as _fib_e:
        pass
    # ── [END N21 宏观Fib] ────────────────────────────────────────────────────


    # ── [N22b] WR矩阵动态加成层 [设计院封印 2026-06-27] ──────────────────────
    # 职责：读取 dharma_runtime.wr_matrix_v7，为主战场体制提供实证WR加分
    # BEAR_TREND×SHORT=71.3% n=1188 → +4分；BLOCK体制→-15分
    try:
        import json as _j22b
        _dm22b = _j22b.loads(open('data/dharma_runtime.json').read())
        _wv7   = _dm22b.get('wr_matrix_v7', {})
        # [方案C v25.4 苏摩审批] 周期感知查找：优先 REGIME_DIR_TF，fallback REGIME_DIR
        _tf22    = ms.get('entry_tf', ms.get('tf', '15M'))  # 信号触发周期
        _combo22     = f"{ms.get('regime','').upper()}_{signal_dir}"
        _combo22_tf  = f"{ms.get('regime','').upper()}_{signal_dir}_{_tf22}"
        _sym_wv7 = _wv7.get(_sym, {})
        # 优先使用带周期的精确键，fallback到混合键
        _wdata22 = _sym_wv7.get(_combo22_tf) or _sym_wv7.get(_combo22, {})
        if _sym_wv7.get(_combo22_tf):  # 命中周期分层
            _combo22 = _combo22_tf  # 用于日志显示
        _wr22b   = _wdata22.get('wr', 0)
        _n22b    = _wdata22.get('n', 0)
        _act22   = _wdata22.get('action', 'SKIP')
        _pts22b  = 0
        if _act22 == 'ALLOW' and _n22b >= 500 and _wr22b > 0:
            _pts22b = max(-10, min(15, round((_wr22b - 0.50) * 20)))
        elif _act22 in ('BLOCK', 'PERMANENT_BLOCK'):
            _pts22b = -15
        elif _act22 == 'PENALIZE':  # [v25.4] 新增：宪法级潜伏死穴惩罚
            _pts22b = int(_wdata22.get('penalize_pts', -10))
        if _pts22b != 0:
            _score_raw += _pts22b
            cf['n22b_wr_matrix'] = f'N22b_WR矩阵:{_pts22b:+d}({_combo22} wr={_wr22b:.1%} n={_n22b})'
            print(f'[N22b-WRMatrix] {_sym} {_combo22}: {_pts22b:+d}分 WR={_wr22b:.1%} n={_n22b}')
    except Exception:
        pass
    # ── [END N22b] ──────────────────────────────────────────────────────────
    # ── [EarlyTrendGate v25.4 死穴修复 2026-06-27] ──────────────────────────
    # 针对宪法级死穴：BULL_EARLY_SHORT(n=5526 WR=51.6%) / BEAR_EARLY_LONG(n=5070 WR=50.5%)
    # 机制：体制逆势方向检测 → N22b已-10分 + 结构确认再-8分（叠加-18分）
    # 豁免：RSI极值（超卖<25做多 / 超买>75做空）→ 仅保留-10分
    try:
        _etg_regime = str(ms.get('regime', '')).upper()
        _etg_dir    = signal_dir
        _etg_rsi1h  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        _etg_active = False
        _etg_exempt = False  # RSI极值豁免

        if 'BULL_EARLY' in _etg_regime and _etg_dir == 'SHORT':
            _etg_active = True
            _etg_exempt = (_etg_rsi1h > 75)  # 超买区做空，豁免结构惩罚
        elif 'BEAR_EARLY' in _etg_regime and _etg_dir == 'LONG':
            _etg_active = True
            _etg_exempt = (_etg_rsi1h < 25)  # 极度超卖做多，豁免结构惩罚

        if _etg_active and not _etg_exempt:
            # [P1-2 设计院 2026-06-30] 双重惩罚修复：乘数→惩罚 二选一
            # 原：N22b已-10分 + ETG再-8分 = -18分（双重惩罚哲学矛盾）
            # 新：N22b已有惩罚 → ETG仅补充-3分（确保不超过-10分总惩罚上限）
            # 逻辑：N22b是数据驱动的WR惩罚，ETG是体制方向确认，职责不同不应叠加同等权重
            _etg_n22b_applied = cf.get('n22b_wr_matrix', '') != ''  # N22b是否已惩罚
            _etg_penalty = -3 if _etg_n22b_applied else -8  # N22b已惩罚则ETG仅补-3
            _score_raw = round(_score_raw + _etg_penalty, 1)
            cf['total'] = _score_raw
            cf['etg_penalty'] = (
                f'EarlyTrendGate[v25.4-P1fix]: {_etg_regime}×{_etg_dir} '
                f'逆势 RSI={_etg_rsi1h:.0f} {_etg_penalty:+d}分({"N22b已惩罚,仅补充" if _etg_n22b_applied else "独立惩罚"}) → {_score_raw:.0f}'
            )
            print(f'[EarlyTrendGate] {_sym} {_etg_regime}×{_etg_dir}: {_etg_penalty:+d}分 RSI={_etg_rsi1h:.0f} N22b已惩罚={_etg_n22b_applied}')
        elif _etg_active and _etg_exempt:
            print(f'[EarlyTrendGate] {_sym} {_etg_regime}×{_etg_dir}: RSI极值豁免 RSI={_etg_rsi1h:.0f}')
    except Exception:
        pass
    # ── [END EarlyTrendGate] ─────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════
    # [P0 苏摩111 2026-06-28] BEAR_EARLY+TC≥+1 门控
    # 正确位置：所有因子计算完毕后 _score_raw = 最终值
    # 铁证：BEAR_EARLY+tc=+1 BTC WR=91.9% ETH=84.7% (p=0.000 n=104)
    #        BEAR_EARLY+tc=-3 WR=53.8%（差距3.4倍）
    # ══════════════════════════════════════════════════════════════
    try:
        _tc_p0 = int(ms.get('tc', 0) if ms else 0)
        if 'BEAR_EARLY' in str(ms.get('regime','') if ms else '').upper() and signal_dir == 'SHORT':
            if _tc_p0 >= 1:
                _p0_bonus = 15
                _score_raw = min(175, round(_score_raw + _p0_bonus, 1))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p0_bear_early_tc'] = (
                    f'BEAR_EARLY+tc={_tc_p0:+d}(空头排列) +{_p0_bonus}分 WR=91.9%(v4.0)')
                print(f'[P0-BearEarlyTC] 🎯 {_sym} BEAR_EARLY tc={_tc_p0:+d}: +{_p0_bonus}分 score→{_score_raw:.0f}')
            elif _tc_p0 <= -2:
                _p0_penalty = -10
                _score_raw = max(0, round(_score_raw + _p0_penalty, 1))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p0_bear_early_tc'] = (
                    f'BEAR_EARLY+tc={_tc_p0:+d}(多头排列做空) {_p0_penalty}分 WR=53.8%')
                print(f'[P0-BearEarlyTC] ⚠️ {_sym} BEAR_EARLY tc={_tc_p0:+d}: {_p0_penalty}分 score→{_score_raw:.0f}')
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]

    # ══════════════════════════════════════════════════════════════
    # [P1 苏摩111 2026-06-28] BTC领先ETH（跨标的领先指标）
    # 铁证：BTC_TP后1-4H内ETH WR=85.7% EV=+1.396%（宪法级）
    #        BTC_SL后1-4H内ETH WR=21.8%（几乎必亏）
    # ══════════════════════════════════════════════════════════════
    try:
        if _sym in ('ETHUSDT',) and signal_dir == 'SHORT':
            import pathlib as _pl1, time as _tl1
            _bsp = _pl1.Path('data/btc_settlement_state.json')
            if _bsp.exists():
                _bst = __import__('json').loads(_bsp.read_text())
                _bres = _bst.get('last_result', '')
                _bts  = float(_bst.get('last_ts', 0))
                _bh   = (_tl1.time() - _bts) / 3600
                if 0 < _bh <= 4:
                    if _bres == 'TP':
                        _p1v = 20
                        _score_raw = min(175, round(_score_raw + _p1v, 1))
                        cf['total'] = _score_raw
                        cf.setdefault('breakdown', {})['p1_btc_lead'] = (
                            f'BTC_TP领先{_bh:.1f}H +{_p1v}分 WR=85.7%(宪法级)')
                        print(f'[P1-BTCLead] 🚀 ETH BTC_TP {_bh:.1f}H前: +{_p1v}分 score→{_score_raw:.0f}')
                    elif _bres == 'SL':
                        _p1v = -25
                        _score_raw = max(0, round(_score_raw + _p1v, 1))
                        cf['total'] = _score_raw
                        cf.setdefault('breakdown', {})['p1_btc_lead'] = (
                            f'BTC_SL领先{_bh:.1f}H {_p1v}分 WR=21.8%')
                        print(f'[P1-BTCLead] ☠️ ETH BTC_SL {_bh:.1f}H前: {_p1v}分 score→{_score_raw:.0f}')
    except Exception: pass

    # ══════════════════════════════════════════════════════════════
    # [P2 苏摩111 2026-06-28] 季节性月份过滤
    # 铁证：BTC 6.6年月份WR（Fisher p=0.001，OOS稳定<2%）
    # [细化 2026-07-01] 7月内部分层：上旬冷起动 / 中旬品质 / 下旬谨慎
    # ══════════════════════════════════════════════════════════════
    try:
        import datetime as _dt_p2
        _now_p2 = _dt_p2.datetime.utcnow()
        _mth = _now_p2.month
        _day = _now_p2.day
        if signal_dir == 'SHORT' and 'BEAR' in str(ms.get('regime','') if ms else '').upper():
            if _mth == 4:
                _p2v, _p2lbl = -30, '4月禁止做空(WR=50.9%)'
            elif _mth == 7:
                # 7月内部分层：达摩院铁证 n=6.6年
                if _day <= 10:
                    _p2v, _p2lbl = -15, '7月上旬冷起动期(WR最低)'
                elif _day <= 20:
                    _p2v, _p2lbl = -5,  '7月中旬回暖期(小心)'
                else:
                    _p2v, _p2lbl = -8,  '7月下旬谨慎期(WR偏低)'
            elif _mth == 9:
                _p2v, _p2lbl = -10, f'{_mth}月谨慎(WR≈55%)'
            elif _mth in (1, 5, 8, 10, 11):
                _p2v, _p2lbl = 5, f'{_mth}月好月(WR=70%+)'
            else:
                _p2v = 0; _p2lbl = ''
            if _p2v != 0:
                _score_raw = max(0, min(175, round(_score_raw + _p2v, 1)))
                cf['total'] = _score_raw
                cf.setdefault('breakdown', {})['p2_seasonal'] = (
                    f'{_p2lbl} {_p2v:+d}分 (p=0.001 OOS稳定) [{_now_p2.strftime("%m-%d")}]')
                if abs(_p2v) >= 5:
                    print(f'[P2-Seasonal] 📅 {_sym} {_p2lbl}: {_p2v:+d}分 score→{_score_raw:.0f}')
    except Exception as _e:
            if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                print(f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}')  # [可观测-v2]
    # ── [END P0/P1/P2 苏摩111 2026-06-28] ────────────────────────


    # [P0-A audit-fix 2026-06-17] 全局封锁保护——n22模块：v2封锁时跳过
    # ── [设计院 2026-06-07] N22 做市商轨道B评分（六方辩论落地）────────────────
    # 实证：LAB处于派发阶段→做空+18，吸筹阶段→做多+10
    # 轨道B品种不走主流评分框架加成，而是单独做市商阶段加分
    try:
# [CLEANED 2026-06-11] from market_maker_engine import market_maker_score as _mm_fn, is_track_b as _is_tb
        if _is_tb(_sym):
            # [P1-A audit-fix 2026-06-17 DISABLED] _mm_res  = {'score': 0}  # [DEAD: market_maker_engine removed]
            _mm_pts  = _mm_res.get('score', 0)
            if _mm_pts != 0 and _score_raw > 0:
                _score_raw = round(_score_raw + _mm_pts, 1)
                cf['total'] = _score_raw
                cf['n22_market_maker'] = f"stage={_mm_res.get('stage','')} conf={_mm_res.get('confidence',0)}% {_mm_pts:+d}pts"
                print(f'[N22-MM轨道B] {_sym} {signal_dir}: stage={_mm_res.get("stage","")} {_mm_pts:+d}分 → {_score_raw:.0f}')
    except Exception as _mm_e:
        pass
    # ── [END N22 做市商轨道B] ────────────────────────────────────────────────

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——dharma_factor模块：v2封锁时跳过
    # ── [达摩院因子引擎 2026-06-03] DharmaFactorEngine 标准化落地层 ──────────
    # 读取 dharma/factor_weights.yaml，应用所有 pending/live 因子
    # 规则：YAML数据驱动，不改代码，达摩院发现直接更新YAML即可
    try:
        import sys as _dfe_sys, os as _dfe_os
        _dfe_root = _dfe_os.path.dirname(_dfe_os.path.dirname(_dfe_os.path.abspath(__file__)))
        if _dfe_root not in _dfe_sys.path:
            _dfe_sys.path.insert(0, _dfe_root)
        from dharma.dharma_factor_engine import apply_dharma_factors as _dfe_apply
        # [达摩院v2.0 2026-06-04] 计算新因子字段，传入DharmaFactorEngine
        _rsi_1h   = float(ms.get('momentum', {}).get('rsi_1h', 50) or 50)
        _vol_r    = float(ms.get('volume', {}).get('vol_ratio', 1.0) or 1.0)
        _price_bb = ms.get('bb', {}) or {}  # BB数据
        _bb_mid   = float(_price_bb.get('mid', 0) or 0)
        _cur_price= float(ms.get('price', 0) or 0)
        _price_below_bb_mid = (_cur_price < _bb_mid) if _bb_mid > 0 else False
        _price_above_bb_mid = (_cur_price > _bb_mid) if _bb_mid > 0 else False
        _bb_upper = float(_price_bb.get('upper', 0) or 0)
        _bb_lower = float(_price_bb.get('lower', 0) or 0)
        _bb_k25u  = _cur_price <= _bb_lower * 0.998 if _bb_lower > 0 else False  # 触碰2.5σ下轨
        _bb_k25d  = _cur_price >= _bb_upper * 1.002 if _bb_upper > 0 else False  # 触碰2.5σ上轨
        # SMC FVG信息
        _smc_fvg  = smc.get('fvg', {}) if isinstance(smc, dict) else {}
        _has_fvg_l= bool(_smc_fvg.get('bullish') or _smc_fvg.get('long'))
        _has_fvg_s= bool(_smc_fvg.get('bearish') or _smc_fvg.get('short'))
        # 三重共振判断（达摩院铁证：RSI+VOL+BB）
        _triple_l = (_rsi_1h < 40 and _vol_r >= 1.1 and _price_below_bb_mid)
        _triple_s = (_rsi_1h > 60 and _vol_r >= 1.1 and _price_above_bb_mid)
        # RSI_BB双重共振（超大样本6.5万验证）
        _rsi_bb_l = (_rsi_1h < 40 and _price_below_bb_mid)
        _rsi_bb_s = (_rsi_1h > 70 and _price_above_bb_mid)
        # VOL_RSI最优量价（vol×1.2+RSI<40）
        _vol_rsi  = (_vol_r >= 1.2 and _rsi_1h < 40)
        # FVG+量能（4H最强中频）
        _fvg_v4h  = ((_has_fvg_l and signal_dir=='LONG') or (_has_fvg_s and signal_dir=='SHORT')) and _vol_r >= 1.3
        _fvg_v1h  = _fvg_v4h  # 同逻辑，通过tf区分
        # OBV方向（简单用volume趋势代理）
        _obv_pos  = _vol_r > 1.0 and ms.get('trend', {}).get('1h', {}).get('direction', '') == 'UP'
        _dfe_ctx = {
            'symbol':     _sym,
            'tf':         '4h',   # brahma主周期
            'signal_dir': signal_dir,
            'utc_hour':   __import__('datetime').datetime.now(__import__('datetime').timezone.utc).hour,
            'vol_ratio':  _vol_r,
            'rsi_1h':     _rsi_1h,
            'atr_pct':    float(params.get('sl_pct', 0.4) or 0.4),
            'range_pos':  float(cf.get('range_position', 0.5) or 0.5),
            'has_div':    bool(ms.get('momentum', {}).get('has_div', False)),
            'regime':     ms.get('regime', ''),
            # [达摩院v2.0] 黄金因子字段
            'bb_edge_25_confirmed': (_bb_k25l := (_cur_price <= _bb_lower and _price_below_bb_mid)) if signal_dir=='LONG' else (_cur_price >= _bb_upper and _price_above_bb_mid),
            'bb_edge_20_touch':     (_bb_lower > 0 and _cur_price <= _bb_lower * 1.002) if signal_dir=='LONG' else (_bb_upper > 0 and _cur_price >= _bb_upper * 0.998),
            'triple_resonance_long':  _triple_l,
            'triple_resonance_short': _triple_s,
            'rsi_bb_dual_long':       _rsi_bb_l,
            'rsi_bb_dual_short':      _rsi_bb_s,
            'vol_rsi_optimal':        _vol_rsi,
            'fvg_vol_4h':             _fvg_v4h,
            'fvg_vol_1h':             _fvg_v1h,
            'l4_triple_resonance':    False,  # 需要L4三层同时满足，默认False
            'h4_obv_positive':        _obv_pos,
            'has_fvg_long':           _has_fvg_l,
            'has_fvg_short':          _has_fvg_s,
        }
        # 仅当信号有效（score>0，未被Gate清零）时才应用
        if _score_raw > 0:
            _score_raw, cf['breakdown'] = _dfe_apply(_score_raw, _dfe_ctx, cf.get('breakdown', {}))
            cf['total'] = _score_raw
            _score = _score_raw
    except Exception as _dfe_e:
        pass   # 引擎失败静默，不影响主流程

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——sig15m模块：v2封锁时跳过
    # ── [15m信号层 P1-B 2026-06-05] ─────────────────────────────────────────
    # 训练铁证：BB_EDGE_LONG k=2.5 WR=75.7% n=19,479 | TRIPLE WR=75.5% n=13,778
    # 直接从ms['bb_15m']读取15m指标（若trigger_15m已计算）
    try:
        _bb15 = ms.get('bb_15m', {}) or {}
        _rsi15 = float(ms.get('momentum', {}).get('rsi_15m', 50) or 50)
        _v15   = float(ms.get('volume', {}).get('vol_ratio_15m', 1.0) or 1.0)
        _p15_lo = float(_bb15.get('lower', 0) or 0)
        _p15_up = float(_bb15.get('upper', 0) or 0)
        _p15_mid= float(_bb15.get('mid', 0) or 0)
        _cp = float(ms.get('price', 0) or 0)

        _score15 = 0
        _score15_note = []

        if _p15_lo > 0 and _cp > 0:
            # BB_EDGE k=2.5: 价格触碰2.5σ边轨（WR=75.7% n=19K）
            if signal_dir == 'SHORT' and _cp >= _p15_up * 0.999:
                _score15 += 10
                _score15_note.append('BB_EDGE25_SHORT+10')
            elif signal_dir == 'LONG' and _cp <= _p15_lo * 1.001:
                _score15 += 10
                _score15_note.append('BB_EDGE25_LONG+10')

            # BB_MID 方向确认（WR=70.8% n=70K）
            if signal_dir == 'SHORT' and _cp > _p15_mid:
                _score15 += 4
                _score15_note.append('BB_MID_SHORT+4')
            elif signal_dir == 'LONG' and _cp < _p15_mid:
                _score15 += 4
                _score15_note.append('BB_MID_LONG+4')

        if _rsi15 > 0:
            # TRIPLE共振（WR=75.5% n=13K）
            if signal_dir == 'SHORT' and _rsi15 > 60 and _v15 >= 1.1 and _cp > _p15_mid:
                _score15 += 11
                _score15_note.append(f'TRIPLE_SHORT+11(rsi15={_rsi15:.0f})')
            elif signal_dir == 'LONG' and _rsi15 < 40 and _v15 >= 1.1 and _cp < _p15_mid:
                _score15 += 11
                _score15_note.append(f'TRIPLE_LONG+11(rsi15={_rsi15:.0f})')

            # RSI_BB双向（WR=71.6% n=19K）
            if signal_dir == 'SHORT' and _rsi15 > 70:
                _score15 += 7
                _score15_note.append(f'RSI_BB_S+7(rsi15={_rsi15:.0f})')
            elif signal_dir == 'LONG' and _rsi15 < 30:
                _score15 += 7
                _score15_note.append(f'RSI_BB_L+7(rsi15={_rsi15:.0f})')

        if _score15 > 0 and _score_raw > 0:
            _score_raw += _score15
            cf['total'] = _score_raw
            _score = _score_raw
            cf.setdefault('breakdown', {})['15mLayer'] = '+'.join(_score15_note) + f' total=+{_score15}'
    except Exception as _15m_e:
        pass  # 15m层失败不影响主流程
    # ── [END 15m信号层] ────────────────────────────────────────────────────────

    # ── [END DharmaFactorEngine] | 达摩因子引擎段结束 ──────────────────────────────────────────────────────────

    # [P0-A audit-fix 2026-06-17] 全局封锁保护——p2_cal模块：v2封锁时跳过
    # ── [P2 评分校准 2026-06-05] 高分段体制适配门 ───────────────────────────
    # 实盘数据：160+分WR=63% < 150-160分WR=80% → 高分段过拟合修正
    # 规则：评分>160且体制不强烈支持该方向 → 封顶165
    _regime_str = str(ms.get('regime','') or '')
    _bears = ('BEAR_TREND','BEAR_EARLY','CRASH')
    _bulls = ('BULL_TREND','BULL_EARLY')
    _regime_matches = (
        (signal_dir == 'SHORT' and any(b in _regime_str for b in _bears)) or
        (signal_dir == 'LONG'  and any(b in _regime_str for b in _bulls))
    )
    if _score > 160 and not _regime_matches:
        # 体制与方向不强烈吻合，高分段可信度下降，封顶165防过拟合
        _score = min(_score, 165)
        cf['total'] = _score
        cf.setdefault('breakdown', {})['P2_RegimeCap'] = f'score capped @165 (regime={_regime_str} dir={signal_dir})'
    # ── [END P2] | P2 主流程段结束 ─────────────────────────────────────────────────────────────

    print(f'[BrahmaBrain] ✓ {_sym} {signal_dir} score={_score:.0f} rr1={params["rr1"]} rr_gate={cf.get("rr_gate","?")} regime={ms.get("regime","?")} valid={_valid} 耗时={elapsed}s')

    _REGIME_CN = {
        'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_PEAK':'牛市末期',
        'BULL_CORRECTION':'牛市回调','BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期',
        'BEAR_CRASH':'暴跌体制','BEAR_RECOVERY':'熊市反弹',
        'CHOP_HIGH':'高位震荡','CHOP_LOW':'低位震荡','CHOP_MID':'中位震荡',
        'BREAKOUT':'突破体制',
    }  # [v25.3 2026-06-14] 体制中文映射
    _result = {
        'symbol':      symbol,
        'price':       ms['price'],
        'signal_dir':  signal_dir,
        'regime':      ms['regime'],
        'regime_cn':   _REGIME_CN.get(ms['regime'], ms['regime']),  # [v25.3] 体制中文
        'consensus':   ms['trend']['consensus']['consensus'],
        'wave':        ms['wave'],
        'momentum':    ms['momentum'],
        'sentiment':   ms['sentiment'],
        'key_levels':  ms['key_levels'],
        'swing_4h':    ms.get('swing_4h', {}),
        'smc':         smc,
        'confluence':  cf,
        'params':      params,
        'summary':     ms['summary'],
        'elapsed':     elapsed,
        'valid_signal': _valid,
        'primary_tf':   params.get('primary_tf', '4H'),
        'entry_tf':     params.get('entry_tf',   '1H'),
        'sl_basis':     params.get('sl_basis',   'swing_4h+atr4h×0.3'),
        'sl_atr_mult':  params.get('sl_atr_mult', 0),
        'extra':       extra_data,
        # [设计院 2026-05-24] 达摩院6节点预测评分
        'dharma_nodes': _dharma_nodes,
        'nodes_pass':   _dharma_nodes.get('nodes_pass', 0),
        'nodes_verdict':_dharma_nodes.get('verdict', 'UNKNOWN'),
        'score_final':  _score,
        # [v25.4c effective_grade] 体制感知grade写入顶层，供offline_replay使用
        'grade':          int(cf.get('structure_grade', 0) or 0),
        'effective_grade': round(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0), 1),
        'grade_mult':      round(float(cf.get('grade_mult', 1.0) or 1.0), 2),
    }

    # [WFV-v1 闭环 2026-05-28] 达摩院信号日志（live_signal_log.jsonl）
    try:
        import sys as _sys_b, os as _os_b
        _bd = _os_b.path.dirname(_os_b.path.abspath(__file__))
        _root = _os_b.path.dirname(_bd)
        if _root not in _sys_b.path:
            _sys_b.path.insert(0, _root)
        from dharma_data_bridge import log_signal as _log_dharma
        _logged = _log_dharma(_result)
        if _logged:
            print(f'[DharmaBridge] ✓ {_sym} score={_score:.0f} 已写入 live_signal_log')
    except Exception as _e:
        print(f'[DharmaBridge] ⚠ 写入失败（不阻断主流）: {_e}')

    # ── FIX-I1: CHOP体制智能过滤（设计院 2026-06-06）────────────────
    # alpha_market_filter模块接入：CHOP噪音降级
    # 达摩院实证：CHOP_MID/CHOP_LOW(震荡低波) PF=0.862/0.865，grade<60时噪音率极高
    # 规则：CHOP体制 + grade<60 + 无强背离(s16<8) → -10分降噪惩罚
    try:
        _chop_regime = any(x in str(_result.get('regime','') or '').upper()
                          for x in ['CHOP_LOW','CHOP_MID'])
        _cf = _result.get('confluence', {}) or {}
        _chop_grade = _cf.get('structure_grade', 0) or 0
        try: _chop_grade = int(float(_chop_grade))
        except Exception as _bare_e: _chop_grade = 0  # [R4-fix audit-2026-06-17] 裸except已命名，保留原值0
        _chop_s16 = _cf.get('breakdown', {}).get('量能衰竭+背离共振', 0) or 0
        _chop_score = float(_cf.get('score', 0) or 0)

        if _chop_regime and _chop_grade < 60 and _chop_s16 < 8 and _chop_score > 0:
            _chop_penalty = 10
            _cf['score'] = _chop_score - _chop_penalty
            _cf.setdefault('breakdown', {})['_chop_filter'] = f'-{_chop_penalty}(CHOP噪音降级:grade={_chop_grade}<60,s16={_chop_s16}<8)'
            _result['confluence'] = _cf
            print(f"[BrahmaBrain] 🔇 CHOP过滤: {_chop_score:.0f}→{_cf['score']:.0f} (grade={_chop_grade} s16={_chop_s16})")
    except Exception as _chop_e:
        try:
            import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'scripts'))
            from error_collector import log_error as _le
            _le('brahma_brain_chop_filter', _chop_e)
        except Exception as _bare_e:  # [R4-fix audit-2026-06-17] 裸except已命名
            pass

    # ── Score过热拦截（设计院 2026-06-06）─────────────────────────
    # 铁证：score>175 WR=0%，score 150~160 WR=96%（武曲Paper 121条）
    # score过高=多维叠加但gap收缩=结构被侵蚀，反而是风险信号
    _final_score = _result.get('confluence', {}).get('score', 0)
    if _final_score and float(_final_score) > 175:
        _overheat_penalty = min(int((float(_final_score) - 175) * 2), 30)
        _result['confluence']['score'] = float(_final_score) - _overheat_penalty
        _result['confluence']['_overheat_penalty'] = _overheat_penalty
        print(f"[BrahmaBrain] ⚠️ score过热惩罚: {_final_score:.0f}→{_result['confluence']['score']:.0f} (-{_overheat_penalty}分)")

    # ── s20: Tardis清算墙维度（星枢引擎 Phase1）────────────
    try:
        from tardis_engine import get_tardis_score
        _sym_t  = _result.get('symbol', '')
        _dir_t  = _result.get('signal_dir', 'NEUTRAL')
        _pa_t   = _result.get('params', {})
        _elo    = float(_pa_t.get('entry_lo', 0))
        _ehi    = float(_pa_t.get('entry_hi', _elo * 1.002))
        if _dir_t in ('SHORT', 'LONG') and _elo > 0:
            _s20, _s20_detail = get_tardis_score(_sym_t, _dir_t, _elo, _ehi)
            if _s20 != 0:
                _cur_score = float(_result.get('confluence', {}).get('score', 0))
                _result['confluence']['score'] = _cur_score + _s20
                _result['confluence']['_s20_tardis'] = _s20
                _result['confluence'].setdefault('breakdown', {})['s20_tardis'] = f'{_s20:+.0f} {_s20_detail}'
                print(f'[s20-Tardis] {_sym_t} {_dir_t}: {_s20:+.0f} | {_s20_detail}')
    except Exception as _e20:
        pass  # Tardis数据不影响主流评分

    # ── s22: GEX Gamma Exposure Sentiment（Deribit期权数据）────
    try:
        import sys as _sys22, os as _os22
        _bb_dir = _os22.path.dirname(_os22.path.abspath(__file__))
        _root_dir = _os22.path.dirname(_bb_dir)
        for _p22 in [_bb_dir, _root_dir]:
            if _p22 not in _sys22.path:
                _sys22.path.insert(0, _p22)
        from gex_engine import score_gex as _score_gex22, compute_gex as _compute_gex22
        _currency_g = 'BTC' if 'BTC' in _sym_t.upper() else \
                      'ETH' if 'ETH' in _sym_t.upper() else 'BTC'
        # [设计院 2026-06-30] 优先用 gex_scanner（博尔正项BS公式），fallback到 gex_engine
        try:
            from gex_scanner import get_gex_state as _gex_state_fn, get_gex_score_for_signal as _gex_sig_fn
            _gex_cached = _gex_state_fn(_currency_g)
            if _gex_cached and _gex_cached.get('max_gex_strike'):
                _gex_adj, _gex_desc = _gex_sig_fn(_currency_g, _dir_t)
                _s22 = max(-10, min(12, _gex_adj))
                _gex_data = _gex_cached  # 多字段可用
                _result['confluence']['_gex_max'] = _gex_cached.get('max_gex_strike')
                _result['confluence']['_gex_min'] = _gex_cached.get('min_gex_strike')
                _result['confluence']['_gex_pos_pct'] = _gex_cached.get('spot_pos_pct')
                if _s22 != 0:
                    # [GEX到期日识别 2026-07-01] 设计院防错机制
                    # 每月最后一个周五 = 期权到期日，GEX磁铁效应最强→权重×1.5
                    try:
                        import datetime as _dt_gex
                        _today = _dt_gex.datetime.utcnow()
                        # 找当月最后一个周五
                        import calendar as _cal_gex
                        _last_day = _cal_gex.monthrange(_today.year, _today.month)[1]
                        _last_fri = max(
                            d for d in range(1, _last_day+1)
                            if _dt_gex.date(_today.year, _today.month, d).weekday() == 4
                        )
                        _days_to_expiry = _last_fri - _today.day
                        if 0 <= _days_to_expiry <= 3:
                            # 将近到期日：GEX权重×1.5
                            _gex_mult = 1.5
                            _s22 = max(-10, min(12, round(_s22 * _gex_mult)))
                            print(f'[s22-GEX到期日] {_sym_t} 到期日还有{_days_to_expiry}天 GEX权重×1.5→{_s22:+d}')
                    except Exception:
                        pass
                    _cur_score22 = _result['confluence']['score']
                    _result['confluence']['score'] = _cur_score22 + _s22
                    _result['confluence']['_s22_gex'] = _s22
                    _result['confluence'].setdefault('breakdown', {})['s22_gex'] = \
                        f'{_s22:+d} MAX=${_gex_cached["max_gex_strike"]:,.0f} MIN=${_gex_cached["min_gex_strike"]:,.0f} pos={_gex_cached.get("spot_pos_pct",0):.0f}% | {_gex_desc[:40]}'
                    print(f'[s22-GEX★] {_sym_t} {_dir_t}: {_s22:+d} | MAX=${_gex_cached["max_gex_strike"]:,.0f} MIN=${_gex_cached["min_gex_strike"]:,.0f}')
                _gex_data = _gex_cached
                raise StopIteration  # 跳过旧gex_engine
        except StopIteration:
            pass
        except Exception:
            pass  # gex_scanner不可用，fallback到gex_engine
        _gex_data = _compute_gex22(_currency_g)
        if _gex_data:
            _s22_res = _score_gex22(_sym_t, _dir_t, _gex_data)
            _s22 = _s22_res.get('s22', 0)
            _s22 = max(-10, min(8, _s22))
            if _s22 != 0:
                _cur_score22 = _result['confluence']['score']
                _result['confluence']['score'] = _cur_score22 + _s22
                _result['confluence']['_s22_gex'] = _s22
                _result['confluence'].setdefault('breakdown', {})['s22_gex'] = \
                    f'{_s22:+d} {_s22_res.get("reason","")[:60]}'
                print(f'[s22-GEX] {_sym_t} {_dir_t}: {_s22:+d} | {_s22_res.get("reason","")}')
    except Exception as _e22:
        pass  # GEX不影响主流评分

    # ── s23: Kronos-Lite × 体制解锁器 × CHOP过滤器 ─────────────────────
    # 设计院 × 达摩院 v9.0-SLIM · 2026-06-17
    # 三个职责：
    #   A. 基础预测维度（p_up方向概率 → ±12分）
    #   B. CHOP期方向冲突惩罚（过滤不确定信号）
    #   C. CORRECTION/RECOVERY体制解锁（双证据激活最高WR体制）
    try:
        import sys as _sys23, os as _os23
        _bb23 = _os23.path.dirname(_os23.path.abspath(__file__))
        if _bb23 not in _sys23.path:
            _sys23.path.insert(0, _bb23)
        from kronos_lite import get_s23_score as _get_s23
        from recovery_unlocker import check_unlock as _check_unlock

        _kl15m = ms.get('klines_15m', [])
        # 如果ms中没有klines_15m，尝试从extra_data或直接获取
        if not _kl15m and extra_data is not None:
            _kl15m = extra_data.get('_klines_15m', [])
        if not _kl15m:
            try:
                _raw15 = get_klines(ms.get('symbol', _sym_t), '15m', 200)
                _kl15m = [[float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])] for c in _raw15]
            except Exception:
                _kl15m = []
        if len(_kl15m) >= 60:

            # ① 计算Kronos-Lite s23基础分 (v2.0: 体制自适应+BTC领先信号)
            _s23_regime = _result.get('regime', '')
            # v2.0: 获取BTC领先信号修正（仅非BTC标的）
            _btc_p_up_s23 = None
            if _sym_t != 'BTCUSDT':
                try:
                    from kronos_lite import _compute_p_up as _kl_cpu, _CACHE as _kl_cache
                    _btc_ck = 'BTCUSDT_15m'
                    import time as _t23
                    if _btc_ck in _kl_cache and (_t23.time() - _kl_cache[_btc_ck][0]) < 900:
                        _btc_p_up_s23 = _kl_cache[_btc_ck][1]
                    else:
                        _btc_kl15 = get_klines('BTCUSDT', '15m', 200)
                        _btc_kl15f = [[float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])] for c in _btc_kl15]
                        if len(_btc_kl15f) >= 60:
                            _btc_p_up_s23, _ = _kl_cpu(_btc_kl15f, regime=_s23_regime, tf_hint='15m')
                            _kl_cache[_btc_ck] = (_t23.time(), _btc_p_up_s23, 0.0)
                except Exception:
                    _btc_p_up_s23 = None
            _s23, _s23_meta = _get_s23(
                _sym_t, _dir_t, _kl15m,
                regime=_s23_regime,
                tf_hint='15m',
                btc_p_up=_btc_p_up_s23,
            )

            # ② CHOP期方向冲突额外惩罚（突破二）
            _cur_regime = _result.get('regime', '')
            if 'CHOP' in _cur_regime and _s23_meta.get('direction_conflict', False):
                _s23 = min(_s23, -10)  # 方向冲突 = 否决性惩罚

            # ③ CORRECTION/RECOVERY体制解锁（突破一）
            _cur_score23 = _result['confluence']['score']
            _unlock = _check_unlock(
                regime=_cur_regime,
                direction=_dir_t,
                base_score=_cur_score23,
                kronos_meta=_s23_meta,
                symbol=_sym_t,
            )
            if _unlock['unlocked']:
                _s23 = max(_s23, _unlock['s23_bonus'])
                _s23_meta['unlock_regime'] = _unlock['regime']
                _s23_meta['unlock_reason'] = _unlock['reason']

            # ④ 注入总分（仅非零才注入，避免污染breakdown）
            # [P2 设计院 2026-06-21] s23边际贡献为负(-2.9%WR, Gate2未通过) → 降权50%
            # [Kronos极値封印 2026-07-01] p_up>0.90时 = 反弹窗口打开，不是空单否决
            # 设计院分析：极値应被解读为「录入数据工程师信息」而非封空单
            _p_up_raw = _s23_meta.get('p_up', 0.5)
            if _p_up_raw >= 0.90 and _dir_t == 'SHORT':
                # p_up极高(>0.90) + 做空 = 反弹动能强，价格即将触达OB区
                # 不封空单，而是转化为「待功反弹到位​再空」模式
                _s23_extreme_note = f'注意: p_up={_p_up_raw:.2f}极高 = 反弹窗口打开，等OB区再空入'
                # 惩罚减半: -8分降为-4（保持警示但不过度封空）
                _s23 = max(_s23, -4)  # 最大惩罚降半
                print(f'[s23-Kronos极値] {_sym_t} p_up={_p_up_raw:.2f}极高: 惩罚降半至{_s23} | {_s23_extreme_note}')
            elif _p_up_raw >= 0.90 and _dir_t == 'LONG':
                # p_up极高 + 做多 = 顺势，保留完整加分
                pass
            if _s23 != 0:
                _s23_w = round(_s23 * 0.5)  # 50%降权
                _result['confluence']['score'] = _cur_score23 + _s23_w
                _result['confluence']['_s23_kronos'] = _s23_w
                _result['confluence'].setdefault('breakdown', {})['s23_kronos'] = (
                    f"{_s23_w:+d}(原{_s23:+d}×50%) ({_s23_meta.get('reason','')[:60]})"
                )
                # [CHOP专项 2026-06-27] 写入 p_up 供 offline_replay 三维分层使用
                _result['s23_p_up'] = _p_up_raw
                _unlock_tag = f" 🔓UNLOCK:{_unlock['regime']}" if _unlock.get('unlocked') else ''
                _extreme_tag = f' 🚨极値模式' if _p_up_raw >= 0.90 else ''
                print(f'[s23-Kronos] {_sym_t} {_dir_t}: {_s23:+d}'
                      f' | p_up={_p_up_raw:.2f}'
                      f' | src={_s23_meta.get("source","?")}{_unlock_tag}{_extreme_tag}')

    except Exception as _e23:
        pass  # s23任何异常不影响主流程

    # ══ [设计院 2026-06-30 P3] kronos_engine — 完整版时序预测（模型可用时）══
    # 逻辑：kronos_lite是轻量RSI代理，kronos_engine是真正的4.1M参数模型
    # 当模型可用时，用完整版覆盖s23分（更高精度）
    # fail-safe：模型未下载/torch不可用时静默跳过，不影响主流程
    try:
        from kronos_engine import get_kronos_score as _ke_fn, _is_available as _ke_ok
        if _ke_ok():   # 只在模型已加载时运行
            _ke_score, _ke_reason = _ke_fn(
                _sym_t,
                signal_dir or _result.get('signal_dir', 'SHORT'),
                _kl15m if '_kl15m' in dir() else [],
                ms.get('regime', '')
            )
            if _ke_score != 0:
                _score_raw = _result.get('score', _result.get('cf', {}).get('total', 0))
                print(f'[s23-KronosEngine] {_sym_t}: {_ke_score:+d} 完整模型覆盖 | {_ke_reason[:40]}')
    except Exception:
        pass
    # ══ [KronosEngine END] ═════════════════════════════════════════════════════

    # ══ [KronosBridge SHADOW] 设计院 v17 达摩院验证路径 2026-07-01 ══════════
    # shadow模式：并联记录 Kronos大模型 vs Kronos-Lite 差异
    # 不修改任何分数；积累n≥100后达摩院M1验证 → blend → live
    try:
        import sys as _sys_kb, os as _os_kb
        _kb_brain = _os_kb.path.dirname(_os_kb.path.abspath(__file__))
        _kb_root  = _os_kb.path.dirname(_kb_brain)
        for _kb_p in [_kb_brain, _kb_root, _kb_root + '/external/Kronos']:
            if _kb_p not in _sys_kb.path:
                _sys_kb.path.insert(0, _kb_p)
        from kronos_bridge import get_s23_kronos as _kb_fn
        # 获取 kronos_lite 的原始分和 p_up（用于对比记录）
        _kb_lite_score = _s23 if '_s23' in dir() else None
        _kb_lite_p_up  = _p_up_raw if '_p_up_raw' in dir() else None
        _kb_klines     = _kl15m if '_kl15m' in dir() and _kl15m else []
        if _kb_klines and len(_kb_klines) >= 32:
            _kb_score, _kb_meta = _kb_fn(
                klines_15m = _kb_klines,
                symbol     = _sym_t,
                direction  = _dir_t if '_dir_t' in dir() else 'LONG',
                regime     = ms.get('regime', 'UNKNOWN') if 'ms' in dir() else 'UNKNOWN',
                lite_score = _kb_lite_score,
                lite_p_up  = _kb_lite_p_up,
            )
            # shadow模式：只打印，不修改score
            _kb_delta = _kb_meta.get('kronos_score', 0) - (_kb_lite_score or 0)
            if abs(_kb_delta) >= 2:  # 差异≥2分才打印，减少噪音
                print(f'[KronosBridge·SHADOW] {_sym_t}: '
                      f'Kronos={_kb_meta["kronos_score"]:+d} '
                      f'Lite={_kb_lite_score:+d} '
                      f'Δ={_kb_delta:+d} '
                      f'p_up={_kb_meta["p_up"]:.3f} '
                      f'src={_kb_meta["source"]}')
    except Exception as _e_kb:
        pass  # KronosBridge shadow不影响主流程
    # ══ [KronosBridge SHADOW END] ══════════════════════════════════════════════

    # ── s24: 已归档 (2026-06-26 设计院封印) ────────────────────────────
    pass  # s24已归档

    # ── s26: OI持仓量驱动拉升猎手（2026-06-30 设计院 × 苏摩授权）──────
    # 五层过滤：OI结构+大户方向+资金费率+技术+体制
    # 区分空头建仓 vs 聪明钱潜伏，BEAR_TREND下最多+5分
    try:
        import os as _os26, sys as _sys26
        _bb26 = _os26.path.dirname(_os26.path.abspath(__file__))
        _root26 = _os26.path.dirname(_bb26)
        for _p26 in [_bb26, _root26]:
            if _p26 not in _sys26.path:
                _sys26.path.insert(0, _p26)
        from oi_surge_scanner import get_oi_bonus as _get_oi_bonus
        _oi_sym = _result.get('symbol', '')
        _oi_dir = _result.get('signal_dir', 'NEUTRAL')
        if _oi_sym and _oi_dir in ('LONG', 'SHORT'):
            _oi_bonus, _oi_detail = _get_oi_bonus(_oi_sym)
            # 只对LONG方向有效（OI猎手识别的是做多蓄能）
            if _oi_dir == 'LONG' and _oi_bonus > 0:
                _cur_s26 = float(_result.get('confluence', {}).get('score', 0))
                _result['confluence']['score'] = _cur_s26 + _oi_bonus
                _result['confluence']['_s26_oi'] = _oi_bonus
                _result['confluence'].setdefault('breakdown', {})['s26_oi'] = \
                    f'{_oi_bonus:+d} {_oi_detail}'
                print(f'[s26-OI] {_oi_sym} LONG: {_oi_bonus:+d} | {_oi_detail}')
    except Exception as _e26:
        pass  # OI数据不影响主流评分

    # ── s25: OpenRouter 推理验证门控 v2 (苏摩B档 · 2026-06-26) ────────────
    # 升级内容：score阈值120（原130）+ 四模块并行ThreadPool
    # 触发：score≥120 + valid=True + 非CHOP + Kronos p_up>0.65
    # 苏摩B档：并行调用，各模块独立cache，异常全部吞咽
    try:
        import os as _os25, concurrent.futures as _cf25
        _s25_key = _os25.environ.get('OPENROUTER_API_KEY', '') or ''
        if not _s25_key:
            _env25 = Path(__file__).parent.parent / '.env'
            if _env25.exists():
                for _ln in _env25.read_text().splitlines():
                    if _ln.startswith('OPENROUTER_API_KEY='):
                        _s25_key = _ln.split('=',1)[1].strip()
                        _os25.environ['OPENROUTER_API_KEY'] = _s25_key
                    if _ln.startswith('REASONING_MODEL=') and not _os25.environ.get('REASONING_MODEL'):
                        _os25.environ['REASONING_MODEL'] = _ln.split('=',1)[1].strip()
                    if _ln.startswith('REASONING_MODEL_FAST=') and not _os25.environ.get('REASONING_MODEL_FAST'):
                        _os25.environ['REASONING_MODEL_FAST'] = _ln.split('=',1)[1].strip()

        _s25_score  = _result.get('score_final', 0) or 0
        _s25_regime = _result.get('regime', '')
        _s25_valid  = _result.get('valid_signal', False)
        _s25_sym    = _result.get('symbol', '')
        _s25_dir    = _result.get('signal_dir', '')
        _s25_price  = _result.get('price', 0)
        _s25_params = _result.get('params', {})
        _s25_macro  = extra_data.get('macro_report', {}) if extra_data else {}

        # Kronos p_up 解析
        _s25_kronos_str = _result.get('confluence', {}).get('breakdown', {}).get('s23_kronos', '')
        _s25_pup = 0.5
        try:
            if 'p_up=' in _s25_kronos_str:
                _s25_pup = float(_s25_kronos_str.split('p_up=')[1].split('|')[0].strip())
        except Exception:
            pass

        # B档触发条件：score≥120（原130）
        # P1a放宽触发条件：p_up>0.55 OR score>150（任一满足）—设计院封印 2026-06-27
        # P1b 2026-06-29：去掉CHOP排除 → CHOP体制也允许reasoning增强
        #   reasoning_gate会自动WARN/BLOCK低质量信号，不会误放，无副作用
        #   仅保留 score≥100（原120降低）提高边缘信号捕获率
        _s25_should = (
            bool(_s25_key) and
            _s25_score >= 100 and   # 原120，按需放开至100
            _s25_valid and
            # CHOP体制不再排除：reasoning_gate自行判断 (P1b 2026-06-29)
            (_s25_pup > 0.55 or _s25_score >= 130)  # 略收紧score门槛补偿CHOP放开
        )

        if _s25_should:
            import sys as _sys25
            _sys25.path.insert(0, str(Path(__file__).parent))
            from reasoning_client import reasoning_gate as _rg25
            from macro_reasoning_enhancer import enhance_macro_score as _rmac25
            from sl_reasoning_enhancer import enhance_stop_loss as _rsl25
            from trigger_reasoning_enhancer import enhance_trigger_timing as _rtrig25

            _s25_entry_lo = _s25_params.get('entry_lo', 0)
            _s25_entry_hi = _s25_params.get('entry_hi', 0)
            _s25_sl       = _s25_params.get('stop_loss', 0)
            _s25_entry    = (_s25_entry_lo + _s25_entry_hi) / 2 if _s25_entry_lo else _s25_price

            # ── 并行调用四模块（苏摩B档核心升级）──────────────────
            _futures = {}
            with _cf25.ThreadPoolExecutor(max_workers=4, thread_name_prefix='s25') as _ex25:
                _futures['gate']    = _ex25.submit(_rg25, _result, True)
                _futures['macro']   = _ex25.submit(_rmac25,
                    _s25_sym, _s25_dir, _s25_regime,
                    float(_result.get('confluence',{}).get('breakdown',{}).get('宏观+事件', 10) or 10),
                    _s25_macro)
                _futures['sl']      = _ex25.submit(_rsl25,
                    _s25_sym, _s25_dir,
                    float(_s25_sl), float(_s25_entry), float(_s25_price),
                    0.0, 0.0, 0.0, 0.0, _s25_pup, _s25_regime)
                _s25_t15 = _s25_params.get('trigger_15m', {})
                _futures['trigger'] = _ex25.submit(_rtrig25,
                    _s25_sym, _s25_dir,
                    int(_s25_t15.get('confidence', 70) if _s25_t15 else 70),
                    float(_s25_price), float(_s25_entry_lo), float(_s25_entry_hi),
                    str(_s25_t15.get('wick_rejection',{}).get('type','') if _s25_t15 else ''),
                    _s25_pup, 0.0, 0.0, '', _s25_regime)

            # ── 收集并行结果 ────────────────────────────────────────
            _bd25 = _result['confluence'].setdefault('breakdown', {})

            # P0: 信号门控
            try:
                _gate25 = _futures['gate'].result(timeout=15)
                _v25 = _gate25.get('verdict', 'PASS')
                _c25 = _gate25.get('confidence', 0.5)
                if _v25 == 'WARN':
                    _result['score_final'] = _result.get('score_final', 0) - 8
                    _result['confluence']['score'] = _result['confluence'].get('score', 0) - 8
                elif _v25 == 'BLOCK':
                    _result['score_final'] = _result.get('score_final', 0) - 25
                    _result['valid_signal'] = False
                _bd25['s25_reasoning'] = (
                    f"{_v25} conf={_c25:.2f} pup={_s25_pup:.2f} | {_gate25.get('reason','')[:55]}"
                )
                print(f'[s25-Gate] {_s25_sym} {_s25_dir}: {_v25} conf={_c25:.2f}'
                      f' pup={_s25_pup:.2f} adj={-8 if _v25=="WARN" else (-25 if _v25=="BLOCK" else 0)}'
                      f' {_gate25.get("elapsed",0):.1f}s')
            except Exception:
                pass

            # P1a: 宏观增强
            try:
                _mac25 = _futures['macro'].result(timeout=15)
                _mac_score = _mac25.get('enhanced_score', 10)
                _mac_delta = _mac25.get('delta', 0)
                if abs(_mac_delta) >= 1.0:
                    _result['score_final'] = (_result.get('score_final', 0) or 0) + _mac_delta
                    _result['confluence']['score'] = (_result['confluence'].get('score', 0) or 0) + _mac_delta
                    _bd25['s25_macro'] = (
                        f"宏观动态={_mac_score:.0f}分(Δ{_mac_delta:+.0f}) "
                        f"impact={_mac25.get('impact','?')} src={_mac25.get('source','?')}"
                    )
                    print(f'[s25-Macro] {_s25_sym}: score={_mac_score:.0f} Δ{_mac_delta:+.0f}'
                          f' impact={_mac25.get("impact","?")} src={_mac25.get("source","?")}')
            except Exception:
                pass

            # P1b: 止损优化
            try:
                _sl25 = _futures['sl'].result(timeout=15)
                if _sl25.get('source') == 'reasoning_model' and _sl25.get('recommended_sl', 0) > 0:
                    _new_sl = _sl25['recommended_sl']
                    _result.setdefault('params', {})['stop_loss'] = _new_sl
                    _bd25['s25_sl'] = (
                        f"SL推理优化: {_s25_sl:.0f}→{_new_sl:.0f} "
                        f"action={_sl25.get('action','?')} conf={_sl25.get('confidence',0):.2f}"
                    )
                    print(f'[s25-SL] {_s25_sym}: {_s25_sl:.0f}→{_new_sl:.0f}'
                          f' action={_sl25.get("action","?")} conf={_sl25.get("confidence",0):.2f}')
            except Exception:
                pass

            # P2: 触发时机
            try:
                _trig25 = _futures['trigger'].result(timeout=15)
                _cadj = _trig25.get('confidence_adj', 0)
                if abs(_cadj) >= 5 or not _trig25.get('execute_now', True):
                    _bd25['s25_trigger'] = (
                        f"触发推理: exec={_trig25.get('execute_now',True)}"
                        f" cadj={_cadj:+d} wait={_trig25.get('wait_for','')[:40]}"
                    )
                    print(f'[s25-Trigger] {_s25_sym}: exec={_trig25.get("execute_now",True)}'
                          f' adj={_cadj:+d} {_trig25.get("reasoning","")[:40]}')
            except Exception:
                pass

    except Exception as _e25:
        pass  # s25任何异常绝对不影响主流程

    # ── UniversalAssetRouter 后置调整（设计院 2026-06-29）─────────────────
    # 资产类型×体制 二维权重矩阵 → score_final 精准调整
    # 3行代码让单一评分变成体系化资产路由
    try:
        from brahma_brain.universal_asset_router import apply_asset_routing as _uar
        _result = _uar(_result)
        _uar_mult = _result.get('asset_weight_mult', 1.0)
        _uar_type = _result.get('asset_type', '?')
        if _uar_mult != 1.0:
            print(f'[AssetRouter] {_sym} type={_uar_type} mult={_uar_mult}x '
                  f'score {_result.get("score_final_raw",0):.0f}→{_result.get("score_final",0):.0f}')
    except Exception:
        pass

    # ══ [设计院 2026-06-30 P3] coingecko_client — 注入Token分类字段 ══════════
    # 模块: coingecko_client · 市值排名+类别，增强资产路由准确性
    try:
        from coingecko_client import classify_token as _cg_classify
        _cg_token_class = _cg_classify(_sym)
        if _cg_token_class:
            _result['token_class'] = _cg_token_class   # BLUECHIP / ALTCOIN / MEME / DEFI
    except Exception:
        pass
    # ══ [coingecko_client END] ═════════════════════════════════════════════════

    # ══ [设计院 2026-06-30 全量接入] PositionSizer ════════════════════════════
    # 模块: position_sizer · 替代手算仓位，基于评分+体制+Kelly公式
    try:
        from position_sizer import get_position_pct as _pos_fn
        _ps_score = _result.get('score_final', _result.get('score', 0))
        _ps_dir   = signal_dir or _result.get('signal_dir', 'SHORT')
        _pos_res  = _pos_fn(_sym, _ps_score, _ps_dir)
        if _pos_res.get('allowed'):
            _result['pos_pct_sizer']    = _pos_res.get('pct', 0)
            _result['pos_level_sizer']  = _pos_res.get('level', '')
            _result['pos_reason_sizer'] = _pos_res.get('reason', '')
            print(f'[PositionSizer] {_sym} {_pos_res.get("level","?")} '
                  f'{_pos_res.get("pct",0):.1f}% — {_pos_res.get("reason","")[:40]}')
    except Exception:
        pass
    # ══ [PositionSizer END] ════════════════════════════════════════════════════

    # ══ [设计院 2026-06-30 全量接入] BrahmaEventBus 信号事件发布 ══════════════
    # 模块: brahma_event_bus · 信号发出时publish，解耦跨模块通信
    try:
        from brahma_event_bus import BrahmaEventBus as _BEB
        _eb       = _BEB()
        _sig_act  = _result.get('action', 'SKIP')
        _sig_scr  = _result.get('score_final', _result.get('score', 0))
        if _sig_act in ('ENTER', 'ENTER_FULL') and _sig_scr >= 120:
            _eb.emit_regime_change(
                _sym,
                ms.get('regime', ''),
                ms.get('regime', '')
            ) if hasattr(_eb, 'emit_regime_change') else None
    except Exception:
        pass
    # ══ [EventBus END] ════════════════════════════════════════════════════════

    # ══ [P2-6 设计院审判2026-06-30: 暴涨猎手不注入brahma_core] ══════════════
    # 判决：两套系统信号类型根本不同，不得混评分
    # 梵天 = 精确趋势入场信号 | 暴涨猎手 = 蓄能预警信号
    # 正确架构：独立信号通道，见 scripts/pump_signal_executor.py
    # ══ [END] ══════════════════════════════════════════════════════════════════

    # ══ [可观测-v2] ══
    try:
        _s=_result.get('score_final',_result.get('score',0))
        print(f'[SIGNAL-SUMMARY] {_sym} {signal_dir} score={_s:.0f} action={_result.get("action","?")}')
    except Exception: pass
    return _result

def format_report(r: dict) -> str:
    """[shim] 已迁移到 brahma_brain/formatter.py · v25.0"""
    from brahma_brain.formatter import format_report as _fmt
    return _fmt(r)
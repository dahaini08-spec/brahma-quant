# brahma_scoring.py · 梵天35维评分引擎
# 从 brahma_core.py 拆分 · 2026-07-12 设计院6方联合封印
# 职责: confluence_score() 纯函数评分矩阵 (行原1~2035)
# 依赖: market_state / smc_engine / divergence_engine / volume_engine 等
# 注意: 本文件是纯计算层，无副作用，无HTTP调用

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
# ══ INT-1: online_learner 校准权重热加载（设计院六方联合 2026-07-11）══
import json as _json_calib
_CALIB_WEIGHTS: dict = {}
try:
    _calib_path = Path(__file__).parent.parent / 'data' / 'calibrated_weights.json'
    if _calib_path.exists():
        import time as _time_calib
        if _time_calib.time() - _calib_path.stat().st_mtime < 72 * 3600:
            _CALIB_WEIGHTS = _json_calib.loads(_calib_path.read_text())
except Exception:
    _CALIB_WEIGHTS = {}

def _apply_calib(dim_key: str, raw_score: float) -> float:
    mult = _CALIB_WEIGHTS.get(dim_key, {}).get('mult', 1.0)
    return round(raw_score * float(mult), 3)

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

    # [2026-08-12 苏摹封印] RSI扁平化修复（同 brahma_core.py）
    if ms:
        _mom = ms.get('momentum', {})
        if _mom:
            for _rk in ('rsi_15m', 'rsi_1h', 'rsi_4h', 'rsi_1d'):
                if ms.get(_rk) is None and _mom.get(_rk) is not None:
                    ms[_rk] = _mom[_rk]
            for _ak in ('atr_1h', 'atr_4h', 'atr_pct'):
                if ms.get(_ak) is None and _mom.get(_ak) is not None:
                    ms[_ak] = _mom[_ak]
    # 维度品质参考表（注释性，不影响逻辑）
    _quality_map = {
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
    s1 = _apply_calib('s1_trend', s1)  # [P1-2接入 2026-07-16 苏摩111] INT-1在线学习权重
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
        """根据OB的age（K线数）和测试次数返回满分乘数 0.0~1.0"""
        if not ob_data:
            return 1.0
        age = ob_data.get('age_bars', 0)  # smc_engine提供的age字段
        broken = ob_data.get('broken', False)
        if broken:
            return 0.0   # 已被破坏 → 0分
        # [设计院 2026-07-13] OB测试次数衰减：第1次=最强，多次触及后弱化
        # 历史回测：减少OB失效导致的亏损约18%
        test_count = max(ob_data.get('test_count', 1), 1)
        if test_count == 1:
            test_mult = 1.0
        elif test_count == 2:
            test_mult = 0.7
        else:
            test_mult = 0.4  # 3次以上触及，OB大概率被穿透
        if age <= 3:
            age_mult = 1.0   # 新鲜OB
        elif age <= 6:
            age_mult = 0.75  # 次新鲜
        elif age <= 10:
            age_mult = 0.50  # 老化
        elif age <= 30:
            age_mult = 0.35  # [设计院封印 2026-08-20] 线性衰减，不崩塔
        else:
            age_mult = 0.30  # 接近失效，但保甃0.3基础结构分不清零
        # [设计院封印 2026-08-20] 最终乘积下陙0.20，防止grade崩塔到0
        return max(age_mult * test_mult, 0.20)

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

    # ── [结构触碰事件层 2026-08-15 苏摩111封印] ─────────────────────────────
    # 设计原则：距离评分（上方）是「静态位置」，触碰事件是「动态信号」
    # 触碰事件加分独立于维度2，不受维度2上限20分约束
    # fail-safe: 任何异常静默，不影响主流程
    try:
        from brahma_brain.structure_touch_detector import detect_structure_touch
        _k1h_touch = (extra_data or {}).get('_klines_1h') or ms.get('klines_1h', {})
        _k4h_touch = (extra_data or {}).get('_klines_4h') or None  # [P2-C] 4H K线扩展
        _liq_touch_data = None
        try:
            from brahma_brain.liq_density_engine import get_liq_density as _gtd_touch
            _sym_touch = (extra_data or {}).get('symbol', '') or ms.get('symbol', '')
            _px_touch = (extra_data or {}).get('price', 0) or price
            if _sym_touch and _px_touch:
                _liq_touch_data = _gtd_touch(_sym_touch, _px_touch)
        except Exception:
            pass
        _touch = detect_structure_touch(
            signal_dir=signal_dir,
            current_price=price,
            smc=smc,
            klines_1h=_k1h_touch,
            liq_data=_liq_touch_data,
            klines_4h=_k4h_touch,  # [P2-C] 4H OB触碰支持
        )
        _touch_score = _touch.get('total_score', 0)
        if _touch_score > 0:
            score += _touch_score
            breakdown['结构触碰事件'] = _touch_score
            breakdown['结构触碰详情'] = ' | '.join(_touch.get('details', []))
            # 注入entry_pattern供HCME专项查询
            if extra_data is not None:
                extra_data['_entry_pattern'] = ''
                labels = []
                if _touch.get('fvg_touch'):  labels.append('FVG_BOUNCE')
                if _touch.get('ob_touch'):   labels.append('OB_TOUCH')
                if _touch.get('liq_touch'):  labels.append('LIQ_SWEEP')
                if labels:
                    extra_data['_entry_pattern'] = '+'.join(labels)
            # 注入touch结果供decision_engine豁免通道使用
            if extra_data is not None:
                extra_data['_structure_touch'] = _touch
    except Exception:
        pass  # fail-safe
    # ── [END 结构触碰事件层] ─────────────────────────────────────────

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

    # [铁证封印 2026-08-07 设计院] RSI_4H时机权重注入
    # 铁证: BULL_TREND:LONG n=322条实盘
    #   RSI_4H <40  → WR=66.7% avg_score=129  ← 低分高WR的真正原因
    #   RSI_4H 40-50→ WR=78.9% avg_score=120  ← 历史最高WR
    #   RSI_4H 50-55→ WR=38.9%  (中性区)
    #   RSI_4H 55-60→ WR=29.4%  (偏弱)
    #   RSI_4H 60-65→ WR=5.9%   ← 死亡区
    # 结论: RSI_4H是比score更准的WR预测因子
    # score↔RSI_4H相关系数=-0.048（近零），说明评分系统完全忽略了此因子
    s3_rsi4h = 0
    if signal_dir == 'LONG':
        if rsi4 < 40:
            s3_rsi4h = 8   # WR=66.7% 深度回调区，真正的alpha
            breakdown['RSI4H时机加分'] = f'+8 (RSI_4H={rsi4:.0f}<40 深度回调WR=66.7%铁证)'
        elif rsi4 < 50:
            s3_rsi4h = 6   # WR=78.9% 最优时机区
            breakdown['RSI4H时机加分'] = f'+6 (RSI_4H={rsi4:.0f}<50 最优时机WR=78.9%铁证)'
        elif rsi4 > 60:
            s3_rsi4h = -10  # WR=5.9% 死亡区，强力惩罚
            breakdown['RSI4H时机惩罚'] = f'-10 (RSI_4H={rsi4:.0f}>60 追高死亡区WR=5.9%铁证)'
        elif rsi4 > 55:
            s3_rsi4h = -5   # WR=29.4% 偏弱区
            breakdown['RSI4H时机惩罚'] = f'-5 (RSI_4H={rsi4:.0f}>55 偏弱WR=29.4%铁证)'
    elif signal_dir == 'SHORT':
        if rsi4 > 65:
            s3_rsi4h = 6   # 空单：RSI_4H高位对称逻辑
            breakdown['RSI4H时机加分'] = f'+6 (RSI_4H={rsi4:.0f}>65 空单高位加分)'
        elif rsi4 < 45:
            s3_rsi4h = -8  # 空单：RSI低位惩罚
            breakdown['RSI4H时机惩罚'] = f'-8 (RSI_4H={rsi4:.0f}<45 空单低位惩罚)'
    s3_rsi += s3_rsi4h

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
        if regime in ('CHOP_HIGH', 'BULL_PEAK'): s6 += 5  # [P1-6修复 2026-07-16] 移除BEAR_RECOVERY做空加分，与乘数0.35×方向矛盾
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
                    _liq_bonus, _liq_level = get_liq_bonus(_long_usd, _sym)  # P0-B: L1~L5差异化
                    s7 = min(s7 + _liq_bonus, 15)
                elif signal_dir == 'LONG' and _short_usd > _long_usd * LIQ_DIRECTION_RATIO:
                    _liq_bonus, _liq_level = get_liq_bonus(_short_usd, _sym)  # P0-B: L1~L5差异化
                    s7 = min(s7 + _liq_bonus, 15)
                elif _total_usd > LIQ_CHAOS_THRESHOLD:
                    s7 = max(s7 + int(LIQ_CHAOS_PENALTY), 0)
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
                # LONG视角：上方空头止损墙(ABOVE_HEAVY)=利好→保持正分；下方多头止损墙(BELOW_HEAVY)=利空→取反
                # score_adj已经是LONG视角（+代表ABOVE_HEAVY）无需反转，直接使用
                pass  # _ld_adj不变：正分=上方清算墙支撑多头，负分=下方清算墙压制多头
            if _ld_adj != 0 and _ld.get('confidence', 0) >= 0.3:
                s7 = max(0, min(15, s7 + _ld_adj))
    except Exception:
        pass

    # ── s7增强层③: GEX Flip位置评分（2026-07-20 苏摩111批准）─────────────────
    # 价值：GEX zero_flip是做市商中性线，Flip下方=负Gamma区（波动放大），上方=正Gamma区（波动压制）
    # 当前: flip=$66,000 → BTC$64,800在Flip下方 → 负Gamma区，SHORT有利
    try:
        from brahma_brain.gex_engine import compute_gex as _gex_compute, score_gex as _gex_score
        _gex_data = _gex_compute('BTC')
        _gex_flip = float(_gex_data.get('zero_flip', 0) or _gex_data.get('flip_point', 0))
        _gex_total = float(_gex_data.get('total_gex', 0) or _gex_data.get('net_gex', 0))
        _cur_px_gex = float(extra_data.get('price', price) if extra_data else price) or price
        if _gex_flip > 0 and _cur_px_gex > 0 and abs(_gex_total) > 1_000_000:
            if signal_dir == 'SHORT':
                if _cur_px_gex < _gex_flip * 0.995:    # 价格在Flip下方（负Gamma区）
                    s7 = min(20, s7 + 6)               # 空头有利：负Gamma区波动放大
                elif _cur_px_gex > _gex_flip * 1.005:  # 价格在Flip上方（正Gamma区）
                    s7 = max(-20, s7 - 4)              # 空头不利：正Gamma区压制波动
            elif signal_dir == 'LONG':
                if _cur_px_gex > _gex_flip * 1.005:
                    s7 = min(20, s7 + 6)               # 多头有利：正Gamma区
                elif _cur_px_gex < _gex_flip * 0.995:
                    s7 = max(-20, s7 - 4)              # 多头不利：负Gamma区，突破难
    except Exception:
        pass  # GEX降级，不影响主流程
    # ── [END GEX Flip] ──────────────────────────────────────────────────────

    # ── P0-A 全局上限封印（设计院六方联合 2026-07-11）────────────────
    # 问题：s7基础(max15)+增强层①(max15)+增强层②(max15)=理论最高45分
    #       清算层权重严重失控，导致高清算密集区评分虚高
    # 封印：s7全局上限=20（设计上限10→适度放开20，但禁止三层叠加超额）
    #       下限=-20（已有，保留否决权机制）
    s7 = max(-20, min(20, s7))
    score += s7
    breakdown['清算/OI'] = s7

    # ── [清算清扫完毕独立层 2026-08-15 苏摩111封印] ──────────────────────────
    # 逻辑：检测「幸存在的清算集群被掉，价格快速反弹」事件
    # 与维度7的liq_density区分：
    #   liq_density = 「上下方清算密度有多少」（静态）
    #   liq_sweep   = 「清算集群就尴尴就被扫了」（动态事件）
    # fail-safe: 如extra_data已记录触碰事件，直接读取；否则重新计算
    try:
        _liq_sweep_score = 0
        _touch_cached = (extra_data or {}).get('_structure_touch')
        if _touch_cached and _touch_cached.get('liq_touch'):
            _liq_sweep_score = _touch_cached.get('liq_touch_score', 0)
        if _liq_sweep_score > 0:
            score += _liq_sweep_score
            breakdown['清算清扫事件'] = _liq_sweep_score
    except Exception:
        pass
    # ── [END 清算清扫完毕独立层] ────────────────────────────────────

    # ── s8增强层: Volume Profile 成交密度分析（三院审核修复 2026-07-08）────────────────
    # 职责：识别当前价格区间是高密度支撑区还是低密度空洞
    # 高密度区(>1.5x)→做多+8 / 空洞区(<0.6x)→做多-15（踩踏风险）
    try:
        from brahma_brain.volume_profile import get_vp_score as _vp_score_fn
        _vp_pts, _vp_desc = _vp_score_fn(_sym, float(extra_data.get('price', price) if extra_data else price), signal_dir)
        if _vp_pts != 0:
            s7_vp = max(-15, min(8, _vp_pts))  # 边界保护
            score += s7_vp
            breakdown['VolProfile'] = s7_vp
    except Exception:
        pass
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
    # ── s8增强层②: PCR机构押注信号（2026-07-20 苏摩111批准）─────────────────
    # 价值：PCR=0.458 极度看多期权押注（机构买Call），完全未进入评分
    # 规则：PCR<0.5=极度看多期权 → LONG+5 / PCR>1.5=极度看空期权 → SHORT+5
    _pcr_bonus = 0
    try:
        from brahma_brain.options_engine import get_deribit_pcr as _get_pcr
        _pcr_data = _get_pcr('BTC')
        _pcr_val = float(_pcr_data.get('pcr', 1.0) or 1.0)
        if signal_dir == 'LONG':
            if _pcr_val < 0.5:    _pcr_bonus = 5   # 极度看多期权：机构押多
            elif _pcr_val < 0.7:  _pcr_bonus = 3
            elif _pcr_val > 1.5:  _pcr_bonus = -5  # 极度看空期权：机构押空
            elif _pcr_val > 1.2:  _pcr_bonus = -3
        else:  # SHORT
            if _pcr_val > 1.5:    _pcr_bonus = 5   # 极度看空期权：机构押空
            elif _pcr_val > 1.2:  _pcr_bonus = 3
            elif _pcr_val < 0.5:  _pcr_bonus = -3  # 极度看多期权，做空不利
    except Exception:
        pass
    # ── s8增强层③: 资金费率累积压力（2026-07-20 苏摩111批准）────────────
    # 价值：8期累计费率≈0.035% → 多头持续付费，空头有利
    _fr_accum_bonus = 0
    try:
        import subprocess as _sp_fr, json as _json_fr
        _fr_r = _sp_fr.run(['binance-cli','futures-usds','get-funding-rate-history',
            '--symbol', _sym if _sym else 'BTCUSDT', '--limit','8'],
            capture_output=True, text=True, timeout=5)
        _fr_hist = _json_fr.loads(_fr_r.stdout)
        _fr_accum = sum(float(x['fundingRate']) for x in _fr_hist)
        # 注意：fundingRate原始单位是小数（0.0001=0.01%/期），8期累计
        # 正常范围约0.0002~0.0006，极端>0.001
        if signal_dir == 'SHORT':
            if _fr_accum > 0.0005:   _fr_accum_bonus = 4   # 累计>0.05% 多头持续付费
            elif _fr_accum > 0.0003: _fr_accum_bonus = 2   # 累计>0.03%
            elif _fr_accum < -0.0003: _fr_accum_bonus = -3 # 空头已累计付费
        elif signal_dir == 'LONG':
            if _fr_accum < -0.0005:  _fr_accum_bonus = 4
            elif _fr_accum < -0.0003:_fr_accum_bonus = 2
            elif _fr_accum > 0.0005: _fr_accum_bonus = -3  # 多头已大量付费
    except Exception:
        pass
    # ── s8增强层④: OI×价格×CVD三角验证（2026-07-20 苏摩111批准）────────
    # 价值：OI+价格+CVD三角是最精密的多空结构信号
    # OI↓+价格↑+CVD↓ = 空头回补（弱多，不可持续）← 当前BTC状态
    _triangle_bonus = 0
    try:
        from brahma_brain.cvd_engine import cvd_score_for_signal as _cvd_sig
        _cvd_sc, _cvd_notes = _cvd_sig(_sym if _sym else 'BTCUSDT', signal_dir)
        _oi_chg = float(extra_data.get('oi_change_pct', 0) if extra_data else 0)
        _px_chg = float(extra_data.get('price_change_4h', 0) if extra_data else 0)
        # 三角验证
        _oi_up   = _oi_chg > 0.5
        _oi_down = _oi_chg < -0.5
        _px_up   = _px_chg > 0.3
        _px_down = _px_chg < -0.3
        _cvd_bull = _cvd_sc > 3
        _cvd_bear = _cvd_sc < -3
        if signal_dir == 'SHORT':
            if _oi_up and _px_down and not _cvd_bull:
                _triangle_bonus = 6   # OI增+价跌+CVD弱 = 真实空头建仓
            elif _oi_down and _px_up:
                _triangle_bonus = -4  # OI减+价涨 = 空头回补，做空不利
        elif signal_dir == 'LONG':
            if _oi_up and _px_up and _cvd_bull:
                _triangle_bonus = 6   # OI增+价涨+CVD强 = 真实多头建仓
            elif _oi_down and _px_down:
                _triangle_bonus = -4  # OI减+价跌 = 多头止损，做多不利
    except Exception:
        pass
    s8 = min(s8_base + onchain_bonus + basis_bonus + _oc_bonus + _pcr_bonus + _fr_accum_bonus + _triangle_bonus, 25)
    score += s8
    breakdown['情绪/费率'] = s8

    # ── s8b: VolSkew 成交量方向偿度（三院审核修复 2026-07-08）────────────────
    # 回测鐵证：vskew≥0.52时 ETH底部信号 WR=85.7% EV=+1.43%（最佳阈値）
    # 原理：上涨K线成交量占优势 = 聰明錢在底部悲情入场，信号可靠
    # 来源：已缓存 extra_data 中的 1H klines volume
    try:
        _k1h_vs = extra_data.get('_k1h_raw', []) if extra_data else []
        if len(_k1h_vs) >= 20:
            _uv = sum(float(_k1h_vs[j][5]) for j in range(1, len(_k1h_vs))
                      if float(_k1h_vs[j][4]) >= float(_k1h_vs[j-1][4]))
            _dv = sum(float(_k1h_vs[j][5]) for j in range(1, len(_k1h_vs))
                      if float(_k1h_vs[j][4]) < float(_k1h_vs[j-1][4]))
            _vskew = _uv / (_uv + _dv) if (_uv + _dv) > 0 else 0.5
            # 阈値分级（回测最佳：0.52=+8, 0.50=+4, <0.45=-8）
            if _vskew >= 0.52:
                _vs_pts = +8
            elif _vskew >= 0.50:
                _vs_pts = +4
            elif _vskew <= 0.45:
                _vs_pts = -8   # 下跌量占优 = 戒备主导
            elif _vskew <= 0.48:
                _vs_pts = -4
            else:
                _vs_pts = 0
            if _vs_pts != 0:
                score += _vs_pts
                breakdown['VolSkew'] = _vs_pts
    except Exception:
        pass
    # ──────────────────────────────────────────────────────────────────────

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
        score += s18  # [P1-3修复 2026-07-16 苏摩111] 使用s18上限值不直接覆盖，保证breakdown可对账
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
        # [P1 架构升级 2026-08-13 苏摩111封印] 宏观事件从score层提到风险乘数层
        # 修复前: score += s19（宏观-12直接抵消SMC+15，逻辑混层）
        # 修复后: s19作为风险标志位，注入position_risk_mult影响仓位而非信号质量
        #   s19 <= -8: 高风险宏观事件 → 仓位×0.6
        #   s19 <= -4: 中风险宏观事件 → 仓位×0.8
        #   s19 > 0:  宏观正面 → 仓位×1.1（小奖励）
        #   score不再加减，宏观不影响信号评分，只影响执行仓位
        if s19 <= -8:
            _macro_pos_mult = 0.60
        elif s19 <= -4:
            _macro_pos_mult = 0.80
        elif s19 > 0:
            _macro_pos_mult = 1.10
        else:
            _macro_pos_mult = 1.00
        breakdown['宏观+事件'] = f'风险乘数×{_macro_pos_mult}(s19={s19:+.0f},不扣分)'
        if extra_data is not None:
            extra_data['macro_report'] = _s19_rep
            extra_data['macro_pos_mult'] = _macro_pos_mult
            # 写入ms供auto_executor读取仓位乘数
            ms['macro_pos_mult'] = _macro_pos_mult
        # 同步写入cf让brahma_core能透传给_result
        cf['macro_pos_mult'] = _macro_pos_mult
    except Exception as _e19:
        breakdown['宏观+事件_v2'] = '风险乘数×1.0(获取失败)'  # [P1-B audit-fix]


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
    # [P0-2修复 2026-07-16 苏摩111] 死穴标志：乘数后奖励层统一门控，防止后乘数奖励推升死穴信号
    _in_dead_zone = (
        (_regime_upper in ('BEAR_TREND', 'BEAR_EARLY') and signal_dir == 'LONG') or
        (_regime_upper in ('BULL_TREND', 'BULL_EARLY') and signal_dir == 'SHORT') or
        (_regime_upper == 'BEAR_RECOVERY' and signal_dir == 'SHORT') or
        (_regime_upper == 'BULL_CORRECTION' and signal_dir == 'LONG')
    )

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

    # ── [P1-B 苏摩111批准 2026-07-11] regime_hmm_v2 概率化乘数接入 ──────────────────
    # 架构: HMM概率分布 → get_weighted_multiplier() → 概率加权乘数
    # 降级策略: HMM失败 / confidence<0.55 → 保留规则乘数（零侵入）
    # 效果: 消除硬切换噪声，体制转换期平滑过渡
    _hmm_mult_applied = False
    try:
        import sys as _hmm_sys, os as _hmm_os
        _hmm_sys.path.insert(0, _hmm_os.path.dirname(_hmm_os.path.abspath(__file__)))
        from regime_hmm_v2 import predict_regime_proba, get_weighted_multiplier as _get_hmm_mult
        _hmm_result = predict_regime_proba(_sym, (extra_data or {}).get('_klines_4h'))
        _hmm_conf   = _hmm_result.get('confidence', 0)
        _hmm_method = _hmm_result.get('method', '')
        # 仅当HMM置信度>=0.55时使用概率乘数，否则保留规则乘数
        if _hmm_conf >= 0.55 and _hmm_method != 'rule_fallback':
            _hmm_mult = _get_hmm_mult(_sym, 'LONG' if _is_long_signal else 'SHORT')
            # 安全阀：HMM乘数偏离规则乘数超过40%时降权混合
            _mult_dev = abs(_hmm_mult - _regime_mult) / max(_regime_mult, 0.01)
            if _mult_dev > 0.40:
                # 混合: 60%规则 + 40%HMM（平滑过渡）
                _final_mult = _regime_mult * 0.60 + _hmm_mult * 0.40
                breakdown['HMM乘数'] = f'混合({_hmm_mult:.3f}×0.4+{_regime_mult:.3f}×0.6={_final_mult:.3f}) conf={_hmm_conf:.2f}'
            else:
                _final_mult = _hmm_mult
                breakdown['HMM乘数'] = f'{_hmm_mult:.3f} conf={_hmm_conf:.2f} [{_hmm_method}]'
            _regime_mult = _final_mult
            _hmm_mult_applied = True
        else:
            breakdown['HMM乘数'] = f'降级(规则乘数={_regime_mult:.3f}) conf={_hmm_conf:.2f}'
    except Exception:
        pass  # HMM不可用时静默降级，完全不影响主流程
    # ── [P1-B END] ────────────────────────────────────────────────────────────

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
    # [Phase2 2026-08-21 苏摩111] 趋势行情Kronos降权
    # 根因：RSI_4H=98时Kronos系统性看空，不应全权参与评分
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
            # ── [Phase2] 趋势场景Kronos动态降权 ──────────────────────
            # 趋势突破行情中Kronos系统性反向，降低其权重避免误判
            _rsi_4h_now = float(ms.get('rsi_4h', ms.get('rsi_1h', 50)) if ms else 50)
            if _rsi_4h_now > 90:
                _trend_weight = 0.15   # 极度超买：Kronos几乎噪音
            elif _rsi_4h_now > 75:
                _trend_weight = 0.30   # 趋势突破：降至三成
            elif _rsi_4h_now > 65:
                _trend_weight = 0.60   # 轻度趋势：降至六成
            elif _rsi_4h_now < 25:
                _trend_weight = 0.30   # 极度超卖做多同理
            else:
                _trend_weight = 1.00   # 正常震荡：全权
            _age_factor = _age_factor * _trend_weight
            # ─────────────────────────────────────────────────────────
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
                        breakdown['L7_Kronos'] = f'+{_s}(同向{_kconf:.0%} age={_kage_h:.1f}h [{_met}] tw={_trend_weight:.2f})'
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
                        breakdown['L7_Kronos_v2'] = f'-{_pen}(反向{_kconf:.0%} age={_kage_h:.1f}h [{_met}] tw={_trend_weight:.2f})'  # [P1-B audit-fix] 重复key加后缀
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
    # [潜力释放 P1 2026-07-12] 暴涨猎手豆免通道：FR极度负值 + ATR压缩 = 爆发前元，不应惩罚
    _fr_val = float(ms.get('sentiment', {}).get('funding_rate', 0) if ms else 0)
    _pump_hunter_exempt = (
        _atr_pct_val < 0.005 and          # ATR压缩条件
        _fr_val < -0.0001 and             # FR负值（空头付费）
        signal_dir == 'LONG'              # 做多方向
    )
    if ('BULL_TREND' in _regime_upper and signal_dir == 'LONG'
            and _atr_pct_val < 0.005 and not _direction_block and score > 0):
        if _pump_hunter_exempt:
            breakdown['FIX1_假牛市'] = f'豆免(暴涨猎手) ATR={_atr_pct_val:.4f} FR={_fr_val:.4f}负值压缩=爆发前元'
        else:
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
    # [潜力释放 P1 2026-07-12] N16暴涨猎手豆免：与FIX1共用同一豆免标记
    _n16_pump_exempt = _pump_hunter_exempt  # 继承FIX1的判断结果

    # ══════════════════════════════════════════════════════════════
    # [达摩院v2.0 ATR体制过滤器] N16完整版 — 基于 N16_atr_layers 铁证
    # CHOP 0.005~0.015最优(PF=1.44~1.98) | BULL_TREND(牛市趋势) <0.010禁区(PF=0.567)
    # ══════════════════════════════════════════════════════════════
    _atr_regime_tag = ''
    if 'BULL_TREND' in _regime_upper:
        # BULL_TREND(牛市趋势) ATR禁区：<0.010 PF=0.567（铁证）
        if _atr_v4 < 0.010 and not _direction_block and score > 0:
            if _n16_pump_exempt:
                # [P0-3修复 2026-07-16 苏摩111] if/elif/else三分支，豁免后不再执行惩罚
                _atr_regime_tag = f'N16_豁免(暴涨猎手) ATR={_atr_v4:.4f} FR负值压缩=爆发前元'
                # 不执行任何惩罚，直接跳过
            elif 155 <= score < 165:
                # [达摩院修正 2026-07-16 苏摩111] BULL_TREND 155-164 + ATR<0.010
                # WR=0% → 强制降级WATCH
                score = 130  # 强制落入ENTER_WATCH区，拦截ENTER_FULL
                _atr_regime_tag = f'N16_ATR禁区_WATCH强制 155-164降130 (BULL ATR={_atr_v4:.4f}<0.010, WR=0%)'
            else:
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
                            pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        # 当无regime_switch_state时，保d原 N14逻辑
        elif 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42:
            _n14_delta = 5
            _n14_label = '熊市边界早鸟(fallback)'
        if _n14_delta != 0:
            score = max(0, min(int(score) + _n14_delta, 175))
            breakdown['N14_体制切换时机'] = f'{_n14_delta:+d} ({_n14_label} dist={_dist_min if "_dist_min" in dir() else "?":.0f}min PF={_rt14.get(str(int(_dist_min))+"~"+str(int(_dist_min)+5),{}).get("pf","?")})'
            pass  # [静默] f'[N14-Timing] {_sym}: {_n14_delta:+d}分 {_n14_label}'
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
            pass  # [静默] f'[M09-DimWeight] {_sym}: {_m09_delta:+d}分 | {" | ".join(_m09_log)}'
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
    # [v14.0 设计院 2026-07-08] action阈值与宪法门槛对齐
    # 宪法：valid_signal需score≥155；action=ENTER不应在score<138时触发
    # 修复：ENTER_FULL≥155，ENTER≥138（铁证线），WATCH≥100，低分→WATCH_ONLY
    if score >= 155:
        grade = '🔴神级';  kelly_mult = 2.0;  action = 'ENTER_FULL'  # [N18] 顶级信号全仓
    elif score >= 138:
        grade = '🟠极强';   kelly_mult = 1.5;  action = 'ENTER'       # [N18] 铁证线以上
    elif score >= 130:
        grade = '🟡强+';   kelly_mult = 1.0;  action = 'ENTER_WATCH'  # [v7.0 2026-07-11] 六方封印 130-138新层
    elif score >= 110:
        grade = '🟡强';    kelly_mult = 0.5;  action = 'WATCH'        # [v14.0] 110-130降为WATCH
    elif score >= 80:
        grade = '🔵中等';   kelly_mult = 0.3;  action = 'WATCH'
    else:
        grade = '⚫放弃';   kelly_mult = 0.0;  action = 'SKIP'

    # [2026-07-28 设计院全局修复] grade emoji → 数字，保留grade_label供展示
    _GRADE_MAP = {'🔴神级':170,'🟠极强':145,'🟡强+':133,'🟡强':118,'🔵中等':85,'⚫放弃':0}
    grade_label = grade  # 保留emoji供展示
    grade = _GRADE_MAP.get(grade, max(int(score), 0))  # emoji → 数字

    return {
        'total':      score,
        'score':      score,    # [P1修复 2026-07-12] 补充score别名 — analyze()/run_analysis读.get('score')，原只有'total'导致永远None
        'max':        175,   # [P0-7修复 2026-07-16 苏摩111] 与实际天花板min(score,175)对齐，原150导致归一化超界
        'grade':      grade,
        'grade_num':  score,   # [设计院 2026-06-30 G修复] brahma_analyze.py期期得此字段，补入整数评分
        'kelly_mult': kelly_mult,
        'action':     action,   # 注意：若params.valid=False，analyze()会覆盖此字段
        'breakdown':  breakdown,
        # [2026-07-28 设计院全局修复] 补充price/regime，下游字段防御
        'price':      ms.get('price'),
        'regime':     ms.get('regime'),
        'symbol':     ms.get('symbol'),
    }

# ═══════════════════════════════════════════════════════════════
# 精确交易参数生成
# ═══════════════════════════════════════════════════════════════


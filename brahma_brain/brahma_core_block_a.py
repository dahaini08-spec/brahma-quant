#!/usr/bin/env python3
# ponytail: brahma_core_block_a 366行，核心计算，35维共享_result状态，拆分条件: 状态隔离方案成熟后
"""
brahma_core_block_a.py — 技术分析层 (维度1-6)
[设计院封印 2026-08-11 苏摩111]

从 brahma_core.py L168-L491 提取
职责：纯技术维度评分（EMA趋势/OB精度/RSI背离/SMC结构/量能/形态）
      无网络依赖，无全局状态，无外部IO

输入：ms, smc, signal_dir, extra_data, score(int), breakdown(dict)
输出：dict {s1, s2, s3, s4, s5, s5b, s6, score, breakdown}

调用方：brahma_core.confluence_score()
        from brahma_brain.brahma_core_block_a import calc_block_a
        r = calc_block_a(ms, smc, signal_dir, extra_data, score, breakdown)
        s1,s2,s3,s4,s5,s5b,s6 = r['s1'],r['s2'],r['s3'],r['s4'],r['s5'],r['s5b'],r['s6']
        score = r['score']
        breakdown = r['breakdown']
"""
import math


def calc_block_a(ms: dict, smc: dict, signal_dir: str,
                 extra_data: dict, score: int, breakdown: dict,
                 symbol: str = '') -> dict:
    """
    维度1-6：纯技术分析层

    维度1: 趋势一致性 (0~20)  - EMA/ADX多周期共识
    维度2: 关键位精确度 (0~20) - OB新鲜度/FVG/Fib
    维度3: 动量背离确认 (0~20) - RSI极值/MACD背离
    维度4: SMC结构支持 (0~20)  - CHoCH/BOS/OB等级
    维度5: 量能验证 (0~20)     - BB位置/ATR/成交量
    维度6: 形态成熟度 (0~20)   - Elliott/Pattern

    Returns: dict with s1-s6, score, breakdown
    """
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
    elif adx_1h < 20:  s1 = max(s1 - 5, 0)  # [P1 2026-08-28] ADX<20=假趋势，扣5分

    # ── [P1 2026-08-28 苏摩111] EMA200方向过滤 ─────────────────────────
    # 铁证：Layer1 30,408笔回测：价格>EMA200做多、<EMA200做空是机构最常用的大方向过滤
    # 高盛/JPM技术团队标配指标，高于EMA200=牛市，低于=熊市
    try:
        _ema200_1h = ms.get('trend', {}).get('1h', {}).get('ema200', 0)
        if not _ema200_1h:
            # 备用：从 indicators 读取
            _ema200_1h = ms.get('indicators', {}).get('ema200_1h', 0) or \
                         ms.get('indicators', {}).get('ema200', 0)
        _price_now = ms.get('price', 0)
        if _ema200_1h > 0 and _price_now > 0:
            _above_ema200 = _price_now > _ema200_1h
            if signal_dir == 'LONG' and _above_ema200:
                s1 = min(s1 + 4, 20)   # 价格>EMA200做多，妇合大方向+4
                breakdown['EMA200确认'] = f'+4 (价格{_price_now:.0f}>EMA200={_ema200_1h:.0f}，顺势多)'
            elif signal_dir == 'LONG' and not _above_ema200:
                s1 = max(s1 - 6, 0)    # 价格<EMA200做多，逆大势-6
                breakdown['EMA200逆势'] = f'-6 (价格{_price_now:.0f}<EMA200={_ema200_1h:.0f}，逆势多)'
            elif signal_dir == 'SHORT' and not _above_ema200:
                s1 = min(s1 + 4, 20)   # 价格<EMA200做空，妇合大方向+4
                breakdown['EMA200确认'] = f'+4 (价格{_price_now:.0f}<EMA200={_ema200_1h:.0f}，顺势空)'
            elif signal_dir == 'SHORT' and _above_ema200:
                s1 = max(s1 - 6, 0)    # 价格>EMA200做空，逆大势-6
                breakdown['EMA200逆势'] = f'-6 (价格{_price_now:.0f}>EMA200={_ema200_1h:.0f}，逆势空)'
    except Exception:
        pass
    # ───────────────────────────────────────────────────────────────────────────
    s1 = min(s1, 20)
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

    # ── Step2: OB/FVG跨周期共振评分（设计院 2026-08-25 苏摔111 Step2封印）────
    try:
        from multi_tf_context_builder import _snapshot_one_tf, _calc_resonance
        s_15m = _snapshot_one_tf(symbol, '15m', signal_dir)
        s_1h  = _snapshot_one_tf(symbol, '1h',  signal_dir)
        res   = _calc_resonance({'15m': s_15m, '1h': s_1h,
                                  '4h': _snapshot_one_tf(symbol, '4h', signal_dir),
                                  '1d': _snapshot_one_tf(symbol, '1d', signal_dir)},
                                signal_dir)
        resonance_score = max(-15, min(20, res['score']))
        score += resonance_score
        breakdown['OB_FVG跨周期共振'] = resonance_score
        breakdown['_resonance_detail'] = ' | '.join(res['details'][:2])
    except Exception:
        pass

    # ── Step3: EMA多周期排列分（设计院 2026-08-25 苏摔111 Step3封印）────
    try:
        from multi_tf_context_builder import _snapshot_one_tf as _stf
        tfs_ema = ['15m', '1h', '4h', '1d']
        snaps = {tf: _stf(symbol, tf, signal_dir) for tf in tfs_ema}
        is_long = signal_dir in ('LONG', 'UP', 'long')
        agree = sum(1 for tf in tfs_ema if snaps[tf].get('ema_bull') == is_long)
        # EMA全局共振分：4TF全同向+10，3同向+5，2同向+0，1同向-5
        ema_score = {4: 10, 3: 5, 2: 0, 1: -5, 0: -10}.get(agree, 0)
        score += ema_score
        ema_desc = ' '.join(f'{tf}:{"↑" if snaps[tf].get("ema_bull") else "↓"}' for tf in tfs_ema)
        breakdown['EMA多周期共振'] = ema_score
        breakdown['_ema_align'] = f'{agree}/4TF同向 {ema_desc}'
    except Exception:
        pass

    return {
        's1': s1, 's2': s2, 's3': s3, 's4': s4,
        's5': s5, 's5b': s5b, 's6': s6,
        'score': score, 'breakdown': breakdown,
    }

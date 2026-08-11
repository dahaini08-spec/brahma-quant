#!/usr/bin/env python3
"""
brahma_core_block_b.py — 链上/清算/资金费层 (维度7-10)
[设计院封印 2026-08-11 苏摩111]

从 brahma_core.py L184-L485 提取（拆分后行号）
职责：实时链上数据维度评分

维度7:  清算带/OI (0~10)  - ws_guardian清算数据/OI变化
维度8:  资金费率+情绪 (0~10) - FR/多空比/情绪引擎
维度9:  时段权重 - 亚盘/美盘/欧盘
维度10: 谐波PRZ + 多周期对齐

依赖: ws_guardian(可选), s7_liq_config, orderbook_heatmap,
      liq_density_engine, bybit_liq_adapter, volume_profile
失败时全部 try/except 归零（fail-safe）

输入: ms, smc, signal_dir, extra_data, score, breakdown
输出: dict {s7, s8, s9, s10, score, breakdown}
"""
import math
import datetime


def calc_block_b(ms: dict, smc: dict, signal_dir: str,
                 extra_data: dict, score: int, breakdown: dict) -> dict:
    """
    维度7-10：链上/清算/资金费层

    维度7:  清算带/OI          (ws_guardian实时数据)
    维度8:  资金费率+情绪       (funding rate / LSR)
    维度9:  时段权重            (亚/欧/美盘精细化)
    维度10: 谐波PRZ+多周期对齐  (Harmonic/MTF)

    Returns: dict with s7-s10, score, breakdown
    """
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
        if _s7os.path.dirname(_s7os.path.dirname(__file__)) not in _s7sys.path: _s7sys.path.insert(0, _s7os.path.dirname(_s7os.path.dirname(__file__)))
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

    # ── P0-A 全局上限封印（设计院六方联合 2026-07-11）────────────────
    # 问题：s7基础(max15)+增强层①(max15)+增强层②(max15)=理论最高45分
    #       清算层权重严重失控，导致高清算密集区评分虚高
    # 封印：s7全局上限=20（设计上限10→适度放开20，但禁止三层叠加超额）
    #       下限=-20（已有，保留否决权机制）
    s7 = max(-20, min(20, s7))
    score += s7
    breakdown['清算/OI'] = s7

    # ── s7增强层③: bybit_liq_adapter L/S拥挤度补充（2026-08-09 设计院接入）──
    # 独立于liq_density_engine，提供L/S拥挤度方向性信号
    # LONG_CROWDED(ratio>1.5) 做空 +3 / SHORT_CROWDED(ratio<0.7) 做多 +3
    try:
        from brahma_brain.bybit_liq_adapter import get_ls_ratio_signal as _bla_ls
        _bla = _bla_ls(_sym)
        _bla_pressure = _bla.get('liq_pressure', 'BALANCED')
        _bla_delta = 0
        if signal_dir == 'SHORT' and _bla_pressure == 'LONG_CROWDED':
            _bla_delta = 3   # 多头过拥挤 → 顺势做空 +3
        elif signal_dir == 'LONG' and _bla_pressure == 'SHORT_CROWDED':
            _bla_delta = 3   # 空头过拥挤 → 顺势做多 +3
        elif signal_dir == 'LONG' and _bla_pressure == 'LONG_CROWDED':
            _bla_delta = -3  # 多头过拥挤 → 逆势做多 -3
        if _bla_delta != 0:
            score += _bla_delta
            breakdown['L/S拥挤度'] = _bla_delta
    except Exception:
        pass

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
    s8 = min(s8_base + onchain_bonus + basis_bonus + _oc_bonus, 20)  # [UP-017] +CoinGlass链上
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

    return {
        's7': s7, 's8': s8, 's9': s9, 's10': s10,
        'score': score, 'breakdown': breakdown,
    }

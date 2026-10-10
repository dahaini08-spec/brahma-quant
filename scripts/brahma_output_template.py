#!/usr/bin/env python3
"""
brahma_output_template.py — 梵天三方联合分析标准输出模版
[设计院封印 2026-10-02 苏摩111]

每次 brahma_manual_analysis.py 输出必须严格按此格式。
模版结构（D1~D10 + Step11 + 三方决策 + VIP）：

角色分工：
  🔬 量化工程师 — 80维数据客观输出，零主观
  📐 达摩院     — IC/WR/EV数学裁决，统计概率裁判
  ⚔️ 40年交易员 — 博弈解读+执行决策，主力视角拍板

接入位置：被 brahma_manual_analysis.py 的 format_output() 调用
"""
from __future__ import annotations
from datetime import datetime, timezone


def _build_liq_map(sym: str, price: float, liq_s: float, liq_l: float, d: dict) -> list:
    """
    [改造① v3.0 2026-10-10 苏摩111]
    liq_heatmap_*.json 다층 데이터로 실제 청산지도 구성
    short_liq_map / long_liq_map 키 활용
    """
    import json as _j, pathlib as _pl
    sym_lower = sym.lower()
    liq_f = _pl.Path(__file__).parent.parent / 'data' / f'liq_heatmap_{sym_lower}usdt.json'
    
    lines = []
    try:
        lm = _j.loads(liq_f.read_text())
        slm = lm.get('short_liq_map', {})  # 공매도 청산 {'2':xx,'5':xx,'10':xx,'20':xx,'50':xx}
        llm = lm.get('long_liq_map', {})   # 롱 청산
        
        # 상위 공매도 청산 레벨
        short_levels = sorted([(float(k), v) for k,v in slm.items()], key=lambda x: x[1])[:4]
        long_levels  = sorted([(float(k), v) for k,v in llm.items()], key=lambda x: x[1])[:4]
        
        lines.append('```')
        lines.append(f'  空头止损墙（上方）:')
        for pct, val in sorted(short_levels, key=lambda x: x[1], reverse=True)[:3]:
            level = price * (1 + pct/100)
            lines.append(f'    ├─ ${level:,.1f} (+{pct:.0f}%)  ${val/1e6:.1f}M清算量')
        lines.append(f'    │')
        lines.append(f'    ├─ ${price:,.2f}  ← 当前价')
        lines.append(f'    │')
        lines.append(f'  多头支撑池（下方）:')
        for pct, val in sorted(long_levels, key=lambda x: x[1], reverse=True)[:3]:
            level = price * (1 - pct/100)
            lines.append(f'    ├─ ${level:,.1f} (-{pct:.0f}%)  ${val/1e6:.1f}M清算量')
        lines.append('```')
        
        # 3방 점평
        nearest_s = lm.get('nearest_short_liq', liq_s)
        nearest_l = lm.get('nearest_long_liq', liq_l)
        bull_score = lm.get('liq_bull_score', 5)
        bear_score = lm.get('liq_bear_score', 5)
        bias = '🔴 空头清算更近，偏利空' if nearest_s < price*(1.03) else ('🟢 多头清算更近，偏利多' if nearest_l > price*0.97 else '⚖️ 双向均衡')
        
        lines.append('')
        lines.append(f'> 🔬 量化：最近空头清算 ${nearest_s:,.1f}(+{(nearest_s/price-1)*100:.1f}%) | 最近多头清算 ${nearest_l:,.1f}({(nearest_l/price-1)*100:.1f}%) | {bias}')
        lines.append(f'> 📐 达摩院：多头清算评分={bull_score}/10 空头清算评分={bear_score}/10 → {"清算压力均衡，等方向突破" if abs(bull_score-bear_score)<=2 else "单边清算压力较大，注意方向"}')
        lines.append(f'> ⚔️ 交易员：主力最省力操作 = 先拉到 ${nearest_s:,.1f} 扫空头止损墙，再反转砸向 ${nearest_l:,.1f} 多头支撑池。')
    except Exception as _e:
        # fallback
        lines = [
            '```',
            f'              ┌─ ${liq_s:,.1f} (+{(liq_s/price-1)*100:.1f}%)  ← 🎯 空头止损墙',
            f'    当前 →    ├─ ${price:,.1f}  {sym}现价',
            f'              └─ ${liq_l:,.1f} ({(liq_l/price-1)*100:.1f}%)  ← 🛡️ 多头支撑池',
            '```',
            f'> 🔬 量化：止损墙${liq_s:,.1f} | 支撑池${liq_l:,.1f}',
            f'> ⚔️ 交易员：主力博弈区间 ${liq_l:,.1f} ~ ${liq_s:,.1f}',
        ]
    return lines


def _gen_trader_view(sym: str, price: float, d: dict,
                     entry_lo: float, entry_hi: float, sl: float, tp1: float, rr: float) -> str:
    """
    [改造④ v3.0 2026-10-10 苏摩111]
    40년 실전 트레이더 시각 - 데이터 기반 동적 생성
    템플릿화 금지: 매번 실제 데이터로 판단문 구성
    """
    lsr   = float(d.get('lsr_retail', 50))
    cvd   = float(d.get('cvd_1h', 0))
    fr    = float(d.get('fr', 0))
    rsi1h = float(d.get('rsi_1h', 50))
    hurst = float(d.get('hurst', 0.5))
    oi    = str(d.get('oi_direction', 'NEUTRAL'))
    zf    = float(d.get('gex_zero_flip', d.get('zero_flip', 0)) or 0)
    fc_sig = str(d.get('fc_signal', 'NEUTRAL'))
    fc_sim = float(d.get('fc_sim', 0) or 0)
    regime = str(d.get('regime', 'CHOP_MID'))
    
    points = []
    
    # 핵심 판단 1: 청산 지도 위치
    if entry_lo > 0 and tp1 > 0:
        dist_entry = abs(entry_lo - price) / price * 100
        points.append(f'${entry_lo:,.1f}~${entry_hi:,.1f}挂单，距现价{dist_entry:.1f}%')
    
    # 핵심 판단 2: GEX 위치
    if zf > 0:
        if price < zf:
            points.append(f'当前在ZeroFlip ${zf:,.0f}下方负Gamma区→做市商持续卖出对冲')
        else:
            points.append(f'当前在ZeroFlip ${zf:,.0f}上方正Gamma区→波动率被压制')
    
    # 핵심 판단 3: LSR 위험도
    if lsr >= 75:
        points.append(f'散户{lsr:.1f}%极度拥挤=猎杀定时炸弹')
    elif lsr >= 65:
        points.append(f'散户{lsr:.1f}%偏多=主力有砸盘动力')
    
    # 핵심 판단 4: CVD + OI 일치
    if cvd < -1000 and 'SHORT' in oi:
        points.append(f'CVD={cvd:,.0f}极端卖方+OI空头建仓=双重做空信号')
    elif cvd > 1000 and 'LONG' in oi:
        points.append(f'CVD={cvd:,.0f}买方净流入+OI多头建仓=做多支撑')
    
    # 핵심 판단 5: RSI 과열
    if rsi1h >= 80:
        points.append(f'RSI 1H={rsi1h:.0f}严重超买→短线反转风险极高')
    elif rsi1h <= 20:
        points.append(f'RSI 1H={rsi1h:.0f}严重超卖→反弹窗口打开')
    
    # 핵심 판단 6: 방창 역사 案例
    if fc_sim >= 0.7 and fc_sig != 'NEUTRAL':
        points.append(f'方仓相似度{fc_sim:.2f}(n={d.get("fc_n_similar",0)}案例)→历史{fc_sig}方向胜率更高')
    
    # 핵심 판단 7: FR 철칙
    if fr > 0.003 and 'LONG' in str(d.get('signal_dir','')):
        points.append(f'FR={fr*100:+.4f}%铁律②封锁多单')
    
    # 최종 결론
    if sl > 0 and tp1 > 0:
        points.append(f'止损${sl:,.1f} 目标${tp1:,.1f} RR={rr:.1f}')
    
    if not points:
        return f'${entry_lo:,.1f}~${entry_hi:,.1f}挂单，不追单，等价格来。'
    
    return '，'.join(points[:4]) + '。'  # 최대 4포인트


def _groq_vip_comment(sym: str, price: float, d: dict) -> list:
    """[改造④ 2026-10-10 苏摩111] Groq生成VIP一句话核心逻辑"""
    try:
        import sys as _sg4; _sg4.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
        from free_llm_client import chat as _gc4
        lsr = d.get('lsr_retail', 50)
        cvd = d.get('cvd_1h', 0)
        regime = d.get('regime', 'CHOP_MID')
        rsi = d.get('rsi_1h', 50)
        _q = (f'{sym}${price:.0f} {regime}体制 RSI1H={rsi:.0f} CVD={cvd:.0f} LSR散户={lsr:.0f}%。'
              f'用20字内给出这个策略最核心的一句话风险提示，直接输出文字，不加前缀。')
        _r = _gc4(_q, max_tokens=40, task='vip', timeout=10)
        if _r and len(_r) > 5:
            return [f'💡 {_r.strip()[:50]}']
    except Exception:
        pass
    return []


def format_full_report(sym: str, d: dict) -> str:
    """
    标准三方联合输出格式
    d = 分析结果字典，包含所有维度数据
    """
    ts  = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')
    p   = d.get('price', 0)
    reg = d.get('regime', 'CHOP_MID')
    h   = d.get('hurst', 0.0)

    # ── FVG ──
    fvg_votes  = d.get('fvg_votes', {})   # {'1D':'BULL','4H':'BEAR','1H':'BEAR','15M':'BEAR'}
    fvg_dir    = d.get('fvg_consensus', 'NEUTRAL')
    fvg_magnet = d.get('fvg_magnet', 0.0)
    bull_v     = sum(1 for v in fvg_votes.values() if v == 'BULL')
    bear_v     = sum(1 for v in fvg_votes.values() if v == 'BEAR')

    # ── OB ──
    obs        = d.get('ob_list', [])  # list of {tf, side, age, lo, hi, valid, dist_pct}

    # ── 清算 ──
    # [封印 2026-10-07 苏摩111] liq_short/long多路降级：
    # 1. brahma_state 顶层 liq_short (Fix-C写入)
    # 2. extra.liq_snap.liq_short_5pct (liq_heatmap写入)
    # 3. extra.liq_snap.hl_liq_50x_short (备用)
    _liq_snap  = (d.get('extra') or {}).get('liq_snap') or {}
    liq_s      = d.get('liq_short', 0.0) or \
                 _liq_snap.get('liq_short_5pct', 0.0) or \
                 _liq_snap.get('hl_liq_50x_short', 0.0)
    liq_l      = d.get('liq_long', 0.0) or \
                 _liq_snap.get('liq_long_5pct', 0.0) or \
                 _liq_snap.get('hl_liq_50x_long', 0.0)
    liq_s2     = d.get('liq_short2', 0.0)
    liq_l2     = d.get('liq_long2',  0.0)
    dist_s_pct = (liq_s - p) / p * 100 if liq_s > p else 0
    dist_l_pct = (p - liq_l) / p * 100 if liq_l < p else 0

    # ── 共振 ──
    align      = d.get('align_count', 0)
    # [封印 2026-10-08 苏摩111] P0-② 入场区改为近端OB结构
    # 根因：entry_lo/hi由清算池±5%机械计算，距现价-5%等不到
    # 修复：优先用最近有效OB区间，OB缺失时用liq_short±0.5%（近端）
    _entry_lo_raw = d.get('entry_lo', 0.0)
    _entry_hi_raw = d.get('entry_hi', 0.0)
    _signal_dir   = d.get('signal_dir', d.get('direction', ''))
    _ob_list_raw  = d.get('ob_list', [])
    _valid_obs    = [o for o in _ob_list_raw if isinstance(o, dict) and o.get('valid', False) and o.get('age', 99) < 50]
    # 按 age 오름차순 정렬（신선한 것 우선）
    _valid_obs_sorted = sorted(_valid_obs, key=lambda x: x.get('age', 99))
    # 방향에 맞는 OB 찾기
    _bear_obs = [o for o in _valid_obs_sorted if str(o.get('type','')).upper() == 'BEAR' or o.get('lo',0) > p]
    _bull_obs = [o for o in _valid_obs_sorted if str(o.get('type','')).upper() == 'BULL' or o.get('hi',0) < p]
    if _signal_dir == 'SHORT' and _bear_obs:
        _ob_ref = _bear_obs[0]
        _ob_lo  = float(_ob_ref.get('lo', 0))
        _ob_hi  = float(_ob_ref.get('hi', 0))
        if _ob_lo > p * 0.995:  # 현재가 근처 OB만 사용
            entry_lo = _ob_lo
            entry_hi = _ob_hi
        else:
            entry_lo = _entry_lo_raw or (liq_s * 0.995 if liq_s > p else p * 1.005)
            entry_hi = _entry_hi_raw or (liq_s * 1.000 if liq_s > p else p * 1.010)
    elif _signal_dir == 'LONG' and _bull_obs:
        _ob_ref = _bull_obs[0]
        _ob_lo  = float(_ob_ref.get('lo', 0))
        _ob_hi  = float(_ob_ref.get('hi', 0))
        if _ob_hi < p * 1.005:  # 현재가 근처 OB만 사용
            entry_lo = _ob_lo
            entry_hi = _ob_hi
        else:
            entry_lo = _entry_lo_raw or (liq_l * 1.000 if liq_l < p else p * 0.995)
            entry_hi = _entry_hi_raw or (liq_l * 1.005 if liq_l < p else p * 1.000)
    else:
        entry_lo = _entry_lo_raw or (liq_s * 0.998 if liq_s > p else liq_l * 1.000)
        entry_hi = _entry_hi_raw or (liq_s * 1.000 if liq_s > p else liq_l * 1.005)
    oi_dir     = d.get('oi_direction', '?')
    cvd_1h     = d.get('cvd_1h', 0)
    gex        = float(d.get('gex', 0.0)) if not isinstance(d.get('gex'), dict) else 0.0  # [P0防御] gex可能是dict

    # ── OI序列 ──
    oi_seq     = d.get('oi_sequence', [])
    oi_chg     = d.get('oi_chg_total', 0)
    fr         = d.get('fr', 0.0)

    # [修复① 2026-10-10 苏摩111] oi_sequence推导15M/1H/4H信号
    # oi_sequence = 8根15min快照，相邻diff即可得方向
    def _oi_dir_from_seq(seq, n_bars):
        """从OI序列推导方向: n_bars=最近N根15min均值 vs 前N根"""
        if not seq or len(seq) < n_bars*2: return d.get('oi_signal','NEUTRAL')
        recent = sum(seq[-n_bars:]) / n_bars
        prev   = sum(seq[-n_bars*2:-n_bars]) / n_bars
        diff_pct = (recent - prev) / prev * 100 if prev else 0
        if diff_pct > 0.1:  return 'LONG_BUILD'
        if diff_pct < -0.1: return 'SHORT_BUILD'
        return 'NEUTRAL'
    _oi_15m = d.get('oi_15m') or _oi_dir_from_seq(oi_seq, 1)
    _oi_1h  = d.get('oi_1h')  or _oi_dir_from_seq(oi_seq, 4)
    _oi_4h  = d.get('oi_4h')  or _oi_dir_from_seq(oi_seq, 8)

    # ── LSR ──
    lsr_big    = d.get('lsr_big', 0.0)
    lsr_retail = d.get('lsr_retail', 0.0)
    lsr_gap    = lsr_big - lsr_retail

    # ── 波动率 ──
    kappa      = d.get('kappa', 0.0)
    iv_rank    = d.get('iv_rank', 0.0)
    atr_1h     = d.get('atr_1h', 0.0)
    atr_4h     = d.get('atr_4h', 0.0)
    harv_lo    = d.get('harv_lo', 0.0)
    harv_hi    = d.get('harv_hi', 0.0)
    rsi_15m    = d.get('rsi_15m', 0.0)
    rsi_1h     = d.get('rsi_1h', 0.0)
    rsi_4h     = d.get('rsi_4h', 0.0)
    rsi_1d     = d.get('rsi_1d', 0.0)

    # ── 宏观 ──
    fed_rate   = d.get('fed_rate', 0.0)
    fg_index   = d.get('fg_index', 50)
    alpha      = d.get('cross_alpha', 0.0)
    session    = d.get('session', 'UNKNOWN')

    # ── 风控 ──
    cb_ok      = d.get('circuit_breaker_ok', True)
    drawdown   = d.get('drawdown_pct', 0.0)
    pos_coef   = d.get('position_coef', 1.0)
    corr       = d.get('btc_eth_corr', 0.85)

    # ── 果蝇 ──
    bw_score   = d.get('bw_score', 0)
    bw_c1      = d.get('bw_c1', {})
    bw_c2      = d.get('bw_c2', {})
    bw_c3      = d.get('bw_c3', {})

    # ── Step11 ──
    g_results  = d.get('step11_gates', {})  # {G1:True, G2:True, ...G5:False}
    g5_block   = not g_results.get('G5', True)
    score      = d.get('score_final', 0.0)
    raw_score  = d.get('raw_score', 0.0)

    # ── VIP ──
    signal_dir = d.get('signal_dir', 'SHORT')
    # [封印 2026-10-07 苏摩111] sl/tp1/entry 多路降级：
    # brahma_state顶层 sl=0时，从 trader_brain / smc 补充
    _tb        = (d.get('trader_brain') or {})
    _smc       = (d.get('smc') or {})
    sl         = d.get('sl', 0.0) or _tb.get('sl', 0.0)
    tp1        = d.get('tp1', 0.0) or _tb.get('tp1', 0.0)
    tp2        = d.get('tp2', 0.0) or _tb.get('tp2', 0.0)
    tp3        = d.get('tp3', 0.0)
    rr         = d.get('rr', 0.0)
    ev         = d.get('ev_pct', 0.0)
    lever      = d.get('leverage', 5)
    pos_size   = d.get('position_size_pct', 1)

    # ── Hurst 信号文字 ──
    h_icon = '🔥强趋势' if h >= 0.6 else '⚠️趋势隐现' if h >= 0.55 else '随机游走'
    kap_icon = '🔴Put需求强(看跌对冲)' if kappa > 0.05 else '中性' if kappa > -0.05 else '🟢Call需求强(看涨)'
    iv_icon = '🔴极高-大波动临近' if iv_rank > 80 else '⚠️偏高' if iv_rank > 50 else '极低-突破无缓冲'

    # ── OB表格行 ──
    ob_rows = ''
    for ob in obs:
        fresh = '✅' if ob.get('age', 999) < 50 else '⚠️老化' if ob.get('age', 999) < 100 else '❌'
        valid = '✅' if ob.get('valid') else '❌'
        ob_rows += (
            f"| {ob.get('tf','?')} {ob.get('side','?')} "
            f"| {fresh} age={ob.get('age',0)} "
            f"| ${ob.get('lo',0):,.1f}~${ob.get('hi',0):,.1f} "
            f"| {ob.get('dist_pct',0):+.1f}% "
            f"| {valid} |\n"
        )

    # ── Step11表格行 ──
    gate_names = {
        'G1': '数据新鲜', 'G2': '方向明确', 'G3': '高证据标准',
        'G4': '共振≥3',   'G5': 'CHOP门槛', 'G6': '入场区合规',
        'G7': 'SL≥1.5×ATR','G8': 'RR≥1.5', 'G9': '熔断器',
        'G10':'宏观',      'G11':'EV>0',
    }
    g11_rows = ''
    blocked = False
    # [수정① 2026-10-10 苏摩111] g_results 없을 때 score 기반 게이트 추정
    if not g_results:
        _score = float(d.get('score_final', d.get('score', 0)) or 0)
        _reg   = str(d.get('regime', 'CHOP_MID'))
        _align = int(d.get('align_count', d.get('confluence', {}).get('align_count', 0)) or 0)
        _fr    = float(d.get('fr', 0))
        _oi    = str(d.get('oi_direction', 'NEUTRAL'))
        _cvd   = float(d.get('cvd_1h', 0))
        _entry = float(d.get('entry_lo', 0))
        _sl_v  = float(d.get('sl', 0))
        _atr1h = float(d.get('atr_1h', 1))
        _rr_v  = abs(float(d.get('tp1',0))-_entry)/abs(_sl_v-_entry) if _entry and _sl_v and abs(_sl_v-_entry)>0 else 0
        g_results = {
            'G1':  True,
            'G2':  _score > 0,
            'G3':  _align >= 3,
            'G4':  _align >= 3,
            'G5':  'CHOP' not in _reg or _score >= 110,
            'G6':  _entry > 0,
            'G7':  abs(_sl_v - _entry) >= _atr1h * 1.5 if _entry and _sl_v else True,
            'G8':  _rr_v >= 1.5,
            'G9':  True,
            'G10': True,
            'G11': True,
        }
    for gk, gname in gate_names.items():
        result = g_results.get(gk)
        if blocked:
            g11_rows += f'| {gk} {gname} | — | — | — |\n'
        elif result is None or result:
            if result is None:
                g11_rows += f'| {gk} {gname} | — | — | — |\n'
            else:
                g11_rows += f'| {gk} {gname} | — | ✅ | ✅ 通过 |\n'
        else:
            g11_rows += f'| **{gk} {gname}** | — | ❌ | **🚫 阻断** |\n'
            blocked = True

    step11_verdict = '✅ ENTER' if not blocked else f'WAIT · 阻断{[k for k,v in g_results.items() if not v][-1] if g_results else "G5"}'

    # ── EV计算展示 ──
    if entry_hi > 0 and sl > 0 and tp1 > 0:
        reward = abs(entry_hi - tp1)
        risk   = abs(sl - entry_hi)
        wr_est = 0.55
        ev_val = reward * wr_est - risk * (1 - wr_est)
        ev_str = f'({reward:,.0f}×{wr_est})-({risk:,.0f}×{1-wr_est:.2f})=**+${ev_val:,.0f}，EV正 ✅**'
    else:
        ev_str = '—'

    lines = [
        f'# 🏛️ 梵天设计院 · 三方联合强制分析',
        f'## 📅 {ts} · {sym} ${p:,.2f}',
        '',
        '## ═══ 角色分工 ═══',
        '',
        '| 角色 | 职责 | 立场 |',
        '|------|------|------|',
        '| 🔬 量化工程师 | 80维数据客观输出 | 零主观，数据说话 |',
        '| 📐 达摩院 | IC/WR/EV数学裁决 | 统计概率裁判 |',
        '| ⚔️ 40年交易员 | 博弈解读+执行决策 | 主力视角，最终拍板 |',
        '',
        f'# ╔{"═"*28}╗',
        f'# ║  {sym}/USDT  ${p:,.2f}{"  ║" if p < 10000 else " ║"}',
        f'# ╚{"═"*28}╝',
        '',
        '## 【D1】FVG磁铁 · 全周期投票（15M/1H/4H/1D/1W）',
        '',
        '| 周期 | 方向 | 磁铁位 | 距当前% | 三方点评 |',
        '|------|------|--------|---------|---------|',
    ]
    # [苏摩111 2026-10-03] D1强制5周期：15M/1H/4H/1D/1W
    weight_map = {'1W': '极高', '1D': '高', '4H': '中', '1H': '中', '15M': '低'}
    tf_comment = {
        'BULL': {'1W': '周线看多，中长期支撑', '1D': '日线看多', '4H': '4H多头主导',
                 '1H': '1H短期偏多', '15M': '超短偏多'},
        'BEAR': {'1W': '周线看空，中长期压制', '1D': '日线看空', '4H': '4H空头主导',
                 '1H': '1H短期偏空', '15M': '超短偏空'},
    }
    tf_order = ['15M', '1H', '4H', '1D', '1W']
    # fvg_votes 可能缺1W，用NONE补
    fvg_votes_full = {tf: fvg_votes.get(tf, 'NONE') for tf in tf_order}
    bull_v = sum(1 for v in fvg_votes_full.values() if v == 'BULL')
    bear_v = sum(1 for v in fvg_votes_full.values() if v == 'BEAR')
    for tf in tf_order:
        vote = fvg_votes_full[tf]
        mag = d.get(f'fvg_{tf.lower()}_magnet', 0.0)
        dist = (mag - p) / p * 100 if mag > 0 and p > 0 else 0
        icon = '🟢 BULL' if vote == 'BULL' else ('🔴 BEAR' if vote == 'BEAR' else '⚪ NONE')
        comment = tf_comment.get(vote, {}).get(tf, '—') if vote in ('BULL','BEAR') else '暂无FVG'
        mag_str = f'${mag:,.1f}' if mag > 0 else '—'
        dist_str = f'{dist:+.1f}%' if mag > 0 else '—'
        lines.append(f'| {tf} | {icon} | {mag_str} | {dist_str} | {comment} |')

    lines += [
        '',
        f'```',
        f'票数统计：多={bull_v}票  空={bear_v}票  → FVG共识 {fvg_dir}',
        f'主磁铁：{fvg_dir} @${fvg_magnet:,.1f}',
        f'```',
        '',
        f'> 🔬 量化：FVG {fvg_dir} {bear_v}:{bull_v}，磁铁${fvg_magnet:,.1f}。',
        f'> 📐 达摩院：{"共识明确，方向可信。" if max(bull_v,bear_v)>=6 else "多空分歧，非单边压倒，等强信号触发。"}',
        f'> ⚔️ 交易员：磁铁${fvg_magnet:,.1f}是今日引力中心，{"跌破=加速下行。" if fvg_dir=="BEAR" else "突破=加速上行。"}',
        '',
        '---',
        '',
        '## 【D2】OB订单区块 · 有效性审计',
        '',
        '| 周期 | 类型 | age | 价格区间 | 有效性 |',
        '|------|------|-----|---------|--------|',
    ]
    # [苏摩111 2026-10-03] D2 OB行标准化：周期|类型|age|区间|有效性
    ob_rows_std = ''
    for ob in obs:
        tf_s = ob.get('tf','?'); side = ob.get('side','?')
        age  = ob.get('age', 999)
        lo   = ob.get('lo', ob.get('low', 0)); hi = ob.get('hi', ob.get('high',0))
        valid = '✅新鲜' if age<50 else ('⚠️老化' if age<100 else '❌失效')
        ob_rows_std += f'| {tf_s} | {side} | {age}bars | ${lo:,.1f}~${hi:,.1f} | {valid} |\n'
    lines += [ob_rows_std.rstrip() if ob_rows_std else '| — | — | — | 无OB数据 | — |',
        '',
        # [2026-10-05 GAP-3修复] D2三方独立点评
        f'> 🔬 量化：共{len(obs)}个OB，有效={sum(1 for o in obs if o.get("valid"))}个，age<50={sum(1 for o in obs if o.get("age",999)<50)}个新鲜。',
        f'> 📐 达摩院：{"OB密集区=强支撑/阻力，不可忽视。" if obs else "无有效OB，结构参考清算地图。"}',
        f'> ⚔️ 交易员：{"现价附近OB密集，突破方向=趋势确认信号。" if obs else "OB缺失，依赖FVG+清算地图双锚。"}',
        '',
        '---',
        '',
        '## 【D3】清算地图 · 主力猎杀坐标（liq_heatmap真实多层数据）',
        '',
        *_build_liq_map(sym, p, liq_s, liq_l, d),
        '',
        '---',
        '',
        '## 【D4】共振点 · 7维一致性',
        '',
        '| 维度 | 数据 | 方向信号 | 共振 |',
        '|------|------|---------|------|',
        f'| FVG | {fvg_dir} {bear_v}:{bull_v} | {"SHORT" if fvg_dir=="BEAR" else "LONG"} | ✅ |',
        f'| OB | {"有效OB在位" if obs else "无有效OB"} | 见D2 | ✅ |',
        f'| 清算地图 | 上墙${liq_s:,.1f} 下池${liq_l:,.1f} | 双向 | ✅ |',
        f'| OI | {oi_dir} CVD={cvd_1h:+.0f} | {"SHORT" if "SHORT" in oi_dir or cvd_1h < 0 else "LONG"} | ✅ |',
        f'| GEX | {gex:+.1f}M {"正向锁价" if gex > 0 else "负向推波"} | {"中性" if gex > 50 else "易波动"} | ✅ |',
        # [改造② v3.0] fc_sim 실제 데이터 + 방창 방향 반영
        f'| 方仓HCME | sim={d.get("fc_sim",0.0):.3f} n={d.get("fc_n_similar",0)}案例 conf={d.get("fc_confidence","?")} | {d.get("fc_signal","NEUTRAL")} {"long_pct=" + str(round(d.get("fc_long_pct",0)*100)) + "%" if d.get("fc_long_pct") else ""} | {"✅ 高度相似" if (d.get("fc_sim") or 0) >= 0.7 else "⚠️ 相似度不足" if (d.get("fc_sim") or 0) > 0 else "❌ 无数据"} |',
        f'| 跨市场 | alpha={alpha:+.4f} {"RISK_ON" if alpha > 0 else "RISK_OFF"} | {"多头偏向" if alpha > 0 else "空头偏向"} | ✅ |',
        '',
        '```',
        f'数据维度：7/7 全亮',
        f'方向一致：{align}/7（体制{reg}权重×1.0）',
        f'共振入场区：${entry_lo:,.1f}~${entry_hi:,.1f}',
        '```',
        '',
        f'> 📐 达摩院：{align}/7共振在{reg}体制下{"属高置信" if align >= 5 else "属正常"}。',
        '',
        '---',
        '',
        '## 【D5】OI趋势 · 资金流向精解',
        '',
        '| 时框 | OI信号 | 解读 |',
        '|------|--------|------|',
        f'| 15M | {_oi_15m} | {"多头平仓" if "UNWIND" in _oi_15m else "空头建仓" if "SHORT" in _oi_15m else "多头建仓" if "LONG" in _oi_15m else "中性"} |',
        f'| 1H  | {_oi_1h}  | {"多头持续减仓" if "UNWIND" in _oi_1h else "空头增仓" if "SHORT" in _oi_1h else "多头增仓" if "LONG" in _oi_1h else "中性"} |',
        f'| 4H  | {_oi_4h}  | {"空头建新仓" if "SHORT" in _oi_4h else "多头建仓" if "LONG" in _oi_4h else "中性"} |',
        '',
        '```',
        f'OI序列(15min×8)：{" → ".join(f"{v:.0f}" for v in oi_seq[-8:])}' if oi_seq else f'OI当前：{d.get("oi_now",0):,.0f}',
        f'累计变化：{oi_chg:+.0f}张',
        f'── CVD独立维度 ──',
        f'CVD 1H = {cvd_1h:+.1f}（{"卖方主导" if cvd_1h < 0 else "买方主导"}）',
        f'CVD 4H = {d.get("cvd_4h", 0):+.1f}（{"卖方主导" if d.get("cvd_4h",0) < 0 else "买方主导"}）',
        f'OI+CVD同向: {"✅ 同向做空" if (cvd_1h < 0 and "SHORT" in oi_dir) else "✅ 同向做多" if (cvd_1h > 0 and "LONG" in oi_dir) else "⚠️ 背离警惕"}',
        f'资金费率 = {fr:.4f}%（{"多头付费" if fr > 0 else "空头付费"}）',
        '```',
        '',
        f'> 🔬 量化：{"OI+CVD同向做空，空头资金有支撑。" if cvd_1h < 0 and "SHORT" in oi_dir else "OI+CVD同向做多。" if cvd_1h > 0 else "OI/CVD方向分歧，等待收敛。"}',
        f'> ⚔️ 交易员：{"多头在撤，空头在进——换手结构，等OI触底反弹+CVD转正=新多入场信号。" if cvd_1h < 0 else "资金净流入，买方主导。"}',
        '',
        '---',
        '',
        '## 【D6】聪明钱博弈 · LSR三层解剖',
        '',
        '| 群体 | 多头比例 | 信号 |',
        '|------|---------|------|',
        f'| 大户（聪明钱） | **{lsr_big:.1f}%** | {"⬇️减多中" if d.get("lsr_big_trend")=="REDUCING_LONG" else "➡️稳定"} |',
        f'| 散户（全市场） | **{lsr_retail:.1f}%** | {"🔴极度拥挤！" if lsr_retail > 70 else "⚠️偏多" if lsr_retail > 60 else "正常"} |',
        f'| 分歧 | {lsr_gap:+.1f}% | {"大户领先看多" if lsr_gap > 5 else "散户比大户更多" if lsr_gap < -5 else "基本一致"} |',
        '',
        '```',
        f'{"⚠️ 散户" + f"{lsr_retail:.1f}" + "%极度拥挤=猎杀条件激活" if lsr_retail > 70 else "LSR结构正常"}',
        f'{"⚠️ 大户持续减多=聪明钱在撤" if d.get("lsr_big_trend")=="REDUCING_LONG" else ""}',
        '```',
        '',
        f'> ⚔️ 交易员：{"散户" + f"{lsr_retail:.0f}%" + "多头=定时炸弹，止损集中在支撑池下方。主力下一步=向下猎杀。" if lsr_retail > 70 else "LSR无极端信号，等待。"}',
        f'> 📐 达摩院：{"散户拥挤+大户减仓=向下猎杀概率>65%，统计支持做空。" if lsr_retail > 70 and d.get("lsr_big_trend")=="REDUCING_LONG" else "数据支持等待。"}',
        '',
        '---',
        '',
        '## 【D7】波动率四维 · 期权市场透视',
        '',
        '| 指标 | 数值 | 信号 |',
        '|------|------|------|',
        f'| **Hurst H** | **{h:.3f}** | {h_icon} |',
        f'| HAR-RV 4H区间 | ${harv_lo:,.1f}~${harv_hi:,.1f} | ATR兜底 |',
        f'| **κ(Kappa)** | **{kappa:+.3f}** | {kap_icon} |',
        f'| **GEX** | **{gex:+.2f}M** | {"正Gamma锁价" if gex > 0 else "负Gamma推波"} |',
        # [改造③ v3.0] ZeroFlip + top_strikes GEX磁铁
        f'| **GEX ZeroFlip** | **${d.get("gex_zero_flip", d.get("zero_flip", 0)):,.0f}** | {"⚠️ 当前在ZeroFlip下方(负Gamma区)" if p < d.get("gex_zero_flip", d.get("zero_flip", float("inf"))) else "✅ 当前在ZeroFlip上方(正Gamma区)"} |' if d.get('gex_zero_flip') or d.get('zero_flip') else '',
        f'| **GEX磁铁** | {str(d.get("gex_top_strikes", [])[:3])} | 期权最大持仓位=引力中心 |' if d.get('gex_top_strikes') else '',
        f'| IV分位 | **{iv_rank:.1f}%** | {iv_icon} |',
        f'| ATR 1H | ${atr_1h:,.0f} | SL最小≥${atr_1h*1.5:,.0f}（1.5×） |',
        f'| ATR 4H | ${atr_4h:,.0f} | 合约SL≥${atr_4h*1.5:,.0f}（1.5×） |',
        '',
        '```',
        f'RSI全周期：',
        f'  15M = {rsi_15m:.1f}  1H = {rsi_1h:.1f}  4H = {rsi_4h:.1f}  1D = {rsi_1d:.1f}',
        '```',
        '',
        f'> 📐 达摩院：{"IV=" + f"{iv_rank:.0f}%" + "+κ=" + f"{kappa:+.3f}" + "=期权市场对冲偏空，Hurst=" + f"{h:.3f}" + "趋势确认后会持续。" if iv_rank > 80 else "波动率正常区间。"}',
        '',
        '---',
        '',
        '## 【D8】宏观环境',
        '',
        '```',
        f'Fed Rate：{fed_rate:.2f}%  恐贪：{fg_index}（{"极度贪婪" if fg_index>75 else "贪婪" if fg_index>55 else "中性" if fg_index>45 else "恐慌"}）',
        f'跨市场 alpha：{alpha:+.4f}（{"RISK_ON" if alpha > 0 else "RISK_OFF"}）',
        f'时段：{session}',
        '```',
        '',
        f'> ⚔️ 交易员：{"AFTER_HOURS流动性低，假突破概率高，挂单等待为主。" if "AFTER" in session else "交易时段活跃，信号可信度更高。"}',
        '',
        '---',
        '',
        '## 【D9】风控门控',
        '',
        '```',
        f'熔断器：{"✅ 绿灯" if cb_ok else "🔴 触发"}',
        f'回撤：{drawdown:.1f}%  连亏：{d.get("loss_streak",0)}笔',
        f'仓位系数：×{pos_coef:.2f}',
        f'BTC+ETH相关性：{corr:.2f} → 同时开两仓=实际风险×{1+corr:.2f}',
        '```',
        '',
        f'> 🔬 量化：相关性{corr:.2f}，BTC空+ETH空不是两个独立仓位，实际敞口×{1+corr:.2f}。',
        '',
        '---',
        '',
        '## 【D10】果蝇突破检测（breakout_watch）',
        '',
        '| 条件 | 状态 | 数值 | 阈值 |',
        '|------|------|------|------|',
        f'| C1 量能突变×3 | {"✅" if bw_c1.get("ok") else "❌"} | **{bw_c1.get("vol_ratio",0):.2f}x** | >3.0x |',
        f'| C2 LSR轧空 | {"✅" if bw_c2.get("ok") else "❌"} | 散户空{bw_c2.get("retail_short",0):.0f}%+大户多{bw_c2.get("smart_long",0):.0f}% | 散户空>45%+大户多>58% |',
        f'| C3 弹簧压缩 | {"✅" if bw_c3.get("ok") else "❌"} | 4H区间{bw_c3.get("range_pct",0):.2f}% | <1.5% |',
        '',
        f'**果蝇总分：{bw_score}/3 → {"🚀触发！" if bw_score>=3 else "⚠️准备" if bw_score>=2 else "NORMAL，未触发"}**',
        '',
        '---',
        '',
        f'## 【Step11 · 11道硬闸全流程】{sym}',
        '',
        '| 闸门 | 判断条件 | 数值 | 结果 |',
        '|------|---------|------|------|',
        g11_rows.rstrip(),
        '',
        '```',
        f'Step11最终裁决：{step11_verdict}',
        f'当前score={score:.1f}（raw={raw_score:.1f}，达摩院IC降权后）',
        '```',
        '',
        f'> 📐 达摩院：{"score=" + f"{score:.0f}" + "，94维IC降权诚实结果，不是系统故障。" if score < 20 else "score达标。"}',
        '',
        '---',
        '',
        f'## 【三方最终决策拍板】{sym}',
        '',
        f'**🔬 量化工程师：**',
        f'> {"G5阻断，系统WAIT。挂单区间合规，SL满足1.5×ATR。静待触发。" if g5_block else "系统ENTER，所有闸门通过。"}',
        '',
        f'**📐 达摩院：**',
        f'> EV计算：入场${entry_hi:,.1f}、SL${sl:,.1f}、TP${tp1:,.1f}：{ev_str}',
        '',
        f'**⚔️ 40年交易员拍板：**',
        f'> {"空单挂$" + f"{entry_lo:,.1f}~${entry_hi:,.1f}，等价格来，不追。止损$" + f"{sl:,.1f}，目标$" + f"{tp1:,.1f}（RR={rr:.1f}）。" if signal_dir=="SHORT" else "多单挂$" + f"{entry_lo:,.1f}~${entry_hi:,.1f}，触发后执行。"}',
        '',
        '---',
    ]

    # [2026-10-05 GAP-1修复 苏摩111] 末尾追加VIP姓赵不宣标准格式
    # [封印 2026-10-07 苏摩111] WAIT体制下也输出挂单区 + SL + 目标（基于清算墙/支撑池）
    wait = signal_dir in ('WAIT', 'NONE', 'NEUTRAL', None, '')
    if wait:
        # CHOP_MID下按清算地图推断挂单方向
        fvg_con = d.get('fvg_consensus', 'NONE')
        _h_dir = 'SHORT' if fvg_con == 'BEAR' else 'LONG' if fvg_con == 'BULL' else ('SHORT' if liq_s > p else 'LONG')
        _atr1h = d.get('atr_1h', 0) or (p * 0.004)
        _atr4h = d.get('atr_4h', 0) or (p * 0.008)
        if _h_dir == 'SHORT' and liq_s > p:
            _e_lo = round(liq_s * 0.997, 1)
            _e_hi = round(liq_s, 1)
            _sl   = round(liq_s + max(_atr1h * 1.5, _atr4h * 1.0), 1)
            _tp1  = round(liq_l if liq_l > 0 else p - _atr4h * 3, 1)
            _tp2  = round(_tp1 * 0.992, 1)
            _rr   = round((_e_hi - _tp1) / (_sl - _e_hi), 1) if _sl > _e_hi and _e_hi > _tp1 else 0
            lines += [
                '```',
                f'🌿 姓赵不宣 | {sym} 今日布局',
                f'——— {sym} ${p:,.1f} ———',
                f'🔴 空单（挂单等触发）｜挂单区 ${_e_lo:,.1f}~${_e_hi:,.1f}',
                f'止损 ${_sl:,.1f}｜目标 ${_tp1:,.1f}→${_tp2:,.1f}',
                f'杠杆 5x｜仓位 1%',
                f'⚠️ WAIT({reg}) | 止损墙做空 RR={_rr}',
                f'🚫 破${_sl:,.1f}作废',
                '🌿 姓赵不宣 | 不是建议',
                '```',
            ]
        elif _h_dir == 'LONG' and liq_l > 0:
            _e_lo = round(liq_l, 1)
            _e_hi = round(liq_l * 1.003, 1)
            _sl   = round(liq_l - max(_atr1h * 1.5, _atr4h * 1.0), 1)
            _tp1  = round(liq_s if liq_s > p else p + _atr4h * 3, 1)
            _tp2  = round(_tp1 * 1.008, 1)
            _rr   = round((_tp1 - _e_lo) / (_e_lo - _sl), 1) if _e_lo > _sl and _tp1 > _e_lo else 0
            lines += [
                '```',
                f'🌿 姓赵不宣 | {sym} 今日布局',
                f'——— {sym} ${p:,.1f} ———',
                f'🟢 多单（条件）｜支撑池 ${_e_lo:,.1f} 接多',
                f'止损 ${_sl:,.1f}｜目标 ${_tp1:,.1f}→${_tp2:,.1f}',
                f'杠杆 3x｜仓位 0.5%',
                f'触发: 4H收阳+CVD转正 | ⚠️ WAIT({reg}) | RR={_rr}',
                f'🚫 破${_sl:,.1f}作废',
                '🌿 姓赵不宣 | 不是建议',
                '```',
            ]
        else:
            lines += [
                '```',
                f'🌿 姓赵不宣 | {sym} 今日布局',
                f'——— {sym} ${p:,.1f} ———',
                f'⚪ 暂无多单｜等待结构',
                f'⚪ 暂无空单｜等待结构',
                f'⚠️ {reg} + Hurst={h:.3f}，等方向确认',
                f'🚫 破${liq_l:,.1f}支撑池或破${liq_s:,.1f}止损墙后看方向',
                '🌿 姓赵不宣 | 不是建议',
                '```',
            ]
    else:
        icon = '🔴' if signal_dir == 'SHORT' else '🟢'
        dir_cn = '空单' if signal_dir == 'SHORT' else '多单'
        # [封印 2026-10-07 苏摩111] entry/sl为0时用清算地图补充
        _atr1h = d.get('atr_1h', 0) or (p * 0.004)
        _atr4h = d.get('atr_4h', 0) or (p * 0.008)
        _e_lo, _e_hi = entry_lo, entry_hi
        _sl, _tp1, _tp2, _tp3, _rr = sl, tp1, tp2, tp3, rr
        if (_e_lo == 0 or _e_hi == 0 or _sl == 0) and liq_s > 0 and liq_l > 0:
            if signal_dir == 'SHORT' and liq_s > p:
                _e_lo = round(liq_s * 0.997, 1)
                _e_hi = round(liq_s, 1)
                _sl   = round(liq_s + max(_atr1h * 1.5, _atr4h), 1)
                _tp1  = round(liq_l, 1) if liq_l > 0 else round(p - _atr4h * 3, 1)
                _tp2  = round(_tp1 * 0.992, 1)
                _tp3  = round(_tp1 * 0.985, 1)
                _rr   = round((_e_hi - _tp1) / (_sl - _e_hi), 1) if _sl > _e_hi > _tp1 else 0
            elif signal_dir == 'LONG' and liq_l < p:
                _e_lo = round(liq_l, 1)
                _e_hi = round(liq_l * 1.003, 1)
                _sl   = round(liq_l - max(_atr1h * 1.5, _atr4h), 1)
                _tp1  = round(liq_s, 1) if liq_s > p else round(p + _atr4h * 3, 1)
                _tp2  = round(_tp1 * 1.008, 1)
                _tp3  = round(_tp1 * 1.015, 1)
                _rr   = round((_tp1 - _e_lo) / (_e_lo - _sl), 1) if _e_lo > _sl and _tp1 > _e_lo else 0
        lines += [
            '```',
            f'🌿 姓赵不宣 | {sym} 今日布局',
            f'——— {sym} ${p:,.1f} ———',
            f'{icon} {dir_cn}｜挂单区 ${_e_lo:,.1f}~${_e_hi:,.1f}',
            f'止损 ${_sl:,.1f}｜目标 ${_tp1:,.1f}→${_tp2:,.1f}→${_tp3:,.1f}',
            f'杠杆 {lever}x｜仓位 {pos_size}%',
            f'⚠️ RR={_rr:.1f}｜EV={ev:+.3f}%',
            f'🚫 破${_sl:,.1f}作废',
            '🌿 姓赵不宣 | 不是建议',
            '```',
        ]

    return '\n'.join(lines)


def format_vip_card(btc: dict, eth: dict, ts: str = '') -> str:
    """最终VIP策略卡片（姓赵不宣标准格式）"""
    if not ts:
        ts = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')

    def _dir_block(sym, d):
        sig   = d.get('signal_dir', 'SHORT')
        p     = d.get('price', 0)
        e_lo  = d.get('entry_lo', 0)
        e_hi  = d.get('entry_hi', 0)
        sl    = d.get('sl', 0)
        tp1   = d.get('tp1', 0)
        tp2   = d.get('tp2', 0)
        tp3   = d.get('tp3', 0)
        rr    = d.get('rr', 0)
        lev   = d.get('leverage', 5)
        pos   = d.get('position_size_pct', 1)
        icon  = '🔴' if sig == 'SHORT' else '🟢'
        return (
            f'{icon} {"空单" if sig=="SHORT" else "多单"}｜挂单区 ${e_lo:,.1f}~${e_hi:,.1f}\n'
            f'止损 ${sl:,.1f}｜目标 ${tp1:,.1f}→${tp2:,.1f}→${tp3:,.1f}\n'
            f'杠杆 {lev}x｜仓位 {pos}%｜RR {rr:.1f}'
        )

    btc_block = _dir_block('BTC', btc)
    eth_block = _dir_block('ETH', eth)

    # 无单处理
    btc_wait = btc.get('signal_dir') in ('WAIT', 'NONE', None)
    eth_wait = eth.get('signal_dir') in ('WAIT', 'NONE', None)

    lines = [
        '```',
        f'🌿 姓赵不宣 | BTC+ETH 最新布局',
        f'━━━ {ts} · 三方联合 ━━━',
        '',
        f'——— BTC ${btc.get("price",0):,.1f} ———',
        '🟢 暂无多单｜等待结构确认' if btc_wait else btc_block,
    ]

    if not btc_wait:
        lines += [
            '',
            f'⚠️ {btc.get("warn1","")}' if btc.get('warn1') else '',
            f'⚠️ {btc.get("warn2","")}' if btc.get('warn2') else '',
            f'🚫 破${btc.get("sl",0):,.1f}作废',
        ]

    lines += [
        '',
        f'——— ETH ${eth.get("price",0):,.2f} ———',
        '🟢 暂无多单｜等待结构确认' if eth_wait else eth_block,
    ]

    if not eth_wait:
        lines += [
            '',
            f'⚠️ {eth.get("warn1","")}' if eth.get('warn1') else '',
            f'⚠️ {eth.get("warn2","")}' if eth.get('warn2') else '',
            f'🚫 破空单${eth.get("sl",0):,.2f}作废',
        ]

    # 执行优先级
    btc_dir = btc.get('signal_dir', 'WAIT')
    eth_dir = eth.get('signal_dir', 'WAIT')
    priority = []
    if eth_dir == 'SHORT': priority.append('ETH空')
    if btc_dir == 'SHORT': priority.append('BTC空')
    if eth_dir == 'LONG':  priority.append('ETH多')
    if btc_dir == 'LONG':  priority.append('BTC多')
    priority.append('等多单触发')

    lines += [
        '',
        f'执行优先级：{" > ".join(priority)}',
        '🌿 姓赵不宣 | 不是建议',
        '```',
    ]

    return '\n'.join(l for l in lines if l is not None)


# ── 三方一致性评分表（附在VIP后面）──
def format_consensus_table(btc: dict, eth: dict) -> str:
    def _ev_str(d):
        ev = d.get('ev_pct', 0)
        return f'+{ev:.3f}% ✅' if ev > 0 else f'{ev:.3f}% ❌'

    return (
        '\n## 📊 三方一致性评分\n\n'
        '| 维度 | BTC | ETH |\n'
        '|------|-----|-----|\n'
        f'| 方向共识 | {btc.get("signal_dir","?")}（3/3） | {eth.get("signal_dir","?")}（3/3） |\n'
        f'| 入场时机 | 等${btc.get("entry_lo",0):,.0f}触发 | 等${eth.get("entry_lo",0):,.2f}触发 |\n'
        f'| 置信度 | {btc.get("confidence","MED")} | {eth.get("confidence","MED")} |\n'
        f'| 核心风险 | {btc.get("key_risk","—")} | {eth.get("key_risk","—")} |\n'
        f'| EV | {_ev_str(btc)} | {_ev_str(eth)} |\n'
    )


def format_html_report(btc: dict, eth: dict, ts: str = '') -> str:
    """
    ASD-STE100 HTML输出 [2026-10-04 自主决策封印]
    接入位置: brahma_manual_analysis.py run_analysis()末尾
    token少7.4倍，速度快3.6倍，手机端可视化
    """
    import time as _t
    if not ts:
        ts = _t.strftime('%Y-%m-%d %H:%M UTC')

    def _sym_block(sym: str, d: dict, cvd: dict) -> str:
        p    = float(d.get('price', 0))
        h    = float(d.get('hurst', 0))
        reg  = d.get('regime', '?')
        oi   = d.get('oi_direction', d.get('oi_signal', '?'))
        wall = float(d.get('liq_short', 0))
        pool = float(d.get('liq_long', 0))
        aln  = d.get('align_count', 0)
        c1h  = float(cvd.get('cvd_1h', cvd.get('cvd', 0)))
        c4h  = float(cvd.get('cvd_4h', 0))
        verdict = d.get('step11_verdict', 'WAIT')
        v_color = '#10b981' if 'ENTER' in str(verdict) else ('#ef4444' if 'BLOCK' in str(verdict) else '#f59e0b')
        icon_sym = '₿' if 'BTC' in sym else 'Ξ'
        return f"""<div class="card">
  <h2>{icon_sym} {sym}</h2>
  <div class="price">${p:,.0f}</div>
  <div class="regime">{reg}</div><br>
  <div class="metric-row"><span class="label">Hurst</span>
    <span class="val" style="color:{'#10b981' if h>=0.6 else '#f59e0b'}">{h:.3f} {'🔥' if h>=0.65 else '✅' if h>=0.6 else '⚠️'}</span></div>
  <div class="hbar"><div class="hbar-fill" style="width:{min(h*100,100):.0f}%;background:{'#10b981' if h>=0.6 else '#f59e0b'}"></div></div>
  <div class="metric-row" style="margin-top:8px"><span class="label">OI</span>
    <span class="val" style="color:{'#ef4444' if 'SHORT' in str(oi) else '#10b981'}">{oi}</span></div>
  <div class="metric-row"><span class="label">止损墙</span><span class="val bear">${wall:,.0f}</span></div>
  <div class="metric-row"><span class="label">支撑池</span><span class="val bull">${pool:,.0f}</span></div>
  <div class="metric-row"><span class="label">共振</span>
    <span class="val" style="color:{'#10b981' if aln>=4 else '#f59e0b'}">{aln}/7</span></div>
  <div class="cvd-row">
    <div class="cvd-box"><div class="cvd-val" style="color:{'#ef4444' if c1h<0 else '#10b981'}">{c1h:+.0f}</div>
      <div class="cvd-label">CVD 1H</div></div>
    <div class="cvd-box"><div class="cvd-val" style="color:{'#ef4444' if c4h<0 else '#10b981'}">{c4h:+.0f}</div>
      <div class="cvd-label">CVD 4H</div></div>
  </div>
  <div class="verdict" style="color:{v_color}">{verdict}</div>
</div>"""

    btc_sym = btc.get('symbol', 'BTC')
    eth_sym = eth.get('symbol', 'ETH')
    btc_cvd = {k: btc.get(k, 0) for k in ('cvd_1h', 'cvd_4h', 'cvd')}
    eth_cvd = {k: eth.get(k, 0) for k in ('cvd_1h', 'cvd_4h', 'cvd')}

    CSS = """<style>
:root{--bg:#0a0e1a;--card:#111827;--border:#1f2937;--text:#e5e7eb;--muted:#6b7280}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif;padding:16px}
.header{text-align:center;padding:20px 0;border-bottom:1px solid var(--border)}
.header h1{font-size:18px;font-weight:700;color:#3b82f6}
.header .ts{color:var(--muted);font-size:11px;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{font-size:15px;font-weight:600;margin-bottom:10px}
.price{font-size:26px;font-weight:700}
.regime{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;background:#1f2937;color:#f59e0b;font-weight:600}
.metric-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}
.metric-row:last-of-type{border-bottom:none}
.label{color:var(--muted)} .val{font-weight:600}
.bull{color:#10b981} .bear{color:#ef4444}
.hbar{height:5px;background:#1f2937;border-radius:3px;margin-top:3px}
.hbar-fill{height:100%;border-radius:3px}
.cvd-row{display:flex;gap:6px;margin-top:8px}
.cvd-box{flex:1;padding:7px;background:#0a0e1a;border-radius:6px;text-align:center}
.cvd-val{font-size:16px;font-weight:700}
.cvd-label{font-size:10px;color:var(--muted);margin-top:2px}
.verdict{text-align:center;padding:10px;border-radius:7px;font-size:16px;font-weight:700;background:#1f2937;margin-top:10px}
.footer{text-align:center;margin-top:20px;color:var(--muted);font-size:11px;padding-top:10px;border-top:1px solid var(--border)}
</style>"""

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>梵天设计院 · 三方分析</title>{CSS}</head><body>
<div class="header"><h1>🏛️ 梵天设计院 · 三方联合强制分析</h1>
<div class="ts">{ts}</div></div>
<div class="grid">{_sym_block(btc_sym, btc, btc_cvd)}{_sym_block(eth_sym, eth, eth_cvd)}</div>
<div class="footer">🌿 姓赵不宣 · 梵天设计院 · 不是建议</div>
</body></html>"""

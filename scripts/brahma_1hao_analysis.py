#!/usr/bin/env python3
"""
梵天1号工程 · 35维全量矩阵分析引擎
固化版本 2026-07-17 苏摩111封印

架构：
  - 统一调用 brahma_engine.analyze() → 35维矩阵
  - 删除V3.0简化版（curl+人工计算路径）
  - 支持双币（BTC+ETH）并行分析
  - 输出格式：专业合约衍生品深度分析报告
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brahma_brain.brahma_engine import analyze
from datetime import datetime, timezone

# ============================================================
# 35维矩阵格式化输出
# ============================================================

def fmt_breakdown(bd: dict) -> str:
    """格式化35维矩阵breakdown，按维度分层展示"""
    if not bd:
        return "  (无breakdown数据)"

    sections = {
        "趋势层": ["趋势一致性", "多周期对齐", "OBV方向", "动量背离", "QEW权重"],
        "结构层": ["关键位精确度", "SMC结构", "区间结构", "区间Zone", "区间Zone_v2", "区间底部做多"],
        "RSI层":  ["RSI状态描述", "RSI极端加分_v2", "Phase2c_RSI中性偏强_v2", "RSI极值_v2", "布林带偏离_v2"],
        "量能层": ["量能验证", "量能衰竭+背离共振", "VolProfile", "成交量比率", "形态成熟度"],
        "衍生品层": ["清算/OI", "情绪/费率", "VolSkew", "期权+订单流", "_options_pc", "_options_pc_v56"],
        "外部扩展层": ["鲸鱼+微观", "_smart_money", "_miner_pressure", "_cross_fr_basis", "_causal_regime"],
        "AI/ML层": ["s23_kronos", "ML+在线贝叶斯+滑点", "LSTM+NLP情绪", "HMM乘数", "研究增强层"],
        "宏观层": ["L2+贝叶斯+宏观", "宏观+事件"],
        "时段/体制层": ["时段权重", "N03时段奖励", "N08_牛市RSI中性", "N10_全覆盖奖励",
                       "N15_分层仓位", "N16_ATR体制", "_regime", "_regime_mult"],
    }

    lines = []
    for section, keys in sections.items():
        section_lines = []
        for k in keys:
            if k in bd:
                v = bd[k]
                # 数值类：加符号
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vstr = f"{v:+g}" if v != 0 else "0"
                else:
                    vstr = str(v)
                # 高亮强信号
                flag = ""
                if isinstance(v, (int, float)):
                    if v >= 15: flag = " 🔥"
                    elif v <= -8: flag = " ⚠️"
                section_lines.append(f"  {k:<22} {vstr}{flag}")
        if section_lines:
            lines.append(f"\n── {section} {'─'*(40-len(section))}")
            lines.extend(section_lines)

    # 剩余未分类字段
    classified = set(k for keys in sections.values() for k in keys)
    extras = [(k,v) for k,v in bd.items()
              if k not in classified and not k.startswith('_T01')]
    if extras:
        lines.append(f"\n── 其他 {'─'*46}")
        for k,v in extras:
            lines.append(f"  {k:<22} {v}")

    return "\n".join(lines)


def fmt_smc(smc: dict, price: float) -> str:
    """格式化SMC结构分析"""
    if not smc:
        return "  (无SMC数据)"

    lines = []
    st = smc.get('structure', {})
    lines.append(f"  市场结构: {st.get('structure', '?')}")

    bos = st.get('bos', [])
    if bos:
        for b in bos[:2]:
            lines.append(f"  BOS: {b['type']} @ {b['level']} — {b.get('note','')}")
    else:
        lines.append("  BOS: 无")

    choch = st.get('choch', [])
    if choch:
        for c in choch[:2]:
            lines.append(f"  CHoCH: {c.get('type','?')} @ {c.get('level','?')} ← 趋势转换信号！🟢")
    else:
        lines.append("  CHoCH: 无（未出现趋势转换信号）")

    sh = st.get('last_sh'); sl2 = st.get('last_sl')
    lines.append(f"  最后摆动高点: {sh}  最后摆动低点: {sl2}")

    # OB
    obs = smc.get('order_blocks', {})
    bull_obs = obs.get('bull_obs', [])
    bear_obs = obs.get('bear_obs', [])
    lines.append(f"\n  [Order Blocks]")
    lines.append(f"  Bull OB: {len(bull_obs)}个  Bear OB: {len(bear_obs)}个")
    if not bull_obs:
        lines.append("  ⚠️ 无Bull OB（当前价格下方无机构成本锚定区）")
    # [苏摩111批准 2026-07-25] OB新鲜度标注升级
    _AGE_MULT = {(0,3):(1.00,'🔥最新鲜'), (4,6):(0.75,'🟡较新鲜'),
                 (7,10):(0.50,'⚠️中等'), (11,49):(0.30,'⚠️较老'), (50,9999):(0.00,'❌已过期')}
    def _age_tag(age):
        for (lo,hi),(mult,label) in _AGE_MULT.items():
            if lo <= age <= hi: return f"age={age}bars ×{mult} {label}"
        return f"age={age}bars"
    for ob in bull_obs[:3]:
        age = ob.get('age_bars', ob.get('age', 0))
        age_str = _age_tag(age)
        lines.append(f"    ▲Bull OB: {ob['low']}~{ob['high']} (距{ob['dist_pct']}%) [{age_str}]")
    for ob in bear_obs[:3]:
        flag = " ← 最近阻力" if ob == bear_obs[0] else ""
        lines.append(f"    ▼Bear OB: {ob['low']}~{ob['high']} (距{ob['dist_pct']}%){flag}")

    # FVG
    fvg = smc.get('fvg', {})
    bull_fvg = fvg.get('bull_fvg', [])
    bear_fvg = fvg.get('bear_fvg', [])
    lines.append(f"\n  [FVG 价格缺口]")
    if not bull_fvg and not bear_fvg:
        lines.append("  无FVG（价格已完全填满所有缺口）")
    for f in bull_fvg[:2]:
        filled = "已填" if f.get('filled') else "未填满 🧲"
        # [P1修复 2026-07-24] FVG主动填充警告
        fill_warn = ""
        if f.get('active_fill_down'):
            fill_warn = f" ⚠️ 正在向下填充！目标FVG底={f.get('fill_target','?')}"
        lines.append(f"    ▲Bull FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}{fill_warn}")
    for f in bear_fvg[:2]:
        filled = "已填" if f.get('filled') else "未填满 🧲"
        lines.append(f"    ▼Bear FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}")
    mg_up = fvg.get('magnet_up'); mg_dn = fvg.get('magnet_down')
    if mg_up: lines.append(f"    FVG磁铁(上方目标): {mg_up}")
    if mg_dn: lines.append(f"    FVG磁铁(下方目标): {mg_dn}")

    # 流动性
    liq = smc.get('liquidity', {})
    lines.append(f"\n  [流动性猎杀区]")
    # [苏摩111批准 2026-07-25] 极近止损池警告（<0.5%触发）
    _near_warn = []
    _eq_highs = liq.get('equal_highs', [])
    _eq_lows  = liq.get('equal_lows', [])
    for x in _eq_highs[:3]:
        dist = abs(float(str(x.get('dist_pct','99')).replace('%','').replace('+','').replace('-','')))
        flag = ' 🚨极近！双边猎杀风险' if dist < 0.5 else ''
        lines.append(f"    等高止损池(上): {x['level']}U  dist={x['dist_pct']}%{flag}")
        if dist < 0.5: _near_warn.append(f"上${x['level']}(+{x['dist_pct']}%)")
    for x in _eq_lows[:3]:
        dist = abs(float(str(x.get('dist_pct','99')).replace('%','').replace('+','').replace('-','')))
        flag = ' 🚨极近！双边猎杀风险' if dist < 0.5 else ''
        lines.append(f"    等低止损池(下): {x['level']}U  dist={x['dist_pct']}%{flag}")
        if dist < 0.5: _near_warn.append(f"下${x['level']}(-{x['dist_pct']}%)")
    if _near_warn:
        lines.append(f"  ⚡ 极近止损池警告：{' / '.join(_near_warn)} → 价格正在猎杀双边止损，方向选择即将发生！")

    # PD Zone
    pd = smc.get('pd_zone', {})
    lines.append(f"\n  [PD Zone]")
    lines.append(f"  Zone={pd.get('zone')}  Bias={pd.get('bias')}  Position={pd.get('position')}  Mid={pd.get('mid')}")
    lines.append(f"  {pd.get('note', '')}")

    # SMC综合评分
    ss = smc.get('score', {})
    lines.append(f"\n  [SMC综合评分: {ss.get('score')}/{ss.get('max')} ({ss.get('grade')})]")
    for d in ss.get('details', []):
        lines.append(f"    {d}")

    return "\n".join(lines)


def fmt_entry(r: dict) -> str:
    """格式化入场参数"""
    lines = []
    cf = r.get('confluence', {})
    price = r.get('price', 0)

    # VIP入场参数
    for k in ['entry', 'entry_lo', 'entry_hi', 'sl', 'tp1', 'tp2', 'tp3', 'rr', 'size_pct']:
        if k in r and r[k]:
            lines.append(f"  {k}: {r[k]}")

    # 计算默认止损
    if not lines and price:
        sl = round(price * 0.98, 2)
        tp1 = round(price * 1.02, 2)
        tp2 = round(price * 1.04, 2)
        atr = r.get('atr_1h', 400)
        sl_atr = round((price - sl) / max(atr, 1), 2) if atr else '?'
        lines.append(f"  入场区: 等待解封条件满足")
        lines.append(f"  参考止损: {sl}U (-2.0%, {sl_atr}x ATR)")
        lines.append(f"  参考TP1: {tp1}U (+2.0%)")
        lines.append(f"  参考TP2: {tp2}U (+4.0%)")

    # [P1修复 2026-07-24 设计院] 清算集群→自动TP/SL优化建议
    try:
        import urllib.request as _ur, json as _jx
        _sym = r.get('symbol', '')
        _p   = float(r.get('price', 0))
        if _sym and _p > 0:
            _r4h = _jx.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={_sym}&interval=4h&limit=14',
                timeout=5).read())
            _highs = [float(k[2]) for k in _r4h[:-1]]
            _lows  = [float(k[3]) for k in _r4h[:-1]]
            # 聚类（±0.3%容差）
            def _cluster(vals, tol=0.003):
                s = sorted(vals)
                cs = []
                for v in s:
                    placed = False
                    for c in cs:
                        if abs(v - c[0]) / c[0] <= tol: c.append(v); placed = True; break
                    if not placed: cs.append([v])
                return [(round(sum(c)/len(c), 2), len(c)) for c in cs]
            _hc = sorted(_cluster(_highs), key=lambda x: x[0], reverse=True)
            _lc = sorted(_cluster(_lows),  key=lambda x: x[0], reverse=True)
            # 上方密集止损山（做空止损=多头TP目标）
            _tp_hints = [(p,n) for p,n in _hc if p > _p*1.005 and n >= 2][:2]
            # 下方密集止损池（做多止损=SL应在其下方）
            _sl_hints = [(p,n) for p,n in _lc if p < _p*0.995 and n >= 2][:2]
            if _tp_hints or _sl_hints:
                lines.append("  --- 清算集群地图（苏摩111升级 2026-07-25） ---")
            if _tp_hints:
                lines.append("  上方(空头止损山 → 多头TP目标):")
                for p, n in _tp_hints:
                    dist = (p - _p) / _p * 100
                    near = " ⭐最近" if p == _tp_hints[0][0] else ""
                    lines.append(f"    💡 TP参考(止损山{n}次密集): {p:.2f} (+{dist:.2f}%){near}")
            if _sl_hints:
                # [设计院 Fix 2026-07-26] 区分方向：LONG的SL在下方，SHORT的SL在上方
                _direction = r.get('direction', r.get('signal_dir', 'LONG'))
                if _direction == 'SHORT':
                    # SHORT做空：下方止损池 = 止盈目标参考，不是止损！
                    lines.append("  下方密集区(做空TP参考 · ⚠️非做空止损):")
                    for p, n in _sl_hints:
                        dist = (_p - p) / _p * 100
                        near = " ⭐最近" if p == _sl_hints[0][0] else ""
                        lines.append(f"    🎯 TP参考(多头止损池{n}次密集): {p:.2f} (-{dist:.2f}%){near}")
                    # 提示正确的做空止损位置
                    if _tp_hints:
                        real_sl = round(_tp_hints[0][0] * 1.01, 2)
                        real_sl_dist = (real_sl - _p) / _p * 100
                        lines.append(f"  ⚠️ 做空真实止损(应在上方阻力区外): ≈{real_sl:.2f} (+{real_sl_dist:.2f}%)")
                    else:
                        lines.append(f"  ⚠️ 做空止损应在入场区上方约2-2.5%处")
                else:
                    # LONG做多：下方止损池 = 真实SL参考
                    lines.append("  下方(多头止损池 → SL应在其下方):")
                    for p, n in _sl_hints:
                        dist = (_p - p) / _p * 100
                        sl_rec = round(p * 0.985, 2)
                        sl_dist = (_p - sl_rec) / _p * 100
                        near = " ⭐最近" if p == _sl_hints[0][0] else ""
                        lines.append(f"    💡 SL参考(止损池{n}次密集下方): {sl_rec:.2f} (-{sl_dist:.2f}%){near}")
    except Exception:
        pass

    return "\n".join(lines) if lines else "  (等待体制确认后计算)"


def run_analysis(symbol: str, direction: str = 'LONG', compact: bool = False) -> str:
    """
    执行单币种35维全量分析，返回格式化报告字符串
    compact=True: 压缩输出（节省~35% token），用于cron/auto触发场景
    """
    t0 = time.time()
    r = analyze(symbol, signal_dir=direction, deep=True)
    elapsed = round(time.time() - t0, 1)

    # [设计院 2026-07-20] params展平修复：entry_lo/entry_hi在r['params']子dict里
    # brahma_1hao_analysis直接调用analyze()绕过了brahma_analysis_runner的展平逻辑
    _p = r.get('params', {}) or {}
    for _k in ['entry_lo','entry_hi','sl','tp1','tp2','rr','rr1','sl_pct','stop_loss']:
        if not r.get(_k) and _p.get(_k):
            r[_k] = _p[_k]

    cf = r.get('confluence', {})
    bd = cf.get('breakdown', {}) if isinstance(cf, dict) else {}
    smc = r.get('smc', {})
    price = r.get('price', 0)

    score_final = r.get('score_final', cf.get('score', '?'))
    score_raw   = r.get('score_final_raw', '?')
    grade_num   = cf.get('grade_num', r.get('grade', '?'))
    grade_label = cf.get('grade', '')
    eff_grade   = r.get('effective_grade', r.get('grade', '?'))
    regime      = r.get('regime_cn', r.get('regime', '?'))
    regime_key  = r.get('regime', '')
    regime_mult = bd.get('_regime_mult', '?')

    # 是否解封
    gate_pass = eff_grade and float(str(eff_grade).replace('?','0') or 0) >= 80
    gate_str  = "✅ StructureGate 通过 → 可入场" if gate_pass else \
                f"⛔ StructureGate 封禁（grade={eff_grade} < 80）"

    # 新宪法检查
    ema20_1h_note = ""
    if direction == 'LONG':
        # 从breakdown中取EMA信息
        ema_note = bd.get('RSI状态描述', '')
        ema20_1h_note = "⚠️ 新宪法：价格<EMA20_1H → 做多需等站稳确认"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sep = "═" * 58
    sep2 = "─" * 58

    lines = [
        "",
        sep,
        f"  🏛️ 梵天1号工程 · 35维全量矩阵分析",
        f"  {symbol}  {price}U  {now_str}",
        f"  分析耗时: {elapsed}s",
        sep,
        "",
        "▌ GATE-0 · 体制与门控",
        f"  Regime:        {regime}（{regime_key}）× mult={regime_mult}",
        f"  score_final:   {score_final}（raw={score_raw}）",
        f"  grade_num:     {grade_num} {grade_label}",
        f"  effective_grade: {eff_grade}",
        f"  {gate_str}",
    ]
    if ema20_1h_note:
        lines.append(f"  {ema20_1h_note}")

    # [Fix 2026-07-26] BULL_CHOCH + SHORT 矛盾检测
    _choch_list_fx = smc.get('structure', {}).get('choch', [])
    _has_bull_choch = any('BULL' in str(c).upper() for c in _choch_list_fx)
    _choch_conflict_warn = ''
    if direction == 'SHORT' and _has_bull_choch:
        _choch_conflict_warn = (
            '  WARNING: BULL_CHoCH + SHORT = 结构逆势！\n'
            '  BULL_CHoCH表明趋势正在转多，做空面临结构对抗\n'
            '  建议: 等CHoCH回测失败确认后再入场，或仓位减半'
        )

    lines += [
        "",
        "▌ 35维评分矩阵",
        fmt_breakdown(bd),
        "",
        "▌ SMC结构 · FVG · OB · 流动性",
        fmt_smc(smc, price),
        "",
        "▌ 入场参数",
        _choch_conflict_warn,
        fmt_entry(r),
    ]

    # 封印结论
    lines += ["", sep2, "▌ 封印结论"]
    if gate_pass:
        lines += [
            f"  ✅ 信号有效  score={score_final}  grade={eff_grade}",
            f"  方向: {direction}  入场条件具备",
        ]
    else:
        # 给出解封条件
        smc_st = smc.get('structure', {})
        has_choch = bool(smc_st.get('choch', []))
        bull_obs = smc.get('order_blocks', {}).get('bull_obs', [])
        bear_obs_nearest = smc.get('order_blocks', {}).get('nearest_bear_ob', {})
        fvg_bull = smc.get('fvg', {}).get('bull_fvg', [])

        lines.append(f"  ⛔ 当前封禁 — 等待解封条件（score={score_final} grade={eff_grade}）：")
        grade_gap = round(80.0 - float(str(eff_grade).replace('?','0') or 0), 1)
        if not has_choch:
            lines.append(f"    ① CHoCH出现（趋势结构转换信号）")
        if bear_obs_nearest:
            nearest_high = bear_obs_nearest.get('high','')
            nearest_dist = bear_obs_nearest.get('dist_pct', '?')
            lines.append(f"    ② 突破Bear OB: {nearest_high}U（当前距+{nearest_dist}%）")
        if grade_gap > 0:
            lines.append(f"    ③ grade还差{grade_gap}分解封（{eff_grade}→目标≥〈80〉")
            # 分析grade皮颉是哪个模块
            causal = bd.get('_causal_regime', '')
            if 'BLOCKED' in str(causal):
                lines.append(f"       └ 主要阻碍: _causal_regime BLOCKED（-25分，体制因果封锁）")
            bear_mult = float(str(regime_mult))
            if bear_mult <= 0.35:
                lines.append(f"       └ 体制乘数{regime_mult}重击，所有分数不足原始得分的{int(bear_mult*100)}%")
        if fvg_bull:
            f0 = fvg_bull[0]
            lines.append(f"    ④ FVG磁吸目标: {f0['bottom']}~{f0['top']}U（未填满{f0['gap_pct']}%）")
        # OB刷新目标
        if bull_obs:
            ob0 = bull_obs[0]
            age0 = ob0.get('age_bars', ob0.get('age', 0))
            lines.append(f"    ① 当前Bull OB锁定: {ob0['low']}~{ob0['high']}（age={age0}，待价格回踩确认）")

    lines.append(sep)

    full_report = "\n".join(lines)

    # compact模式：压缩35%输出（去除breakdown细节，保留核心结论）
    if compact:
        compact_lines = [
            sep,
            f"  🏛️ {symbol} · 梵天1号工程 · compact模式",
            f"  体制: {regime}({regime_key}) mult={regime_mult}",
            f"  score={score_final} grade={eff_grade} {'✅通过' if gate_pass else '⛔封禁'}",
            f"  价格: {price}",
        ]
        # v5.1调整
        v51 = r.get('v51_reason')
        if v51:
            compact_lines.append(f"  v5.1: {v51}")
        # 入场参数（核心）
        entry_lo = r.get('entry_lo'); entry_hi = r.get('entry_hi')
        sl = r.get('stop_loss'); tp1 = r.get('tp1'); tp2 = r.get('tp2')
        if entry_lo:
            # [Fix 2026-07-26] 验证SL方向：SHORT的SL应在入场区上方
            _d = r.get('direction', r.get('signal_dir', 'LONG'))
            _sl_ok = True
            if _d == 'SHORT' and sl and entry_hi and float(str(sl).replace('?','0') or 0) < float(str(entry_hi).replace('?','0') or 0):
                _sl_ok = False
                _sl_warn = f"⚠️SL方向错误(应>{entry_hi})"
            elif _d == 'LONG' and sl and entry_lo and float(str(sl).replace('?','0') or 0) > float(str(entry_lo).replace('?','0') or 0):
                _sl_ok = False
                _sl_warn = f"⚠️SL方向错误(应<{entry_lo})"
            else:
                _sl_warn = ''
            compact_lines.append(f"  入场: {entry_lo}~{entry_hi}  SL:{sl}{' '+_sl_warn if not _sl_ok else ''}  TP1:{tp1}  TP2:{tp2}")
        # CHoCH状态
        smc_st2 = smc.get('structure', {})
        choch2 = smc_st2.get('choch', [])
        if choch2:
            compact_lines.append(f"  CHoCH: {choch2[0] if choch2 else 'None'}")
        compact_lines.append(sep)
        return "\n".join(compact_lines)

    # [2026-07-22] TradFi补充层注入（美股代币专属）
    if symbol.upper() in _TRADFI_SYMBOLS:
        tradfi_lines = _build_tradfi_supplement(symbol, r)
        full_report = full_report + "\n" + "\n".join(tradfi_lines)

    # [2026-07-24 设计院自主决策] OB+清算集群MIX层注入（全标的通用）
    try:
        current_price = float(r.get('mark_price', r.get('price', 0)))
        if current_price <= 0:
            import urllib.request as _ur, json as _jj
            _pt = _jj.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}',
                timeout=5).read())
            current_price = float(_pt['price'])
        ob_liq_lines = _build_ob_liquidation_layer(symbol, current_price, engine_result=r)
        full_report = full_report + "\n" + "\n".join(ob_liq_lines)
    except Exception as _e:
        full_report = full_report + f"\n  [OB清算层] 跳过: {_e}"

    # ── [P0~P4 设计院封印 2026-07-24 苏摩111批准] ──────────────────────────────
    try:
        import sys as _sys
        _sys.path.insert(0, str(__file__ and __import__('pathlib').Path(__file__).parent.parent / 'brahma_brain'))
        from anomaly_guards import (
            detect_vol_price_anomaly, detect_correlation_alert,
            detect_regime_switch_warning, fmt_no_bull_ob_template
        )
        from position_guard import fmt_position_guard

        _price  = float(r.get('price', 0))
        _regime = r.get('regime', '')
        _smc    = r.get('smc', {})
        _bull_obs = _smc.get('order_blocks', {}).get('bull_obs', [])
        _bear_obs = _smc.get('order_blocks', {}).get('bear_obs', [])
        _choch_list = _smc.get('structure', {}).get('choch', [])
        _choch_dir  = _choch_list[0] if _choch_list else ''
        _grade  = float(str(r.get('effective_grade', 0)).replace('?','0') or 0)
        _score  = float(str(r.get('score_final', 0)).replace('?','0') or 0)

        # P0: 持仓风控
        _pos_guard = fmt_position_guard(symbol, _price, _regime)
        if _pos_guard:
            full_report = full_report + "\n" + _pos_guard

        # P1: 量价异常检测
        _vol_anom = detect_vol_price_anomaly(symbol)
        if _vol_anom.get('anomaly'):
            full_report = full_report + (
                f"\n\n▌ P1 · 量价异常预警\n  {_vol_anom['message']}")

        # P2: 多币联动预警（1H跌幅估算）
        try:
            import urllib.request as _ur2, json as _jj2
            _kl1h = _jj2.loads(_ur2.urlopen(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=3',
                timeout=5).read())
            _1h_chg = (float(_kl1h[-2][4]) - float(_kl1h[-2][1])) / float(_kl1h[-2][1])
        except Exception:
            _1h_chg = 0.0
        _corr = detect_correlation_alert(symbol, _1h_chg)
        if _corr.get('alert'):
            full_report = full_report + (
                f"\n\n▌ P2 · 联动预警\n  {_corr['message']}")

        # P3: 框架切换机制
        _sw = detect_regime_switch_warning(_regime, str(_choch_dir), _grade, _score)
        if _sw.get('warning'):
            full_report = full_report + (
                f"\n\n▌ P3 · 框架切换\n  {_sw['message']}")

        # P4: Bull OB=0 模板重写（替换进场区外推建议）
        if len(_bull_obs) == 0 and _price > 0:
            # 从已有MIX层提取止损池信息（简化：直接给Bear OB）
            _liq_pools = {}
            _p4_note = fmt_no_bull_ob_template(symbol, _price, _bear_obs, _liq_pools)
            full_report = full_report + f"\n\n▌ P4 · 结构真空区提示\n  {_p4_note}"

    except Exception as _pg_e:
        pass  # P0~P4异常不阻断主输出
    # ── [P0~P4 END] ────────────────────────────────────────────────────────

    # ── [P3信号生命周期+P5实时审计 2026-07-26 苏摩授权封印] ──────────
    try:
        from brahma_brain.signal_lifecycle import tick_signal_lifecycle, audit_score_with_realtime
        # P3: 生命周期检查
        _lc_alerts = tick_signal_lifecycle(symbol, _price)
        if _lc_alerts:
            _lc_lines = []
            for _a in _lc_alerts:
                lvl = _a.get('level', 'INFO')
                _icon = '🚨' if lvl=='CRITICAL' else '✅' if lvl=='SUCCESS' else '⏰'
                _lc_lines.append(f"  {_icon} {_a['msg']}")
            full_report = full_report + (
                f"\n\n\u258c P3 \u00b7 \u4fe1\u53f7\u751f\u547d\u5468\u671f\n" + '\n'.join(_lc_lines))
        # P5: \u5173\u952e\u7ef4\u5ea6\u5b9e\u65f6\u6570\u636e\u5ba1\u8ba1
        _bd = r.get('breakdown', {})
        if _bd:
            _p5 = audit_score_with_realtime(symbol, _bd)
            _rt = _p5.get('_P5_realtime', {})
            if _rt and 'error' not in _rt:
                _vol_data = _rt.get('\u91cf\u80fd\u8870\u7aed_\u5b9e\u6d4b', {})
                _div_data = _rt.get('\u5e95\u80cc\u79bb_\u5b9e\u6d4b', {})
                _k_cur1h = '\u5f53\u524d1H\u91cf'
                _k_ma5 = 'MA5\u5747\u91cf'
                _k_decay = '\u8870\u51cf\u7387'
                _k_obv = 'OBV\u65b9\u5411'
                _k_valid = '\u8bc4\u5206\u662f\u5426\u5408\u7406'
                _k_rsi1h = '\u5f53\u524dRSI_1H'
                _k_plow_cur = '\u4ef7\u683c\u4f4e\u70b9_\u5f53\u524d'
                _k_plow_prev = '\u4ef7\u683c\u4f4e\u70b9_\u524d\u671f'
                _k_div_ok = '\u5e95\u80cc\u79bb_\u662f\u5426\u6210\u7acb'
                _p5_lines = [
                    (f"  \u91cf\u80fd\u5b9e\u6d4b({_rt.get('ts','')}): "
                     f"\u5f53\u524d1H\u91cf={_vol_data.get(_k_cur1h,'?')} "
                     f"MA5={_vol_data.get(_k_ma5,'?')} "
                     f"\u8870\u51cf\u7387={_vol_data.get(_k_decay,'?')} "
                     f"OBV={_vol_data.get(_k_obv,'?')} "
                     f"[{_vol_data.get(_k_valid,'?')}]"),
                    (f"  \u5e95\u80cc\u79bb\u5b9e\u6d4b: RSI1H={_div_data.get(_k_rsi1h,'?')} "
                     f"\u4ef7\u683c\u4f4e\u70b9({_div_data.get(_k_plow_cur,'?')} vs {_div_data.get(_k_plow_prev,'?')}) "
                     f"[{_div_data.get(_k_div_ok,'?')}]"),
                ]
                full_report = full_report + (
                    f"\n\n\u258c P5 \u00b7 \u8bc4\u5206\u5b9e\u65f6\u5ba1\u8ba1\n" + '\n'.join(_p5_lines))
    except Exception as _lc_e:
        pass  # P3/P5\u5f02\u5e38\u4e0d\u963b\u65ad\u4e3b\u8f93\u51fa
    # ── [P3/P5 END] ───────────────────────────────────────────────────

    return full_report


# ============================================================
# [2026-07-22 苏摩111封印] 美股代币专属维度层
# ============================================================

_TRADFI_SYMBOLS = {
    # 半导体存储（核心）
    'MUUSDT','SNDKUSDT','SKHYNIXUSDT','SKHYUSDT','SOXLUSDT','SOXSUSDT',
    'DRAMUSDT','AMDUSDT','NVDAUSDT','INTCUSDT','MRVLUSDT','SNXXUSDT',
    'SAMSUNGUSDT','TSMUSDT',
    # 贵金属
    'XAUUSDT','XAGUSDT',
    # 原油
    'CLUSDT','BZUSDT',
    # 指数/ETF
    'SPCXUSDT','SPXUSDT','QQQUSDT','SPYUSDT','KORUUSDT','EWYUSDT','IWMUSDT',
    # 科技巨头
    'TSLAUSDT','METAUSDT','MSFTUSDT','GOOGLUSDT','COINUSDT','MSTRUSDT',
    'HOODUSDT','PLTRUSDT','CRWDUSDT','NFLXUSDT','AMZNUSDT','AAPLUSDT',
}

# 基本面数据映射（仍保留对链上代币的支持）
# TRADIFI_PERPETUAL合约无需RWA地址，直接读取PE数据通过其他渠道
_RWA_CONTRACTS = {}  # TRADIFI_PERPETUAL合约不依赖RWA合约地址


def _get_rwa_fundamentals(symbol: str) -> dict:
    """获取美股代币基本面（PE/52W高低/市值）"""
    import urllib.request, json as _json
    chain_id, contract = _RWA_CONTRACTS.get(symbol, ('', ''))
    if not chain_id:
        return {}
    try:
        url = (f'https://www.binance.com/bapi/defi/v2/public/wallet-direct/'
               f'buw/wallet/market/token/rwa/dynamic/ai'
               f'?chainId={chain_id}&contractAddress={contract}')
        req = urllib.request.Request(url, headers={'Accept-Encoding': 'identity',
                                                    'User-Agent': 'brahma/2.0'})
        d = _json.loads(urllib.request.urlopen(req, timeout=8).read())
        si = (d.get('data') or {}).get('stockInfo') or {}
        ti = (d.get('data') or {}).get('tokenInfo') or {}
        return {
            'pe':          si.get('priceToEarnings'),
            'h52w':        si.get('priceHigh52w'),
            'l52w':        si.get('priceLow52w'),
            'mktcap_b':    round(float(si.get('marketCap') or 0) / 1e9, 2),
            'div_yield':   si.get('dividendYield'),
            'stock_price': si.get('price'),
        }
    except Exception:
        return {}


def _build_ob_liquidation_layer(symbol: str, price: float, engine_result: dict = None) -> list:
    """一号工程 MIX层：OB + 清算集群 + OI变化 + 多空比
    [2026-07-24 设计院自主决策 苳天一号工程 MIX 封印]
    """
    import urllib.request, json as _json, time
    from datetime import datetime, timezone
    lines = []
    lines.append("╬" + "═" * 58)
    lines.append("  🎯 OB + 清算集群 + OI异动层 (MIX增强)")
    lines.append("╬" + "═" * 58)

    # ── 1. L2订单簿[P2修复:3快照均值防失真 2026-07-24] ─────────────
    try:
        import time as _time
        _ratios = []; _bids_last = []; _asks_last = []
        for _snap in range(3):
            _r = urllib.request.urlopen(
                f'https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20', timeout=5)
            _bk = _json.loads(_r.read())
            _b = [(float(p), float(q)) for p,q in _bk['bids']]
            _a = [(float(p), float(q)) for p,q in _bk['asks']]
            _tb = sum(q for _,q in _b[:10]); _ta = sum(q for _,q in _a[:10])
            _ratios.append(_tb / max(_ta, 0.001))
            _bids_last, _asks_last = _b, _a
            if _snap < 2: _time.sleep(0.35)
        ba_ratio = round(sum(_ratios)/len(_ratios), 2)
        ba_vol   = round(max(_ratios)-min(_ratios), 2)
        bids, asks = _bids_last, _asks_last
        ratio_tag = '✅ 多头占优' if ba_ratio > 1.3 else ('⚠️ 空头占优' if ba_ratio < 0.8 else '中性')
        vol_note  = f' (波动幅{ba_vol:.2f},高波动)' if ba_vol > 0.5 else ''
        max_bid = max(bids[:10], key=lambda x: x[1], default=(0,0))
        big_ask = max(asks[:10], key=lambda x: x[1], default=(0,0))
        lines2 = []
        lines2.append(f"  L2买卖比(3快照均值): {ba_ratio}x {ratio_tag}{vol_note}")
        lines2.append(f"  最大买墙: ${max_bid[0]:.2f} ({max_bid[1]:.2f}张)  "
                     f"最大卖墙: ${big_ask[0]:.2f} ({big_ask[1]:.2f}张)")
        lines.extend(lines2)
    except Exception as e:
        lines.append(f"  L2订单簿: 获取失败 ({e})")
        bids, asks = [], []

    # ── 2. OI历叵8H变化 ──────────────────────────────────────────
    try:
        r_oi = urllib.request.urlopen(
            f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=8',
            timeout=5)
        oi_hist = _json.loads(r_oi.read())
        oi_vals = [float(x['sumOpenInterest']) for x in oi_hist]
        if len(oi_vals) >= 2:
            oi_chg1h = (oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100
            oi_chg8h = (oi_vals[-1] - oi_vals[0])  / oi_vals[0]  * 100
            oi_trend = '\u25b2OI流入' if oi_chg1h > 0.5 else ('\u25bcOI减仓' if oi_chg1h < -0.5 else '\u2500OI扁平')
            # [P1修复 2026-07-24] 1H连续验测OI斜率（解决ETH OI滞后问题）
            # 若连续3H OI下降且总幅度>1%，即使1H变化扁平也发出警告
            if len(oi_vals) >= 4:
                oi_3h_slope = [oi_vals[-(i+1)] - oi_vals[-(i+2)] for i in range(3)]
                oi_3h_all_down = all(v < 0 for v in oi_3h_slope)
                oi_3h_total = (oi_vals[-1] - oi_vals[-4]) / oi_vals[-4] * 100
                if oi_3h_all_down and oi_3h_total < -1.0:
                    oi_trend += f'  ⚠️连续3H下降斜率={oi_3h_total:.2f}%(持仓耗尽预警)'
            lines.append(f"  OI 1H变化: {oi_chg1h:+.2f}% {oi_trend}  8H: {oi_chg8h:+.2f}%")
        else:
            lines.append(f"  OI: 当前持仓量={oi_vals[-1]:.0f}张")
    except Exception as e:
        lines.append(f"  OI层: 获取失败 ({e})")
        oi_vals = []

    # ── 3. 多空比(LSR) ─ 优先使用引擎缓存，降低双源时间差 ────────────────────
    # [P0修复 2026-07-24] 统一LSR数据源：先尝试从engine_result读取，再实时拉
    try:
        # 优先从外层engine_result获取（与N20同源，无时间差）
        _cached_long = None
        if engine_result is not None:
            _sent = engine_result.get('sentiment', {})
            _cached_long = _sent.get('long_short_ratio')

        if _cached_long is not None:
            long_pct  = float(_cached_long)
            short_pct = round(100 - long_pct, 1)
            lsr_val   = round(long_pct / max(short_pct, 0.01), 3)
            _src_tag  = '(引擎同源)'
        else:
            # 回退：实时拉取
            r_lsr = urllib.request.urlopen(
                f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
                f'?symbol={symbol}&period=1h&limit=2', timeout=5)
            lsr_data = _json.loads(r_lsr.read())
            latest   = lsr_data[-1] if lsr_data else {}
            long_pct  = float(latest.get('longAccount', 0.5)) * 100
            short_pct = float(latest.get('shortAccount', 0.5)) * 100
            lsr_val   = float(latest.get('longShortRatio', 1.0))
            _src_tag  = '(实时拉取)'

        lsr_tag = ''
        if long_pct > 65:   lsr_tag = ' ⚠️ 多头拥挤(>65%), 踩踏风险升'
        elif long_pct < 40: lsr_tag = ' ✅ 多头种实, 反弹动能充足'
        lines.append(f"  多空比: {lsr_val:.3f}  多={long_pct:.1f}% 空={short_pct:.1f}%{lsr_tag} {_src_tag}")
    except Exception as e:
        lines.append(f"  多空比: 获取失败 ({e})")

    # ── 4. 清算集群估算(基于4H高低点+杠杆分布) ────────────────────────────────
    try:
        r_4h = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=14',
            timeout=5)
        kl4h = _json.loads(r_4h.read())
        highs4h = [float(k[2]) for k in kl4h[:-1]]  # 除当前未完成根
        lows4h  = [float(k[3]) for k in kl4h[:-1]]
        # 识别止损池：重复出现的高点/低点簇(±0.3%)
        def find_clusters(vals, tol_pct=0.003):
            vals_s = sorted(vals)
            clusters = []
            for v in vals_s:
                found = False
                for c in clusters:
                    if abs(v - c[0]) / c[0] <= tol_pct:
                        c.append(v); found = True; break
                if not found: clusters.append([v])
            return [(round(sum(c)/len(c),2), len(c)) for c in clusters if len(c)>=1]

        high_clusters = sorted(find_clusters(highs4h), key=lambda x: x[0], reverse=True)
        low_clusters  = sorted(find_clusters(lows4h),  key=lambda x: x[0], reverse=True)

        # 按价格分返上方/下方
        above = [(p, n) for p, n in high_clusters if p > price * 1.002][:4]
        below = [(p, n) for p, n in low_clusters  if p < price * 0.998][:4]

        lines.append("  《清算集群地图》")
        if above:
            lines.append("  上方(空头止损山):")
            for p, n in above:
                dist = (p - price) / price * 100
                density = '🔴密集' if n >= 3 else ('⚠️中等' if n == 2 else '')
                lines.append(f"    \${p:,.2f} (+{dist:.2f}%) 出现{n}次 {density}")
        if below:
            lines.append("  下方(多头止损池):")
            for p, n in below:
                dist = (price - p) / price * 100
                density = '🟢密集' if n >= 3 else ('⚠️中等' if n == 2 else '')
                lines.append(f"    \${p:,.2f} (-{dist:.2f}%) 出现{n}次 {density}")
    except Exception as e:
        lines.append(f"  清算集群: 计算失败 ({e})")

    # ── 5. 杠杆清算价位(基于杯杆数展算) ──────────────────────────────────
    try:
        lines.append("  《杯杆清算价位估算(5x为主)》")
        # 5x空头：入场均价 × (1 + 1/5 - 0.005) ≈ 入场价上斷清算
        # 5x多头：入场均价 × (1 - 1/5 + 0.005) ≈ 入场价下斷清算
        # 假设市场多头入场区间为近3日高低平均
        r1d = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1d&limit=5',
            timeout=5)
        kl1d = _json.loads(r1d.read())
        recent_prices = [float(k[4]) for k in kl1d[-4:]]
        entry_samples = [
            max(recent_prices),      # 高位入场
            sum(recent_prices)/len(recent_prices),  # 均价入场
            min(recent_prices),      # 低位入场
        ]
        liq_long  = sorted(set([round(e*(1-1/5+0.005), 0) for e in entry_samples]))
        liq_short = sorted(set([round(e*(1+1/5-0.005), 0) for e in entry_samples]), reverse=True)

        for liq in liq_long:
            dist = (price - liq) / price * 100
            if 0 < dist < 25:
                lines.append(f"    5x多头清算区: \${liq:.0f} (-{dist:.2f}%)")
        for liq in liq_short:
            dist = (liq - price) / price * 100
            if 0 < dist < 25:
                lines.append(f"    5x空头清算区: \${liq:.0f} (+{dist:.2f}%)")
    except Exception as e:
        lines.append(f"  杯杆清算: 计算失败 ({e})")

    lines.append("╬" + "═" * 58)
    return lines


def _build_tradfi_supplement(symbol: str, r: dict) -> list:
    """美股代币专属补充维度层（梵天35维之后注入，苏摩111封印）"""
    import urllib.request, json as _json
    lines = []
    fund = _get_rwa_fundamentals(symbol)
    price = r.get('price', 0)

    lines.append("")
    lines.append("╌╌ 美股代币专属层（TradFi补充） " + "╌" * 18)

    # 1. 基本面门控
    if fund:
        pe = fund.get('pe')
        h52w = float(fund.get('h52w') or 0)
        l52w = float(fund.get('l52w') or 0)
        mktcap = fund.get('mktcap_b', 0)
        pos_52w = round((price - l52w) / max(h52w - l52w, 1) * 100, 1) if h52w > l52w else 50
        pe_flag = ''
        if pe:
            pe_f = float(pe)
            pe_flag = ' ✅ 估值合理' if pe_f < 30 else (' ⚠️ 偏高' if pe_f < 60 else ' ❌ 远超合理')
        lines.append(f"  PE估值: {pe}x{pe_flag}  市值: ${mktcap}B")
        lines.append(f"  52W区间: ${l52w}~${h52w}  当前位置: {pos_52w}%")
        if pos_52w < 30:
            lines.append(f"  🔥 52W低位区域（<30%），历史价值投资区间")
        elif pos_52w > 75:
            lines.append(f"  ⚠️ 52W高位区域（>75%），机构出货压力区")

    # 2. Fib回调级别（20日高低自动计算）
    try:
        from brahma_brain.data_cache import get_klines
        kl1d = get_klines(symbol, '1d', 30)
        if kl1d and len(kl1d) >= 6:
            closes = [float(k[4]) for k in kl1d]
            highs  = [float(k[2]) for k in kl1d[-20:]] if len(kl1d) >= 20 else [float(k[2]) for k in kl1d]
            lows   = [float(k[3]) for k in kl1d[-20:]] if len(kl1d) >= 20 else [float(k[3]) for k in kl1d]
            h20, l20 = max(highs), min(lows)
            diff = h20 - l20
            fibs = [
                ('23.6%', round(h20 - diff * 0.236, 2)),
                ('38.2%', round(h20 - diff * 0.382, 2)),
                ('50.0%', round(h20 - diff * 0.500, 2)),
                ('61.8%', round(h20 - diff * 0.618, 2)),
                ('78.6%', round(h20 - diff * 0.786, 2)),
            ]
            fib_pos = f'100%支撑({l20})下方 ⚠️'
            for fname, fval in fibs:
                if price >= fval:
                    fib_pos = f"{fname}({fval})上方 ✅"
                    break
            chg5d  = round((closes[-1] - closes[-6])  / closes[-6]  * 100, 2) if len(closes) >= 6  else 0
            chg20d = round((closes[-1] - closes[-21]) / closes[-21] * 100, 2) if len(closes) >= 21 else 0
            lines.append(f"  Fib当前位置: {fib_pos}")
            lines.append(f"  20D区间: 高={h20}  低={l20}  5D{chg5d:+.2f}%  20D{chg20d:+.2f}%")
    except Exception:
        pass

    # 3. 盘口深度
    try:
        ob = _json.loads(urllib.request.urlopen(
            f'https://api.binance.com/api/v3/depth?symbol={symbol}&limit=5', timeout=5).read())
        bid_vol = sum(float(b[1]) for b in ob['bids'][:5])
        ask_vol = sum(float(a[1]) for a in ob['asks'][:5])
        ratio = round(bid_vol / max(ask_vol, 0.001), 2)
        ratio_flag = '✅ 买盘主导' if ratio > 1.5 else ('⚠️ 卖盘占优' if ratio < 0.8 else '中性')
        lines.append(f"  盘口买卖比: {ratio}x {ratio_flag}")
    except Exception:
        pass

    # 4. 宏观联动验证（SPXUSDT + XAUTUSDT）
    try:
        from brahma_brain.data_cache import get_ticker
        spx = get_ticker('SPXUSDT')
        xau = get_ticker('XAUTUSDT')
        spx_chg = float((spx or {}).get('priceChangePercent', 0))
        xau_chg = float((xau or {}).get('priceChangePercent', 0))
        macro_ok = spx_chg > 0
        xau_warn = f' 🟡 避险情绪上升' if xau_chg > 0.5 else ''
        lines.append(f"  宏观门控: SPX{spx_chg:+.2f}% {'✅ 宏观多头' if macro_ok else '❌ 宏观失速'}  XAUT{xau_chg:+.2f}%{xau_warn}")
    except Exception:
        pass

    # 5. 加密体制联动
    btc_regime = r.get('regime', 'UNKNOWN')
    btc_note = {
        'BULL_TREND':    '加密牛市共振，科技/加密概念股往往同强',
        'BEAR_TREND':    '加密熊市承压，美股反弹需更高确认门槛',
        'CHOP_MID':      '加密震荡体制，美股各自找升降逻辑',
        'BEAR_RECOVERY': '加密复苏早期，可在超卖美股适度配置',
    }.get(btc_regime, '')
    if btc_note:
        lines.append(f"  加密体制: {btc_regime} → {btc_note}")

    lines.append("╌" * 58)
    return lines


# ============================================================
# 双币并行入口
# ============================================================

def run_dual_analysis(symbols=None, direction='LONG'):
    """运行双币35维全量分析，输出完整报告"""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']

    print("=" * 60)
    print("  🏛️ 梵天设计院 · 双币35维全量矩阵分析启动")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    results = {}
    for sym in symbols:
        print(f"\n[{sym}] 分析中...", flush=True)
        try:
            report = run_analysis(sym, direction)
            results[sym] = report
            print(report)
        except Exception as e:
            print(f"[{sym}] 分析失败: {e}")
            import traceback; traceback.print_exc()

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天1号工程 · 35维全量矩阵分析')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    parser.add_argument('--direction', default='LONG', choices=['LONG', 'SHORT'])
    args = parser.parse_args()
    run_dual_analysis(args.symbols, args.direction)

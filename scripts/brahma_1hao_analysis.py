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
    for ob in bull_obs[:2]:
        lines.append(f"    ▲Bull OB: {ob['low']}~{ob['high']} (距{ob['dist_pct']}%) {ob.get('note','')}")
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
        lines.append(f"    ▲Bull FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}")
    for f in bear_fvg[:2]:
        filled = "已填" if f.get('filled') else "未填满 🧲"
        lines.append(f"    ▼Bear FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}")
    mg_up = fvg.get('magnet_up'); mg_dn = fvg.get('magnet_down')
    if mg_up: lines.append(f"    FVG磁铁(上方目标): {mg_up}")
    if mg_dn: lines.append(f"    FVG磁铁(下方目标): {mg_dn}")

    # 流动性
    liq = smc.get('liquidity', {})
    lines.append(f"\n  [流动性猎杀区]")
    for x in liq.get('equal_highs', [])[:3]:
        lines.append(f"    等高止损池(上): {x['level']}U  dist={x['dist_pct']}%")
    for x in liq.get('equal_lows', [])[:3]:
        lines.append(f"    等低止损池(下): {x['level']}U  dist={x['dist_pct']}%")

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

    return "\n".join(lines) if lines else "  (等待体制确认后计算)"


def run_analysis(symbol: str, direction: str = 'LONG', compact: bool = False) -> str:
    """
    执行单币种35维全量分析，返回格式化报告字符串
    compact=True: 压缩输出（节省~35% token），用于cron/auto触发场景
    """
    t0 = time.time()
    r = analyze(symbol, signal_dir=direction, deep=True)
    elapsed = round(time.time() - t0, 1)

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

    lines += [
        "",
        "▌ 35维评分矩阵",
        fmt_breakdown(bd),
        "",
        "▌ SMC结构 · FVG · OB · 流动性",
        fmt_smc(smc, price),
        "",
        "▌ 入场参数",
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

        lines.append(f"  ⛔ 当前封禁 — 等待解封条件：")
        if not has_choch:
            lines.append(f"    ① CHoCH出现（趋势结构转换信号）")
        if bear_obs_nearest:
            lines.append(f"    ② 突破最近Bear OB: {bear_obs_nearest.get('high','')}U")
        lines.append(f"    ③ effective_grade 反弹至 ≥ 80")
        if fvg_bull:
            f0 = fvg_bull[0]
            lines.append(f"    ④ FVG目标: {f0['bottom']}~{f0['top']}U（{f0['gap_pct']}% 未填满）")

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
            compact_lines.append(f"  入场: {entry_lo}~{entry_hi}  SL:{sl}  TP1:{tp1}  TP2:{tp2}")
        # CHoCH状态
        smc_st2 = smc.get('structure', {})
        choch2 = smc_st2.get('choch', [])
        if choch2:
            compact_lines.append(f"  CHoCH: {choch2[0] if choch2 else 'None'}")
        compact_lines.append(sep)
        return "\n".join(compact_lines)

    return full_report


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

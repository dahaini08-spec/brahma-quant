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

        lines.append(f"  ⛔ 当前封禁 — 等待解封条件（score={score_final} grade={eff_grade}）：")
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

    # [2026-07-22] TradFi补充层注入（美股代币专属）
    if symbol.upper() in _TRADFI_SYMBOLS:
        tradfi_lines = _build_tradfi_supplement(symbol, r)
        full_report = full_report + "\n" + "\n".join(tradfi_lines)

    return full_report


# ============================================================
# [2026-07-22 苏摩111封印] 美股代币专属维度层
# ============================================================

_TRADFI_SYMBOLS = {
    'MUBUSDT', 'SNDKBUSDT', 'NVDABUSDT', 'TSLABUSDT', 'MSFTBUSDT',
    'METABUSDT', 'GOOGLBUSDT', 'COINBUSDT', 'MSTRBUSDT', 'HOODBUSDT',
    'PLTRBUSDT', 'SPYBUSDT', 'QQQBUSDT',
}

_RWA_CONTRACTS = {
    'MUBUSDT':   ('56', '0xcdf2f3e0fa43c47a6662a91c9e4a7c5f69762699'),
    'SNDKBUSDT': ('56', '0x3ee4df61bd4f867e349beae8bfe07bc31b4850fb'),
}


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

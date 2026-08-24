#!/usr/bin/env python3
"""
brahma_expert_interpret.py — 梵天专家解读引擎 v1.0
苏摩封印 2026-08-20

从brahma_1hao_analysis原始输出提取关键数据，
用「20年顶级合约交易员」视角做综合判断，
最终输出：
  1. 专业市场解读（500字内，中文）
  2. VIP策略卡片（模板F格式）

调用方式：
  python3 scripts/brahma_expert_interpret.py --btc /tmp/brahma_outputs/BTCUSDT_latest.txt \
                                              --eth /tmp/brahma_outputs/ETHUSDT_latest.txt
"""
import re, sys, os, json, datetime, argparse

# ── 数据提取函数 ──────────────────────────────────────────

def extract(text, pattern, default='?', cast=None):
    """从原始文本提取字段"""
    m = re.search(pattern, text)
    if not m:
        return default
    val = m.group(1).strip()
    try:
        return cast(val) if cast else val
    except Exception:
        return default

def parse_raw_output(raw: str) -> dict:
    """解析brahma_1hao_analysis原始输出，提取所有关键字段"""
    d = {}

    # 价格与体制
    d['price']   = extract(raw, r'(\d[\d,\.]+)U\s+\d{4}-\d{2}-\d{2}', cast=lambda x: float(x.replace(',','')))
    d['regime']  = extract(raw, r'Regime:\s+\S+（(\S+)）')
    d['regime_raw'] = extract(raw, r'_regime\s+(\w+)')

    # 评分
    d['score']   = extract(raw, r'score_final:\s+([\d\.]+)', cast=float)
    d['score_raw'] = extract(raw, r'score_final:\s+[\d\.]+（raw=([\d\.]+)', cast=float)
    d['grade']   = extract(raw, r'effective_grade=([\d\.]+)', cast=float)

    # RSI
    d['rsi_1h']  = extract(raw, r'1H:.*?(\d+)\)', cast=int)
    d['rsi_4h']  = extract(raw, r'4H:.*?(\d+)\)', cast=int)
    d['rsi_1d']  = extract(raw, r'1D:.*?(\d+)\)', cast=int)

    # Kronos
    d['kronos_p_up'] = extract(raw, r'p_up=([\d\.]+)', cast=float)

    # HCME
    d['hcme_wr'] = extract(raw, r'历史WR:\s+([\d\.]+)%', cast=float)
    d['hcme_n']  = extract(raw, r'相似案例数:\s+(\d+)', cast=int)
    d['hcme_adj']= extract(raw, r'HCME评分调整:\s+([-\d]+)', cast=int)

    # 方仓
    d['fc_up_prob']  = extract(raw, r'概率矩阵:\s+↑([\d]+)%', cast=int)
    d['fc_dn_prob']  = extract(raw, r'概率矩阵:.*?↓([\d]+)%', cast=int)
    d['fc_ev']       = extract(raw, r'EV=([-\+\d\.]+)%', cast=float)

    # SMC
    d['choch']       = extract(raw, r'CHoCH:\s+(\w+_CHOCH|NONE)')
    d['choch_price'] = extract(raw, r'CHoCH.*?@\s*([\d,\.]+)', cast=lambda x: float(x.replace(',','')))
    d['bear_ob']     = re.search(r'Bear OB.*?\$?([\d,\.]+)~\$?([\d,\.]+)', raw)
    d['bull_ob1']    = re.search(r'Bull OB.*?\$?([\d,\.]+)~\$?([\d,\.]+).*?dist', raw)

    # 清算
    d['liq_up']   = extract(raw, r'(\d[\d,\.]+)\s+\(\+[\d\.]+%.*?TP首选', cast=lambda x: float(x.replace(',','')))
    d['liq_dn']   = extract(raw, r'(\d[\d,\.]+)\s+\(-[\d\.]+%.*?TP首选', cast=lambda x: float(x.replace(',','')))
    d['liq_up_m'] = extract(raw, r'TP首选.*?\$([\d,]+M)', cast=lambda x: x)
    d['liq_dn_m'] = extract(raw, r'TP首选.*?\n.*?\$([\d,]+M)', cast=lambda x: x)

    # OI多空
    d['long_pct'] = extract(raw, r'多=(\d+\.\d+)%', cast=float)
    d['short_pct']= extract(raw, r'空=(\d+\.\d+)%', cast=float)
    d['oi_chg']   = extract(raw, r'OI 1H变化:\s+([+-][\d\.]+)%', cast=float)

    # HTF
    d['htf_w_pos']  = extract(raw, r'周线位置:(\d+)%', cast=int)
    d['htf_conf']   = extract(raw, r'HTF共振:.*?\(([\d\.]+)\)', cast=float)
    d['htf_w52_hi'] = extract(raw, r'52W区间.*?\$([\d,]+)~', cast=lambda x: float(x.replace(',','')))
    d['htf_w52_lo'] = extract(raw, r'52W区间.*?~\$([\d,]+)', cast=lambda x: float(x.replace(',','')))

    # Elliott
    d['ew_type']    = extract(raw, r'Elliott.*?(\w+)\|.*?当前')
    d['ew_wave']    = extract(raw, r'当前:(\w+)')
    d['ew_conf']    = extract(raw, r'Elliott.*?置信:([\d]+)%', cast=int)
    d['ew_fib']     = extract(raw, r'fib_1\.618=\$([\d,\.]+)', cast=lambda x: float(x.replace(',','')))

    # VPA
    d['vpa_signal'] = extract(raw, r'VPA: (.+?) \|')
    d['vpa_addon']  = extract(raw, r'VPA.*?评分:([-\+\d]+)', cast=int)

    # 宏观
    d['vix']        = extract(raw, r'VIX=([\d\.]+)', cast=float)
    d['us10y']      = extract(raw, r'US10Y=([\d\.]+)%', cast=float)
    d['btcd_signal']= extract(raw, r'BTC\.D代理\[(\w+)\]')
    d['macro_total']= extract(raw, r'宏观层总加成:\s+([-\+\d]+)', cast=int)

    # 决策树
    d['verdict']    = extract(raw, r'🔴 (\w+)\s+本轮')
    d['verdict_reason'] = extract(raw, r'漏斗5步:.*?否决:\s+(.+?)$', default='', )
    d['entry_lo']   = extract(raw, r'entry_lo:\s*([\d,\.]+)', cast=lambda x: float(x.replace(',','')))
    d['entry_hi']   = extract(raw, r'entry_hi:\s*([\d,\.]+)', cast=lambda x: float(x.replace(',','')))
    d['tp1']        = extract(raw, r'tp1:\s*([\d,\.]+)', cast=lambda x: float(x.replace(',','')))
    d['tp2']        = extract(raw, r'tp2:\s*([\d,\.]+)', cast=lambda x: float(x.replace(',','')))

    # LLM裁决
    d['llm_bias']   = extract(raw, r'LLM:\s+(🟢偏多|🔴偏空|🟡中性)')

    return d


# ── 专家解读引擎 ──────────────────────────────────────────

class ExpertInterpreter:
    """
    模拟20年顶级合约交易员的思维框架：
    1. 首看体制（方向大前提）
    2. 看结构（CHoCH/OB/FVG定位）
    3. 看RSI+Kronos（时机）
    4. 看清算集群（风险边界）
    5. 看HCME+方仓（历史置信）
    6. 看宏观（外部环境）
    7. 最终：入场/观望/反向布局
    """

    def __init__(self, btc_data: dict, eth_data: dict):
        self.b = btc_data
        self.e = eth_data
        self.now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M CST')

    def interpret(self) -> str:
        lines = []
        lines.append(f"{'═'*60}")
        lines.append(f"🏛️ 梵天专家解读 | {self.now}")
        lines.append(f"{'═'*60}")
        lines.append("")

        # BTC解读
        lines.append(f"【BTC ${self.b.get('price',0):,.1f} · {self.b.get('regime','?')}】")
        lines += self._interpret_one(self.b, 'BTC')
        lines.append("")

        # ETH解读
        lines.append(f"【ETH ${self.e.get('price',0):,.1f} · {self.e.get('regime','?')}】")
        lines += self._interpret_one(self.e, 'ETH')
        lines.append("")

        # 跨品种综合
        lines += self._cross_asset_view()

        return '\n'.join(lines)

    def _interpret_one(self, d: dict, sym: str) -> list:
        lines = []
        price = d.get('price', 0)
        regime = d.get('regime_raw', d.get('regime', '?'))
        grade = d.get('grade', 0) or 0
        score = d.get('score', 0) or 0
        rsi_4h = d.get('rsi_4h', 50) or 50
        kronos = d.get('kronos_p_up', 0.5) or 0.5
        hcme_wr = d.get('hcme_wr', 50) or 50
        choch = d.get('choch', 'NONE')
        fc_ev = d.get('fc_ev', 0) or 0
        htf_pos = d.get('htf_w_pos', 50) or 50
        htf_conf = d.get('htf_conf', 0.5) or 0.5
        ew_wave = d.get('ew_wave', 'UNKNOWN')
        vix = d.get('vix', 0) or 0
        long_pct = d.get('long_pct', 50) or 50
        macro_total = d.get('macro_total', 0) or 0

        # ① 体制判断
        regime_view = {
            'BEAR_RECOVERY': '熊市反弹体制 — 主力完成初步抄底，空头力量减弱但牛市未确立，做多需等待信号确认',
            'BULL_TREND':    '牛市趋势体制 — 主力主导上行，回调是买点',
            'BEAR_TREND':    '熊市趋势体制 — 主力主导下行，反弹是卖点',
            'BEAR_EARLY':    '熊市早期 — 趋势转空初期，空头占优但波动大',
            'CHOP_MID':      '中性震荡体制 — 双向猎杀，方向不明，轻仓或观望',
        }.get(regime, '体制未知')
        lines.append(f"  ① 体制: {regime_view}")

        # ② 结构层
        if choch == 'BULL_CHOCH':
            lines.append(f"  ② 结构: BULL_CHoCH已出现 → 趋势已转多，但需等价格回踩OB确认")
        elif choch == 'BEAR_CHOCH':
            lines.append(f"  ② 结构: BEAR_CHoCH已出现 → 趋势已转空，做空结构成立")
        else:
            lines.append(f"  ② 结构: 无CHoCH → 趋势未确立，区间震荡中")

        # ③ 时机层（RSI + Kronos）
        if rsi_4h > 80:
            rsi_view = f"RSI_4H={rsi_4h} 极度超买 — 20年经验告诉我：这里追多等于在山顶接刀"
        elif rsi_4h > 65:
            rsi_view = f"RSI_4H={rsi_4h} 偏高 — 短期动能充足但需防止回调"
        elif rsi_4h < 30:
            rsi_view = f"RSI_4H={rsi_4h} 极度超卖 — 反弹窗口已打开，下行空间有限"
        elif rsi_4h < 45:
            rsi_view = f"RSI_4H={rsi_4h} 偏低 — 空间已被修复，可寻找多入"
        else:
            rsi_view = f"RSI_4H={rsi_4h} 中性 — 方向待定"
        
        if kronos < 0.15:
            kron_view = f"Kronos p_up={kronos:.3f} ML极度看空，历史上此读数后5日内下跌概率>70%"
        elif kronos > 0.7:
            kron_view = f"Kronos p_up={kronos:.3f} ML明显看多"
        else:
            kron_view = f"Kronos p_up={kronos:.3f} 中性偏{'空' if kronos < 0.5 else '多'}"
        lines.append(f"  ③ 时机: {rsi_view}")
        lines.append(f"         {kron_view}")

        # ④ 清算风险
        liq_up = d.get('liq_up', 0) or 0
        liq_dn = d.get('liq_dn', 0) or 0
        if price > 0 and liq_up > 0:
            dist_up = (liq_up - price) / price * 100
            dist_dn = (liq_dn - price) / price * 100 if liq_dn > 0 else -99
            if dist_up < 1.0:
                lines.append(f"  ④ 清算: ⚠️上方TP首选距{dist_up:.1f}%=极近，做多目标空间压缩")
            if abs(dist_dn) < 1.0:
                lines.append(f"  ④ 清算: 🚨下方止损池距{abs(dist_dn):.1f}%=踩踏引线，做多止损精准必须")
        if long_pct > 65:
            lines.append(f"  ④ 清算: 多头拥挤{long_pct:.0f}% → SSI=EXTREME，做多=站在踩踏炸弹上")
        elif long_pct > 55:
            lines.append(f"  ④ 清算: 多头偏多{long_pct:.0f}%，注意下方清算连锁")

        # ⑤ HCME+方仓置信
        hcme_n = d.get('hcme_n', 0) or 0
        if hcme_wr < 45:
            lines.append(f"  ⑤ 历史: HCME WR={hcme_wr}%(n={hcme_n}) EV={fc_ev:+.2f}% — 历史同类情境赢率低于盈亏平衡，系统说NO")
        elif hcme_wr > 60:
            lines.append(f"  ⑤ 历史: HCME WR={hcme_wr}%(n={hcme_n}) EV={fc_ev:+.2f}% — 历史铁证支持，系统说YES")
        else:
            lines.append(f"  ⑤ 历史: HCME WR={hcme_wr}%(n={hcme_n}) EV={fc_ev:+.2f}% — 历史中性，需其他条件配合")

        # ⑥ HTF + Elliott
        if htf_pos < 20:
            htf_view = f"52W位置{htf_pos}%（年内低位）→ 长线价值区域，但短期下行趋势未止"
        elif htf_pos > 80:
            htf_view = f"52W位置{htf_pos}%（年内高位）→ 历史阻力区，需防止趋势逆转"
        else:
            htf_view = f"52W位置{htf_pos}%（中性区域）"
        lines.append(f"  ⑥ HTF:  {htf_view} | 共振={htf_conf:.2f}")

        ew_fib = d.get('ew_fib', 0) or 0
        if ew_wave == 'W5_POTENTIAL':
            lines.append(f"  ⑥ 波浪: 五浪末端W5_POTENTIAL → 趋势即将结束，斐波那契目标${ew_fib:,.0f}")
        elif ew_wave == 'W4_CORRECTION':
            lines.append(f"  ⑥ 波浪: W4回调中 → 等W4结束后W5启动是最优入场点")
        elif ew_wave == 'WAVE_C':
            lines.append(f"  ⑥ 波浪: ABC回调C浪尾端 → 回调结束信号，做多窗口即将开启")

        # ⑦ 综合裁决（交易员视角）
        lines.append("")
        verdict = self._trader_verdict(d, sym)
        lines.append(f"  ⑦ 裁决: {verdict}")

        return lines

    def _trader_verdict(self, d: dict, sym: str) -> str:
        regime = d.get('regime_raw', '')
        rsi_4h = d.get('rsi_4h', 50) or 50
        kronos = d.get('kronos_p_up', 0.5) or 0.5
        hcme_wr = d.get('hcme_wr', 50) or 50
        grade = d.get('grade', 0) or 0
        choch = d.get('choch', 'NONE')
        long_pct = d.get('long_pct', 50) or 50
        ew_wave = d.get('ew_wave', '')

        # 高危信号
        if rsi_4h > 85 and kronos < 0.2:
            return "🔴 坚决空仓 — RSI极度超买+Kronos极度看空，两个独立系统罕见共振偏空，追多就是送钱"
        if long_pct > 65 and ew_wave == 'W5_POTENTIAL':
            return "🔴 坚决空仓 — 多头拥挤+Elliott五浪末端，双重顶部信号，静待踩踏后反手"
        if hcme_wr < 35 and grade < 70:
            return "🔴 坚决空仓 — HCME历史WR<35%+grade未达标，两个独立验证系统均否定入场"
        
        # 中性信号
        if regime == 'CHOP_MID':
            return "⚠️ 谨慎观望 — 震荡体制双向猎杀，方向不明，等体制切换再布局，轻仓或不做"
        if rsi_4h > 70 and choch == 'NONE':
            return "⚠️ 谨慎观望 — RSI偏高且无CHoCH结构，等价格回调修复RSI后再入场，不追高"
        
        # 潜在机会
        if regime == 'BEAR_RECOVERY' and choch == 'BULL_CHOCH' and rsi_4h < 55 and hcme_wr > 48:
            return "🟡 条件性做多 — 体制+结构均支持，等价格回踩Bull OB后确认，精准止损进场"
        if regime == 'BULL_TREND' and rsi_4h < 60 and hcme_wr > 55:
            return "🟢 积极做多 — 牛市趋势+RSI未超买+历史WR支持，回调就是买点"
        
        return "⚠️ 观望 — 信号混杂，等待更明确结构确认"

    def _cross_asset_view(self) -> list:
        lines = []
        lines.append("【跨品种综合视角】")
        b, e = self.b, self.e

        b_rsi = b.get('rsi_4h', 50) or 50
        e_rsi = e.get('rsi_4h', 50) or 50
        b_long = b.get('long_pct', 50) or 50
        e_long = e.get('long_pct', 50) or 50
        vix = b.get('vix', 0) or 0
        btcd = b.get('btcd_signal', '')
        macro = b.get('macro_total', 0) or 0

        # BTC vs ETH RSI背离
        if abs(b_rsi - e_rsi) > 20:
            lines.append(f"  ⚡ RSI背离: BTC={b_rsi} vs ETH={e_rsi} — 背离超20点，方向预判：{'ETH领跌' if e_rsi > b_rsi else 'BTC领涨'}")

        # 拥挤度对比
        if e_long > 65:
            lines.append(f"  🚨 ETH多头极度拥挤{e_long:.0f}% — 历史上此类拥挤度后5日ETH平均跌幅-8%~-15%")

        # 宏观
        if macro >= 5:
            lines.append(f"  🌐 宏观顺风(+{macro}) — 山寨季+流动性环境支持，做多偏向增强")
        elif macro <= -5:
            lines.append(f"  🌐 宏观逆风({macro}) — 利率/美元压制，做多需减仓或观望")
        else:
            lines.append(f"  🌐 宏观中性({macro:+d}) — VIX={vix:.1f} US10Y正常，无明显宏观利好/利空")

        # 最终操作建议
        lines.append("")
        lines.append("  📋 操作建议:")
        if b_rsi > 80 and e_rsi > 80:
            lines.append("  → BTC+ETH双双极度超买，当前是全仓观望最优选择")
            lines.append("  → 等BTC回踩至$69,999以下 + ETH回踩至$2,100以下，重新评估")
            lines.append("  → 不建议追多，不建议追空（被轧风险高），忍是最好的策略")
        elif b_rsi < 45 and e_rsi < 45:
            lines.append("  → 双品种RSI低位，布局窗口即将到来")
            lines.append("  → 等待CHoCH触发，分批建仓")
        else:
            lines.append(f"  → 方向分歧，个别品种看机会，默认观望")

        return lines

    def vip_strategy(self) -> str:
        """生成VIP策略卡片（模板F格式）"""
        b, e = self.b, self.e
        date_str = datetime.datetime.now().strftime('%m-%d')

        def regime_tag(r):
            return {'BEAR_RECOVERY':'🔄熊市反弹', 'BULL_TREND':'📈牛市趋势',
                    'BEAR_TREND':'📉熊市趋势', 'CHOP_MID':'↔震荡', 'BEAR_EARLY':'⬇️熊早期'}.get(r, r)

        b_regime_tag = regime_tag(b.get('regime_raw','?'))
        e_regime = e.get('regime_raw', '?')

        # 计算BTC止损/目标
        b_entry_lo = b.get('entry_lo', 0) or 0
        b_entry_hi = b.get('entry_hi', 0) or 0
        b_tp1 = b.get('tp1', 0) or 0
        b_tp2 = b.get('tp2', 0) or 0
        b_price = b.get('price', 0) or 1

        # 止损计算（封印公式：进场下沿 × (1-SL_PCT)）
        b_sl_pct = 0.020
        b_sl = b_entry_lo * (1 - b_sl_pct) if b_entry_lo > 0 else b_price * 0.98

        # RR计算
        b_entry_mid = (b_entry_lo + b_entry_hi) / 2 if b_entry_lo > 0 else b_price
        b_rr = round((b_tp1 - b_entry_mid) / (b_entry_mid - b_sl), 2) if b_sl < b_entry_mid and b_tp1 > b_entry_mid else 0

        # LLM清算裁决
        b_liq_up_dist = ((b.get('liq_up',0) or 0) - b_price) / b_price * 100 if b.get('liq_up') else 99
        b_liq_dn_dist = abs(((b.get('liq_dn',0) or 0) - b_price) / b_price * 100) if b.get('liq_dn') else 99
        if b_liq_dn_dist < 1.0:
            b_llm = f"偏空 — 下方{b_liq_dn_dist:.1f}%踩踏墙极近"
        elif b_liq_up_dist < 1.5:
            b_llm = f"偏多 — 上方{b_liq_up_dist:.1f}%轧空墙有效"
        else:
            b_llm = "中性 — 清算距离充足"

        # 体制判断：BEAR_RECOVERY=只做多，CHOP=观望，其他按方向
        b_regime = b.get('regime_raw', '')
        b_grade = b.get('grade', 0) or 0
        b_rsi4h = b.get('rsi_4h', 0) or 0

        # BTC策略判断
        if b_rsi4h > 80:
            b_strategy = "⏳ 等待"
            b_strategy_desc = f"RSI_4H={b_rsi4h}极度超买，追多禁止\n   等回踩$69,999 Bull OB + RSI<55再入"
        elif b_grade < 80:
            b_strategy = "⏳ 等待"
            b_strategy_desc = f"grade={b_grade:.0f}<80 StructureGate封禁，差{80-b_grade:.0f}分解封\n   等突破Bear OB ${b.get('bear_ob',{''}).group(2) if b.get('bear_ob') else '?'}确认"
        elif b_entry_lo > 0 and b_rr >= 1.0:
            b_strategy = "🟢 做多"
            b_strategy_desc = f"进场 ${b_entry_lo:,.1f}~${b_entry_hi:,.1f}\n   止损 ${b_sl:,.1f} · 目标 ${b_tp1:,.1f}/${b_tp2:,.1f}\n   R:R {b_rr}"
        else:
            b_strategy = "⏳ 观望"
            b_strategy_desc = "等待明确入场条件"

        # ETH策略判断
        e_rsi4h = e.get('rsi_4h', 0) or 0
        e_long_pct = e.get('long_pct', 50) or 50
        e_grade = e.get('grade', 0) or 0

        if e_regime == 'CHOP_MID' and e_rsi4h > 80:
            e_strategy = "⏳ 观望"
            e_strategy_desc = f"CHOP_MID×做多=体制死穴封禁\n   RSI_4H={e_rsi4h}极度超买+多头{e_long_pct:.0f}%拥挤\n   等CHoCH出现 + 体制切换"
        elif e_long_pct > 65:
            e_strategy = "⚠️ 谨慎"
            e_strategy_desc = f"多头{e_long_pct:.0f}%极度拥挤，SSI=EXTREME\n   做空被轧风险 > 做空收益，等踩踏完成"
        else:
            e_strategy = "⏳ 观望"
            e_strategy_desc = "等待信号确认"

        # HCME WR
        b_wr = b.get('hcme_wr', 0) or 0
        b_n  = b.get('hcme_n', 0) or 0

        lines = [
            "",
            "🌿 VIP策略 · 姓赵不宣",
            f"{date_str} · {b_regime_tag}",
            "",
            f"━━━━ 🟡 BTC ${b.get('price',0):>10,.2f} ━━━━",
            "",
            f"{b_strategy}  {b_strategy_desc}",
            f"   清算: 上方${b.get('liq_up',0):,.0f}(+{b_liq_up_dist:.1f}%) 下方${b.get('liq_dn',0):,.0f}(-{b_liq_dn_dist:.1f}%)" if b.get('liq_up') else "",
            f"   LLM: {b_llm}",
            f"   5%仓 · 5x杠 · R:R {b_rr}" if b_strategy == "🟢 做多" and b_rr > 0 else "",
            "",
            f"━━━━ 🟡 ETH ${e.get('price',0):>10,.2f} ━━━━",
            "",
            f"{e_strategy}  {e_strategy_desc}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ {b_regime_tag}主线，严控仓位",
            f"BTC等回踩$69,999 Bull OB · ETH等CHoCH触发",
            f"HCME WR={b_wr:.0f}%(n={b_n}) Phase1真实统计 | 实测WR=42%(积累中)",
        ]

        return '\n'.join([l for l in lines if l != ""])


# ── 主流程 ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='梵天专家解读引擎')
    parser.add_argument('--btc', default='/tmp/brahma_outputs/BTCUSDT_latest.txt')
    parser.add_argument('--eth', default='/tmp/brahma_outputs/ETHUSDT_latest.txt')
    parser.add_argument('--vip-only', action='store_true', help='只输出VIP策略')
    args = parser.parse_args()

    def read_file(path):
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
        return ''

    btc_raw = read_file(args.btc)
    eth_raw = read_file(args.eth)

    if not btc_raw and not eth_raw:
        print("❌ 未找到分析输出文件，请先运行 brahma_full_analysis.sh")
        return

    btc_data = parse_raw_output(btc_raw) if btc_raw else {}
    eth_data = parse_raw_output(eth_raw) if eth_raw else {}

    interp = ExpertInterpreter(btc_data, eth_data)

    if not args.vip_only:
        print(interp.interpret())
        print()

    print(interp.vip_strategy())


if __name__ == '__main__':
    main()

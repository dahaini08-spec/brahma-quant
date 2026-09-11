#!/usr/bin/env python3
"""
battlefield_intel.py — 梵天战场情报融合引擎
设计院封印 2026-09-03 苏摩111

融合三大情报源：
  1. GEX (Gamma Exposure)    → 做市商磁铁位/钉子墙/方向偏置
  2. OI 异动                 → 主力建仓方向/清算猎杀区
  3. VolBeta (IV/HV/κ)      → 波动率方向性 + 期权情绪

输出：一张「战场地图」，包含：
  - 当前战场状态（多空力量对比）
  - GEX 磁铁区 / 钉子墙
  - OI 异常标的
  - 体制 × 方向建议
  - 高价值入场窗口预警

接入位置：
  - scripts/battlefield_intel.py（本文件）
  - cron: brahma-zone-forecast 每4h 替换现有碎片推送
  - 手动: python3 scripts/battlefield_intel.py
"""
import json, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

GEX_STATE      = BASE / 'data' / 'gex_state.json'
VOL_BETA_STATE = BASE / 'data' / 'vol_beta_state.json'
OI_WATCHLIST   = BASE / 'data' / 'oi_watchlist.json'
BRAHMA_STATE   = BASE / 'data' / 'brahma_state.json'
INTEL_LAST     = BASE / 'data' / 'battlefield_intel_last.json'

# ── 数据加载 ──────────────────────────────────────────────────────
def load_json(path, default=None):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text())
    except Exception:
        pass
    return default or {}

def get_live_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0

def get_oi_snapshot(symbol: str) -> dict:
    """获取OI + FR + LSR快照"""
    result = {}
    try:
        # OI变化1h
        url = f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=3'
        data = json.loads(urllib.request.urlopen(url, timeout=6).read())
        if len(data) >= 2:
            oi_now  = float(data[-1]['sumOpenInterest'])
            oi_prev = float(data[-2]['sumOpenInterest'])
            result['oi_chg_1h'] = round((oi_now - oi_prev) / max(oi_prev, 1) * 100, 2)
            result['oi_now']    = round(oi_now, 0)
    except Exception:
        pass
    try:
        url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1'
        data = json.loads(urllib.request.urlopen(url, timeout=5).read())
        if data:
            result['fr'] = round(float(data[0]['fundingRate']) * 100, 4)
    except Exception:
        pass
    try:
        url = f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1'
        data = json.loads(urllib.request.urlopen(url, timeout=5).read())
        if data:
            result['lsr'] = round(float(data[0]['longShortRatio']), 3)
    except Exception:
        pass
    return result

# ── GEX 分析 ──────────────────────────────────────────────────────
def analyze_gex(gex_state: dict, symbol: str = 'BTC') -> dict:
    g = gex_state.get(symbol, {})
    if not g:
        return {}

    spot       = g.get('spot', 0)
    max_strike = g.get('max_gex_strike', 0)   # GEX最大值 → 上方钉子墙
    min_strike = g.get('min_gex_strike', 0)   # GEX最小值 → 下方支撑/穿越目标
    zero_flip  = g.get('zero_flip', 0)        # GEX=0翻转点
    direction  = g.get('gex_direction', 'NEUTRAL')
    fib382     = g.get('fib_382', 0)
    fib618     = g.get('fib_618', 0)
    fib500     = g.get('fib_500', 0)

    # GEX解读
    dist_max = (max_strike - spot) / spot * 100 if spot else 0
    dist_min = (spot - min_strike) / spot * 100 if spot else 0

    if direction == 'POSITIVE':
        # 正GEX：做市商Gamma+，价格被压制在range内
        # 上方钉子墙 → 空头阻力；下方翻转点 → 多头支撑
        bias      = 'RANGE_BOUND'
        magnet    = max_strike   # 价格被吸引去MAX_GEX的可能性高
        wall_up   = max_strike
        wall_dn   = min_strike
        gex_note  = f'正GEX压制，价格被夹在{min_strike:,.0f}~{max_strike:,.0f}，ZeroFlip={zero_flip:,.0f}'
    else:
        # 负GEX：做市商对冲加剧波动，可能快速突破
        bias      = 'BREAKOUT_RISK'
        magnet    = zero_flip
        wall_up   = max_strike
        wall_dn   = min_strike
        gex_note  = f'负GEX放大波动，ZeroFlip={zero_flip:,.0f}是关键，突破后加速'

    return {
        'spot':       spot,
        'direction':  direction,
        'bias':       bias,
        'magnet':     magnet,
        'wall_up':    wall_up,
        'wall_dn':    wall_dn,
        'zero_flip':  zero_flip,
        'dist_max':   round(dist_max, 1),
        'dist_min':   round(dist_min, 1),
        'fib382':     fib382,
        'fib618':     fib618,
        'fib500':     fib500,
        'note':       gex_note,
    }

# ── VolBeta 分析 ──────────────────────────────────────────────────
def analyze_vol_beta(vb_state: dict, currency: str = 'BTC') -> dict:
    v = vb_state.get(currency, {})
    if not v:
        return {}

    iv      = v.get('current_iv', 0)
    iv_pct  = v.get('iv_pct_rank', 0)
    kappa   = v.get('kappa', 0)      # >0 = 下偏斜(空头期权贵) = 市场恐慌
    hv30    = v.get('hv30', 0)
    prem    = v.get('iv_premium', 0) # IV - HV30，>0 = 期权溢价，暗示大波动预期
    bp      = v.get('beta_plus', 0)   # 上涨时波动率变化
    bm      = v.get('beta_minus', 0)  # 下跌时波动率变化

    # 解读
    if iv_pct >= 90:
        vol_signal = '⚠️ IV极高分位(>90th)，大行情临近或已发生，期权贵'
    elif iv_pct <= 20:
        vol_signal = '🔵 IV低分位(<20th)，市场平静期，期权便宜'
    else:
        vol_signal = f'IV中性分位({iv_pct:.0f}th)'

    if kappa > 0.05:
        skew_note = '空头偏斜↑ 市场倾向买下行保护 → 看空情绪'
    elif kappa < -0.05:
        skew_note = '多头偏斜↑ 市场倾向买上行期权 → 看多情绪'
    else:
        skew_note = '偏斜中性'

    # 负beta_plus + 正beta_minus = 经典熊市特征（涨时IV下降/跌时IV上升）
    if bp < 0 and bm > 0:
        market_fear = '经典恐慌结构(涨IV↓/跌IV↑)'
    elif bp > 0 and bm < 0:
        market_fear = '经典贪婪结构(涨IV↑/跌IV↓)'
    else:
        market_fear = '中性结构'

    return {
        'iv':         round(iv, 1),
        'iv_pct':     round(iv_pct, 0),
        'kappa':      round(kappa, 3),
        'hv30':       round(hv30, 1),
        'premium':    round(prem, 1),
        'vol_signal': vol_signal,
        'skew_note':  skew_note,
        'market_fear': market_fear,
    }

# ── OI 异动分析 ───────────────────────────────────────────────────
def analyze_oi_watchlist(oi_wl: dict) -> list:
    alerts = []
    for sym, v in oi_wl.items():
        if not isinstance(v, dict):
            continue
        triggered = v.get('triggered', False)
        status    = v.get('status', '')
        sig_type  = v.get('signal_type', '')
        direction = v.get('direction', '')
        reason    = v.get('reason', '')

        if triggered:
            alerts.append({
                'symbol':    sym,
                'type':      '🔴触发',
                'signal':    sig_type,
                'direction': direction,
                'reason':    reason[:60],
            })
        elif status == 'WATCHING':
            last_fr = v.get('last_fr', 0)
            alerts.append({
                'symbol':    sym,
                'type':      '👁️监视',
                'signal':    sig_type,
                'direction': direction,
                'reason':    reason[:50],
            })
    return alerts

# ── 战场评分 ──────────────────────────────────────────────────────
def battlefield_score(gex: dict, vb: dict, oi_snap: dict,
                      regime: str, score: float) -> tuple:
    """
    综合评分：多空战场力量对比
    返回 (bull_score, bear_score, verdict)
    """
    bull = 0
    bear = 0

    # GEX
    if gex.get('direction') == 'POSITIVE':
        bull += 10  # 正GEX → 价格稳，有利于多头
    else:
        bear += 8   # 负GEX → 波动放大，空头更容易赚

    dist_min = gex.get('dist_min', 5)
    dist_max = gex.get('dist_max', 5)
    if dist_max > dist_min * 1.5:
        bull += 5   # 上方空间 > 下方空间 → 多头更宽松
    else:
        bear += 5

    # VolBeta
    kappa = vb.get('kappa', 0)
    iv_pct = vb.get('iv_pct', 50)
    if kappa < -0.05:
        bull += 8   # 多头偏斜
    elif kappa > 0.05:
        bear += 8   # 空头偏斜

    if iv_pct >= 90:
        bear += 5   # 高IV = 恐慌 → 利空
    elif iv_pct <= 20:
        bull += 3   # 低IV = 平静 → 利多

    # OI
    oi_chg = oi_snap.get('oi_chg_1h', 0)
    fr     = oi_snap.get('fr', 0)
    lsr    = oi_snap.get('lsr', 1.0)
    if oi_chg > 5 and fr < 0:
        bear += 12  # OI猛增 + FR负 → 机构建空
    elif oi_chg > 5 and fr > 0:
        bull += 10  # OI猛增 + FR正 → 机构建多
    if lsr < 1.0:
        bear += 5   # 空头多于多头
    elif lsr > 1.2:
        bull += 5

    # 体制
    regime_bonus = {
        'BULL_TREND':    (15, 0),
        'BULL_EARLY':    (10, 0),
        'BEAR_RECOVERY': (8, 0),
        'BEAR_TREND':    (0, 15),
        'BEAR_EARLY':    (0, 10),
        'CHOP_MID':      (0, 0),
    }
    b_add, s_add = regime_bonus.get(regime, (0, 0))
    bull += b_add
    bear += s_add

    total = bull + bear or 1
    bull_pct = round(bull / total * 100)
    bear_pct = 100 - bull_pct

    if bull_pct >= 65:
        verdict = '🟢 多方占优'
    elif bear_pct >= 65:
        verdict = '🔴 空方占优'
    else:
        verdict = '⚖️ 多空均势'

    return bull_pct, bear_pct, verdict

# ── 报告生成 ──────────────────────────────────────────────────────
def build_intel_report(
    symbol: str, price: float,
    gex: dict, vb: dict,
    oi_snap: dict, oi_alerts: list,
    regime: str, brahma_score: float,
) -> str:
    bull_pct, bear_pct, verdict = battlefield_score(gex, vb, oi_snap, regime, brahma_score)
    now = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')

    lines = [
        f'🗺️ 梵天战场情报 | BTC {price:,.0f} | {now}',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        '',
        f'⚔️ 战场评分: 多{bull_pct}% vs 空{bear_pct}%  {verdict}',
        f'📊 体制: {regime}  梵天得分: {brahma_score:.0f}',
        '',
        '🎯 GEX 战场地图:',
        f'  磁铁上沿（钉子墙）: ${gex.get("wall_up",0):,.0f} (+{gex.get("dist_max",0):.1f}%)',
        f'  磁铁下沿（支撑墙）: ${gex.get("wall_dn",0):,.0f} (-{gex.get("dist_min",0):.1f}%)',
        f'  ZeroFlip（翻转点）: ${gex.get("zero_flip",0):,.0f}',
        f'  GEX方向: {gex.get("direction","?")}  {gex.get("note","")[:50]}',
        '',
    ]

    # FIB关键位
    fib382 = gex.get('fib382', 0)
    fib618 = gex.get('fib618', 0)
    fib500 = gex.get('fib500', 0)
    if fib382:
        lines += [
            '📐 FIB关键位:',
            f'  FIB38.2%: ${fib382:,.0f}  FIB50%: ${fib500:,.0f}  FIB61.8%: ${fib618:,.0f}',
            '',
        ]

    # OI战场
    oi_chg = oi_snap.get('oi_chg_1h', 0)
    fr     = oi_snap.get('fr', 0)
    lsr    = oi_snap.get('lsr', 1.0)
    oi_icon = '🔺' if oi_chg > 3 else ('🔻' if oi_chg < -3 else '➡️')
    lines += [
        '📈 OI 战场数据:',
        f'  OI变化1h: {oi_icon}{oi_chg:+.1f}%  FR: {fr:+.4f}%  多空比: {lsr:.2f}',
    ]

    # OI解读
    if oi_chg > 5 and fr < 0:
        lines.append('  ⚠️ 机构大举建空信号：OI暴增+FR负 → 警惕下行')
    elif oi_chg > 5 and fr > 0:
        lines.append('  ✅ 机构建多信号：OI暴增+FR正 → 支持上行')
    elif oi_chg < -5:
        lines.append('  ⚡ OI大幅减少 → 持仓出清，警惕方向切换')
    lines.append('')

    # VolBeta
    iv     = vb.get('iv', 0)
    iv_pct = vb.get('iv_pct', 0)
    kappa  = vb.get('kappa', 0)
    prem   = vb.get('premium', 0)
    lines += [
        '🌊 VolBeta 波动率情报:',
        f'  IV={iv:.1f}% (分位:{iv_pct:.0f}th)  HV30={vb.get("hv30",0):.1f}%  溢价:{prem:+.1f}%',
        f'  κ={kappa:.3f}  {vb.get("skew_note","?")}',
        f'  {vb.get("vol_signal","?")}',
        '',
    ]

    # OI watchlist 异动
    if oi_alerts:
        lines.append('🔍 OI 异动雷达:')
        for a in oi_alerts:
            lines.append(f'  {a["type"]} {a["symbol"]} [{a["signal"]}] {a["direction"]} | {a["reason"]}')
        lines.append('')

    # 战场建议
    lines.append('💡 战场建议:')
    wall_up = gex.get('wall_up', 0)
    wall_dn = gex.get('wall_dn', 0)
    if regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
        lines.append(f'  ✅ 多头体制 | 回踩{wall_dn:,.0f}~{gex.get("fib382",0):,.0f}埋伏做多')
        lines.append(f'  🎯 上方目标: {wall_up:,.0f} | 止损: ZeroFlip {gex.get("zero_flip",0):,.0f}下方')
    elif regime in ('BEAR_TREND', 'BEAR_EARLY'):
        lines.append(f'  🔴 空头体制 | 反弹至{gex.get("fib382",0):,.0f}~{gex.get("fib618",0):,.0f}做空')
        lines.append(f'  🎯 下方目标: {wall_dn:,.0f} | 止损: {wall_up:,.0f}上方')
    else:
        lines.append(f'  ⚖️ CHOP震荡 | {wall_dn:,.0f}支撑/{wall_up:,.0f}阻力 | 等方向选择后入场')
        lines.append(f'  ⚠️ ZeroFlip={gex.get("zero_flip",0):,.0f} 是关键翻转位，突破后看新方向')

    lines.append('')
    lines.append(f'📡 梵天系统 | 数据驱动 | 非投资建议')

    return '\n'.join(lines)

# ── 主函数 ────────────────────────────────────────────────────────
def main(symbols=None, push=True) -> str:
    symbols = symbols or ['BTC']
    gex_state = load_json(GEX_STATE)
    vb_state  = load_json(VOL_BETA_STATE)
    oi_wl     = load_json(OI_WATCHLIST)
    b_state   = load_json(BRAHMA_STATE)

    reports = []
    for sym_short in symbols:
        sym_full = f'{sym_short}USDT'
        price    = get_live_price(sym_full) or b_state.get('price', 0)

        gex_a  = analyze_gex(gex_state, sym_short)
        vb_a   = analyze_vol_beta(vb_state, sym_short)
        oi_s   = get_oi_snapshot(sym_full)
        oi_al  = analyze_oi_watchlist(oi_wl)

        regime      = b_state.get('regime', 'CHOP_MID')
        brahma_score = float(b_state.get('score_final') or b_state.get('score') or 0)

        report = build_intel_report(
            sym_short, price,
            gex_a, vb_a, oi_s, oi_al,
            regime, brahma_score,
        )
        reports.append(report)

    full_report = '\n\n'.join(reports)
    print(full_report)

    # 保存最后一份报告
    try:
        Path(INTEL_LAST).write_text(json.dumps({
            'ts': time.time(),
            'report': full_report,
        }, ensure_ascii=False))
    except Exception:
        pass

    return full_report

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['BTC'])
    p.add_argument('--no-push', action='store_true')
    args = p.parse_args()
    main(symbols=args.symbols, push=not args.no_push)

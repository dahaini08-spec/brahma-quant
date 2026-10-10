#!/usr/bin/env python3
"""
🏛️ 梵天交易副驾 trading_copilot.py
2026-10-09 苏摩111封印

五大监控模块：
  M1. 价位接近监控   — liq_short/liq_long/GEX关键位 ±0.3%
  M2. OI日变化监控   — 单日OI变化 ±2.5% 触发（领先2天！）
  M3. FR极端值监控   — FR > +0.008% 或 < -0.005% 触发
  M4. 大户LSR追多监控 — 单日大户LSR变化 > +5% 触发（底部/顶部信号）
  M5. 仓位信号反向检查 — 有持仓时CVD/OI/LSR逆向立即推警报

设计原则：
  苏摩需要的是交易副驾，不是分析机器
  信号出现 → 60秒内推送 → 不依赖苏摩主动问
"""

import json, pathlib, time, subprocess, urllib.request, ssl, os, sys

# ── 配置 ──
WORKDIR       = pathlib.Path('/root/.openclaw/workspace/trading-system')
BASE          = WORKDIR  # [P0-A fix 2026-10-10] M0自愈路径别名
JARVIS_USER   = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed')
CHANNEL       = 'jarvis'
STATE_FILE    = WORKDIR / 'data' / 'copilot_state.json'
POSITIONS_FILE = WORKDIR / 'data' / 'active_positions.json'

# 触发阈值（基于7日复盘血泪总结）
PROX_PCT      = 0.003   # 价位接近 ±0.3%
OI_DAY_THRESH = 0.025   # OI日变化 ±2.5%（10/05 -3.95% 提前2天预警）
FR_HIGH       = 0.008   # FR极端高 > +0.008%（10/06 ETH 0.0092% 前兆）
FR_LOW        = -0.005  # FR极端低 < -0.005%
LSR_BIG_DELTA = 5.0     # 大户LSR单日变化 > +5%（10/07 ETH +10.6%）
# [asset_config SSOT 2026-10-09] 표적 파라미터 동적 로드
import json as _acj3, pathlib as _acp3
def _get_ac():
    _pp = _acp3.Path(__file__).parent.parent / 'data' / 'asset_config.json'
    try: return _acj3.loads(_pp.read_text())
    except: return {}

LSR_HUNT_BTC  = float(_get_ac().get("BTC",{}).get("lsr_hunt",65.0))
LSR_HUNT_ETH  = float(_get_ac().get("ETH",{}).get("lsr_hunt",77.0))
CVD_CONFLICT  = -300    # CVD与多单方向冲突阈值

# 冷却时间（同一信号不重复推送）
COOLDOWN = {
    'price':    7200,   # [2026-10-09 苏摩111] 价位：2小时（防止价格震荡多次触发）
    'oi_day':   14400,  # OI日变化：4小时
    'fr':       14400,  # FR极端：4小时
    'lsr_big':  7200,   # 大户追多：2小时
    'lsr_hunt': 3600,   # 猎杀门槛：1小时
    'conflict': 7200,   # 信号冲突：2小时
    'position': 1800,   # 仓位反向：30分钟
}

ctx = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def push(msg, key=''):
    """推送到苏摩Jarvis线程"""
    target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
    cmd = ['openclaw','message','send','--channel',CHANNEL,'-t',target,'-m',msg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print(f'[PUSH OK] {key}: {msg[:60]}...')
        else:
            print(f'[PUSH FAIL] {r.stderr[:100]}')
    except Exception as e:
        print(f'[PUSH ERR] {e}')

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except:
        return {}

def save_state(s):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2))

def load_positions():
    """加载苏摩当前持仓"""
    try:
        return json.loads(POSITIONS_FILE.read_text())
    except:
        return {}

def get_price(sym):
    try:
        return float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT')['price'])
    except:
        return 0.0

def get_liq(sym):
    try:
        d = json.loads((WORKDIR/'data'/f'liq_heatmap_{sym.lower()}usdt.json').read_text())
        return float(d.get('nearest_short_liq',0)), float(d.get('nearest_long_liq',0))
    except:
        return 0.0, 0.0

def get_gex_strikes(sym):
    try:
        g = json.loads((WORKDIR/'data'/'gex_state.json').read_text())
        return g.get(sym.upper(),{}).get('top_strikes',{})
    except:
        return {}

def get_cvd(sym):
    try:
        d = json.loads((WORKDIR/'data'/f'cvd_realtime_{sym.lower()}usdt.json').read_text())
        return float(d.get('cvd_1h', d.get('delta_1h', d.get('cvd', 0))) or 0)
    except:
        return 0.0

def near(price, target, pct=PROX_PCT):
    if target == 0: return False
    return abs(price - target) / target <= pct

# ══════════════════════════════════════════
# M1. 价位接近监控
# ══════════════════════════════════════════
def m1_price_proximity(state, alerts):
    """价格进入关键结构位±0.3%时推送"""
    for sym in ['BTC','ETH']:
        p = get_price(sym)
        if p == 0: continue
        liq_s, liq_l = get_liq(sym)
        ts = get_gex_strikes(sym)
        cvd = get_cvd(sym)

        levels = []
        if sym == 'BTC':
            if liq_s > 0:
                levels.append(('liq_short', liq_s,
                    f'🔴 BTC止损墙 ${liq_s:,.0f} 触及！\n'
                    f'当前价 ${p:,.0f}\n\n'
                    f'空单入场区：${liq_s:,.0f}~${liq_s+300:,.0f}\n'
                    f'止损 ${liq_s+900:,.0f} | 目标 ${liq_l:,.0f}\n'
                    f'触发条件：1H收阴 + CVD转负'))
            if liq_l > 0:
                levels.append(('liq_long', liq_l,
                    f'🟢 BTC支撑池 ${liq_l:,.0f} 触及！\n'
                    f'当前价 ${p:,.0f}\n\n'
                    f'多单入场区：${liq_l:,.0f}~${liq_l+300:,.0f}\n'
                    f'止损 ${liq_l-900:,.0f} | 目标 ${liq_s:,.0f}\n'
                    f'GEX $80,000=-30.4M 做市商机械支撑'))
            gex_80k = float(ts.get('80000', 0))
            if gex_80k < 0:
                levels.append(('gex_80000', 80000.0,
                    f'🟢 BTC进入GEX大负区！\n'
                    f'当前价 ${p:,.0f} | GEX $80,000={gex_80k/1e6:.1f}M\n\n'
                    f'做市商被迫买入区间\n'
                    f'多单入场 $79,700~$80,200 | 止损 $78,800\n'
                    f'RR≈3.3 ✅'))
        else:  # ETH
            if liq_s > 0:
                levels.append(('liq_short', liq_s,
                    f'🔴 ETH止损墙 ${liq_s:,.0f} 触及！\n'
                    f'当前价 ${p:,.2f}\n\n'
                    f'空单入场区：${liq_s:,.0f}~${liq_s+20:,.0f}\n'
                    f'止损 ${liq_s+60:,.0f} | 目标 ${liq_l:,.0f}\n'
                    f'散户77%极端=猎杀弹药充足'))
            if liq_l > 0:
                levels.append(('liq_long', liq_l,
                    f'🟢 ETH支撑池 ${liq_l:,.0f} 触及！\n'
                    f'当前价 ${p:,.2f}\n\n'
                    f'多单入场区：${liq_l:,.0f}~${liq_l+20:,.0f}\n'
                    f'止损 ${liq_l-60:,.0f} | 目标 ${liq_s:,.0f}\n'
                    f'RR≈3.5 ✅'))
            # [2026-10-09 苏摩111] GEX决战位：先查OI+CVD方向，再决定推送内容
            # 根因：无论空头/多头背景都发「两可」信号 → 与signal_conflict矛盾
            gex_2500 = float(ts.get('2500', 0))
            if gex_2500 != 0:
                # 读取OI+CVD实时方向
                _cvd_eth = get_cvd('ETH')
                _oi_bias = ''
                try:
                    _oi_h = fetch('https://fapi.binance.com/futures/data/openInterestHist?symbol=ETHUSDT&period=1h&limit=4')
                    _oi_vals = [float(o['sumOpenInterest']) for o in _oi_h]
                    _oi_chg = (_oi_vals[-1]-_oi_vals[0])/_oi_vals[0]*100 if _oi_vals[0]>0 else 0
                    _oi_bias = 'BUILD' if _oi_chg > 0.3 else ('UNWIND' if _oi_chg < -0.3 else 'FLAT')
                except: pass

                # 方向裁决：CVD+OI双重确认
                _cvd_bearish = _cvd_eth < -300
                _gex_bearish = gex_2500 < 0

                if _cvd_bearish and _gex_bearish:
                    # 空头背景：只推空单方向，不发两可信号
                    extra = f'⛔ GEX{gex_2500/1e6:.1f}M+CVD{_cvd_eth:.0f}=双重阻力\n禁止做多 | 等跌破$2,400+CVD转正才接多'
                elif not _cvd_bearish and _oi_bias == 'BUILD':
                    # 多头背景：推多单方向
                    extra = f'✅ CVD转正+OI BUILD\n突破$2,500+站稳 → 多单入场 | 目标$2,560'
                else:
                    # 信号不明确：不推送，等待明确方向
                    extra = ''

                if extra:  # 方向明确才推送
                    levels.append(('gex_2500', 2500.0,
                        f'⚡ ETH触及GEX决战位 $2,500！\n'
                        f'当前价 ${p:,.2f} | GEX={gex_2500/1e6:.1f}M\n\n'
                        f'{extra}\n'
                        f'今日最高优先级价位'))

        for level_key, target, msg in levels:
            sk = f'{sym}_M1_{level_key}'
            if near(p, target) and (time.time() - state.get(sk, 0)) > COOLDOWN['price']:
                full = f'🏛️ 梵天副驾·价位警报 [{sym}]\n{msg}\n\n🌿 姓赵不宣 | 不是建议'
                alerts.append((sk, full))


# ══════════════════════════════════════════
# M2. OI日变化监控（领先2天的先行指标）
# ══════════════════════════════════════════
def m2_oi_daily_change(state, alerts):
    """
    7日复盘发现：
      10/05 BTC OI -3.95% → 提前2天预示10/07大跌
      10/06 ETH OI +3.90% → 提前1天预示10/07暴跌
    阈值：单日OI变化 ±2.5%
    """
    for sym in ['BTC','ETH']:
        try:
            oi1h = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}USDT&period=1h&limit=25')
            oi_v = [float(o['sumOpenInterest']) for o in oi1h]
            if len(oi_v) < 24: continue

            # 24H OI变化
            oi_24h_ago = oi_v[0]
            oi_now = oi_v[-1]
            chg = (oi_now - oi_24h_ago) / oi_24h_ago * 100

            p = get_price(sym)
            sk = f'{sym}_M2_oi_daily'

            if abs(chg) >= OI_DAY_THRESH * 100 and \
               (time.time() - state.get(sk, 0)) > COOLDOWN['oi_day']:

                direction = 'UNWIND（多头撤退）' if chg < 0 else 'BUILD（资金涌入）'
                if chg < -OI_DAY_THRESH * 100:
                    action = (
                        f'⚠️ OI大量流出=主力悄悄出货\n'
                        f'历史规律：OI-3.95%后2日内跌幅>5%（准确率82%）\n\n'
                        f'📌 建议：空单准备\n'
                        f'等散户LSR超过65%（BTC）/ 77%（ETH）时入场'
                    )
                    emoji = '🔴'
                else:
                    action = (
                        f'⚡ OI大量涌入=新资金建仓\n'
                        f'需对照FR判断方向：\n'
                        f'  FR>+0.008%+OI BUILD = 多头过热→做空准备\n'
                        f'  FR中性+OI BUILD = 真实多头入场→做多'
                    )
                    emoji = '🟡'

                msg = (
                    f'{emoji} 梵天副驾·OI日变化警报 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'📊 24H OI变化：{chg:+.2f}% → {direction}\n'
                    f'{oi_24h_ago:,.0f} → {oi_now:,.0f}\n\n'
                    f'{action}\n\n'
                    f'⏰ 此信号通常领先价格变动24~48小时\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk, msg))
        except Exception as e:
            print(f'[M2 ERR {sym}] {e}')


# ══════════════════════════════════════════
# M3. FR极端值监控（反转前最强信号）
# ══════════════════════════════════════════
def m3_fr_extreme(state, alerts):
    """
    7日复盘发现：
      10/06 ETH FR=+0.0092% → 24H后暴跌-4.58%（准确率81%）
    阈值：FR > +0.008% 或 < -0.005%
    """
    for sym in ['BTC','ETH']:
        try:
            fr_data = fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}USDT&limit=3')
            fr_now = float(fr_data[-1]['fundingRate']) * 100
            fr_prev = float(fr_data[-2]['fundingRate']) * 100
            p = get_price(sym)

            sk_high = f'{sym}_M3_fr_high'
            sk_low  = f'{sym}_M3_fr_low'

            # FR极端高（多头过度拥挤=即将反转做空）
            if fr_now > FR_HIGH * 100 and \
               (time.time() - state.get(sk_high, 0)) > COOLDOWN['fr']:
                msg = (
                    f'🔴 梵天副驾·FR极端警报 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'⚠️ FR={fr_now:+.4f}%（多头极端付费）\n'
                    f'历史规律：FR>+0.008%后72H内下跌概率=81%\n\n'
                    f'📌 空单准备信号：\n'
                    f'  10/06 ETH FR=+0.0092% → 次日跌-4.58%（已验证）\n\n'
                    f'等散户LSR再上升0.5~1% → 空单入场\n'
                    f'目标：liq_long（支撑池）\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk_high, msg))

            # FR极端低（空头过度拥挤=底部反转信号）
            if fr_now < FR_LOW * 100 and \
               (time.time() - state.get(sk_low, 0)) > COOLDOWN['fr']:
                msg = (
                    f'🟢 梵天副驾·FR底部信号 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'✅ FR={fr_now:+.4f}%（空头极端付费）\n'
                    f'空头过度拥挤=底部反转概率高\n\n'
                    f'📌 多单准备信号：\n'
                    f'  FR从正→负的那一刻=真正底部\n'
                    f'  当前已转负，等OI止跌+1H收阳确认\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk_low, msg))

        except Exception as e:
            print(f'[M3 ERR {sym}] {e}')


# ══════════════════════════════════════════
# M4. 大户LSR单日变化监控（底部/顶部信号）
# ══════════════════════════════════════════
def m4_big_lsr_delta(state, alerts):
    """
    7日复盘发现：
      10/07 ETH大户LSR单日+10.6% → 底部接货确认
      10/08 BTC大户LSR+3.1%（54%→66%）→ 低位建多
    阈值：单日大户LSR变化 > +5%（追多）/ < -5%（出货）
    """
    for sym in ['BTC','ETH']:
        try:
            bl1h = fetch(f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={sym}USDT&period=1h&limit=25')
            bl_v = [float(o['longAccount'])*100 for o in bl1h]
            if len(bl_v) < 24: continue

            bl_24h = bl_v[0]
            bl_now = bl_v[-1]
            delta = bl_now - bl_24h

            p = get_price(sym)
            liq_s, liq_l = get_liq(sym)

            sk_up   = f'{sym}_M4_big_up'
            sk_down = f'{sym}_M4_big_down'

            # 大户单日大量追多（底部建仓信号）
            if delta >= LSR_BIG_DELTA and \
               (time.time() - state.get(sk_up, 0)) > COOLDOWN['lsr_big']:
                msg = (
                    f'🟢 梵天副驾·大户接货警报 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'🏦 大户LSR单日+{delta:.1f}%！\n'
                    f'  {bl_24h:.1f}% → {bl_now:.1f}%（24H）\n\n'
                    f'历史规律：大户单日+5%以上后48H内反弹概率82%\n'
                    f'  10/07 ETH大户+10.6% → 底部确认（已验证）\n\n'
                    f'📌 多单信号：\n'
                    f'  大户在接货 = 主力判断底部在此\n'
                    f'  等OI止跌+1H收阳 → 跟随入多\n'
                    f'  入场区：${max(p-50,liq_l):,.0f}~${p:,.0f}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk_up, msg))

            # 大户单日大量减多（出货信号）
            if delta <= -LSR_BIG_DELTA and \
               (time.time() - state.get(sk_down, 0)) > COOLDOWN['lsr_big']:
                msg = (
                    f'🔴 梵天副驾·大户出货警报 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'⚠️ 大户LSR单日{delta:.1f}%（大量减多）\n'
                    f'  {bl_24h:.1f}% → {bl_now:.1f}%（24H）\n\n'
                    f'大户在出货 = 主力认为顶部已到\n\n'
                    f'📌 空单准备信号：\n'
                    f'  等散户LSR继续上升到猎杀门槛\n'
                    f'  BTC>65% / ETH>77% 触发猎杀\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk_down, msg))

        except Exception as e:
            print(f'[M4 ERR {sym}] {e}')


# ══════════════════════════════════════════
# M5. 猎杀门槛 + 仓位信号反向检查
# ══════════════════════════════════════════
def m5_hunt_and_position(state, alerts):
    """
    两个子功能：
    5a. 散户LSR猎杀门槛（BTC>65% / ETH>77%）
    5b. 有持仓时CVD/OI/FR反向立即推警报（血的教训）
    """
    positions = load_positions()

    for sym in ['BTC','ETH']:
        try:
            lsr_data = fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}USDT&period=5m&limit=3')
            lsr_now  = float(lsr_data[-1]['longAccount'])*100
            lsr_prev = float(lsr_data[-2]['longAccount'])*100
            p = get_price(sym)
            hunt_thresh = LSR_HUNT_BTC if sym=='BTC' else LSR_HUNT_ETH
            liq_s, liq_l = get_liq(sym)
            cvd = get_cvd(sym)
            fr_data = fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}USDT&limit=2')
            fr_now = float(fr_data[-1]['fundingRate'])*100

            # 5a. 猎杀门槛
            sk_hunt = f'{sym}_M5_hunt'
            if lsr_now >= hunt_thresh and \
               (time.time() - state.get(sk_hunt, 0)) > COOLDOWN['lsr_hunt']:
                oi_data = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}USDT&period=15m&limit=4')
                oi_v = [float(o['sumOpenInterest']) for o in oi_data]
                oi_dir = 'OI上升（有人接货）' if oi_v[-1]>oi_v[-3] else 'OI下降（主力撤退）'

                msg = (
                    f'🚨 梵天副驾·猎杀门槛警报 [{sym}]\n'
                    f'当前价 ${p:,.1f}\n\n'
                    f'散户LSR={lsr_now:.1f}% ≥ {hunt_thresh}%（猎杀门槛触发！）\n'
                    f'FR={fr_now:+.4f}% | {oi_dir}\n\n'
                    f'📌 主力即将启动猎杀：\n'
                    f'  历史规律：散户超门槛后24H内下跌概率73%\n\n'
                    f'空单准备区：${liq_s:,.0f}（止损墙反弹后入场）\n'
                    f'止损：${liq_s+60 if sym=="ETH" else liq_s+900:,.0f}\n'
                    f'目标：${liq_l:,.0f}\n\n'
                    f'⚠️ 如果有多单：考虑止盈或上移止损！\n'
                    f'🌿 姓赵不宣 | 不是建议'
                )
                alerts.append((sk_hunt, msg))

            # 5b. 仓位反向检查（苏摩有持仓时）
            pos_key = sym.upper()
            if pos_key in positions:
                pos = positions[pos_key]
                pos_dir    = pos.get('direction','').upper()   # LONG / SHORT
                pos_entry  = float(pos.get('entry', 0))
                pos_sl     = float(pos.get('sl', 0))
                pos_size   = pos.get('size','')
                sk_pos = f'{sym}_M5_pos_risk'

                warn_reasons = []

                # 多单时的危险信号
                if pos_dir == 'LONG':
                    if cvd < CVD_CONFLICT:
                        warn_reasons.append(f'CVD={cvd:.0f}（卖方主导，与多单方向相反）')
                    if fr_now > 0.003:
                        warn_reasons.append(f'FR={fr_now:+.4f}%（多头仍付费=清洗未完成）')
                    if lsr_now >= hunt_thresh:
                        warn_reasons.append(f'散户{lsr_now:.1f}%≥猎杀门槛{hunt_thresh}%')
                    oi_data = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}USDT&period=15m&limit=6')
                    oi_v = [float(o['sumOpenInterest']) for o in oi_data]
                    oi_chg = (oi_v[-1]-oi_v[-3])/oi_v[-3]*100 if oi_v[-3]>0 else 0
                    if oi_chg < -1.0:
                        warn_reasons.append(f'OI 15min={oi_chg:+.2f}%（多头在撤退）')

                # 空单时的危险信号
                elif pos_dir == 'SHORT':
                    if cvd > abs(CVD_CONFLICT):
                        warn_reasons.append(f'CVD={cvd:.0f}（买方主导，与空单方向相反）')
                    if fr_now < -0.003:
                        warn_reasons.append(f'FR={fr_now:+.4f}%（空头仍付费=反弹未完成）')
                    bl_data = fetch(f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={sym}USDT&period=1h&limit=6')
                    bl_v = [float(o['longAccount'])*100 for o in bl_data]
                    if bl_v[-1] > bl_v[0] + 5:
                        warn_reasons.append(f'大户LSR+{bl_v[-1]-bl_v[0]:.1f}%（大户追多，逆空单）')

                if warn_reasons and (time.time() - state.get(sk_pos, 0)) > COOLDOWN['position']:
                    pnl = (p-pos_entry)/pos_entry*100 if pos_dir=='LONG' else (pos_entry-p)/pos_entry*100
                    msg = (
                        f'⚠️ 梵天副驾·持仓风险警报 [{sym} {pos_dir}]\n'
                        f'当前价 ${p:,.1f} | 入场 ${pos_entry:,.1f}\n'
                        f'浮动 {pnl:+.2f}% | SL ${pos_sl:,.1f}\n\n'
                        f'🚨 检测到{len(warn_reasons)}项逆向信号：\n'
                        + '\n'.join(f'  ❌ {r}' for r in warn_reasons) +
                        f'\n\n📌 建议：\n'
                        f'  考虑止盈/上移止损\n'
                        f'  逆向信号>2个=强制离场建议\n\n'
                        f'血的教训：10/08苏摩两笔止损根因\n'
                        f'此警报是为了避免重演\n\n'
                        f'🌿 姓赵不宣 | 不是建议'
                    )
                    alerts.append((sk_pos, msg))

        except Exception as e:
            print(f'[M5 ERR {sym}] {e}')


# ══════════════════════════════════════════
# 主执行函数
# ══════════════════════════════════════════
def m0_analysis_staleness_guard():
    """
    [修复 2026-10-10 苏摩111] auto_analysis 시효 守护
    90분 초과 시 자동 재분석 트리거
    30분 초과 시 경고 로그
    """
    import subprocess as _sp
    try:
        _af = BASE / 'data' / 'auto_analysis_latest.json'
        if not _af.exists():
            return
        import json as _jm
        _d   = _jm.loads(_af.read_text())
        _ts  = float(_d.get('ts', 0))
        _age = (time.time() - _ts) / 60
        if _age > 90:
            print(f'[M0] ⚠️ auto_analysis {_age:.0f}min 경과 → 자동 재분석 트리거')
            # [P0-A 2026-10-10 苏摩111] Popen非阻塞替换run，防止主链3min阻塞M1~M7
            _lock_f = pathlib.Path('/tmp/brahma_m0_analysis.lock')
            if not _lock_f.exists():
                _lock_f.write_text(str(__import__('os').getpid()))
                _proc = _sp.Popen(
                    ['python3', str(BASE / 'scripts' / 'brahma_manual_analysis.py'),
                     '--symbols', 'BTC', 'ETH'],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, cwd=str(BASE)
                )
                print(f'[M0] ✅ auto_analysis 非阻塞启动 PID={_proc.pid}')
            else:
                print(f'[M0] 已有分析进程运行中，跳过重复触发')
        elif _age > 30:
            print(f'[M0] auto_analysis {_age:.0f}min (임계치 90min)')
    except Exception as _e:
        print(f'[M0 ERR] {_e}')


def main():
    ts = time.strftime('%H:%M:%S')
    state = load_state()
    alerts = []

    print(f'[副驾] {ts} 五模块检查启动...')

    try: m0_analysis_staleness_guard()
    except Exception as e: print(f'[M0 ERR] {e}')

    try: m1_price_proximity(state, alerts)
    except Exception as e: print(f'[M1 ERR] {e}')

    try: m2_oi_daily_change(state, alerts)
    except Exception as e: print(f'[M2 ERR] {e}')

    try: m3_fr_extreme(state, alerts)
    except Exception as e: print(f'[M3 ERR] {e}')

    try: m4_big_lsr_delta(state, alerts)
    except Exception as e: print(f'[M4 ERR] {e}')

    try: m5_hunt_and_position(state, alerts)
    except Exception as e: print(f'[M5 ERR] {e}')

    # M6: Goal Loop Monitor — 目标导向循环检查
    try:
        import subprocess as _sp, sys as _sys
        _gl = _sp.run(
            ['python3', 'scripts/brahma_goal_loop.py', 'check'],
            cwd=str(WORKDIR), capture_output=True, text=True, timeout=20
        )
        if _gl.stdout.strip():
            print(f'[M6 goal_loop] {_gl.stdout.strip()[:120]}')
    except Exception as e:
        print(f'[M6 ERR] {e}')

    # M6.5: BTC→ETH 领先信号 + 相关性监控 [P1-⑤⑦ 2026-10-09]
    try:
        import importlib.util as _ilu6
        _spec6 = _ilu6.spec_from_file_location('lead', str(WORKDIR / 'scripts/brahma_lead_signal.py'))
        _lead  = _ilu6.module_from_spec(_spec6)
        _spec6.loader.exec_module(_lead)
        _lead_alerts = _lead.check_lead_signals()
        if _lead_alerts:
            print(f'[M6.5 lead] {len(_lead_alerts)}条领先信号')
    except Exception as e:
        print(f'[M6.5 ERR] {e}')

    # M7: VIP策略状态监控 — 关键维度变化主动推送
    try:
        import importlib.util as _ilu, sys as _sys
        _spec = _ilu.spec_from_file_location('m7', str(WORKDIR / 'scripts/m7_vip_monitor.py'))
        _m7 = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_m7)
        _m7_result = _m7.check_all()
        for sk, msg in _m7_result:
            alerts.append((sk, msg))
        if _m7_result:
            print(f'[M7] {len(_m7_result)}条VIP策略变更警报')
    except Exception as e:
        print(f'[M7 ERR] {e}')

    # [P0-A] M0 lock 정리: 분석 프로세스 완료 시 lock 파일 삭제
    try:
        _lf = pathlib.Path('/tmp/brahma_m0_analysis.lock')
        if _lf.exists():
            import os as _os
            _pid = int(_lf.read_text().strip())
            try: _os.kill(_pid, 0)
            except ProcessLookupError: _lf.unlink(missing_ok=True)
    except Exception: pass

    if alerts:
        for sk, msg in alerts:
            push(msg, sk)
            state[sk] = time.time()
        save_state(state)
        print(f'[副驾] 推送 {len(alerts)} 条警报')
    else:
        print(f'[副驾] 无触发 — 所有信号在安全区间')

    # M8: Groq恒量分析层 [2026-10-10 苏摩111] 每分钟8次 = 11520次/日 = 80%配额
    m8_groq_constant_layer(state)
    save_state(state)


if __name__ == '__main__':
    main()

# ══════════════════════════════════════════════════════════════════
# M8: Groq恒量分析层 [新增 2026-10-10 苏摩111]
# 每分钟运行，8次Groq调用，累计11520次/日 = 80%配额利用率
# 输出写入 data/groq_realtime_analysis.json，供仪表盘+下次分析读取
# ══════════════════════════════════════════════════════════════════
def m8_groq_constant_layer(state: dict) -> None:
    """
    每分钟必跑的Groq恒量分析层
    不推送，只写文件，为梵天大脑提供实时AI判断支撑
    8个维度 × 1440分钟 = 11,520次/日 = Groq配额80%
    """
    import time as _t, json as _j, pathlib as _pl
    _now = _t.time()
    
    # 节流：同一分钟内不重复运行
    if _now - state.get('m8_last_run', 0) < 55:
        return
    state['m8_last_run'] = _now
    
    try:
        _sys = __import__('sys')
        _sys.path.insert(0, str(_pl.Path(__file__).parent))
        from free_llm_client import chat as _gc, GROQ_KEY
        if not GROQ_KEY:
            return
        
        # 读取最新市场状态
        _data_dir = _pl.Path(__file__).parent.parent / 'data'
        def _read_state(sym):
            f = _data_dir / f'brahma_state_{sym.lower()}.json'
            return _j.loads(f.read_text()) if f.exists() else {}
        
        btc = _read_state('btc')
        eth = _read_state('eth')
        
        _bp = float(btc.get('price', 0) or 0)
        _ep = float(eth.get('price', 0) or 0)
        _br = str(btc.get('regime', 'CHOP_MID'))
        _er = str(eth.get('regime', 'CHOP_MID'))
        _bcvd = float(btc.get('cvd_1h', 0) or 0)
        _ecvd = float(eth.get('cvd_1h', 0) or 0)
        _blsr = float(btc.get('lsr_retail', 50) or 50)
        _elsr = float(eth.get('lsr_retail', 50) or 50)
        _bfr  = float(btc.get('fr', 0) or 0) * 100
        _efr  = float(eth.get('fr', 0) or 0) * 100
        _boi  = str(btc.get('oi_direction', 'NEUTRAL'))
        _eoi  = str(eth.get('oi_direction', 'NEUTRAL'))
        _brsi = float(btc.get('rsi_1h', 50) or 50)
        _ersi = float(eth.get('rsi_1h', 50) or 50)
        
        import concurrent.futures as _cf
        results = {}
        
        def _call(key, prompt, task):
            try:
                r = _gc(prompt, max_tokens=40, task=task, timeout=12)
                return key, (r.strip() if r else '')
            except Exception:
                return key, ''
        
        # 8개 병렬 Groq 호출
        _calls = [
            ('btc_sentiment', 
             f'BTC${_bp:.0f} {_br}体制 RSI={_brsi:.0f} CVD={_bcvd:.0f}。10字内：当前多空情绪？', 
             'oi'),
            ('eth_sentiment',
             f'ETH${_ep:.0f} {_er}体制 RSI={_ersi:.0f} CVD={_ecvd:.0f}。10字内：当前多空情绪？',
             'oi'),
            ('btc_flow',
             f'BTC OI={_boi} CVD={_bcvd:.0f} FR={_bfr:+.3f}%。10字内：资金流向解读？',
             'council'),
            ('eth_flow',
             f'ETH OI={_eoi} CVD={_ecvd:.0f} FR={_efr:+.3f}% LSR散户={_elsr:.0f}%。10字内：资金流向？',
             'council'),
            ('correlation',
             f'BTC {_br} vs ETH {_er}，CVD背离={abs(_bcvd-_ecvd):.0f}。10字内：相关性判断？',
             'regime'),
            ('position_advice',
             f'BTC${_bp:.0f} ETH${_ep:.0f}，体制{_br}。10字内：现在仓位建议？',
             'vip'),
            ('risk_8h',
             f'BTC FR={_bfr:+.3f}% ETH LSR={_elsr:.0f}%。10字内：未来8H最大风险？',
             'review'),
            ('constitution_check',
             f'当前BTC={_br} ETH={_er}，FR={_bfr:+.3f}%。梵天铁律是否有需要注意的？10字内',
             'safety'),
        ]
        
        with _cf.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_call, k, prompt, task) for k, prompt, task in _calls]
            for fut in _cf.as_completed(futures, timeout=15):
                try:
                    k, v = fut.result()
                    if v:
                        results[k] = v
                except Exception:
                    pass
        
        if results:
            # 파일 저장
            out_f = _data_dir / 'groq_realtime_analysis.json'
            _prev = {}
            if out_f.exists():
                try: _prev = _j.loads(out_f.read_text())
                except: pass
            _prev.update(results)
            _prev['ts'] = _now
            _prev['btc_price'] = _bp
            _prev['eth_price'] = _ep
            out_f.write_text(_j.dumps(_prev, ensure_ascii=False, indent=2))
            print(f'[M8-Groq] {len(results)}/8 분석완료 저장')
    
    except Exception as _e:
        pass  # 조용히 실패, 메인 루프 불간섭

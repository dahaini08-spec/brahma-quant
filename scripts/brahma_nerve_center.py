#!/usr/bin/env python3
"""
brahma_nerve_center.py · 梵天动态感知神经中枢 v1.0
══════════════════════════════════════════════════
设计院自主决策 · 2026-07-03 · 苏摩授权

核心能力：
  1. BTC体制变化 → 全链条联动感知（持仓/信号/相关币种/策略）
  2. ETH/主流币结构变化感知（价格关键位/OI/资金费率/清算）
  3. 持仓风险动态感知（逆势/亏损/止盈触达）
  4. 全市场异常感知（OI异动/资金费率极值/清算集群）
  5. 系统自身状态感知（模块故障/数据陈旧/队列积压）

感知层次：
  P0 紧急（立即推送）: 体制逆转/持仓危险/系统CRITICAL
  P1 重要（有信息推送）: 关键位穿越/OI异动/体制变化
  P2 参考（日报汇总）: 普通波动/轻微告警

触发方式：cron every 5min（与 rsi_structure_watcher 对齐）
"""

import sys, os, json, time, requests, hmac, hashlib, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

# ── 推送配置 ─────────────────────────────────────────────────
try:
    import scripts.system_config as _sc
    PUSH_TARGET = f"{_sc.JARVIS_USER_ID}:t:{_sc.JARVIS_THREAD_ID}"
except Exception:
    PUSH_TARGET   = '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075'  # SSOT [2026-07-07]
PUSH_CHANNEL = 'jarvis'
STATE_FILE   = BASE / 'data' / 'nerve_center_state.json'
API_KEY = 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b'
SECRET  = 'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7'
FAPI    = 'https://fapi.binance.com'

# ── 感知标的列表 ──────────────────────────────────────────────
WATCH_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'HYPEUSDT']

# ── 感知阈值 ──────────────────────────────────────────────────
OI_SURGE_PCT       = 5.0    # OI 1H变化超5% → P1告警
FR_EXTREME         = 0.05   # 资金费率超0.05% → P1告警
PRICE_MOVE_1H_PCT  = 3.0    # 1H价格变动超3% → P1告警
LIQUIDATION_USD    = 5e6    # 清算量超500万USD → P0告警

# ── 关键位穿越配置 ────────────────────────────────────────────
KEY_LEVELS = {
    'BTCUSDT': {'major': [60000, 62000, 65000, 68000], 'ema_cross': True},
    'ETHUSDT': {'major': [1600, 1700, 1800, 2000],     'ema_cross': True},
    'SOLUSDT': {'major': [130, 150, 170],               'ema_cross': False},
    'BNBUSDT': {'major': [550, 600, 650],               'ema_cross': False},
}


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _pub(path, params=None):
    try:
        r = requests.get(f'{FAPI}{path}', params=params or {}, timeout=6)
        return r.json()
    except Exception:
        return {}


def _signed(path, params=None):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    qs += f'&signature={sig}'
    return requests.get(f'{FAPI}{path}?{qs}',
                        headers={'X-MBX-APIKEY': API_KEY}, timeout=6).json()


def _push(msg: str, priority: int = 1, dedup_key: str = None, dedup_ttl: int = 3600):
    """推送消息（含去重）"""
    state = _load_state()
    now = time.time()

    if dedup_key:
        last_push = state.get('push_dedup', {}).get(dedup_key, 0)
        if now - last_push < dedup_ttl:
            return False
        state.setdefault('push_dedup', {})[dedup_key] = now
        _save_state(state)

    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target', PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return True
    except Exception as e:
        print(f'[NerveCenter] 推送失败: {e}')
        return False


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _calc_rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    g = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    l = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(g[-n:]) / n
    al = sum(l[-n:]) / n
    for i in range(n, len(g)):
        ag = (ag * (n-1) + g[i]) / n
        al = (al * (n-1) + l[i]) / n
    return round(100 - 100 / (1 + ag / al), 1) if al else 100.0


def _calc_ema(closes, n):
    k = 2 / (n + 1)
    v = closes[0]
    for c in closes[1:]:
        v = c * k + v * (1 - k)
    return round(v, 2)


# ══════════════════════════════════════════════════════════════
# 感知模块1：BTC体制变化 → 全链条联动
# ══════════════════════════════════════════════════════════════

def sense_btc_regime_change(state: dict) -> list:
    """
    BTC体制变化感知
    变化时 → 触发：1.持仓风险评估 2.信号失效扫描 3.相关币种感知 4.策略重评
    """
    alerts = []
    try:
        reg_file = BASE / 'data' / 'regime_state.json'
        if not reg_file.exists():
            return alerts
        reg = json.loads(reg_file.read_text())

        btc_regime  = reg.get('BTCUSDT', {}).get('confirmed', 'UNKNOWN')
        eth_regime  = reg.get('ETHUSDT', {}).get('confirmed', 'UNKNOWN')
        prev_btc    = state.get('prev_btc_regime', btc_regime)
        prev_eth    = state.get('prev_eth_regime', eth_regime)

        state['prev_btc_regime'] = btc_regime
        state['prev_eth_regime'] = eth_regime

        # ── BTC体制切换检测 ───────────────────────────────────
        if prev_btc and btc_regime and prev_btc != btc_regime:
            # 判断切换方向严重性
            dangerous = (
                ('BULL' in prev_btc and 'BEAR' in btc_regime) or
                ('BEAR' in prev_btc and 'BULL' in btc_regime)
            )
            priority = 0 if dangerous else 1

            # 持仓逆势检查
            position_risk = _check_positions_vs_regime(btc_regime, eth_regime)

            msg_lines = [
                f"⚡ **BTC体制切换**",
                f"  {prev_btc} → **{btc_regime}**",
                f"  ETH: {eth_regime}",
                f"",
            ]
            if position_risk:
                msg_lines.append("🔴 **持仓风险告警：**")
                for pr in position_risk:
                    msg_lines.append(f"  {pr}")
            else:
                msg_lines.append("✅ 当前持仓无逆势风险")

            # 信号方向变化提示
            if 'BULL' in btc_regime:
                msg_lines.append("\n📋 策略调整：做多乘数1.6x | 做空封印")
            elif 'BEAR' in btc_regime:
                msg_lines.append("\n📋 策略调整：做空乘数1.6x | 做多封印")
            elif 'CHOP' in btc_regime:
                msg_lines.append("\n📋 策略调整：震荡体制 | 双向降权")

            alerts.append({
                'priority': priority,
                'type': 'REGIME_SWITCH',
                'msg': '\n'.join(msg_lines),
                'dedup_key': f'regime_switch_{btc_regime}',
                'dedup_ttl': 1800,
            })

        # ── ETH体制切换检测 ───────────────────────────────────
        if prev_eth and eth_regime and prev_eth != eth_regime:
            if 'BULL' in prev_eth and 'BEAR' in eth_regime:
                alerts.append({
                    'priority': 0,
                    'type': 'ETH_REGIME_SWITCH',
                    'msg': (f"⚡ **ETH体制逆转** {prev_eth}→{eth_regime}\n"
                            f"  ETH多单止损预警 | 挂单建议撤销"),
                    'dedup_key': f'eth_regime_{eth_regime}',
                    'dedup_ttl': 1800,
                })

    except Exception as e:
        print(f'[NerveCenter] 体制感知异常: {e}')
    return alerts


def _check_positions_vs_regime(btc_regime: str, eth_regime: str) -> list:
    """持仓方向 vs 当前体制 → 返回风险警告列表"""
    risks = []
    try:
        positions = _signed('/fapi/v2/positionRisk')
        if not isinstance(positions, list):
            return risks
        for p in positions:
            amt = float(p.get('positionAmt', 0))
            if abs(amt) < 1e-8:
                continue
            sym = p.get('symbol', '')
            side = 'LONG' if amt > 0 else 'SHORT'
            upnl = float(p.get('unRealizedProfit', 0))
            regime = btc_regime if 'BTC' in sym else eth_regime if 'ETH' in sym else None
            if not regime:
                continue
            risk_key = (side, regime)
            risk_map = {
                ('SHORT', 'BULL_TREND'):  '🔴 {s}做空+牛市趋势=死穴逆势 UPnL={u:.1f}U',
                ('SHORT', 'BULL_EARLY'):  '🔴 {s}做空+牛市初期=高风险 UPnL={u:.1f}U',
                ('LONG',  'BEAR_TREND'):  '🔴 {s}做多+熊市趋势=WR=45%封禁 UPnL={u:.1f}U',
                ('LONG',  'BEAR_EARLY'):  '🟡 {s}做多+熊市初期=逆势 UPnL={u:.1f}U',
            }
            if risk_key in risk_map:
                risks.append(risk_map[risk_key].format(
                    s=sym.replace('USDT', ''), u=upnl))
    except Exception as e:
        print(f'[NerveCenter] 持仓检查异常: {e}')
    return risks


# ══════════════════════════════════════════════════════════════
# 感知模块2：关键价格结构变化
# ══════════════════════════════════════════════════════════════

def sense_price_structure(state: dict) -> list:
    """
    价格结构变化感知：
    - 关键整数关口穿越
    - EMA20_4H 多空分界穿越
    - 1H大幅波动（>3%）
    - 50H高低点突破
    """
    alerts = []
    now = time.time()

    for sym in WATCH_SYMBOLS:
        try:
            k1h = _pub('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'limit': 55})
            if not isinstance(k1h, list) or len(k1h) < 20:
                continue

            closes = [float(x[4]) for x in k1h]
            highs  = [float(x[2]) for x in k1h]
            lows   = [float(x[3]) for x in k1h]
            price  = closes[-1]
            prev_close = closes[-2]

            # 1H变动%
            chg_1h = (price - prev_close) / prev_close * 100

            # EMA20_4H
            k4h = _pub('/fapi/v1/klines', {'symbol': sym, 'interval': '4h', 'limit': 22})
            if isinstance(k4h, list) and len(k4h) >= 20:
                ema20_4h = _calc_ema([float(x[4]) for x in k4h], 20)
            else:
                ema20_4h = None

            # 50H 高低点
            high50 = max(highs[-50:])
            low50  = min(lows[-50:])

            prev_price  = state.get(f'{sym}_prev_price', price)
            prev_h50    = state.get(f'{sym}_prev_high50', high50)
            prev_l50    = state.get(f'{sym}_prev_low50', low50)
            prev_ema4h  = state.get(f'{sym}_prev_ema4h', ema20_4h)

            state[f'{sym}_prev_price']  = price
            state[f'{sym}_prev_high50'] = high50
            state[f'{sym}_prev_low50']  = low50
            if ema20_4h:
                state[f'{sym}_prev_ema4h'] = ema20_4h

            short_sym = sym.replace('USDT', '')

            # ── 1H 大幅波动 ──────────────────────────────────
            if abs(chg_1h) >= PRICE_MOVE_1H_PCT:
                direction = '🚀' if chg_1h > 0 else '💥'
                alerts.append({
                    'priority': 1,
                    'type': 'PRICE_SURGE',
                    'symbol': sym,
                    'price': price,
                    'msg': (f"{direction} **{short_sym} 1H大幅波动**\n"
                            f"  {prev_close:,.1f} → {price:,.1f} ({chg_1h:+.1f}%)"),
                    'dedup_key': f'{sym}_surge_{int(now//3600)}',
                    'dedup_ttl': 3600,
                })

            # ── 50H 高点突破 ──────────────────────────────────
            if prev_price <= prev_h50 and price > high50 * 1.002:
                alerts.append({
                    'priority': 1,
                    'type': 'BREAKOUT_HIGH',
                    'symbol': sym,
                    'price': price,
                    'msg': (f"🚀 **{short_sym} 突破50H高点**\n"
                            f"  ${high50:,.1f} → 现价${price:,.1f}\n"
                            f"  做多信号增强 | 下一目标+3%"),
                    'dedup_key': f'{sym}_high50_{int(high50)}',
                    'dedup_ttl': 7200,
                })

            # ── 50H 低点跌破 ──────────────────────────────────
            if prev_price >= prev_l50 and price < low50 * 0.998:
                alerts.append({
                    'priority': 0,
                    'type': 'BREAKDOWN_LOW',
                    'msg': (f"💥 **{short_sym} 跌破50H低点**\n"
                            f"  ${low50:,.1f} → 现价${price:,.1f}\n"
                            f"  🔴 持多仓需立即评估止损"),
                    'dedup_key': f'{sym}_low50_{int(low50)}',
                    'dedup_ttl': 7200,
                })

            # ── EMA20_4H 穿越 ─────────────────────────────────
            if ema20_4h and prev_ema4h:
                was_above = prev_price > prev_ema4h * 1.003
                is_above  = price > ema20_4h * 1.003
                was_below = prev_price < prev_ema4h * 0.997
                is_below  = price < ema20_4h * 0.997

                if was_below and is_above:
                    alerts.append({
                        'priority': 1,
                        'type': 'EMA_CROSS_UP',
                        'symbol': sym,
                        'price': price,
                        'msg': (f"📈 **{short_sym} 突破EMA20_4H**\n"
                                f"  EMA=${ema20_4h:,.1f} 现价=${price:,.1f}\n"
                                f"  多头结构确认 | BULL_TREND倾向增强"),
                        'dedup_key': f'{sym}_ema_up_{int(ema20_4h)}',
                        'dedup_ttl': 3600,
                    })
                elif was_above and is_below:
                    alerts.append({
                        'priority': 0,
                        'type': 'EMA_CROSS_DOWN',
                        'msg': (f"📉 **{short_sym} 跌破EMA20_4H**\n"
                                f"  EMA=${ema20_4h:,.1f} 现价=${price:,.1f}\n"
                                f"  🔴 多头结构破坏 | 持仓需审查"),
                        'dedup_key': f'{sym}_ema_down_{int(ema20_4h)}',
                        'dedup_ttl': 3600,
                    })

            # ── 关键整数关口 ─────────────────────────────────
            levels = KEY_LEVELS.get(sym, {}).get('major', [])
            for lvl in levels:
                was_above_lvl = prev_price > lvl * 1.001
                is_above_lvl  = price > lvl * 1.001
                was_below_lvl = prev_price < lvl * 0.999
                is_below_lvl  = price < lvl * 0.999

                if was_below_lvl and is_above_lvl:
                    alerts.append({
                        'priority': 1,
                        'type': 'KEY_LEVEL_BREAK',
                        'msg': (f"🎯 **{short_sym} 突破关键位 ${lvl:,}**\n"
                                f"  现价=${price:,.1f} | 强多头信号"),
                        'dedup_key': f'{sym}_level_{lvl}_up',
                        'dedup_ttl': 14400,
                    })
                elif was_above_lvl and is_below_lvl:
                    alerts.append({
                        'priority': 1,
                        'type': 'KEY_LEVEL_BREAK',
                        'msg': (f"⚠️ **{short_sym} 跌破关键位 ${lvl:,}**\n"
                                f"  现价=${price:,.1f} | 支撑失守"),
                        'dedup_key': f'{sym}_level_{lvl}_down',
                        'dedup_ttl': 14400,
                    })

        except Exception as e:
            print(f'[NerveCenter] 价格结构感知 {sym}: {e}')

    return alerts


# ══════════════════════════════════════════════════════════════
# 感知模块3：OI/资金费率/清算 异常
# ══════════════════════════════════════════════════════════════

def sense_market_microstructure(state: dict) -> list:
    """
    市场微观结构异常感知：
    - OI 1H变化超阈值
    - 资金费率极值
    - 多空比异常（散户L/S > 1.8）
    """
    alerts = []
    now = time.time()

    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            short_sym = sym.replace('USDT', '')

            # OI 历史
            oi_hist = _pub('/futures/data/openInterestHist',
                           {'symbol': sym, 'period': '1h', 'limit': 3})
            if isinstance(oi_hist, list) and len(oi_hist) >= 2:
                oi_now  = float(oi_hist[-1]['sumOpenInterestValue'])
                oi_prev = float(oi_hist[-2]['sumOpenInterestValue'])
                oi_chg  = (oi_now - oi_prev) / oi_prev * 100 if oi_prev else 0

                prev_oi_chg = state.get(f'{sym}_prev_oi_chg', 0)
                state[f'{sym}_prev_oi_chg'] = oi_chg

                if abs(oi_chg) >= OI_SURGE_PCT and abs(oi_chg - prev_oi_chg) > 3:
                    direction = '📈' if oi_chg > 0 else '📉'
                    alerts.append({
                        'priority': 1,
                        'type': 'OI_SURGE',
                        'msg': (f"{direction} **{short_sym} OI异动 {oi_chg:+.1f}%/1H**\n"
                                f"  OI={oi_now/1e9:.2f}B → "
                                f"{'多头加仓' if oi_chg > 0 else '去杠杆/空头加仓'}"),
                        'dedup_key': f'{sym}_oi_{int(now//3600)}',
                        'dedup_ttl': 3600,
                    })

            # 资金费率
            fr_data = _pub('/fapi/v1/fundingRate', {'symbol': sym, 'limit': 1})
            if isinstance(fr_data, list) and fr_data:
                fr = float(fr_data[-1]['fundingRate']) * 100
                prev_fr = state.get(f'{sym}_prev_fr', fr)
                state[f'{sym}_prev_fr'] = fr

                if abs(fr) >= FR_EXTREME:
                    emoji = '🔥' if fr > 0 else '🧊'
                    alerts.append({
                        'priority': 1,
                        'type': 'FR_EXTREME',
                        'msg': (f"{emoji} **{short_sym} 资金费率极值 {fr:+.4f}%**\n"
                                f"  {'多头过热→做空机会' if fr > 0 else '空头过热→做多机会'}\n"
                                f"  死穴精英解锁检查：score≥155+grade≥90"),
                        'dedup_key': f'{sym}_fr_extreme_{int(now//14400)}',
                        'dedup_ttl': 14400,
                    })

            # 多空比
            lsr = _pub('/futures/data/globalLongShortAccountRatio',
                       {'symbol': sym, 'period': '1h', 'limit': 2})
            if isinstance(lsr, list) and lsr:
                ls = float(lsr[-1]['longShortRatio'])
                long_pct = float(lsr[-1]['longAccount']) * 100
                prev_ls = state.get(f'{sym}_prev_ls', ls)
                state[f'{sym}_prev_ls'] = ls

                # 散户极度偏多（历史反向信号）
                if long_pct >= 65 and prev_ls < 1.7 and ls >= 1.7:
                    alerts.append({
                        'priority': 1,
                        'type': 'LSR_EXTREME',
                        'msg': (f"⚠️ **{short_sym} 散户多空比极值**\n"
                                f"  L/S={ls:.2f} 多头={long_pct:.1f}%（历史反向信号）\n"
                                f"  建议：减仓/等待洗盘"),
                        'dedup_key': f'{sym}_lsr_extreme_{int(now//7200)}',
                        'dedup_ttl': 7200,
                    })

        except Exception as e:
            print(f'[NerveCenter] 微观结构感知 {sym}: {e}')

    return alerts


# ══════════════════════════════════════════════════════════════
# 感知模块4：持仓全生命周期感知
# ══════════════════════════════════════════════════════════════

def sense_position_lifecycle(state: dict) -> list:
    """
    持仓动态感知：
    - 新仓位建立
    - PnL达到止盈/止损区域
    - 持仓时间过长（超24H未触TP）
    """
    alerts = []
    try:
        positions = _signed('/fapi/v2/positionRisk')
        if not isinstance(positions, list):
            return alerts

        active = {p['symbol']: p for p in positions
                  if abs(float(p.get('positionAmt', 0))) > 1e-8}

        prev_positions = state.get('active_positions', {})
        state['active_positions'] = {k: {
            'amt': float(v['positionAmt']),
            'upnl': float(v['unRealizedProfit']),
            'entry': float(v['entryPrice']),
            'ts': state.get('active_positions', {}).get(k, {}).get('ts', time.time()),
        } for k, v in active.items()}

        # 新仓位感知
        new_syms = set(active.keys()) - set(prev_positions.keys())
        for sym in new_syms:
            p = active[sym]
            amt = float(p['positionAmt'])
            side = 'LONG' if amt > 0 else 'SHORT'
            entry = float(p['entryPrice'])
            alerts.append({
                'priority': 1,
                'type': 'NEW_POSITION',
                'msg': (f"🆕 **新仓位开立: {sym.replace('USDT','')}**\n"
                        f"  方向: {side} 入场: ${entry:,.2f}\n"
                        f"  自动监控已激活"),
                'dedup_key': f'{sym}_new_pos_{int(time.time()//300)}',
                'dedup_ttl': 300,
            })

        # 平仓感知
        closed_syms = set(prev_positions.keys()) - set(active.keys())
        for sym in closed_syms:
            prev = prev_positions.get(sym, {})
            upnl = prev.get('upnl', 0)
            result = '✅盈利' if upnl >= 0 else '❌亏损'
            alerts.append({
                'priority': 1,
                'type': 'POSITION_CLOSED',
                'msg': (f"📋 **仓位已平: {sym.replace('USDT','')}**\n"
                        f"  {result} | 最后PnL≈{upnl:+.2f}U"),
                'dedup_key': f'{sym}_closed_{int(time.time()//60)}',
                'dedup_ttl': 60,
            })

        # PnL 异常感知（亏损超5%）
        for sym, p in active.items():
            amt = float(p['positionAmt'])
            upnl = float(p['unRealizedProfit'])
            notional = abs(float(p.get('notional', 0)) or amt * float(p.get('markPrice', 0)))
            if notional > 0:
                pnl_pct = upnl / notional * 100
                prev_pnl_pct = state.get(f'{sym}_prev_pnl_pct', pnl_pct)
                state[f'{sym}_prev_pnl_pct'] = pnl_pct

                if pnl_pct <= -4.0 and prev_pnl_pct > -4.0:
                    side = 'LONG' if amt > 0 else 'SHORT'
                    alerts.append({
                        'priority': 0,
                        'type': 'POSITION_LOSS_ALERT',
                        'msg': (f"🔴 **{sym.replace('USDT','')} {side}仓亏损扩大**\n"
                                f"  PnL={upnl:+.2f}U ({pnl_pct:.1f}%)\n"
                                f"  梵天止损监控中 | 请确认SL设置"),
                        'dedup_key': f'{sym}_loss_alert_{int(time.time()//1800)}',
                        'dedup_ttl': 1800,
                    })

    except Exception as e:
        print(f'[NerveCenter] 持仓感知异常: {e}')
    return alerts


# ══════════════════════════════════════════════════════════════
# 感知模块5：信号失效感知
# ══════════════════════════════════════════════════════════════

def sense_signal_validity(state: dict) -> list:
    """
    信号有效性感知：
    - live_signal_log 中有效信号的价格偏离
    - 体制切换导致信号方向失效
    - 入场区间已被跳过（价格超出）
    """
    alerts = []
    try:
        sig_file = BASE / 'data' / 'live_signal_log.jsonl'
        if not sig_file.exists():
            return alerts

        lines = sig_file.read_text().strip().split('\n')
        now = time.time()

        for line in lines[-10:]:
            try:
                sig = json.loads(line)
                ts = sig.get('ts', 0)
                if now - ts > 6 * 3600:  # 6H前信号忽略
                    continue
                if not sig.get('valid'):
                    continue

                sym     = sig.get('symbol', '')
                d       = sig.get('direction', '')
                entry_lo = float(sig.get('entry_lo', 0) or 0)
                entry_hi = float(sig.get('entry_hi', 0) or 0)
                sl      = float(sig.get('sl_price', sig.get('sl', 0)) or 0)

                if not entry_lo or not sym:
                    continue

                # 获取当前价格
                price_data = _pub('/fapi/v1/ticker/price', {'symbol': sym})
                current = float(price_data.get('price', 0))
                if not current:
                    continue

                sig_id = sig.get('signal_id', sym)[:30]

                # 判断入场区间是否被跳过
                if d == 'LONG' and current > entry_hi * 1.03:
                    alerts.append({
                        'priority': 2,
                        'type': 'SIGNAL_MISSED',
                        'msg': (f"⏩ **信号入场区间已跳过**\n"
                                f"  {sym.replace('USDT','')} {d}\n"
                                f"  信号区间: ${entry_lo:.0f}~${entry_hi:.0f}\n"
                                f"  现价: ${current:.0f} (+{(current/entry_hi-1)*100:.1f}%)\n"
                                f"  建议: 等下一个回调信号"),
                        'dedup_key': f'{sig_id}_missed',
                        'dedup_ttl': 7200,
                    })
                elif d == 'SHORT' and current < entry_lo * 0.97:
                    alerts.append({
                        'priority': 2,
                        'type': 'SIGNAL_MISSED',
                        'msg': (f"⏩ **空单信号入场区间已跳过**\n"
                                f"  {sym.replace('USDT','')} {d}\n"
                                f"  现价: ${current:.0f} 低于入场区 ${entry_lo:.0f}"),
                        'dedup_key': f'{sig_id}_missed_short',
                        'dedup_ttl': 7200,
                    })

            except Exception:
                continue

    except Exception as e:
        print(f'[NerveCenter] 信号感知异常: {e}')
    return alerts


# ══════════════════════════════════════════════════════════════
# 汇总推送
# ══════════════════════════════════════════════════════════════

def _get_noise_level() -> int:
    """
    OPT-B: 读取体制噪音等级（设计院优化 2026-07-03）
    BTC switch24h > 40 → 高噪音模式，延长dedup_ttl
    返回: 0=低噪音 1=中噪音 2=高噪音
    """
    try:
        reg = json.loads((BASE / 'data' / 'regime_state.json').read_text())
        sw = reg.get('BTCUSDT', {}).get('switch_count_24h', 0)
        if sw > 50: return 2
        if sw > 30: return 1
    except Exception:
        pass
    return 0


def _get_active_position_syms() -> set:
    """获取当前持仓标的（用于改造C：持仓相关推送提权）"""
    try:
        pos_file = BASE / 'data' / 'wuqu_positions.json'
        if pos_file.exists():
            pos = json.loads(pos_file.read_text())
            if isinstance(pos, list):
                return {p['symbol'] for p in pos if float(p.get('positionAmt', 0)) != 0}
            return {k for k, v in pos.items() if v.get('active')}
    except Exception:
        pass
    return set()


def _is_event_sustained(state: dict, event_key: str, price: float,
                        price_threshold_pct: float = 1.5) -> bool:
    """
    改造B: 判断事件是否为「持续状态」而非「新事件」
    - 若已在 active_events 中 且 价格变化 < threshold → 持续中 → 静默
    - 若价格变化 >= threshold → 视为新阶段 → 允许推送
    返回 True = 持续中（应静默）
    """
    active = state.setdefault('active_events', {})
    now = time.time()
    if event_key in active:
        prev = active[event_key]
        prev_price = prev.get('price', price)
        age = now - prev.get('ts', 0)
        chg = abs(price - prev_price) / prev_price * 100
        if chg < price_threshold_pct and age < 14400:  # 4H内价格变化<1.5% = 持续
            return True  # 静默
        else:
            # 价格变化超阈值 → 更新记录，视为新事件
            active[event_key] = {'ts': now, 'price': price}
            return False
    else:
        # 首次触发
        active[event_key] = {'ts': now, 'price': price}
        return False


def _format_and_push(all_alerts: list, state: dict = None):
    """按优先级分组推送（改造B/C：事件去重 + 持仓提权）"""
    if not all_alerts:
        return

    state = state or {}
    position_syms = _get_active_position_syms()

    # OPT-B: 噪音等级 → 动态调整 P1 dedup_ttl
    noise = _get_noise_level()
    noise_mult = {0: 1, 1: 2, 2: 3}[noise]

    # 分组
    p0 = [a for a in all_alerts if a['priority'] == 0]
    p1_raw = [a for a in all_alerts if a['priority'] == 1]
    p2 = [a for a in all_alerts if a['priority'] == 2]

    # ── 改造B/C: P1 过滤 ─────────────────────────────────────
    p1_filtered = []
    p1_sustained = []
    for alert in p1_raw:
        sym = alert.get('symbol', '')
        event_key = alert.get('dedup_key', alert.get('type', 'unknown'))
        atype = alert.get('type', '')
        price = alert.get('price', 0.0)

        # 改造C: 非持仓标的的普通价格事件 → 降为P2静默
        if sym and sym not in position_syms and position_syms:
            non_pos_types = {'PRICE_SURGE', 'EMA_CROSS_UP'}
            if atype in non_pos_types:
                p2.append(alert)
                continue

        # 改造B: 检查是否为持续中的事件
        sustained_types = {'EMA_CROSS_UP', 'PRICE_SURGE'}
        if atype in sustained_types and price:
            if _is_event_sustained(state, event_key, price):
                p1_sustained.append(alert)
                continue

        p1_filtered.append(alert)

    # P0: 立即逐条推送
    for alert in p0:
        _push(alert['msg'], 0,
              alert.get('dedup_key'), alert.get('dedup_ttl', 3600))
        print(f'[NerveCenter] 🔴 P0推送: {alert["type"]}')

    # P1: 合并推送（过滤后，最多5条）
    if p1_filtered:
        lines = ['⚡ **梵天神经中枢 · 市场感知**', '']
        for a in p1_filtered[:5]:
            # 改造C: 持仓标的 → 附加RSI超买提示
            msg_body = a['msg']
            sym = a.get('symbol', '')
            if sym in position_syms:
                msg_body += '\n  📌 *持仓标的 | 关注止盈时机*'
            lines.append(msg_body)
            lines.append('')
        msg = '\n'.join(lines)
        dedup = f'nerve_p1_{int(time.time()//(300*noise_mult))}'
        pushed = _push(msg, 1, dedup, 300)
        if pushed:
            print(f'[NerveCenter] 🟡 P1推送: {len(p1_filtered)}条有效 '
                  f'(过滤持续中{len(p1_sustained)}条 降级{len(p2)-len([a for a in all_alerts if a["priority"]==2])}条)')
    elif p1_raw:
        print(f'[NerveCenter] 🟢 P1全部为持续事件或降级，静默 ({len(p1_raw)}条)')

    # P2: 静默记录
    for a in p2:
        print(f'[NerveCenter] 🟢 P2静默: {a["type"]} - {a["msg"][:50]}')


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════
# OPT-D: 清算集群 + GEX实时穿越感知（设计院优化 2026-07-03）
# ══════════════════════════════════════════════════════════════

def sense_liquidation_and_gex(state: dict) -> list:
    """清算热力图感知 + GEX MAX穿越感知"""
    alerts = []
    now = time.time()

    for sym in ['BTCUSDT', 'ETHUSDT']:
        short = sym.replace('USDT', '')
        try:
            liq = _pub('/fapi/v1/allForceOrders', {'symbol': sym, 'limit': 20})
            if isinstance(liq, list) and liq:
                recent_liq = [l for l in liq
                              if (now - l.get('time', 0) / 1000) < 300]
                if recent_liq:
                    total_usd = sum(
                        float(l.get('executedQty', 0)) * float(l.get('price', 0))
                        for l in recent_liq
                    )
                    long_liq  = sum(1 for l in recent_liq if l.get('side') == 'SELL')
                    short_liq = sum(1 for l in recent_liq if l.get('side') == 'BUY')
                    if total_usd >= LIQUIDATION_USD:
                        dom = '多头' if long_liq > short_liq else '空头'
                        tip = '多头踩踏' if long_liq > short_liq else '空头清算做多机会'
                        alerts.append({
                            'priority': 1,
                            'type': 'LIQUIDATION_SURGE',
                            'msg': (f"清算预警 {short} ${total_usd/1e6:.1f}M/5min"
                                    f" | {dom}主导 {tip}"),
                            'dedup_key': f'{sym}_liq_{int(now // 300)}',
                            'dedup_ttl': 300,
                        })
        except Exception:
            pass

        try:
            gex_file = BASE / 'data' / f'gex_{sym.lower()}.json'
            if gex_file.exists():
                gex = json.loads(gex_file.read_text())
                gex_max  = float(gex.get('gamma_max', gex.get('gex_max', 0)) or 0)
                gex_flip = float(gex.get('zero_flip', gex.get('gex_flip', 0)) or 0)
                if gex_max > 0:
                    price_data = _pub('/fapi/v1/ticker/price', {'symbol': sym})
                    price = float(price_data.get('price', 0))
                    prev_price = state.get(f'{sym}_prev_price', price)
                    prev_above = prev_price > gex_max * 1.005
                    is_above   = price > gex_max * 1.005
                    prev_below = prev_price < gex_max * 0.995
                    is_below   = price < gex_max * 0.995
                    if prev_below and is_above:
                        alerts.append({
                            'priority': 1,
                            'type': 'GEX_MAX_BREAK',
                            'msg': (f"GEX突破 {short} MAX=${gex_max:,.0f}"
                                    f" | 伽马挤压助涨 ZeroFlip={gex_flip:,.0f}"),
                            'dedup_key': f'{sym}_gex_up_{int(gex_max)}',
                            'dedup_ttl': 7200,
                        })
                    elif prev_above and is_below:
                        alerts.append({
                            'priority': 1,
                            'type': 'GEX_MAX_BREAK_DOWN',
                            'msg': (f"GEX跌破 {short} MAX={gex_max:,.0f}"
                                    f" | 伽马支撑失效 助跌风险"),
                            'dedup_key': f'{sym}_gex_dn_{int(gex_max)}',
                            'dedup_ttl': 7200,
                        })
        except Exception:
            pass

    return alerts


def sense_order_anomaly(state: dict) -> list:
    """P0加固：挂单异常检测（设计院 2026-07-03）"""
    alerts = []
    try:
        orders = _signed('/fapi/v1/openOrders')
        if not isinstance(orders, list) or not orders:
            return alerts
        open_orders = [o for o in orders if not o.get('reduceOnly', False)]
        from collections import defaultdict
        by_sym = defaultdict(list)
        for o in open_orders:
            by_sym[o['symbol']].append(o)
        for sym, sym_orders in by_sym.items():
            if len(sym_orders) > 3:  # >3张即告警
                total_notional = sum(
                    float(o.get('origQty',0)) * float(o.get('price',0) or 0)
                    for o in sym_orders
                )
                alerts.append({
                    'priority': 0,
                    'type': 'ORDER_ANOMALY',
                    'symbol': sym,
                    'msg': (f'🚨 {sym} 挂单异常：{len(sym_orders)}张未成交开仓单'
                            f'（名义合计${total_notional:.0f}）\n'
                            f'建议立即检查，防止全部成交导致超仓'),
                    'action': 'CHECK_ORDERS',
                })
                print(f'[NerveCenter] 🚨 {sym} 挂单异常: {len(sym_orders)}张')
    except Exception as e:
        print(f'[NerveCenter] 挂单检测异常: {e}')
    return alerts


def main():
    print(f'[NerveCenter] 启动 {datetime.utcnow().strftime("%H:%M UTC")}')
    state = _load_state()
    all_alerts = []

    # 依次运行各感知模块
    all_alerts += sense_btc_regime_change(state)
    all_alerts += sense_price_structure(state)
    all_alerts += sense_market_microstructure(state)
    all_alerts += sense_position_lifecycle(state)
    all_alerts += sense_signal_validity(state)
    all_alerts += sense_liquidation_and_gex(state)  # OPT-D
    all_alerts += sense_order_anomaly(state)         # P0加固 2026-07-03

    # 保存状态
    state['last_run'] = time.time()
    _save_state(state)

    # 推送
    _format_and_push(all_alerts, state)

    p0 = len([a for a in all_alerts if a['priority'] == 0])
    p1 = len([a for a in all_alerts if a['priority'] == 1])
    print(f'[NerveCenter] 完成 | 感知结果: P0={p0} P1={p1} 总={len(all_alerts)}')


if __name__ == '__main__':
    main()


# ══════════════════════════════════════════════════════════════
# OPT-A: 感知事件持久化日志（设计院优化 2026-07-03）
# 解决：历史追溯 2/10 → 可回溯全天感知事件
# ══════════════════════════════════════════════════════════════

EVENT_LOG = BASE / 'logs' / 'nerve_event_log.jsonl'

def _log_event(event_type: str, symbol: str, detail: str, priority: int, pushed: bool):
    """将感知事件写入持久化日志"""
    EVENT_LOG.parent.mkdir(exist_ok=True)
    record = {
        'ts': round(time.time(), 1),
        'ts_iso': datetime.utcnow().strftime('%m-%d %H:%M UTC'),
        'type': event_type,
        'symbol': symbol,
        'detail': detail[:200],
        'priority': priority,
        'pushed': pushed,
    }
    with open(EVENT_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def get_event_summary(hours: int = 24) -> dict:
    """读取最近N小时感知事件汇总（用于日报/复盘）"""
    if not EVENT_LOG.exists():
        return {'total': 0, 'by_type': {}, 'pushed': 0}
    since = time.time() - hours * 3600
    events = []
    for line in EVENT_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get('ts', 0) >= since:
                events.append(r)
        except Exception:
            pass
    by_type = {}
    pushed = 0
    for e in events:
        t = e.get('type', '?')
        by_type[t] = by_type.get(t, 0) + 1
        if e.get('pushed'):
            pushed += 1
    return {'total': len(events), 'by_type': by_type, 'pushed': pushed, 'events': events}
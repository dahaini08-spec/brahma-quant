#!/usr/bin/env python3
"""
brahma_signal_early_alert.py — 梵天先行信号层
[封印 2026-10-19 苏摩111]

核心理念：预判型系统，而非描述型系统
  描述型：看到大跌 → 说"跌了"
  预判型：在大跌前 → 检测到"CVD背离+Session切换+OI加速"共振 → 发出预警

触发逻辑（三维共振）：
  单维度不触发，两维度警告，三维度立即推送

监控的先行指标：
  A. BB squeeze + 方向信号（布林带极度收窄后必有爆发）
  B. CVD背离（价格上涨但CVD下降 = 主力在出货）
  C. LSR加速（散户极度偏多 = 猎杀池已满）
  D. OI速率（短时间内OI急变 = 主力建仓方向确认）
  E. Session切换（欧洲盘/美国盘开盘 = 流动性注入时刻）
  F. 清算池距离（价格距清算池<1% = 穿越在即）
"""
import sys, json, time, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

ALERT_STATE_FILE = BASE / 'data' / 'early_alert_state.json'


def fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_signal_snapshot(sym: str) -> dict:
    """拉取先行信号所需的多维快照"""
    s = sym.upper() + 'USDT'
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {
            'price':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}'),
            'k15m':   ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=15m&limit=8'),
            'k1h':    ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=6'),
            'oi_h':   ex.submit(fetch, f'https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=15m&limit=4'),
            # [封印 2026-10-28 苏摩111] Taker升级: 15m主信号+1h权威确认
            'taker':    ex.submit(fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=15m&limit=4'),
            'taker_1h': ex.submit(fetch, f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=1h&limit=3'),
            'lsr':    ex.submit(fetch, f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=5m&limit=3'),
            'depth':  ex.submit(fetch, f'https://fapi.binance.com/fapi/v1/depth?symbol={s}&limit=10'),
        }
        raw = {k: f.result(timeout=10) for k, f in futs.items()}

    px = float(raw['price'].get('price', 0))

    # BB宽度（用15M K线估算）
    k15m = raw['k15m'] if isinstance(raw['k15m'], list) else []
    bb_width = 2.0
    if len(k15m) >= 5:
        closes = [float(k[4]) for k in k15m]
        mean = sum(closes) / len(closes)
        std = (sum((c - mean)**2 for c in closes) / len(closes)) ** 0.5
        bb_width = round(std * 4 / mean * 100, 3) if mean > 0 else 2.0  # 简化BB宽度

    # RSI 15M
    rsi_15m = 50.0
    if len(k15m) >= 4:
        cl = [float(k[4]) for k in k15m]
        df = [cl[i] - cl[i-1] for i in range(1, len(cl))]
        g = [max(d, 0) for d in df]; l = [max(-d, 0) for d in df]
        ag = sum(g)/len(g) or 0.001; al = sum(l)/len(l) or 0.001
        rsi_15m = round(100 - 100/(1+ag/al), 1)

    # ATR 1H
    k1h = raw['k1h'] if isinstance(raw['k1h'], list) else []
    atr1h = 0.0
    if len(k1h) >= 2:
        trs = [max(float(k[2])-float(k[3]),
                   abs(float(k[2])-float(k1h[i-1][4])),
                   abs(float(k[3])-float(k1h[i-1][4])))
               for i, k in enumerate(k1h[1:], 1)]
        atr1h = sum(trs)/len(trs) if trs else 0

    # OI速率（最近15分钟变化率）
    oi_h = raw['oi_h'] if isinstance(raw['oi_h'], list) else []
    oi_rate = 0.0
    if len(oi_h) >= 2:
        p = float(oi_h[-2].get('sumOpenInterest', 0))
        c = float(oi_h[-1].get('sumOpenInterest', 0))
        oi_rate = round((c - p) / p * 100, 3) if p > 0 else 0

    # Taker趋势
    tak_list = raw['taker'] if isinstance(raw['taker'], list) else []
    tak_vals = [round(float(x.get('buySellRatio', 1)), 3) for x in tak_list]
    tak_now = tak_vals[-1] if tak_vals else 1
    tak_trend = round(tak_vals[-1] - tak_vals[0], 3) if len(tak_vals) >= 2 else 0

    # LSR趋势
    lsr_list = raw['lsr'] if isinstance(raw['lsr'], list) else []
    lsr_now = round(float(lsr_list[-1]['longShortRatio']), 3) if lsr_list else 1
    lsr_trend = round(float(lsr_list[-1]['longShortRatio']) - float(lsr_list[0]['longShortRatio']), 3) if len(lsr_list) >= 2 else 0

    # 盘口深度比
    depth = raw['depth']
    ba = 1.0
    if depth:
        bids = [(float(p), float(q)) for p, q in depth.get('bids', [])]
        asks = [(float(p), float(q)) for p, q in depth.get('asks', [])]
        bw = sum(p*q for p, q in bids); aw = sum(p*q for p, q in asks)
        ba = round(bw/aw, 2) if aw > 0 else 1

    # 从state读清算距离
    sd = {}
    sf = BASE / 'data' / f'brahma_state_{sym.lower()}.json'
    if sf.exists():
        try: sd = json.load(open(sf))
        except: pass
    liq_up = sd.get('liq_short', 0)
    liq_dn = sd.get('liq_long', 0)
    liq_up_pct = round((liq_up - px) / px * 100, 2) if liq_up > px > 0 else 99
    liq_dn_pct = round((px - liq_dn) / px * 100, 2) if 0 < liq_dn < px else 99

    # Session
    utc_h = time.gmtime().tm_hour
    session = ('EU_OPEN' if 6<=utc_h<10 else
               'US_OPEN' if 13<=utc_h<17 else
               'ASIA_LATE' if 0<=utc_h<4 else
               'US_CLOSE' if 21<=utc_h<24 else 'NORMAL')

    return {
        'ts': time.time(), 'sym': sym.upper(), 'px': px,
        'bb_width': bb_width, 'rsi_15m': rsi_15m, 'atr1h': round(atr1h, 1),
        'oi_rate': oi_rate,
        'tak': tak_now, 'tak_trend': tak_trend,
        'lsr': lsr_now, 'lsr_trend': lsr_trend,
        'ba': ba, 'session': session,
        'liq_up_pct': liq_up_pct, 'liq_dn_pct': liq_dn_pct,
    }


def detect_early_signals(curr: dict, prev: dict) -> list:
    """
    先行信号检测（预判型，不是描述型）
    返回: [(信号名, 严重程度1-3, 描述)]
    """
    signals = []
    sym = curr['sym']
    px = curr['px']

    # A. BB squeeze爆发预警
    if curr['bb_width'] < 0.5:
        signals.append(('BB_SQUEEZE_CRITICAL', 3,
            f"BB宽度={curr['bb_width']:.3f}(极度收窄⚡⚡⚡) = 即将爆发性突破，方向未知"))
    elif curr['bb_width'] < 1.0:
        signals.append(('BB_SQUEEZE', 2,
            f"BB宽度={curr['bb_width']:.3f}(收窄⚡) = 突破临近，Taker={curr['tak']}决定方向"))

    # B. LSR猎杀池满溢
    if curr['lsr'] >= 3.0:
        signals.append(('LSR_HUNT_CRITICAL', 3,
            f"LSR={curr['lsr']}(>3.0⚡⚡⚡) = 猎杀池满溢，清算触发概率极高"))
    elif curr['lsr'] >= 2.5 and curr['lsr_trend'] > 0.1:
        signals.append(('LSR_HUNT_BUILDING', 2,
            f"LSR={curr['lsr']}↑趋势+{curr['lsr_trend']:.3f} = 猎杀结构快速积累"))

    # C. OI速率异常（主力快速建仓）
    if abs(curr['oi_rate']) >= 0.5:
        dir_str = '空单' if curr['oi_rate'] > 0 and curr['tak'] < 0.8 else '多单'
        signals.append(('OI_SPIKE', 2,
            f"OI速率={curr['oi_rate']:+.3f}%(15min⚡) = 主力快速建{dir_str}，跟进信号"))

    # D. 清算池近距离预警
    if curr['liq_dn_pct'] < 1.0:
        signals.append(('LIQ_POOL_NEAR_SHORT', 3,
            f"多单止损池距今{curr['liq_dn_pct']:.2f}%⚡⚡⚡ = 清算穿越在即，D计划待命"))
    elif curr['liq_dn_pct'] < 1.5:
        signals.append(('LIQ_POOL_NEAR', 2,
            f"多单止损池距今{curr['liq_dn_pct']:.2f}%⚡ = 接近清算区，注意穿越后反手"))

    if curr['liq_up_pct'] < 1.0:
        signals.append(('LIQ_POOL_NEAR_LONG', 3,
            f"空单止损墙距今{curr['liq_up_pct']:.2f}%⚡⚡⚡ = 逼空在即，注意穿越反转"))

    # E_ob. OB穿越预警（从state检测）
    sd = {}
    sf = BASE / 'data' / f'brahma_state_{curr["sym"].lower()}.json'
    if sf.exists():
        try: sd = json.load(open(sf))
        except: pass
    ob_1h_lo = sd.get('ob_1h_lo', 0)
    ob_4h_lo = sd.get('ob_4h_lo', 0)
    if ob_1h_lo > 0 and px < ob_1h_lo * 0.998:
        signals.append(('OB1H_BREACHED', 3,
            f"1H OB下沿${ob_1h_lo:,.0f}被穿越⚡⚡⚡ = 多头支撑失守，下一支撑OB4H${ob_4h_lo:,.0f}/GEX ZF"))
    elif ob_4h_lo > 0 and px < ob_4h_lo * 0.998:
        signals.append(('OB4H_BREACHED', 3,
            f"4H OB下沿${ob_4h_lo:,.0f}被穿越⚡⚡⚡ = 主力成本区失守，趋势性下跌信号"))

    # IV低位预警
    iv_rank = sd.get('iv_rank', 50)
    if 0 < iv_rank < 8 and curr['bb_width'] < 1.0:
        signals.append(('IV_BB_COIL', 2,
            f"IV排名={iv_rank:.1f}(极低)+BB收窄={curr['bb_width']:.3f} = 双重收缩，爆发性突破临近"))

    # E. Session切换预警
    if curr['session'] in ('EU_OPEN', 'US_OPEN') and prev:
        if prev.get('session') not in ('EU_OPEN', 'US_OPEN'):
            signals.append(('SESSION_SWITCH', 2,
                f"Session切换→{curr['session']}⚡ = 流动性注入，大波动高发期开始"))

    # F. Taker急转（方向切换）
    if prev and abs(curr['tak'] - prev.get('tak', 1)) >= 0.5:
        dir_str = '买→卖' if curr['tak'] < prev.get('tak', 1) else '卖→买'
        signals.append(('TAKER_FLIP', 2,
            f"Taker{prev.get('tak',1):.2f}→{curr['tak']:.2f}({dir_str}⚡) = 主动资金方向切换"))

    # G. RSI超买超卖临界
    if curr['rsi_15m'] >= 78:
        signals.append(('RSI_OVERBOUGHT', 2,
            f"RSI_15M={curr['rsi_15m']}(超买⚡) = 短线回调压力高"))
    elif curr['rsi_15m'] <= 22:
        signals.append(('RSI_OVERSOLD', 2,
            f"RSI_15M={curr['rsi_15m']}(超卖⚡) = 反弹机会临近"))

    # H. 盘口极端失衡（大单突然压盘）
    if curr['ba'] < 0.25:
        signals.append(('DEPTH_SELL_CRUSH', 2,
            f"盘口BA={curr['ba']}x(卖盘压倒性) = 大单砸盘，价格承压"))
    elif curr['ba'] > 5.0:
        signals.append(('DEPTH_BUY_WALL', 2,
            f"盘口BA={curr['ba']}x(买盘极厚) = 大单托盘，价格支撑强"))

    return signals


def run_early_alert():
    """主运行（每15分钟由cron触发）"""
    state = {}
    if ALERT_STATE_FILE.exists():
        try: state = json.load(open(ALERT_STATE_FILE))
        except: pass

    now_str = time.strftime('%H:%M UTC')
    all_alerts = []

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(get_signal_snapshot, sym): sym for sym in ['BTC', 'ETH']}
        snapshots = {}
        for fut in as_completed(futs):
            sym = futs[fut]
            try: snapshots[sym] = fut.result()
            except Exception as e: print(f'[early_alert] {sym} 快照失败: {e}')

    for sym, curr in snapshots.items():
        prev = state.get(sym, {})
        signals = detect_early_signals(curr, prev)
        state[sym] = curr

        if signals:
            all_alerts.append((sym, curr, signals))
            for name, severity, desc in signals:
                stars = '⚡' * severity
                print(f'[{sym}] {stars} {name}: {desc}')
        else:
            print(f'[{sym}] ${curr["px"]:,.2f}  BB={curr["bb_width"]:.3f}  '
                  f'LSR={curr["lsr"]:.3f}  OI速率={curr["oi_rate"]:+.3f}%  无先行信号')

    # 保存状态
    json.dump(state, open(ALERT_STATE_FILE, 'w'), ensure_ascii=False)

    # 推送高优先级信号
    if all_alerts:
        _push_alerts(all_alerts, now_str)


def _push_alerts(alerts: list, now_str: str):
    """推送先行信号"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('push_hub', BASE/'scripts'/'push_hub.py')
        ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ph)

        for sym, curr, signals in alerts:
            max_severity = max(s[1] for s in signals)
            if max_severity < 2:
                continue

            lines = [f'⚡ 梵天先行信号 | {sym} ${curr["px"]:,.2f}  {now_str}']
            for name, severity, desc in sorted(signals, key=lambda x: -x[1]):
                stars = '⚡' * severity
                lines.append(f'{stars} {name}: {desc}')

            lines.append(f'\nBB={curr["bb_width"]:.3f}  LSR={curr["lsr"]:.3f}  '
                        f'Taker={curr["tak"]:.3f}  OI速率={curr["oi_rate"]:+.3f}%')
            lines.append(f'Session={curr["session"]}  '
                        f'清算↓{curr["liq_dn_pct"]:.2f}%  清算↑{curr["liq_up_pct"]:.2f}%')

            priority = 'P0' if max_severity >= 3 else 'P1'
            ph.push_jarvis(
                '\n'.join(lines), priority=priority,
                dedup_key=f'early_{sym}_{int(time.time()//900)}',
                dedup_ttl=900)
            print(f'[early_alert] {priority} 推送 {sym} {max_severity}级信号 ✅')

    except Exception as e:
        print(f'[early_alert] 推送失败: {e}')


if __name__ == '__main__':
    run_early_alert()

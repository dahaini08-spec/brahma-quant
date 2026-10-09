#!/usr/bin/env python3
"""
M7: VIP策略状态监控
2026-10-09 苏摩111封印

关键维度变化时主动推送，苏摩不用人工分析也能知道策略变了

监控5个维度：
① FR突破警戒线（+0.003% 封锁 / +0.006% 做空预警）
② LSR接近/触发猎杀门槛（BTC 65% / ETH 77%）
③ CVD极端翻转（正→负 < -2000，方向反转信号）
④ OI结构转变（NEUTRAL→UNWIND，多头减仓）
⑤ VIP作废条件触发（BTC<$81K / ETH<$2,413）

由 trading_copilot.py 每分钟调用
"""
import json, pathlib, time, sys, os, ssl, urllib.request

BASE  = pathlib.Path(__file__).parent.parent
DATA  = BASE / 'data'
STATE_FILE = DATA / 'copilot_state.json'
COOLDOWN   = 1800  # 同类信号30分钟内只推一次

# VIP策略关键位（与当前活跃策略保持一致）
VIP_CONFIG = {
    'BTC': {
        'long_entry':  (82000, 82500),
        'long_sl':      81387,
        'long_tp':      84709,
        'short_entry': (83800, 84500),
        'short_sl':     85200,
        'short_tp':     81387,
        'hunt_lsr':     65.0,
        'invalidate_long': 81000,
    },
    'ETH': {
        'long_entry':  (2449, 2465),
        'long_sl':      2413,
        'long_tp':      2549,
        'short_entry': (2535, 2549),
        'short_sl':     2582,
        'short_tp':     2442,
        'hunt_lsr':     77.0,
        'invalidate_long': 2413,
        'gex_zf':       2473,  # GEX ZeroFlip防线
    },
}

ctx = ssl.create_default_context()

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
        return json.loads(r.read())

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {}

def save_state(s):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def check_all() -> list:
    """返回 [(state_key, message), ...] 列表"""
    state   = load_state()
    alerts  = []
    now     = time.time()

    for sym in ['BTC', 'ETH']:
        sf  = f'{sym}USDT'
        cfg = VIP_CONFIG[sym]

        # ── 拉价格 ────────────────────────────────────
        try:
            price = float(fetch_json(
                f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sf}'
            )['price'])
        except Exception as e:
            print(f'[M7] {sym} price fail: {e}', file=sys.stderr)
            continue

        # ── 拉FR ──────────────────────────────────────
        try:
            fr_v = float(fetch_json(
                f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sf}&limit=1'
            )[0]['fundingRate']) * 100
        except:
            fr_v = 0.0

        # ── 拉LSR ─────────────────────────────────────
        try:
            lsr_v = float(fetch_json(
                f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
                f'?symbol={sf}&period=5m&limit=1'
            )[0]['longAccount']) * 100
        except:
            lsr_v = 50.0

        # ── 拉CVD ─────────────────────────────────────
        try:
            cvd_d = json.loads((DATA / f'cvd_realtime_{sym.lower()}usdt.json').read_text())
            cvd_v = float(cvd_d.get('cvd_1h', cvd_d.get('delta_1h', cvd_d.get('cvd', 0))) or 0)
        except:
            cvd_v = 0.0

        # ── 拉OI ──────────────────────────────────────
        try:
            oi_data = fetch_json(
                f'https://fapi.binance.com/futures/data/openInterestHist'
                f'?symbol={sf}&period=1h&limit=3'
            )
            oi_vals = [float(o['sumOpenInterest']) for o in oi_data]
            oi_chg  = (oi_vals[-1] - oi_vals[0]) / oi_vals[0] * 100 if oi_vals[0] > 0 else 0
            oi_dir  = 'UNWIND' if oi_chg < -0.5 else ('BUILD' if oi_chg > 0.5 else 'NEUTRAL')
        except:
            oi_dir = 'NEUTRAL'; oi_chg = 0.0

        hunt = cfg['hunt_lsr']
        dist = hunt - lsr_v

        print(f'[M7] {sym} ${price:,.2f} FR={fr_v:+.4f}% LSR={lsr_v:.1f}% CVD={cvd_v:.0f} OI={oi_dir}({oi_chg:+.2f}%)')

        # ══════════════════════════════════════
        # ① FR 警戒/预警
        # ══════════════════════════════════════
        if fr_v > 0.006:
            k = f'm7_fr_alert_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'🔴 梵天VIP警报 | {sym} FR做空预警\n\n'
                    f'FR={fr_v:+.4f}% 突破+0.006%\n'
                    f'= 多头极度拥挤，主力清洗即将启动\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 VIP空单准备：\n'
                    f'入场 ${cfg["short_entry"][0]:,}~${cfg["short_entry"][1]:,}\n'
                    f'止损 ${cfg["short_sl"]:,} | 目标 ${cfg["short_tp"]:,}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))
        elif fr_v > 0.003:
            k = f'm7_fr_warn_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'⚠️ 梵天VIP提醒 | {sym} FR警戒线\n\n'
                    f'FR={fr_v:+.4f}% 突破+0.003%\n'
                    f'= 铁律②触发：{sym}多单入场封锁\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'✋ 暂停一切{sym}多单\n'
                    f'等FR回落<+0.003%后再入场\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ══════════════════════════════════════
        # ② LSR 猎杀预警/触发
        # ══════════════════════════════════════
        if dist <= 0:
            k = f'm7_lsr_hunt_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'🔴 梵天VIP警报 | {sym} 散户猎杀触发！\n\n'
                    f'散户LSR={lsr_v:.1f}% ≥ 门槛{hunt:.0f}%\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 VIP空单立即准备：\n'
                    f'入场 ${cfg["short_entry"][0]:,}~${cfg["short_entry"][1]:,}\n'
                    f'止损 ${cfg["short_sl"]:,} | 目标 ${cfg["short_tp"]:,}\n'
                    f'等FR同步转正 + 1H收阴最终确认\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))
        elif dist <= 1.5:
            k = f'm7_lsr_near_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'⚠️ 梵天VIP预警 | {sym} 猎杀门槛逼近\n\n'
                    f'散户LSR={lsr_v:.1f}% 距{hunt:.0f}%仅差{dist:.1f}%\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 空单提前挂好：\n'
                    f'${cfg["short_entry"][0]:,}~${cfg["short_entry"][1]:,} SL${cfg["short_sl"]:,}\n'
                    f'等FR转正+1H收阴确认入场\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ══════════════════════════════════════
        # ③ CVD 极端翻转
        # ══════════════════════════════════════
        cvd_prev = state.get(f'm7_cvd_prev_{sym}', 0)
        state[f'm7_cvd_prev_{sym}'] = cvd_v

        if cvd_prev > 500 and cvd_v < -2000:
            k = f'm7_cvd_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                impact = (
                    f'BTC多单暂缓，等CVD回正后再入' if sym == 'BTC'
                    else f'ETH回踩可能加速，等${cfg["long_entry"][0]:,}~${cfg["long_entry"][1]:,}接多'
                )
                alerts.append((k,
                    f'⚠️ 梵天VIP提醒 | {sym} CVD极端翻转\n\n'
                    f'CVD: {cvd_prev:+.0f} → {cvd_v:+.0f}\n'
                    f'= 买方→卖方极端转变\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 策略影响：{impact}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ══════════════════════════════════════
        # ④ OI 结构转变 NEUTRAL→UNWIND
        # ══════════════════════════════════════
        oi_prev = state.get(f'm7_oi_prev_{sym}', 'NEUTRAL')
        state[f'm7_oi_prev_{sym}'] = oi_dir

        if oi_prev == 'NEUTRAL' and oi_dir == 'UNWIND':
            k = f'm7_oi_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                impact = (
                    f'BTC多单暂缓，上涨动能衰减' if sym == 'BTC'
                    else f'ETH回踩可能更深，${cfg["long_entry"][0]:,}~${cfg["long_entry"][1]:,}守候'
                )
                alerts.append((k,
                    f'⚠️ 梵天VIP提醒 | {sym} OI结构转变\n\n'
                    f'OI: NEUTRAL → UNWIND（{oi_chg:.2f}%）\n'
                    f'= 多头在减仓，上涨动能衰减\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 策略影响：{impact}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ══════════════════════════════════════
        # ⑤ VIP作废条件触发
        # ══════════════════════════════════════
        inv_price = cfg.get('invalidate_long', 0)
        if inv_price and price < inv_price:
            k = f'm7_invalidate_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'🚫 梵天VIP作废通知 | {sym}\n\n'
                    f'价格 ${price:,.2f} 跌破${inv_price:,}（止损位）\n'
                    f'= 所有{sym}多单方案全部作废\n\n'
                    f'等待新结构重新确认\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ⑤-B ETH专属：GEX ZeroFlip失守
        if sym == 'ETH' and 'gex_zf' in cfg and price < cfg['gex_zf']:
            k = f'm7_gex_zf_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                alerts.append((k,
                    f'⚠️ 梵天VIP提醒 | ETH GEX ZeroFlip失守\n\n'
                    f'价格 ${price:,.2f} 跌破GEX ZeroFlip ${cfg["gex_zf"]:,}\n'
                    f'= 进入负GEX区，做市商转为做空对冲\n'
                    f'= 波动放大风险上升\n\n'
                    f'📌 策略影响：\n'
                    f'等${cfg["long_entry"][0]:,}~${cfg["long_entry"][1]:,}区间再接多\n'
                    f'SL ${cfg["long_sl"]:,} | TP ${cfg["long_tp"]:,}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

    # 保存状态（含cvd_prev/oi_prev更新）
    save_state(state)
    return alerts


if __name__ == '__main__':
    # 独立冒烟测试
    alerts = check_all()
    print(f'\nM7 检查完成，触发 {len(alerts)} 条警报')
    for k, msg in alerts:
        print(f'\n[{k}]\n{msg}')

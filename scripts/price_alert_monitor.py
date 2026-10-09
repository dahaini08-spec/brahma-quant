#!/usr/bin/env python3
"""
价位触发推送监控 — 苏摩复盘修复版
当价格接近关键结构位时立即推送到Jarvis
2026-10-09 苏摩批评后紧急修复
"""
import time, json, os, sys, pathlib, urllib.request, ssl, subprocess

# ── 配置 ──
JARVIS_USER  = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed')
CHANNEL = 'jarvis'
WORKDIR = pathlib.Path('/root/.openclaw/workspace/trading-system')
STATE_FILE = WORKDIR / 'data' / 'price_alert_state.json'
CHECK_INTERVAL = 60  # 每60秒检查一次
PROXIMITY_PCT = 0.003  # 价格距关键位0.3%内触发

ctx = ssl.create_default_context()

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
        return json.loads(r.read())

def push_jarvis(msg):
    """通过openclaw CLI推送到Jarvis"""
    target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
    cmd = ['openclaw', 'message', 'send', '--channel', CHANNEL, '--to', target, '--message', msg]
    try:
        subprocess.run(cmd, timeout=10, check=True, capture_output=True)
        print(f'[PUSH OK] {msg[:80]}')
    except Exception as e:
        print(f'[PUSH ERR] {e}')

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except:
        return {}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False))

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

def get_gex_key(sym):
    try:
        g = json.loads((WORKDIR/'data'/'gex_state.json').read_text())
        sg = g.get(sym.upper(),{}).get('top_strikes',{})
        if sym=='BTC':
            return float(sg.get('80000',0)), float(sg.get('90000',0))
        else:
            return float(sg.get('2500',0)), 0.0
    except:
        return 0.0, 0.0

def near(price, target, pct=PROXIMITY_PCT):
    if target == 0: return False
    return abs(price - target) / target <= pct

def check_and_alert(state):
    alerts = []
    for sym in ['BTC','ETH']:
        p = get_price(sym)
        if p == 0: continue
        liq_s, liq_l = get_liq(sym)
        gk1, gk2 = get_gex_key(sym)

        key_levels = {}
        if sym == 'BTC':
            key_levels = {
                'liq_short_83493': (liq_s, 'SHORT_LIQ',
                    f'🔴 BTC止损墙触及！\n入场空单 ${liq_s:,.0f}\n止损 $84,900 | 目标 $80,219\n条件：1H收阴+CVD转负'),
                'liq_long_80219': (liq_l, 'LONG_LIQ',
                    f'🟢 BTC支撑池触及！\n入场多单 ${liq_l:,.0f}~${liq_l+300:,.0f}\n止损 $78,800 | 目标 $83,493\nGEX-30.4M托底机械支撑'),
                'gex_80000': (80000.0, 'GEX_SUPPORT',
                    f'🟢 BTC GEX-30.4M强支撑区！\n${p:,.0f}进入做市商被迫买入区\n多单入场 $79,700~$80,200\n止损 $78,800'),
                'break_83000': (83000.0, 'BREAK_UP',
                    f'⚡ BTC突破$83,000！\n向止损墙$83,493冲击\n空单准备区 $83,200~$83,493'),
            }
        else:  # ETH
            key_levels = {
                'liq_short_2526': (liq_s, 'SHORT_LIQ',
                    f'🔴 ETH止损墙触及！\n入场空单 ${liq_s:,.0f}\n止损 $2,560 | 目标 $2,427→$2,405\n散户77%猎杀区'),
                'liq_long_2427': (liq_l, 'LONG_LIQ',
                    f'🟢 ETH支撑池触及！\n入场多单 ${liq_l:,.0f}~${liq_l+20:,.0f}\n止损 $2,368 | 目标 $2,526'),
                'gex_2500': (2500.0, 'GEX_KEY',
                    f'⚡ ETH触及GEX-9.8M关卡$2,500！\n突破+CVD转正 → 多单\n未突破+1H收阴 → 空单\n今日决战位'),
                'lsr_extreme': (0, 'LSR_CHECK', ''),  # 特殊处理
            }

        for level_key, (target, level_type, msg) in key_levels.items():
            if target == 0 or not msg: continue
            state_key = f'{sym}_{level_key}'
            last_alert = state.get(state_key, 0)
            cooldown = 3600  # 同一价位1小时内不重复推送

            if near(p, target) and (time.time() - last_alert) > cooldown:
                full_msg = f'🏛️ 梵天价位警报 {sym}\n当前价 ${p:,.2f}\n\n{msg}\n\n🌿 姓赵不宣 | 不是建议'
                alerts.append((state_key, full_msg))

    # LSR猎杀门槛检查（BTC散户>65%）
    try:
        lsr_b = fetch('https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1')
        lsr_val = float(lsr_b[0]['longAccount'])*100
        key = 'BTC_lsr_65pct'
        last = state.get(key, 0)
        btc_p = get_price('BTC')
        if lsr_val >= 65.0 and (time.time()-last) > 3600:
            msg = (f'🚨 BTC散户LSR={lsr_val:.1f}%触发猎杀门槛！\n当前价 ${btc_p:,.0f}\n\n'
                   f'历史规律：散户>65%后主力启动砸盘\n'
                   f'空单准备：等反弹$83,200~$83,493\n'
                   f'止损 $84,500 | 目标 $80,219\n\n'
                   f'🌿 姓赵不宣 | 不是建议')
            alerts.append((key, msg))
    except: pass

    return alerts

def main():
    print(f'[价位监控] 启动 {time.strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  推送目标: {JARVIS_USER}:thread:{JARVIS_THREAD}')
    print(f'  检查间隔: {CHECK_INTERVAL}s | 触发距离: {PROXIMITY_PCT*100:.1f}%')

    while True:
        state = load_state()
        try:
            alerts = check_and_alert(state)
            for state_key, msg in alerts:
                push_jarvis(msg)
                state[state_key] = time.time()
                save_state(state)
                print(f'[ALERT] {state_key}')
        except Exception as e:
            print(f'[ERR] {e}')
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()

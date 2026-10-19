#!/usr/bin/env python3
"""
brahma_vip_watcher.py — VIP策略神经感知变化监控
[封印 2026-10-19 苏摩111]

功能：
  每5分钟检查关键指标是否发生重大变化
  变化触发 → 重新运行深度分析 → 推送新VIP策略
  
监控触发阈值：
  Taker变化 > 0.4（方向切换）
  LSR变化 > 0.3（猎杀结构进出）
  RSI变化 > 8（超买/超卖切换）
  OI方向变化（FLAT→SHORT_BUILD等）
  价格变化 > ATR4H×0.8（关键价位触及）
"""
import sys, json, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

STATE_FILE = BASE / 'data' / 'vip_watcher_state.json'

THRESHOLDS = {
    'taker_delta':  0.4,    # Taker变化阈值（固定，0~2之间的比率变化）
    'lsr_delta':    0.25,   # LSR变化阈值（调松：散户多空比变化）
    'rsi_delta':    8.0,    # RSI变化阈值
    'price_atr_pct':0.7,    # 价格变化 > ATR×0.7（调松：更早感知）
}

def _adaptive_taker_thresh(sym: str, atr1h: float) -> float:
    """ATR自适应Taker阈值：波动大时阈值松，波动小时阈值紧"""
    # BTC ATR1H≈$450 → base=0.4；ETH ATR1H≈$36 → base=0.35
    # 高波动(ATR>500) → 阈值0.5；低波动(ATR<100) → 阈值0.25
    if atr1h <= 0: return THRESHOLDS['taker_delta']
    if atr1h > 500:   return 0.5
    if atr1h > 200:   return 0.4
    if atr1h > 80:    return 0.35
    return 0.25


def fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_current_snapshot(sym: str) -> dict:
    """拉取当前关键指标快照"""
    s = sym.upper() + 'USDT'
    price  = fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={s}')
    k1h    = fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=8')
    oi_h   = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=3')
    lsr    = fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=5m&limit=1')
    taker  = fetch(f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=5m&limit=1')

    px  = float(price.get('price', 0))
    lsr_v = float(lsr[0]['longShortRatio']) if isinstance(lsr, list) and lsr else 1
    tak_v = float(taker[0].get('buySellRatio', 1)) if isinstance(taker, list) and taker else 1

    # RSI 1H
    rsi = 50.0
    if isinstance(k1h, list) and len(k1h) >= 5:
        cl = [float(k[4]) for k in k1h[-8:]]
        df = [cl[i]-cl[i-1] for i in range(1, len(cl))]
        g = [max(d, 0) for d in df]; l = [max(-d, 0) for d in df]
        ag = sum(g)/len(g) or 0.001; al = sum(l)/len(l) or 0.001
        rsi = round(100 - 100/(1+ag/al), 1)

    # ATR 1H
    atr1h = 0.0
    if isinstance(k1h, list) and len(k1h) >= 2:
        trs = [max(float(k[2])-float(k[3]), abs(float(k[2])-float(k1h[i-1][4])),
                   abs(float(k[3])-float(k1h[i-1][4]))) for i, k in enumerate(k1h[1:], 1)]
        atr1h = sum(trs)/len(trs) if trs else 0

    # OI方向
    oi_dir = 'FLAT'
    if isinstance(oi_h, list) and len(oi_h) >= 2:
        p = float(oi_h[-2].get('sumOpenInterest', 0))
        c = float(oi_h[-1].get('sumOpenInterest', 0))
        if p > 0:
            chg = (c-p)/p*100
            oi_dir = 'SHORT_BUILD' if chg > 0.3 else ('LONG_UNWIND' if chg < -0.3 else 'FLAT')

    return {
        'ts': time.time(), 'sym': sym.upper(),
        'px': px, 'rsi': rsi, 'atr1h': round(atr1h, 1),
        'lsr': lsr_v, 'taker': tak_v, 'oi_dir': oi_dir,
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.load(open(STATE_FILE))
        except: pass
    return {}


def save_state(state: dict):
    json.dump(state, open(STATE_FILE, 'w'), ensure_ascii=False)


def detect_changes(prev: dict, curr: dict) -> list:
    """检测关键变化，返回触发原因列表"""
    if not prev:
        return []
    triggers = []

    # Taker方向切换（ATR自适应阈值）
    tak_thresh = _adaptive_taker_thresh(curr['sym'], curr['atr1h'])
    tak_delta = abs(curr['taker'] - prev['taker'])
    if tak_delta >= tak_thresh:
        triggers.append(f"Taker{prev['taker']:.2f}→{curr['taker']:.2f}(Δ{tak_delta:.2f})")

    # LSR猎杀阈值穿越
    lsr_delta = abs(curr['lsr'] - prev['lsr'])
    cross_hunt = (prev['lsr'] < 2.5 <= curr['lsr']) or (curr['lsr'] < 2.5 <= prev['lsr'])
    if lsr_delta >= THRESHOLDS['lsr_delta'] or cross_hunt:
        tag = '⚡穿越猎杀阈值' if cross_hunt else f'Δ{lsr_delta:.2f}'
        triggers.append(f"LSR{prev['lsr']:.3f}→{curr['lsr']:.3f} {tag}")

    # RSI超买超卖切换
    rsi_delta = abs(curr['rsi'] - prev['rsi'])
    if rsi_delta >= THRESHOLDS['rsi_delta']:
        cross = ''
        if prev['rsi'] < 75 <= curr['rsi']: cross = '↑进超买区'
        elif curr['rsi'] < 75 <= prev['rsi']: cross = '↓出超买区'
        elif prev['rsi'] > 30 >= curr['rsi']: cross = '↓进超卖区'
        elif curr['rsi'] > 30 >= prev['rsi']: cross = '↑出超卖区'
        triggers.append(f"RSI{prev['rsi']}→{curr['rsi']}{cross}")

    # OI方向变化
    if prev['oi_dir'] != curr['oi_dir']:
        triggers.append(f"OI {prev['oi_dir']}→{curr['oi_dir']}")

    # 价格大幅变动
    if prev['px'] > 0 and curr['atr1h'] > 0:
        px_chg = abs(curr['px'] - prev['px'])
        threshold = curr['atr1h'] * THRESHOLDS['price_atr_pct']
        if px_chg >= threshold:
            pct = (curr['px']-prev['px'])/prev['px']*100
            triggers.append(f"价格${prev['px']:,.0f}→${curr['px']:,.0f}({pct:+.2f}%)")

    return triggers


def run_watcher():
    """主监控循环（单次执行，由cron每5分钟调用）"""
    state = load_state()
    now_str = time.strftime('%H:%M UTC')
    changed_syms = []

    for sym in ['BTC', 'ETH']:
        curr = get_current_snapshot(sym)
        prev = state.get(sym, {})
        triggers = detect_changes(prev, curr)

        if triggers:
            changed_syms.append((sym, triggers, curr))
            print(f'[watcher] {sym} 触发变化: {triggers}')
        else:
            print(f'[watcher] {sym} ${curr["px"]:,.0f} RSI={curr["rsi"]} LSR={curr["lsr"]:.3f} 无变化')

        state[sym] = curr

    save_state(state)

    if changed_syms:
        # 重新运行深度分析
        print(f'[watcher] {len(changed_syms)}个标的变化 → 重新分析')
        from brahma_deep_analysis import run_deep_analysis
        syms = [s for s, _, _ in changed_syms]
        try:
            result = run_deep_analysis(syms)
            # 推送变化通知
            _push_vip_update(changed_syms, result)
        except Exception as e:
            print(f'[watcher] 分析失败: {e}')


def _push_vip_update(changed_syms: list, analysis_result: str):
    """推送VIP策略更新通知"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'push_hub', BASE / 'scripts' / 'push_hub.py')
        ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ph)

        for sym, triggers, curr in changed_syms:
            trigger_str = ' / '.join(triggers[:3])
            # 从快照文件读取最新VIP
            vip_f = BASE / 'data' / f'vip_snapshot_{sym.lower()}.json'
            vip_card = ''
            if vip_f.exists():
                try:
                    vip_data = json.load(open(vip_f))
                    vip_card = vip_data.get('vip', '')
                except Exception:
                    pass

            msg = (
                f'⚡ 梵天神经感知 | {sym} VIP更新\n'
                f'触发: {trigger_str}\n'
                f'{curr["sym"]} ${curr["px"]:,.2f}  RSI={curr["rsi"]}  LSR={curr["lsr"]:.3f}\n'
            )
            if vip_card:
                msg += f'\n{vip_card}'

            ph.push_jarvis(
                msg, priority='P1',
                dedup_key=f'vip_update_{sym}_{int(time.time()//300)}',
                dedup_ttl=300)
            print(f'[watcher] 推送{sym} VIP更新✅')
    except Exception as e:
        print(f'[watcher] 推送失败: {e}')


if __name__ == '__main__':
    run_watcher()

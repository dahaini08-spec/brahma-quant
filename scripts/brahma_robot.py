#!/usr/bin/env python3
"""
梵天自动机器人主控 v2 [封印 2026-10-26 苏摩111]
接入位置：crontab → */5 * * * * brahma_robot.py --mode heartbeat

九大模块全自动驱动：
  M1 信号监控   — S级信号自动推VIP（confluence≥160，2H冷却）
  M2 预测结算   — prediction结算自动通知盈亏
  M3 相关性     — BTC-ETH<0.70分化预警
  M4 系统自愈   — state过期/supercronic宕机自愈+告警
  M5 定时播报   — UTC01/07/13三场开盘自动布局
  M6 宏观变化   — MACRO_REGIME切换主动告警
  M7 大户虚多   — lsr_t>>lsr_p差异>25%预警
  M8 周线支撑   — 价格接近/突破周线EMA告警
  M9 42标全扫   — 每1H全标的热力扫描，输出机会摘要
= 苏摩无需手动查询，梵天主动驱动
"""
import sys, json, time, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE           = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

STATE_PATH = BASE / 'data' / 'robot_state.json'

# ── 推送 ─────────────────────────────────────────────────────────────────────
def _push(msg: str, priority: str = 'P2') -> bool:
    try:
        from push_hub import push_jarvis
        return push_jarvis(msg, priority=priority)
    except Exception as e:
        print(f"[robot] 推送失败: {e}")
        return False

def _fapi(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}

def _fapi_list(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            d = json.loads(r.read())
            return d if isinstance(d, list) else []
    except Exception:
        return []

# ── 状态管理 ─────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        return {}

def _save_state(state: dict):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# M1：S级信号自动推送VIP
# ══════════════════════════════════════════════════════════════════════════════
def check_signals(state: dict) -> dict:
    now = time.time()
    for sym in ['btc', 'eth']:
        sf = BASE / 'data' / f'brahma_state_{sym}.json'
        if not sf.exists(): continue
        try:
            sd = json.loads(sf.read_text())
        except Exception:
            continue
        conf_raw  = sd.get('confluence', 0)
        confluence = conf_raw.get('score', 0) if isinstance(conf_raw, dict) else float(conf_raw or 0)
        price_ts  = sd.get('price_ts', 0)
        if now - price_ts > 3600: continue
        if confluence < 160: continue
        key = f'last_signal_push_{sym}'
        if now - state.get(key, 0) < 7200: continue
        try:
            from brahma_deep_analysis import fetch_all, enrich, reason_40yr, format_vip
            from brahma_prob_tree import calc_prob_tree, format_prob_tree
            raw = fetch_all(sym.upper())
            d   = enrich(raw); r = reason_40yr(d)
            if r.get('direction', 'WAIT') == 'WAIT': continue
            vip  = format_vip(d, r)
            ctx  = d.get('_vip_context', {})
            tree = calc_prob_tree(d, r, vip_context=ctx)
            pt   = '\n'.join(format_prob_tree(tree, r['direction']))
            msg  = f"⚡ 梵天S级信号 | {sym.upper()} Confluence={confluence:.0f}\n\n{vip}\n{pt}"
            if _push(msg, priority='P1'):
                state[key] = now
        except Exception as e:
            print(f"[M1] {sym} 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M2：prediction结算通知
# ══════════════════════════════════════════════════════════════════════════════
def check_predictions(state: dict) -> dict:
    now = time.time()
    if now - state.get('last_pred_check', 0) < 1800: return state
    state['last_pred_check'] = now
    try:
        from prediction_settler import run_settler
        result  = run_settler(verbose=False)
        settled = result.get('settled', 0)
        if settled > 0:
            sp = BASE / 'data' / 'prediction_settled.jsonl'
            if sp.exists():
                lines  = sp.read_text().splitlines()
                recent = [json.loads(l) for l in lines[-settled:] if l.strip()]
                parts  = [
                    f"{'✅' if (r.get('pnl_pct',0) or 0)>0 else '❌'} "
                    f"{r['symbol']} {r['direction']} → {r['status']} "
                    f"pnl={r.get('pnl_pct',0):+.2f}%"
                    for r in recent
                ]
                _push(f"📊 预测结算 | {settled}笔\n" + '\n'.join(parts), priority='P2')
    except Exception as e:
        print(f"[M2] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M3：BTC-ETH相关性分化预警
# ══════════════════════════════════════════════════════════════════════════════
def check_correlation(state: dict) -> dict:
    cm = BASE / 'data' / 'correlation_matrix.json'
    if not cm.exists(): return state
    try:
        d       = json.loads(cm.read_text())
        corr    = d.get('corr_matrix', {})
        btc_eth = corr.get('BTC-ETH', 0.9)
        prev    = state.get('last_btc_eth_corr', 0.9)
        state['last_btc_eth_corr'] = btc_eth
        if btc_eth < 0.50 and prev >= 0.50:
            _push(f"🚨 P1 相关性崩溃\nBTC-ETH={btc_eth:.3f}<0.50\n= 铁律⑤激活，单标操作", priority='P1')
        elif btc_eth < 0.70 and prev >= 0.70:
            _push(f"⚠️ 相关性分化\nBTC-ETH={btc_eth:.3f}<0.70\n= 双标操作风险上升", priority='P2')
    except Exception as e:
        print(f"[M3] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M4：系统自检+自愈
# ══════════════════════════════════════════════════════════════════════════════
def check_system_health(state: dict) -> dict:
    now = time.time()
    if now - state.get('last_health_check', 0) < 600: return state
    state['last_health_check'] = now
    alerts = []

    for sym in ['btc', 'eth']:
        sf = BASE / 'data' / f'brahma_state_{sym}.json'
        if sf.exists():
            try:
                sd  = json.loads(sf.read_text())
                age = (now - sd.get('price_ts', 0)) / 60
                if age > 90:
                    alerts.append(f"⚠️ {sym.upper()} state过期{age:.0f}min")
                    subprocess.Popen(
                        ['python3', str(BASE/'scripts'/'brahma_state_refresh.py'), sym.upper()],
                        cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
            except Exception:
                pass

    r = subprocess.run(['pgrep', '-f', 'supercronic'], capture_output=True, text=True)
    if not r.stdout.strip():
        alerts.append("🚨 supercronic未运行→已尝试重启")
        try:
            subprocess.Popen(['bash', str(BASE/'scripts'/'start_supercronic.sh')],
                             cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    for fname in ['fangcang_merged_v2.json', 'fangcang_experience_matrix_v2.json', 'fangcang_cases_tradfi.json']:
        if not (BASE / 'data' / fname).exists():
            alerts.append(f"❌ 缺失: {fname}")

    last = set(state.get('last_health_alerts', []))
    new  = [a for a in alerts if a not in last]
    if new:
        _push("🏛️ 梵天系统告警\n" + '\n'.join(new), priority='P1')
        state['last_health_alerts'] = list(set(alerts))
    elif not alerts:
        state['last_health_alerts'] = []
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M5：定时开盘播报
# ══════════════════════════════════════════════════════════════════════════════
def check_scheduled_broadcast(state: dict) -> dict:
    now_dt = datetime.now(timezone.utc)
    h, m   = now_dt.hour, now_dt.minute
    hours  = {1: '亚盘前策略', 7: '欧盘开盘布局', 13: '美盘开盘布局'}
    if h not in hours or m > 5: return state
    key = f'broadcast_{now_dt.strftime("%Y%m%d_%H")}'
    if state.get(key): return state
    state[key] = True
    try:
        from brahma_deep_analysis import fetch_all, enrich, reason_40yr, format_vip
        from brahma_prob_tree import calc_prob_tree, format_prob_tree
        parts = []
        for sym in ['BTC', 'ETH']:
            raw  = fetch_all(sym); d = enrich(raw); r = reason_40yr(d)
            vip  = format_vip(d, r)
            ctx  = d.get('_vip_context', {})
            tree = calc_prob_tree(d, r, vip_context=ctx)
            pt   = '\n'.join(format_prob_tree(tree, r['direction']))
            parts.append(f"{vip}\n{pt}")
        msg = (f"🏛️ 梵天{hours[h]} | {now_dt.strftime('%m/%d %H:%M')} UTC\n\n"
               + '\n\n'.join(parts))
        _push(msg, priority='P2')
    except Exception as e:
        print(f"[M5] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M6：宏观体制切换告警（新增）
# ══════════════════════════════════════════════════════════════════════════════
def check_macro_regime(state: dict) -> dict:
    """每次心跳检查宏观体制是否切换，切换时主动告警"""
    now = time.time()
    if now - state.get('last_macro_check', 0) < 3600: return state  # 1H检查一次
    state['last_macro_check'] = now
    try:
        from macro_regime_engine import run_macro_regime
        macro = run_macro_regime()
        for sym in ['BTC', 'ETH']:
            new_r = macro.get(sym, {}).get('macro_regime', 'UNKNOWN')
            old_r = state.get(f'macro_{sym}', 'UNKNOWN')
            if new_r != old_r and old_r != 'UNKNOWN' and new_r != 'UNKNOWN':
                pos   = macro.get(sym, {}).get('pos52', 0)
                desc  = macro.get(sym, {}).get('desc', '')
                _push(
                    f"🔄 宏观体制切换 | {sym}\n"
                    f"{old_r} → {new_r}\n"
                    f"52周位置={pos:.0f}% {desc}\n"
                    f"= 策略方向需重新评估",
                    priority='P1'
                )
            state[f'macro_{sym}'] = new_r
        # ETH/BTC相对强弱变化
        new_rel = macro.get('ETH_BTC_rel_4w', 0)
        old_rel = state.get('eth_btc_rel', 0)
        if abs(new_rel - old_rel) > 3.0:  # 相对强弱变化超3%
            sig = macro.get('ETH_BTC_rel_signal', 'NEUTRAL')
            _push(
                f"📊 ETH/BTC相对强弱变化\n"
                f"{old_rel:+.1f}% → {new_rel:+.1f}% [{sig}]\n"
                f"= {'铁律⑤豁免，可ETH单边操作' if sig != 'NEUTRAL' else '恢复同向操作'}",
                priority='P2'
            )
        state['eth_btc_rel'] = new_rel
    except Exception as e:
        print(f"[M6] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M7：大户虚多实空预警（新增）
# ══════════════════════════════════════════════════════════════════════════════
def check_smart_money(state: dict) -> dict:
    """检测大户账户比>>仓位比（虚多实空），每30min检查一次"""
    now = time.time()
    if now - state.get('last_smart_check', 0) < 1800: return state
    state['last_smart_check'] = now
    alerts = []
    for sym in ['BTC', 'ETH']:
        ticker = sym + 'USDT'
        lsa = _fapi_list(f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={ticker}&period=5m&limit=1')
        lsp = _fapi_list(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={ticker}&period=5m&limit=1')
        if not lsa or not lsp: continue
        try:
            lsr_t = float(lsa[0]['longShortRatio'])
            lsr_p = float(lsp[0]['longShortRatio'])
            div   = (lsr_t - lsr_p) / lsr_t if lsr_t > 0 else 0
            prev_div = state.get(f'smart_{sym}_div', 0)
            state[f'smart_{sym}_div'] = div
            # 新触发：差异从<25%升至>35%
            if div > 0.35 and prev_div <= 0.25 and lsr_t > 2.5:
                alerts.append(
                    f"🐋 {sym} 大户虚多实空激活\n"
                    f"账户={lsr_t:.2f} 仓位={lsr_p:.2f} 差异={div:.0%}\n"
                    f"= 主力在诱多做空，空单信号增强"
                )
        except Exception:
            pass
    for a in alerts:
        _push(a, priority='P2')
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M8：周线关键位突破告警（新增）
# ══════════════════════════════════════════════════════════════════════════════
def check_weekly_levels(state: dict) -> dict:
    """检测价格是否突破/接近周线EMA20或关键支撑，每15min检查"""
    now = time.time()
    if now - state.get('last_weekly_check', 0) < 900: return state
    state['last_weekly_check'] = now
    try:
        from macro_regime_engine import run_macro_regime
        macro = run_macro_regime()
        for sym in ['BTC', 'ETH']:
            ticker = sym + 'USDT'
            px_d   = _fapi(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={ticker}')
            px     = float(px_d.get('price', 0))
            if px <= 0: continue
            sm     = macro.get(sym, {})
            wema   = sm.get('weekly_ema20', 0)
            wsupp  = sm.get('weekly_support', 0)
            if not wema: continue
            # 周线EMA20突破
            prev_above = state.get(f'above_wema_{sym}', None)
            above_now  = px > wema
            if prev_above is not None and prev_above != above_now:
                direction = '上穿' if above_now else '下穿'
                emoji     = '🚀' if above_now else '⚠️'
                priority  = 'P2' if above_now else 'P1'
                _push(
                    f"{emoji} {sym} 周线EMA20{direction}\n"
                    f"EMA20=${wema:,.0f} 当前=${px:,.0f}\n"
                    f"= {'宏观多头结构确认' if above_now else '宏观空头风险，策略重评'}",
                    priority=priority
                )
            state[f'above_wema_{sym}'] = above_now
            # 接近周线支撑（1.5%内）
            if wsupp > 0:
                dist = (px - wsupp) / px
                prev_near = state.get(f'near_wsupp_{sym}', False)
                near_now  = dist < 0.015
                if near_now and not prev_near:
                    _push(
                        f"📍 {sym} 进入周线支撑区\n"
                        f"支撑=${wsupp:,.0f} 当前=${px:,.0f} 距离={dist:.1%}\n"
                        f"= 做空风险高，禁止追空",
                        priority='P2'
                    )
                state[f'near_wsupp_{sym}'] = near_now
    except Exception as e:
        print(f"[M8] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# M9：42标的热力扫描摘要（新增）
# ══════════════════════════════════════════════════════════════════════════════
def check_market_heatmap(state: dict) -> dict:
    """每1H对主要标的做快速热力扫描，输出机会摘要"""
    now = time.time()
    if now - state.get('last_heatmap', 0) < 3600: return state
    state['last_heatmap'] = now
    WATCHLIST = ['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX','DOT','LINK']
    strong_bull, strong_bear = [], []
    try:
        for sym in WATCHLIST:
            ticker = sym + 'USDT'
            k1h = _fapi_list(f'https://fapi.binance.com/fapi/v1/klines?symbol={ticker}&interval=1h&limit=20')
            if len(k1h) < 14: continue
            closes = [float(k[4]) for k in k1h]
            # 简单RSI
            gains  = [max(0, closes[i]-closes[i-1]) for i in range(1,len(closes))]
            losses = [max(0, closes[i-1]-closes[i]) for i in range(1,len(closes))]
            ag = sum(gains[:14])/14; al = sum(losses[:14])/14
            rsi = round(100-100/(1+ag/al), 1) if al > 0 else 50
            chg = round((closes[-1]-closes[-5])/closes[-5]*100, 2)
            if rsi < 30:  strong_bear.append(f"{sym}(RSI={rsi})")
            if rsi > 70:  strong_bull.append(f"{sym}(RSI={rsi})")
        if strong_bull or strong_bear:
            lines = [f"🌡️ 梵天热力扫描 | {datetime.now(timezone.utc).strftime('%H:%M UTC')}"]
            if strong_bull: lines.append(f"🔴 超买预警: {', '.join(strong_bull)}")
            if strong_bear: lines.append(f"🟢 超卖反弹: {', '.join(strong_bear)}")
            lines.append("= 仅作参考，需结合梵天完整分析")
            _push('\n'.join(lines), priority='P3' if 'P3' else 'P2')
    except Exception as e:
        print(f"[M9] 失败: {e}")
    return state

# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--mode',
                   choices=['heartbeat','signal','health','broadcast','macro','smart','weekly','heatmap','all'],
                   default='all')
    args = p.parse_args()
    m    = args.mode
    state = _load_state()

    # 心跳模式：按频率运行各模块
    if m in ('heartbeat', 'all'):
        state = check_system_health(state)   # 每10min
        state = check_signals(state)         # 每5min（有S级才触发）
        state = check_predictions(state)     # 每30min
        state = check_correlation(state)     # 实时
        state = check_macro_regime(state)    # 每1H
        state = check_smart_money(state)     # 每30min
        state = check_weekly_levels(state)   # 每15min
        state = check_market_heatmap(state)  # 每1H

    if m in ('broadcast',):
        state = check_scheduled_broadcast(state)

    if m == 'signal':  state = check_signals(state)
    if m == 'health':  state = check_system_health(state)
    if m == 'macro':   state = check_macro_regime(state)
    if m == 'smart':   state = check_smart_money(state)
    if m == 'weekly':  state = check_weekly_levels(state)
    if m == 'heatmap': state = check_market_heatmap(state)

    _save_state(state)
    print(f"[brahma_robot] {m} 完成 {time.strftime('%H:%M:%S')}")

if __name__ == '__main__':
    main()

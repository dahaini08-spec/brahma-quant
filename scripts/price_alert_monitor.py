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
    cmd = ['openclaw', 'message', 'send', '--channel', CHANNEL, '-t', target, '-m', msg]
    try:
        result = subprocess.run(cmd, timeout=10, check=True, capture_output=True, text=True)
        print(f'[PUSH OK] {result.stdout.strip()[:80]}')
    except subprocess.CalledProcessError as e:
        print(f'[PUSH ERR] RC={e.returncode} {e.stderr[:100] if e.stderr else ""}')
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
                # [2026-10-09 苏摩111] gex_2500已废弃 - 方向由conflict_check统一裁决
                # 原问题：无论OI/CVD如何都发送「突破看多|未破看空」两可信号
                # 与ETH_signal_conflict（禁止做多）形成矛盾推送
                # 'gex_2500': (2500.0, 'GEX_KEY', ''),  # 已停用
                'lsr_extreme': (0, 'LSR_CHECK', ''),  # 特殊处理
            }

        for level_key, (target, level_type, msg) in key_levels.items():
            if target == 0 or not msg: continue
            state_key = f'{sym}_{level_key}'
            last_alert = state.get(state_key, 0)
            cooldown = 7200  # [2026-10-09 苏摩111] 同一价位2小时内不重复推送（防震荡多次触发）

            if near(p, target) and (time.time() - last_alert) > cooldown:
                full_msg = f'🏛️ 梵天价位警报 {sym}\n当前价 ${p:,.2f}\n\n{msg}\n\n🌿 姓赵不宣 | 不是建议'
                alerts.append((state_key, full_msg))

    # [P0-新① 2026-10-09 苏摩111] LSR猎杀门槛检查 — 多标的（从asset_config读取）
    try:
        import json as _lj, pathlib as _lp
        _ac = _lj.loads((_lp.Path(__file__).parent.parent / 'data' / 'asset_config.json').read_text())
        _active = [(k,v) for k,v in _ac.items()
                   if not k.startswith('_') and v.get('tier') in ('L1','L2')]
        for _sym, _cfg in _active:
            _sf = _cfg.get('full_symbol', f'{_sym}USDT')
            _hunt = float(_cfg.get('lsr_hunt', 65.0))
            _near_dist = float(_cfg.get('lsr_near', 1.5))
            try:
                _lsr_r = fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={_sf}&period=5m&limit=1')
                _lsr_v = float(_lsr_r[0]['longAccount']) * 100
                _sym_p = get_price(_sym)
                _dist  = _hunt - _lsr_v

                # 猎杀触发
                _key_hunt = f'{_sym}_lsr_hunt'
                if _dist <= 0 and (time.time() - state.get(_key_hunt, 0)) > 3600:
                    _inv_lo = _cfg.get('short_entry_lo', int(_sym_p * 1.003))
                    _inv_hi = _cfg.get('liq_short', int(_sym_p * 1.015))
                    _sl     = _cfg.get('invalidate_short', int(_sym_p * 1.025))
                    _tp     = _cfg.get('liq_long', int(_sym_p * 0.985))
                    _msg = (
                        f'🚨 {_sym}散户LSR={_lsr_v:.1f}% 触发猎杀门槛{_hunt:.0f}%！\n'
                        f'当前价 ${_sym_p:,.3f}\n\n'
                        f'历史规律：散户>{_hunt:.0f}%后主力启动砸盘\n'
                        f'空单准备：等反弹 ${_inv_lo:,}~${_inv_hi:,}\n'
                        f'止损 ${_sl:,} | 目标 ${_tp:,}\n\n'
                        f'🌿 姓赵不宣 | 不是建议'
                    )
                    alerts.append((_key_hunt, _msg))

                # 门槛逼近预警
                _key_near = f'{_sym}_lsr_near'
                if 0 < _dist <= _near_dist and (time.time() - state.get(_key_near, 0)) > 3600:
                    _msg_near = (
                        f'⚠️ {_sym}散户LSR={_lsr_v:.1f}% 距猎杀门槛{_hunt:.0f}%仅差{_dist:.1f}%\n'
                        f'当前价 ${_sym_p:,.3f}\n\n'
                        f'空单挂好准备，等FR转正+1H收阴确认\n\n'
                        f'🌿 姓赵不宣 | 不是建议'
                    )
                    alerts.append((_key_near, _msg_near))

            except Exception as _le:
                import sys as _ls; print(f'[price_alert] {_sym} LSR err: {_le}', file=_ls.stderr)
    except Exception as _ae:
        import sys as _as; print(f'[price_alert] asset_config err: {_ae}', file=_as.stderr)

    # ── 신호 모순 체크 (ETH GEX 음성구 다단 금지) ──
    try:
        import pathlib as _pl
        # ETH CVD + GEX 충돌 체크
        cvd_d = json.loads(_pl.Path('data/cvd_realtime_ethusdt.json').read_text())
        cvd_eth = float(cvd_d.get('cvd_1h', cvd_d.get('delta_1h', cvd_d.get('cvd', 0))) or 0)
        gex_d2 = json.loads(_pl.Path('data/gex_state.json').read_text())
        eth_ts2 = gex_d2.get('ETH', {}).get('top_strikes', {})
        gex_2500 = float(eth_ts2.get('2500', 0))
        eth_p = float(fetch('https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT')['price'])
        
        # 조건: ETH가 $2,500 근처 + CVD 음수 + GEX 음수
        near_2500 = abs(eth_p - 2500) / 2500 <= 0.025  # 2.5% 이내
        cvd_negative = cvd_eth < -300
        gex_negative = gex_2500 < 0
        
        key = 'ETH_signal_conflict'
        last = state.get(key, 0)
        if near_2500 and cvd_negative and gex_negative and (time.time() - last) > 7200:
            conflict_msg = (
                f'⚠️ 梵天信号冲突警报 ETH\n'
                f'当前价 ${eth_p:,.1f}（接近$2,500）\n\n'
                f'❌ CVD={cvd_eth:.0f}（卖方主导）\n'
                f'❌ GEX $2,500={gex_2500/1e6:.1f}M（最大负GEX=阻力！）\n\n'
                f'⛔ $2,500附近禁止做多\n'
                f'= GEX负区+CVD卖方=双重阻力\n'
                f'等价格跌破$2,400+CVD转正才接多\n\n'
                f'🌿 姓赵不宣 | 不是建议'
            )
            alerts.append((key, conflict_msg))
    except Exception as e:
        print(f'[冲突检查ERR] {e}')
    
    return alerts

def main():
    """单次执行版本 — 由supercronic每分钟调用一次"""
    print(f'[价位监控] {time.strftime("%H:%M:%S")} 检查中...')
    state = load_state()
    try:
        alerts = check_and_alert(state)
        if alerts:
            for state_key, msg in alerts:
                # [新增 2026-10-10 苏摩111] Groq한줄 맥락 추가
                try:
                    import sys as _pa; _pa.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
                    from free_llm_client import chat as _pac
                    _cr = _pac(f'价格触发关键位，{msg[:80]}，15字内博弈判断', max_tokens=30, task='oi', timeout=8)
                    if _cr: msg = msg + f'\n💡 {_cr.strip()[:40]}'
                except Exception:
                    pass
                push_jarvis(msg)
                state[state_key] = time.time()
                save_state(state)
                print(f'[ALERT SENT] {state_key}')
        else:
            print(f'[OK] 无触发 BTC/ETH均在安全区间')
    except Exception as e:
        print(f'[ERR] {e}')

if __name__ == '__main__':
    main()

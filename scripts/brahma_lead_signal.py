#!/usr/bin/env python3
"""
📡 brahma_lead_signal.py — BTC→ETH 领先信号独立模块
2026-10-09 苏摩111封印 (P1-⑤)

铁证（brahma_core.py L2305）：
  BTC_TP后1~4H内ETH WR=85.7% EV=+1.396%（宪法级）
  BTC_SL后1~4H内ETH WR=21.8%（几乎必亏）

功能：
  1. 实时检测BTC/ETH价格相关性（ρ）
  2. BTC突破前高→自动推送ETH补涨机会
  3. BTC下跌→自动推送ETH做空机会
  4. ρ背离检测（BTC涨/ETH不动=背离警告）

由 trading_copilot.py M6附近调用，每分钟执行
"""
import json, pathlib, time, sys, os, ssl, math, urllib.request

BASE   = pathlib.Path(__file__).parent.parent
DATA   = BASE / 'data'
STATE_FILE = DATA / 'lead_signal_state.json'
COOLDOWN   = 1800  # 30

JARVIS_USER   = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a12e17-726c-78da-9857-4edab995522b')

ctx = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def push(msg, key='lead'):
    # [修复 2026-10-29 苏摩111] 改走push_hub经signal_govern仲裁
    import sys as _sys_ls
    _sys_ls.path.insert(0, str(BASE / 'scripts'))
    try:
        from push_hub import push_jarvis as _pj
        _pj(msg, priority='P2', source='lead_signal', direction='neutral')
        print(f'[lead_signal] push OK: {key}')
    except Exception as e:
        import subprocess
        target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
        try:
            subprocess.run(['openclaw','message','send','--channel','jarvis',
                '-t',target,'-m',msg], capture_output=True,text=True,timeout=15)
        except Exception:
            pass
        print(f'[lead_signal] push ERR: {e}', file=sys.stderr)

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {}

def save_state(s):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def calc_correlation(btc_closes, eth_closes, n=20):
    """1H K线n根收益率相关系数计算"""
    if len(btc_closes) < n+1 or len(eth_closes) < n+1:
        return None
    btc_r = [btc_closes[i]/btc_closes[i-1]-1 for i in range(-n, 0)]
    eth_r = [eth_closes[i]/eth_closes[i-1]-1 for i in range(-n, 0)]
    mb = sum(btc_r)/n; me = sum(eth_r)/n
    cov = sum((b-mb)*(e-me) for b,e in zip(btc_r,eth_r))/n
    sb  = math.sqrt(sum((b-mb)**2 for b in btc_r)/n)
    se  = math.sqrt(sum((e-me)**2 for e in eth_r)/n)
    return round(cov/(sb*se), 3) if sb>0 and se>0 else None


def check_lead_signals():
    """主检查函数 — 由trading_copilot调用"""
    state = load_state()
    now   = time.time()
    alerts = []

    # [根本修复 2026-10-10 苏摩111] GEX ZeroFlip   ETH  方向
    # ZeroFlip (負GEX区) =    → ETH
    _eth_gex_suppressed = False
    try:
        # [P1性能优化 2026-10-10 苏摩111] state_store 30秒缓存
        import sys as _jgs_s, pathlib as _pgs
        _bb = str(_pgs.Path(__file__).parent.parent / 'brahma_brain')
        if _bb not in _jgs_s.path: _jgs_s.path.insert(0, _bb)
        try:
            from state_store import get_state as _gst_eth
            _bstate = _gst_eth('ETH')
        except Exception:
            import json as _jgs
            _bstate = _jgs.loads((_pgs.Path(__file__).parent.parent/'data'/'brahma_state_eth.json').read_text())
        _eth_price = float(_bstate.get('price', 0) or 0)
        _eth_zf = float(_bstate.get('gex_zero_flip', _bstate.get('zero_flip', 0)) or 0)
        if _eth_zf > 0 and _eth_price < _eth_zf:
            _eth_gex_suppressed = True
            print(f'[M6.5] ETH GEX ZeroFlip失守(${_eth_price:.0f}<${_eth_zf:.0f}) → ETH多单信号抑制')
    except Exception:
        pass

    # ── 1. K   ──────────────────────────────
    try:
        btc_k = fetch('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=25')
        eth_k = fetch('https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=1h&limit=25')
    except Exception as e:
        print(f'[lead_signal] K线加载失败: {e}', file=sys.stderr)
        return []

    btc_c = [float(k[4]) for k in btc_k]
    eth_c = [float(k[4]) for k in eth_k]
    btc_now = btc_c[-1]
    eth_now = eth_c[-1]

    # ── 2.   ──────────────────────────────────
    rho = calc_correlation(btc_c, eth_c, n=20)
    state['last_rho'] = rho
    state['last_ts']  = now

    print(f'[lead_signal] BTC=${btc_now:,.0f} ETH=${eth_now:,.2f} ρ={rho}')

    # ── 3. BTC  感知 (1H  ) ─────────────────
    btc_prev_high = max(float(k[2]) for k in btc_k[-13:-1])  # 12H
    btc_1h_close  = btc_c[-1]
    btc_1h_open   = float(btc_k[-1][1])
    btc_bullish   = btc_1h_close > btc_1h_open  # 当前

    eth_1h_close  = eth_c[-1]
    eth_12h_high  = max(float(k[2]) for k in eth_k[-13:-1])

    # BTC
    btc_breakout = btc_1h_close > btc_prev_high and btc_bullish
    eth_lagging  = eth_1h_close < eth_12h_high * 0.999  # ETH

    if btc_breakout and eth_lagging:
        k = 'm8_btc_lead_long'
        if now - state.get(k, 0) > COOLDOWN:
            # asset_config ETH
            try:
                ac = json.loads((DATA / 'asset_config.json').read_text())
                eth_cfg = ac.get('ETH', {})
                liq_s = eth_cfg.get('liq_short', int(eth_now*1.02))
                liq_l = eth_cfg.get('liq_long',  int(eth_now*0.98))
            except:
                liq_s = int(eth_now*1.02); liq_l = int(eth_now*0.98)

            # [根本修复 2026-10-10 苏摩111] GEX ZeroFlip   ETH
            # ZeroFlip  =    → ETH  方向
            if _eth_gex_suppressed:
                print(f'[M6.5] GEX抑制: 跳过ETH多单信号(ZeroFlip失守)')
            else:
                alerts.append((k,
                    f'🚀 梵天领先信号 | BTC突破→ETH补涨\n\n'
                f'BTC突破12H高点 ${btc_prev_high:,.0f} → ${btc_now:,.0f}\n'
                f'ETH滞涨中 ${eth_now:,.2f}（距12H高点{(eth_12h_high/eth_now-1)*100:.1f}%落后）\n'
                f'BTC/ETH相关性 ρ={rho}\n\n'
                f'📌 铁证：BTC突破后1~4H内ETH WR=85.7%\n'
                f'ETH补涨机会窗口：现在~4H内\n\n'
                f'建议入场：${eth_now:,.2f}~${eth_now*1.005:,.0f}\n'
                f'目标：${liq_s:,} | 止损：${liq_l:,}\n\n'
                f'🌿 姓赵不宣 | 不是建议'
                ))

    # ── 4. ρ  感知 ──────────────────────────────────
    if rho is not None:
        # BTC 1H  vs ETH 1H
        btc_1h_chg = (btc_c[-1] - btc_c[-2]) / btc_c[-2] * 100
        eth_1h_chg = (eth_c[-1] - eth_c[-2]) / eth_c[-2] * 100
        diverge = abs(btc_1h_chg - eth_1h_chg) > 1.5  # 1.5%

        if rho > 0.85 and diverge:
            k = 'm8_diverge'
            if now - state.get(k, 0) > COOLDOWN:
                leader   = 'BTC' if btc_1h_chg > eth_1h_chg else 'ETH'
                follower = 'ETH' if leader == 'BTC' else 'BTC'
                diff     = abs(btc_1h_chg - eth_1h_chg)
                alerts.append((k,
                    f'⚠️ 梵天背离信号 | {leader}领涨/{follower}滞涨\n\n'
                    f'BTC 1H: {btc_1h_chg:+.2f}% | ETH 1H: {eth_1h_chg:+.2f}%\n'
                    f'差值：{diff:.2f}%（正常ρ={rho}，但走势背离）\n\n'
                    f'📌 规律：高相关期背离通常在15~30min内收敛\n'
                    f'→ {follower}大概率跟随{leader}补涨/补跌\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

    # ── 5. BTC_settlement   (brahma_core P1 ) ─
    # brahma_core L2306 btc_settlement_state.json  ETH
    btc_4h_k = fetch('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=5')
    btc_4h_c = [float(k[4]) for k in btc_4h_k]
    btc_4h_chg = (btc_4h_c[-1] - btc_4h_c[-2]) / btc_4h_c[-2] * 100
    settlement = {
        'last_result': 'TP' if btc_4h_chg > 1.5 else ('SL' if btc_4h_chg < -1.5 else 'NEUTRAL'),
        'last_ts': now,
        'btc_4h_chg': round(btc_4h_chg, 3),
        'rho': rho,
    }
    tmp = DATA / 'btc_settlement_state.tmp'
    tmp.write_text(json.dumps(settlement, ensure_ascii=False))
    tmp.replace(DATA / 'btc_settlement_state.json')
    print(f'[lead_signal] BTC settlement: {settlement["last_result"]} ({btc_4h_chg:+.2f}%)')

    #
    for key, msg in alerts:
        push(msg, key)
        state[key] = now

    save_state(state)
    return alerts


if __name__ == '__main__':
    result = check_lead_signals()
    print(f'\n领先信号检查完成: {len(result)}个警报')

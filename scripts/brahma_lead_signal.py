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
COOLDOWN   = 1800  # 30분 쿨다운

JARVIS_USER   = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed')

ctx = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def push(msg, key='lead'):
    import subprocess
    target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
    try:
        subprocess.run(
            ['openclaw', 'message', 'send', '--channel', 'jarvis', '-t', target, '-m', msg],
            capture_output=True, text=True, timeout=15, check=True
        )
        print(f'[lead_signal] push OK: {key}')
    except Exception as e:
        print(f'[lead_signal] push ERR: {e}', file=sys.stderr)

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {}

def save_state(s):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def calc_correlation(btc_closes, eth_closes, n=20):
    """1H K선 n개 수익률 상관계수 계산"""
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
    """메인 체크 함수 — trading_copilot에서 호출"""
    state = load_state()
    now   = time.time()
    alerts = []

    # ── 1. K선 데이터 로드 ──────────────────────────────
    try:
        btc_k = fetch('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=25')
        eth_k = fetch('https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=1h&limit=25')
    except Exception as e:
        print(f'[lead_signal] K선 로드 실패: {e}', file=sys.stderr)
        return []

    btc_c = [float(k[4]) for k in btc_k]
    eth_c = [float(k[4]) for k in eth_k]
    btc_now = btc_c[-1]
    eth_now = eth_c[-1]

    # ── 2. 상관계수 계산 ──────────────────────────────────
    rho = calc_correlation(btc_c, eth_c, n=20)
    state['last_rho'] = rho
    state['last_ts']  = now

    print(f'[lead_signal] BTC=${btc_now:,.0f} ETH=${eth_now:,.2f} ρ={rho}')

    # ── 3. BTC 돌파 감지 (1H 고점 돌파) ─────────────────
    btc_prev_high = max(float(k[2]) for k in btc_k[-13:-1])  # 12H 고점
    btc_1h_close  = btc_c[-1]
    btc_1h_open   = float(btc_k[-1][1])
    btc_bullish   = btc_1h_close > btc_1h_open  # 현재봉 양봉

    eth_1h_close  = eth_c[-1]
    eth_12h_high  = max(float(k[2]) for k in eth_k[-13:-1])

    # BTC 신고점 돌파
    btc_breakout = btc_1h_close > btc_prev_high and btc_bullish
    eth_lagging  = eth_1h_close < eth_12h_high * 0.999  # ETH가 아직 고점 안 갱신

    if btc_breakout and eth_lagging:
        k = 'm8_btc_lead_long'
        if now - state.get(k, 0) > COOLDOWN:
            # asset_config에서 ETH 진입 파라미터 로드
            try:
                ac = json.loads((DATA / 'asset_config.json').read_text())
                eth_cfg = ac.get('ETH', {})
                liq_s = eth_cfg.get('liq_short', int(eth_now*1.02))
                liq_l = eth_cfg.get('liq_long',  int(eth_now*0.98))
            except:
                liq_s = int(eth_now*1.02); liq_l = int(eth_now*0.98)

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

    # ── 4. ρ 배리 감지 ──────────────────────────────────
    if rho is not None:
        # BTC 1H 변화율 vs ETH 1H 변화율
        btc_1h_chg = (btc_c[-1] - btc_c[-2]) / btc_c[-2] * 100
        eth_1h_chg = (eth_c[-1] - eth_c[-2]) / eth_c[-2] * 100
        diverge = abs(btc_1h_chg - eth_1h_chg) > 1.5  # 1.5% 이상 차이

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

    # ── 5. BTC_settlement 상태 기록 (brahma_core P1 연동) ─
    # brahma_core L2306이 btc_settlement_state.json을 읽어서 ETH 스코어 조정
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

    # 경보 발송
    for key, msg in alerts:
        push(msg, key)
        state[key] = now

    save_state(state)
    return alerts


if __name__ == '__main__':
    result = check_lead_signals()
    print(f'\n리드 신호 체크 완료: {len(result)}개 경보')

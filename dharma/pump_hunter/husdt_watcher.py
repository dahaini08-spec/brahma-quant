#!/usr/bin/env python3
"""
HUSDT 暴涨猎手独立预警通道
[设计院 2026-07-06] HUSDT: RSI_1D=16 + 鲸鱼背离+11.8% + FR低 = 暴涨猎手候选

监控维度：
  - RSI_1H/4H/1D 趋势变化（底部反转信号）
  - OI变化（6H vs 前24H）
  - 资金费率（负→中性→正 = 逼空完成信号）
  - 鲸鱼多头持仓比例
  - 价格突破（当前高点压力）

触发标准（score≥60推送 / score≥80执行预警）：
  RSI_1H从<40穿越到>50 → +25
  RSI_4H从<35变化到>45 → +20
  RSI_1D<25（极度超卖）→ +15
  OI 6H变化>20% → +25
  FR从负→正 → +15
  鲸鱼L>70% → +10
  价格突破近48H高点 → +20
"""

import sys, os
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _root not in sys.path:
    sys.path.insert(0, _root)

import requests, json, time, datetime
from pathlib import Path

SYMBOL  = 'HUSDT'
API     = 'https://fapi.binance.com'
DIR     = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(DIR, 'husdt_state.json')
LOG_F   = os.path.join(DIR, 'husdt_log.jsonl')
PUSH_SCORE = 60
EXEC_SCORE = 80

# ── SSOT推送地址 ─────────────────────────────────────────────────
def _get_target():
    if os.environ.get('JARVIS_TARGET'):
        return os.environ['JARVIS_TARGET']
    try:
        from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
    except Exception:
        return '73295708:thread:019f443a-b891-70f1-8cb0-ed031a80e68b'

def fetch(url, params=None, timeout=8):
    r = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0'}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    d = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(0, x) for x in d[-period:]]
    losses = [max(0, -x) for x in d[-period:]]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)

def scan():
    score = 0
    reasons = []
    details = {}

    # ── 价格 & 基础行情 ─────────────────────────────────────────
    try:
        ticker = fetch(f'{API}/fapi/v1/ticker/24hr', {'symbol': SYMBOL})
        price = float(ticker['lastPrice'])
        chg24h = float(ticker['priceChangePercent'])
        vol_m = float(ticker['quoteVolume']) / 1e6
        details['price'] = price
        details['chg24h'] = chg24h
        details['vol_m'] = round(vol_m, 1)
    except Exception as e:
        return {'error': f'ticker: {e}', 'score': 0}

    # ── K线 RSI ─────────────────────────────────────────────────
    try:
        kl_1h = fetch(f'{API}/fapi/v1/klines', {'symbol': SYMBOL, 'interval': '1h', 'limit': 50})
        closes_1h = [float(k[4]) for k in kl_1h]
        highs_1h  = [float(k[2]) for k in kl_1h]
        rsi_1h = get_rsi(closes_1h[-15:])
        high48h = max(highs_1h[-48:]) if len(highs_1h) >= 48 else max(highs_1h)
        details['rsi_1h'] = rsi_1h
        details['high48h'] = high48h

        # 读上次状态，判断RSI穿越
        prev_state = {}
        if os.path.exists(STATE_F):
            try:
                prev_state = json.load(open(STATE_F))
            except Exception:
                pass
        prev_rsi_1h = prev_state.get('rsi_1h', 50)

        if prev_rsi_1h < 40 and rsi_1h >= 50:
            score += 25; reasons.append(f'🔥RSI_1H底部穿越: {prev_rsi_1h:.0f}→{rsi_1h:.0f}')
        elif rsi_1h < 25:
            score += 15; reasons.append(f'RSI_1H极度超卖: {rsi_1h:.0f}')

        # 价格突破48H高点
        if price >= high48h * 0.995:
            score += 20; reasons.append(f'突破/逼近48H高: {high48h:.6f}')
    except Exception as e:
        details['rsi_1h_err'] = str(e)

    try:
        kl_4h = fetch(f'{API}/fapi/v1/klines', {'symbol': SYMBOL, 'interval': '4h', 'limit': 20})
        closes_4h = [float(k[4]) for k in kl_4h]
        rsi_4h = get_rsi(closes_4h)
        details['rsi_4h'] = rsi_4h
        prev_rsi_4h = prev_state.get('rsi_4h', 50) if 'prev_state' in dir() else 50
        if prev_rsi_4h < 35 and rsi_4h >= 45:
            score += 20; reasons.append(f'🔥RSI_4H反转: {prev_rsi_4h:.0f}→{rsi_4h:.0f}')
        elif rsi_4h < 30:
            score += 10; reasons.append(f'RSI_4H超卖: {rsi_4h:.0f}')
    except Exception as e:
        details['rsi_4h_err'] = str(e)

    try:
        kl_1d = fetch(f'{API}/fapi/v1/klines', {'symbol': SYMBOL, 'interval': '1d', 'limit': 15})
        closes_1d = [float(k[4]) for k in kl_1d]
        rsi_1d = get_rsi(closes_1d)
        details['rsi_1d'] = rsi_1d
        if rsi_1d < 20:
            score += 20; reasons.append(f'🚨RSI_1D极度超卖: {rsi_1d:.0f}')
        elif rsi_1d < 30:
            score += 10; reasons.append(f'RSI_1D超卖: {rsi_1d:.0f}')
    except Exception as e:
        details['rsi_1d_err'] = str(e)

    # ── OI变化 ──────────────────────────────────────────────────
    try:
        oi_hist = fetch(f'{API}/futures/data/openInterestHist',
                        {'symbol': SYMBOL, 'period': '1h', 'limit': 30})
        if isinstance(oi_hist, list) and len(oi_hist) >= 12:
            oi_base = sum(float(x['sumOpenInterestValue']) for x in oi_hist[:18]) / 18
            oi_late = sum(float(x['sumOpenInterestValue']) for x in oi_hist[-6:]) / 6
            oi_chg = (oi_late - oi_base) / oi_base * 100 if oi_base > 0 else 0
            details['oi_chg_pct'] = round(oi_chg, 1)
            if oi_chg >= 30:
                score += 25; reasons.append(f'🔥OI暴增: +{oi_chg:.0f}%')
            elif oi_chg >= 15:
                score += 15; reasons.append(f'OI大增: +{oi_chg:.0f}%')
    except Exception as e:
        details['oi_err'] = str(e)

    # ── 资金费率 ─────────────────────────────────────────────────
    try:
        fr_list = fetch(f'{API}/fapi/v1/fundingRate', {'symbol': SYMBOL, 'limit': 8})
        if fr_list:
            fr_latest = float(fr_list[-1]['fundingRate']) * 100
            fr_prev   = float(fr_list[-2]['fundingRate']) * 100 if len(fr_list) >= 2 else fr_latest
            details['fr_latest'] = round(fr_latest, 4)
            details['fr_prev']   = round(fr_prev, 4)

            if fr_prev < -0.03 and fr_latest > -0.01:
                score += 20; reasons.append(f'🔥FR逼空完成: {fr_prev:.3f}%→{fr_latest:.3f}%')
            elif fr_latest < -0.05:
                score += 15; reasons.append(f'极端负FR: {fr_latest:.3f}%')
            elif fr_latest < -0.02:
                score += 8; reasons.append(f'负FR: {fr_latest:.3f}%')
    except Exception as e:
        details['fr_err'] = str(e)

    # ── 多空比（散户空头）───────────────────────────────────────
    try:
        lsr = fetch(f'{API}/futures/data/globalLongShortAccountRatio',
                    {'symbol': SYMBOL, 'period': '1h', 'limit': 3})
        if lsr:
            short_pct = float(lsr[-1].get('shortAccount', 0)) * 100
            details['short_pct'] = round(short_pct, 1)
            if short_pct > 62:
                score += 15; reasons.append(f'空头拥挤: {short_pct:.0f}%')
            elif short_pct > 55:
                score += 8; reasons.append(f'空头偏多: {short_pct:.0f}%')
    except Exception as e:
        details['lsr_err'] = str(e)

    # ── 多空持仓比（大户）───────────────────────────────────────
    try:
        top_lsr = fetch(f'{API}/futures/data/topLongShortPositionRatio',
                        {'symbol': SYMBOL, 'period': '1h', 'limit': 2})
        if top_lsr:
            whale_long = float(top_lsr[-1].get('longAccount', 0)) * 100
            details['whale_long_pct'] = round(whale_long, 1)
            if whale_long > 70:
                score += 10; reasons.append(f'鲸鱼多头: {whale_long:.0f}%')
    except Exception as e:
        details['whale_err'] = str(e)

    # ── 结果汇总 ─────────────────────────────────────────────────
    result = {
        'ts':       time.time(),
        'symbol':   SYMBOL,
        'score':    score,
        'reasons':  reasons,
        'details':  details,
        'need_push': score >= PUSH_SCORE,
        'exec_eligible': score >= EXEC_SCORE,
        'scan_time': datetime.datetime.utcnow().isoformat(),
    }

    # 更新状态文件
    new_state = {
        'last_ts':   result['ts'],
        'last_score': score,
        'rsi_1h':   details.get('rsi_1h', 50),
        'rsi_4h':   details.get('rsi_4h', 50),
        'rsi_1d':   details.get('rsi_1d', 50),
        'price':    details.get('price', 0),
    }
    with open(STATE_F, 'w') as f:
        json.dump(new_state, f, ensure_ascii=False)

    # 追加日志
    with open(LOG_F, 'a') as f:
        f.write(json.dumps({'ts': result['ts'], 'score': score,
                            'rsi_1h': details.get('rsi_1h'), 'rsi_1d': details.get('rsi_1d'),
                            'oi_chg': details.get('oi_chg_pct'), 'reasons': reasons[:3]}) + '\n')

    return result


def push(result: dict):
    target = _get_target()
    lvl    = '💣' if result['exec_eligible'] else '🚨'
    rsi_1h = result['details'].get('rsi_1h', '-')
    rsi_1d = result['details'].get('rsi_1d', '-')
    fr     = result['details'].get('fr_latest', '-')
    oi_chg = result['details'].get('oi_chg_pct', '-')
    reasons_str = ' | '.join(result['reasons'][:4])

    lines = [
        f'{lvl} HUSDT 暴涨猎手独立预警',
        f'评分: {result["score"]} | 执行级: {"✅" if result["exec_eligible"] else "👀"}',
        f'RSI_1H={rsi_1h} RSI_1D={rsi_1d} | OI变化={oi_chg}% | FR={fr}%',
        f'触发因子: {reasons_str}',
        f'价格: ${result["details"].get("price", 0):.6f}',
        f'时间: {result["scan_time"][:16]} UTC',
    ]
    if result.get('exec_eligible'):
        lines.append('⚡ 执行级预警：可触发暴涨猎手执行器')
    msg = '\n'.join(lines)

    import subprocess as sp
    r = sp.run(['openclaw', 'message', 'send',
                '--channel', 'jarvis',
                '--target', target,
                '--message', msg],
               capture_output=True, timeout=15)
    return r.returncode == 0


if __name__ == '__main__':
    print(f'[HUSDT-Watcher] 开始扫描 {datetime.datetime.utcnow().isoformat()}')
    result = scan()
    if 'error' in result:
        print(f'[HUSDT-Watcher] ERROR: {result["error"]}')
        sys.exit(0)
    print(f'score={result["score"]} need_push={result.get("need_push",False)} exec={result.get("exec_eligible",False)}')
    if result.get('reasons'):
        for r_item in result['reasons']:
            print(f'  {r_item}')

    if result.get('need_push'):
        ok = push(result)
        print(f'推送: {"✅" if ok else "❌"}')
    else:
        print('HEARTBEAT_OK')

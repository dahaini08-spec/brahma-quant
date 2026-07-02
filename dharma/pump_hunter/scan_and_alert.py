#!/usr/bin/env python3
"""
暴涨猎手 · 纯脚本扫描器（零AI token消耗）
运行逻辑：
  1. 扫描全市场TIGHT形态+OI异动
  2. 评分 → 高分信号写入 new_alerts.json
  3. 与上次结果对比，只有「新出现」的高分信号才标记为需推送
  4. cron读取 need_push 标志，有则推送，无则HEARTBEAT_OK
"""
import requests, json, datetime, os, time
from collections import defaultdict

API   = 'https://fapi.binance.com'
DIR   = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(DIR, 'new_alerts.json')
LAST  = os.path.join(DIR, 'last_alerts.json')
LOG   = os.path.join(DIR, 'scan_log.jsonl')

EXCLUDE     = {'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT'}
MIN_VOL     = 1_500_000    # 最低24H成交额
MAX_VOL     = 800_000_000  # 排除超大盘
MAX_CHG_ABS = 25.0         # 排除已大幅波动
PUSH_SCORE  = 75           # 触发推送的评分门槛

def get_symbols():
    info = requests.get(f'{API}/fapi/v1/exchangeInfo', timeout=12).json()
    return [s['symbol'] for s in info['symbols']
            if s['status'] == 'TRADING'
            and s['symbol'].endswith('USDT')
            and 'UP' not in s['symbol']
            and 'DOWN' not in s['symbol']
            and s['symbol'] not in EXCLUDE]

def scan():
    t0 = time.time()
    syms = get_symbols()

    # 批量获取24H行情（1次请求）
    tickers = {t['symbol']: t for t in
               requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=15).json()
               if t['symbol'].endswith('USDT')}

    # 过滤候选
    candidates = [s for s in syms
                  if s in tickers
                  and MIN_VOL < float(tickers[s].get('quoteVolume',0)) < MAX_VOL
                  and abs(float(tickers[s].get('priceChangePercent',0))) < MAX_CHG_ABS]

    alerts = []
    weight_used = 0
    weight_reset = time.time()

    for sym in candidates:
        # 限速：每分钟≤800权重（留余量给主系统）
        if weight_used >= 800:
            elapsed = time.time() - weight_reset
            if elapsed < 60:
                time.sleep(61 - elapsed)
            weight_used = 0
            weight_reset = time.time()

        try:
            tick  = tickers[sym]
            chg   = float(tick['priceChangePercent'])
            vol   = float(tick['quoteVolume'])
            price = float(tick['lastPrice'])

            score    = 0
            reasons  = []

            # ── OI变化（近6H vs 前42H）──────────────
            oi_hist = requests.get(f'{API}/futures/data/openInterestHist',
                params={'symbol':sym,'period':'1h','limit':48}, timeout=6).json()
            weight_used += 1

            if isinstance(oi_hist, list) and len(oi_hist) >= 12:
                oi_early = sum(float(x['sumOpenInterestValue']) for x in oi_hist[:36]) / 36
                oi_late  = sum(float(x['sumOpenInterestValue']) for x in oi_hist[-6:]) / 6
                oi_chg   = (oi_late - oi_early) / oi_early * 100 if oi_early > 0 else 0
                if oi_chg >= 50:
                    score += 35; reasons.append(f'OI暴增+{oi_chg:.0f}%')
                elif oi_chg >= 30:
                    score += 20; reasons.append(f'OI大增+{oi_chg:.0f}%')
            else:
                oi_chg = 0

            # ── 资金费率 ──────────────────────────
            fr_list = requests.get(f'{API}/fapi/v1/fundingRate',
                params={'symbol':sym,'limit':6}, timeout=5).json()
            weight_used += 1

            latest_fr = float(fr_list[-1]['fundingRate']) * 100 if fr_list else 0
            if latest_fr < -0.05:
                score += 30; reasons.append(f'极端负费率{latest_fr:.3f}%')
            elif latest_fr < -0.02:
                score += 15; reasons.append(f'负费率{latest_fr:.3f}%')
            elif latest_fr > 0.04:
                score += 8;  reasons.append(f'正费率偏高{latest_fr:.3f}%')

            # ── 多空比 ────────────────────────────
            lsr = requests.get(f'{API}/futures/data/globalLongShortAccountRatio',
                params={'symbol':sym,'period':'1h','limit':3}, timeout=5).json()
            weight_used += 1

            short_pct = float(lsr[-1].get('shortAccount',0)) * 100 if lsr else 50
            if short_pct > 62:
                score += 20; reasons.append(f'空头拥挤{short_pct:.0f}%')
            elif short_pct > 57:
                score += 10; reasons.append(f'空头偏多{short_pct:.0f}%')

            # ── 4H K线：压缩度 + 量萎缩 ────────────
            kl = requests.get(f'{API}/fapi/v1/klines',
                params={'symbol':sym,'interval':'4h','limit':24}, timeout=6).json()
            weight_used += 1

            if isinstance(kl, list) and len(kl) >= 12:
                closes = [float(k[4]) for k in kl]
                highs  = [float(k[2]) for k in kl]
                lows   = [float(k[3]) for k in kl]
                qvols  = [float(k[7]) for k in kl]

                h48 = max(highs[-12:]); l48 = min(lows[-12:])
                ctr  = (h48 + l48) / 2
                comp = (h48 - l48) / ctr * 100 if ctr > 0 else 99

                vol_recent = sum(qvols[-6:]) / 6
                vol_base   = sum(qvols[-24:-6]) / 18 if len(qvols) >= 24 else vol_recent
                vol_ratio  = vol_recent / vol_base if vol_base > 0 else 1

                if comp < 15:
                    score += 25; reasons.append(f'TIGHT压缩{comp:.0f}%')
                elif comp < 25:
                    score += 12; reasons.append(f'MODERATE压缩{comp:.0f}%')

                if vol_ratio < 0.5:
                    score += 10; reasons.append(f'量能萎缩{vol_ratio:.2f}x')

                # RSI简算
                d = [closes[i]-closes[i-1] for i in range(1,len(closes))]
                g = [max(0,x) for x in d[-14:]]; lo = [max(0,-x) for x in d[-14:]]
                ag = sum(g)/14; al = sum(lo)/14
                rsi = 100-100/(1+ag/al) if al>0 else 50
                if rsi < 30:
                    score += 15; reasons.append(f'RSI超卖{rsi:.0f}')
                elif rsi < 50:
                    score += 5; reasons.append(f'RSI低位{rsi:.0f}')

                # 距历史高点
                hist_high = max(highs)
                dist = (price - hist_high) / hist_high * 100
                if dist < -60:
                    score += 10; reasons.append(f'深度低位{dist:.0f}%')
                elif dist < -40:
                    score += 5

            else:
                comp = 99; vol_ratio = 1; rsi = 50; dist = 0

            if score >= PUSH_SCORE:
                alerts.append({
                    'symbol': sym, 'score': score,
                    'price': price, 'chg_24h': round(chg,1),
                    'vol_m': round(vol/1e6,1),
                    'oi_chg': round(oi_chg,1),
                    'funding': round(latest_fr,4),
                    'short_pct': round(short_pct,1),
                    'compression': round(comp,1),
                    'vol_ratio': round(vol_ratio,2),
                    'rsi': round(rsi,1),
                    'reasons': reasons,
                    'scan_time': datetime.datetime.utcnow().isoformat(),
                })

        except Exception:
            pass

    alerts.sort(key=lambda x: x['score'], reverse=True)
    elapsed = time.time() - t0

    # ── 与上次对比，找「新出现」的信号 ─────────────
    last_syms = set()
    if os.path.exists(LAST):
        try:
            last_data = json.load(open(LAST))
            last_syms = {a['symbol'] for a in last_data.get('alerts',[])
                         if a['score'] >= PUSH_SCORE}
        except: pass

    new_alerts = [a for a in alerts if a['symbol'] not in last_syms]

    # ── 梵天体制感知（设计院修正版 2026-06-29）──────────────────────
    # 设计哲学：体制只调整「仓位+止盈」，不折损信号分，不屏蔽妖币
    # 原因：BEAR_TREND逆势妖往往是最强逼空，不能因体制压制信号本身
    _REGIME_POS = {'BEAR_RECOVERY': 3.0, 'BULL_TREND': 2.5, 'CHOP_MID': 2.0,
                   'BULL_EARLY': 2.0, 'BEAR_EARLY': 1.5, 'BEAR_TREND': 1.0}
    _REGIME_TP  = {'BEAR_RECOVERY': 2.0, 'BULL_TREND': 1.5, 'CHOP_MID': 1.2,
                   'BULL_EARLY': 1.2, 'BEAR_EARLY': 1.0, 'BEAR_TREND': 0.8}
    try:
        from brahma_brain.universal_asset_router import get_regime_cached
        _btc_regime = get_regime_cached('BTCUSDT')
        _pos = _REGIME_POS.get(_btc_regime, 2.0)
        _tp  = _REGIME_TP.get(_btc_regime, 1.0)
        for a in alerts + new_alerts:
            a['brahma_regime']         = _btc_regime
            a['exec_pos_pct']          = _pos   # 仓位参考，不改信号分
            a['exec_tp_mult']          = _tp    # 止盈倍数参考
            a['exec_eligible']         = a.get('score', 0) >= PUSH_SCORE  # 门槛不变
            a['brahma_weighted_score'] = a.get('score', 0)  # 分数不折损
            if _btc_regime == 'BEAR_TREND':
                a['regime_note'] = '❗熊市逆势，仓位1%，止盈目标保守'
        # 排序保持原始 score，不用体制折损分
        alerts     = sorted(alerts,     key=lambda x: x['score'], reverse=True)
        new_alerts = sorted(new_alerts, key=lambda x: x['score'], reverse=True)
    except Exception:
        pass  # 静默降级，不阻断扫描主流

    result = {
        'scan_time':    datetime.datetime.utcnow().isoformat(),
        'elapsed_sec':  round(elapsed, 1),
        'total_scanned': len(candidates),
        'alerts':       alerts,
        'new_alerts':   new_alerts,
        'need_push':    len(new_alerts) > 0,
    }

    json.dump(result, open(OUT, 'w'),  indent=2, ensure_ascii=False)
    json.dump(result, open(LAST, 'w'), indent=2, ensure_ascii=False)

    # 追加日志
    with open(LOG, 'a') as f:
        f.write(json.dumps({
            'ts': result['scan_time'],
            'alerts': len(alerts),
            'new': len(new_alerts),
            'elapsed': elapsed
        }) + '\n')

    return result

if __name__ == '__main__':
    r = scan()
    print(f'扫描完成 {r["elapsed_sec"]:.1f}s | 高分信号={len(r["alerts"])} | 新信号={len(r["new_alerts"])} | 需推送={r["need_push"]}')

    # 自推送逻辑：need_push=true时直接调openclaw推送，无需AI中间人
    if r['need_push'] and r['new_alerts']:
        lines = [f'🎯 暴涨猎手预警 · {r["scan_time"][:16]}']
        for a in r['new_alerts'][:5]:
            lvl = '💣' if a.get('score', 0) >= 85 else '🚨'
            reasons = ' | '.join(a.get('reasons', [])[:2])
            lines.append(f'{lvl} {a["symbol"]:<16} score={a["score"]} | {reasons}')
        lines.append(f'\n共{len(r["new_alerts"])}个新信号 · 暴涨猎手系统')
        msg = '\n'.join(lines)
        import subprocess as _sp
        _sp.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--target', os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID'),
             '--message', msg],
            capture_output=True, timeout=15
        )
        print(f'[pump-hunter] 推送完成 {len(r["new_alerts"])}个信号')

        # ── [P2-6 架构重构 2026-06-30] 写入独立信号通道 ──
        # 高分信号同时写入 pump_signal_queue，供独立执行器消费
        try:
            import sys as _sys_ph, importlib.util as _ilu
            # 修复: 多层备用路径解析，确保 trading-system 根目录加入 sys.path
            _cur_ph = os.path.abspath(__file__)
            _root_ph = _cur_ph
            for _ in range(5):  # 最多向上找5层
                _root_ph = os.path.dirname(_root_ph)
                if os.path.isfile(os.path.join(_root_ph, 'ws_guardian.py')):
                    break  # 找到 trading-system 根目录
            if _root_ph not in _sys_ph.path:
                _sys_ph.path.insert(0, _root_ph)
            # v5.0 fix 2026-07-02: 强制插入 trading-system 根目录确保 scripts 可导入
            import os as _os_ph2
            _ts_root = _os_ph2.path.abspath(_os_ph2.path.join(_os_ph2.path.dirname(__file__), '..', '..'))
            if _ts_root not in _sys_ph.path:
                _sys_ph.path.insert(0, _ts_root)
            from scripts.pump_signal_executor import emit_pump_signal
            import time as _time_ph

            _regime_ph = r['new_alerts'][0].get('brahma_regime', 'BEAR_TREND') if r['new_alerts'] else 'BEAR_TREND'
            for _a in r['new_alerts']:
                _scan_fmt = {
                    'symbol':    _a['symbol'],
                    'score':     _a['score'],
                    'valid':     _a['score'] >= 85,    # 独立通道门槛提升至85
                    'direction': 'LONG',
                    'signal_type': 'PUMP_SIGNAL',
                    'tight7d':   _a.get('tight_7d', 0),
                    'tight8h':   _a.get('tight_8h', 0),
                    'rsi':       _a.get('rsi', 50),
                    'shrink_h':  _a.get('shrink_hours', 0),
                    'vol_ratio': _a.get('vol_ratio', 1.0),
                    'chg24':     _a.get('chg_24h', 0),
                    'atr':       _a.get('atr', 0),
                    'atr_pct':   _a.get('atr_pct', 3.0),
                    'price':     _a.get('price', 0),
                    'ts':        _time_ph.time(),
                }
                if _scan_fmt['valid'] and _scan_fmt['price'] and _scan_fmt['atr']:
                    _sig = emit_pump_signal(_scan_fmt, _regime_ph)
                    if _sig:
                        print(f'[pump-hunter] PUMP_SIGNAL写入独立队列: {_a["symbol"]} score={_a["score"]}')
        except Exception as _e_ph:
            print(f'[pump-hunter] 独立通道写入失败（不影响主流）: {_e_ph}')
        # ── [END 独立通道] ──

    elif r['new_alerts']:
        for a in r['new_alerts'][:5]:
            print(f'  🚨{a["symbol"]:<18} score={a["score"]} | {" | ".join(a["reasons"][:2])}')

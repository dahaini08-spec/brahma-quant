#!/usr/bin/env python3
"""
import sys as _sys_ph, os as _os_ph
# [2026-07-06] 设计院修复: 独立通道 'No module named scripts' 根因
_ph_root = _os_ph.path.dirname(_os_ph.path.dirname(_os_ph.path.abspath(__file__)))
if _ph_root not in _sys_ph.path:
    _sys_ph.path.insert(0, _ph_root)
_ph_scripts = _os_ph.path.join(_ph_root, 'scripts')
if _ph_scripts not in _sys_ph.path:
    _sys_ph.path.insert(0, _ph_scripts)
del _sys_ph, _os_ph, _ph_root, _ph_scripts

暴涨猎手 · 纯脚本扫描器（零AI token消耗）
运行逻辑：
  1. 扫描全市场TIGHT形态+OI异动
  2. 评分 → 高分信号写入 new_alerts.json
  3. 与上次结果对比，只有「新出现」的高分信号才标记为需推送
  4. cron读取 need_push 标志，有则推送，无则HEARTBEAT_OK
"""
import requests, json, datetime, os, time, sys
from collections import defaultdict
from pathlib import Path

# ── SSOT推送地址：动态读取，永不硬编码 ──
def _get_jarvis_target() -> str:
    """从SSOT读取推送目标，环境变量→system_config→硬编码兜底"""
    if os.environ.get('JARVIS_TARGET'):
        return os.environ['JARVIS_TARGET']
    try:
        _ts = Path(__file__).parent.parent.parent
        if str(_ts) not in sys.path:
            sys.path.insert(0, str(_ts))
        from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
    except Exception:
        pass
    return '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075'  # 最终兜底

JARVIS_TARGET = _get_jarvis_target()

API   = 'https://fapi.binance.com'
DIR   = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(DIR, 'new_alerts.json')
LAST  = os.path.join(DIR, 'last_alerts.json')
LOG   = os.path.join(DIR, 'scan_log.jsonl')

EXCLUDE     = {'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT'}
MIN_VOL     = 50_000_000  # 内存优化: 2000万→5000万，候选池~50个   # 最低24H成交额 v2: 150万→2000万，控制候选<100个防超时
MAX_VOL     = 800_000_000  # 排除超大盘
MAX_CHG_ABS = 25.0         # 排除已大幅波动
PUSH_SCORE  = 75           # 触发推送的评分门槛
EXEC_SCORE  = 85           # 触发开单的评分门槛（v2.1 设计院 2026-07-03）

# ── [P0 设计院 2026-07-07] 暴涨已发生防漏判 ────────────
VOL_RATIO_EXPIRED   = 5.0   # vol_ratio超过此值=暴涨已发生，信号作废
PRICE_FROM_LOW_MAX  = 15.0  # 价格距近期最低点涨幅超过此值=追高风险，信号静默
SIGNAL_VALID_MIN    = 30    # 信号有效窗口（分钟），超时发二次提醒
SIGNAL_EXPIRY_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signal_expiry.json')

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
    # [2026-07-07 防御修复] API偶发返回dict/error时转为list，防止TypeError: string indices
    _raw_tickers = requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=15).json()
    if not isinstance(_raw_tickers, list):
        _raw_tickers = []  # API异常返回，降级为空，本次扫描跳过
    tickers = {t['symbol']: t for t in _raw_tickers
               if isinstance(t, dict) and t.get('symbol', '').endswith('USDT')}

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

                # ── [P0修复 2026-07-07] 暴涨已发生检测 ─────────────────
                # vol_ratio>=5x = 量能已炸裂，暴涨正在或已结束，信号过期
                if vol_ratio >= VOL_RATIO_EXPIRED:
                    reasons.append(f'⚠️SKIP:暴涨已发生(vol_ratio={vol_ratio:.1f}x>5x)')
                    score = -999  # 标记为无效，后面 if score >= PUSH_SCORE 自然过滤
                    comp = 99; rsi = 50; dist = 0
                    # 不break，继续让后续正常退出
                else:
                    # ── 价格离地距离检测 ──────────────────────────────────
                    price_from_low_pct = (price - min(lows[-6:])) / min(lows[-6:]) * 100 if min(lows[-6:]) > 0 else 0
                    if price_from_low_pct > PRICE_FROM_LOW_MAX:
                        reasons.append(f'⚠️SKIP:价格已离起跳点+{price_from_low_pct:.1f}%>{PRICE_FROM_LOW_MAX}%')
                        score = -999
                        comp = 99; rsi = 50; dist = 0
                    else:
                        if comp < 15:
                            score += 25; reasons.append(f'TIGHT压缩{comp:.0f}%')
                        elif comp < 25:
                            score += 12; reasons.append(f'MODERATE压缩{comp:.0f}%')

                        if vol_ratio < 0.5:
                            score += 10; reasons.append(f'量能萎缩{vol_ratio:.2f}x')

                # RSI简算（仅在score有效时计算）
                if score != -999:
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
                # ── [P1修复 2026-07-07] 计算入场区+SL+TP+有效窗口 ──────
                _atr_pct = comp * 0.3 if comp < 99 else 5.0   # 简算ATR%
                _sl_pct  = max(_atr_pct * 1.5, 4.0)
                _tp_mult = 1.8
                _entry_lo = round(price * 0.995, 6)
                _entry_hi = round(price * 1.005, 6)
                _sl_price = round(price * (1 - _sl_pct/100), 6)
                _tp_price = round(price * (1 + _sl_pct * _tp_mult / 100), 6)
                _price_from_low = round((price - min(lows[-6:])) / min(lows[-6:]) * 100, 1) if (isinstance(kl, list) and len(kl) >= 12 and min([float(k[3]) for k in kl[-6:]]) > 0) else 0
                _expire_ts = time.time() + SIGNAL_VALID_MIN * 60
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
                    'entry_lo': _entry_lo, 'entry_hi': _entry_hi,
                    'sl_price': _sl_price, 'sl_pct': round(_sl_pct,1),
                    'tp_price': _tp_price, 'rr': _tp_mult,
                    'price_from_low_pct': _price_from_low,
                    'expire_ts': _expire_ts,
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

    # [v2.1 设计院 2026-07-04] 时间窗口去重：6H内同标的不重复推送
    # 原逻辑：只要上次见过就永远不推 → 持续高分标的永远被去重
    import time as _time_dedup
    _DEDUP_WINDOW = 6 * 3600  # 6小时
    _now_ts = _time_dedup.time()
    _last_ts = {}
    try:
        _last_ts = {a['symbol']: last_data.get('scan_ts', _now_ts - 99999)
                    for a in last_data.get('alerts', []) if a.get('score', 0) >= PUSH_SCORE}
    except: pass
    new_alerts = [a for a in alerts
                  if (_now_ts - _last_ts.get(a['symbol'], 0)) > _DEDUP_WINDOW]

    # ── 梵天体制感知（设计院修正版 2026-06-29）──────────────────────
    # 设计哲学：体制只调整「仓位+止盈」，不折损信号分，不屏蔽妖币
    # 原因：BEAR_TREND逆势妖往往是最强逼空，不能因体制压制信号本身
    # [v2.1 设计院 2026-07-03] 体制仓位/止盈完全重构
    # 核心逻辑：BEAR_TREND是逼仓最肥沃的土壤，仓位最高；BULL_TREND空头少，仓位最低
    _REGIME_POS = {
        'BEAR_TREND':    2.5,  # ⬆️ 熊市逼仓最猛
        'BEAR_RECOVERY': 2.0,  # 反弹适中
        'CHOP_MID':      2.0,  # 震荡适中
        'BEAR_EARLY':    1.5,  # 熊初保守
        'BULL_EARLY':    2.0,  # 牛初适中
        'BULL_TREND':    1.5,  # ⬇️ 牛市空头少
    }
    _REGIME_TP  = {
        'BEAR_TREND':    1.8,  # ⬆️ 熊市逼仓最暴力，追主升浪
        'BEAR_RECOVERY': 1.5,  # 适中
        'CHOP_MID':      1.3,  # 保守
        'BEAR_EARLY':    1.2,
        'BULL_EARLY':    1.3,
        'BULL_TREND':    1.2,  # ⬇️ 牛市快进快出
    }
    try:
        from brahma_brain.universal_asset_router import get_regime_cached
        _btc_regime = get_regime_cached('BTCUSDT')
        _pos = _REGIME_POS.get(_btc_regime, 2.0)
        _tp  = _REGIME_TP.get(_btc_regime, 1.0)
        for a in alerts + new_alerts:
            a['brahma_regime']         = _btc_regime
            a['exec_pos_pct']          = _pos   # 仓位参考，不改信号分
            a['exec_tp_mult']          = _tp    # 止盈倍数参考
            a['exec_eligible']         = a.get('score', 0) >= EXEC_SCORE   # v2.1: 75→85
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
        'scan_ts':      __import__('time').time(),
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

    # ── [P1+P2+P3 设计院全面升级 2026-07-07] ─────────────────────────────
    # P1: 新版推送格式（含价格+入场区+有效窗口+测距）
    # P2: score≥85 + brahma valid → 自动执行
    # P3: 写入signal_expiry.json，供30分钟后二次提醒检测
    import subprocess as _sp
    import time as _time_push

    def _send_jarvis(msg):
        _sp.run(['openclaw','message','send','--channel','jarvis','--target',JARVIS_TARGET,'--message',msg],
                capture_output=True, timeout=15)

    def _format_alert_v3(a, auto_executed=False, exec_result=None):
        """新版推送格式 v3.0：含完整决策信息"""
        lvl = '💣' if a.get('score',0) >= 85 else '🚨'
        regime = a.get('brahma_regime','?')
        pos_pct = a.get('exec_pos_pct', 1.0)
        sym = a['symbol']
        price = a.get('price', 0)
        score = a.get('score', 0)
        oi_chg = a.get('oi_chg', 0)
        funding = a.get('funding', 0)
        short_pct = a.get('short_pct', 50)
        entry_lo = a.get('entry_lo', price * 0.995)
        entry_hi = a.get('entry_hi', price * 1.005)
        sl_price = a.get('sl_price', 0)
        sl_pct   = a.get('sl_pct', 5.0)
        tp_price = a.get('tp_price', 0)
        rr       = a.get('rr', 1.8)
        from_low = a.get('price_from_low_pct', 0)
        reasons_str = ' | '.join(a.get('reasons',[])[:3])
        expire_min = SIGNAL_VALID_MIN

        from_low_tag = f'+{from_low:.1f}%（安全范围✅）' if from_low <= 8 else f'+{from_low:.1f}%（注意追高⚠️）'

        exec_line = ''
        if auto_executed and exec_result:
            status = exec_result.get('status','?')
            fill   = exec_result.get('fill_price', price)
            exec_line = f'\n🤖 已自动执行：{status} | 成交={fill:.6f}'
        elif score >= EXEC_SCORE:
            exec_line = f'\n🤖 score≥{EXEC_SCORE}→梵天验证中...'

        return (
            f'{lvl} 暴涨猎手 · {sym}\n'
            f'⏰ {datetime.datetime.utcnow().strftime("%H:%M")} UTC | ⚡ 有效窗口：{expire_min}分钟\n'
            f'📍 当前价：{price:.6g} | 距起跳点：{from_low_tag}\n'
            f'📊 score={score} | OI+{oi_chg:.0f}% | FR={funding:.4f}% | 空头{short_pct:.0f}%\n'
            f'📈 {reasons_str}\n'
            f'🎯 入场区：{entry_lo:.6g}~{entry_hi:.6g}\n'
            f'   止损：{sl_price:.6g}（-{sl_pct:.1f}%）  目标：{tp_price:.6g}  R:R={rr}\n'
            f'   仓位：{pos_pct:.1f}% NAV | 体制：{regime}'
            f'{exec_line}\n'
            f'--------------------\n'
            f'⚠️ 超{expire_min}分钟未操作→信号自动作废'
        )

    if r['need_push'] and r['new_alerts']:
        # 写入过期追踪文件（P3用）
        _expiry_data = {}
        try:
            if os.path.exists(SIGNAL_EXPIRY_FILE):
                _expiry_data = json.load(open(SIGNAL_EXPIRY_FILE))
        except: pass

        for a in r['new_alerts'][:5]:
            sym = a['symbol']
            score = a.get('score', 0)
            auto_executed = False
            exec_result = None

            # ── P2: score≥85 → 梵天验证 → 自动执行 ──────────────
            if score >= EXEC_SCORE:
                try:
                    import sys as _sys_exec
                    _ts_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
                    if _ts_root not in _sys_exec.path:
                        _sys_exec.path.insert(0, _ts_root)
                    from brahma_brain.brahma_analysis_runner import run_analysis
                    _result = run_analysis(sym)
                    _valid = _result.get('valid_signal', False)
                    _brahma_score = _result.get('score', 0)
                    print(f'[pump-hunter] P2梵天验证 {sym}: valid={_valid} score={_brahma_score}')
                    if _valid:
                        # 自动执行（写入signal_bus，由sub-executor消费）
                        try:
                            from scripts.signal_bus import write as _sb_write
                            _px = float(a.get('price', 0))
                            _atr = float(a.get('sl_pct', 5.0)) / 100
                            _sb_write({
                                'source':    'pump_auto',
                                'symbol':    sym,
                                'direction': 'LONG',
                                'score':     float(score),
                                'valid':     True,
                                'regime':    a.get('brahma_regime','?'),
                                'entry_lo':  a.get('entry_lo', _px*0.995),
                                'entry_hi':  a.get('entry_hi', _px*1.005),
                                'sl':        a.get('sl_price', _px*(1-_atr*1.5)),
                                'sl_pct':    a.get('sl_pct', 5.0),
                                'tp1':       a.get('tp_price', _px*1.15),
                                'rr1':       a.get('rr', 1.8),
                                'expires_at': _time_push.time() + SIGNAL_VALID_MIN*60,
                                'pos_pct':   a.get('exec_pos_pct', 1.0),
                            })
                            auto_executed = True
                            exec_result = {'status': '已写入执行队列', 'fill_price': a.get('price',0)}
                            print(f'[pump-hunter] P2自动执行写入信号总线: {sym}')
                        except Exception as _e_sb:
                            print(f'[pump-hunter] P2信号总线写入失败: {_e_sb}')
                    else:
                        print(f'[pump-hunter] P2梵天验证未通过 {sym}: valid=False')
                except Exception as _e_exec:
                    print(f'[pump-hunter] P2梵天验证异常 {sym}: {_e_exec}')

            # ── P1: 发送新版格式推送 ──────────────────────────────
            msg = _format_alert_v3(a, auto_executed=auto_executed, exec_result=exec_result)
            _send_jarvis(msg)
            print(f'[pump-hunter] P1推送完成: {sym} score={score}')

            # ── P3: 记录信号过期时间，用于30分钟后二次提醒 ────────
            _expiry_data[sym] = {
                'score': score,
                'price': a.get('price', 0),
                'entry_lo': a.get('entry_lo', 0),
                'entry_hi': a.get('entry_hi', 0),
                'sl_price': a.get('sl_price', 0),
                'tp_price': a.get('tp_price', 0),
                'expire_ts': a.get('expire_ts', _time_push.time() + SIGNAL_VALID_MIN*60),
                'pushed_ts': _time_push.time(),
                'auto_executed': auto_executed,
                'reminded': False,
            }

        # 保存过期追踪
        try:
            json.dump(_expiry_data, open(SIGNAL_EXPIRY_FILE, 'w'), indent=2)
        except: pass

        print(f'[pump-hunter] 推送完成 {len(r["new_alerts"])}个信号（新版格式v3.0）')

    # ── P3: 检查即将过期的信号，发二次提醒 ───────────────────────
    try:
        _now_ts = _time_push.time()
        _expiry_data = {}
        if os.path.exists(SIGNAL_EXPIRY_FILE):
            _expiry_data = json.load(open(SIGNAL_EXPIRY_FILE))
        _updated = False
        for _sym, _info in list(_expiry_data.items()):
            _expire_ts = _info.get('expire_ts', 0)
            _reminded  = _info.get('reminded', False)
            _pushed_ts = _info.get('pushed_ts', 0)
            _auto_exec = _info.get('auto_executed', False)
            # 窗口剩余<5分钟 且 未提醒 且 非自动执行
            _remaining = _expire_ts - _now_ts
            if 0 < _remaining < 300 and not _reminded and not _auto_exec:
                _remind_msg = (
                    f'⏰ 信号即将过期：{_sym}\n'
                    f'   score={_info.get("score")} | 入场={_info.get("entry_lo"):.6g}~{_info.get("entry_hi"):.6g}\n'
                    f'   还剩约{int(_remaining/60)}分钟 | 未操作将自动作废'
                )
                _send_jarvis(_remind_msg)
                _expiry_data[_sym]['reminded'] = True
                _updated = True
                print(f'[pump-hunter] P3二次提醒发送: {_sym}')
            elif _remaining <= 0:
                # 已过期，清理
                del _expiry_data[_sym]
                _updated = True
        if _updated:
            json.dump(_expiry_data, open(SIGNAL_EXPIRY_FILE,'w'), indent=2)
    except Exception as _e_p3:
        print(f'[pump-hunter] P3过期检查异常: {_e_p3}')

    if r.get('alerts') and not r['need_push']:
        for a in r['alerts'][:3]:
            print(f'  ⚠️{a["symbol"]:<18} score={a["score"]} | skip原因: {" | ".join(a["reasons"][:2])}')

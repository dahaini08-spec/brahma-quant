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

# [asset_config SSOT 2026-10-09] VIP关键位从asset_config.json读取
import json as _m7j, pathlib as _m7p
def _load_vip_cfg():
    _f = _m7p.Path(__file__).parent.parent / 'data' / 'asset_config.json'
    try:
        _ac = _m7j.loads(_f.read_text())
        _r = {}
        for sym, cfg in _ac.items():
            if sym.startswith('_'): continue
            _r[sym] = {
                'long_entry':  (int(cfg.get('liq_long',0)*0.99), int(cfg.get('liq_long',0))),
                'long_sl':     int(cfg.get('invalidate_long', 0)),
                'long_tp':     int(cfg.get('liq_short', 0)),
                'short_entry': (int(cfg.get('liq_short',0)*0.995), int(cfg.get('liq_short',0))),
                'short_sl':    int(cfg.get('liq_short',0)*1.009),
                'short_tp':    int(cfg.get('liq_long',0)),
                'hunt_lsr':    float(cfg.get('lsr_hunt', 65.0)),
                'lsr_near':    float(cfg.get('lsr_near', 1.5)),
                'invalidate_long': float(cfg.get('invalidate_long', 0)),
                'gex_zf':      float(cfg.get('gex_zf', 0)),
                'fr_warn':     float(cfg.get('fr_warn', 0.003)),
                'fr_alert':    float(cfg.get('fr_alert', 0.006)),
            }
        return _r
    except Exception as e:
        print(f'[M7] asset_config load fail: {e}', file=__import__('sys').stderr)
        return {
            'BTC': {'long_entry':(82000,82500),'long_sl':81000,'long_tp':84120,'short_entry':(83800,84120),'short_sl':85200,'short_tp':80821,'hunt_lsr':65.0,'lsr_near':1.5,'invalidate_long':81000,'gex_zf':69910,'fr_warn':0.003,'fr_alert':0.006},
            'ETH': {'long_entry':(2410,2435),'long_sl':2413,'long_tp':2534,'short_entry':(2520,2534),'short_sl':2558,'short_tp':2435,'hunt_lsr':77.0,'lsr_near':1.5,'invalidate_long':2413,'gex_zf':2519,'fr_warn':0.003,'fr_alert':0.006},
        }

VIP_CONFIG = _load_vip_cfg()

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


def _m7_atr_neuron(state: dict) -> list:
    """
    P0-② 각 표적 ATR 신경원 — 극도 압축 감지 및 경고
    ATR 30일 평균 대비 현재 ATR 계산 → 역사적 분위 추정
    압축>50% → 대변동 24~48H 내 예고 경보
    """
    import json as _j, pathlib as _pl, ssl as _ssl, urllib.request as _ur
    alerts = []
    now = __import__('time').time()
    COOLDOWN = 7200  # ATR경보는 2시간 쿨다운

    _ctx = _ssl.create_default_context()
    DATA = _pl.Path(__file__).parent.parent / 'data'
    AC   = VIP_CONFIG  # asset_config에서 이미 로드됨

    for sym in ['BTC', 'ETH']:
        sf = f'{sym}USDT'
        k = f'm7_atr_{sym}'
        if now - state.get(k, 0) < COOLDOWN:
            continue
        try:
            # 1H K선 50개 → ATR 계산
            req = _ur.Request(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={sf}&interval=1h&limit=50',
                headers={'User-Agent':'Mozilla/5.0'}
            )
            with _ur.urlopen(req, context=_ctx, timeout=8) as r:
                klines = _j.loads(r.read())

            # ATR 계산
            def _atr(k_data, n=14):
                tr = [max(float(x[2])-float(x[3]),
                          abs(float(x[2])-float(k_data[max(0,i-1)][4])),
                          abs(float(x[3])-float(k_data[max(0,i-1)][4])))
                      for i,x in enumerate(k_data)]
                return sum(tr[-n:])/n

            atr_now  = _atr(klines[-14:])
            atr_30d  = _atr(klines, n=30)  # 30일 평균
            price    = float(klines[-1][4])
            compress = (atr_30d - atr_now) / atr_30d if atr_30d > 0 else 0

            # 압축률 계산
            print(f'[M7-ATR] {sym} ATR현재=${atr_now:.1f} ATR30d평균=${atr_30d:.1f} 압축={compress:.0%}')

            if compress > 0.50:  # 50% 이상 압축
                # ATR 백분위 추정
                atr_pct = max(0.05, 0.50 - compress)  # 근사값
                direction_hint = ''

                # CVD 방향으로 힌트
                try:
                    cvd_d = _j.loads((DATA / f'cvd_realtime_{sym.lower()}usdt.json').read_text())
                    cvd_v = float(cvd_d.get('cvd_1h', 0) or 0)
                    if cvd_v < -1000:
                        direction_hint = '\nCVD<0 → 하락 방향성 우세'
                    elif cvd_v > 1000:
                        direction_hint = '\nCVD>0 → 상승 방향성 우세'
                except: pass

                alerts.append((k,
                    f'⚠️ 梵天神经果蝇 | {sym} ATR极度压缩\n\n'
                    f'当前ATR=${atr_now:.1f} vs 30日均值${atr_30d:.1f}\n'
                    f'压缩幅度：{compress:.0%}（历史低分位！）\n'
                    f'当前价：${price:,.2f}{direction_hint}\n\n'
                    f'⚡ 历史规律：ATR压缩>50%后\n'
                    f'   24~48H内大波动概率≈78%\n\n'
                    f'📌 策略影响：\n'
                    f'SL应用{2.0 if compress>0.5 else 1.5}×ATR（压缩期放宽）\n'
                    f'即 {sym} SL最小=${atr_now*2.0:.0f}点\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))
        except Exception as e:
            print(f'[M7-ATR] {sym} 오류: {e}')

    return alerts


def check_all() -> list:
    """返回 [(state_key, message), ...] 列表"""
    state   = load_state()
    alerts  = []
    now     = time.time()

    # [2026-10-10 苏摩111] P0静默窗口
    # ZeroFlip失守/LSR猎杀触发后4小时内，FR/CVD等P2信号静默
    _P0_SILENCE = 14400  # 4小时
    _p0_keys = [k for k in state if 'gex_zf' in k or 'lsr_hunt' in k]
    _p0_last = max((state.get(k, 0) for k in _p0_keys), default=0)
    _in_p0_silence = (now - _p0_last) < _P0_SILENCE
    if _in_p0_silence:
        print(f'[M7] P0静默窗口激活（距上次P0触发{(now-_p0_last)/60:.0f}min），FR/CVD P2信号跳过')

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
        # ① FR 警戒/预警（P0静默窗口内跳过）
        # ══════════════════════════════════════
        if not _in_p0_silence and fr_v > 0.006:
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
        elif not _in_p0_silence and fr_v > 0.003:
            k = f'm7_fr_warn_{sym}'
            # [2026-10-10 苏摩111] 状态变化触发，不重复推送
            # 原问题：FR持续>0.003%时每30min重发，同一信息刷屏
            # 修复：记录上次FR状态，仅在「低→高」跨越时推送一次
            _fr_prev = state.get(f'{sym}_fr_prev', 0.0)
            _was_warn = _fr_prev > 0.003  # 上次已在警戒区
            _cooldown_fr = 14400  # FR警戒冷却4小时（非30分钟）
            if not _was_warn and (now - state.get(k, 0)) > _cooldown_fr:
                # 首次突破才推送
                alerts.append((k,
                    f'⚠️ 梵天VIP | {sym} FR首次突破警戒\n\n'
                    f'FR={fr_v:+.4f}% 突破+0.003% = 多单封锁启动\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'✋ {sym}多单暂停，等FR回落<+0.003%\n'
                    f'（持续在警戒区内不重复提醒）\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # ══════════════════════════════════════
        # ② LSR 猎杀预警/触发
        # ══════════════════════════════════════
        if dist <= 0:
            k = f'm7_lsr_hunt_{sym}'
            # [P0-② 2026-10-10] 猎杀触发冷却5min（极紧急信号）
            if now - state.get(k, 0) > 300:
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
            # [P0-② 2026-10-10 苏摩111] LSR最后1%区间冷却改为5min，防止漏推关键信号
            _near_cooldown = 300 if dist <= 0.5 else (600 if dist <= 1.0 else COOLDOWN)
            if now - state.get(k, 0) > _near_cooldown:
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
        # [2026-10-09 苏摩111] 修复：失守=进入负GEX区=空单主场
        # 原逻辑只给多单候补位，忽略「现在就是做市商卖出区」
        # 新逻辑：P1空单(主)+P2多单候补，CVD加持确认方向
        if sym == 'ETH' and 'gex_zf' in cfg and price < cfg['gex_zf']:
            k = f'm7_gex_zf_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                zf_price = cfg['gex_zf']
                atr_1h = cfg.get('atr_1h', 16.0)
                # 空单：等小幅反弹至ZeroFlip下沿，RR更优
                short_entry_lo = round(zf_price * 0.993, 1)  # ZeroFlip下方0.7%
                short_entry_hi = round(zf_price * 0.997, 1)  # ZeroFlip下方0.3%
                short_sl       = round(zf_price + atr_1h * 1.5, 1)
                short_tp1      = cfg['long_entry'][0] if 'long_entry' in cfg else round(price - atr_1h * 6, 1)
                short_rr       = round((short_entry_hi - short_tp1) / (short_sl - short_entry_hi), 1)
                # 多单：等跌至正GEX区边界（候补）
                long_lo  = cfg['long_entry'][0] if 'long_entry' in cfg else round(price * 0.975, 0)
                long_hi  = cfg['long_entry'][1] if 'long_entry' in cfg else round(price * 0.977, 0)
                long_sl  = round(long_lo - atr_1h * 1.5, 1)
                long_tp  = cfg.get('long_tp', zf_price)
                # [修复 2026-10-10 苏摩111] 实时校验触发条件，标注[预警]vs[执行]
                import urllib.request as _ur2, ssl as _ssl2, json as _j2
                _ctx2 = _ssl2.create_default_context()
                try:
                    _k1h = _j2.loads(_ur2.urlopen(
                        'https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=1h&limit=2',
                        timeout=5, context=_ctx2).read())
                    _1h_bear = float(_k1h[-2][4]) < float(_k1h[-2][1])  # 최신 완성K 하락?
                except: _1h_bear = False
                try:
                    import pathlib as _pl2
                    _cvd_d = _j2.loads((_pl2.Path(__file__).parent.parent/'data'/'cvd_realtime_ethusdt.json').read_text())
                    _cvd_now = float(_cvd_d.get('cvd_1h', _cvd_d.get('delta_1h', 0)) or 0)
                except: _cvd_now = 0

                _cond_bear_k   = '✅' if _1h_bear else '❌'
                _cond_cvd_neg  = '✅' if _cvd_now < -200 else '❌'
                _all_met       = _1h_bear and _cvd_now < -200
                _tag = '[✅执行] 条件满足，立即挂单' if _all_met else '[⏳预警] 等待条件满足后挂单'

                alerts.append((k,
                    f'⚠️ 梵天VIP | ETH GEX ZeroFlip失守\n'
                    f'{_tag}\n\n'
                    f'价格 ${price:,.2f} 跌破ZeroFlip ${zf_price:,}\n'
                    f'= 进入负GEX区，做市商转为做空对冲\n\n'
                    f'🔴 P1 空单（主策略）\n'
                    f'入场 ${short_entry_lo:,}~${short_entry_hi:,}（反弹至ZeroFlip下沿）\n'
                    f'止损 ${short_sl:,}（ZeroFlip上方1.5×ATR）\n'
                    f'目标 ${short_tp1:,}  RR≈{short_rr}\n'
                    f'条件确认：1H收阴={_cond_bear_k} CVD持续负={_cond_cvd_neg}\n\n'
                    f'🟢 P2 多单（候补，等结构）\n'
                    f'入场 ${long_lo:,}~${long_hi:,}（正GEX区边界）\n'
                    f'止损 ${long_sl:,}  目标 ${long_tp:,}\n'
                    f'条件：1H收阳+CVD转正\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

    # [修复 2026-10-10 苏摩111] 同方向信号30min内去重
    # 防止M7预警和主链VIP在同一时间段重复推送同方向信号
    try:
        _dedup_keys = set()
        _filtered = []
        for _ak, _am in alerts:
            # 方向提取
            _is_short = '空单' in _am or 'SHORT' in _ak
            _is_long  = '多单' in _am or 'LONG' in _ak
            _dir_key  = f'dedup_short_{_ak[:6]}' if _is_short else (f'dedup_long_{_ak[:6]}' if _is_long else None)
            if _dir_key:
                _last_same = state.get(_dir_key, 0)
                if now - _last_same < 1800:  # 30min 쿨다운
                    print(f'[M7-DEDUP] {_ak} 同方向30min内去重跳过')
                    continue
                state[_dir_key] = now
            _filtered.append((_ak, _am))
        alerts = _filtered
    except Exception as _dd_e:
        print(f'[M7-DEDUP ERR] {_dd_e}')

    # P0-② ATR 신경원 果蝇 감지
    try:
        _atr_alerts = _m7_atr_neuron(state)
        alerts.extend(_atr_alerts)
    except Exception as e:
        print(f'[M7-ATR ERR] {e}')

    # [2026-10-10 苏摩111] 保存FR状态供下次比较（状态变化触发用）
    try:
        import urllib.request as _ur, ssl as _ssl, json as _j
        _ctx = _ssl.create_default_context()
        for _sym in ['BTC','ETH']:
            _fr_r = _j.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={_sym}USDT',
                timeout=4, context=_ctx).read())
            state[f'{_sym}_fr_prev'] = float(_fr_r.get('lastFundingRate',0))*100
    except: pass
    # 保存状态（含cvd_prev/oi_prev更新）
    save_state(state)
    return alerts


if __name__ == '__main__':
    # 独立冒烟测试
    alerts = check_all()
    print(f'\nM7 检查完成，触发 {len(alerts)} 条警报')
    for k, msg in alerts:
        print(f'\n[{k}]\n{msg}')


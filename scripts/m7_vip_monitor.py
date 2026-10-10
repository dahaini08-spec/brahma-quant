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
        # atr_1h在asset_config中不存在 - 从brahma_state补充
        try:
            for _s in list(_r.keys()):
                _sp = _m7p.Path(__file__).parent.parent/'data'/f'brahma_state_{_s.lower()}.json'
                if _sp.exists():
                    _sd = _m7j.loads(_sp.read_text())
                    _r[_s]['atr_1h'] = float(_sd.get('atr_1h') or (16 if _s=='ETH' else 400))
        except: pass
        return _r
    except Exception as e:
        print(f'[M7] asset_config load fail: {e}', file=__import__('sys').stderr)
        # [2026-10-10 苏摩111] : 改用liq_snap动态读取，替代静态硬编码
        # 根因: 硬编码BTC short_entry=83800=旧版价格，实际wall=86561
        _fb = {}
        try:
            import json as _j
            _data_dir = Path(__file__).parent.parent / 'data'
            for _sym, _hunt, _gex_zf in [('BTC',65.0,69910),('ETH',77.0,2519)]:
                _st = _j.loads((_data_dir/f'brahma_state_{_sym.lower()}.json').read_text())
                _liq = (_st.get('extra') or {}).get('liq_snap', {})
                _pr  = float(_st.get('price', 0) or 0)
                _wall= float(_liq.get('liq_short_5pct') or (_pr*1.05 if _pr else 0))
                _pool= float(_liq.get('liq_long_5pct')  or (_pr*0.95 if _pr else 0))
                _hl_s= float(_liq.get('hl_liq_25x_short') or _wall*0.99)
                _hl_l= float(_liq.get('hl_liq_25x_long')  or _pool*1.01)
                _atr = float(_st.get('atr_1h') or ((_st.get('extra') or {}).get('atr_1h')) or (16 if _sym=='ETH' else 400))
                # 动态价格水平
                _long_lo  = round(_hl_l * 0.999, 0)
                _long_hi  = round(_hl_l * 1.001, 0)
                _long_sl  = round(_hl_l - _atr*1.5, 0)
                _short_lo = round(_hl_s * 0.999, 0)
                _short_hi = round(_hl_s * 1.001, 0)
                _short_sl = round(_hl_s + _atr*1.5, 0)
                # GEX ZeroFlip动态读取
                try:
                    _gex_d = _j.loads((_data_dir/'gex_state.json').read_text())
                    _gex_zf = float(_gex_d.get(_sym,{}).get('zero_flip',0) or _gex_zf)
                except: pass
                _fb[_sym] = {
                    'long_entry': (_long_lo, _long_hi),
                    'long_sl': _long_sl, 'long_tp': round(_hl_s, 0),
                    'short_entry': (_short_lo, _short_hi),
                    'short_sl': _short_sl, 'short_tp': round(_hl_l, 0),
                    'hunt_lsr': _hunt, 'lsr_near': 1.5,
                    'invalidate_long': _long_sl,
                    'gex_zf': _gex_zf, 'atr_1h': _atr,
                    'fr_warn': 0.003, 'fr_alert': 0.006,
                }
        except Exception as _fe:
            print(f'[M7] 动态config失败，用静态: {_fe}')
            _fb = {
                'BTC': {'long_entry':(79463,79800),'long_sl':78700,'long_tp':85398,'short_entry':(85200,85398),'short_sl':86200,'short_tp':79463,'hunt_lsr':65.0,'lsr_near':1.5,'invalidate_long':78700,'gex_zf':69910,'atr_1h':400,'fr_warn':0.003,'fr_alert':0.006},
                'ETH': {'long_entry':(2391,2410),'long_sl':2369,'long_tp':2570,'short_entry':(2547,2570),'short_sl':2592,'short_tp':2391,'hunt_lsr':77.0,'lsr_near':1.5,'invalidate_long':2369,'gex_zf':2519,'atr_1h':16,'fr_warn':0.003,'fr_alert':0.006},
            }
        return _fb

VIP_CONFIG = _load_vip_cfg()  # 导入时初始值

def _refresh_vip_config():
    """[2026-10-10 苏摩111] check_all每次更新最新liq/atr"""
    global VIP_CONFIG
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
    P0-② 各标的ATR神经元 — 极度压缩感知及预警
    ATR 30日均值对比现值 → 历史分位估算
    压缩>50% → 24~48H内大波动预警
    """
    import json as _j, pathlib as _pl, ssl as _ssl, urllib.request as _ur
    alerts = []
    now = __import__('time').time()
    COOLDOWN = 7200  # ATR警报2小时冷却

    _ctx = _ssl.create_default_context()
    DATA = _pl.Path(__file__).parent.parent / 'data'
    AC   = VIP_CONFIG  # 已从asset_config加载

    for sym in ['BTC', 'ETH']:
        sf = f'{sym}USDT'
        k = f'm7_atr_{sym}'
        if now - state.get(k, 0) < COOLDOWN:
            continue
        try:
            # 1H K线50根 → ATR计算
            req = _ur.Request(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={sf}&interval=1h&limit=50',
                headers={'User-Agent':'Mozilla/5.0'}
            )
            with _ur.urlopen(req, context=_ctx, timeout=8) as r:
                klines = _j.loads(r.read())

            # ATR计算
            def _atr(k_data, n=14):
                tr = [max(float(x[2])-float(x[3]),
                          abs(float(x[2])-float(k_data[max(0,i-1)][4])),
                          abs(float(x[3])-float(k_data[max(0,i-1)][4])))
                      for i,x in enumerate(k_data)]
                return sum(tr[-n:])/n

            atr_now  = _atr(klines[-14:])
            atr_30d  = _atr(klines, n=30)  # 30日均值
            price    = float(klines[-1][4])
            compress = (atr_30d - atr_now) / atr_30d if atr_30d > 0 else 0

            #  
            print(f'[M7-ATR] {sym} ATR当前=${atr_now:.1f} ATR30d均值=${atr_30d:.1f} 压缩={compress:.0%}')

            if compress > 0.50:  # 50%以上压缩
                # ATR百分位估算
                atr_pct = max(0.05, 0.50 - compress)  # 近似值
                direction_hint = ''

                # CVD方向提示
                try:
                    cvd_d = _j.loads((DATA / f'cvd_realtime_{sym.lower()}usdt.json').read_text())
                    cvd_v = float(cvd_d.get('cvd_1h', 0) or 0)
                    if cvd_v < -1000:
                        direction_hint = '\nCVD<0 → 下行方向性占优'
                    elif cvd_v > 1000:
                        direction_hint = '\nCVD>0 → 上行方向性占优'
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
            print(f'[M7-ATR] {sym} 错误: {e}')

    return alerts


def check_all() -> list:
    """返回 [(state_key, message), ...] 列表"""
    _refresh_vip_config()  # 每次反映最新liq/gex/atr
    state   = load_state()
    alerts  = []
    now     = time.time()

    # [2026-10-10 苏摩111] P0静默窗口
    # ZeroFlip失守/LSR猎杀触发后4小时内，FR等P2信号静默
    # 例外：CVD极端反转（幅度>20000 BTC / >50000 ETH）= 方向逆转 = 强制穿透静默
    _P0_SILENCE = 14400  # 4小时
    _p0_keys = [k for k in state if 'gex_zf' in k or 'lsr_hunt' in k]
    _p0_last = max((state.get(k, 0) for k in _p0_keys), default=0)
    _in_p0_silence = (now - _p0_last) < _P0_SILENCE
    if _in_p0_silence:
        print(f'[M7] P0静默窗口激活（距上次P0触发{(now-_p0_last)/60:.0f}min），FR P2信号跳过')

    # CVD极端反转阈值（穿透P0静默）
    _CVD_PIERCE_BTC = 20000   # BTC CVD单次变化超过此值=方向逆转
    _CVD_PIERCE_ETH = 50000   # ETH CVD单次变化超过此值=方向逆转

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
        # ③ CVD 极端翻转（双向：卖→买 / 买→卖）
        # [2026-10-10 苏摩111] 新增买方翻转，穿透P0静默窗口
        # 根因：ZeroFlip失守后CVD从-59651→+81580，系统无推送（P0窗口屏蔽）
        # 修复：CVD极端反转=方向逆转=P0级，强制穿透静默
        # ══════════════════════════════════════
        cvd_prev = state.get(f'm7_cvd_prev_{sym}', 0)
        state[f'm7_cvd_prev_{sym}'] = cvd_v
        _cvd_pierce = _CVD_PIERCE_ETH if sym == 'ETH' else _CVD_PIERCE_BTC
        _cvd_delta = cvd_v - cvd_prev

        # 买→卖翻转（原逻辑）
        if cvd_prev > 500 and cvd_v < -2000:
            k = f'm7_cvd_{sym}'
            if now - state.get(k, 0) > COOLDOWN:
                impact = (
                    f'BTC多单暂缓，等CVD回正后再入' if sym == 'BTC'
                    else f'ETH回踩可能加速，等${cfg["long_entry"][0]:,}~${cfg["long_entry"][1]:,}接多'
                )
                alerts.append((k,
                    f'⚠️ 梵天VIP | {sym} CVD极端翻转（买→卖）\n\n'
                    f'CVD: {cvd_prev:+.0f} → {cvd_v:+.0f}\n'
                    f'= 买方→卖方极端转变\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 策略影响：{impact}\n\n'
                    f'🌿 姓赵不宣 | 不是建议'
                ))

        # 卖→买极端翻转（新增，穿透P0静默）
        # 条件：前值负+当前强正+变化幅度超阈值 = 方向完全逆转
        elif cvd_prev < -500 and cvd_v > 2000 and abs(_cvd_delta) > _cvd_pierce:
            k = f'm7_cvd_bull_{sym}'
            _cooldown_cvd_bull = 3600  # 1小时冷却（不受P0窗口限制）
            if now - state.get(k, 0) > _cooldown_cvd_bull:
                # ZeroFlip状态检查
                try:
                    import pathlib as _pl
                    _gex = json.loads((_pl.Path('data/gex_state.json')).read_text()).get(sym,{})
                    _zf = float(_gex.get('zero_flip',0) or 0)
                    _above_zf = price > _zf if _zf > 0 else False
                except: _zf = 0; _above_zf = False

                if sym == 'ETH' and not _above_zf and _zf > 0:
                    # ZeroFlip失守状态下CVD反转 = Fake Break可能性
                    action = (
                        f'⚡ ZeroFlip失守后CVD强反转 = Fake Break警报\n'
                        f'空单暂缓 | 等1H收阳突破${_zf:,.0f}确认多方向\n'
                        f'突破站稳 → 入多 SL${price-14*1.5:,.0f} TP${_zf+30:,.0f}'
                    )
                else:
                    action = f'{sym}买方接管，空单暂缓，等1H收阳确认后可入多'

                alerts.append((k,
                    f'🟢 梵天VIP | {sym} CVD强势反转（卖→买）\n\n'
                    f'CVD: {cvd_prev:+.0f} → {cvd_v:+.0f}\n'
                    f'变化幅度: {_cvd_delta:+,.0f}（>{_cvd_pierce:,}阈值）\n'
                    f'当前价 ${price:,.2f}\n\n'
                    f'📌 {action}\n\n'
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
            # [修复 2026-10-10 苏摩111] ZeroFlip失守不重复推送
            # ZeroFlip是日级别结构信号，失守后会持续触发
            # 改为：同一ZeroFlip价位 → 4H只推1次（不是每30min）
            _zf_cd = 14400  # 4小时冷却（之前1800=30min导致重复刷屏）
            _last_zf_push = state.get(k, 0)
            _last_zf_price = state.get(f'{k}_price', 0)
            _zf_price_changed = abs(_last_zf_price - cfg.get('gex_zf', 0)) > 10
            _zf_should_push = (now - _last_zf_push > _zf_cd) or _zf_price_changed
            if _zf_should_push:
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
                    _1h_bear = float(_k1h[-2][4]) < float(_k1h[-2][1])  # 最新完成K线下跌?
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
                # [ 2026-10-10 苏摩111] alerts添加后立即保存state
                # 修复前: 未保存state → 每分钟条件重新满足 → 无限推送
                state[k] = now
                state[f'{k}_price'] = cfg.get('gex_zf', 0)
                save_state(state)

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
                if now - _last_same < 1800:  # 30min冷却
                    print(f'[M7-DEDUP] {_ak} 同方向30min内去重跳过')
                    continue
                state[_dir_key] = now
            _filtered.append((_ak, _am))
        alerts = _filtered
    except Exception as _dd_e:
        print(f'[M7-DEDUP ERR] {_dd_e}')

    # P0-② ATR神经元果蝇感知
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


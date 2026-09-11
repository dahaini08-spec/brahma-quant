#!/usr/bin/env python3
"""
brahma_manual_analysis.py — 梵天手动全链路分析入口
设计院封印 2026-09-03 苏摩111

定位：
  苏摩说「梵天分析」→ 调用此脚本
  一次输出：10步完整链路 + 80维判断 + VIP策略卡片
  不需要追问，不需要「这是全能力吗」

10步强制链路（MEMORY.md封印）：
  Step 0  实时数据并行拉取
  Step 1  FVG磁铁（Bull/Bear方向）
  Step 2  OB有效性（age<50bars且未穿越）
  Step 3  清算地图（止损山/止损池）
  Step 4  共振点（FVG+OB+清算三交叉）
  Step 5  OI趋势（15min连续，LONG_BUILD/SHORT_BUILD）
  Step 6  聪明钱分歧（大户vs散户）
  Step 7  Hurst+HAR-RV+VolBeta（波动率三维）
  Step 8  宏观压制（NFP/CPI日历）
  Step 9  风控门控（熔断/回撤/反脆弱）
  Step 10 输出VIP卡片（姓赵不宣格式）

接入位置：
  - python3 scripts/brahma_manual_analysis.py --symbols BTC ETH
  - morning-battlefield / afternoon-battlefield cron message
  - AI手动分析触发

2026-09-03 苏摩111封印
"""
import os as _os_blas
_os_blas.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
_os_blas.environ.setdefault('OMP_NUM_THREADS', '1')
_os_blas.environ.setdefault('MKL_NUM_THREADS', '1')

import json, sys, time, urllib.request, argparse, signal
from pathlib import Path
from datetime import datetime, timezone

# 超时守卫：全链路分析超90s强制abort（防止阻塞gateway event loop）
MAX_RUNTIME_S = 90
def _timeout_handler(signum, frame):
    print(f'[brahma] ⚠️ 超时中止: 全链路超过{MAX_RUNTIME_S}s，强制退出防gateway阻塞', flush=True)
    sys.exit(1)
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(MAX_RUNTIME_S)

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

DATA = BASE / 'data'

# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def fetch(url, timeout=7):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    except Exception:
        return {}

def klines(sym, interval, limit):
    try:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={sym}&interval={interval}&limit={limit}')
        d = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [(float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in d]
    except Exception:
        return []

def load_json(path):
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}

# ══════════════════════════════════════════════════════════
# Step 0: 并行拉取实时数据
# ══════════════════════════════════════════════════════════

def step0_fetch_all(sym: str) -> dict:
    """并行拉取所有实时数据"""
    usdt = sym + 'USDT'

    price = float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={usdt}').get('price', 0))
    k1h   = klines(usdt, '1h', 8)
    k4h   = klines(usdt, '4h', 6)
    k15m  = klines(usdt, '15m', 8)

    fr_raw = fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={usdt}&limit=1')
    fr     = float(fr_raw[0].get('fundingRate', 0)) if isinstance(fr_raw, list) and fr_raw else 0

    # OI全周期：15M短期 + 1H中期 + 4H主力
    oi_hist   = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=15m&limit=8')
    oi_1h     = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=1h&limit=8')
    oi_4h     = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={usdt}&period=4h&limit=6')
    oi_vals   = [float(x.get('sumOpenInterest', 0)) for x in oi_hist] if isinstance(oi_hist, list) else []
    oi_usd    = [float(x.get('sumOpenInterestValue', 0)) for x in oi_hist] if isinstance(oi_hist, list) else []
    oi_1h_vals= [float(x.get('sumOpenInterest', 0)) for x in oi_1h]  if isinstance(oi_1h, list) else []
    oi_4h_vals= [float(x.get('sumOpenInterest', 0)) for x in oi_4h]  if isinstance(oi_4h, list) else []

    # 大户仓位：拉3条历史，计算变化速率
    lsr_raw  = fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={usdt}&period=1h&limit=4')
    lsr_list = [(float(x.get('longAccount', 0)), float(x.get('shortAccount', 0))) for x in lsr_raw] if isinstance(lsr_raw, list) else []

    top_raw  = fetch(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={usdt}&period=1h&limit=4')
    top_list = [float(x.get('longAccount', 0)) for x in top_raw] if isinstance(top_raw, list) else []
    # 大户变化速率（最新vs2小时前，正=增仓多，负=减仓多）
    top_delta = (top_list[0] - top_list[-1]) * 100 if len(top_list) >= 2 else 0.0

    # ATR全周期：1H + 4H + 1D
    k1d = klines(usdt, '1d', 10)

    dep      = fetch(f'https://fapi.binance.com/fapi/v1/depth?symbol={usdt}&limit=5')
    bids_sum = sum(float(x[1]) for x in dep.get('bids', []))
    asks_sum = sum(float(x[1]) for x in dep.get('asks', []))

    liq_b    = load_json(DATA / f'liq_heatmap_{usdt}.json')
    # 优先读取标的专属state文件（修复ETH OB/FVG数据污染）
    # brahma_state_refresh.py 已封印为每个标的写入独立文件
    _sym_lower    = sym.lower()  # btc / eth / near / zec ...
    _sym_state    = DATA / f'brahma_state_{_sym_lower}.json'
    _fallback     = DATA / 'brahma_state.json'
    _candidate    = load_json(_sym_state) if _sym_state.exists() else {}
    # 验证价格范围（防止读到错误的state文件）
    # 修复 2026-09-10：山寨币没有state文件时，实时调用brahma_core.analyze()
    if _candidate and _candidate.get('price', 0) > 0:
        # state文件存在且有price字段 → 验证价格是否匹配标的
        _state_price = _candidate.get('price', 0)
        _actual_price = price  # 使用Step0拉取的实际价格
        # 价格偏差<5%才可信（防止BTC数据污染NEAR分析）
        if _actual_price > 0 and abs(_state_price - _actual_price) / _actual_price < 0.05:
            bs = _candidate
        else:
            # 价格不匹配 → 实时调用brahma_core
            try:
                sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
                from brahma_core import analyze as _analyze
                bs = _analyze(f'{sym}USDT')
            except Exception as _e:
                bs = load_json(_fallback)
    else:
        # state文件不存在 → 实时调用brahma_core
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
            from brahma_core import analyze as _analyze
            bs = _analyze(f'{sym}USDT')
        except Exception as _e:
            bs = load_json(_fallback)
    gex_s    = load_json(DATA / 'gex_state.json')
    vb_s     = load_json(DATA / 'vol_beta_state.json')
    mac_s    = load_json(DATA / 'macro_state.json')
    mac_cal  = load_json(DATA / 'macro_cal_cache.json')
    cb       = load_json(DATA / 'circuit_breaker.json')
    dd       = load_json(DATA / 'drawdown_state.json')
    af       = load_json(DATA / 'antifragile_state.json')
    regime_s = load_json(DATA / 'regime_state.json')

    return {
        'sym': sym, 'usdt': usdt, 'price': price,
        'k1h': k1h, 'k4h': k4h, 'k15m': k15m, 'k1d': k1d,
        'fr': fr, 'oi_vals': oi_vals, 'oi_usd': oi_usd,
        'oi_1h_vals': oi_1h_vals, 'oi_4h_vals': oi_4h_vals,
        'lsr_list': lsr_list, 'top_list': top_list, 'top_delta': top_delta,
        'bids_sum': bids_sum, 'asks_sum': asks_sum,
        'liq': liq_b, 'bs': bs, 'gex': gex_s.get(sym, {}),
        'vb': vb_s.get(sym, {}), 'mac': mac_s, 'mac_cal': mac_cal,
        'cb': cb, 'dd': dd, 'af': af, 'regime_s': regime_s,
    }

# ══════════════════════════════════════════════════════════
# Step 1: FVG磁铁
# ══════════════════════════════════════════════════════════

def step1_fvg(d: dict) -> dict:
    bd    = d['bs'].get('confluence', {}).get('breakdown', {})
    price = d['price']
    k1h   = d['k1h']
    sym   = d['sym']

    # ── 优先读取 brahma_state 里的 _fvg_map（由 block_a 实时计算）────────
    fvg_map = bd.get('_fvg_map', {})
    magnet = 0
    fvg_dir = 'NONE'
    fvg_desc = '无有效FVG数据'
    price_lo = price * 0.70
    price_hi = price * 1.30

    if fvg_map:
        # 全周期FVG地图：每个周期保留最近有效FVG，按权重综合方向
        # 苏摩111封印 2026-09-04：禁止只看单一FVG
        TF_WEIGHT = {'1d': 4, '4h': 3, '1h': 2, '15m': 1}
        all_fvgs = []
        # 每个周期的FVG分组
        tf_fvg_map = {}  # {tf: [fvg,...]}
        for tf, fvgs in fvg_map.items():
            # 过滤：距离现价>8%的FVG是历史遗留，不参与近期决策
            valid = [f for f in fvgs
                     if not f.get('filled')
                     and price_lo <= f['mid'] <= price_hi
                     and abs(f.get('mid', price) - price) / price <= 0.08]
            if valid:
                # 每个周期取距离现价最近的一个
                valid.sort(key=lambda x: abs(x['mid'] - price))
                tf_fvg_map[tf] = valid[0]
                w = TF_WEIGHT.get(tf, 1)
                all_fvgs.append((abs(valid[0]['mid'] - price), w, tf, valid[0]))

        # 全周期综合方向投票（权重加权）
        bull_score = 0; bear_score = 0
        for _, w, tf, f in all_fvgs:
            if f['type'] == 'BULL': bull_score += w
            else: bear_score += w

        # 主导方向：权重票数多的方向
        fvg_consensus = 'BULL' if bull_score > bear_score else ('BEAR' if bear_score > bull_score else 'NONE')

        # 主磁铁：选最高权重周期里方向与共识一致的最近FVG
        all_fvgs.sort(key=lambda x: (-x[1], x[0]))  # 先按权重降序，再按距离升序
        best = None
        for _, w, tf, f in all_fvgs:
            if f['type'] == fvg_consensus or fvg_consensus == 'NONE':
                best = (tf, f); break
        if best is None and all_fvgs:
            best = (all_fvgs[0][2], all_fvgs[0][3])

        if best:
            best_tf, best_f = best
            magnet  = best_f['mid']
            fvg_dir = fvg_consensus if fvg_consensus != 'NONE' else best_f['type']

            # 全周期描述
            tf_parts = []
            for _, w, tf, f in sorted(all_fvgs, key=lambda x: -x[1]):
                tf_parts.append(f'{tf.upper()}:{f["type"]}@${f["mid"]:,.0f}')
            fvg_desc = (
                f'全周期FVG共识: {fvg_consensus} '
                f'(多{bull_score}分 空{bear_score}分) '
                f'主磁铁:{best_tf.upper()} ${best_f["lo"]:,.0f}~${best_f["hi"]:,.0f} '
                f'中点${best_f["mid"]:,.0f}({best_f.get("dist_pct",0):+.1f}%)\n'
                f'  各周期: {" | ".join(tf_parts)}'
            )
    else:
        # 没有 _fvg_map（旧版state）→ 回走breakdown旧逻辑
        for label, txt in [
            ('4H_LONG',  str(bd.get('FVG_4H_LONG',  '') or '')),
            ('15M_LONG', str(bd.get('FVG_15M_LONG', '') or '')),
            ('4H_SHORT', str(bd.get('FVG_4H_SHORT', '') or '')),
        ]:
            if '磁铁' in txt:
                try:
                    mag = float(txt.split('磁铁')[1].split(']')[0].strip())
                    if price_lo <= mag <= price_hi:
                        magnet   = mag
                        fvg_dir  = 'BULL' if 'LONG' in label else 'BEAR'
                        fvg_desc = txt[:100]
                        break
                except Exception:
                    pass

    # 如果state层无数据，临时K线估算
    if not magnet and len(k1h) >= 3:
        last = k1h[-1]; prev2 = k1h[-3]
        if prev2[1] < last[2]:  # Bull FVG
            manual_fvg = round((prev2[1] + last[2]) / 2, 1)
            if price_lo <= manual_fvg <= price_hi:
                magnet   = manual_fvg
                fvg_dir  = 'BULL'
                fvg_desc = f'1H Bull FVG估算 缺口{prev2[1]:.0f}~{last[2]:.0f} 中点{manual_fvg:.0f}'

    # D1修复：删除死代码，D2：注入hi/lo供step4边界计算
    _fvg_hi = 0; _fvg_lo = 0
    if fvg_map:
        for tf, fvgs in fvg_map.items():
            for f in fvgs:
                if f.get('mid') == magnet:
                    _fvg_hi = f.get('hi', 0)
                    _fvg_lo = f.get('lo', 0)
    return {'dir': fvg_dir, 'magnet': magnet, 'desc': fvg_desc,
            'hi': _fvg_hi, 'lo': _fvg_lo, 'fvg_map': fvg_map,
            'tf_fvg_map': tf_fvg_map if 'tf_fvg_map' in dir() else {},
            'bull_score': bull_score if 'bull_score' in dir() else 0,
            'bear_score': bear_score if 'bear_score' in dir() else 0,
            'consensus': fvg_consensus if 'fvg_consensus' in dir() else fvg_dir}

# ══════════════════════════════════════════════════════════
# Step 2: OB有效性
# ══════════════════════════════════════════════════════════

def step2_ob(d: dict) -> dict:
    bd = d['bs'].get('confluence', {}).get('breakdown', {})

    # ── 优先读取 _ob_map（由 block_a 实时计算）──────────────────────
    ob_map = bd.get('_ob_map', {})
    results = {}

    if ob_map:
        for tf, obs in ob_map.items():
            for ob in obs:
                key  = f'OB_{tf.upper()}_{ob["type"]}'
                note_tag = ob['note']  # NEW/FRESH/AGING/EXPIRED
                valid    = ob['valid']
                icon = {'NEW': '✅最新鲜', 'FRESH': '✅新鲜有效',
                        'AGING': '⚠️老化中', 'EXPIRED': '❌已过期'}.get(note_tag, '⚠️')
                results[key] = {
                    'valid': valid,
                    'note':  (f'{icon} age={ob["age"]}bars '
                              f'${ob["lo"]:,.0f}~${ob["hi"]:,.0f} '
                              f'dist={ob["dist_pct"]:+.2f}%')
                }
    else:
        # 备用：读取旧版breakdown字段
        for key in ['OB新鲜度_1H_LONG', 'OB新鲜度_4H_LONG', 'OB_1D_LONG',
                    'OB新鲜度_1H_SHORT', 'OB新鲜度_4H_SHORT']:
            val = str(bd.get(key, '') or '')
            if not val:
                continue
            valid = True
            note  = val[:100]
            if 'age乘数=0.30' in val:
                valid = False
                note  = f'❌已老化(age≥5bars) → 作废  {val[:60]}'
            elif 'age乘数=0.50' in val:
                note  = f'⚠️ 中等有效(age40-50)  {val[:60]}'
            elif 'age乘数=1.0' in val or 'age乘数=0.8' in val:
                note  = f'✅ 新鲜有效  {val[:60]}'
            results[key] = {'valid': valid, 'note': note}

    return results

# ══════════════════════════════════════════════════════════
# Step 3: 清算地图
# ══════════════════════════════════════════════════════════

def step3_liq(d: dict) -> dict:
    liq   = d['liq']
    price = d['price']

    short_map = liq.get('short_liq_map', {})
    long_map  = liq.get('long_liq_map', {})

    # ── 优先使用文件直接计算好的nearest字段 ──────────────────────────
    # liq_heatmap_BTCUSDT.json 中 short_liq_map 的 key=百分比, value=该百分比对应的清算价
    # 但key与value顺序是倒置的（key小对应更远的价），直接用 nearest_short_liq 字段最准确
    nearest_short = liq.get('nearest_short_liq', 0)
    nearest_long  = liq.get('nearest_long_liq',  0)
    dist_short    = liq.get('dist_to_short_liq', 0)   # % distance
    dist_long     = liq.get('dist_to_long_liq',  0)

    # 若文件没有 nearest 字段（旧格式），用实时价格×最小百分比反算
    if not nearest_short and short_map:
        min_pct = min(float(k) for k in short_map.keys())
        nearest_short = round(price * (1 + min_pct / 100), 1)
        dist_short    = min_pct
    if not nearest_long and long_map:
        min_pct = min(float(k) for k in long_map.keys())
        nearest_long  = round(price * (1 - min_pct / 100), 1)
        dist_long     = min_pct

    # 第二层清算目标（用实时价格×次小百分比）
    sorted_short_pcts = sorted(float(k) for k in short_map.keys()) if short_map else []
    sorted_long_pcts  = sorted(float(k) for k in long_map.keys())  if long_map  else []
    # D10修复: 第二层清算用nearest_short为基准往外推ATR，不用分析时刻price重算
    _ns = nearest_short if nearest_short else price
    _nl = nearest_long  if nearest_long  else price
    second_short = round(_ns * (1 + sorted_short_pcts[1] / 100), 1) if len(sorted_short_pcts) >= 2 else 0
    second_long  = round(_nl * (1 - sorted_long_pcts[1]  / 100), 1) if len(sorted_long_pcts)  >= 2 else 0

    target_pct  = (nearest_short - price) / price * 100 if nearest_short and price else 0
    support_pct = (price - nearest_long)  / price * 100 if nearest_long  and price else 0

    # CRITICAL-1修复: 真实计算liq_bias，L2门控依赖此字段
    # 下方多头清算 > 上方空头清算*1.3 → 主力优先往下打（DOWN）
    # 上方空头清算 > 下方多头清算*1.3 → 主力优先往上打（UP）
    _sl = dist_short if dist_short else 999
    _ll = dist_long  if dist_long  else 999
    if _ll < _sl * 0.77:        # 下方清算更近（距离更小=量更集中）
        _liq_bias = 'DOWN'
    elif _sl < _ll * 0.77:      # 上方清算更近
        _liq_bias = 'UP'
    else:
        _liq_bias = 'NEUTRAL'

    return {
        'nearest_short':      nearest_short,
        'nearest_short_pct':  dist_short,
        'second_short':       second_short,
        'nearest_long':       nearest_long,
        'nearest_long_pct':   dist_long,
        'second_long':        second_long,
        'target_pct':         round(target_pct, 2),
        'support_pct':        round(support_pct, 2),
        'liq_bias':           _liq_bias,   # L2方向门控字段
        'short_map':          short_map,
        'long_map':           long_map,
    }

# ══════════════════════════════════════════════════════════
# Step 4: 共振点
# ══════════════════════════════════════════════════════════

def step4_resonance(d: dict, fvg: dict, ob: dict, liq: dict, oi: dict = None, vol: dict = None) -> dict:
    """[P2-4修复 2026-09-11] 共振升级5维：FVG+OB+清算+OI+GEX
    FVG+OB同源（价格结构）→ 1.5个独立信号
    OI独立（量价关系）→ 1个独立信号
    GEX独立（期权市场）→ 1个独立信号
    清算半独立（持仓数据）→ 0.5个独立信号
    总计4个独立信号，远大于原3/3的1.5个独立信号
    """
    price = d['price']

    fvg_mid     = fvg['magnet']
    fvg_dir     = fvg['dir']
    valid_obs   = [k for k, v in ob.items() if v.get('valid', False)]
    nearest_liq = liq['nearest_short'] if fvg_dir in ('BULL', 'NONE') else liq['nearest_long']

    # 共振条件：FVG有效 + 有fresh OB + 清算目标明确
    has_fvg    = fvg_mid > 0 and fvg_dir != 'NONE'
    has_ob     = len(valid_obs) > 0
    has_liq    = nearest_liq > 0

    score      = sum([has_fvg, has_ob, has_liq])
    resonance  = score >= 2  # 至少2/3条件

    if resonance and has_fvg:
        # BUG-6修复：入场区必须在现价的正确一侧
        # BEAR：做空应等反弹到现价上方阻力位，入场区必须 > price
        # BULL：做多应等回调到现价下方支撑位，入场区必须 < price
        if fvg_dir == 'BEAR':
            # 空单：入场区在现价上方（等反弹到FVG上沿/OB阻力）
            # 取有效OB中最近的上方阻力，没有就用吸力目标上方ATR
            bear_obs = [k for k in valid_obs if 'BEAR' in k]
            bull_obs = [k for k in valid_obs if 'BULL' in k]
            # 首选：现价上方最近的BEAR_OB（真实阻力）
            # 次选：清算目标上方（nearest_short）
            resistance = liq.get('nearest_short', 0)
            if resistance and resistance > price:
                # 反弹入场区：现价到resistance之间的顶部
                entry_hi = round(min(resistance, price * 1.015), 1)  # 最多反弹1.5%
                entry_lo = round(price * 1.003, 1)                   # 入场区少于0.3%距离
            else:
                # 无上方清算目标，用fvg范围上沿+ATR作阻力
                entry_hi = round(fvg['hi'] * 1.001 if fvg.get('hi', 0) > price else price * 1.008, 1)
                entry_lo = round(price * 1.003, 1)
            # 如果入场区不在现价上方，无效
            if entry_lo <= price or entry_hi <= price:
                entry_lo = 0.0
                entry_hi = 0.0
                resonance = False
        else:  # BULL
            # 多单：入场区在现价下方（等回调到FVG/OB支撑）
            # 情形A：FVG中点在现价下方 → 用FVG中点作锚
            # 情形B：FVG中点在现价上方（价格已在FVG内）→ 用有效OB下沿作锚
            ob_map = fvg.get('fvg_map', {})
            # 找现价下方最近的有效BULL OB
            best_ob_lo = 0.0
            best_ob_hi = 0.0
            for k, v in ob.items():
                if not v.get('valid', False): continue
                if 'BULL' not in k: continue
                # 从note里提取价格范围
                try:
                    note = v.get('note', '')
                    import re
                    prices_in_note = re.findall(r'\\$([\d,]+)', note)
                    if len(prices_in_note) >= 2:
                        lo_v = float(prices_in_note[0].replace(',',''))
                        hi_v = float(prices_in_note[1].replace(',',''))
                        if hi_v < price and lo_v > best_ob_lo:
                            best_ob_lo = lo_v
                            best_ob_hi = hi_v
                except Exception:
                    pass

            if fvg_mid < price:
                # 情形A：FVG中点在现价下方，用FVG中点
                entry_lo = round(min(fvg_mid * 0.998, price * 0.993), 1)
                entry_hi = round(fvg_mid * 1.002, 1)
            elif best_ob_lo > 0:
                # 情形B：价格已在FVG内，用最近有效BULL OB下沿
                entry_lo = round(best_ob_lo * 0.998, 1)
                entry_hi = round(best_ob_hi * 1.002, 1)
            else:
                # 无锚点：用现价-1%~-2%的支撑区
                entry_lo = round(price * 0.988, 1)
                entry_hi = round(price * 0.993, 1)

            # 最终校验：BULL入场区必须在现价下方
            # 若入场区在现价上方 = FVG磁铁未触及 = 还没到入场位 = 等待
            if entry_lo >= price or entry_hi >= price:
                entry_lo = 0.0
                entry_hi = 0.0
                resonance = False
    else:
        # BUG-5修复：无共振时不用price*0.997这种无结构入场区，直接标为失效
        entry_lo = 0.0
        entry_hi = 0.0

    missing = []
    if not has_fvg:  missing.append('FVG无效')
    if not has_ob:   missing.append('无新鲜OB')
    if not has_liq:  missing.append('清算数据缺失')
    if entry_lo == 0.0 and resonance:
        missing.append('入场区方向错误（商品价格不在入场区正确一侧）')

    # [P2-4修复 2026-09-11] OI/GEX作为独立共振维度
    has_oi = False
    has_gex = False
    if oi and oi.get('signal','') not in ('NO_DATA','MIXED'):
        has_oi = True
        score += 1
    if vol and vol.get('gex_note',''):
        has_gex = True
        score += 1
    # 共振标准升级：≥3/5 = 有效共振（原2/3）
    resonance = score >= 3

    # [P2-5修复 2026-09-11] 交叉验证层：Step1-4结构层 vs Step5-9市场层
    # [D1修复 2026-09-11] 用共识方向(fvg_consensus)而非主磁铁方向(fvg_dir)
    _fvg_consensus = fvg.get('consensus', fvg_dir)  # 共识方向优先
    cross_check = {'consistent': True, 'conflicts': []}
    if oi and _fvg_consensus != 'NONE':
        _struct_bull = _fvg_consensus == 'BULL'
        _oi_bull = oi['signal'] in ('LONG_BUILD', 'SHORT_SQUEEZE')
        if _struct_bull != _oi_bull:
            cross_check['consistent'] = False
            cross_check['conflicts'].append(f'FVG={_fvg_consensus} vs OI={oi["signal"]}')
    if vol and _fvg_consensus != 'NONE':
        _kappa_bull = vol.get('kappa', 0) < -0.05
        _struct_bull = _fvg_consensus == 'BULL'
        if _struct_bull != _kappa_bull and abs(vol.get('kappa', 0)) > 0.03:
            cross_check['consistent'] = False
            cross_check['conflicts'].append(f'FVG={_fvg_consensus} vs κ={vol.get("kappa",0):.3f}')
    # Hurst交叉验证：共振但Hurst<0.5 = 信号可信度存疑
    if vol and vol.get('hurst', 0.5) < 0.5 and resonance:
        cross_check['conflicts'].append(f'共振但Hurst={vol["hurst"]:.3f}<0.5=随机游走')

    return {
        'resonance':   resonance,
        'score':       score,
        'has_fvg':     has_fvg,
        'has_ob':      has_ob,
        'has_liq':     has_liq,
        'has_oi':      has_oi,
        'has_gex':     has_gex,
        'entry_lo':    entry_lo,
        'entry_hi':    entry_hi,
        'missing':     missing,
        'liq_nearest_long': liq.get('nearest_long', 0),
        'cross_check': cross_check,
    }

# ══════════════════════════════════════════════════════════
# Step 5: OI趋势
# ══════════════════════════════════════════════════════════

def step5_oi(d: dict) -> dict:
    """全周期OI趋势：15M短期 + 1H中期 + 4H主力 苏摩111封印 2026-09-04"""
    oi_vals    = d['oi_vals']      # 15M x8
    oi_1h_vals = d.get('oi_1h_vals', [])  # 1H x8
    oi_4h_vals = d.get('oi_4h_vals', [])  # 4H x6
    price      = d['price']
    k1h        = d['k1h']
    k4h        = d.get('k4h', [])

    if len(oi_vals) < 2:
        return {'signal': 'NO_DATA', 'trend': [], 'conclusion': 'OI数据不足'}

    def _classify(vals, klines, label):
        """单周期OI信号分类"""
        if len(vals) < 2: return 'NO_DATA', 0
        diffs  = [vals[i]-vals[i-1] for i in range(1,len(vals))]
        rising = sum(1 for x in diffs if x > 0)
        falling= sum(1 for x in diffs if x < 0)
        price_up = (klines[-1][4] > klines[-2][4]) if len(klines) >= 2 else True
        chg    = vals[-1] - vals[0]
        if rising >= int(len(diffs)*0.6) and price_up:   return 'LONG_BUILD',  chg
        if rising >= int(len(diffs)*0.6) and not price_up: return 'SHORT_BUILD', chg
        if falling>= int(len(diffs)*0.6) and price_up:   return 'SHORT_SQUEEZE',chg
        if falling>= int(len(diffs)*0.6) and not price_up: return 'LONG_UNWIND', chg
        return 'MIXED', chg

    sig_15m, chg_15m = _classify(oi_vals,    k1h,  '15M')
    sig_1h,  chg_1h  = _classify(oi_1h_vals, k1h,  '1H')
    sig_4h,  chg_4h  = _classify(oi_4h_vals, k4h,  '4H')

    # 全周期一致性判断（权重：4H=3, 1H=2, 15M=1）
    WEIGHT = {sig_4h: 3, sig_1h: 2, sig_15m: 1}
    score = {}
    for sig, w in [(sig_4h,3),(sig_1h,2),(sig_15m,1)]:
        score[sig] = score.get(sig,0) + w

    # 主信号：权重最高的
    main_signal = max(score, key=score.get)
    total_weight= sum(score.values())
    main_conf   = score.get(main_signal, 0) / total_weight  # 0~1

    # 结论文字
    _labels = {
        'LONG_BUILD':   '全周期增仓+价格上涨 → 主力真实建多',
        'SHORT_BUILD':  '全周期增仓+价格下跌 → 主力真实建空',
        'SHORT_SQUEEZE':'OI减仓+价格上涨 → 轧空，持续性存疑',
        'LONG_UNWIND':  'OI减仓+价格下跌 → 多头平仓',
        'MIXED':        'OI信号分歧，方向不明',
        'NO_DATA':      'OI数据不足',
    }

    if main_conf >= 0.833:  # 6/6权重全票
        conf_str = '强共识'
    elif main_conf >= 0.5:  # 多数一致
        conf_str = '多数一致'
    else:
        conf_str = '分歧'
        main_signal = 'MIXED'

    conclusion = (
        f'{conf_str} | 15M:{sig_15m} 1H:{sig_1h} 4H:{sig_4h} | '
        f'{_labels.get(main_signal,"?")}'
    )

    total_change = oi_vals[-1] - oi_vals[0] if oi_vals else 0
    usd_change   = (d['oi_usd'][-1] - d['oi_usd'][0]) if len(d.get('oi_usd',[])) >= 2 else 0

    # [P0修复 2026-09-10] CVD交叉验证
    cvd_data = {}
    cvd_note = ''
    try:
        import json as _json
        _sym_lower = d.get('sym','').lower() + 'usdt'
        _cvd_path = Path(__file__).parent.parent / 'data' / f'cvd_realtime_{_sym_lower}.json'
        if _cvd_path.exists():
            cvd_data = _json.loads(_cvd_path.read_text())
            cvd_1h = cvd_data.get('cvd_1h', 0)
            cvd_4h = cvd_data.get('cvd_4h', 0)
            buy_vol = cvd_data.get('buy_vol_1h', 0)
            sell_vol = cvd_data.get('sell_vol_1h', 0)
            if cvd_1h > 0:
                cvd_note = f'CVD 1H=+{cvd_1h:.0f}（买方主导）'
            elif cvd_1h < 0:
                cvd_note = f'CVD 1H={cvd_1h:.0f}（卖方主导）'
            else:
                cvd_note = 'CVD 1H=0（中性）'
            # OI+CVD交叉验证
            if main_signal == 'LONG_BUILD' and cvd_1h < 0:
                cvd_note += ' ⚠️OI多但CVD空=矛盾'
            elif main_signal == 'SHORT_BUILD' and cvd_1h > 0:
                cvd_note += ' ⚠️OI空但CVD多=矛盾'
            elif main_signal == 'LONG_BUILD' and cvd_1h > 0:
                cvd_note += ' ✅OI+CVD同向做多'
            elif main_signal == 'SHORT_BUILD' and cvd_1h < 0:
                cvd_note += ' ✅OI+CVD同向做空'
    except Exception:
        pass

    return {
        'signal':       main_signal,
        'signal_15m':   sig_15m,
        'signal_1h':    sig_1h,
        'signal_4h':    sig_4h,
        'conf':         round(main_conf, 2),
        'conclusion':   conclusion,
        'total_change': round(total_change, 0),
        'usd_change_m': round(usd_change / 1e6, 1),
        'latest':       round(oi_vals[-1], 0) if oi_vals else 0,
        'trend':        [round(v,0) for v in oi_vals],
        'cvd_1h':       cvd_data.get('cvd_1h', 0),
        'cvd_note':     cvd_note,
    }

# ══════════════════════════════════════════════════════════
# Step 6: 聪明钱分歧
# ══════════════════════════════════════════════════════════

def step6_smart_money(d: dict) -> dict:
    top_list = d['top_list']   # 大户多头占比列表
    lsr_list = d['lsr_list']   # 散户 (long%, short%)

    big_latest    = top_list[-1] if top_list else 0.5
    retail_latest = lsr_list[-1][0] if lsr_list else 0.5

    # 趋势：大户多头是在增加还是减少
    big_trend = 'INCREASING' if len(top_list) >= 2 and top_list[-1] > top_list[0] else ('DECREASING' if len(top_list) >= 2 and top_list[-1] < top_list[0] else 'STABLE')
    # 大户变化速率（升级：2H内净变化，正=主力加多，负=主力减多）
    top_delta = d.get('top_delta', 0.0)  # step0已计算

    diverge = abs(big_latest - retail_latest)

    if big_latest > 0.60 and retail_latest < 0.52:
        signal      = 'STRONG_BULL'
        conclusion  = f'大户{big_latest*100:.0f}%多 vs 散户{retail_latest*100:.0f}%多 → 极端分歧，主力在买，散户在空，强烈看多'
    elif big_latest > 0.55:
        signal      = 'MILD_BULL'
        conclusion  = f'大户{big_latest*100:.0f}%多，方向偏多'
    elif big_latest < 0.45:
        signal      = 'BEAR'
        conclusion  = f'大户{big_latest*100:.0f}%多（空头主导），偏空'
    else:
        signal      = 'NEUTRAL'
        conclusion  = f'大户多空均衡，方向不明'

    return {
        'signal':      signal,
        'conclusion':  conclusion,
        'big_long':    round(big_latest * 100, 1),
        'retail_long': round(retail_latest * 100, 1),
        'diverge':     round(diverge * 100, 1),
        'big_trend':   big_trend,
    }

# ══════════════════════════════════════════════════════════
# Step 7: Hurst + HAR-RV + VolBeta
# ══════════════════════════════════════════════════════════

def step7_volatility(d: dict) -> dict:
    bd    = d['bs'].get('confluence', {}).get('breakdown', {})
    vb    = d['vb']
    sym   = d['sym']
    price = d['price']

    # ATR全周期计算（苏摩111封印 2026-09-04）
    k1h_l = d.get('k1h', []); k4h_l = d.get('k4h', []); k1d_l = d.get('k1d', [])
    atr_1h = round(sum(abs(x[1]-x[2]) for x in k1h_l[-8:])/min(8,len(k1h_l)),1) if len(k1h_l)>=2 else price*0.005
    atr_4h = round(sum(abs(x[1]-x[2]) for x in k4h_l[-7:])/min(7,len(k4h_l)),1) if len(k4h_l)>=2 else 0.0
    atr_1d = round(sum(abs(x[1]-x[2]) for x in k1d_l[-7:])/min(7,len(k1d_l)),1) if len(k1d_l)>=2 else 0.0
    # 合约SL参考：取1.5×ATR1H 和 1.0×ATR4H 的较大值
    atr_sl_ref = max(atr_1h * 1.5, atr_4h * 1.0) if atr_4h else atr_1h * 1.5

    hurst_raw = str(bd.get('Hurst体制验证', '') or '')
    harv_raw  = str(bd.get('HAR-RV波动率', '') or '')

    hurst_val = 0.5
    try:
        if 'H=' in hurst_raw:
            hurst_val = float(hurst_raw.split('H=')[1].split()[0])
    except Exception:
        pass

    harv_val = 0.0
    try:
        if 'RV=' in harv_raw:
            harv_val = float(harv_raw.split('RV=')[1].split()[0])
    except Exception:
        pass

    # HAR-RV 转换为具体价格波动区间
    # RV = 已实现波动率（对数收益标准差）
    # 预测未来 4H 价格区间：当前价格 ± RV * 价格 * sqrt(4) 年化因子追倒
    price_now = d.get('price', 0)
    harv_range_lo = harv_range_hi = 0
    harv_range_str = ''
    if harv_val > 0 and price_now > 0:
        # D5修复: RV是已实现波动率(年化)
        # 日波动率 = RV / sqrt(252)
        # 4H波动率 = 日波动率 / sqrt(6)  [一天6个4H区间]
        # 注意RV已经是年化标准差，不需要再开方
        daily_vol = harv_val / (252 ** 0.5)   # 年化→日化
        fh_vol    = daily_vol / (6 ** 0.5)    # 日化→4H化
        harv_range_lo = round(price_now * (1 - fh_vol), 1)
        harv_range_hi = round(price_now * (1 + fh_vol), 1)
        harv_range_str = f'未来4H价格区间: ${harv_range_lo:,.0f}~${harv_range_hi:,.0f}'

    kappa    = vb.get('kappa', 0)
    beta_p   = vb.get('beta_plus', 0)
    beta_m   = vb.get('beta_minus', 0)
    iv_rank  = vb.get('iv_pct_rank', 50)
    iv_pct   = vb.get('iv_pct', 0)
    premium  = vb.get('iv_premium_pct', 0)

    # [P0修复 2026-09-10] GEX加入Step 7（波动率四维）
    gex_note = ''
    gex_bias = 'NEUTRAL'
    try:
        import json as _json
        _gex_path = Path(__file__).parent.parent / 'data' / 'gex_state.json'
        if _gex_path.exists():
            _gex_all = _json.loads(_gex_path.read_text())
            _gex_sym = d.get('sym','')
            _gex = _gex_all.get(_gex_sym, _gex_all.get(_gex_sym.upper(), {}))
            # 修复 2026-09-10：山寨币不在gex_state中，直接用空数据（不回退BTC）
            # 原bug：山寨币没有GEX数据时回退到BTC的GEX → $80,000污染山寨币分析
            # 现在：山寨币无GEX数据 = GEX NEUTRAL（不显示BTC的strike价格）
            if _gex:
                gex_total = _gex.get('gex_total', 0) or _gex.get('total_gex', 0)
                gex_bias_val = _gex.get('gex_bias', '') or _gex.get('bias', '')
                min_gex_strike = _gex.get('min_gex_strike', 0)
                if gex_total > 0:
                    gex_bias = 'POSITIVE'
                    gex_note = f'GEX=+{gex_total/1e6:.2f}M 正gamma→价格被钉住(低波动)'
                elif gex_total < 0:
                    gex_bias = 'NEGATIVE'
                    gex_note = f'GEX={gex_total/1e6:.2f}M 负gamma→波动率引爆点'
                else:
                    gex_note = 'GEX≈0 中性'
                if min_gex_strike > 0:
                    gex_dist = abs(price - min_gex_strike) / price * 100 if price else 0
                    gex_note += f' MIN_GEX=${min_gex_strike:,.0f}({gex_dist:.1f}%)'
    except Exception:
        pass

    # [P1修复 2026-09-10] FR展示
    fr_note = ''
    try:
        fr_val = d.get('fr', 0)
        if fr_val:
            if abs(fr_val) > 0.001:
                fr_note = f'FR={fr_val*100:.4f}% ⚠️极端值=反转前兆'
            else:
                fr_note = f'FR={fr_val*100:.4f}% 正常'
    except Exception:
        pass

    # Hurst解读
    if hurst_val >= 0.65:
        hurst_note = f'H={hurst_val:.3f} 🔥强趋势持续性，当前方向会继续'
    elif hurst_val >= 0.55:
        hurst_note = f'H={hurst_val:.3f} ⚠️趋势性隐现，体制切换前兆'
    elif hurst_val <= 0.40:
        hurst_note = f'H={hurst_val:.3f} 均值回归强，震荡不适合趋势追踪'
    else:
        hurst_note = f'H={hurst_val:.3f} 随机游走，方向不确定'

    # kappa解读
    if kappa < -0.05:
        kappa_note = f'κ={kappa:.3f} 🟢Call需求强(期权市场偏多，大资金买上涨保险)'
    elif kappa > 0.05:
        kappa_note = f'κ={kappa:.3f} 🔴Put需求强(期权市场偏空或对冲)'
    else:
        kappa_note = f'κ={kappa:.3f} 期权市场中性'

    return {
        'hurst':       hurst_val,
        'hurst_note':  hurst_note,
        'harv':           harv_val,
        'atr_1h': atr_1h, 'atr_4h': atr_4h, 'atr_1d': atr_1d, 'atr_sl_ref': atr_sl_ref,
        'harv_range_lo':  harv_range_lo,
        'harv_range_hi':  harv_range_hi,
        'harv_range_str': harv_range_str,
        'kappa':       kappa,
        'kappa_note':  kappa_note,
        'beta_p':      beta_p,
        'beta_m':      beta_m,
        'iv_rank':     iv_rank,
        'trend_signal': 'TRENDING' if hurst_val >= 0.55 else 'RANGING',
        'gex_note':    gex_note,
        'gex_bias':    gex_bias,
        'fr_note':     fr_note,
    }

# ══════════════════════════════════════════════════════════
# Step 8: 宏观压制
# ══════════════════════════════════════════════════════════

def step8_macro(d: dict) -> dict:
    mac     = d['mac']
    mac_cal = d['mac_cal']

    fear_greed = mac.get('fear_greed', 50)
    macro_bias = mac.get('macro_bias', 'NEUTRAL')
    macro_note = mac.get('macro_note', '')

    # 宏观日历事件
    events = []
    if isinstance(mac_cal, dict):
        for k, v in mac_cal.items():
            if isinstance(v, list):
                events.extend(v[:2])
            elif isinstance(v, str):
                events.append(v[:50])
    elif isinstance(mac_cal, list):
        events = [str(e)[:50] for e in mac_cal[:3]]

    # NFP/CPI/FOMC检测
    high_impact = [e for e in events if any(x in str(e).upper() for x in ['NFP', 'CPI', 'FOMC', 'PCE', '非农'])]
    has_event   = len(high_impact) > 0

    if has_event:
        pos_note = f'⚠️ 重大宏观事件迫近({", ".join(high_impact[:2])}) → 持仓减半，SL加宽50%'
    else:
        pos_note = '✅ 近期无重大宏观事件，正常仓位'

    return {
        'fear_greed':  fear_greed,
        'macro_bias':  macro_bias,
        'macro_note':  macro_note[:60],
        'events':      events[:3],
        'high_impact': high_impact,
        'has_event':   has_event,
        'pos_note':    pos_note,
    }

# ══════════════════════════════════════════════════════════
# Step 9: 风控门控
# ══════════════════════════════════════════════════════════

def step9_risk(d: dict) -> dict:
    cb  = d['cb']
    dd  = d['dd']
    af  = d['af']

    l1 = cb.get('l1', False)
    l2 = cb.get('l2', False)
    l3 = cb.get('l3', False)
    circuit_ok = not (l1 or l2 or l3)

    dd_pct    = dd.get('drawdown_pct', 0)
    dd_status = dd.get('status', 'NORMAL')
    dd_ok     = dd_status == 'NORMAL'

    consec_loss = af.get('consecutive_losses', 0)
    af_ok       = consec_loss < 3

    all_green = circuit_ok and dd_ok and af_ok

    blocks = []
    if not circuit_ok:
        level = 'L3' if l3 else ('L2' if l2 else 'L1')
        blocks.append(f'熔断器{level}触发 → 禁止入场')
    if not dd_ok:
        blocks.append(f'回撤{dd_pct:.1f}% status={dd_status} → 降仓50%')
    if not af_ok:
        blocks.append(f'连亏{consec_loss}笔 → 冷却期，降仓50%')

    nav_mult = 1.0
    if not dd_ok and dd_pct >= 5:
        nav_mult = 0.5
    if not dd_ok and dd_pct >= 10:
        nav_mult = 0.25
    if not af_ok:
        nav_mult = min(nav_mult, 0.5)

    # [P1修复 2026-09-10] 失效期检测器接入Step 9
    regime_state = 'GREEN'
    regime_note = ''
    try:
        import json as _json
        _rd_path = Path(__file__).parent.parent / 'data' / 'dharma_regime_detector_result.json'
        if _rd_path.exists():
            _rd = _json.loads(_rd_path.read_text())
            regime_state = _rd.get('current_state', 'GREEN')
            if regime_state == 'RED':
                regime_note = '失效期RED → 仓位减半+杠杆减半'
                nav_mult = min(nav_mult, 0.5)
            elif regime_state == 'YELLOW':
                regime_note = '警戒期YELLOW → 仓位×0.75'
                nav_mult = min(nav_mult, 0.75)
    except Exception:
        pass

    return {
        'all_green':  all_green,
        'circuit_ok': circuit_ok,
        'dd_ok':      dd_ok,
        'af_ok':      af_ok,
        'dd_pct':     dd_pct,
        'consec':     consec_loss,
        'blocks':     blocks,
        'nav_mult':   nav_mult,
        'regime_state': regime_state,
        'regime_note':  regime_note,
    }

# ══════════════════════════════════════════════════════════
# Step 10: VIP卡片（姓赵不宣格式）
# ══════════════════════════════════════════════════════════

def step10_vip(sym, price, d, fvg, ob, liq, res, oi, sm, vol, mac, risk) -> str:
    bs      = d['bs']
    mom     = bs.get('momentum', {})
    bd      = bs.get('confluence', {}).get('breakdown', {})
    regime  = bs.get('regime', 'CHOP_MID')
    regime_s= d['regime_s']
    reg_now = regime_s.get(sym + 'USDT', {}).get('confirmed', regime)

    # ══════════════════════════════════════════════════════
    # L1【一票否决层】三方战略架构 2026-09-04 苏摩111封印
    # 任何一项触发 → 禁止入场，直接返回等待卡片
    # ══════════════════════════════════════════════════════
    def _wait_card(reason: str, layer: str) -> str:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ [{layer}] 禁止入场\n'
            f'   原因: {reason}\n'
            f'   当前体制: {reg_now}  FVG方向: {fvg["dir"]}  AI议会: (见下)'
        )

    score_val = float(bs.get('score_final', bs.get('score', 0)))

    # L1-①: 死穴门控
    _DEAD = [
        ('BEAR_TREND', 'LONG'),
        ('BEAR_RECOVERY', 'SHORT'),
    ]
    _bias_hint = 'LONG' if (fvg['dir'] == 'BULL' or bs.get('bias') == 'LONG') else 'SHORT'
    for dead_regime, dead_dir in _DEAD:
        if dead_regime in reg_now and _bias_hint == dead_dir:
            return _wait_card(f'死穴封禁 {dead_regime}:{dead_dir}', 'L1-死穴')

    # L1-②: CHOP_MID低分禁止（score<80且体制CHOP）
    if 'CHOP' in reg_now and score_val < 80:
        return _wait_card(f'CHOP_MID体制score={score_val:.0f}<80，震荡无方向禁止入场', 'L1-CHOP')

    # L1-③: 宏观红色日历
    if mac.get('red_flag'):
        return _wait_card(f'宏观红色事件: {mac.get("event","?")}', 'L1-宏观')

    # L1-④: 风控熔断
    if risk.get('circuit_break'):
        return _wait_card(f'风控熔断触发: {risk.get("reason","?")}', 'L1-风控')

    # L2【方向决策层】FVG × 体制 × LiqMap × 大户仓位
    # HIGH-3修复: 大户仓位纳入L2否决票
    liq_bias     = liq.get('liq_bias', 'NEUTRAL')
    _big_long    = sm.get('big_long', 50) if sm else 50
    _regime_bull = any(x in reg_now for x in ('BULL', 'BULL_EARLY', 'BEAR_RECOVERY'))
    _regime_bear = any(x in reg_now for x in ('BEAR_TREND', 'BEAR_EARLY'))
    _fvg_bull    = fvg['dir'] == 'BULL'
    _fvg_bear    = fvg['dir'] == 'BEAR'
    _liq_bull    = liq_bias in ('UP', 'NEUTRAL')
    _liq_bear    = liq_bias in ('DOWN', 'NEUTRAL')
    _sm_bull     = _big_long >= 58   # 大户明显偏多
    _sm_bear     = _big_long <= 42   # 大户明显偏空

    # HIGH-1修复: BULL_EARLY+FVG=BEAR → 等待FVG触及后做多（特殊处理）
    # 不是方向矛盾，是触发器等待
    _bull_early_wait = ('BULL' in reg_now) and _fvg_bear
    if _bull_early_wait:
        _trigger_price = fvg['magnet']
        _wait_detail = (
            f'BULL_EARLY体制强势，等FVG磁铁${_trigger_price:,.0f}被触及后做多\n'
            f'   触发条件: 价格跌到${_trigger_price:,.0f} + 1H收阳确认\n'
            f'   届时入场区: ${_trigger_price*0.998:,.0f}~${_trigger_price*1.005:,.0f}\n'
            f'   止损: ${_trigger_price - vol.get("atr_1h", price*0.005)*1.5:,.0f}'
        )
        # 注意：这里不return，继续让AI议会裁决
        # 若AI议会=ENTER（FVG已触及），才输出入场

    # 三者一致性（排除BULL_EARLY特殊情形）
    _l2_long  = (_fvg_bull or _bull_early_wait) and _regime_bull and _liq_bull
    _l2_short = _fvg_bear and _regime_bear and _liq_bear and not _bull_early_wait

    # 大户否决：大户方向与L2结论相反时降级为WAIT
    if _l2_long and _sm_bear:
        return _wait_card(f'L2: 大户{_big_long:.0f}%偏空与做多方向矛盾，主力资金方向优先', 'L2-大户')
    if _l2_short and _sm_bull:
        return _wait_card(f'L2: 大户{_big_long:.0f}%偏多与做空方向矛盾，主力资金方向优先', 'L2-大户')

    # L3【入场方向校验】
    # HIGH-2修复: 等待时输出具体监控触发价
    _entry_lo = res.get('entry_lo', 0)
    _entry_hi = res.get('entry_hi', 0)
    # L3【入场方向+位置校验】苏摩111封印 2026-09-04
    # BULL_EARLY+FVG磁铁在上方：重新计算下方支撑区，判断现价位置
    if _l2_long and _entry_lo >= price:
        _atr_hint = vol.get('atr_1h', price * 0.005) if vol else price * 0.005
        _support  = liq.get('nearest_long', 0)
        _ob_floor = 0
        for _k, _v in ob.items():
            if 'BULL' in _k and _v.get('valid'):
                try:
                    import re as _re2
                    _ps = [float(x.replace(',','')) for x in _re2.findall(r'\$([\d,]+)', _v.get('note',''))]
                    if len(_ps) >= 1 and float(_ps[0]) < price:
                        _ob_floor = max(_ob_floor, float(_ps[0]))
                except: pass
        _es    = max(_support, _ob_floor) if (_support or _ob_floor) else price * 0.985
        _ehi_s = round(min(_es * 1.010, price * 1.000), 1)  # 最高不超过现价
        _elo_s = round(_es * 0.990, 1)
        _sl_s  = round(_elo_s - _atr_hint * 1.5, 1)

        if _elo_s <= price <= _ehi_s:
            # ✅ 现价在支撑区内 → 直接放行，修正入场区为当前区间
            _entry_lo = _elo_s
            _entry_hi = _ehi_s
            res['entry_lo'] = _elo_s
            res['entry_hi'] = _ehi_s
            res['resonance'] = True
            # 继续执行，不return
        else:
            # 等价格回调到支撑区
            return _wait_card(
                f'BULL_EARLY做多 FVG磁铁${fvg["magnet"]:,.0f}是目标 | '
                f'等回调至: ${_elo_s:,.0f}~${_ehi_s:,.0f} +1H收阳 | '
                f'SL:${_sl_s:,.0f} | T1:${fvg["magnet"]:,.0f} T2:${liq.get("nearest_short",0):,.0f}',
                'L3-等待回调'
            )

    elif _l2_short and _entry_lo > 0 and _entry_hi <= price:
        return _wait_card(
            f'FVG阻力${fvg["magnet"]:,.0f}未触及 现价${price:,.0f} 差${fvg["magnet"]-price:,.0f} | '
            f'触发价:${fvg["magnet"]*0.998:,.0f}~${fvg["magnet"]*1.002:,.0f}+1H收阴',
            'L3-等待触发'
        )

    # ══════════════════════════════════════════════════════
    # L1~L3通过，进入后续VIP生成
    # ══════════════════════════════════════════════════════

    atr_1h  = mom.get('atr_1h', 0)
    # ATR合理性验证：应在价格的0.2%~3%之间
    if not atr_1h or atr_1h > price * 0.03 or atr_1h < price * 0.002:
        k1h_local = d.get('k1h', [])
        if len(k1h_local) >= 3:
            tr_list = [abs(x[1] - x[2]) for x in k1h_local[-8:]]
            atr_1h  = round(sum(tr_list) / len(tr_list), 1)
        if not atr_1h or atr_1h > price * 0.03 or atr_1h < price * 0.002:
            atr_1h = price * 0.005

    # ATR全周期升级：4H + 1D（SL应参考操作周期ATR）苏摩111封印 2026-09-04
    k4h_local = d.get('k4h', [])
    k1d_local = d.get('k1d', [])
    atr_4h = 0.0
    atr_1d = 0.0
    if len(k4h_local) >= 5:
        atr_4h = round(sum(abs(x[1]-x[2]) for x in k4h_local[-7:])/min(7,len(k4h_local)), 1)
    if len(k1d_local) >= 5:
        atr_1d = round(sum(abs(x[1]-x[2]) for x in k1d_local[-7:])/min(7,len(k1d_local)), 1)
    # 合约参考SL = max(1.5×ATR1H, 1.0×ATR4H)
    atr_sl_ref = max(atr_1h * 1.5, atr_4h * 1.0) if atr_4h else atr_1h * 1.5

    # 方向判断（综合5个信号投票）
    bull_votes = 0
    bear_votes = 0

    if oi['signal'] in ('LONG_BUILD',):    bull_votes += 2
    if oi['signal'] in ('SHORT_BUILD',):   bear_votes += 2
    if oi['signal'] in ('SHORT_SQUEEZE',): bull_votes += 1

    if sm['signal'] in ('STRONG_BULL', 'MILD_BULL'): bull_votes += 2
    if sm['signal'] == 'BEAR':                        bear_votes += 2

    if fvg['dir'] == 'BULL': bull_votes += 1
    if fvg['dir'] == 'BEAR': bear_votes += 1

    if vol['kappa'] < -0.05: bull_votes += 1
    if vol['kappa'] > 0.05:  bear_votes += 1

    if 'BULL' in reg_now: bull_votes += 2
    if 'BEAR' in reg_now: bear_votes += 2

    bias = 'LONG' if bull_votes > bear_votes else ('SHORT' if bear_votes > bull_votes else 'NEUTRAL')

    # ══ 死穴门控（MEMORY.md封印铁律）══
    # BULL_TREND:LONG score≥140+SL≥3% → WR=0% 永久封禁
    # BEAR_RECOVERY:SHORT → WR=0% 严禁
    # BEAR_TREND:LONG → WR=45% 封禁
    _is_dead = False
    _dead_reason = ''
    _score_now = float(bs.get('score_final', bs.get('score', 0)))
    if 'BULL_TREND' in reg_now and bias == 'LONG' and _score_now >= 140:
        _sl_est = abs(res['entry_lo'] - (res['entry_lo'] * 0.97)) / res['entry_lo'] * 100
        if _sl_est >= 3.0:
            _is_dead = True
            _dead_reason = f'死穴: BULL_TREND:LONG score={_score_now:.0f}≥140 + SL≥3% → WR=0% 永久封禁'
    if 'BEAR_RECOVERY' in reg_now and bias == 'SHORT':
        _is_dead = True
        _dead_reason = 'BEAR_RECOVERY:SHORT → WR=0% 严禁'
    if 'BEAR_TREND' in reg_now and bias == 'LONG':
        _is_dead = True
        _dead_reason = f'BEAR_TREND:LONG → WR=45% EV=-2.0 封禁（精英解锁: score≥155+grade≥90+RSI<20）'

    if _is_dead:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'🚫 禁止入场 — {_dead_reason}\n'
            f'   当前体制: {reg_now}  方向: {bias}  score: {_score_now:.0f}'
        )

    # 无共振点 → 等待
    if not res['resonance']:
        missing_str = ' / '.join(res['missing']) if res['missing'] else '方向不明'
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ 当前无精确共振点 — 等待结构\n'
            f'   缺失条件：{missing_str}\n'
            f'   有效信号满足后自动更新'
        )

    entry_lo = res['entry_lo']
    entry_hi = res['entry_hi']

    # [P1修复 2026-09-10] 入场区 = max(共振下沿, 支撑池)
    _liq_support = res.get('liq_nearest_long', 0) or liq.get('nearest_long', 0)
    if _liq_support > 0 and bias == 'LONG' and entry_lo < _liq_support:
        # 入场区在支撑池下方 → 上移到支撑池上方
        _shift = _liq_support - entry_lo
        entry_lo = round(_liq_support, 1)
        entry_hi = round(entry_hi + _shift, 1)
        if entry_hi <= entry_lo:
            entry_hi = round(entry_lo * 1.005, 1)  # 入场区至少0.5%宽

    # D7修复: entry=0时强制走等待路径（不应进入SL计算）
    if entry_lo == 0.0 or entry_hi == 0.0:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⏳ 入场区无效（结构不满足），等待共振\n'
            f'   当前体制: {reg_now}  方向偏向: {bias}'
        )

    # D6修复: bias方向必须与入场区方向一致，否则拒绝输出
    # LONG bias → 需要BULL FVG → 入场区在现价下方 (entry_lo < price)
    # SHORT bias → 需要BEAR FVG → 入场区在现价上方 (entry_lo > price)
    _entry_dir_ok = True
    if bias == 'LONG' and entry_lo > 0 and entry_lo >= price:
        _entry_dir_ok = False
    if bias == 'SHORT' and entry_lo > 0 and entry_lo <= price:
        _entry_dir_ok = False
    if not _entry_dir_ok:
        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'⚠️ 方向冲突：bias={bias} 但入场区${entry_lo:,.0f}~${entry_hi:,.0f}在错误方向\n'
            f'   FVG方向={fvg["dir"]} 与投票方向={bias} 矛盾，等待方向收敛'
        )

    # 仓位调整（宏观+风控）
    base_lev_main = 10 if 'TREND' in reg_now else 5
    base_nav_main = 5  if 'TREND' in reg_now else 2
    if mac['has_event']:
        base_nav_main = max(1, base_nav_main // 2)
        base_lev_main = max(3, base_lev_main - 3)
    base_nav_main = round(base_nav_main * risk['nav_mult'])
    # [P4修复 2026-09-10] 失效期检测器：RED→仓位减半，YELLOW→仓位×0.75
    try:
        from pathlib import Path as _P
        _rd = _P(__file__).parent.parent / 'data' / 'dharma_regime_detector_result.json'
        if _rd.exists():
            _rd_data = json.loads(_rd.read_text())
            _regime_state = _rd_data.get('current_state', 'GREEN')
            if _regime_state == 'RED':
                base_nav_main = max(1, base_nav_main // 2)
                base_lev_main = max(3, base_lev_main // 2)  # [P1修复] 失效期RED杠杆也减半
            elif _regime_state == 'YELLOW':
                base_nav_main = max(1, round(base_nav_main * 0.75))
                base_lev_main = max(3, round(base_lev_main * 0.75))  # [P1修复] YELLOW杠杆×0.75
    except Exception:
        pass
    base_nav_main = max(1, base_nav_main)

    # [P1-3修复 2026-09-11] 仓位=f(score)线性映射
    # score=110 → 仓位×1.0（基线）
    # score=130 → 仓位×1.3
    # score=150 → 仓位×1.5（上限）
    _score = float(bs.get('score_final', bs.get('score', 0)))
    if _score >= 110:
        _score_mult = min(1.5, 1.0 + (_score - 110) / 100.0)  # 110→1.0, 160→1.5
        base_nav_main = max(1, round(base_nav_main * _score_mult))

    lev_side  = max(3, base_lev_main - 5)
    nav_side  = max(1, base_nav_main // 2)

    # SL / TP
    # [P1修复 2026-09-10 苏摩111] SL铁律：max(SL_PCT, 1.5×ATR4H)
    # SL_PCT: 做多2.0% / BULL做空2.5% / BEAR做空2.0%
    # ATR铁律: SL距离 >= 1.5×ATR4H（不是ATR1H！）
    if bias == 'LONG':
        sl_pct_required = 0.02  # 做多2.0%
        min_sl = max(entry_lo * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_lo * sl_pct_required
        sl       = round(entry_lo - min_sl, 1)
        sl_pct   = round((entry_lo - sl) / entry_lo * 100, 2)
        tp1      = round(liq['nearest_short'] if liq['nearest_short'] > price else price + atr_1h * 2.5, 1)
        tp2      = round(liq['second_short']  if liq.get('second_short', 0) > tp1 else tp1 + atr_1h * 2, 1)
        tp3      = round(tp2 + atr_1h * 2, 1)
        rr       = round((tp1 - entry_lo) / (entry_lo - sl), 2) if entry_lo > sl else 0

        # BUG-6修复：多单入场区在现价下方（等回调），描述明确
        main_line   = f'🟢 多单｜回调入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}（价格跌到此区挂单）'
        main_params = f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}'

        # [P1修复 2026-09-10] BULL_TREND → 只出多单，不出空单
        if 'BULL' in str(reg_now) or 'BEAR_RECOVERY' in str(reg_now):
            side_line  = '🔴 暂无空单｜等待结构'
            side_params = 'BULL体制不做空'
        else:
            side_hi    = round(entry_lo + atr_1h * 2.0, 1)
            side_lo    = round(entry_lo + atr_1h * 1.2, 1)
            side_sl_pct = 0.025 if 'BULL' in str(reg_now) else 0.02
            side_min_sl = max(side_hi * side_sl_pct, atr_4h * 1.5) if atr_4h else side_hi * side_sl_pct
            side_sl    = round(side_hi + side_min_sl, 1)
            side_tp1   = round(entry_lo - atr_1h * 1.5, 1)
            side_tp2   = round(side_tp1 - atr_1h * 1.5, 1)
            side_tp    = f'${side_tp1:,.0f}→${side_tp2:,.0f}'
            side_line  = f'🔴 空单（轻）｜若反弹到 ${side_lo:,.1f}~${side_hi:,.1f} 再空'
            side_params= f'止损 ${side_sl:,.1f}｜目标 {side_tp}'
        main_dir   = '主方向做多'

    else:  # SHORT
        sl_pct_required = 0.025 if 'BULL' in str(reg_now) else 0.02  # BULL做空2.5%/BEAR做空2.0%
        min_sl = max(entry_hi * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_hi * sl_pct_required
        sl       = round(entry_hi + min_sl, 1)
        sl_pct   = round((sl - entry_hi) / entry_hi * 100, 2)
        tp1      = round(liq['nearest_long'] if liq['nearest_long'] < price else price - atr_1h * 2.5, 1)
        tp2      = round(liq['second_long']  if liq.get('second_long', 0) > 0 and liq['second_long'] < tp1 else tp1 - atr_1h * 2, 1)
        tp3      = round(tp2 - atr_1h * 2, 1)
        rr       = round((entry_hi - tp1) / (sl - entry_hi), 2) if sl > entry_hi else 0

        # BUG-6修复：空单入场区在现价上方（等反弹），描述明确
        main_line   = f'🔴 空单｜反弹入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}（价格反弹到此区挂单）'
        main_params = f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}'

        # [P1修复 2026-09-10] BEAR_TREND → 只出空单，不出多单
        if 'BEAR' in str(reg_now):
            side_line  = '🟢 暂无多单｜等待结构'
            side_params = 'BEAR体制不做多'
        else:
            hunt_lo    = round(entry_lo - atr_1h * 1.5, 1)
            hunt_hi    = round(entry_lo - atr_1h * 0.5, 1)
            side_min_sl = max(hunt_lo * 0.02, atr_4h * 1.5) if atr_4h else hunt_lo * 0.02
            side_sl    = round(hunt_lo - side_min_sl, 1)
            side_tp1   = round(entry_lo + atr_1h * 1.5, 1)
            side_tp2   = round(side_tp1 + atr_1h * 1.5, 1)
            side_tp    = f'${side_tp1:,.0f}→${side_tp2:,.0f}'
            side_line  = f'🟢 多单（轻）｜若下探 ${hunt_lo:,.1f}~${hunt_hi:,.1f} 被扫后接'
            side_params= f'止损 ${side_sl:,.1f}｜目标 {side_tp}'
        main_dir   = '主方向做空'

    # 风控提示
    risk_note = ''
    if mac['has_event']:
        risk_note = f'\n⚠️ 宏观事件({", ".join(mac["high_impact"][:1])})→仓位已压缩'
    if risk['blocks']:
        risk_note += f'\n🚨 风控: {risk["blocks"][0]}'

    # SL验证 [P2修复 2026-09-10] 用ATR4H铁律验证
    sl_distance = abs(entry_lo - sl) if bias == 'LONG' else abs(sl - entry_hi)
    atr4h_threshold = atr_4h * 1.5 if atr_4h else atr_1h * 1.5
    sl_ok = sl_distance >= atr4h_threshold
    sl_tag = '✅' if sl_ok else '⚠️偏窄'

    lines = [
        f'──── VIP ────',
        f'🌿 姓赵不宣 | {sym}({reg_now}) 今日布局',
        f'',
        f'{main_line}',
        f'{main_params}',
        f'杠杆 {base_lev_main}x｜仓位 {base_nav_main}%  RR={rr}x  SL={sl_pct:.2f}% {sl_tag}',
        f'',
        f'{side_line}',
        f'{side_params}',
        f'杠杆 {lev_side}x｜仓位 {nav_side}%',
        f'',
        f'⚠️ {main_dir}  ATR1H=${atr_1h:.0f} ATR4H=${atr_4h:.0f}',
        f'🚫 破${sl:,.0f} 策略作废',
    ]
    if risk_note:
        lines.append(risk_note)

    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════
# 主报告组装
# ══════════════════════════════════════════════════════════

def _trader_narrative(sym, price, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, tb_result) -> str:
    """40年顶级合约交易员视角：用94维数据串成市场故事，不是列条件"""
    bs = d.get('bs', {})
    regime = bs.get('regime', 'CHOP_MID')
    score = float(bs.get('score_final', bs.get('score', 0)))
    hurst = vol.get('hurst', 0.5)
    kappa = vol.get('kappa', 0)
    big_long = sm.get('big_long', 50)
    retail_long = sm.get('retail_long', 50)
    oi_signal = oi.get('signal', 'NO_DATA')
    cvd_1h = oi.get('cvd_1h', 0)
    fvg_dir = fvg.get('dir', 'NONE')
    fvg_consensus = fvg.get('consensus', fvg_dir)
    fvg_mid = fvg.get('magnet', 0)
    liq_short = liq.get('nearest_short', 0)
    liq_long = liq.get('nearest_long', 0)
    liq_2nd_short = liq.get('second_short', 0)
    liq_2nd_long = liq.get('second_long', 0)
    atr_1h = vol.get('atr_1h', 0)
    atr_4h = vol.get('atr_4h', 0)
    regime_state = risk.get('regime_state', 'GREEN')
    direction = tb_result.get('direction', 'NONE')
    entry_lo = tb_result.get('entry_lo', 0) or res.get('entry_lo', 0)
    entry_hi = tb_result.get('entry_hi', 0) or res.get('entry_hi', 0)
    sl = tb_result.get('sl', 0)
    rr = tb_result.get('rr', 0)
    missing = tb_result.get('missing', [])
    cross = tb_result.get('consistent_count', 0)
    k4h_mult = d.get('_k4h_mult', 1.0)
    k1h_mult = d.get('_k1h_mult', 1.0)

    parts = []

    # 1. 市场现状一句话
    if regime == 'CHOP_MID' and score < 110:
        parts.append(f'{sym}在${price:,.0f}横盘，CHOP体制score={score:.0f}，大户{big_long:.0f}%多但OI={oi_signal}。')
    elif 'BULL' in regime:
        parts.append(f'{sym}在${price:,.0f}，{regime} score={score:.0f}，FVG{fvg_consensus}共识，OI={oi_signal}。')
    elif 'BEAR' in regime:
        parts.append(f'{sym}在${price:,.0f}，{regime} score={score:.0f}，FVG{fvg_consensus}共识，OI={oi_signal}。')
    else:
        parts.append(f'{sym}在${price:,.0f}，体制{regime} score={score:.0f}。')

    # 2. 主力意图
    _intent = ''
    if big_long >= 60 and 'UNWIND' in oi_signal:
        _intent = f'大户{big_long:.0f}%多但OI全线撤退=大户在等不是在加'
    elif big_long >= 60 and oi_signal in ('LONG_BUILD', 'SHORT_SQUEEZE'):
        _intent = f'大户{big_long:.0f}%多+OI={oi_signal}=主力在加多'
    elif big_long <= 45 and 'BUILD' in oi_signal and 'SHORT' in oi_signal:
        _intent = f'大户{big_long:.0f}%偏空+OI={oi_signal}=主力在加空'
    elif big_long >= 55 and oi_signal == 'SHORT_BUILD':
        _intent = f'大户{big_long:.0f}%多但OI={oi_signal}=有人在高位挂空单对冲'
    elif 'UNWIND' in oi_signal:
        _intent = f'OI={oi_signal}=资金在减仓离场，不是加仓'
    else:
        _intent = f'大户{big_long:.0f}%多 vs 散户{retail_long:.0f}%多，OI={oi_signal}'
    parts.append(_intent + '。')

    # 3. 趋势状态
    if hurst >= 0.6:
        parts.append(f'Hurst={hurst:.3f}趋势要来但还没到，')
    elif hurst >= 0.55:
        parts.append(f'Hurst={hurst:.3f}趋势性隐现，')
    elif hurst >= 0.5:
        parts.append(f'Hurst={hurst:.3f}随机游走，')
    else:
        parts.append(f'Hurst={hurst:.3f}均值回归，')

    if kappa < -0.1:
        parts[-1] += f'κ={kappa:.3f}Call强(大资金买上涨保险)。'
    elif kappa > 0.1:
        parts[-1] += f'κ={kappa:.3f}Put强(大资金买下跌保险)。'
    else:
        parts[-1] += f'κ={kappa:.3f}中性。'

    # 4. 剧本推演
    _scenarios = []
    if liq_short > price:
        _up_pct = (liq_short - price) / price * 100
        _scenarios.append(f'上方${liq_short:,.0f}(+{_up_pct:.1f}%)空头止损墙最密集，破了就是逼空')
        if liq_2nd_short > liq_short:
            _scenarios.append(f'到${liq_2nd_short:,.0f}')
    if liq_long > 0 and liq_long < price:
        _dn_pct = (price - liq_long) / price * 100
        _scenarios.append(f'下方${liq_long:,.0f}(-{_dn_pct:.1f}%)支撑池，破了就是猎杀')
        if liq_2nd_long > 0 and liq_2nd_long < liq_long:
            _scenarios.append(f'到${liq_2nd_long:,.0f}')
    if _scenarios:
        parts.append('。'.join(_scenarios) + '。')

    # 5. 关键价位汇总
    _levels = []
    if entry_lo > 0 and entry_hi > 0 and direction != 'NONE':
        _levels.append(f'入场区${entry_lo:,.1f}~${entry_hi:,.1f}')
    if sl > 0:
        _levels.append(f'SL=${sl:,.1f}')
    if liq_short > price:
        _levels.append(f'上方目标${liq_short:,.0f}')
    if liq_long > 0 and liq_long < price:
        _levels.append(f'下方支撑${liq_long:,.0f}')
    if fvg_mid > 0:
        _levels.append(f'FVG磁铁${fvg_mid:,.1f}')
    if _levels:
        parts.append('关键价位：' + ' / '.join(_levels) + '。')

    # 6. 交易员结论
    if direction != 'NONE' and entry_lo > 0:
        if missing:
            _miss_short = '、'.join(missing[:3])
            parts.append(f'方向{direction}，入场区${entry_lo:,.1f}~${entry_hi:,.1f}已给，但{_miss_short}。')
            if regime_state == 'RED':
                parts.append(f'失效期RED→仓位强制减半，只能轻仓试探。')
            if rr > 0 and rr < 2.0:
                parts.append(f'RR={rr:.1f}不够，等止损墙拉远或入场区上移。')
            if 'OI' in ' '.join(missing):
                parts.append(f'OI跟方向反=资金流不确认，等OI翻转再进。')
            parts.append(f'如果条件确认（OI翻转+score过{120 if regime_state == "RED" else 110}），这单可以进。')
        else:
            parts.append(f'条件全满，{direction}入场区${entry_lo:,.1f}~${entry_hi:,.1f}可以进。')
    elif direction != 'NONE' and entry_lo == 0:
        parts.append(f'方向{direction}但OI矛盾导致入场区失效。')
        if liq_long > 0 and liq_long < price and direction == 'LONG':
            parts.append(f'下方支撑${liq_long:,.0f}是潜在接位点，等OI从{oi_signal}翻转为LONG_BUILD，在${liq_long:,.0f}~${price:,.0f}区间接。')
        elif liq_short > price and direction == 'SHORT':
            parts.append(f'上方阻力${liq_short:,.0f}是潜在空点，等OI确认后在此区域空。')
        if regime_state == 'RED':
            parts.append(f'失效期RED→即使条件确认也只轻仓。')
    elif direction == 'NONE':
        parts.append(f'体制{regime}无方向，不强行做。等Hurst确认+score站上110，${price*0.985:,.0f}附近多单可以试。')

    return ' '.join(parts)


def run_analysis(sym: str) -> str:
    ts  = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')
    print(f'[{sym}] Step 0: 拉取实时数据...', flush=True)
    t_start = __import__('time').time()
    d   = step0_fetch_all(sym)
    p   = d['price']  # 分析基准价（拉取时刻）

    # ── CHOP盲区旁路检测（不影响主链路）──────────────────────
    try:
        from breakout_watch import run_breakout_watch
        bw = run_breakout_watch([sym + 'USDT'])
        bw_alerts = bw.get('alerts', [])
        if bw_alerts:
            a = bw_alerts[0]
            print(f'[{sym}] 🚨 BREAKOUT_WATCH触发! score={a["score"]}/3 level={a["level"]}', flush=True)
        else:
            bw_score = bw['results'].get(sym+'USDT', {}).get('score', 0)
            print(f'[{sym}] CHOP旁路: score={bw_score}/3 无触发', flush=True)
    except Exception as _bw_e:
        print(f'[{sym}] CHOP旁路检测跳过: {_bw_e}', flush=True)
    # ─────────────────────────────────────────────────────────

    print(f'[{sym}] Step 1~3: FVG/OB/清算...', flush=True)
    fvg = step1_fvg(d)
    ob  = step2_ob(d)
    liq = step3_liq(d)

    print(f'[{sym}] Step 5~9: OI/聪明钱/波动率/宏观/风控...', flush=True)
    oi  = step5_oi(d)
    sm  = step6_smart_money(d)
    vol = step7_volatility(d)
    mac = step8_macro(d)
    risk= step9_risk(d)

    # [P2-4修复] Step 4共振升级5维：需要oi+vol
    print(f'[{sym}] Step 4: 共振点（5维：FVG+OB+清算+OI+GEX）...', flush=True)
    res = step4_resonance(d, fvg, ob, liq, oi=oi, vol=vol)

    # AI议会实时裁决（纯规则引擎，零延迟零成本）
    council = {}
    try:
        import sys as _sys2
        _sys2.path.insert(0, str(BASE / 'brahma_brain'))
        from llm_council import council_verdict
        bd_c     = d['bs'].get('confluence', {}).get('breakdown', {})
        regime_c = d['regime_s'].get(sym+'USDT',{}).get('confirmed', d['bs'].get('regime','CHOP_MID'))
        bias_dir_c = 'LONG' if (oi['signal'] in ('LONG_BUILD','SHORT_SQUEEZE') and sm['big_long'] > 52) else 'SHORT'
        council = council_verdict(
            breakdown=bd_c, signal_dir=bias_dir_c,
            regime=regime_c,
            score=float(d['bs'].get('score_final', d['bs'].get('score', 0))),
            liq_data=liq,
            # 传入实时信号供LLM真实裁决（设计院三方封印 2026-09-04）
            fvg_dir=fvg['dir'],
            oi_signal=oi['signal'],
            sm_signal=sm['signal'],
            hurst=vol['hurst'],
            kappa=vol['kappa'],
            entry_lo=res['entry_lo'],
            entry_hi=res['entry_hi'],
            price=p,
            sym=sym,
        )
    except Exception as _ce:
        council = {'bias':'N/A','reason':str(_ce)[:40],'action':'WAIT','confidence':'LOW'}

    # ── trader_brain 6层确定性决策引擎（2026-09-11 苏摩111封印）──
    tb_result = {}
    try:
        from brahma_brain.trader_brain import decide as tb_decide, format_vip_card as tb_format
        regime_c = d['regime_s'].get(sym+'USDT',{}).get('confirmed', d['bs'].get('regime','CHOP_MID'))
        tb_result = tb_decide(
            regime=regime_c,
            score=float(d['bs'].get('score_final', d['bs'].get('score', 0))),
            grade=float(d['bs'].get('grade', 0)),
            macro=mac,
            risk=risk,
            hurst=vol.get('hurst', 0.5),
            fvg=fvg,
            ob=ob,
            liq=liq,
            atr_1h=vol.get('atr_1h', 0),
            atr_4h=vol.get('atr_4h', 0),
            price=p,
            oi=oi,
            sm=sm,
            vol=vol,
            res=res,
            symbol=sym+'USDT',
        )
    except Exception as _tbe:
        tb_result = {'action':'WAIT','reason':f'trader_brain异常: {str(_tbe)[:60]}','missing':['trader_brain异常']}
        try:
            from brahma_brain.trader_brain import format_vip_card as tb_format
        except Exception:
            tb_format = None

    # trader_brain替代AI议会作为最终裁决
    _regime_c = regime_c if 'regime_c' in dir() else d['bs'].get('regime', 'CHOP_MID')
    final_action = tb_result.get('action', 'WAIT')
    final_direction = tb_result.get('direction', 'NONE')
    final_reason = tb_result.get('reason', council.get('reason', ''))
    final_missing = tb_result.get('missing', [])
    final_confidence = tb_result.get('confidence', council.get('confidence', 'LOW'))
    final_consistent = tb_result.get('consistent_count', 0)

    # BUG-1修复：分析完成后拉一次实时价，检测漂移
    import time as _t, urllib.request as _ur, ssl as _ssl, json as _js
    try:
        _ctx = _ssl.create_default_context(); _ctx.check_hostname=False; _ctx.verify_mode=_ssl.CERT_NONE
        _live = float(_js.loads(_ur.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT', timeout=4, context=_ctx
        ).read()).get('price', p))
    except Exception:
        _live = None  # D9修复: 拉价格失败时标记为None，不用p掩盖偏差
    _elapsed    = _t.time() - t_start
    _drift_pct  = ((_live - p) / p * 100) if _live is not None else 0.0
    _live       = _live if _live is not None else p   # display用
    _price_warn = ''
    if abs(_drift_pct) >= 1.0:
        _price_warn = f'\n⚠️ 【价格漂移警告】分析基准${p:,.0f} → 当前${_live:,.0f} 偏差{_drift_pct:+.1f}% 入场区已失效，请重跑'
    elif abs(_drift_pct) >= 0.5:
        _price_warn = f'\n⚠️ 价格微偏：分析${p:,.0f}→当前${_live:,.0f}({_drift_pct:+.1f}%)，入场区仅供参考'

    print(f'[{sym}] Step 10: 生成VIP卡片...', flush=True)

    bd      = d['bs'].get('confluence', {}).get('breakdown', {})
    regime  = d['bs'].get('regime', 'UNKNOWN')
    score   = d['bs'].get('score_final', d['bs'].get('score', 0))
    grade   = d['bs'].get('grade', 0)
    hurst_s = str(bd.get('Hurst体制验证', '') or '')
    hcme_s  = str(d['bs'].get('hcme_ctx', '') or '')
    fc_raw  = d['bs'].get('fangcang', {})
    fc_case = ''
    if isinstance(fc_raw, dict):
        top = fc_raw.get('top_similar', [])
        if top:
            t = top[0]
            fc_case = f'{t.get("dt","?")} 相似度{t.get("score",0):.3f} 未来收益{t.get("future_ret",0):+.2f}%'

    k4h    = d['k4h']
    k4h_last = k4h[-1] if k4h else None
    k4h_prev_vols = [x[4] for x in k4h[:-1]] if k4h else []
    avg_v4h = sum(k4h_prev_vols) / len(k4h_prev_vols) if k4h_prev_vols else 0
    k4h_vol_mult = round(k4h_last[4] / avg_v4h, 1) if avg_v4h and k4h_last else 1.0
    # [P2修复 2026-09-10] 量能倍数最小值0.1x，避免round后显示0.0x
    k4h_vol_mult = max(0.1, k4h_vol_mult)

    k1h    = d['k1h']
    vol_avg1h = sum(x[4] for x in k1h[:-2]) / max(len(k1h)-2, 1) if len(k1h) > 2 else 0
    vol_last1h= k1h[-1][4] if k1h else 0
    k1h_mult  = round(vol_last1h / vol_avg1h, 1) if vol_avg1h else 1.0
    # [P2修复 2026-09-10] 量能倍数最小值0.1x，避免round后显示0.0x
    k1h_mult  = max(0.1, k1h_mult)

    vip = step10_vip(sym, p, d, fvg, ob, liq, res, oi, sm, vol, mac, risk)

    # A: VIP入场理由LLM生成
    # B: 信号矛盾自动LLM裁决
    # [P1-2修复 2026-09-11] AI议会确定性化：移除LLM裁决，用规则替代
    _llm_entry_reason = ''
    _llm_conflict     = ''
    try:
        # A: 入场理由 → 用规则生成（替代LLM）
        if res['resonance'] and res['entry_lo'] > 0:
            _bias_a = 'LONG' if fvg['dir'] == 'BULL' else 'SHORT'
            _llm_entry_reason = (
                f'{regime}体制顺势{_bias_a}+'
                f'{fvg["dir"]}FVG磁铁+'
                f'OI={oi["signal"]}+'
                f'清算墙${liq["nearest_short"]:,.0f}磁吸'
            )
        # B: 矛盾裁决 → 用规则判断（替代LLM）
        _oi_bull = oi['signal'] in ('LONG_BUILD', 'SHORT_SQUEEZE')
        _sm_bull = sm['signal'] in ('STRONG_BULL', 'MILD_BULL')
        if _oi_bull != _sm_bull:
            _llm_conflict = (
                f'OI={oi["signal"]} vs 聪明钱={sm["signal"]}矛盾 → '
                f'{"跟随OI" if abs(oi.get("total_change",0))>5000 else "跟随聪明钱"}'
            )
    except Exception:
        pass

    lines = [
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'🏛️ 梵天80维全能力分析 | {sym}/USDT 基准${p:,.0f}→实时${_live:,.0f} | {ts} (耗时{_elapsed:.0f}s)',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'',
        f'【体制】{regime}  score={score:.0f}  grade={grade}',
        f'',
        f'【Step1 FVG磁铁】全周期',
        (f'  共识方向: {fvg.get("consensus",fvg["dir"])}  多{fvg.get("bull_score",0)}分 vs 空{fvg.get("bear_score",0)}分  主磁铁: {fvg["dir"]}@${fvg["magnet"]:,.0f}') if fvg['magnet'] else '  无有效FVG',
        f'  {fvg["desc"][:120]}',
        f'',
        f'【Step2 OB有效性】',
    ]
    if ob:
        for k, v in ob.items():
            lines.append(f'  {k}: {v["note"][:80]}')
    else:
        lines.append('  无OB数据')

    lines += [
        f'',
        f'【Step3 清算地图】',
        f'  🎯上方空头止损墙: ${liq["nearest_short"]:,.0f} (+{liq["nearest_short_pct"]:.1f}%，目标+{liq["target_pct"]:.1f}%)'
        + (f'  → 第二层: ${liq["second_short"]:,.0f}' if liq.get('second_short') else ''),
        f'  🛡️下方多头支撑池: ${liq["nearest_long"]:,.0f} (-{liq["support_pct"]:.1f}%)',
        f'',
        f'【Step4 共振点】5维（FVG+OB+清算+OI+GEX）',
        f'  共振得分: {res["score"]}/5  {"✅有效共振，可布局" if res["resonance"] and res["entry_lo"] > 0 else ("⚠️共振但方向矛盾，不出入场区" if res["resonance"] and res["entry_lo"] == 0 else "❌共振不足，等待")}',
        f'  FVG={res["has_fvg"]} OB={res["has_ob"]} 清算={res["has_liq"]} OI={res.get("has_oi",False)} GEX={res.get("has_gex",False)}',
        f'  入场区间: ${res["entry_lo"]:,.1f} ~ ${res["entry_hi"]:,.1f}',
    ]
    if res['missing']:
        lines.append(f'  缺失: {" / ".join(res["missing"])}')

    lines += [
        f'',
        f'【Step5 OI趋势】全周期',
        f'  15M:{oi.get("signal_15m","?")} | 1H:{oi.get("signal_1h","?")} | 4H:{oi.get("signal_4h","?")}',
        f'  主信号: {oi["signal"]} (置信{oi.get("conf",0):.0%}) | {oi["conclusion"][:60]}',
        f'  15min序列: {" → ".join(str(int(v)) for v in oi["trend"])}',
        f'  累计变化: {oi["total_change"]:+,.0f}张  OI价值变化: {oi["usd_change_m"]:+.1f}M',
        f'  {oi.get("cvd_note","")}',  # [P0] CVD展示
        f'',
        f'【Step6 聪明钱分歧】',
        f'  {sm["conclusion"]}',
        f'  大户多{sm["big_long"]}% vs 散户多{sm["retail_long"]}%  分歧={sm["diverge"]}%',
        f'  大户2H变化: {sm.get("top_delta",0.0):+.1f}%pt ({"主力加多↑" if sm.get("top_delta",0)>0.5 else "主力减多↓" if sm.get("top_delta",0)<-0.5 else "平稳"})',
        f'',
        f'【Step7 波动率四维+ATR全周期】',  # [P0] 升级为四维
        f'  {vol["hurst_note"]}',
        f'  {vol["kappa_note"]}',
        f'  {vol.get("gex_note","")}',  # [P0] GEX展示
        f'  {vol.get("fr_note","")}',   # [P1] FR展示
        f'  β⁺={vol.get("beta_p",0):.3f} β⁻={vol.get("beta_m",0):.3f}  IV分位={vol["iv_rank"]}',  # [P2] β展示
        f'  ATR1H=${vol.get("atr_1h",0):.0f} ATR4H=${vol.get("atr_4h",0):.0f} ATR1D=${vol.get("atr_1d",0):.0f}  合约SL参考=${vol.get("atr_sl_ref",0):.0f}({vol.get("atr_sl_ref",0)/p*100:.2f}%)',
        f'',
        f'【Step8 宏观压制】',
        f'  {mac["pos_note"]}',
        f'  恐贪={mac["fear_greed"]}  宏观偏向={mac["macro_bias"]}',
    ]
    if res.get('cross_check',{}).get('conflicts'):
        lines.append(f'  ⚠️ 交叉验证矛盾: {" / ".join(res["cross_check"]["conflicts"])}')
    elif res.get('cross_check',{}).get('consistent'):
        lines.append(f'  ✅ 交叉验证一致：结构层与市场层方向一致')
    if mac['high_impact']:
        lines.append(f'  ⚠️重大事件: {" / ".join(mac["high_impact"][:2])}')

    lines += [
        f'',
        f'【Step9 风控门控】',
        f'  熔断器: {"✅绿灯" if risk["circuit_ok"] else "🔴触发"}  '
        f'回撤: {risk["dd_pct"]:.1f}% {"✅正常" if risk["dd_ok"] else "⚠️"}  '
        f'连亏: {risk["consec"]}笔 {"✅" if risk["af_ok"] else "⚠️冷却"}',
        f'  失效期: {risk.get("regime_state","GREEN")} {risk.get("regime_note","")}',  # [P1] 失效期展示
        f'  仓位系数: x{risk["nav_mult"]}',
    ]
    if risk['blocks']:
        for b in risk['blocks']:
            lines.append(f'  🚨{b}')

    lines += [
        f'',
        f'【关键附加维度】',
        f'  4H量能倍数: {k4h_vol_mult}x（均量倍数，>2=放量突破）',
        f'  1H量能倍数: {k1h_mult}x',
        f'  Hurst: {hurst_s[:60]}',
        f'  HCME: {hcme_s[:60]}',
        f'  方仓最相似案例: {fc_case or "无数据"}',
    ]
    # B: 信号矛盾裁决（有就显示）
    if _llm_conflict:
        lines.append(f'  ⚙️ 规则矛盾裁决: {_llm_conflict}')

    # ── 交易员叙事（40年顶级合约交易员视角）──
    _narrative = _trader_narrative(sym, p, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, tb_result)
    if _narrative:
        lines += [
            f'',
            f'── 交易员视角 ──',
            _narrative,
        ]

    lines += [
        f'',
        f'{"─"*43}',
    ]

    # ── trader_brain裁决替代AI议会（2026-09-11 苏摩111封印）──
    _tb_action = tb_result.get('action', 'WAIT')
    _vip_blocked = _tb_action == 'WAIT'
    if _vip_blocked:
        # WAIT也输出观点卡片，不是空白
        try:
            _vip_out = '──── 观点 ────\n' + tb_format(
                tb_result, sym+'USDT', p, _regime_c
            )
        except Exception:
            _vip_out = (
                f'──── 观点 ────\n'
                f'🌿 姓赵不宣 | {sym} 今日观点\n'
                f'⏳ {tb_result.get("reason", "等待")[:60]}'
            )
    else:
        _vip_out = '──── VIP ────\n' + vip
        if _llm_entry_reason and '⚠️' in _vip_out:
            _vip_out = _vip_out.replace(
                next((l for l in _vip_out.split('\n') if '⚠️' in l), ''),
                next((l for l in _vip_out.split('\n') if '⚠️' in l), '') + f'  |入场逻辑: {_llm_entry_reason}',
                1
            )

    # BUG-1：如果入场区已失效，在VIP之前追加警告
    if _price_warn and abs(_drift_pct) >= 1.0:
        _vip_out = f'☠️ 「入场区已失效」基准${p:,.0f} → 当前${_live:,.0f}({_drift_pct:+.1f}%)，请重新跑分析\n' + _vip_out

    _tb_bias = tb_result.get('direction', 'NONE')
    _tb_conf = tb_result.get('confidence', 'LOW')
    _tb_cross = tb_result.get('consistent_count', 0)
    _tb_layers = tb_result.get('cross_check', {}).get('layer_directions', {})
    _tb_layer_str = ' '.join(f'{k}={v}' for k,v in _tb_layers.items()) if _tb_layers else ''
    lines += [
        _vip_out,
        f'{"─"*43}',
        (f'🧠 交易员大脑: {_tb_action} | 方向={_tb_bias} | 置信={_tb_conf} | 交叉验证={_tb_cross}/4'
         + (f'\n   {_tb_layer_str}' if _tb_layer_str else '')
         + (f'\n   缺: {" ".join(tb_result.get("missing",[]))}' if tb_result.get('missing') else '')),
        f'📊 梵天系统 · 80维 · 6层确定性决策 · 交易员大脑',
    ]
    if _price_warn:
        lines.append(_price_warn)

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='梵天手动全链路分析')
    ap.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    args = ap.parse_args()

    symbols = args.symbols

    if len(symbols) == 1:
        # 单个标的直接运行
        print(run_analysis(symbols[0]))
        return

    # 多标的并行化（60s→30s）——设计院三方封印 2026-09-04 苏摩111
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time
    t0 = _time.time()
    results = {}

    # [P0并发预热 2026-09-07 苏摩111] 多标的并行预热数据缓存
    # 串行: BTC 2.7s + ETH 1.8s = 4.5s → 并行: max(2.7, 1.8) ≈ 2.0s，节省2.5s
    try:
        from data_cache import prefetch_symbol as _pf
        _syms_u = [s.upper() + ('USDT' if 'USDT' not in s.upper() else '') for s in symbols]
        with ThreadPoolExecutor(max_workers=len(_syms_u)) as _pf_pool:
            _pf_futs = [_pf_pool.submit(_pf, sym) for sym in _syms_u]
            for _f in as_completed(_pf_futs):
                pass  # 等待所有预热完成
        print(f'[P0并发预热] {len(_syms_u)}个标的缓存已就绪 ({_time.time()-t0:.1f}s)')
    except Exception:
        pass  # 预热失败不阻断主流程

    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futures = {pool.submit(run_analysis, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as e:
                results[sym] = f'[{sym}] 分析失败: {e}'

    elapsed = _time.time() - t0
    print(f'\n[并行分析完成] 耗时 {elapsed:.1f}s ({len(symbols)}个标的并行)\n')

    # 汇总输出 + 推送到Jarvis（2026-09-08 苏摩111封印）
    full_output = []
    for sym in symbols:
        r = results.get(sym, f'[{sym}] 无结果')
        print(r)
        print()
        full_output.append(r)

    # 推送到Jarvis新线程
    try:
        import sys as _sys
        _sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
        from push_hub import push_jarvis as _push_j
        combined = '\n\n'.join(full_output)
        # 截断防超长
        if len(combined) > 3000:
            combined = combined[:3000] + '\n...[截断]'
        _push_j(f'🏟️ 梵天战场情报 | {__import__("datetime").datetime.utcnow().strftime("%m-%d %H:%M")} UTC\n\n{combined}')
        print('[推送] 战场情报已推送到Jarvis')
    except Exception as _pe:
        print(f'[推送失败] {_pe}')


if __name__ == '__main__':
    main()

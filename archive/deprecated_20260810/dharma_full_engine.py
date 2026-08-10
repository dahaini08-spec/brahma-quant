#!/usr/bin/env python3
"""
达摩院 全能力实测引擎 v1.0
==============================
梵天35维矩阵 × 6.5年历史数据 × 无上帝视角
设计院封印 2026-08-03

核心突破：
  - 完全离线运行：patch urllib + regime_scorer + live_price_feed
  - 注入历史K线：走完brahma_engine全部35维
  - BRAHMA_SKIP_S25=1：跳过LLM s25层（纯数学层验证）
  - 采样策略：每N根4H bar采样一次（≈每16H一个信号机会）
  - 持仓模拟：基于analyze()的score/direction/entry/sl/tp做真实出场

速度：约0.5s/次 × 采样~900次 = ~7分钟完成全量BTC+ETH
"""
import sys, json, time, os, math
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data' / 'backtest'

# ══ STEP 0: 环境配置 ══
os.environ['BRAHMA_SKIP_S25'] = '1'  # 跳过LLM
sys.path.insert(0, str(BASE))

# ══ STEP 1: 全局网络拦截 ══
import urllib.request as _ur_mod

_PRICE_NOW = {'v': 42000.0}
_LSR_NOW   = {'v': 50.0}

def _offline_urlopen(url, *args, **kwargs):
    url_s = url if isinstance(url, str) else getattr(url, 'full_url', str(url))
    price = _PRICE_NOW['v']
    if 'openInterest' in url_s:
        d = {'openInterest': '12000000000', 'time': 0}
    elif 'ticker/price' in url_s and 'symbol' not in url_s:
        # 批量ticker
        d = [{'symbol': 'BTCUSDT', 'price': str(price)},
             {'symbol': 'ETHUSDT', 'price': str(price * 0.045)}]
    elif 'ticker/price' in url_s:
        sym = url_s.split('symbol=')[1].split('&')[0] if 'symbol=' in url_s else 'BTCUSDT'
        d = {'symbol': sym, 'price': str(price)}
    elif 'longShortAccountRatio' in url_s or 'globalLong' in url_s:
        lsr = _LSR_NOW['v'] / 100
        d = [{'longShortRatio': str(lsr), 'longAccount': str(lsr),
              'shortAccount': str(1-lsr), 'timestamp': 0}]
    elif 'premiumIndex' in url_s or 'fundingRate' in url_s:
        d = [{'fundingRate': '0.0001', 'markPrice': str(price), 'symbol': 'BTCUSDT'}]
    elif 'klines' in url_s:
        # 返回空（data_cache注入优先）
        d = []
    else:
        d = {}
    class _R:
        def read(self): return json.dumps(d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return _R()

_ur_mod.urlopen = _offline_urlopen

# ══ STEP 2: 加载历史数据 ══
print('[达摩院] 加载历史K线数据...')
_kdata = {}
for sym in ['BTCUSDT', 'ETHUSDT']:
    _kdata[sym] = {}
    for tf in ['1h', '4h', '1d']:
        fname = DATA / f'{sym}_{tf}.json'
        with open(fname) as f:
            _kdata[sym][tf] = json.load(f)
    _kdata[sym]['15m'] = []  # 15m太大，暂不加载
    print(f'  {sym}: 1H={len(_kdata[sym]["1h"])} 4H={len(_kdata[sym]["4h"])} 1D={len(_kdata[sym]["1d"])}')

# ══ STEP 3: Patch regime_scorer ══
import brahma_brain.regime_scorer as _rs_mod

_RS_SYM = {'v': 'BTCUSDT'}
_RS_IDX = {'v': 10000}

def _rs_klines_offline(symbol, interval, limit=100):
    sym = symbol.upper()
    if not sym.endswith('USDT'): sym += 'USDT'
    if sym not in _kdata: sym = _RS_SYM['v']
    arr = _kdata[sym].get(interval, _kdata[sym]['1h'])
    n_arr = len(arr)
    idx = _RS_IDX['v']
    n_1h = len(_kdata[sym]['1h'])
    # 对齐到正确时间点
    if interval == '4h':
        mapped = min(idx * len(_kdata[sym]['4h']) // n_1h, len(_kdata[sym]['4h'])-1)
    elif interval == '1d':
        mapped = min(idx * len(_kdata[sym]['1d']) // n_1h, len(_kdata[sym]['1d'])-1)
    else:
        mapped = min(idx, n_arr-1)
    start = max(0, mapped - limit)
    raw = arr[start:mapped]
    # 转换为 regime_scorer 期望的 dict 格式 {o,h,l,c,v,ts}
    return [{'ts': int(k[0]), 'o': float(k[1]), 'h': float(k[2]),
             'l': float(k[3]), 'c': float(k[4]), 'v': float(k[5])} for k in raw]

_rs_mod._klines = _rs_klines_offline

# ══ STEP 4: Patch live_price_feed ══
import brahma_brain.live_price_feed as _lpf_mod

def _bulk_update_offline(symbols):
    for sym in symbols:
        price = _PRICE_NOW['v']
        _lpf_mod._prices[sym] = {
            'price': price, 'updated': time.time(),
            'change_pct': 0.0, 'volume': 1e9,
        }

if hasattr(_lpf_mod, 'bulk_update_from_api'):
    _lpf_mod.bulk_update_from_api = _bulk_update_offline

# ══ STEP 5: data_cache 注入函数 ══
from brahma_brain import data_cache as _dc

def _inject_klines(sym, idx):
    """把历史K线注入data_cache，让brahma_engine.analyze()读到正确数据"""
    kd = _kdata[sym]
    n_1h = len(kd['1h'])
    n_4h = len(kd['4h'])
    n_1d = len(kd['1d'])

    # 计算各周期对应的索引（严格无未来）
    i_1h = idx
    i_4h = min(idx * n_4h // n_1h, n_4h - 2)
    i_1d = min(idx * n_1d // n_1h, n_1d - 2)

    price = float(kd['1h'][min(i_1h-1, n_1h-1)][4])
    _PRICE_NOW['v'] = price
    _RS_SYM['v'] = sym
    _RS_IDX['v'] = i_1h

    # 注入各limit变体
    for lim in [50, 60, 100, 200, 300, 500]:
        _dc._cache_set(_dc._cache_key(sym, '1h', lim), kd['1h'][max(0,i_1h-lim):i_1h], 999999)
        _dc._cache_set(_dc._cache_key(sym, '4h', lim), kd['4h'][max(0,i_4h-lim):i_4h][-lim:], 999999)
        _dc._cache_set(_dc._cache_key(sym, '1d', lim), kd['1d'][max(0,i_1d-lim):i_1d][-lim:], 999999)

    # 无limit版本
    for tf, arr, i_end in [('1h', kd['1h'], i_1h), ('4h', kd['4h'], i_4h), ('1d', kd['1d'], i_1d)]:
        _dc._cache_set(_dc._cache_key(sym, tf), arr[max(0,i_end-500):i_end], 999999)

    # OFFLINE_MODE
    _dc.OFFLINE_MODE = True
    _dc.OFFLINE_CTX = {
        'ticker': {'lastPrice': str(price), 'price': str(price), 'priceChangePercent': '0.0'},
        'fr': 0.0001, 'oi': 1.2e10, 'lsr': 50.0,
    }
    _rs_mod._CACHE.clear()
    return price, i_1h, i_4h, i_1d

# ══ STEP 6: 主回测逻辑 ══
from brahma_brain.brahma_engine import analyze

FEE = 0.0008    # 0.04%×2 往返
LEV = 5.0
NAV_START = 10000.0
SAMPLE_STRIDE = 16  # 每16根1H bar采一次（≈ 每2/3天一次扫描）

DEAD_ZONES = {
    ('BEAR_TREND', 'LONG'), ('BULL_TREND', 'SHORT'),
    ('BEAR_EARLY', 'SHORT'), ('BULL_EARLY', 'LONG'),
    ('BEAR_RECOVERY', 'LONG'),
}
MIN_SCORE = 110  # 梵天有效信号门槛

def run_full_ability(sym):
    print(f'\n{"="*65}')
    print(f'  梵天全能力实测 · {sym} · 35维矩阵 · 无上帝视角')
    print(f'{"="*65}')

    kd = _kdata[sym]
    n_1h = len(kd['1h'])
    NAV = NAV_START
    trades = []
    open_pos = None
    errors = 0
    analyzed = 0
    t_total = 0.0
    signals_blocked = defaultdict(int)

    # 采样范围（留300根初始窗口）
    sample_range = range(300, n_1h - 50, SAMPLE_STRIDE)
    total_samples = len(sample_range)

    print(f'  采样: {total_samples}次 | stride={SAMPLE_STRIDE} | 预计{total_samples*0.6/60:.0f}分钟')

    for step, i in enumerate(sample_range):
        if step % 100 == 0 and step > 0:
            elapsed_so_far = time.time() - t_run_start
            eta = elapsed_so_far / step * (total_samples - step)
            print(f'  [{step}/{total_samples}] nav={NAV:.0f}U trades={len(trades)} eta={eta:.0f}s', flush=True)

        # ── 持仓检查（用最新价格） ──
        if open_pos:
            p = open_pos
            # 扫描从持仓起到现在的每根bar
            for j in range(p['open_i'] + 1, min(i+1, n_1h)):
                bh = float(kd['1h'][j][2])
                bl = float(kd['1h'][j][3])
                bc = float(kd['1h'][j][4])

                closed = False; cp = bc; cr = 'hold'

                if p['direction'] == 'LONG':
                    if bl <= p['sl']: cp=p['sl']; cr='SL'; closed=True
                    elif bh >= p['tp1'] and not p['tp1_hit']:
                        p['tp1_hit'] = True
                        pnl_p = (p['tp1'] - p['entry']) / p['entry'] - FEE
                        NAV = max(NAV + NAV * p['pos'] * 0.5 * LEV * pnl_p, NAV * 0.01)
                        p['pos'] *= 0.5; p['sl'] = p['entry']
                    if p['tp1_hit'] and not closed:
                        trail = bc * 0.985; p['sl'] = max(p['sl'], trail)
                        if bl <= p['sl']: cp=p['sl']; cr='TRAIL'; closed=True
                else:  # SHORT
                    if bh >= p['sl']: cp=p['sl']; cr='SL'; closed=True
                    elif bl <= p['tp1'] and not p['tp1_hit']:
                        p['tp1_hit'] = True
                        pnl_p = (p['entry'] - p['tp1']) / p['entry'] - FEE
                        NAV = max(NAV + NAV * p['pos'] * 0.5 * LEV * pnl_p, NAV * 0.01)
                        p['pos'] *= 0.5; p['sl'] = p['entry']
                    if p['tp1_hit'] and not closed:
                        trail = bc * 1.015; p['sl'] = min(p['sl'], trail)
                        if bh >= p['sl']: cp=p['sl']; cr='TRAIL'; closed=True

                # 超时（48H=48根bar）
                if not closed and (j - p['open_i']) >= 48:
                    cp = bc; cr = 'TIMEOUT'; closed = True

                if closed:
                    if p['direction'] == 'LONG': fpnl = (cp - p['entry']) / p['entry'] - FEE
                    else: fpnl = (p['entry'] - cp) / p['entry'] - FEE
                    NAV = max(NAV + NAV * p['pos'] * LEV * fpnl, NAV * 0.01)
                    ts_close = kd['1h'][j][0]
                    dt_close = datetime.utcfromtimestamp(ts_close/1000).strftime('%Y-%m-%d')
                    trades.append({
                        'dt_open': p['dt'], 'dt_close': dt_close,
                        'direction': p['direction'], 'entry': p['entry'],
                        'close': cp, 'pnl': fpnl * 100,
                        'reason': cr, 'regime': p['regime'],
                        'score': p['score'], 'grade': p['grade'],
                        'nav': NAV,
                    })
                    open_pos = None
                    break

        # 有持仓时不新建信号
        if open_pos: continue

        # ── 注入K线 + 调用analyze() ──
        try:
            price, i_1h, i_4h, i_1d = _inject_klines(sym, i)
            t0 = time.time()
            r = analyze(sym, deep=True)
            elapsed = time.time() - t0
            t_total += elapsed
            analyzed += 1
        except Exception as e:
            errors += 1
            continue

        score = r.get('score') or 0
        direction = r.get('direction') or ''
        regime = r.get('regime') or ''
        grade = r.get('grade') or 0
        action = r.get('action') or ''
        entry_lo = r.get('entry_lo') or r.get('entry') or price
        entry_hi = r.get('entry_hi') or entry_lo
        sl_val = r.get('sl') or 0
        tp1_val = r.get('tp1') or 0
        valid = r.get('valid') or r.get('valid_signal') or False

        # ── 信号过滤 ──
        if not direction or direction in ('NEUTRAL', '', 'HOLD'):
            signals_blocked['neutral'] += 1; continue
        if not valid and score < MIN_SCORE:
            signals_blocked['score_low'] += 1; continue
        if (regime, direction) in DEAD_ZONES:
            signals_blocked['dead'] += 1; continue
        if grade < 70:
            signals_blocked['grade'] += 1; continue

        # ── 构建持仓 ──
        # 使用analyze()输出的entry/sl/tp（梵天真实参数）
        entry_use = (entry_lo + entry_hi) / 2 if entry_hi > entry_lo else price

        if not sl_val or sl_val <= 0:
            # fallback SL
            sl_pct = 0.020
            sl_val = entry_use * (1 - sl_pct) if direction == 'LONG' else entry_use * (1 + sl_pct)
        if not tp1_val or tp1_val <= 0:
            sl_dist = abs(entry_use - sl_val) / entry_use
            tp1_val = entry_use * (1 + sl_dist * 1.5) if direction == 'LONG' else entry_use * (1 - sl_dist * 1.5)

        # 仓位：梵天体制乘数 × 5%基础
        from brahma_brain.brahma_engine import _REGIME_MUL_MAP
        regime_cfg = _REGIME_MUL_MAP if hasattr(sys.modules.get('brahma_brain.brahma_engine', type('',(),{})()), '_REGIME_MUL_MAP') else {}
        # 简化：用score分档
        if score >= 155: pos = 0.08
        elif score >= 140: pos = 0.06
        elif score >= 120: pos = 0.05
        else: pos = 0.03
        pos = min(pos, 0.10)

        ts_open = kd['1h'][i][0]
        dt_open = datetime.utcfromtimestamp(ts_open / 1000).strftime('%Y-%m-%d')

        open_pos = {
            'direction': direction, 'entry': entry_use,
            'sl': sl_val, 'tp1': tp1_val,
            'tp1_hit': False, 'pos': pos,
            'regime': regime, 'score': score, 'grade': grade,
            'open_i': i, 'dt': dt_open,
        }

    t_run_start  # just to keep reference

    # ── 统计 ──
    if not trades:
        print(f'  无信号交易 (analyzed={analyzed} errors={errors})')
        return {}

    n = len(trades); wins = sum(1 for t in trades if t['pnl'] > 0)
    wr = wins / n * 100
    aw = sum(t['pnl'] for t in trades if t['pnl'] > 0) / max(wins, 1)
    al = sum(t['pnl'] for t in trades if t['pnl'] <= 0) / max(n-wins, 1)
    pf = abs(aw) / abs(al) if al != 0 else 999
    ev = aw * wr/100 + al * (1-wr/100)
    tot = (NAV - NAV_START) / NAV_START * 100

    # MaxDD
    pk = NAV_START; mdd = 0
    for t in trades:
        pk = max(pk, t['nav']); mdd = max(mdd, (pk-t['nav'])/pk*100)

    # 体制分层
    reg_st = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        k = f"{t['regime']}_{t['direction']}"
        reg_st[k][0] += 1 if t['pnl'] > 0 else 0
        reg_st[k][1] += 1; reg_st[k][2] += t['pnl']

    # 按年
    by_year = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        yr = t['dt_open'][:4]
        by_year[yr][0] += 1 if t['pnl'] > 0 else 0
        by_year[yr][1] += 1; by_year[yr][2] += t['pnl']

    # 分数分层
    score_st = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        sc = t['score'] or 0
        if sc < 120: sb = '<120'
        elif sc < 130: sb = '120-129'
        elif sc < 140: sb = '130-139'
        elif sc < 150: sb = '140-149'
        else: sb = '150+'
        score_st[sb][0] += 1 if t['pnl'] > 0 else 0
        score_st[sb][1] += 1; score_st[sb][2] += t['pnl']

    reason_st = defaultdict(int)
    for t in trades: reason_st[t['reason']] += 1

    avg_ms = t_total / max(analyzed, 1) * 1000

    print(f'\n  ✅ n={n} | WR={wr:.1f}% | EV={ev:+.3f}%/笔 | PF={pf:.2f}')
    print(f'  总收益={tot:+.1f}% | MaxDD={mdd:.1f}% | NAV={NAV:.0f}U')
    print(f'  analyze={analyzed}次 avg={avg_ms:.0f}ms | errors={errors}')
    print(f'  过滤: neutral={signals_blocked["neutral"]} dead={signals_blocked["dead"]} ' +
          f'grade={signals_blocked["grade"]} score_low={signals_blocked["score_low"]}')

    print(f'\n  ── 体制×方向 ──')
    for k, (w, tot_r, pnl) in sorted(reg_st.items(), key=lambda x: -x[1][0]/max(1,x[1][1])):
        if tot_r < 3: continue
        wr_k = w/tot_r*100; ev_k = pnl/tot_r
        flag = '✅' if wr_k >= 52 else ('⚠️' if wr_k >= 47 else '❌')
        print(f'    {k:25s}: WR={wr_k:.1f}% n={tot_r:4d} EV={ev_k:+.3f}%/笔 {flag}')

    print(f'\n  ── 评分分层 ──')
    for sb in ['<120','120-129','130-139','140-149','150+']:
        if sb not in score_st: continue
        w, tot_r, pnl = score_st[sb]
        if tot_r < 2: continue
        wr_k = w/tot_r*100; ev_k = pnl/tot_r
        flag = '✅' if ev_k > 0 else '❌'
        print(f'    score{sb:8s}: WR={wr_k:.1f}% n={tot_r:3d} EV={ev_k:+.3f}%/笔 {flag}')

    print(f'\n  ── 按年度 ──')
    for yr, (w, tot_r, pnl) in sorted(by_year.items()):
        wr_y = w/tot_r*100; ev_y = pnl/tot_r
        flag = '✅' if ev_y > 0 else '❌'
        print(f'    {yr}: WR={wr_y:.1f}% n={tot_r:3d} EV={ev_y:+.3f}%/笔 {flag}')

    print(f'\n  ── 平仓原因 ──')
    for r_k, c in sorted(reason_st.items(), key=lambda x: -x[1]):
        print(f'    {r_k:10s}: {c:4d}笔 ({c/n*100:.1f}%)')

    return {'sym': sym, 'n': n, 'wr': wr, 'ev': ev, 'tot': tot, 'mdd': mdd,
            'pf': pf, 'nav': NAV, 'analyzed': analyzed}

# ══ MAIN ══
if __name__ == '__main__':
    results = {}
    for sym in ['BTCUSDT', 'ETHUSDT']:
        t_run_start = time.time()
        r = run_full_ability(sym)
        if r:
            r['wall_time'] = time.time() - t_run_start
            results[sym] = r

    print('\n' + '='*65)
    print('  ╔═ 梵天全能力实测 vs 达摩院基础实测 对比 ═╗')
    print('='*65)
    baselines = {
        'BTC_基础2维实测': (154, 48.7, -0.038, 7.9, 12.3, 1.02),
        'ETH_基础2维实测': (162, 46.9, -0.048, 11.1, 8.2, 1.09),
    }
    print(f'  {"版本":22s}|{"n":5s}|{"WR":6s}|{"EV/笔":8s}|{"收益":8s}|{"MaxDD":7s}|{"PF":5s}')
    print('  '+'-'*63)
    for k, (n, wr, ev, tot, mdd, pf) in baselines.items():
        print(f'  {k:22s}|{n:5d}|{wr:5.1f}%|{ev:+.4f}%|{tot:+6.1f}%|{mdd:5.1f}%|{pf:5.2f}')
    for sym, r in results.items():
        k = f'{sym}_梵天35维全能力'
        print(f'  {k:22s}|{r["n"]:5d}|{r["wr"]:5.1f}%|{r["ev"]:+.4f}%|{r["tot"]:+6.1f}%|{r["mdd"]:5.1f}%|{r["pf"]:5.2f}')
        print(f'  {"  → 耗时":22s}  {r["wall_time"]/60:.1f}分钟 analyze={r["analyzed"]}次')

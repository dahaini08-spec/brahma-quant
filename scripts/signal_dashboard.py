#!/usr/bin/env python3
"""
signal_dashboard.py v2.0 — 梵天信号仪表盘
设计院 · 达摩院 · 2026-06-06

架构：零AI纯Python，cron每30m执行
  - 三源合并：signal_queue / live_signal_log / pipeline_watch
  - 策略详情：入场区 / 止损 / T1 / T2 / R:R / 结构分
  - 表格化排版：紧凑列对齐
  - 推送规则：有新高分→推送；无新信号→HEARTBEAT_OK
"""
import sys, os, json, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

BASE       = Path('/root/.openclaw/workspace/trading-system')
STATE_FILE = Path('/root/.openclaw/workspace/trading-system/data/signal_dashboard_state.json')
ZONES_FILE = BASE / 'data' / 'price_zones.json'

SCORE_HQ   = 155   # 高质量门槛
SCORE_MID  = 140   # 有效门槛
COOLDOWN   = 1800  # 30min同标的不重复推送

# ══════════════════════════════════════════════════════════════
# 模块A: 状态管理
# ══════════════════════════════════════════════════════════════
def load_state():
    try: return json.load(open(STATE_FILE))
    except: return {'last_push': {}, 'last_run_ts': 0}

def save_state(s):
    s['last_run_ts'] = time.time()
    json.dump(s, open(STATE_FILE, 'w'))

# ══════════════════════════════════════════════════════════════
# 模块B: 数据读取（三源合并）
# ══════════════════════════════════════════════════════════════
def _load_price_zones():
    try: return json.load(open(ZONES_FILE))
    except: return {}

def load_source_queue(hours=24):
    """源A: signal_queue.jsonl — 猎手拉娜/on_demand候选信号"""
    results = {}
    cutoff  = time.time() - hours * 3600
    zones   = _load_price_zones()
    try:
        for raw in open(BASE / 'data' / 'signal_queue.jsonl'):
            if not raw.strip(): continue
            d = json.loads(raw)
            ts_str = str(d.get('ts', ''))
            try:
                ts_epoch = datetime.fromisoformat(
                    ts_str.replace('Z', '+00:00')).timestamp()
                if ts_epoch < cutoff: continue
            except: continue
            score = float(d.get('score', 0) or 0)
            if score < SCORE_MID: continue
            sym = d.get('symbol', '')
            di  = d.get('signal_dir', 'SHORT')
            key = f"{sym}_{di}"
            if key in results and score <= results[key]['score']:
                continue
            # 从price_zones补入场区参数
            z = zones.get(sym, {})
            results[key] = {
                'source':    'queue',
                'symbol':    sym,
                'direction': di,
                'score':     score,
                'ts':        ts_str,
                'regime':    d.get('regime', ''),
                'valid':     None,
                'status':    'candidate',
                'entry_lo':  z.get('last_entry_lo'),
                'entry_hi':  z.get('last_entry_hi'),
                'sl':        None,
                'tp1':       None,
                'tp2':       None,
                'sl_pct':    None,
                'rr1':       None,
                'structure_grade': None,
                'breakdown': {},
                'recent_wr': d.get('recent_wr'),
            }
    except: pass
    return results

def load_source_livelog(hours=24):
    """源B: live_signal_log.jsonl — brahma_brain完整分析结果"""
    results = {}
    cutoff  = time.time() - hours * 3600
    try:
        for raw in open(BASE / 'data' / 'live_signal_log.jsonl'):
            if not raw.strip(): continue
            d = json.loads(raw)
            ts_str = str(d.get('ts', '') or d.get('signal_id', ''))
            try:
                ts_epoch = datetime.fromisoformat(
                    ts_str.replace('Z', '+00:00')).timestamp()
                if ts_epoch < cutoff: continue
            except: continue
            score = float(d.get('score', 0) or 0)
            if score < SCORE_MID: continue
            sym = d.get('symbol', '')
            di  = d.get('signal_dir', '') or d.get('direction', '')
            key = f"{sym}_{di}"
            if key in results and score <= results[key]['score']:
                continue
            bd  = d.get('_breakdown', {}) or {}
            results[key] = {
                'source':    'live_log',
                'symbol':    sym,
                'direction': di,
                'score':     score,
                'ts':        ts_str,
                'regime':    d.get('regime', ''),
                'valid':     d.get('valid_signal') or d.get('valid'),
                'status':    'settled' if d.get('settled') else 'active',
                'entry_lo':  d.get('entry_lo'),
                'entry_hi':  d.get('entry_hi'),
                'sl':        d.get('stop_loss'),
                'stop_loss': d.get('stop_loss'),
                'tp1':       d.get('tp1'),
                'tp2':       d.get('tp2'),
                'sl_pct':    d.get('sl_pct'),
                'rr1':       d.get('rr1'),
                'structure_grade': d.get('structure_grade'),
                'outcome':   d.get('outcome'),
                'settled':   d.get('settled'),
                'breakdown': bd,
                'price':     d.get('price'),
                'rsi_1h':    d.get('rsi_1h'),
                'recent_wr': None,
            }
    except: pass
    return results

def load_source_pipeline():
    """源C: pipeline_watch.json — 进入流水线的信号"""
    results = {}
    try:
        pw = json.load(open(BASE / 'data' / 'pipeline_watch.json'))
        for k, d in pw.items():
            if d.get('status') in ('expired', 'done', 'cancelled'):
                continue
            score = float(d.get('score', 0) or 0)
            if score < SCORE_MID: continue
            sym = d.get('symbol', '')
            di  = d.get('direction', 'SHORT')
            key = f"{sym}_{di}"
            results[key] = {
                'source':    'pipeline',
                'symbol':    sym,
                'direction': di,
                'score':     score,
                'ts':        d.get('added_at', ''),
                'regime':    d.get('regime', ''),
                'valid':     True,
                'status':    d.get('status', 'watching'),
                'entry_lo':  d.get('entry_lo'),
                'entry_hi':  d.get('entry_hi'),
                'sl':        d.get('stop_loss'),
                'stop_loss': d.get('stop_loss'),
                'tp1':       d.get('tp1'),
                'tp2':       d.get('tp2'),
                'sl_pct':    None,
                'rr1':       2.5,
                'structure_grade': None,
                'breakdown': {},
                'trigger_price': d.get('trigger_price'),
                'recent_wr': None,
            }
    except: pass
    return results

def merge_sources():
    """三源合并，pipeline > live_log > queue 优先级
    同品种：优先取最新信号（ts最大），分数相同时取最近生成的
    """
    q  = load_source_queue()
    ll = load_source_livelog()
    pl = load_source_pipeline()
    merged = {}
    for d in [q, ll, pl]:
        for key, sig in d.items():
            if key not in merged:
                merged[key] = sig
            else:
                # 优先最新ts，ts相同时取更高分
                old_ts = merged[key].get('ts','')
                new_ts = sig.get('ts','')
                if new_ts > old_ts:
                    merged[key] = sig
                elif new_ts == old_ts and sig['score'] > merged[key]['score']:
                    merged[key] = sig
    # 额外：同品种只保留最新一条（防止多版本残留）
    by_sym = {}
    for key, sig in merged.items():
        sym = sig.get('symbol','')
        if sym not in by_sym or sig.get('ts','') > by_sym[sym][1].get('ts',''):
            by_sym[sym] = (key, sig)
    return [v for _, v in by_sym.values()]

# ══════════════════════════════════════════════════════════════
# 模块C: 信号分类
# ══════════════════════════════════════════════════════════════
def classify(signals):
    active, settled = [], []
    for s in signals:
        if s.get('settled') or s.get('status') == 'settled':
            settled.append(s)
        else:
            active.append(s)
    active.sort(key=lambda x: -x['score'])
    settled.sort(key=lambda x: -x['score'])
    return active, settled
def bj(ts_iso=None):
    if ts_iso:
        try:
            t = datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
            return (t + timedelta(hours=8)).strftime('%H:%M')
        except: return '--:--'
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%m/%d %H:%M')

def score_tag(s):
    s = int(s)
    if s >= 165: return '🔴神'
    if s >= 155: return '🟠强'
    if s >= 145: return '🟡中'
    return '🟢有'

def dir_tag(d):
    d = str(d).upper()
    return '空↓' if ('SHORT' in d or '做空' in str(d) or '空' in str(d)) else '多↑'

def src_tag(s):
    return {'queue': '候选', 'live_log': '分析', 'pipeline': '流水'}.get(s, '?')

def status_tag(s):
    m = {'watching':'监控','triggered':'触发','confirmed':'确认',
         'active':'活跃','candidate':'候选','settled':'结算'}
    return m.get(s, s or '-')

def fmt_price(v, decimals=4):
    if v is None: return '-'
    f = float(v)
    if f >= 10000: return f'{f:,.0f}'
    if f >= 1000:  return f'{f:,.1f}'
    if f >= 100:   return f'{f:.2f}'
    if f >= 10:    return f'{f:.3f}'
    if f >= 1:     return f'{f:.4f}'
    if f >= 0.1:   return f'{f:.4f}'
    if f >= 0.01:  return f'{f:.5f}'
    return f'{f:.6f}'
def strategy_line(s):
    """单行策略详情：入场/止损/T1/T2/RR/结构分"""
    parts = []
    elo = s.get('entry_lo')
    ehi = s.get('entry_hi')
    sl  = s.get('sl') or s.get('stop_loss')
    tp1 = s.get('tp1')
    tp2 = s.get('tp2')
    slp = s.get('sl_pct')
    rr  = s.get('rr1')
    sg  = s.get('structure_grade')

    if elo and ehi:
        parts.append(f'入场:{fmt_price(elo)}~{fmt_price(ehi)}')
    if sl:
        parts.append(f'SL:{fmt_price(sl)}' + (f'({float(slp):.1f}%)' if slp else ''))
    if tp1:
        parts.append(f'T1:{fmt_price(tp1)}')
    if tp2:
        parts.append(f'T2:{fmt_price(tp2)}')
    if rr:
        parts.append(f'RR:{float(rr):.1f}x')
    if sg is not None:
        parts.append(f'结构:{int(sg)}')
    return '  '.join(parts) if parts else '  （等待brahma完整分析）'

def breakdown_summary(s):
    """关键评分维度摘要（仅live_log来源有）"""
    bd = s.get('breakdown', {}) or {}
    if not bd: return None
    key_dims = ['趋势一致性','动量背离','SMC结构','量能验证','情绪/费率','多周期对齐']
    items = []
    for dim in key_dims:
        v = bd.get(dim)
        if v is not None and str(v) != '0':
            items.append(f'{dim}:{v}')
    regime_mult = bd.get('_regime_mult')
    if regime_mult and regime_mult != 1.0:
        items.append(f'体制系数:×{regime_mult}')
    return ' | '.join(items[:4]) if items else None

def fmt_table_header():
    return (
        '序  品种    分数  评级  方向  体制      状态  来源  时间BJ\n'
        + '-' * 60
    )

def fmt_table_row(idx, s):
    sym   = s['symbol'].replace('USDT', '').ljust(6)
    score = f"{s['score']:.0f}".rjust(4)
    tag   = score_tag(s['score'])
    di    = dir_tag(s.get('direction', ''))
    reg   = str(s.get('regime', '')).replace('BEAR_', 'B_').replace('BULL_', 'U_').replace('CHOP_', 'C_')[:8].ljust(8)
    st    = status_tag(s.get('status', ''))[:4].ljust(4)
    src   = src_tag(s.get('source', ''))
    t     = bj(s.get('ts', ''))
    row1  = f'{str(idx).rjust(2)}  {sym} {score}  {tag}  {di}  {reg}  {st}  {src}  {t}'
    row2  = f'    ↳ {strategy_line(s)}'
    bd    = breakdown_summary(s)
    if bd:
        return f'{row1}\n{row2}\n    📊 {bd}'
    return f'{row1}\n{row2}'

# ══════════════════════════════════════════════════════════════
# 模块E: 持仓快照
# ══════════════════════════════════════════════════════════════
def position_block():
    try:
        bs = json.load(open(BASE / 'data' / 'brahma_state.json'))
        positions = bs.get('positions', [])
        if not positions: return None
        lines = ['━━ 💼 持仓 ━━']
        for p in positions:
            sym   = p.get('symbol', '').replace('USDT', '')
            di    = dir_tag(p.get('direction', ''))
            entry = fmt_price(p.get('entry_price'))
            sl    = fmt_price(p.get('sl'))
            tp1   = fmt_price(p.get('tp1'))
            score = p.get('score', '-')
            regime= str(p.get('regime', '')).replace('BEAR_', 'B_')
            lines.append(
                f'  {sym} {di} 入{entry} SL{sl} T1{tp1}  '
                f'score={score} {regime}'
            )
        return '\n'.join(lines)
    except: return None

# ══════════════════════════════════════════════════════════════
# 模块F: 主程序
# ══════════════════════════════════════════════════════════════
import urllib.request

SCORE_ELITE = 170
BRAHMA_FILE = BASE / 'data' / 'brahma_state.json'

def _get_price(sym):
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=3)
        return float(json.loads(r.read())['price'])
    except: return 0.0

def _get_fr(sym):
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}', timeout=3)
        return float(json.loads(r.read()).get('lastFundingRate', 0)) * 100
    except: return 0.0

def age_str(ts_iso):
    try:
        ts = datetime.fromisoformat(ts_iso.replace('Z','+00:00')).timestamp()
        h = (time.time() - ts) / 3600
        return f'{h:.1f}H'
    except: return '?H'

def fmt_row_v3(idx, s, price, fr, ep_val):
    """双行信号卡片 — 手机宽度友好"""
    sym   = s['symbol'].replace('USDT','')
    score = float(s.get('score', 0))
    dtag  = '空↓' if 'SHORT' in str(s.get('signal_dir') or s.get('direction','')).upper() else '多↑'
    lo    = float(s.get('entry_lo') or 0)
    sl    = float(s.get('stop_loss') or s.get('sl') or 0)
    tp1   = float(s.get('tp1') or 0)

    gap = (lo - price) / price * 100 if price and lo else 0
    gap_icon = '⚡' if abs(gap) <= 0.3 else ('📍' if 0 < gap <= 1.5 else ('🔴' if gap > 1.5 else '⬇'))

    # R:R
    rr_s = f'R:R={abs(tp1-lo)/abs(sl-lo):.1f}' if lo and sl and tp1 and abs(sl-lo)>0 else ''
    sl_s  = f'SL{abs(sl-lo)/lo*100:.1f}%' if sl and lo else ''
    fr_s  = f'FR{fr:+.3f}%' if fr else ''

    # 行1：核心数据（不超过28字符）
    px_s  = f'${fmt_price(price)}' if price else '--'
    lo_s  = f'${fmt_price(lo)}'   if lo    else '--'
    line1 = f'{idx}. {sym} {dtag}  现{px_s}  入{lo_s}'

    # 行2：入场要素（止损+R:R+FR）
    line2_parts = [f'gap{gap:+.2f}%{gap_icon}', f'{score:.0f}分']
    if sl_s:  line2_parts.append(sl_s)
    if rr_s:  line2_parts.append(rr_s)
    if fr_s:  line2_parts.append(fr_s)
    line2 = '   ' + '  '.join(line2_parts)

    # 行3：止损价+目标价（只在有数据时显示）
    line3 = ''
    if sl and tp1:
        line3 = f'   止损${fmt_price(sl)}  目标${fmt_price(tp1)}'

    if line3:
        return line1 + '\n' + line2 + '\n' + line3
    return line1 + '\n' + line2


def decision_card(active, prices, brahma):
    """决策卡：前3行核心信息"""
    nav     = float(brahma.get('nav', 0))
    regime  = brahma.get('regime', 'UNKNOWN')
    pos     = brahma.get('positions', [])
    gate3   = len(pos)
    gate_s  = f'Gate3:{gate3}/1占用' if gate3 else 'Gate3:空闲'

    # 体制emoji
    r_emoji = '🐻' if 'BEAR' in regime else ('🟢' if 'BULL' in regime else '⚡')

    # BTC预警距离
    try:
        btcp = _get_price('BTCUSDT')
        warn = 63500
        dist = (warn - btcp) / btcp * 100
        warn_s = f'BTC${btcp:,.0f} 预警{dist:+.1f}%'
    except: warn_s = ''

    _now_bj = datetime.now(timezone(timedelta(hours=8))).strftime("%m/%d %H:%M")
    # 拆成两行，避免手机折行
    line1 = f'📊 梵天  {_now_bj}BJ  {r_emoji}{regime}'
    line1b = f'💰 NAV${nav:.1f}  {gate_s}'
    line2 = ''
    # 持仓PNL
    if pos:
        p = pos[0]
        sym  = p.get('symbol','').replace('USDT','')
        entr = float(p.get('entry_price') or p.get('entry',0))
        sl   = float(p.get('sl') or p.get('stop_loss',0))
        tp1  = float(p.get('tp1',0))
        pr   = prices.get(p.get('symbol',''), 0)
        if pr and entr:
            pnl = (entr-pr)/entr*100
            tp1_diff = (pr-tp1)/pr*100 if tp1 else 0
            line2 = f'💼 {sym}空↓  入${entr:.5f}→现${pr:.5f}  PNL{pnl:+.2f}%  SL${sl:.5f}  T1差{tp1_diff:+.2f}%'

    # 下一单建议（EP最高且gate3有空时）
    # 今日市场背景
    try:
        btcp  = _get_price('BTCUSDT')
        ethp  = _get_price('ETHUSDT')
        btc24 = json.loads(urllib.request.urlopen(
            urllib.request.Request('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT',
            headers={'User-Agent':'x'}), timeout=4).read())
        eth24 = json.loads(urllib.request.urlopen(
            urllib.request.Request('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=ETHUSDT',
            headers={'User-Agent':'x'}), timeout=4).read())
        btc_chg = float(btc24['priceChangePercent'])
        eth_chg = float(eth24['priceChangePercent'])
        if btc_chg > 2 or eth_chg > 2:
            mkt_bias = f'⚠️ 今日反弹BTC{btc_chg:+.1f}% ETH{eth_chg:+.1f}%→短期多势，入场等高位'
        elif btc_chg < -2:
            mkt_bias = f'✅ 今日下跌BTC{btc_chg:+.1f}%→空头顺势，信号可靠性↑'
        else:
            mkt_bias = f'今日BTC{btc_chg:+.1f}% ETH{eth_chg:+.1f}%  震荡等方向'
    except:
        mkt_bias = ''

    # 最优单推荐
    line3 = ''
    if gate3 == 0 and active:
        best = active[0]
        bsym = best['symbol'].replace('USDT','')
        bep  = best.get('_ep_val', float(best.get('score',0)))
        blo  = float(best.get('entry_lo') or 0)
        bsl  = float(best.get('stop_loss') or best.get('sl') or 0)
        btp1 = float(best.get('tp1') or 0)
        bgap = best.get('_gap', 0)
        # R:R
        rr_txt = ''
        if blo and bsl and btp1:
            risk = abs(bsl-blo); rew = abs(btp1-blo)
            if risk > 0: rr_txt = f' R:R={rew/risk:.1f}'
        # 最大亏损
        loss_txt = ''
        if bsl and blo and nav > 0:
            loss_u = nav * 0.02
            loss_txt = f' 最大亏损≈${loss_u:.1f}'
        line3 = (f'⚡ 首选: {bsym} EP{bep:.0f}  入场差{bgap:+.2f}%{rr_txt}{loss_txt}  {warn_s}')
    elif pos:
        cands = [s for s in active if s['symbol'] != pos[0].get('symbol','')]
        if cands:
            nxt = cands[0]
            nsym = nxt['symbol'].replace('USDT','')
            nep  = nxt.get('_ep_val', float(nxt.get('score',0)))
            line3 = f'🔜 T1后下一单: {nsym} EP{nep:.0f}  {warn_s}'

    # Paper进度（支持Phase0重置显示历史数据）
    try:
        wp = json.load(open(str(BASE/'data'/'wuqu_paper_state.json')))
        wn  = wp.get('wins',0) + wp.get('losses',0)
        wwr = wp.get('wins',0)/wn*100 if wn else 0
        # Phase0重置后用legacy数据显示历史成绩
        if wp.get('phase') == 'phase0_reset' and wn == 0:
            leg_n  = wp.get('reset_legacy_count', 0)
            leg_wr = wp.get('reset_legacy_wr', 0) * 100
            target = wp.get('target_n', 200)
            paper_s = f'Paper 新周期0/{target} | 历史{leg_n}条WR={leg_wr:.0f}% | 三铁律✅'
        else:
            target = wp.get('target_n', 200)
            paper_s = f'Paper {wn}/{target} WR={wwr:.0f}% | 三铁律✅'
    except:
        paper_s = ''

    line4 = f'📌 {mkt_bias}' if mkt_bias else ''
    line5 = f'🔧 {paper_s}' if paper_s else ''

    return '\n'.join(filter(None, [line1, line1b, line2, line3, line4, line5]))


def main():
    state = load_state()
    now   = time.time()

    all_signals     = merge_sources()
    active, settled = classify(all_signals)

    # ── 拉取实时价格 ──────────────────────────────
    syms   = list({s['symbol'] for s in active[:12]})
    prices = {}
    for sym in syms:
        prices[sym] = _get_price(sym)

    # ── EP Score排名 ──────────────────────────────
    sys.path.insert(0, str(BASE / 'scripts'))
    try:
        from ep_score import calc_ep
        for s in active:
            sym = s['symbol']
            p   = prices.get(sym, 0)
            fr  = _get_fr(sym)
            ep  = calc_ep(s, price=p, fr=fr)
            s['_ep_val'] = ep['ep']
            s['_gap']    = ep['gap_pct']
            s['_fr']     = fr
        active.sort(key=lambda x: -x.get('_ep_val', x['score']))
    except Exception as e:
        for s in active:
            s['_ep_val'] = float(s.get('score',0))
            lo = float(s.get('entry_lo') or 0)
            p  = prices.get(s['symbol'], 0)
            s['_gap'] = (lo-p)/p*100 if p and lo else 99

    # ── 有变化才推逻辑 ────────────────────────────
    last_gaps = state.get('last_gaps', {})
    brahma    = json.load(open(BRAHMA_FILE)) if BRAHMA_FILE.exists() else {}
    has_pos   = len(brahma.get('positions',[])) > 0

    changed = False
    new_gaps = {}
    for s in active:
        sym = s['symbol']
        g   = round(s.get('_gap', 99), 2)
        new_gaps[sym] = g
        prev = last_gaps.get(sym, 99)
        if abs(g - prev) >= 0.3:
            changed = True

    if not changed and not has_pos and active:
        print('HEARTBEAT_OK')
        return

    # ── 构建输出 ──────────────────────────────────
    out = []
    out.append(decision_card(active, prices, brahma))
    out.append('')

    hq  = [s for s in active if s['score'] >= SCORE_HQ]
    mid = [s for s in active if SCORE_MID <= s['score'] < SCORE_HQ]

    if not active:
        print('HEARTBEAT_OK')
        return

    if hq:
        out.append('🔥 高质量信号（≥155分）')
        for i, s in enumerate(hq[:6], 1):
            p  = prices.get(s['symbol'], 0)
            fr = s.get('_fr', 0)
            ep = s.get('_ep_val', s['score'])
            out.append(fmt_row_v3(i, s, p, fr, ep))
        out.append('')

    if mid:
        out.append('✅ 有效信号（140~154分）')
        for i, s in enumerate(mid[:4], 1):
            p  = prices.get(s['symbol'], 0)
            fr = s.get('_fr', 0)
            ep = s.get('_ep_val', s['score'])
            out.append(fmt_row_v3(i, s, p, fr, ep))
        out.append('')

    if settled:
        out.append('━━ ❌ 结算/失效 ━━')
        for s in settled[:3]:
            sym = s['symbol'].replace('USDT','')
            out.append(f'  {sym} {s["score"]:.0f}分 → {s.get("outcome","?")}')
        out.append('')

    # ── 清算数据模块 ──────────────────────────────
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE / 'scripts'))
        from liquidation_module import fmt_liq_block
        # 主要品种 + 有持仓的品种
        liq_syms = list({'BTCUSDT','ETHUSDT'} | {p['symbol'] for p in brahma.get('positions',[]) if p.get('status')=='OPEN'})
        # 收集当前信号入场区供预警
        entry_zones = {s['symbol']: float(s.get('entry_lo') or 0) for s in active if s.get('entry_lo')}
        out.append(fmt_liq_block(liq_syms, entry_zones))
    except Exception as _e:
        out.append(f'⚡ 清算数据 暂不可用')

    print('\n'.join(out))

    state['last_gaps']      = new_gaps
    state['last_full_push'] = now
    save_state(state)


if __name__ == '__main__':
    main()

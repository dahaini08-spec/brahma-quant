#!/usr/bin/env python3
"""
paper_engine.py — 梵天纸面交易唯一调度器
2026-08-26 苏摩111封印

架构原则：单一漏斗
  所有信号源 → signal_queue.jsonl → paper_engine（唯一决策+开单+结算）

每5min由cron触发，完成：
  1. 读signal_queue（去重+去过期）
  2. BBW/RSI/score豁免判断
  3. 战场预判 calc_zones
  4. 35维评分
  5. AI议会裁决
  6. 纸面开单
  7. 结算检查（PENDING→FILLED→CLOSED）
  8. 清空已处理信号 + IC反馈写入
"""

import sys, os, json, time, logging
from pathlib import Path
from datetime import datetime, timezone

ROOT  = Path(__file__).parent.parent
BRAIN = ROOT / 'brahma_brain'
DATA  = ROOT / 'data'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BRAIN))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [engine] %(message)s')
_log = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────
SIGNAL_QUEUE   = DATA / 'signal_queue.jsonl'
PAPER_ORDERS   = DATA / 'paper_orders.jsonl'
PAPER_ACCOUNT  = DATA / 'paper_account.json'
ENGINE_LOG     = DATA / 'paper_engine_log.jsonl'
DEDUP_FILE     = DATA / 'paper_engine_dedup.json'

QUEUE_TTL_S    = 3600 * 2    # 队列信号2H过期
DEDUP_TTL_S    = 3600 * 4    # 同标的同方向4H内不重复开单
MAX_OPEN_POS   = 10          # 最多同时持仓
PAPER_NAV      = 100000      # 纸面NAV

# 评分门槛
SCORE_MIN      = 80          # 普通最低分
SCORE_BBW_MIN  = 70          # BBW<3%时的最低分（P1豁免）
BBW_TIGHT      = 3.0         # BBW压缩阈值%
RR_MIN         = 1.5         # 最低RR

# 仓位规则
SIZE_MAJOR = 0.05   # BTC/ETH
SIZE_ALT   = 0.03   # 其他
LEV_MAJOR  = 100
LEV_ALT    = 20
MAJOR_SYMS = {'BTCUSDT', 'ETHUSDT'}

# 死穴
DEAD_LONG  = {'BEAR_TREND'}
DEAD_SHORT = {'BULL_TREND'}

# ── 工具 ─────────────────────────────────────────────────────────────────

def _push(msg: str):
    try:
        import subprocess
        subprocess.Popen([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
            '--message', msg
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except: pass


def _load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try: return json.loads(DEDUP_FILE.read_text())
        except: pass
    return {}


def _is_dedup(symbol: str, side: str) -> bool:
    d = _load_dedup()
    return (time.time() - d.get(f'{symbol}:{side}', 0)) < DEDUP_TTL_S


def _mark_dedup(symbol: str, side: str):
    d = _load_dedup()
    d[f'{symbol}:{side}'] = time.time()
    DEDUP_FILE.write_text(json.dumps(d))


def _get_nav() -> float:
    try:
        if PAPER_ACCOUNT.exists():
            return json.loads(PAPER_ACCOUNT.read_text()).get('nav_current', PAPER_NAV)
    except: pass
    return PAPER_NAV


def _count_open() -> int:
    if not PAPER_ORDERS.exists(): return 0
    count = 0
    for line in PAPER_ORDERS.read_text().strip().split('\n'):
        if not line: continue
        try:
            if json.loads(line).get('status') == 'FILLED': count += 1
        except: pass
    return count


def _get_bbw_rsi(symbol: str) -> tuple:
    """返回 (bbw%, rsi_1h)"""
    import urllib.request
    try:
        kl = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=25',
            timeout=4).read())
        closes = [float(k[4]) for k in kl]
        sma = sum(closes[-20:])/20
        std = (sum((c-sma)**2 for c in closes[-20:])/20)**0.5
        bbw = (4*std)/sma*100
        g=[max(closes[i]-closes[i-1],0) for i in range(1,15)]
        l=[max(closes[i-1]-closes[i],0) for i in range(1,15)]
        ag=sum(g)/14; al=sum(l)/14
        rsi = 100-100/(1+ag/al) if al>0 else 50.0
        return round(bbw,2), round(rsi,1)
    except:
        return 999.0, 50.0


# ── Step1: 读队列 ─────────────────────────────────────────────────────────

def read_queue() -> list:
    """读取signal_queue，去重+去过期，返回待处理列表"""
    if not SIGNAL_QUEUE.exists():
        return []
    now = time.time()
    seen = set()
    fresh = []
    for line in SIGNAL_QUEUE.read_text().strip().split('\n'):
        if not line: continue
        try:
            sig = json.loads(line)
            sym = sig.get('symbol', '')
            if not sym: continue
            if now - sig.get('ts', 0) > QUEUE_TTL_S: continue  # 过期
            if sym in seen: continue  # 去重（同标的取最新）
            seen.add(sym)
            fresh.append(sig)
        except: pass
    _log.info(f'队列读取: {len(fresh)}个待处理信号')
    return fresh


def clear_queue(processed_symbols: set):
    """清除已处理的信号"""
    if not SIGNAL_QUEUE.exists(): return
    remaining = []
    for line in SIGNAL_QUEUE.read_text().strip().split('\n'):
        if not line: continue
        try:
            sig = json.loads(line)
            if sig.get('symbol') not in processed_symbols:
                remaining.append(line)
        except: pass
    SIGNAL_QUEUE.write_text('\n'.join(remaining) + '\n' if remaining else '')


# ── Step2~6: 单标的决策流水线 ──────────────────────────────────────────────

def process_one(symbol: str, source: str = 'queue') -> dict:
    """全流水线：BBW/RSI → 35维 → 议会 → 战场 → 开单"""
    import urllib.request
    result = {'symbol': symbol, 'action': 'SKIP', 'reason': '', 'source': source, 'orders': []}

    # 持仓上限检查
    if _count_open() >= MAX_OPEN_POS:
        result['reason'] = 'max_positions'
        return result

    # Step1: 实时价格
    try:
        t = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}', timeout=5).read())
        price = float(t['lastPrice'])
        result['price'] = price
    except Exception as e:
        result['reason'] = f'price_fail: {e}'
        return result

    # Step2: BBW/RSI（豁免计算先于35维，节省token）
    bbw, rsi = _get_bbw_rsi(symbol)
    result['bbw'] = bbw
    result['rsi_1h'] = rsi

    # Step3: 35维评分
    try:
        import kronos_bridge as kb; kb._cache = {}
        from brahma_core import analyze
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            _fut = _ex.submit(analyze, symbol)
            try:
                r = _fut.result(timeout=45)  # 单标的最多45秒
            except concurrent.futures.TimeoutError:
                result['reason'] = 'analyze_timeout_45s'
                return result
        score     = r.get('score', 0)
        regime    = r.get('regime', '')
        direction = r.get('direction') or r.get('signal_dir') or 'LONG'
        blocked   = r.get('blocked', False)
    except Exception as e:
        result['reason'] = f'analyze_fail: {e}'
        return result

    result.update({'score': score, 'regime': regime, 'direction': direction})

    # 死穴 + blocked检查
    if blocked:
        result['reason'] = f'blocked:{r.get("block_reason","")}'
        return result
    if direction == 'LONG'  and regime in DEAD_LONG:
        result['reason'] = f'dead_hole:{regime}×LONG'
        return result
    if direction == 'SHORT' and regime in DEAD_SHORT:
        result['reason'] = f'dead_hole:{regime}×SHORT'
        return result

    # 豁免判断
    p1 = bbw < BBW_TIGHT and score >= SCORE_BBW_MIN   # P1: BBW压缩
    p2 = (rsi < 20 and direction == 'LONG') or \
         (rsi > 80 and direction == 'SHORT')           # P2: RSI极值

    if not p1 and not p2 and score < SCORE_MIN:
        result['reason'] = f'score={score:.1f}<{SCORE_MIN} bbw={bbw:.1f}% rsi={rsi:.1f}'
        return result

    exempt_by = ('P1_BBW' if p1 else '') + ('P2_RSI' if p2 else '')
    result['exempt'] = exempt_by

    # Step4: AI议会
    council_adj = 0
    try:
        from llm_council import council_verdict
        v = council_verdict({'score':score,'regime':regime,'grade':r.get('grade',100)},
                            direction, regime, score)
        if v.get('action') == 'HARD_BLOCK':
            result['reason'] = 'council:HARD_BLOCK'
            return result
        council_adj = v.get('council_score', 0)
    except: pass
    score_adj = score + council_adj

    # Step5: 战场预判
    try:
        from price_zone_engine import calc_zones
        z = calc_zones(symbol, force_refresh=True)
    except Exception as e:
        result['reason'] = f'zone_fail:{e}'
        return result

    # Step6: 开单
    opened = []
    h = z.get('high_short', {})
    l = z.get('low_long', {})

    for side, zone in [('SHORT', h), ('LONG', l)]:
        if not zone.get('low'): continue
        if zone.get('rr', 0) < RR_MIN: continue
        if _is_dedup(symbol, side): continue

        nav   = _get_nav()
        is_mj = symbol in MAJOR_SYMS
        sp    = SIZE_MAJOR if is_mj else SIZE_ALT
        lev   = LEV_MAJOR  if is_mj else LEV_ALT
        notional = nav * sp * lev
        margin   = nav * sp
        entry    = (zone['low'] + zone['high']) / 2
        prec     = {'BTCUSDT':3,'ETHUSDT':2,'BNBUSDT':2}.get(symbol, 1)
        qty      = round(notional/entry, prec) or round(notional/entry, prec+2)
        sl_dist  = abs(entry - zone['sl']) / entry
        max_loss = margin * sl_dist * lev

        rec = {
            'id': f"PE-{int(time.time())}-{symbol}-{side}",
            'symbol': symbol, 'side': side,
            'entry': round(entry,4), 'sl': zone['sl'], 'tp': zone['tp'],
            'rr': zone['rr'], 'qty': qty,
            'notional': round(notional,2), 'margin': round(margin,2),
            'lev': lev, 'nav': nav, 'max_loss': round(max_loss,2),
            'score': round(score_adj,1), 'regime': regime,
            'exempt': exempt_by, 'source': source,
            'status': 'PENDING', 'created_at': int(time.time()),
            'filled_at': None, 'fill_price': None, 'close_price': None, 'pnl': None,
        }
        with open(PAPER_ORDERS, 'a') as f:
            f.write(json.dumps(rec) + '\n')
        _mark_dedup(symbol, side)
        opened.append(rec)
        _log.info(f'[OPEN] {symbol} {side} @${entry:,} RR={zone["rr"]} score={score_adj:.1f} {exempt_by}')

    if opened:
        result['action'] = 'PAPER_OPEN'
        result['orders'] = opened
    else:
        result['reason'] = f'no_valid_zone rr_h={h.get("rr","?")} rr_l={l.get("rr","?")}'

    return result


# ── Step7: 结算检查 ───────────────────────────────────────────────────────

def settle_orders() -> list:
    """检查PENDING→FILLED，FILLED→CLOSED"""
    import urllib.request
    if not PAPER_ORDERS.exists(): return []

    lines = PAPER_ORDERS.read_text().strip().split('\n')
    updated = []
    settled = []
    ts = int(time.time())

    for line in lines:
        if not line: continue
        try: o = json.loads(line)
        except: updated.append(line); continue

        status = o.get('status', 'PENDING')

        if status == 'PENDING':
            try:
                t = json.loads(urllib.request.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={o["symbol"]}',
                    timeout=3).read())
                price = float(t['price'])
                entry = o['entry']
                if (o['side']=='LONG' and price <= entry * 1.005) or \
                   (o['side']=='SHORT' and price >= entry * 0.995):
                    o['status'] = 'FILLED'
                    o['filled_at'] = ts
                    o['fill_price'] = price
                    _log.info(f'[FILLED] {o["symbol"]} {o["side"]} @${price}')
            except: pass

        elif status == 'FILLED':
            try:
                t = json.loads(urllib.request.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={o["symbol"]}',
                    timeout=3).read())
                price = float(t['price'])
                fill  = o.get('fill_price', o['entry'])
                hit_tp = (o['side']=='LONG' and price >= o['tp']) or \
                         (o['side']=='SHORT' and price <= o['tp'])
                hit_sl = (o['side']=='LONG' and price <= o['sl']) or \
                         (o['side']=='SHORT' and price >= o['sl'])
                if hit_tp or hit_sl:
                    cp = o['tp'] if hit_tp else o['sl']
                    pnl = (cp-fill)/fill*o['notional'] if o['side']=='LONG' \
                          else (fill-cp)/fill*o['notional']
                    o.update({'status':'CLOSED','close_price':cp,'close_at':ts,
                              'pnl':round(pnl,2),'close_reason':'TP' if hit_tp else 'SL'})
                    settled.append(o)
                    _log.info(f'[CLOSED] {o["symbol"]} {o["side"]} PnL=${pnl:.2f} {o["close_reason"]}')
            except: pass

        updated.append(json.dumps(o))

    PAPER_ORDERS.write_text('\n'.join(updated) + '\n')

    # 更新NAV
    if settled:
        total_pnl = sum(o['pnl'] for o in settled)
        try:
            acc = json.loads(PAPER_ACCOUNT.read_text()) if PAPER_ACCOUNT.exists() else {}
            acc['nav_current'] = acc.get('nav_current', PAPER_NAV) + total_pnl
            acc['realized_pnl'] = acc.get('realized_pnl', 0) + total_pnl
            acc['updated_at'] = ts
            PAPER_ACCOUNT.write_text(json.dumps(acc, indent=2))
        except: pass

    return settled


# ── 主循环 ────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    _log.info('=== paper_engine 启动 ===')

    # Step1: 读队列
    signals = read_queue()

    # 若队列为空，仍做BBW扫描（主动发现）
    if not signals:
        _log.info('队列为空，执行BBW主动扫描')
        import urllib.request, json as _json
        # 从candidates + BTC/ETH加入扫描
        scan_syms = ['BTCUSDT', 'ETHUSDT']
        cf = DATA / 'candidates.json'
        if cf.exists():
            try:
                c = _json.loads(cf.read_text())
                tops = (c if isinstance(c,list) else c.get('symbols',[]))[:10]
                scan_syms += [s if isinstance(s,str) else s.get('symbol','') for s in tops]
            except: pass
        for sym in list(dict.fromkeys(scan_syms)):
            if not sym: continue
            bbw, rsi = _get_bbw_rsi(sym)
            if bbw < 5.0 or rsi < 20 or rsi > 80:
                signals.append({'symbol': sym, 'ts': time.time(), 'source': 'bbw_auto'})

    # Step2~6: 处理每个信号
    results = []
    processed = set()
    opened_total = 0
    opened_list = []

    for sig in signals:
        sym = sig.get('symbol', '')
        if not sym: continue
        source = sig.get('source', 'queue')
        try:
            r = process_one(sym, source)
            results.append(r)
            processed.add(sym)
            if r['action'] == 'PAPER_OPEN':
                opened_total += len(r['orders'])
                for o in r['orders']:
                    opened_list.append(f"{'🟢' if o['side']=='LONG' else '🔴'} {sym} {o['side']} @${o['entry']:,} RR={o['rr']}")
            else:
                _log.info(f'SKIP {sym}: {r["reason"]}')
        except Exception as e:
            _log.error(f'process_one {sym}: {e}')

    # Step7: 结算
    settled = settle_orders()

    # Step8: 清空已处理信号
    clear_queue(processed)

    # 写引擎日志
    elapsed = round(time.time() - t0, 1)
    log_entry = {
        'ts': int(time.time()),
        'signals_in': len(signals),
        'opened': opened_total,
        'settled': len(settled),
        'elapsed_s': elapsed,
    }
    with open(ENGINE_LOG, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')

    # 推送摘要（有开单或有结算时才推）
    if opened_total > 0 or settled:
        msg_lines = [f'🔱 梵天纸面引擎 | {datetime.now(timezone.utc).strftime("%H:%M UTC")}']
        if opened_total > 0:
            msg_lines += [''] + ['📋 新开单：'] + opened_list
        if settled:
            msg_lines.append('')
            msg_lines.append('📊 结算：')
            total_pnl = 0
            for o in settled:
                emoji = '✅' if o['pnl'] > 0 else '❌'
                msg_lines.append(f"{emoji} {o['symbol']} {o['side']} {o['close_reason']} PnL=${o['pnl']:+,.2f}")
                total_pnl += o['pnl']
            msg_lines.append(f'合计: ${total_pnl:+,.2f} | NAV: ${_get_nav():,.0f}')
        _push('\n'.join(msg_lines))

    print(f'完成: 信号={len(signals)} 开单={opened_total} 结算={len(settled)} 耗时={elapsed}s')


if __name__ == '__main__':
    main()

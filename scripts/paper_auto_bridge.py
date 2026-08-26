#!/usr/bin/env python3
"""
paper_auto_bridge.py — 梵天纸面自动开单桥接器
2026-08-26 苏摩111封印

职责：作为全自动纸面闭环的统一入口
  信号来源 → 战场预判 → 议会裁决 → 纸面开单 → 结算追踪 → IC反馈

调用方式：
  python3 paper_auto_bridge.py --source candidates   # 候选池→战场→纸面
  python3 paper_auto_bridge.py --source oi            # OI异常→纸面
  python3 paper_auto_bridge.py --source zone          # 战场预判→纸面
  python3 paper_auto_bridge.py --source all           # 全量扫描

门槛（纸面专属，低于实盘）:
  score ≥ 80（实盘138，纸面80）
  RR ≥ 1.5
  AI议会 ≠ HARD_BLOCK
  体制不在死穴列表
"""

import sys, os, json, time, argparse, logging
from pathlib import Path
from datetime import datetime, timezone

# ── 路径 ────────────────────────────────────────────────────────────────
ROOT   = Path(__file__).parent.parent
BRAIN  = ROOT / 'brahma_brain'
DATA   = ROOT / 'data'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BRAIN))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [bridge] %(message)s')
_log = logging.getLogger(__name__)

# ── 门槛常量 ────────────────────────────────────────────────────────────
PAPER_SCORE_MIN  = 80       # 纸面开单最低分（实盘=138）
PAPER_RR_MIN     = 1.5      # 最低RR
PAPER_NAV        = 100000   # 纸面NAV
PAPER_SIZE_BTC   = 0.05     # BTC/ETH仓位比
PAPER_SIZE_ALT   = 0.03     # 山寨仓位比
PAPER_LEV_MAJOR  = 100      # BTC/ETH杠杆
PAPER_LEV_ALT    = 20       # 山寨杠杆
MAX_OPEN_POS     = 10       # 最多同时持仓数
DEDUP_WINDOW_S   = 3600 * 4 # 同标的4H内不重复开单

# 死穴体制（禁止做多）
DEAD_HOLE_LONG  = {'BEAR_TREND'}
# 死穴体制（禁止做空）
DEAD_HOLE_SHORT = {'BULL_TREND'}

PAPER_ORDERS_FILE = DATA / 'paper_orders.jsonl'
PAPER_ACCOUNT_FILE= DATA / 'paper_account.json'
PAPER_BRIDGE_LOG  = DATA / 'paper_bridge_log.jsonl'
PAPER_DEDUP_FILE  = DATA / 'paper_bridge_dedup.json'

# ── 工具函数 ────────────────────────────────────────────────────────────

def _push(msg: str):
    """推送到Jarvis线程"""
    try:
        import subprocess
        subprocess.Popen([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
            '--message', msg
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _log.warning(f'push失败: {e}')


def _load_dedup() -> dict:
    if PAPER_DEDUP_FILE.exists():
        return json.loads(PAPER_DEDUP_FILE.read_text())
    return {}


def _save_dedup(d: dict):
    PAPER_DEDUP_FILE.write_text(json.dumps(d))


def _is_dedup(symbol: str, direction: str) -> bool:
    """同标的同方向4H内不重复开单"""
    d = _load_dedup()
    key = f'{symbol}:{direction}'
    last = d.get(key, 0)
    return (time.time() - last) < DEDUP_WINDOW_S


def _mark_dedup(symbol: str, direction: str):
    d = _load_dedup()
    d[f'{symbol}:{direction}'] = time.time()
    _save_dedup(d)


def _count_open_positions() -> int:
    if not PAPER_ORDERS_FILE.exists():
        return 0
    count = 0
    for line in PAPER_ORDERS_FILE.read_text().strip().split('\n'):
        if not line: continue
        try:
            r = json.loads(line)
            if r.get('status') == 'FILLED': count += 1
        except: pass
    return count


def _get_paper_nav() -> float:
    try:
        from paper_trader import get_paper_nav
        return get_paper_nav()
    except:
        if PAPER_ACCOUNT_FILE.exists():
            return json.loads(PAPER_ACCOUNT_FILE.read_text()).get('nav_current', PAPER_NAV)
        return PAPER_NAV


def _open_paper_order(symbol: str, direction: str, entry: float,
                      sl: float, tp: float, rr: float,
                      source: str, score: float = 0, regime: str = '') -> dict:
    """核心：写入纸面挂单"""
    nav = _get_paper_nav()
    is_major = symbol in ('BTCUSDT', 'ETHUSDT')
    size_pct = PAPER_SIZE_BTC if is_major else PAPER_SIZE_ALT
    lev = PAPER_LEV_MAJOR if is_major else PAPER_LEV_ALT

    notional = nav * size_pct * lev
    margin   = nav * size_pct
    qty_raw  = notional / entry

    # 精度
    prec = {'BTCUSDT':3,'ETHUSDT':2,'BNBUSDT':2}.get(symbol, 1)
    qty  = round(qty_raw, prec) or round(qty_raw, prec+2)

    max_loss   = abs(entry - sl) / entry * margin * lev
    max_profit = max_loss * rr

    ts = int(time.time())
    record = {
        'id':          f'PAPER-{ts}-{symbol}',
        'symbol':      symbol,
        'side':        direction,
        'type':        'LIMIT',
        'entry':       entry,
        'sl':          sl,
        'tp':          tp,
        'rr':          round(rr, 2),
        'qty':         qty,
        'notional':    round(notional, 2),
        'margin':      round(margin, 2),
        'lev':         lev,
        'size_pct':    size_pct,
        'nav':         nav,
        'max_loss':    round(max_loss, 2),
        'max_profit':  round(max_profit, 2),
        'score':       round(score, 1),
        'regime':      regime,
        'source':      source,
        'status':      'PENDING',
        'created_at':  ts,
        'filled_at':   None,
        'close_price': None,
        'pnl':         None,
    }

    with open(PAPER_ORDERS_FILE, 'a') as f:
        f.write(json.dumps(record) + '\n')

    _mark_dedup(symbol, direction)
    _log.info(f'[PAPER] {symbol} {direction} @${entry:,} RR={rr} score={score} src={source}')
    return record


# ── 核心流水线 ────────────────────────────────────────────────────────

def process_symbol(symbol: str, source: str = 'auto') -> dict:
    """
    单标的全流水线：
    Step1 实时数据 → Step2 35维评分 → Step3 AI议会 → Step4 战场预判 → Step5 纸面开单
    """
    import urllib.request
    result = {'symbol': symbol, 'action': 'SKIP', 'reason': '', 'orders': []}

    # ── Step1: 实时价格 ──
    try:
        t = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}', timeout=5).read())
        price = float(t['lastPrice'])
    except Exception as e:
        result['reason'] = f'price_fetch_fail: {e}'
        return result

    # ── Step2: 35维评分 ──
    try:
        import kronos_bridge as kb; kb._cache = {}
        from brahma_core import analyze
        r = analyze(symbol)
        score   = r.get('score', 0)
        regime  = r.get('regime', '')
        direction = r.get('direction') or r.get('signal_dir') or 'LONG'
        blocked = r.get('blocked', False)
    except Exception as e:
        result['reason'] = f'analyze_fail: {e}'
        return result

    # 死穴检查
    if blocked:
        result['reason'] = f'blocked: {r.get("block_reason","")}'
        return result
    if direction == 'LONG' and regime in DEAD_HOLE_LONG:
        result['reason'] = f'dead_hole_long in {regime}'
        return result
    if direction == 'SHORT' and regime in DEAD_HOLE_SHORT:
        result['reason'] = f'dead_hole_short in {regime}'
        return result

    # 纸面门槛
    if score < PAPER_SCORE_MIN:
        result['reason'] = f'score={score:.1f} < {PAPER_SCORE_MIN}'
        return result

    # ── Step3: AI议会 ──
    council_adj = 0
    council_action = 'PASS'
    try:
        from llm_council import council_verdict
        bd = {'score': score, 'regime': regime, 'grade': r.get('grade', 100)}
        v = council_verdict(bd, direction, regime, score)
        council_action = v.get('action', 'PASS')
        council_adj    = v.get('council_score', 0)
        if council_action in ('HARD_BLOCK',):
            result['reason'] = f'council HARD_BLOCK'
            return result
    except Exception as e:
        _log.warning(f'council skip: {e}')

    score_adj = score + council_adj

    # ── Step4: 战场预判 ──
    try:
        from price_zone_engine import calc_zones
        z = calc_zones(symbol, force_refresh=True)
        high_zone = z.get('high_short', {})
        low_zone  = z.get('low_long', {})
    except Exception as e:
        result['reason'] = f'zone_fail: {e}'
        return result

    # ── Step5: 选择最优区间开单 ──
    opened = []

    # 检查持仓上限
    if _count_open_positions() >= MAX_OPEN_POS:
        result['reason'] = f'max_positions {MAX_OPEN_POS} reached'
        return result

    # 做多区间（低多区）
    if low_zone.get('low') and low_zone.get('rr', 0) >= PAPER_RR_MIN:
        lrr = low_zone['rr']
        entry_mid = (low_zone['low'] + low_zone['high']) / 2
        if not _is_dedup(symbol, 'LONG'):
            rec = _open_paper_order(
                symbol, 'LONG', entry_mid,
                sl=low_zone['sl'], tp=low_zone['tp'], rr=lrr,
                source=source, score=score_adj, regime=regime
            )
            opened.append(rec)

    # 做空区间（高空区）
    if high_zone.get('low') and high_zone.get('rr', 0) >= PAPER_RR_MIN:
        hrr = high_zone['rr']
        entry_mid = (high_zone['low'] + high_zone['high']) / 2
        if not _is_dedup(symbol, 'SHORT'):
            rec = _open_paper_order(
                symbol, 'SHORT', entry_mid,
                sl=high_zone['sl'], tp=high_zone['tp'], rr=hrr,
                source=source, score=score_adj, regime=regime
            )
            opened.append(rec)

    if not opened:
        result['reason'] = f'no valid zone: long_rr={low_zone.get("rr","?")}, short_rr={high_zone.get("rr","?")}'
        return result

    result['action'] = 'PAPER_OPEN'
    result['orders'] = opened
    result['score']  = score_adj
    result['regime'] = regime
    return result


def run_candidates(notify: bool = True) -> list:
    """候选池→全量纸面开单"""
    cand_file = DATA / 'candidates.json'
    if not cand_file.exists():
        _log.warning('candidates.json不存在，跳过')
        return []

    candidates = json.loads(cand_file.read_text())
    symbols = candidates if isinstance(candidates, list) else candidates.get('symbols', [])
    symbols = [s if isinstance(s, str) else s.get('symbol', '') for s in symbols]
    symbols = [s for s in symbols if s][:20]  # 最多处理20个

    _log.info(f'候选池: {len(symbols)}个标的')
    results = []
    opened_count = 0

    for sym in symbols:
        try:
            r = process_symbol(sym, source='candidates')
            results.append(r)
            if r['action'] == 'PAPER_OPEN':
                opened_count += len(r['orders'])
                _log.info(f'  ✅ {sym}: 开单{len(r["orders"])}笔')
            else:
                _log.info(f'  ⏭ {sym}: {r["reason"]}')
        except Exception as e:
            _log.error(f'  ❌ {sym}: {e}')

    if notify and opened_count > 0:
        syms_opened = [r['symbol'] for r in results if r['action'] == 'PAPER_OPEN']
        _push(f'📋 梵天纸面自动开单 | 候选池扫描完成\n'
              f'扫描: {len(symbols)}个 | 开单: {opened_count}笔\n'
              f'标的: {", ".join(syms_opened)}\n'
              f'NAV: ${_get_paper_nav():,.0f} | 持仓上限: {MAX_OPEN_POS}')

    return results


def run_oi_signals(notify: bool = True) -> list:
    """OI异常信号→纸面开单"""
    # [2026-08-26 fix] oi_advanced_scanner写入oi_advanced_signals.jsonl，不是oi_signals.jsonl
    oi_file = DATA / 'oi_advanced_signals.jsonl'
    if not oi_file.exists():
        # fallback: rsi_trigger_event.json（oi_advanced_scanner同时写入此文件）
        trig_file = DATA / 'rsi_trigger_event.json'
        if trig_file.exists():
            try:
                sigs = json.loads(trig_file.read_text())
                if isinstance(sigs, list):
                    symbols = list({s.get('symbol') for s in sigs if s.get('symbol')})
                    _log.info(f'fallback rsi_trigger_event: {symbols}')
                    return run_zone_trigger(symbols, notify=notify)
            except Exception as e:
                _log.warning(f'rsi_trigger_event fallback失败: {e}')
        _log.warning('oi_advanced_signals.jsonl不存在')
        return []

    cutoff = time.time() - 3600  # 只处理1H内的OI信号
    results = []
    opened_count = 0

    for line in oi_file.read_text().strip().split('\n'):
        if not line: continue
        try:
            sig = json.loads(line)
            if sig.get('ts', 0) < cutoff: continue
            sym = sig.get('symbol', '')
            if not sym: continue

            r = process_symbol(sym, source='oi_signal')
            results.append(r)
            if r['action'] == 'PAPER_OPEN':
                opened_count += len(r['orders'])
        except Exception as e:
            _log.error(f'OI信号处理失败: {e}')

    if notify and opened_count > 0:
        _push(f'⚡ 梵天OI异常→纸面开单\n开单: {opened_count}笔')

    return results


def run_zone_trigger(symbols: list = None, notify: bool = True) -> list:
    """战场预判触发→纸面开单"""
    if not symbols:
        # 默认扫描BTC/ETH + 候选池TOP5
        symbols = ['BTCUSDT', 'ETHUSDT']
        cand_file = DATA / 'candidates.json'
        if cand_file.exists():
            cands = json.loads(cand_file.read_text())
            top5 = (cands if isinstance(cands, list) else cands.get('symbols', []))[:5]
            symbols += [s if isinstance(s, str) else s.get('symbol', '') for s in top5]
        symbols = list(dict.fromkeys(symbols))  # 去重保序

    results = []
    opened_count = 0

    for sym in symbols:
        try:
            r = process_symbol(sym, source='zone_trigger')
            results.append(r)
            if r['action'] == 'PAPER_OPEN':
                opened_count += len(r['orders'])
        except Exception as e:
            _log.error(f'{sym}: {e}')

    if notify and opened_count > 0:
        lines = []
        for r in results:
            if r['action'] != 'PAPER_OPEN': continue
            for o in r['orders']:
                emoji = '🟢' if o['side']=='LONG' else '🔴'
                lines.append(f"{emoji} {o['symbol']} {o['side']} @${o['entry']:,} RR={o['rr']}")
        _push(f'🔱 梵天纸面自动开单 | 战场预判触发\n' + '\n'.join(lines))

    return results


# ── 结算追踪 ────────────────────────────────────────────────────────────

def settle_pending_orders(notify: bool = True) -> list:
    """
    扫描PENDING纸面挂单，检查价格是否触及进场区
    触及→FILLED，同时检查止损/止盈是否触发
    """
    import urllib.request
    if not PAPER_ORDERS_FILE.exists():
        return []

    lines = PAPER_ORDERS_FILE.read_text().strip().split('\n')
    updated = []
    settled = []
    ts = int(time.time())

    for line in lines:
        if not line: continue
        try:
            o = json.loads(line)
        except:
            updated.append(line)
            continue

        status = o.get('status', 'PENDING')

        if status == 'PENDING':
            # 检查价格是否触及进场区（±0.5%容忍）
            try:
                t = json.loads(urllib.request.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={o["symbol"]}',
                    timeout=3).read())
                price = float(t['price'])
                entry = o['entry']
                tol   = entry * 0.005

                if abs(price - entry) <= tol or (
                    o['side'] == 'LONG'  and price <= entry + tol) or (
                    o['side'] == 'SHORT' and price >= entry - tol):
                    o['status']    = 'FILLED'
                    o['filled_at'] = ts
                    o['fill_price']= price
                    _log.info(f'[FILLED] {o["symbol"]} {o["side"]} @${price:,}')
            except:
                pass

        elif status == 'FILLED':
            # 检查止损/止盈
            try:
                t = json.loads(urllib.request.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={o["symbol"]}',
                    timeout=3).read())
                price = float(t['price'])
                fill  = o.get('fill_price', o['entry'])
                sl    = o['sl']
                tp    = o['tp']
                side  = o['side']

                hit_sl = (side=='LONG' and price <= sl) or (side=='SHORT' and price >= sl)
                hit_tp = (side=='LONG' and price >= tp) or (side=='SHORT' and price <= tp)

                if hit_tp or hit_sl:
                    close_price = tp if hit_tp else sl
                    if side == 'LONG':
                        pnl = (close_price - fill) / fill * o['notional']
                    else:
                        pnl = (fill - close_price) / fill * o['notional']
                    o['status']      = 'CLOSED'
                    o['close_price'] = close_price
                    o['close_at']    = ts
                    o['pnl']         = round(pnl, 2)
                    o['close_reason']= 'TP' if hit_tp else 'SL'
                    settled.append(o)
                    _log.info(f'[CLOSED] {o["symbol"]} {o["side"]} PnL=${pnl:.2f} ({o["close_reason"]})')
            except:
                pass

        updated.append(json.dumps(o))

    PAPER_ORDERS_FILE.write_text('\n'.join(updated) + '\n')

    # 推送结算结果
    if notify and settled:
        lines_out = []
        total_pnl = 0
        for o in settled:
            emoji = '✅' if o['pnl'] > 0 else '❌'
            lines_out.append(f"{emoji} {o['symbol']} {o['side']} {o['close_reason']} PnL=${o['pnl']:+,.2f}")
            total_pnl += o['pnl']
        _push(f'📊 梵天纸面结算\n' + '\n'.join(lines_out) + f'\n合计: ${total_pnl:+,.2f}')

    return settled


# ── 主入口 ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='梵天纸面自动开单桥接器')
    parser.add_argument('--source', choices=['candidates','oi','zone','settle','all'], default='zone')
    parser.add_argument('--symbols', nargs='*', help='指定标的列表')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-notify', action='store_true')
    args = parser.parse_args()

    notify = not args.no_notify
    results = []

    if args.source in ('candidates', 'all'):
        results += run_candidates(notify=notify)

    if args.source in ('oi', 'all'):
        results += run_oi_signals(notify=notify)

    if args.source in ('zone', 'all'):
        syms = args.symbols or None
        results += run_zone_trigger(syms, notify=notify)

    if args.source in ('settle', 'all'):
        settle_pending_orders(notify=notify)

    opened = sum(1 for r in results if r.get('action') == 'PAPER_OPEN')
    skipped = len(results) - opened
    print(f'完成: 开单={opened} 跳过={skipped}')

    # 写bridge日志
    log_entry = {
        'ts': int(time.time()),
        'source': args.source,
        'total': len(results),
        'opened': opened,
        'skipped': skipped,
    }
    with open(PAPER_BRIDGE_LOG, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')


if __name__ == '__main__':
    main()

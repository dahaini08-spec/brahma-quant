#!/usr/bin/env python3
"""
square_trade_loop.py — 战场报告→纸面开单→Square闭环帖自动发布
设计院封印 2026-09-14 苏摩111

逻辑链路：
  1. 读取梵天分析输出的VIP信号（ENTER/WAIT+入场条件）
  2. 如果有ENTER信号 → 纸面开单（paper_positions.json）
  3. 用模板E（build_trade_open）生成入场帖 → 发到Square
  4. 定时检查持仓 → 如果TP1/TP2/SL触达 → 用模板E生成平仓帖 → 发到Square
  5. 如果持仓中 → 用模板E生成持仓帖 → 发到Square
  6. Phase 0（新增）：读取帖子观点文件 → 注入信号队列 → 纸面开单 → 闭环发帖

接入位置：
  - scripts/square_trade_loop.py（本文件）
  - supercronic: 0 */6 * * * * python3 scripts/square_trade_loop.py
  - 读取 data/paper_positions.json
  - 读取 data/square_signal_inject.json（帖子观点注入文件）
  - 依赖 square_template.py build_trade_open/hold/close
  - 依赖 paper_executor.py 开单逻辑
"""
import json, sys, time, ssl, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

# Square API
SQUARE_KEY = 'd9f19e3f6ba3480584db27b09bec0f27'
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'

PAPER_POS_FILE = BASE / 'data' / 'paper_positions.json'
LOG_FILE = BASE / 'logs' / 'square_trade_loop.log'
DEDUP_FILE = BASE / 'data' / 'square_trade_loop_dedup.json'
SIGNAL_INJECT_FILE = BASE / 'data' / 'square_signal_inject.json'
SIGNAL_QUEUE = BASE / 'data' / 'auto_signal_queue.json'

# 🛡️ 决策门控开关（默认开启）
PRE_POST_VALIDATE_ENABLED = True

# 发帖去重（每条帖子只发一次）
_dedup = {}
if DEDUP_FILE.exists():
    try:
        _dedup = json.loads(DEDUP_FILE.read_text())
    except Exception:
        pass


def _log(msg: str):
    ts = datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _pre_post_validate(post_content, sig_data, pos_data, current_price):
    """
    🛡️ 发帖前决策门控：5条规则全过才允许发帖
    
    R1: 入场帖价格 == 信号entry_price
    R2: 挂单模式下入场帖价格 != 当前市价
    R3: 当前价格已到入场位（挂单模式）
    R4: 帖子方向 == 信号方向
    R5: SL/TP方向正确
    
    返回: (passed: bool, issues: list)
    """
    if not PRE_POST_VALIDATE_ENABLED:
        return True, []
    
    issues = []
    sym = sig_data.get('symbol', '')
    side = sig_data.get('signal_dir', sig_data.get('direction', ''))
    target_entry = float(sig_data.get('entry_price', 0))
    
    # R1: 入场帖价格 == 信号entry_price
    if target_entry > 0 and pos_data:
        actual_entry = float(pos_data.get('entry_price', 0))
        if actual_entry > 0 and abs(actual_entry - target_entry) / target_entry > 0.001:
            issues.append(f'R1 FAIL: 入场帖价格{actual_entry:.2f} != 信号entry_price{target_entry:.2f}')
    
    # R2: 挂单模式下入场帖价格 != 当前市价
    # 修正：价格到位时入场价≈市价是正常的，只有"价格没到但用了市价"才是BUG
    # 所以R2只在R3未触发时检查（即价格没到位时，入场价不应该等于市价）
    if target_entry > 0 and current_price > 0:
        price_at_target = False
        if side == 'SHORT' and current_price >= target_entry * 0.998:
            price_at_target = True
        elif side == 'LONG' and current_price <= target_entry * 1.002:
            price_at_target = True
        if not price_at_target:  # 价格没到位时才检查R2
            market_diff = abs(float(pos_data.get('entry_price', 0)) - current_price) / current_price * 100
            if market_diff < 0.1:
                issues.append(f'R2 FAIL: 入场价{pos_data.get("entry_price"):.2f} ≈ 市价{current_price:.2f}，价格未到位但用了市价开仓')
    
    # R3: 当前价格已到入场位（挂单模式）
    if target_entry > 0 and current_price > 0:
        if side == 'SHORT' and current_price < target_entry * 0.998:
            issues.append(f'R3 FAIL: 当前{current_price:.2f} < 入场位{target_entry:.2f}，价格未到位')
        elif side == 'LONG' and current_price > target_entry * 1.002:
            issues.append(f'R3 FAIL: 当前{current_price:.2f} > 入场位{target_entry:.2f}，价格未到位')
    
    # R4: 帖子方向 == 信号方向
    if side == 'SHORT' and '多单' in post_content:
        issues.append(f'R4 FAIL: 信号方向SHORT但帖子含"多单"')
    if side == 'LONG' and '空单' in post_content:
        issues.append(f'R4 FAIL: 信号方向LONG但帖子含"空单"')
    
    # R5: SL/TP方向正确
    if pos_data:
        entry = float(pos_data.get('entry_price', 0))
        sl = float(pos_data.get('sl_price', 0))
        tp = float(pos_data.get('tp1', pos_data.get('tp1_price', 0)))
        if side == 'SHORT':
            if sl > 0 and sl <= entry:
                issues.append(f'R5 FAIL: 做空SL{sl:.2f}应高于入场价{entry:.2f}')
            if tp > 0 and tp >= entry:
                issues.append(f'R5 FAIL: 做空TP{tp:.2f}应低于入场价{entry:.2f}')
        elif side == 'LONG':
            if sl > 0 and sl >= entry:
                issues.append(f'R5 FAIL: 做多SL{sl:.2f}应低于入场价{entry:.2f}')
            if tp > 0 and tp <= entry:
                issues.append(f'R5 FAIL: 做多TP{tp:.2f}应高于入场价{entry:.2f}')
    
    passed = len(issues) == 0
    if not passed:
        _log(f'🛡️ VALIDATE FAIL {sym} {side}: {issues}')
    return passed, issues


def _post_to_square(content: str, sig_data=None, pos_data=None) -> dict:
    """发帖到Binance Square（含🛡️决策门控）"""
    import hashlib
    
    # 🛡️ 决策门控：发帖前验证
    if sig_data and pos_data:
        sym = sig_data.get('symbol', '')
        current_price = _get_price(sym) if sym else 0
        passed, issues = _pre_post_validate(content, sig_data, pos_data, current_price)
        if not passed:
            _log(f'🛡️ BLOCKED post: {issues}')
            return {'blocked': True, 'issues': issues}
    
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    if h in _dedup:
        _log(f'DEDUP skip {h}')
        return {'skip': True}

    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={
            'X-Square-OpenAPI-Key': SQUARE_KEY,
            'Content-Type': 'application/json',
            'clienttype': 'binanceSkill',
        },
    )
    ctx = ssl.create_default_context()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=ctx).read())
        if resp.get('success'):
            _dedup[h] = time.time()
            DEDUP_FILE.write_text(json.dumps(_dedup, indent=2))
            _log(f'POST success id={resp.get("data", {}).get("id")}')
        return resp
    except Exception as e:
        _log(f'POST error: {e}')
        return {'error': str(e)}


def _get_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0


def _fmt_sym(symbol: str) -> str:
    """BTCUSDT → BTC"""
    return symbol.replace('USDT', '')


def _load_positions() -> dict:
    if PAPER_POS_FILE.exists():
        try:
            return json.loads(PAPER_POS_FILE.read_text())
        except Exception:
            pass
    return {'positions': [], 'closed': [], 'stats': {'total': 0, 'win': 0, 'pnl': 0.0}}


def _save_positions(data: dict):
    PAPER_POS_FILE.write_text(json.dumps(data, indent=2))


def _calc_pnl(pos: dict, current_price: float) -> float:
    """计算浮盈百分比"""
    entry = pos['entry_price']
    side = pos['side']
    if side == 'LONG':
        pnl = (current_price - entry) / entry * 100 * pos.get('leverage', 1)
    else:
        pnl = (entry - current_price) / entry * 100 * pos.get('leverage', 1)
    return pnl


def run_inject_loop():
    """
    Phase 0: 读取帖子观点注入文件 → 注入信号队列
    
    帖子观点文件格式 (square_signal_inject.json):
    [
      {
        "symbol": "BTCUSDT",
        "direction": "SHORT",
        "entry_price": 78458,
        "sl_price": 80300,
        "tp1": 76300,
        "tp2": 75000,
        "leverage": 10,
        "nav_pct": 5,
        "score": 120,
        "regime": "CHOP_MID",
        "source": "square_post_366420356749874",
        "logic_line": "FVG磁铁78193+止损墙78458+OI SHORT_BUILD共振",
        "vip_status": "ENTER"
      }
    ]
    """
    if not SIGNAL_INJECT_FILE.exists():
        return
    
    try:
        injects = json.loads(SIGNAL_INJECT_FILE.read_text())
        if not injects:
            return
    except Exception as e:
        _log(f'Inject file read error: {e}')
        return
    
    # 读取现有信号队列
    existing_signals = []
    if SIGNAL_QUEUE.exists():
        try:
            existing_signals = json.loads(SIGNAL_QUEUE.read_text())
            if isinstance(existing_signals, dict):
                existing_signals = existing_signals.get('signals', [])
        except Exception:
            existing_signals = []
    
    # 过滤掉已注入的
    new_count = 0
    existing_sources = {s.get('source', '') for s in existing_signals}
    for inj in injects:
        if inj.get('source') in existing_sources:
            continue
        if not inj.get('vip_status') == 'ENTER':
            continue
        # 注入信号队列
        existing_signals.append(inj)
        new_count += 1
        _log(f'INJECT {inj.get("symbol")} {inj.get("direction")} source={inj.get("source")}')
    
    if new_count > 0:
        SIGNAL_QUEUE.write_text(json.dumps(existing_signals, indent=2))
        _log(f'Injected {new_count} signals into queue')
    
    # 清空注入文件（已处理）
    SIGNAL_INJECT_FILE.write_text('[]')


def run_open_loop():
    """
    Phase 1: 检查信号队列，有ENTER信号则纸面开单+发入场帖
    """
    from square.square_template import build_trade_open

    # 读取信号队列
    signal_queue_file = BASE / 'data' / 'auto_signal_queue.json'
    if not signal_queue_file.exists():
        _log('No signal queue file')
        return

    try:
        signals = json.loads(signal_queue_file.read_text())
        if isinstance(signals, dict):
            signals = signals.get('signals', [])
    except Exception:
        signals = []

    if not signals:
        _log('No signals in queue')
        return

    positions_data = _load_positions()
    active_symbols = {p['symbol'] for p in positions_data['positions']}

    for sig in signals:
        if sig.get('paper_consumed') or sig.get('square_loop_consumed'):
            continue

        sym = sig.get('symbol', '')
        side = sig.get('signal_dir', sig.get('direction', ''))
        vip_status = sig.get('vip_status', '')

        if vip_status != 'ENTER':
            continue

        if sym in active_symbols:
            _log(f'SKIP {sym}: already have position')
            continue

        sl_pct = float(sig.get('sl_pct', 2.0))
        tp1 = float(sig.get('tp1', 0))
        tp2 = float(sig.get('tp2', 0))
        leverage = int(sig.get('leverage', 10))
        nav_pct = float(sig.get('nav_pct', 5))
        score = float(sig.get('score_final', sig.get('score', 0)))
        regime = sig.get('regime', '')
        logic = sig.get('logic_line', sig.get('decision', ''))

        # BUG修复：如果信号中指定了entry_price，说明是挂单（等价格到入场位再开仓）
        # 不是市价开仓！必须检查当前价格是否接近入场价才开仓。
        target_entry = float(sig.get('entry_price', 0))
        current_price = _get_price(sym)
        if not current_price:
            _log(f'SKIP {sym}: no price')
            continue

        if target_entry > 0:
            # 挂单模式：检查价格是否到达入场位
            distance_pct = abs(current_price - target_entry) / target_entry * 100
            if side == 'SHORT':
                # 做空挂单：等价格反弹到target_entry附近才开仓
                if current_price < target_entry * 0.998:
                    _log(f'WAIT {sym} {side}: 当前{current_price:.2f} < 入场位{target_entry:.2f}，等反弹')
                    continue
                # 到了入场位（或已超过），用target_entry作为入场价
                entry_price = target_entry
            elif side == 'LONG':
                # 做多挂单：等价格回调到target_entry附近才开仓
                if current_price > target_entry * 1.002:
                    _log(f'WAIT {sym} {side}: 当前{current_price:.2f} > 入场位{target_entry:.2f}，等回调')
                    continue
                entry_price = target_entry
        else:
            # 市价模式（梵天信号没有target_entry）
            entry_price = current_price

        if side == 'LONG':
            sl_price = entry_price * (1 - sl_pct / 100)
            tp_price = tp1 if tp1 else entry_price * 1.02
        else:
            sl_price = entry_price * (1 + sl_pct / 100)
            tp_price = tp1 if tp1 else entry_price * 0.98

        # 写入纸面持仓
        pos = {
            'symbol': sym,
            'side': side,
            'entry_price': entry_price,
            'sl_price': round(sl_price, 2),
            'tp1': round(tp_price, 2),
            'tp1_price': round(tp_price, 2),
            'tp2': round(tp2, 2) if tp2 else 0,
            'tp2_price': round(tp2, 2) if tp2 else 0,
            'nav_pct': nav_pct / 100,
            'leverage': leverage,
            'score': score,
            'regime': regime,
            'sl_pct': sl_pct,
            'open_ts': int(time.time()),
            'open_at': datetime.now(UTC).isoformat(),
            'partial_tp1': False,
            'status': 'open',
            'source': 'square_trade_loop',
        }
        positions_data['positions'].append(pos)
        positions_data['stats']['total'] = positions_data['stats'].get('total', 0) + 1

        # 标记消费
        sig['square_loop_consumed'] = True

        # 发入场帖（🛡️带验证）
        post = build_trade_open(
            _fmt_sym(sym), side, entry_price, sl_price, tp_price,
            leverage, nav_pct, logic
        )
        resp = _post_to_square(post, sig_data=sig, pos_data=pos)
        if resp.get('blocked'):
            _log(f'🛡️ BLOCKED {sym} {side} entry post: {resp.get("issues")}')
            # 不标记消费，下次重新检查
        else:
            _log(f'OPEN {sym} {side} @{entry_price:.2f} → Square posted')
            sig['square_loop_consumed'] = True
    _save_positions(positions_data)
    # 更新信号队列
    try:
        signal_queue_file.write_text(json.dumps(signals, indent=2))
    except Exception:
        pass


def run_monitor_loop():
    """
    Phase 2: 检查活跃持仓 → 止盈/止损/超时 → 发平仓帖
    """
    from square.square_template import build_trade_hold, build_trade_close

    positions_data = _load_positions()
    if not positions_data['positions']:
        _log('No active positions to monitor')
        return

    new_positions = []
    for pos in positions_data['positions']:
        sym = pos['symbol']
        side = pos['side']
        entry = pos['entry_price']
        sl = pos['sl_price']
        tp1 = pos.get('tp1_price', pos.get('tp1', 0))
        tp2 = pos.get('tp2_price', pos.get('tp2', 0))
        open_ts = pos.get('open_ts', 0)

        price = _get_price(sym)
        if not price:
            new_positions.append(pos)
            continue

        pnl = _calc_pnl(pos, price)

        # 检查止盈
        tp1_hit = False
        if side == 'LONG' and tp1 > 0 and price >= tp1:
            tp1_hit = True
        elif side == 'SHORT' and tp1 > 0 and price <= tp1:
            tp1_hit = True

        # 检查止损
        sl_hit = False
        if side == 'LONG' and price <= sl:
            sl_hit = True
        elif side == 'SHORT' and price >= sl:
            sl_hit = True

        # 检查超时 (72H)
        timeout = (time.time() - open_ts) > 72 * 3600

        if sl_hit:
            # 止损平仓
            pnl_final = _calc_pnl(pos, sl)
            pos['status'] = 'closed'
            pos['close_price'] = sl
            pos['close_reason'] = '🔴SL止损'
            pos['close_ts'] = int(time.time())
            pos['pnl_pct'] = pnl_final
            positions_data['closed'].append(pos)
            if pnl_final > 0:
                positions_data['stats']['win'] = positions_data['stats'].get('win', 0) + 1
            positions_data['stats']['pnl'] = positions_data['stats'].get('pnl', 0) + pnl_final

            post = build_trade_close(_fmt_sym(sym), side, entry, sl, pnl_final,
                                     '止损离场，严格执行纪律')
            _post_to_square(post)
            _log(f'CLOSE {sym} {side} SL@{sl:.2f} pnl={pnl_final:+.1f}% → Square posted')

        elif tp1_hit and not pos.get('partial_tp1'):
            # TP1止盈
            pos['partial_tp1'] = True
            pnl_at_tp1 = _calc_pnl(pos, tp1)
            post = build_trade_hold(_fmt_sym(sym), side, entry, price, pnl,
                                     f'TP1@{tp1:.2f}已触达，减仓50%，剩余移动止损保本')
            _post_to_square(post)
            _log(f'TP1 {sym} {side} @{tp1:.2f} pnl={pnl_at_tp1:+.1f}% → Square posted')
            new_positions.append(pos)

        elif tp1_hit and pos.get('partial_tp1') and tp2 > 0:
            # TP2全平
            pnl_final = _calc_pnl(pos, tp1)
            pos['status'] = 'closed'
            pos['close_price'] = tp1
            pos['close_reason'] = '✅TP止盈'
            pos['close_ts'] = int(time.time())
            pos['pnl_pct'] = pnl_final
            positions_data['closed'].append(pos)
            positions_data['stats']['win'] = positions_data['stats'].get('win', 0) + 1
            positions_data['stats']['pnl'] = positions_data['stats'].get('pnl', 0) + pnl_final

            post = build_trade_close(_fmt_sym(sym), side, entry, tp1, pnl_final,
                                     'TP止盈完成，闭环结束')
            _post_to_square(post)
            _log(f'TP2 {sym} {side} @{tp1:.2f} pnl={pnl_final:+.1f}% → Square posted')

        elif timeout:
            # 超时平仓
            pnl_final = pnl
            pos['status'] = 'closed'
            pos['close_price'] = price
            pos['close_reason'] = '⏰72H超时平仓'
            pos['close_ts'] = int(time.time())
            pos['pnl_pct'] = pnl_final
            positions_data['closed'].append(pos)
            if pnl_final > 0:
                positions_data['stats']['win'] = positions_data['stats'].get('win', 0) + 1
            positions_data['stats']['pnl'] = positions_data['stats'].get('pnl', 0) + pnl_final

            post = build_trade_close(_fmt_sym(sym), side, entry, price, pnl_final,
                                     '72H超时平仓，时间止损')
            _post_to_square(post)
            _log(f'TIMEOUT {sym} {side} @{price:.2f} pnl={pnl_final:+.1f}% → Square posted')

        else:
            # 持仓中 — 每6小时发一次持仓帖
            last_hold_ts = pos.get('last_hold_post_ts', 0)
            if time.time() - last_hold_ts > 6 * 3600:
                post = build_trade_hold(_fmt_sym(sym), side, entry, price, pnl,
                                        pos.get('hold_note', ''))
                _post_to_square(post)
                pos['last_hold_post_ts'] = int(time.time())
                _log(f'HOLD {sym} {side} @{price:.2f} pnl={pnl:+.1f}% → Square posted')
            new_positions.append(pos)

    positions_data['positions'] = new_positions
    _save_positions(positions_data)


def main():
    _log('=== square_trade_loop start ===')
    run_inject_loop()  # Phase 0: 帖子观点→信号队列
    run_open_loop()    # Phase 1: 检查新信号→开单+入场帖
    run_monitor_loop() # Phase 2: 检查持仓→止盈/止损/持仓帖
    _log('=== square_trade_loop done ===')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
📊 梵天策略自动验证 brahma_strategy_validator.py
2026-10-09 苏摩111封印

Loop Engineer 融合：红绿循环（TDD思想）
VIP策略发出时自动写入测试用例，每15分钟检查结果，自动推送复盘

功能：
  - VIP策略发出后自动记录（可由brahma_output_template.py调用）
  - 每15分钟检查价格是否触及SL/TP
  - 自动推送复盘结果
  - 累积真实胜率统计
"""
import json, pathlib, time, sys, os, subprocess, uuid, ssl, urllib.request, argparse

BASE  = pathlib.Path(__file__).parent.parent
DATA  = BASE / 'data'
TESTS_FILE   = DATA / 'strategy_tests.json'
RESULTS_FILE = DATA / 'strategy_results.json'
JARVIS_USER   = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed')
CHANNEL = 'jarvis'

ctx = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def push(msg):
    target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
    cmd = ['openclaw', 'message', 'send', '--channel', CHANNEL, '-t', target, '-m', msg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
        print(f'[PUSH OK] {r.stdout.strip()[:60]}')
    except Exception as e:
        print(f'[PUSH ERR] {e}', file=sys.stderr)

def load_tests():
    try: return json.loads(TESTS_FILE.read_text())
    except: return []

def save_tests(t):
    tmp = TESTS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2))
    tmp.replace(TESTS_FILE)

def load_results():
    try: return json.loads(RESULTS_FILE.read_text())
    except: return []

def save_results(r):
    tmp = RESULTS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(r, ensure_ascii=False, indent=2))
    tmp.replace(RESULTS_FILE)

def get_price(symbol):
    try:
        return float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}USDT')['price'])
    except:
        return 0.0


def cmd_add(args):
    """VIP策略发出时调用，写入测试用例"""
    tests = load_tests()
    test_id = str(uuid.uuid4())[:8]
    test = {
        'id':         test_id,
        'symbol':     args.symbol.upper(),
        'direction':  args.direction.upper(),
        'entry':      args.entry,
        'sl':         args.sl,
        'tp1':        args.tp1,
        'tp2':        args.tp2 or 0,
        'created_at': time.time(),
        'entry_price': get_price(args.symbol),
        'status':     'PENDING',
        'result':     None,
        'source':     args.source or 'manual',
    }
    tests.append(test)
    save_tests(tests)
    print(f'✅ 策略测试用例已记录 ID={test_id} {args.symbol} {args.direction}')
    return test_id


def cmd_check(args):
    """每15分钟由cron调用，检查所有待验证策略"""
    tests   = load_tests()
    results = load_results()
    pending = [t for t in tests if t['status'] == 'PENDING']

    if not pending:
        print('[validator] 无待验证策略')
        return

    changed = False
    for t in tests:
        if t['status'] != 'PENDING':
            continue

        sym   = t['symbol']
        price = get_price(sym)
        if price == 0:
            continue

        age_h = (time.time() - t['created_at']) / 3600
        sl    = t['sl']
        tp1   = t['tp1']
        entry = t['entry']
        direction = t['direction']

        # 判断是否触及SL/TP
        sl_hit  = (direction == 'LONG'  and price <= sl) or \
                  (direction == 'SHORT' and price >= sl)
        tp_hit  = (direction == 'LONG'  and price >= tp1) or \
                  (direction == 'SHORT' and price <= tp1)
        expired = age_h >= 72  # 72H未触发则过期

        if sl_hit:
            t['status'] = 'COMPLETED'
            t['result']  = 'STOP_LOSS'
            t['exit_price'] = price
            t['exit_at']    = time.time()
            changed = True
            rr    = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            loss  = abs(entry - price) / entry * 100
            msg   = (
                f'🔴 梵天策略复盘 [{t["id"]}] — 止损\n'
                f'{sym} {direction}\n\n'
                f'入场 ${entry:,.2f} → 出场 ${price:,.2f}\n'
                f'亏损 -{loss:.2f}% | RR设计={rr:.1f}\n\n'
                f'❌ RED：价格先触及止损 ${sl:,.2f}\n\n'
                f'复盘要点：\n'
                f'  · 入场前FR/CVD/LSR方向是否一致？\n'
                f'  · SL是否≥1.5×ATR？\n'
                f'  · 是否在GEX负区入多？\n\n'
                f'🌿 姓赵不宣 | 失败是最好的老师'
            )
            push(msg)
            results.append({**t})
            print(f'[STOP_LOSS] {t["id"]} {sym} ${price:,.2f}')

        elif tp_hit:
            t['status'] = 'COMPLETED'
            t['result']  = 'TAKE_PROFIT'
            t['exit_price'] = price
            t['exit_at']    = time.time()
            changed = True
            rr     = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            profit = abs(price - entry) / entry * 100
            msg    = (
                f'✅ 梵天策略复盘 [{t["id"]}] — 止盈\n'
                f'{sym} {direction}\n\n'
                f'入场 ${entry:,.2f} → 出场 ${price:,.2f}\n'
                f'盈利 +{profit:.2f}% | RR={rr:.1f}\n\n'
                f'🟢 GREEN：价格触及目标 ${tp1:,.2f}\n\n'
                f'🌿 姓赵不宣 | 策略验证通过'
            )
            push(msg)
            results.append({**t})
            print(f'[TAKE_PROFIT] {t["id"]} {sym} ${price:,.2f}')

        elif expired:
            t['status'] = 'EXPIRED'
            t['result']  = 'EXPIRED'
            t['exit_at'] = time.time()
            changed = True
            print(f'[EXPIRED] {t["id"]} {sym} 72H未触发')

        else:
            dist_sl = abs(price - sl) / price * 100
            dist_tp = abs(price - tp1) / price * 100
            print(f'[PENDING] {t["id"]} {sym} ${price:,.2f} | SL距{dist_sl:.2f}% TP距{dist_tp:.2f}%')

    if changed:
        save_tests(tests)
        save_results(results)


def cmd_stats(args):
    """胜率统计"""
    results = load_results()
    completed = [r for r in results if r['result'] in ('STOP_LOSS', 'TAKE_PROFIT')]
    if not completed:
        print('暂无完成的策略记录')
        return
    wins   = sum(1 for r in completed if r['result'] == 'TAKE_PROFIT')
    losses = sum(1 for r in completed if r['result'] == 'STOP_LOSS')
    wr     = wins / len(completed) * 100 if completed else 0

    # 按标的分组
    by_sym = {}
    for r in completed:
        s = r['symbol']
        if s not in by_sym: by_sym[s] = {'win': 0, 'loss': 0}
        if r['result'] == 'TAKE_PROFIT': by_sym[s]['win'] += 1
        else: by_sym[s]['loss'] += 1

    print(f'\n📊 梵天策略验证统计')
    print(f'总计: {len(completed)}笔 | 胜率: {wr:.1f}% | 胜{wins}/负{losses}')
    for sym, d in by_sym.items():
        total = d['win'] + d['loss']
        sym_wr = d['win'] / total * 100
        print(f'  {sym}: {sym_wr:.1f}% ({d["win"]}/{total})')


def main():
    parser = argparse.ArgumentParser(description='梵天策略自动验证')
    sub = parser.add_subparsers(dest='cmd')

    p_add = sub.add_parser('add', help='记录VIP策略测试用例')
    p_add.add_argument('--symbol',    required=True)
    p_add.add_argument('--direction', required=True, choices=['LONG','SHORT','long','short'])
    p_add.add_argument('--entry',  type=float, required=True)
    p_add.add_argument('--sl',     type=float, required=True)
    p_add.add_argument('--tp1',    type=float, required=True)
    p_add.add_argument('--tp2',    type=float, default=0)
    p_add.add_argument('--source', type=str, default='manual')

    sub.add_parser('check', help='检查所有待验证策略（每15分钟执行）')
    sub.add_parser('stats', help='查看胜率统计')

    args = parser.parse_args()
    if args.cmd == 'add':   cmd_add(args)
    elif args.cmd == 'check': cmd_check(args)
    elif args.cmd == 'stats': cmd_stats(args)
    else: parser.print_help()

if __name__ == '__main__':
    main()

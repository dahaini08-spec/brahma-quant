#!/usr/bin/env python3
"""
🎯 梵天目标导向循环 brahma_goal_loop.py
2026-10-09 苏摩111封印

Loop Engineer 融合：Goal-oriented Loop
苏摩设定目标 → 梵天持续守候 → 条件满足立即推送 → 超时/失效自动通知

使用方式：
  # 设定目标（苏摩说"等ETH回踩$2,450"）
  python3 scripts/brahma_goal_loop.py add \
    --symbol ETH --direction LONG \
    --condition "price <= 2465 AND cvd > 0" \
    --entry 2450 --sl 2413 --tp 2541 \
    --note "ETH回踩liq_long入场" --timeout 48

  # 查看当前目标
  python3 scripts/brahma_goal_loop.py list

  # 清除目标
  python3 scripts/brahma_goal_loop.py clear --id <id>

  # 每分钟由 trading_copilot.py 调用检查
  python3 scripts/brahma_goal_loop.py check
"""
import json, pathlib, time, sys, os, subprocess, argparse, uuid, ssl, urllib.request

BASE   = pathlib.Path(__file__).parent.parent
DATA   = BASE / 'data'
GOALS_FILE = DATA / 'goal_loop.json'
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
        print(f'[PUSH OK] {r.stdout.strip()[:80]}')
    except Exception as e:
        print(f'[PUSH ERR] {e}', file=sys.stderr)

def load_goals():
    try:
        return json.loads(GOALS_FILE.read_text())
    except:
        return []

def save_goals(goals):
    tmp = GOALS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(goals, ensure_ascii=False, indent=2))
    tmp.replace(GOALS_FILE)

def get_price(symbol):
    try:
        return float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}USDT')['price'])
    except Exception as e:
        print(f'[PRICE ERR] {symbol}: {e}', file=sys.stderr)
        return 0.0

def get_cvd(symbol):
    try:
        d = json.loads((DATA / f'cvd_realtime_{symbol.lower()}usdt.json').read_text())
        return float(d.get('cvd_1h', d.get('delta_1h', d.get('cvd', 0))) or 0)
    except:
        return 0.0

def get_rsi_1h(symbol):
    try:
        st = json.loads((DATA / f'brahma_state_{symbol.lower()}.json').read_text())
        return float(st.get('rsi_1h', 50) or 50)
    except:
        return 50.0

def get_oi_dir(symbol):
    try:
        st = json.loads((DATA / f'brahma_state_{symbol.lower()}.json').read_text())
        return str(st.get('oi_direction', 'NEUTRAL'))
    except:
        return 'NEUTRAL'

def eval_condition(condition_str, symbol, price):
    """
    简单条件求值器
    支持：price / cvd / rsi_1h / oi_dir
    操作符：<= >= < > == AND OR
    """
    if not condition_str or condition_str.strip() == '':
        return True
    try:
        cvd    = get_cvd(symbol)
        rsi_1h = get_rsi_1h(symbol)
        oi_dir = get_oi_dir(symbol)
        # 安全替换变量
        expr = condition_str
        expr = expr.replace('price',  str(price))
        expr = expr.replace('cvd',    str(cvd))
        expr = expr.replace('rsi_1h', str(rsi_1h))
        expr = expr.replace('oi_dir', f'"{oi_dir}"')
        expr = expr.replace(' AND ', ' and ')
        expr = expr.replace(' OR ',  ' or ')
        return bool(eval(expr, {"__builtins__": {}}))
    except Exception as e:
        print(f'[EVAL ERR] {condition_str}: {e}', file=sys.stderr)
        return False


# ── 命令：add ──────────────────────────────────────
def cmd_add(args):
    goals = load_goals()
    goal_id = str(uuid.uuid4())[:8]
    goal = {
        'id':         goal_id,
        'symbol':     args.symbol.upper(),
        'direction':  args.direction.upper(),
        'condition':  args.condition,
        'entry':      args.entry,
        'sl':         args.sl,
        'tp1':        args.tp,
        'note':       args.note or '',
        'timeout_h':  args.timeout,
        'invalidate': args.invalidate or '',
        'created_at': time.time(),
        'status':     'WATCHING',
    }
    goals.append(goal)
    save_goals(goals)

    price = get_price(args.symbol)
    msg = (
        f'🎯 梵天目标已设定 [{goal_id}]\n'
        f'{args.symbol.upper()} {args.direction.upper()}\n\n'
        f'触发条件：{args.condition}\n'
        f'入场区：${args.entry:,.2f} | SL ${args.sl:,.2f} | TP ${args.tp:,.2f}\n'
        f'备注：{args.note or "无"}\n'
        f'超时：{args.timeout}H | 当前价：${price:,.2f}\n\n'
        f'梵天每分钟守候，条件满足立即推送\n'
        f'🌿 姓赵不宣 | 目标追踪中'
    )
    push(msg)
    print(f'✅ 目标已添加 ID={goal_id}')


# ── 命令：list ─────────────────────────────────────
def cmd_list(args):
    goals = load_goals()
    watching = [g for g in goals if g['status'] == 'WATCHING']
    if not watching:
        print('当前无活跃目标')
        return
    for g in watching:
        price = get_price(g['symbol'])
        age_h = (time.time() - g['created_at']) / 3600
        remain_h = g['timeout_h'] - age_h
        print(f"[{g['id']}] {g['symbol']} {g['direction']}")
        print(f"  条件: {g['condition']}")
        print(f"  当前价: ${price:,.2f} | 剩余: {remain_h:.1f}H")
        print(f"  入场: ${g['entry']:,.2f} SL ${g['sl']:,.2f} TP ${g['tp1']:,.2f}")
        print()


# ── 命令：clear ────────────────────────────────────
def cmd_clear(args):
    goals = load_goals()
    before = len(goals)
    if args.id == 'all':
        goals = [g for g in goals if g['status'] != 'WATCHING']
    else:
        goals = [g for g in goals if not (g['id'] == args.id and g['status'] == 'WATCHING')]
    save_goals(goals)
    print(f'清除完成: {before - len(goals)}个目标已移除')


# ── 命令：check（每分钟由cron调用）───────────────────
def cmd_check(args):
    goals = load_goals()
    watching = [g for g in goals if g['status'] == 'WATCHING']
    if not watching:
        print('[goal_loop] 无活跃目标')
        return

    changed = False
    for g in goals:
        if g['status'] != 'WATCHING':
            continue

        sym   = g['symbol']
        price = get_price(sym)
        if price == 0:
            continue

        age_h   = (time.time() - g['created_at']) / 3600
        expired = age_h >= g['timeout_h']
        invalid = g.get('invalidate') and eval_condition(g['invalidate'], sym, price)
        met     = eval_condition(g['condition'], sym, price)

        if met:
            g['status'] = 'TRIGGERED'
            g['triggered_at'] = time.time()
            g['triggered_price'] = price
            changed = True
            msg = (
                f'🎯 梵天目标达成！[{g["id"]}]\n'
                f'{sym} {g["direction"]} @ ${price:,.2f}\n\n'
                f'✅ 触发条件：{g["condition"]}\n\n'
                f'📌 VIP入场建议：\n'
                f'  入场区 ${g["entry"]:,.2f}\n'
                f'  止损   ${g["sl"]:,.2f}\n'
                f'  目标   ${g["tp1"]:,.2f}\n'
                f'  RR = {abs(g["tp1"]-g["entry"])/abs(g["entry"]-g["sl"]):.1f}\n\n'
                f'备注：{g.get("note","")}\n'
                f'🌿 姓赵不宣 | 不是建议'
            )
            push(msg)
            print(f'[TRIGGERED] {g["id"]} {sym} @ ${price:,.2f}')

        elif invalid:
            g['status'] = 'INVALID'
            g['ended_at'] = time.time()
            changed = True
            msg = (
                f'🚫 梵天目标作废 [{g["id"]}]\n'
                f'{sym} {g["direction"]}\n\n'
                f'触发失效条件：{g["invalidate"]}\n'
                f'当前价：${price:,.2f}\n'
                f'目标作废，请重新设定\n'
                f'🌿 姓赵不宣 | 不是建议'
            )
            push(msg)
            print(f'[INVALID] {g["id"]} {sym}')

        elif expired:
            g['status'] = 'EXPIRED'
            g['ended_at'] = time.time()
            changed = True
            msg = (
                f'⏰ 梵天目标超时 [{g["id"]}]\n'
                f'{sym} {g["direction"]}\n\n'
                f'{g["timeout_h"]}H内条件未触发\n'
                f'设定条件：{g["condition"]}\n'
                f'当前价：${price:,.2f}\n\n'
                f'目标已过期，市场结构可能已变化\n'
                f'🌿 姓赵不宣 | 不是建议'
            )
            push(msg)
            print(f'[EXPIRED] {g["id"]} {sym}')
        else:
            dist = abs(price - g['entry']) / price * 100
            print(f'[WATCHING] {g["id"]} {sym} ${price:,.2f} 距入场{dist:.2f}%')

    if changed:
        save_goals(goals)


# ── 入口 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='梵天目标导向循环')
    sub = parser.add_subparsers(dest='cmd')

    # add
    p_add = sub.add_parser('add', help='添加监控目标')
    p_add.add_argument('--symbol', required=True)
    p_add.add_argument('--direction', required=True, choices=['LONG','SHORT','long','short'])
    p_add.add_argument('--condition', required=True, help='触发条件，如 "price <= 2465 AND cvd > 0"')
    p_add.add_argument('--entry', type=float, required=True)
    p_add.add_argument('--sl',    type=float, required=True)
    p_add.add_argument('--tp',    type=float, required=True)
    p_add.add_argument('--note',  type=str, default='')
    p_add.add_argument('--timeout', type=int, default=48, help='超时小时数（默认48H）')
    p_add.add_argument('--invalidate', type=str, default='', help='失效条件')

    # list
    sub.add_parser('list', help='查看活跃目标')

    # clear
    p_clear = sub.add_parser('clear', help='清除目标')
    p_clear.add_argument('--id', required=True, help='目标ID或all')

    # check（cron调用）
    sub.add_parser('check', help='检查所有目标（每分钟执行）')

    args = parser.parse_args()
    if args.cmd == 'add':    cmd_add(args)
    elif args.cmd == 'list': cmd_list(args)
    elif args.cmd == 'clear':cmd_clear(args)
    elif args.cmd == 'check':cmd_check(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()

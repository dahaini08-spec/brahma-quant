#!/usr/bin/env python3
"""
square_trade_tracker.py — 实盘追踪栏目 [2026-10-04 苏摩111 P2]
=========================================================================
定位: 公开B线paper_ledger数据，建立信任
     「我的每一笔单，你都能看到结果」
触发: 
  1. 有新开仓 → 开仓通知帖
  2. 有平仓   → 战绩帖（盈+亏都发，诚实建立信任）
  3. 每日持仓状态更新（有持仓时）→ 每日17:00 UTC
"""
import json, sys, ssl, time, hashlib, urllib.request, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST        = timezone(timedelta(hours=8))
POSITIONS  = BASE / 'data' / 'paper_positions.json'
POSTED     = BASE / 'data' / 'trade_tracker_posted.json'
DEDUP      = BASE / 'data' / 'square_post_dedup.json'
LOG        = BASE / 'data' / 'square_post_log.jsonl'

from square_key_router import get_square_key as _gsk
SQUARE_KEY = _gsk('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

def load_posted() -> set:
    try:
        return set(json.loads(POSTED.read_text()).get('ids', []))
    except Exception: return set()

def save_posted(ids: set):
    POSTED.write_text(json.dumps({'ids': list(ids)}, ensure_ascii=False), encoding='utf-8')

def build_open_post(trade: dict) -> str:
    sym = trade['symbol'].replace('USDT','')
    side = '多单' if 'LONG' in trade.get('id','') else '空单'
    emoji = '🟢' if side == '多单' else '🔴'
    entry = trade['entry_price']
    sl = trade['sl']
    tp1 = trade.get('tp1', 0)
    lev = trade.get('leverage', 5)
    nav = trade.get('nav_pct', 5)
    regime = trade.get('regime','')
    regime_cn = {'BULL_TREND':'牛市趋势','BEAR_TREND':'熊市趋势',
                 'CHOP_MID':'震荡整理','BEAR_RECOVERY':'熊市反弹'}.get(regime, regime)
    date_s = datetime.now(CST).strftime('%m/%d %H:%M')

    lines = [
        f'{emoji} 实盘开仓 | {sym} {side} | {date_s}',
        '',
        f'开仓价: ${entry:,.2f}',
        f'止损位: ${sl:,.2f}  (SL={abs(entry-sl)/entry*100:.1f}%)',
        f'目标位: ${tp1:,.2f}' if tp1 else '目标位: 等结构',
        f'杠杆: {lev:.0f}x  仓位: {nav:.0f}% NAV',
        '',
        f'入场逻辑:',
        f'体制={regime_cn}，系统评分通过，结构确认后入场。',
        f'止损放在结构失效位，不是随意猜的数字。',
        '',
        '这笔单我会实时更新结果，不管赚了还是亏了。',
        '',
        random.choice([
            f'你觉得这笔{side}能否到达目标？评论 A（能）B（止损出）',
            f'这个入场位你认同吗？评论说说你的判断',
            f'如果是你，这笔单你跟还是观望？评论告诉我',
        ]),
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 仅供参考',
        f'${sym} #实盘追踪 #合约交易 #永续合约',
    ]
    return '\n'.join(lines)

def build_close_post(trade: dict) -> str:
    sym = trade['symbol'].replace('USDT','')
    side = '多单' if 'LONG' in trade.get('id','') else '空单'
    entry = trade['entry_price']
    exit_p = trade['exit_price']
    pnl = trade.get('net_pnl', trade.get('pnl', 0))
    pnl_pct = trade.get('pnl_pct', 0) * 100
    status = trade.get('status','')
    is_win = pnl > 0
    emoji = '✅' if is_win else '❌'
    reason = trade.get('close_reason','')
    date_s = datetime.now(CST).strftime('%m/%d')

    if is_win:
        verdict = f'这笔赚了 {pnl:+.1f}U ({abs(pnl_pct):.2f}%)。结构判断正确，止盈到位。'
        lesson = '赢的不是运气，是系统在有效体制下做了正确的事。下一笔继续等结构。'
        hook = '你之前猜对了吗？评论说说'
    else:
        verdict = f'这笔亏了 {pnl:+.1f}U ({abs(pnl_pct):.2f}%)。止损触发，按计划出场。'
        lesson = '止损不是失败，是系统在保护本金。这笔单逻辑不成立就出，没什么好纠结的。亏损可控，继续等下一个机会。'
        hook = '你觉得这笔单哪里可以改进？评论告诉我'

    lines = [
        f'{emoji} 实盘结果 | {sym} {side} | {date_s}',
        '',
        f'开仓: ${entry:,.2f}',
        f'出场: ${exit_p:,.2f}',
        f'结果: {pnl:+.1f}U',
        '',
        verdict,
        lesson,
        '',
        hook,
        '',
        '所有实盘记录公开，不选择性展示。',
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 仅供参考',
        f'${sym} #实盘追踪 #合约交易 #永续合约',
    ]
    return '\n'.join(lines)

def build_position_update(positions: list) -> str:
    date_s = datetime.now(CST).strftime('%m/%d %H:%M')
    lines = [f'📊 持仓状态 | {date_s}', '']

    if not positions:
        lines += [
            '当前空仓，无持仓。',
            '',
            '不是没机会，是没有符合条件的结构。',
            '等待比强行入场更重要。',
        ]
    else:
        lines.append(f'当前持仓 {len(positions)} 笔:')
        for pos in positions[:3]:
            sym = pos['symbol'].replace('USDT','')
            side = '多单' if 'LONG' in pos.get('id','') else '空单'
            entry = pos['entry_price']
            lines.append(f'  {sym} {side} @ ${entry:,.2f}')

    lines += [
        '',
        random.choice([
            '你现在有仓位吗？评论说说你在做什么',
            '空仓等待也是一种操作。你现在什么状态？',
            '持仓的时候最考验心态。你怎么管理持仓情绪？',
        ]),
        '',
        '关注我，每晚21:00直播+SMC教学',
        '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 仅供参考',
        '$BTC $ETH #实盘追踪 #合约交易 #永续合约',
    ]
    return '\n'.join(lines)

def is_duplicate(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        return h in d and time.time() - d[h] < 86400
    except Exception: return False

def mark_dup(content):
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP.read_text(encoding='utf-8')) if DEDUP.exists() else {}
        d[h] = time.time()
        DEDUP.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception: pass

def post_to_square(content):
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': SQUARE_KEY,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}

def run(dry_run=False, mode='auto'):
    """
    mode: auto | open | close | update
    auto: 自动检测新开仓/平仓
    """
    if not POSITIONS.exists():
        print('[tracker] paper_positions.json不存在，HEARTBEAT_OK')
        return

    data = json.loads(POSITIONS.read_text())
    open_pos = data.get('positions', [])
    closed   = data.get('closed', [])
    posted   = load_posted()
    new_posted = set()

    # 检查新平仓
    for trade in closed:
        tid = trade.get('id','')
        if not tid or tid in posted: continue
        content = build_close_post(trade)
        if is_duplicate(content): continue
        print(f'[tracker] 新平仓: {tid} pnl={trade.get("net_pnl",0):+.1f}U')
        if not dry_run:
            resp = post_to_square(content)
            if resp.get('code') == '000000' or resp.get('success'):
                pid = resp.get('data',{}).get('id','')
                print(f'[tracker] ✅ 平仓帖发布 id={pid}')
                mark_dup(content)
                with open(LOG,'a') as f:
                    f.write(json.dumps({'ts':time.time(),'post_type':'trade_close',
                        'id':pid,'chars':len(content),'preview':content[:150]},ensure_ascii=False)+'\n')
        else:
            print(content[:300])
        new_posted.add(tid)

    # 每日持仓状态更新
    update_key = f'position_update_{datetime.now(CST).strftime("%Y-%m-%d")}'
    if update_key not in posted:
        content = build_position_update(open_pos)
        if not is_duplicate(content):
            print(f'[tracker] 持仓状态更新 ({len(open_pos)}笔持仓)')
            if not dry_run:
                resp = post_to_square(content)
                if resp.get('code') == '000000' or resp.get('success'):
                    pid = resp.get('data',{}).get('id','')
                    print(f'[tracker] ✅ 持仓更新发布 id={pid}')
                    mark_dup(content)
                    with open(LOG,'a') as f:
                        f.write(json.dumps({'ts':time.time(),'post_type':'trade_update',
                            'id':pid,'chars':len(content),'preview':content[:150]},ensure_ascii=False)+'\n')
                    new_posted.add(update_key)
            else:
                print(content[:300])
                new_posted.add(update_key)

    if new_posted:
        save_posted(posted | new_posted)
    else:
        print('[tracker] 无新动作，HEARTBEAT_OK')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mode', default='auto')
    args = ap.parse_args()
    run(dry_run=args.dry_run, mode=args.mode)

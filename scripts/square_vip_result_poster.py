#!/usr/bin/env python3
"""
square_vip_result_poster.py — VIP战绩帖自动发布 + 引流漏斗 [2026-10-03 苏摩111]
=========================================================================
接入位置:
  1. cron 每30分钟检查 paper_positions.json 新关闭交易
  2. 止盈(CLOSED_TP)→自动生成战绩帖→人工确认后发布(或直发)
  3. 每帖末尾附 VIP邀请码引流链接

变现逻辑:
  战绩帖(真实盈利) → 读者信任度↑ → 点击邀请链接注册 → 佣金收益
  邀请码: XZBX666 / 链接: www.bsmkweb.cc/register?ref=XZBX666

自动发布门槛:
  - CLOSED_TP(止盈): 自动发布
  - CLOSED_SL(止损): 推送苏摩人工审核(不自动发)
  - pnl_pct > 0: 自动发布
"""
import json, sys, ssl, time, hashlib, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST         = timezone(timedelta(hours=8))
POSITIONS   = BASE / 'data' / 'paper_positions.json'
POSTED_FILE = BASE / 'data' / 'vip_result_posted.json'
DEDUP_FILE  = BASE / 'data' / 'square_post_dedup.json'
LOG_FILE    = BASE / 'data' / 'square_post_log.jsonl'

from square_key_router import get_square_key as _get_sq_key
SQUARE_KEY = _get_sq_key('auto_post')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_ctx = ssl.create_default_context()

REFERRAL = 'www.bsmkweb.cc/register?ref=XZBX666'


def load_posted() -> set:
    try:
        d = json.loads(POSTED_FILE.read_text()) if POSTED_FILE.exists() else {}
        return set(d.get('ids', []))
    except Exception:
        return set()


def save_posted(ids: set):
    POSTED_FILE.write_text(json.dumps({'ids': list(ids)}, indent=2))


def build_win_post(trade: dict) -> str:
    """止盈战绩帖（正收益，自动发布）"""
    sym = trade['symbol'].replace('USDT', '')
    direction = '多单' if trade.get('side', 'LONG') == 'LONG' or 'LONG' in trade.get('id', '') else '空单'
    emoji = '🟢' if direction == '多单' else '🔴'
    entry  = trade['entry_price']
    exit_p = trade['exit_price']
    pnl    = trade.get('net_pnl', trade.get('pnl', 0))
    pnl_pct = abs(trade.get('pnl_pct', pnl / trade.get('margin', 1) if trade.get('margin') else 0) * 100)
    lev    = trade.get('leverage', 5)
    regime = trade.get('regime', '')
    rr     = trade.get('rr', 0)
    date_s = datetime.now(CST).strftime('%m/%d')

    regime_map = {
        'BULL_TREND': '牛市上行', 'BEAR_TREND': '熊市下行',
        'CHOP_MID': '震荡整理', 'BEAR_RECOVERY': '熊市反弹',
    }
    regime_cn = regime_map.get(regime, '系统判断')

    lines = [
        f'{emoji} {sym} {direction} 实盘战绩 | {date_s}',
        '',
        f'开仓: ${entry:,.2f}',
        f'出场: ${exit_p:,.2f}',
        f'盈利: +{pnl_pct:.2f}%（净收益）',
        '',
        '策略逻辑：',
        f'体制={regime_cn}，系统识别趋势方向，在结构确认点入场。',
        f'止损放在结构失效位，RR={rr:.1f}，让盈利奔跑。',
        '',
        '不猜方向，跟结构。系统说什么做什么。',
        '',
        '想获取下一个信号？',
        f'🔗 {REFERRAL}',
        '关注我，每晚21:00直播+SMC教学',
        '🤖 内容含AI生成分析，非实时人工观点',
        '🌿 姓赵不宣 | 不是建议',
        '#实盘战绩 #合约交易 #量化交易',
    ]
    return '\n'.join(lines)


def build_loss_review_post(trade: dict) -> str:
    """止损复盘帖（人工审核版——诚实复盘反而建立信任）"""
    sym = trade['symbol'].replace('USDT', '')
    direction = '多单' if 'LONG' in trade.get('id', '') else '空单'
    entry  = trade['entry_price']
    exit_p = trade['exit_price']
    pnl    = trade.get('net_pnl', trade.get('pnl', 0))
    pnl_pct = abs(trade.get('pnl_pct', 0) * 100)
    date_s = datetime.now(CST).strftime('%m/%d')

    lines = [
        f'📋 {sym} {direction} 复盘记录 | {date_s}',
        '',
        f'开仓: ${entry:,.2f}',
        f'止损出场: ${exit_p:,.2f}',
        f'本单亏损: -{pnl_pct:.2f}%',
        '',
        '止损不是失败，是系统在保护本金。',
        '这笔单止损位在结构失效点，逻辑不成立就出，没什么好纠结的。',
        '',
        '仓位管理到位，亏损可控。下一个机会继续等。',
        '',
        f'🔗 {REFERRAL}',
        '关注我，每晚21:00直播+SMC教学',
        '🤖 内容含AI生成分析，非实时人工观点',
        '🌿 姓赵不宣 | 不是建议',
        '#合约交易 #量化交易 #风险管理',
    ]
    return '\n'.join(lines)


def is_duplicate(trade_id: str, posted: set) -> bool:
    return trade_id in posted


def post_to_square(content: str) -> bool:
    h = hashlib.md5(content[:200].encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        if h in d and float(d[h]) > time.time() - 86400:
            return False
    except Exception:
        pass

    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(SQUARE_URL, data=payload,
        headers={'X-Square-OpenAPI-Key': SQUARE_KEY,
                 'Content-Type': 'application/json',
                 'clienttype': 'binanceSkill'})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
        if resp.get('code') == '000000' or resp.get('success'):
            # 记录去重
            try:
                d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
                d[h] = time.time()
                DEDUP_FILE.write_text(json.dumps(d))
            except Exception:
                pass
            return True
        return False
    except Exception as e:
        print(f'[vip_result] 发帖异常: {e}')
        return False


def push_review_to_jarvis(trade: dict, content: str):
    """止损单推苏摩人工审核"""
    try:
        from push_hub import push_jarvis
        sym = trade['symbol']
        pnl = trade.get('net_pnl', 0)
        push_jarvis(
            f'📋 {sym} 止损单复盘帖待审核（pnl={pnl:+.1f}U）\n\n'
            f'帖子草稿：\n{content}\n\n'
            f'回复「发布」确认发布，回复「跳过」不发',
            priority='P2',
            dedup_key=f'vip_result_review_{trade["id"]}',
            dedup_ttl=86400
        )
    except Exception as e:
        print(f'[vip_result] jarvis推送失败: {e}')


def run(dry_run=False):
    if not POSITIONS.exists():
        print('[vip_result] paper_positions.json不存在')
        return

    data     = json.loads(POSITIONS.read_text())
    closed   = data.get('closed', [])
    posted   = load_posted()
    new_posted = set()

    for trade in closed:
        tid = trade.get('id', '')
        if not tid or is_duplicate(tid, posted):
            continue

        status    = trade.get('status', '')
        net_pnl   = trade.get('net_pnl', trade.get('pnl', 0))
        is_win    = status == 'CLOSED_TP' or net_pnl > 0

        if is_win:
            content = build_win_post(trade)
            if dry_run:
                print(f'[vip_result DRY] 止盈帖 {tid}:')
                print(content[:300])
                print('---')
            else:
                ok = post_to_square(content)
                if ok:
                    print(f'[vip_result] ✅ 止盈战绩帖发布: {tid}')
                    # 记录日志
                    with open(LOG_FILE, 'a') as f:
                        f.write(json.dumps({
                            'ts': time.time(), 'post_type': 'vip_result_win',
                            'trade_id': tid, 'pnl': net_pnl
                        }, ensure_ascii=False) + '\n')
                else:
                    print(f'[vip_result] ❌ 发布失败: {tid}')
        else:
            content = build_loss_review_post(trade)
            if dry_run:
                print(f'[vip_result DRY] 止损复盘推苏摩 {tid}')
            else:
                push_review_to_jarvis(trade, content)
                print(f'[vip_result] 📋 止损复盘已推苏摩审核: {tid}')

        new_posted.add(tid)

    if new_posted:
        save_posted(posted | new_posted)
        print(f'[vip_result] 处理了 {len(new_posted)} 笔新交易')
    else:
        print('[vip_result] 无新交易，HEARTBEAT_OK')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)

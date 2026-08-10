#!/usr/bin/env python3
"""
梵天纸仓管理器 v2.0
设计院封印 2026-07-12 苏摩111批准

职责：
  - 纸仓新定位：score=120~154 标准层信号的前向验证器
  - 实盘已执行的信号不重复记录
  - 自动结算：TTL到期 / SL触发 / TP触发
  - 每次运行输出统计摘要

分层架构（v3.0）：
  精英层 score≥155 → 实盘自动执行 (auto_executor)
  标准层 120~154  → 纸仓记录 + 推送苏摩确认
  守望层 100~119  → 仅日志
  丢弃层 <100     → 静默
"""
import json, requests, hmac, hashlib, time
from pathlib import Path
import sys as _sys_sc
_sys_sc.path.insert(0, str(Path(__file__).parent.parent))
try:
    from scripts.system_config import API_KEY, API_SECRET
except Exception:
    import os as _os_sc
    API_KEY    = _os_sc.environ.get("BINANCE_API_KEY", "")
    API_SECRET = _os_sc.environ.get("BINANCE_API_SECRET", "")

from datetime import datetime, timezone

now = datetime.now(timezone.utc)
API_KEY = API_KEY
API_SECRET = API_SECRET
FAPI_BASE = 'https://fapi.binance.com'

PAPER_DIR = Path('data/paper_forward')
PAPER_DIR.mkdir(parents=True, exist_ok=True)
EXEC_LOG   = PAPER_DIR / 'paper_exec_log.jsonl'
SETTLE_LOG = PAPER_DIR / 'paper_settle_log.jsonl'
SIG_LOG    = Path('data/live_signal_log.jsonl')

# 纸仓参数
MIN_SCORE = 120
MAX_SCORE = 154
TTL_HOURS = 6
MAX_OPEN_POSITIONS = 5


def get_price(symbol):
    try:
        r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                         params={'symbol': symbol}, timeout=5).json()
        return float(r.get('price', 0))
    except:
        return 0


def load_jsonl(path):
    if not path.exists(): return []
    records = []
    for l in path.read_text().strip().split('\n'):
        if not l.strip(): continue
        try: records.append(json.loads(l))
        except: pass
    return records


def append_jsonl(path, record):
    with open(path, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def get_live_executed_symbols():
    """获取实盘已执行的信号，避免纸仓重复"""
    ts = int(time.time() * 1000)
    sig = hmac.new(API_SECRET.encode(), f'timestamp={ts}'.encode(), hashlib.sha256).hexdigest()
    try:
        acc = requests.get(f'{FAPI_BASE}/fapi/v2/account',
                           params={'timestamp': ts, 'signature': sig},
                           headers={'X-MBX-APIKEY': API_KEY}, timeout=8).json()
        return {p['symbol'] for p in acc.get('positions', [])
                if abs(float(p.get('positionAmt', 0))) > 0}
    except:
        return set()


def settle_open_positions(open_positions, live_symbols):
    """结算到期/触发的纸仓"""
    remaining = []
    settled_new = []

    for pos in open_positions:
        if pos.get('settled') or pos.get('stale'):
            continue

        sym = pos.get('symbol', '')
        entry = float(pos.get('entry_price', 0))
        direction = pos.get('direction', 'LONG')
        sl = float(pos.get('sl') or pos.get('stop_loss') or 0)
        tp = float(pos.get('tp1') or 0)
        created_ts = float(pos.get('created_ts', 0))

        current = get_price(sym) if sym else 0
        age_hours = (now.timestamp() - created_ts) / 3600 if created_ts else 0

        settle_reason = None

        # TTL到期
        if age_hours >= TTL_HOURS:
            settle_reason = 'TTL_EXPIRED'
        # SL触发
        elif sl > 0 and current > 0:
            if direction == 'LONG' and current <= sl:
                settle_reason = 'SL_HIT'
            elif direction == 'SHORT' and current >= sl:
                settle_reason = 'SL_HIT'
        # TP触发
        if tp > 0 and current > 0:
            if direction == 'LONG' and current >= tp:
                settle_reason = 'TP1_HIT'
            elif direction == 'SHORT' and current <= tp:
                settle_reason = 'TP1_HIT'

        if settle_reason and entry > 0 and current > 0:
            pnl_abs = (current - entry) if direction == 'LONG' else (entry - current)
            pnl_pct = pnl_abs / entry * 100
            settled_pos = {
                **pos,
                'settled': True,
                'settle_price': round(current, 6),
                'settle_time': now.isoformat(),
                'settle_reason': settle_reason,
                'pnl': round(pnl_abs, 6),
                'pnl_pct': round(pnl_pct, 4),
                'result': 'WIN' if pnl_abs > 0 else 'LOSS',
                'age_hours': round(age_hours, 1)
            }
            settled_new.append(settled_pos)
        else:
            remaining.append(pos)

    return remaining, settled_new


def add_new_paper_positions(open_positions, live_symbols):
    """从信号日志中添加新纸仓"""
    if not SIG_LOG.exists(): return []

    # 已在纸仓中的信号
    existing_sigs = {p.get('signal_id') or p.get('symbol') for p in open_positions}
    new_positions = []

    week_ts = now.timestamp() - 7 * 86400
    sigs = load_jsonl(SIG_LOG)

    for s in sorted(sigs, key=lambda x: x.get('ts', 0), reverse=True):
        if s.get('ts', 0) < week_ts: break
        if s.get('settled'): continue
        if not s.get('valid'): continue

        sym = s.get('symbol', '')
        score = float(s.get('score', 0))

        # 只接受标准层
        if not (MIN_SCORE <= score <= MAX_SCORE): continue
        # 实盘已持有则跳过
        if sym in live_symbols: continue
        # 已在纸仓则跳过
        if sym in existing_sigs: continue
        # 超过最大持仓数
        if len(open_positions) + len(new_positions) >= MAX_OPEN_POSITIONS: break

        entry = float(s.get('entry_hi', 0) or s.get('entry_lo', 0))
        if entry == 0: continue

        paper_pos = {
            'signal_id': s.get('signal_id', f'{sym}_{s.get("ts",0)}'),
            'symbol': sym,
            'direction': s.get('direction', 'LONG'),
            'score': score,
            'regime': s.get('regime', ''),
            'entry_price': round(entry, 6),
            'entry_lo': float(s.get('entry_lo', 0)),
            'entry_hi': float(s.get('entry_hi', 0)),
            'sl': float(s.get('sl') or 0),
            'tp1': float(s.get('tp1') or 0),
            'rr1': s.get('rr1', 0),
            'created_ts': now.timestamp(),
            'created_time': now.isoformat(),
            'settled': False,
            'paper': True,
            'layer': 'STANDARD'
        }
        new_positions.append(paper_pos)

    return new_positions


def stats_summary(settle_records):
    """生成统计摘要"""
    valid = [r for r in settle_records if r.get('pnl') is not None and not r.get('stale')]
    if not valid:
        return '暂无有效结算数据'
    wins = [r for r in valid if r.get('result') == 'WIN']
    losses = [r for r in valid if r.get('result') == 'LOSS']
    total_pnl = sum(r.get('pnl', 0) for r in valid)
    wr = len(wins) / len(valid) * 100
    avg_win = sum(r.get('pnl', 0) for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r.get('pnl', 0) for r in losses) / len(losses) if losses else 0
    return (f'总结算{len(valid)}笔 WR={wr:.1f}% '
            f'总PnL={total_pnl:+.4f} '
            f'avg胜={avg_win:+.4f} avg负={avg_loss:+.4f}')


def main():
    print(f'[paper_forward_manager] {now.strftime("%m-%d %H:%M UTC")} v2.0 启动')

    # 1. 获取实盘已持仓
    live_symbols = get_live_executed_symbols()
    print(f'实盘持仓: {len(live_symbols)}个 {live_symbols}')

    # 2. 加载当前开放纸仓
    open_positions = [p for p in load_jsonl(EXEC_LOG)
                      if not p.get('settled') and not p.get('stale')]
    print(f'当前开放纸仓: {len(open_positions)}条')

    # 3. 结算到期纸仓
    remaining, settled_new = settle_open_positions(open_positions, live_symbols)
    if settled_new:
        for s in settled_new:
            append_jsonl(SETTLE_LOG, s)
            icon = '✅' if s.get('result') == 'WIN' else '❌'
            print(f'{icon} 结算: {s["symbol"]} {s["direction"]} PnL={s["pnl"]:+.4f} ({s["settle_reason"]})')

    # 4. 添加新纸仓
    new_pos = add_new_paper_positions(remaining, live_symbols)
    if new_pos:
        for p in new_pos:
            append_jsonl(EXEC_LOG, p)
            print(f'🧪 新纸仓: {p["symbol"]} {p["direction"]} sc={p["score"]} entry=${p["entry_price"]:.4g}')
    else:
        print('🧪 无新标准层信号，纸仓无新增')

    # 5. 统计
    all_settle = load_jsonl(SETTLE_LOG)
    summary = stats_summary(all_settle)
    print(f'📊 {summary}')

    total_open = len(remaining) + len(new_pos)
    if total_open == 0 and not settled_new and not new_pos:
        print('HEARTBEAT_OK')
    else:
        print(f'🧪 纸仓状态: 开放{total_open}条 本次结算{len(settled_new)}条 新增{len(new_pos)}条')


if __name__ == '__main__':
    main()

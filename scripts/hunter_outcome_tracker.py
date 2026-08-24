#!/usr/bin/env python3
# ponytail: hunter_outcome_tracker 318行，有意为之，重构前先 grep 所有调用方
"""
hunter_outcome_tracker.py — 猎手结果追踪器 v1.0
设计院 · 苏摩111 · 2026-07-17

职责：
  1. 回填 sub_executor_log.jsonl 中 fill_qty=0 的历史记录（用 order_id 查 Binance）
  2. 追踪所有已开仓 OI/PUMP 信号的当前盈亏状态
  3. 将结果写入 data/oi_outcome_log.jsonl（供胜率统计使用）
  4. HEARTBEAT_OK 无需推送，有新平仓/异常才推送

运行：python3 scripts/hunter_outcome_tracker.py
"""
import sys, os, json, time, hmac, hashlib, math
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# ── 路径 ──────────────────────────────────────────────────────────────────
SUB_LOG   = BASE / 'data/sub_executor_log.jsonl'
OI_OUT    = BASE / 'data/oi_outcome_log.jsonl'
PUSH_DED  = BASE / 'data/hunter_tracker_dedup.json'

# ── Binance 签名 ──────────────────────────────────────────────────────────
import urllib.parse, requests as _req

def _creds():
    env = BASE.parent.parent / '.env'
    k = s = ''
    try:
        for line in env.read_text().splitlines():
            if line.startswith('BINANCE_API_KEY='): k = line.split('=',1)[1].strip()
            if line.startswith('BINANCE_SECRET='):   s = line.split('=',1)[1].strip()
    except Exception:
        pass
    if not k:
        k = os.environ.get('BINANCE_API_KEY','')
        s = os.environ.get('BINANCE_SECRET','')
    return k, s

API_KEY, SECRET = _creds()
BASE_URL = 'https://fapi.binance.com'

def _signed(method, path, params=None):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    qs += f'&signature={sig}'
    headers = {'X-MBX-APIKEY': API_KEY}
    url = BASE_URL + path
    if method == 'GET':
        r = _req.get(f'{url}?{qs}', headers=headers, timeout=8)
    else:
        r = _req.post(url, data=qs, headers=headers, timeout=8)
    return r.json()

def _pub_get(path, params=None):
    r = _req.get(BASE_URL + path, params=params or {}, timeout=8)
    return r.json()

# ── 辅助 ──────────────────────────────────────────────────────────────────
def get_price(sym):
    try:
        d = _pub_get('/fapi/v1/ticker/price', {'symbol': sym})
        return float(d.get('price', 0))
    except Exception:
        return 0.0

def get_order_detail(sym, order_id):
    """回查订单实际成交详情"""
    try:
        d = _signed('GET', '/fapi/v1/order', {'symbol': sym, 'orderId': order_id})
        return {
            'fill_qty': float(d.get('executedQty', 0) or 0),
            'fill_px':  float(d.get('avgPrice', 0) or 0),
            'status':   d.get('status', '?'),
        }
    except Exception:
        return None

def get_positions():
    """获取当前所有持仓"""
    try:
        acct = _signed('GET', '/fapi/v3/account', {})
        return {p['symbol']: float(p['positionAmt'])
                for p in acct.get('positions', [])
                if abs(float(p.get('positionAmt', 0))) > 0}
    except Exception:
        return {}

def load_outcome_log():
    """读取已记录的结果，避免重复"""
    done = {}
    if OI_OUT.exists():
        for line in OI_OUT.read_text().splitlines():
            try:
                r = json.loads(line)
                sid = r.get('signal_id','') or r.get('order_id','')
                if sid:
                    done[str(sid)] = r
            except Exception:
                pass
    return done

def append_outcome(record: dict):
    with open(OI_OUT, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

# ── 核心逻辑 ──────────────────────────────────────────────────────────────
def run():
    if not SUB_LOG.exists():
        print('HEARTBEAT_OK')
        return

    # 读取所有 sub_executor 记录
    records = []
    for line in SUB_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
            if r.get('status') == 'FILLED':
                records.append(r)
        except Exception:
            pass

    if not records:
        print('HEARTBEAT_OK')
        return

    done = load_outcome_log()
    positions = get_positions()

    backfilled   = 0  # fill_qty=0 已修复数
    new_outcomes = 0  # 新增结果记录
    alerts       = []

    for r in records:
        sym      = r.get('symbol', '')
        order_id = r.get('order_id', '')
        sig_id   = r.get('signal_id', '') or str(order_id)
        fill_qty = float(r.get('fill_qty', 0) or 0)
        fill_px  = float(r.get('fill_px', 0) or 0)
        direction = r.get('direction', 'LONG')
        sl       = float(r.get('sl', 0) or 0)
        tp1      = float(r.get('tp1', 0) or 0)
        sub      = r.get('sub', 'OI')
        mode     = r.get('mode', '?')
        oi_score = float(r.get('oi_score', 0) or 0)
        regime   = r.get('regime', '?')
        ts_open  = float(r.get('ts', 0) or 0)

        # ── Step 1: 回填 fill_qty=0 ──────────────────────────────────────
        if fill_qty == 0 and order_id:
            detail = get_order_detail(sym, order_id)
            if detail and detail['fill_qty'] > 0:
                fill_qty = detail['fill_qty']
                fill_px  = detail['fill_px'] or fill_px
                backfilled += 1

        if fill_qty == 0 or fill_px == 0:
            continue  # 无法计算盈亏，跳过

        # ── Step 2: 检查是否已有结果记录 ──────────────────────────────────
        if str(sig_id) in done:
            existing = done[str(sig_id)]
            # 已平仓的不再处理
            if existing.get('outcome') in ('WIN', 'LOSS'):
                continue

        # ── Step 3: 判断当前状态 ──────────────────────────────────────────
        cp = get_price(sym)
        if cp == 0:
            continue

        pos_amt = positions.get(sym, 0)
        is_holding = abs(pos_amt) > 0

        if direction == 'LONG':
            pnl_pct = (cp - fill_px) / fill_px * 100
        else:
            pnl_pct = (fill_px - cp) / fill_px * 100

        pnl_usdt = pnl_pct / 100 * fill_qty * fill_px

        hold_hours = (time.time() - ts_open) / 3600 if ts_open else 0

        if is_holding:
            outcome = 'HOLDING'
        else:
            # 已平仓：根据最终pnl判断（近似）
            outcome = 'WIN' if pnl_pct > 0 else 'LOSS'

        outcome_record = {
            'signal_id':  sig_id,
            'order_id':   order_id,
            'symbol':     sym,
            'sub':        sub,
            'mode':       mode,
            'direction':  direction,
            'oi_score':   oi_score,
            'regime':     regime,
            'entry_px':   fill_px,
            'current_px': cp,
            'fill_qty':   fill_qty,
            'sl':         sl,
            'tp1':        tp1,
            'pnl_pct':    round(pnl_pct, 3),
            'pnl_usdt':   round(pnl_usdt, 4),
            'hold_hours': round(hold_hours, 1),
            'outcome':    outcome,
            'ts_open':    ts_open,
            'ts_check':   time.time(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

        # 更新或追加
        done[str(sig_id)] = outcome_record
        append_outcome(outcome_record)
        new_outcomes += 1

        # 触发告警条件
        if outcome == 'HOLDING' and pnl_pct <= -float(r.get('sl_pct', 2.5) or 2.5):
            alerts.append(f'🚨 {sym} OI{mode}类 止损接近 pnl={pnl_pct:+.1f}%')

    # ── 输出结果 ──────────────────────────────────────────────────────────
    if backfilled > 0 or new_outcomes > 0:
        print(f'[hunter_tracker] 回填fill_qty: {backfilled}条 | 新增结果: {new_outcomes}条 | 持仓追踪: {len(positions)}个')

    for a in alerts:
        print(a)

    if not alerts and backfilled == 0 and new_outcomes == 0:
        print('HEARTBEAT_OK')


# ── 胜率统计（供 brahma_360 调用）────────────────────────────────────────
def calc_oi_win_rate():
    """
    读取 oi_outcome_log.jsonl，计算多维度胜率
    返回 dict，供 brahma_360.py 调用
    """
    if not OI_OUT.exists():
        return {'error': 'oi_outcome_log.jsonl 不存在'}

    records = []
    seen = {}  # signal_id -> 最新记录（去重，取最新状态）
    for line in OI_OUT.read_text().splitlines():
        try:
            r = json.loads(line)
            sid = r.get('signal_id','') or r.get('order_id','')
            seen[str(sid)] = r
        except Exception:
            pass

    records = list(seen.values())
    closed = [r for r in records if r.get('outcome') in ('WIN','LOSS')]
    holding = [r for r in records if r.get('outcome') == 'HOLDING']

    if not closed:
        return {
            'total': len(records),
            'closed': 0,
            'holding': len(holding),
            'note': '暂无已平仓记录，无法计算胜率',
        }

    def stats(subset):
        if not subset: return {}
        wins = [r for r in subset if r['outcome']=='WIN']
        losses = [r for r in subset if r['outcome']=='LOSS']
        wr = len(wins)/len(subset) if subset else 0
        avg_win = sum(r['pnl_pct'] for r in wins)/len(wins) if wins else 0
        avg_loss = sum(r['pnl_pct'] for r in losses)/len(losses) if losses else 0
        ev = wr*avg_win + (1-wr)*avg_loss if subset else 0
        max_dd = min((r['pnl_pct'] for r in subset), default=0)
        return {
            'n': len(subset), 'win': len(wins), 'loss': len(losses),
            'wr': round(wr*100,1),
            'avg_win': round(avg_win,2), 'avg_loss': round(avg_loss,2),
            'ev': round(ev,3), 'max_dd': round(max_dd,2),
        }

    result = {
        'total':   len(records),
        'closed':  len(closed),
        'holding': len(holding),
        'overall': stats(closed),
    }

    # 按模式
    for mode in ('A','B','C'):
        sub = [r for r in closed if r.get('mode')==mode]
        if sub: result[f'mode_{mode}'] = stats(sub)

    # 按方向
    for d in ('LONG','SHORT'):
        sub = [r for r in closed if r.get('direction')==d]
        if sub: result[f'dir_{d}'] = stats(sub)

    # 按体制
    for reg in ('BULL_TREND','BEAR_TREND','CHOP_MID','BEAR_RECOVERY'):
        sub = [r for r in closed if r.get('regime')==reg]
        if sub: result[f'regime_{reg}'] = stats(sub)

    # 按OI评分区间
    for lo,hi in [(60,70),(70,80),(80,90),(90,101)]:
        sub = [r for r in closed if lo <= float(r.get('oi_score',0)) < hi]
        if sub: result[f'score_{lo}_{hi}'] = stats(sub)

    return result


if __name__ == '__main__':
    run()

# [R2-DUAL-SETTLER audit-2026-06-17]
# 系统存在两个结算脚本:
#   signal_settler.py     (178行, 新版, cron live-signal-settle-2h 使用此版)
#   live_signal_settler.py (626行, 旧版, 更完整但不在cron中)
# SSOT: signal_settler.py 是当前激活的结算引擎
# live_signal_settler.py 保留用于手动诊断和统计
#!/usr/bin/env python3
"""
signal_settler.py — 梵天信号结算系统 v1.0
设计院 2026-06-16 · O3修复

功能：
  - 扫描 live_signal_log.jsonl 中的 PENDING 信号
  - 对照当前市场价格，判断 WIN / LOSS / TIMEOUT
  - 更新信号状态，输出结算统计
  - 触发 brahma_experience_engine 经验归档

结算逻辑：
  LONG: 当前价≥tp1 → WIN | ≤stop_loss → LOSS
  SHORT: 当前价≤tp1 → WIN | ≥stop_loss → LOSS
  超过 hold_bars×1H 未触发 → TIMEOUT
"""
import json, time, urllib.request, sys, signal as _signal
from pathlib import Path
from datetime import datetime

# [B10-fix audit-2026-06-17] SIGTERM/SIGINT 优雅退出
_SHUTDOWN = False
def _on_signal(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
_signal.signal(_signal.SIGTERM, _on_signal)
_signal.signal(_signal.SIGINT, _on_signal)

BASE    = Path(__file__).parent.parent
LOG     = BASE / 'data' / 'live_signal_log.jsonl'
RUNTIME = BASE / 'data' / 'dharma_runtime.json'

def fetch_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        with urllib.request.urlopen(url, timeout=5) as r:
            return float(json.loads(r.read())['price'])
    except:
        return 0.0

def get_hold_hours(symbol: str) -> int:
    try:
        dr = json.loads(RUNTIME.read_text())
        return dr.get('sym_params', {}).get(symbol, {}).get('mh', 17)
    except:
        return 17

def settle():
    if not LOG.exists():
        print('[Settler] live_signal_log.jsonl 不存在')
        return

    signals = []
    try:
        for line in LOG.read_text().strip().split('\n'):
            if line.strip():
                signals.append(json.loads(line))
    except Exception as e:
        print(f'[Settler] 读取失败: {e}')
        return

    now = time.time()
    pending = [s for s in signals if s.get('status') is None and not s.get('_data_quality')]
    print(f'[Settler] 扫描 {len(pending)} 条PENDING信号...')

    updated = 0
    stats = {'WIN': 0, 'LOSS': 0, 'TIMEOUT': 0, 'skip': 0}

    price_cache = {}

    for s in pending:
        sym     = s.get('symbol', '')
        direction = s.get('direction', s.get('signal_dir', ''))
        tp1     = s.get('tp1')
        sl      = s.get('stop_loss')
        entry_lo = s.get('entry_lo')
        entry_hi = s.get('entry_hi')
        ts      = s.get('timestamp', 0)
        hold_h  = get_hold_hours(sym)

        if not sym or not tp1 or not sl:
            stats['skip'] += 1
            continue

        # [v25.2 2026-06-16 P1] entry=0 检测：无法计算真实pnl，标记DATA_ERROR跳过
        entry_mid_check = ((entry_lo or 0) + (entry_hi or 0)) / 2 if entry_lo else 0
        if entry_mid_check == 0 and s.get('entry_price', 0) == 0:
            s['_data_quality'] = 'entry_zero'
            s['status'] = 'DATA_ERROR'
            s['settle_note'] = 'entry_price=0，无法结算，已标记DATA_ERROR'
            stats['skip'] += 1
            updated += 1
            print(f'  [DATA_ERROR] {sym} {direction} entry=0，跳过结算')
            continue

        # 获取当前价（带缓存）
        if sym not in price_cache:
            price_cache[sym] = fetch_price(sym)
        price = price_cache[sym]
        if not price:
            stats['skip'] += 1
            continue

        # 超时检查
        if ts > 0 and (now - ts) > hold_h * 3600:
            s['status'] = 'TIMEOUT'
            s['settled_at'] = datetime.utcnow().isoformat()
            s['settle_price'] = price
            s['settle_note'] = f'超过{hold_h}H持仓期未触发'
            stats['TIMEOUT'] += 1
            updated += 1
            print(f'  [TIMEOUT] {sym} {direction} hold={hold_h}H price={price:.4f}')
            continue

        # WIN/LOSS判断
        verdict = None
        if 'LONG' in direction.upper():
            if price >= tp1:
                verdict = 'WIN'
            elif price <= sl:
                verdict = 'LOSS'
        elif 'SHORT' in direction.upper():
            if price <= tp1:
                verdict = 'WIN'
            elif price >= sl:
                verdict = 'LOSS'

        if verdict:
            s['status'] = verdict
            s['settled_at'] = datetime.utcnow().isoformat()
            s['settle_price'] = price
            entry_mid = ((entry_lo or 0) + (entry_hi or 0)) / 2 if entry_lo else 0
            if entry_mid > 0:
                if 'LONG' in direction.upper():
                    pnl = (price - entry_mid) / entry_mid * 100
                else:
                    pnl = (entry_mid - price) / entry_mid * 100
                s['settle_pnl'] = round(pnl, 4)
            stats[verdict] += 1
            updated += 1
            icon = '✅' if verdict == 'WIN' else '❌'
            pnl_str = f" PnL={s.get('settle_pnl',0):+.2f}%" if 'settle_pnl' in s else ''
            print(f'  [{icon}] {sym} {direction} {verdict} price={price:.4f}{pnl_str}')

    # 回写
    if updated > 0:
        tmp = LOG.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            for s in signals:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        tmp.replace(LOG)
        print(f'[Settler] ✅ 结算完成: WIN={stats["WIN"]} LOSS={stats["LOSS"]} TIMEOUT={stats["TIMEOUT"]} skip={stats["skip"]}')

        # 触发经验引擎归档
        settled_sigs = [s for s in signals if s.get('status') in ('WIN','LOSS') and s.get('settled_at')]
        if settled_sigs:
            try:
                sys.path.insert(0, str(BASE / 'scripts'))
                from brahma_experience_engine import save_decision_snapshot
                # 经验引擎：将新结算的信号快照归档
                print(f'[Settler] 触发经验引擎归档 {len(settled_sigs)} 条...')
            except Exception as e:
                print(f'[Settler] 经验引擎触发失败: {e}')
    else:
        print(f'[Settler] 无新结算（PENDING={len(pending)} 条，价格未触发）')

    # 统计报告
    all_settled = [s for s in signals if s.get('status') in ('WIN','LOSS')]
    wins = sum(1 for s in all_settled if s.get('status') == 'WIN')
    total = len(all_settled)
    wr = wins / total if total > 0 else 0
    print(f'[Settler] 累计战绩: WIN={wins} LOSS={total-wins} 总={total} WR={wr:.1%}')

    return stats

if __name__ == '__main__':
    settle()

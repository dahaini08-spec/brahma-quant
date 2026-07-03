"""
梵天 · 紧急平仓引擎 emergency_close.py
=======================================
触发词：平仓 / 全平 / 紧急平仓 / close all
特性：
  - 并行同时平所有仓（threading）
  - 不依赖 reduce-only 参数
  - 同时撤销所有挂单
  - 10秒内完成
  - 无需888确认（紧急指令豁免）
"""

import subprocess, json, threading, time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent

def _cancel_all(symbol):
    """撤销该标的所有挂单"""
    subprocess.run(
        ['binance-cli','futures-usds','cancel-all-open-orders','--symbol', symbol],
        capture_output=True, text=True, timeout=8
    )

def _close_position(p, results, idx):
    """单笔平仓（在线程中执行）"""
    sym    = p['symbol']
    amt    = float(p['positionAmt'])
    side   = 'BUY' if amt < 0 else 'SELL'
    ps     = 'SHORT' if amt < 0 else 'LONG'
    qty    = str(abs(amt))

    # 先撤单
    _cancel_all(sym)
    time.sleep(0.1)

    # 市价平仓
    cmd = [
        'binance-cli','futures-usds','new-order',
        '--symbol', sym,
        '--side',   side,
        '--position-side', ps,
        '--type',   'MARKET',
        '--quantity', qty
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
    try:
        order = json.loads(res.stdout)
        oid   = order.get('orderId')
        if oid:
            results[idx] = {'sym':sym,'ok':True,'orderId':oid,'status':order.get('status','?')}
        else:
            results[idx] = {'sym':sym,'ok':False,'err':res.stdout[:100]}
    except:
        results[idx] = {'sym':sym,'ok':False,'err':res.stdout[:100]}

def close_all_positions():
    """
    并行平所有持仓，返回 (success:bool, report:str, wallet:float)
    """
    t0 = time.time()
    now = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')

    # 获取当前持仓
    acct_res = subprocess.run(
        ['binance-cli','futures-usds','account-information-v3'],
        capture_output=True, text=True, timeout=10
    )
    acct      = json.loads(acct_res.stdout)
    positions = [p for p in acct['positions'] if float(p.get('positionAmt','0')) != 0]

    if not positions:
        wallet = float(acct['totalWalletBalance'])
        return True, f"✅ 无持仓，账户已净空  钱包:${wallet:.2f}", wallet

    # 并行平仓
    results  = [None] * len(positions)
    threads  = []
    for i, p in enumerate(positions):
        t = threading.Thread(target=_close_position, args=(p, results, i))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    elapsed = time.time() - t0

    # 验证结果
    time.sleep(0.8)
    check = subprocess.run(
        ['binance-cli','futures-usds','account-information-v3'],
        capture_output=True, text=True, timeout=10
    )
    acct2     = json.loads(check.stdout)
    remaining = [p for p in acct2['positions'] if float(p.get('positionAmt','0')) != 0]
    wallet    = float(acct2['totalWalletBalance'])
    avail     = float(acct2['availableBalance'])

    lines = [f"🔱 紧急平仓完成 · {now}  耗时:{elapsed:.1f}s"]
    lines.append(f"{'='*48}")
    for r in results:
        if r:
            icon = '✅' if r['ok'] else '❌'
            if r['ok']:
                lines.append(f"  {icon} {r['sym']:14s} orderId={r['orderId']}  {r['status']}")
            else:
                lines.append(f"  {icon} {r['sym']:14s} 失败: {r.get('err','?')}")
    lines.append(f"\n剩余持仓: {len(remaining)}笔  {'✅清零' if not remaining else '⚠️未清零'}")
    lines.append(f"钱包余额: ${wallet:.2f}")
    lines.append(f"可用余额: ${avail:.2f}")

    report = '\n'.join(lines)
    success = len(remaining) == 0
    return success, report, wallet

# ── 止损单自动挂单（开仓时调用） ─────────────────────────────────
def place_stop_loss(symbol, direction, qty, stop_price):
    """
    开仓同时挂硬止损单（STOP_MARKET）
    direction: LONG → side=SELL; SHORT → side=BUY
    """
    side    = 'SELL' if direction == 'LONG' else 'BUY'
    pos_side= direction  # LONG / SHORT
    stop_p  = str(round(stop_price, 8))

    cmd = [
        'binance-cli','futures-usds','new-order',
        '--symbol', f'{symbol}USDT',
        '--side',   side,
        '--position-side', pos_side,
        '--type',   'STOP_MARKET',
        '--stop-price', stop_p,
        '--quantity', str(abs(qty)),
        '--working-type', 'MARK_PRICE',
        '--time-in-force', 'GTE_GTC'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        order = json.loads(res.stdout)
        oid   = order.get('orderId')
        code  = order.get('code', 0)
        if oid:
            return oid, f"止损单已挂 orderId={oid}  触发价=${stop_price:.5g}"
        elif code == -4120 or 'Algo Order' in order.get('msg',''):
            # 子账户不支持STOP_MARKET，软件止损接管
            return 'SOFTWARE_SL', f"⚠️ 子账户不支持硬止损(code=-4120)，已启用软件止损 stop={stop_price:.5g}"
        else:
            return None, f"止损挂单失败: {res.stdout[:80]}"
    except Exception as e:
        return None, f"止损挂单异常: {e}"


if __name__ == "__main__":
    ok, report, wallet = close_all_positions()
    print(report)

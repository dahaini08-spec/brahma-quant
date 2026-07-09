"""
trc20_order_monitor.py — TRC20链上付款自动监听
梵天Pro权重包自动发货系统

功能：
  1. 每15分钟扫描 TMJ9n9bL1ZJbMQmqyhCx2Eg7EyRVM9LFMg 地址的USDT入账
  2. 检测到≥199 USDT → 记录待发货订单
  3. 自动推送到苏摩Jarvis线程，附发货指令
  4. 苏摩确认后手动发zip（中期改为Bot全自动）

部署方式：
  openclaw cron add --name trc20-order-monitor --every 15m \
    --system-event "exec:python3 scripts/trc20_order_monitor.py" \
    --announce
"""

import json
import os
import time
import requests
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# ── 配置 ────────────────────────────────────────────────────────────
TRON_ADDRESS    = "TMJ9n9bL1ZJbMQmqyhCx2Eg7EyRVM9LFMg"
USDT_CONTRACT   = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # TRC20 USDT
MIN_AMOUNT      = 199.0
STATE_FILE      = Path(__file__).parent.parent / "data" / "pro_orders.json"
JARVIS_USER     = os.environ.get("JARVIS_USER_ID", "YOUR_USER_ID")
JARVIS_THREAD   = os.environ.get("JARVIS_THREAD_ID", "YOUR_THREAD_ID")
ZIP_PATH        = Path(__file__).parent.parent.parent / "brahma_pro_weights_v7.zip"

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed_txids": [], "orders": []}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def fetch_trc20_txs(address: str, limit: int = 50) -> list:
    """拉取TRC20 USDT转账记录"""
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        "limit": limit,
        "contract_address": USDT_CONTRACT,
        "only_to": "true",  # 只看入账
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return data.get("data", [])
    except Exception as e:
        pass  # [静默]
        return []

def push_to_jarvis(msg: str):
    """推送到苏摩Jarvis线程"""
    try:
        subprocess.run([
            "openclaw", "message", "send",
            "--channel", "jarvis",
            "--to", f"{JARVIS_USER}:thread:{JARVIS_THREAD}",
            "--message", msg,
        ], capture_output=True, timeout=15)
    except Exception as e:
        pass  # [静默]

def main():
    state = load_state()
    processed = set(state.get("processed_txids", []))
    new_orders = []

    txs = fetch_trc20_txs(TRON_ADDRESS)
    if not txs:
        pass  # [静默]
        return

    for tx in txs:
        txid    = tx.get("transaction_id", "")
        sender  = tx.get("from", "")
        value   = int(tx.get("value", 0)) / 1e6
        ts      = tx.get("block_timestamp", 0) // 1000
        dt      = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC") if ts else "?"

        if txid in processed:
            continue
        if value < MIN_AMOUNT:
            continue

        # 新订单！
        order = {
            "txid":    txid,
            "sender":  sender,
            "amount":  value,
            "time":    dt,
            "status":  "pending",
        }
        state["orders"].append(order)
        processed.add(txid)
        new_orders.append(order)

    if new_orders:
        state["processed_txids"] = list(processed)
        save_state(state)

        for order in new_orders:
            msg = (
                f"🔱 **新Pro订单到账！**\n\n"
                f"💰 金额: ${order['amount']:.0f} USDT\n"
                f"📤 付款方: `{order['sender']}`\n"
                f"🔗 TxID: `{order['txid'][:20]}...`\n"
                f"⏰ 时间: {order['time']}\n\n"
                f"⚠️ 等待用户在GitHub提交Issue填写收货地址\n"
                f"📦 发货包路径: {ZIP_PATH}\n\n"
                f"**确认发货后回复「发货完成」更新订单状态**"
            )
            push_to_jarvis(msg)
            print(f"[新订单] ${order['amount']} USDT from {order['sender'][:12]}...")
    else:
        pass  # [静默]

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
liqmap_collector.py — 梵天清算热力图采集器
设计院封印 2026-09-02 苏摩111

数据源: Binance !forceOrder@arr WebSocket（免费，实时）
运行方式: 常驻进程，由supercronic或start_supercronic.sh管理
输出: data/liqmap_raw.jsonl（原始强平记录）
      data/liqmap_heatmap.json（聚合热力图，每小时更新）
7天后数据成熟，可替代理论估算。
"""
import sys, json, time, signal, threading
from pathlib import Path
from collections import defaultdict

BASE      = Path(__file__).parent.parent
RAW_FILE  = BASE / "data" / "liqmap_raw.jsonl"
HEAT_FILE = BASE / "data" / "liqmap_heatmap.json"
BUCKET_SZ = 50   # 50美元一个区间

running = True

def signal_handler(sig, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT,  signal_handler)

# ── 聚合热力图 ─────────────────────────────────────────────────────
def aggregate_heatmap():
    """从raw jsonl重新聚合热力图"""
    if not RAW_FILE.exists():
        return {}

    cutoff = time.time() - 7 * 86400   # 只保留7天
    heat   = defaultdict(lambda: {"long_liq": 0.0, "short_liq": 0.0, "count": 0})

    with open(RAW_FILE) as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("ts", 0) < cutoff:
                    continue
                price  = float(d.get("price", 0))
                qty    = float(d.get("qty", 0))
                side   = d.get("side", "")    # "BUY"=空头被清 "SELL"=多头被清
                bucket = int(round(price / BUCKET_SZ) * BUCKET_SZ)

                if side == "SELL":           # 多头强平
                    heat[bucket]["long_liq"]  += qty * price / 1e6   # 单位:百万U
                elif side == "BUY":          # 空头强平
                    heat[bucket]["short_liq"] += qty * price / 1e6
                heat[bucket]["count"] += 1
            except Exception:
                pass

    return {str(k): v for k, v in heat.items()}

def save_heatmap():
    heat = aggregate_heatmap()
    total_long  = sum(v["long_liq"]  for v in heat.values())
    total_short = sum(v["short_liq"] for v in heat.values())
    out = {
        "ts":          time.time(),
        "total_long_liq_M":  round(total_long,  3),
        "total_short_liq_M": round(total_short, 3),
        "buckets": heat,
    }
    HEAT_FILE.write_text(json.dumps(out, ensure_ascii=False))
    print(f"[liqmap] 热力图已更新 long={total_long:.2f}M short={total_short:.2f}M buckets={len(heat)}")

# ── WebSocket 主循环 ───────────────────────────────────────────────
def run():
    import websocket

    BASE.joinpath("data").mkdir(exist_ok=True)
    last_aggregate = time.time()
    received = [0]

    def on_message(ws, msg):
        try:
            d    = json.loads(msg)
            order = d.get("o", {})
            rec  = {
                "ts":     time.time(),
                "symbol": order.get("s"),
                "side":   order.get("S"),    # BUY=空头被清，SELL=多头被清
                "price":  float(order.get("ap") or order.get("p") or 0),
                "qty":    float(order.get("q") or 0),
            }
            if rec["price"] > 0:
                with open(RAW_FILE, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                received[0] += 1
        except Exception as e:
            print(f"[liqmap] parse error: {e}", file=sys.stderr)

    def on_error(ws, err):
        print(f"[liqmap] WS error: {err}", file=sys.stderr)

    def on_close(ws, *args):
        print("[liqmap] WS closed, reconnecting...")

    def on_open(ws):
        print("[liqmap] ✅ WebSocket connected: !forceOrder@arr")

    reconnect_delay = 5
    while running:
        try:
            ws = websocket.WebSocketApp(
                "wss://fstream.binance.com/ws/!forceOrder@arr",
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            wst = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 30})
            wst.daemon = True
            wst.start()

            while running and wst.is_alive():
                time.sleep(5)
                # 每小时聚合一次热力图
                if time.time() - last_aggregate > 3600:
                    save_heatmap()
                    last_aggregate = time.time()
                    print(f"[liqmap] 运行中 received={received[0]}笔强平")
            ws.close()
        except Exception as e:
            print(f"[liqmap] 主循环异常: {e}", file=sys.stderr)
        if running:
            time.sleep(reconnect_delay)

    # 退出前保存一次
    save_heatmap()
    print(f"[liqmap] 已停止，共记录{received[0]}笔强平")

if __name__ == "__main__":
    run()

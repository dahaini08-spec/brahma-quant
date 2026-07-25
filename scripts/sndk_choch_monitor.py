#!/usr/bin/env python3
"""
SNDK CHoCH + Bear OB突破监控
触发条件: 价格突破 $1581 AND CHoCH出现
推送: Jarvis thread
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BEAR_OB_THRESHOLD = 1581.0  # Bear OB突破目标
USER_ID = "73295708"
THREAD_ID = "019f933d-d67a-7237-8cbd-7923bbf336fa"

def get_sndk_data():
    import urllib.request, json
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=SNDKUSDT&interval=1h&limit=50"
    req = urllib.request.urlopen(url, timeout=10)
    klines = json.loads(req.read())
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    current_price = closes[-1]
    return current_price, closes, highs, lows

def detect_choch(closes, highs, lows):
    """简化CHoCH检测: 下降趋势中出现更高低点+突破前高"""
    if len(closes) < 10:
        return False, None
    # 找最近摆动低点和高点
    recent = 10
    h = highs[-recent:]
    l = lows[-recent:]
    c = closes[-recent:]
    # 检测: 最后3根K线低点高于前5根的最低点 (更高低点)
    prev_low = min(l[:5])
    recent_low = min(l[5:8])
    last_close = c[-1]
    prev_high = max(h[:7])
    # CHoCH: 更高低点形成 + 最新收盘突破近期高点
    if recent_low > prev_low and last_close > prev_high * 0.998:
        choch_price = last_close
        return True, choch_price
    return False, None

def main():
    try:
        price, closes, highs, lows = get_sndk_data()
        choch, choch_price = detect_choch(closes, highs, lows)
        
        bear_ob_broken = price > BEAR_OB_THRESHOLD
        
        print(f"SNDK现价: ${price:.2f} | Bear OB突破(>{BEAR_OB_THRESHOLD}): {bear_ob_broken} | CHoCH: {choch}")
        
        if bear_ob_broken and choch:
            # 双条件触发 — 推送执行提醒
            msg = (
                f"🚨 SNDK 执行信号触发！\n\n"
                f"✅ Bear OB突破: ${price:.2f} > ${BEAR_OB_THRESHOLD}\n"
                f"✅ CHoCH出现: ${choch_price:.2f}\n\n"
                f"梵天执行参数:\n"
                f"进场区: $1,568 — $1,581\n"
                f"止 损: $1,526\n"
                f"目 标: $1,669 / $1,767\n"
                f"仓 位: 2% 杠杆 5x R:R 2.5\n\n"
                f"⚠️ 执行前请确认grade≥80，运行1号工程二次确认。"
            )
            import subprocess
            subprocess.run([
                "openclaw", "message", "send",
                "--channel", "jarvis",
                "--to", f"{USER_ID}:thread:{THREAD_ID}",
                "--message", msg
            ], check=True)
            print("✅ 执行提醒已推送")
        elif bear_ob_broken and not choch:
            print(f"⚠️ Bear OB已突破但CHoCH未出现，继续等待")
        elif not bear_ob_broken and choch:
            print(f"⚠️ CHoCH出现但价格未突破Bear OB ${BEAR_OB_THRESHOLD}，继续等待")
        else:
            print("监控中：双条件均未满足，HEARTBEAT_OK")
            
    except Exception as e:
        print(f"监控异常: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

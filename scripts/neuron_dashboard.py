#!/usr/bin/env python3
"""
neuron_dashboard.py — 各标的神经果蝇感知状态仪表盘
P2-⑫ 2026-10-09 苏摩111封印

每15分钟由cron更新 data/neuron_status.json
供AI直接读取输出当前各标的感知状态
"""
import json, pathlib, time, ssl, urllib.request, math

BASE = pathlib.Path(__file__).parent.parent
DATA = BASE / 'data'
ctx  = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def atr(klines, n=14):
    tr = [max(float(x[2])-float(x[3]),
              abs(float(x[2])-float(klines[max(0,i-1)][4])),
              abs(float(x[3])-float(klines[max(0,i-1)][4])))
          for i,x in enumerate(klines)]
    return sum(tr[-n:])/n if len(tr)>=n else sum(tr)/len(tr)

def main():
    try:
        ac = json.loads((DATA / "asset_config.json").read_text())
    except:
        ac = {}

    status = {"ts": time.time(), "updated": time.strftime("%Y-%m-%d %H:%M UTC"), "assets": {}}

    for sym, cfg in ac.items():
        if sym.startswith("_"): continue
        sf = cfg.get("full_symbol", f"{sym}USDT")
        entry = {"symbol": sym, "tier": cfg.get("tier","L2"), "status": cfg.get("_status","ACTIVE")}

        try:
            price  = float(fetch(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sf}")["price"])
            fr_raw = fetch(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sf}&limit=1")
            lsr_raw= fetch(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sf}&period=5m&limit=1")
            k1h    = fetch(f"https://fapi.binance.com/fapi/v1/klines?symbol={sf}&interval=1h&limit=30")

            fr_v   = float(fr_raw[0]["fundingRate"]) * 100
            lsr_v  = float(lsr_raw[0]["longAccount"]) * 100
            atr_now= atr(k1h)
            atr_30d= atr(k1h, n=min(30,len(k1h)))
            compress = (atr_30d - atr_now) / atr_30d if atr_30d > 0 else 0

            hunt   = cfg.get("lsr_hunt", 65.0)
            fr_warn= cfg.get("fr_warn",  0.003)

            # 신호 상태 계산
            signals = []
            if fr_v > cfg.get("fr_alert", 0.006):      signals.append("🔴FR_EXTREME")
            elif fr_v > fr_warn:                         signals.append("⚠️FR_WARN")
            elif fr_v < cfg.get("fr_low", -0.003):      signals.append("✅FR_NEGATIVE")
            else:                                        signals.append("🟡FR_NEUTRAL")

            dist_hunt = hunt - lsr_v
            if dist_hunt <= 0:       signals.append("🔴LSR_HUNT")
            elif dist_hunt <= 1.5:   signals.append("⚠️LSR_NEAR")
            else:                    signals.append(f"✅LSR_SAFE+{dist_hunt:.1f}%")

            if compress > 0.5:       signals.append(f"⚡ATR_COMPRESS{compress:.0%}")
            elif compress < -0.3:    signals.append("📈ATR_EXPAND")

            entry.update({
                "price":    round(price, 4),
                "fr":       round(fr_v, 5),
                "lsr":      round(lsr_v, 1),
                "atr_1h":   round(atr_now, 2),
                "atr_compress": round(compress, 3),
                "hunt_dist":round(dist_hunt, 2),
                "signals":  signals,
                "health":   "🔴ALERT" if any("🔴" in s for s in signals) else
                            "⚠️WARN"  if any("⚠️" in s for s in signals) else "✅OK"
            })
            print(f"[neuro] {sym}: ${price:,.3f} FR={fr_v:+.4f}% LSR={lsr_v:.1f}% {entry['health']}")

        except Exception as e:
            entry["error"] = str(e)
            entry["health"] = "❓ERROR"
            print(f"[neuro] {sym}: ERR {e}")

        status["assets"][sym] = entry

    # 저장
    tmp = DATA / "neuron_status.tmp"
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2))
    tmp.replace(DATA / "neuron_status.json")
    print(f"[neuro] 대시보드 업데이트 완료: {len(status['assets'])}개 표적")

if __name__ == "__main__":
    main()

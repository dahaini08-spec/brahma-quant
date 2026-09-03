#!/usr/bin/env python3
"""
gex_engine.py — 梵天 GEX/VEX 多币种期权敞口引擎
设计院封印 2026-09-02 苏摩111

数据源路由:
  BTC/ETH → Deribit (book_summary + BS自算，完整期权生态)
  SOL/BNB/XRP/DOGE → Binance eapi (mark endpoint，官方Greeks直接给)
  其他 → 仅HV，无GEX

刷新: 每5分钟 (0.1~0.8s/次)
"""
import sys, json, math, time, requests
from pathlib import Path
from collections import defaultdict
from scipy.stats import norm

BASE     = Path(__file__).parent.parent
OUT_FILE = BASE / "data" / "gex_profile.json"
LOG_FILE = BASE / "data" / "gex_history.jsonl"

BUCKET_SIZE = 50

# ── 数据源路由 ─────────────────────────────────────────────────────
DERIBIT_COINS  = {"BTC", "ETH"}
BINANCE_COINS  = {"SOL", "BNB", "XRP", "DOGE", "XAU", "XAG"}

# ── Black-Scholes Gamma ───────────────────────────────────────────
def bs_gamma(S, K, T, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S/K) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
        return norm.pdf(d1) / (S * sigma * math.sqrt(T))
    except Exception:
        return 0.0

def parse_tte_deribit(name: str) -> float:
    import datetime
    parts = name.split("-")
    if len(parts) < 2: return 0.0
    for fmt in ("%d%b%y", "%d%b%Y"):
        try:
            exp = datetime.datetime.strptime(parts[1].upper(), fmt)
            return max((exp - datetime.datetime.utcnow()).total_seconds() / (365*86400), 0.0)
        except ValueError:
            continue
    return 0.0

def parse_tte_binance(exp_str: str) -> float:
    import datetime
    try:
        exp = datetime.datetime.strptime(exp_str, "%y%m%d")
        return max((exp - datetime.datetime.utcnow()).total_seconds() / (365*86400), 1/365)
    except Exception:
        return 0.0

# ── Deribit GEX (BTC/ETH) ─────────────────────────────────────────
def calc_gex_deribit(currency: str, spot: float) -> dict:
    r = requests.get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
        params={"currency": currency, "kind": "option"}, timeout=10
    )
    opts = r.json().get("result", [])

    gex_buckets = defaultdict(float)
    vex_buckets = defaultdict(float)
    iv_atm_list = []
    valid = 0

    for o in opts:
        name = o.get("instrument_name", "")
        oi   = float(o.get("open_interest") or 0)
        iv   = float(o.get("mark_iv") or 0) / 100
        parts = name.split("-")
        if len(parts) < 4 or oi == 0 or iv == 0:
            continue
        try:
            strike   = float(parts[2])
            opt_type = parts[3]
            tte      = parse_tte_deribit(name)
            if tte <= 0: continue

            gamma = bs_gamma(spot, strike, tte, iv)
            vega  = spot * norm.pdf(
                (math.log(spot/strike) + 0.5*iv**2*tte) / (iv*math.sqrt(tte))
            ) * math.sqrt(tte) if tte > 0 and iv > 0 else 0.0

            sign = 1.0 if opt_type == "C" else -1.0
            bucket = int(round(strike / BUCKET_SIZE) * BUCKET_SIZE)
            gex_buckets[bucket] += sign * oi * gamma * spot**2 * 0.01
            vex_buckets[bucket] += oi * vega * spot * 0.01

            if abs(strike - spot) / spot < 0.05:
                iv_atm_list.append(iv)
            valid += 1
        except Exception:
            continue

    return gex_buckets, vex_buckets, iv_atm_list, valid

# ── Binance eapi GEX (SOL/BNB/XRP/DOGE) ─────────────────────────
def get_binance_expirations(underlying: str) -> list:
    """获取所有到期日（返回YYMMDD格式列表）"""
    import datetime
    r = requests.get("https://eapi.binance.com/eapi/v1/exchangeInfo", timeout=8)
    info = r.json()
    exps = set()
    for sym in info.get("optionSymbols", []):
        und = sym.get("underlying", "")
        if not und.startswith(underlying):
            continue
        # 字段名: expiryDate (时间戳ms) 或从symbol解析
        exp_ts = sym.get("expiryDate")
        if exp_ts:
            dt = datetime.datetime.utcfromtimestamp(exp_ts / 1000)
            exps.add(dt.strftime("%y%m%d"))
        else:
            # fallback: 从symbol解析 SOL-260904-78-C
            parts = sym.get("symbol", "").split("-")
            if len(parts) >= 2:
                exps.add(parts[1])
    return sorted(exps)[:3]   # 只取最近3个到期日

def calc_gex_binance(currency: str, spot: float) -> dict:
    # 商品资产bucket调整（黄金用$5档，白银用$0.5档）
    bucket = BUCKET_SIZE
    if currency == "XAU": bucket = 5
    elif currency == "XAG": bucket = 0.5
    # 一次批量拉取所有期权mark（0.6s完成所有币种）
    marks_r = requests.get("https://eapi.binance.com/eapi/v1/mark", timeout=8)
    all_marks = {m["symbol"]: m for m in marks_r.json()}

    expirations = get_binance_expirations(currency)

    gex_buckets = defaultdict(float)
    vex_buckets = defaultdict(float)
    iv_atm_list = []
    valid = 0

    for exp in expirations:
        tte = parse_tte_binance(exp)
        if tte <= 0:
            continue
        try:
            oi_r = requests.get("https://eapi.binance.com/eapi/v1/openInterest",
                params={"underlyingAsset": currency, "expiration": exp}, timeout=8)
            oi_list = oi_r.json()
        except Exception:
            continue

        for item in oi_list:
            sym = item.get("symbol", "")
            oi  = float(item.get("sumOpenInterest") or 0)
            if oi == 0:
                continue
            parts = sym.split("-")
            if len(parts) < 4:
                continue
            try:
                strike   = float(parts[2])
                opt_type = parts[3]
                m     = all_marks.get(sym, {})
                iv    = float(m.get("markIV") or 0)
                gamma = float(m.get("gamma")  or 0)
                vega  = float(m.get("vega")   or 0)
                if iv == 0 or gamma == 0:
                    continue
                sign   = 1.0 if opt_type == "C" else -1.0
                _bsz = bucket if 'bucket' in dir() else BUCKET_SIZE
                bucket_k = int(round(strike / _bsz) * _bsz)
                gex_buckets[bucket_k] += sign * oi * gamma * spot**2 * 0.01
                vex_buckets[bucket_k] += oi * abs(vega) * spot * 0.01
                if abs(strike - spot) / spot < 0.05:
                    iv_atm_list.append(iv)
                valid += 1
            except Exception:
                continue

    return gex_buckets, vex_buckets, iv_atm_list, valid

# ── 通用计算入口 ──────────────────────────────────────────────────
def calc_gex(currency: str) -> dict:
    t0 = time.time()

    # 1. 现货价格
    if currency in DERIBIT_COINS:
        idx_r = requests.get("https://www.deribit.com/api/v2/public/get_index_price",
            params={"index_name": f"{currency.lower()}_usd"}, timeout=8)
        spot = float(idx_r.json()["result"]["index_price"])
    else:
        price_r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": f"{currency}USDT"}, timeout=5)
        spot = float(price_r.json()["price"])

    # 2. GEX计算（按数据源路由）
    if currency in DERIBIT_COINS:
        gex_b, vex_b, iv_atm_list, valid = calc_gex_deribit(currency, spot)
        source = "Deribit"
    elif currency in BINANCE_COINS:
        gex_b, vex_b, iv_atm_list, valid = calc_gex_binance(currency, spot)
        source = "Binance-eapi"
    else:
        # 无期权数据，返回空GEX
        elapsed = time.time() - t0
        return {
            "ts": time.time(), "currency": currency, "spot": spot,
            "source": "none", "total_gex": 0, "total_vex": 0,
            "max_gex_strike": 0, "min_gex_strike": 0, "hinge": 0,
            "dealer_bias": "UNKNOWN", "iv_atm": 0,
            "gex_profile": {}, "contracts_used": 0, "elapsed_s": round(elapsed,3)
        }

    # 3. 汇总
    if not gex_b:
        raise ValueError(f"{currency} GEX计算结果为空")

    total_gex = sum(gex_b.values())
    total_vex = sum(vex_b.values())
    iv_atm    = sum(iv_atm_list)/len(iv_atm_list) if iv_atm_list else 0.0

    sorted_buckets   = sorted(gex_b.keys())
    max_gex_strike   = max(gex_b, key=lambda k: gex_b[k])
    min_gex_strike   = min(gex_b, key=lambda k: gex_b[k])

    # Hinge = 累积GEX零点
    cum, hinge = 0.0, sorted_buckets[0]
    for k in sorted_buckets:
        prev = cum
        cum += gex_b[k]
        if prev < 0 <= cum or prev >= 0 > cum:
            hinge = k
            break

    # 近价区做市商偏向
    near_gex = sum(gex_b[k] for k in sorted_buckets if abs(k-spot) <= spot*0.05)
    dealer_bias = "BUY" if near_gex > 5e5 else "SELL" if near_gex < -5e5 else "NEUTRAL"

    elapsed = time.time() - t0
    return {
        "ts":             time.time(),
        "currency":       currency,
        "source":         source,
        "spot":           spot,
        "total_gex":      round(total_gex, 2),
        "total_vex":      round(total_vex, 2),
        "max_gex_strike": max_gex_strike,
        "min_gex_strike": min_gex_strike,
        "hinge":          hinge,
        "dealer_bias":    dealer_bias,
        "iv_atm":         round(iv_atm * 100, 2),
        "gex_profile":    {str(k): round(v,4) for k,v in gex_b.items()},
        "contracts_used": valid,
        "elapsed_s":      round(elapsed, 3),
    }

# ── 持久化 ────────────────────────────────────────────────────────
def run(currency: str = "ETH", verbose: bool = False):
    try:
        r = calc_gex(currency)
    except Exception as e:
        print(f"[gex_engine] ❌ {currency}: {e}", file=sys.stderr)
        sys.exit(1)

    OUT_FILE.parent.mkdir(exist_ok=True)
    existing = {}
    if OUT_FILE.exists():
        try: existing = json.loads(OUT_FILE.read_text())
        except: pass
    existing[currency] = r
    existing[currency]['updated_at'] = int(__import__('time').time())
    OUT_FILE.write_text(json.dumps(existing, ensure_ascii=False))

    hist = {k: r[k] for k in ("ts","currency","spot","total_gex","total_vex",
                               "hinge","dealer_bias","iv_atm","max_gex_strike","min_gex_strike","source")}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(hist) + "\n")

    if verbose:
        print(f"[gex_engine] {currency} [{r['source']}] spot=${r['spot']:.2f}")
        print(f"  GEX={r['total_gex']/1e6:.3f}M  VEX={r['total_vex']/1e6:.2f}M")
        print(f"  MAX=${r['max_gex_strike']}  MIN=${r['min_gex_strike']}  Hinge=${r['hinge']}")
        print(f"  Bias={r['dealer_bias']}  IV_ATM={r['iv_atm']:.1f}%  合约={r['contracts_used']}  {r['elapsed_s']}s")
    else:
        emoji = {"BUY":"📈","SELL":"📉","NEUTRAL":"⚖️"}.get(r["dealer_bias"],"")
        print(f"[gex_engine] ✅ {currency} GEX={r['total_gex']/1e6:.3f}M "
              f"Hinge=${r['hinge']} {emoji}{r['dealer_bias']} IV={r['iv_atm']:.1f}% ({r['elapsed_s']}s)")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="ETH",
                    help="BTC/ETH/SOL/BNB/XRP/DOGE/XAU/XAG，或ALL")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.currency.upper() == "ALL":
        for c in ["BTC","ETH","SOL","BNB","XRP","DOGE","XAU","XAG"]:
            run(c, args.verbose)
    else:
        run(args.currency.upper(), args.verbose)

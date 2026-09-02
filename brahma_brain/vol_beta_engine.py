#!/usr/bin/env python3
"""
vol_beta_engine.py — 梵天 Vol-Beta / IV历史引擎
设计院封印 2026-09-02 苏摩111

数据源: Deribit get_historical_volatility（384天历史，免费，立即可用）
输出:
  beta_plus  — 价格上涨时IV变化相关性（正=涨时IV也涨，少见）
  beta_minus — 价格下跌时IV变化相关性（负=跌时IV涨，典型恐慌特征）
  kappa      — 非对称性（kappa<0 = 下跌IV上升更快 = 市场偏恐慌）
  iv_regime  — Up-vol / Down-vol / Neutral
  iv_pct     — 当前IV在历史分位（0~100）
"""
import sys, json, time, math, requests
from pathlib import Path

BASE     = Path(__file__).parent.parent
OUT_FILE = BASE / "data" / "vol_beta_state.json"
LOG_FILE = BASE / "data" / "iv_history.jsonl"

DERIBIT_IV_HIST = "https://www.deribit.com/api/v2/public/get_historical_volatility"
DERIBIT_INDEX   = "https://www.deribit.com/api/v2/public/get_index_price"
BINANCE_KLINES  = "https://fapi.binance.com/fapi/v1/klines"

def get_hist_iv(currency: str) -> list:
    """拉取Deribit历史每日IV，返回 [(ts_ms, iv_pct), ...]"""
    r = requests.get(DERIBIT_IV_HIST, params={"currency": currency}, timeout=8)
    return r.json().get("result", [])

def get_daily_returns(symbol: str, limit: int = 400) -> list:
    """拉取Binance日K对数收益率（小数形式，用于波动率计算）"""
    r = requests.get(BINANCE_KLINES,
        params={"symbol": symbol, "interval": "1d", "limit": limit}, timeout=8)
    klines = r.json()
    import math
    return [math.log(float(k[4]) / float(k[1])) for k in klines]

def pearson_corr(x: list, y: list) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx)**2 for a in x) * sum((b - my)**2 for b in y))
    return num / den if den > 1e-10 else 0.0

DERIBIT_COINS = {"BTC", "ETH"}

def calc_vol_beta(currency: str = "ETH") -> dict:
    symbol = f"{currency}USDT"

    # 1. 历史IV（Deribit有BTC/ETH 384天；其他币种用滚动HV20替代）
    if currency in DERIBIT_COINS:
        iv_raw = get_hist_iv(currency)
        if not iv_raw:
            raise ValueError("Deribit历史IV为空")
        # 2. 日收益率
        returns = get_daily_returns(symbol, limit=len(iv_raw) + 5)
        iv_vals = [x[1] for x in iv_raw]
    else:
        # 非BTC/ETH：用滚动20日HV序列模拟IV（Binance帟三期权不廪Greeks API覆盖，为简化）
        returns = get_daily_returns(symbol, limit=400)
        iv_vals = []
        for i in range(20, len(returns)):
            window = returns[i-20:i]
            mean_w = sum(window) / 20
            hv_i = math.sqrt(sum((r-mean_w)**2 for r in window) / 20) * math.sqrt(252) * 100
            iv_vals.append(hv_i)
        if not iv_vals:
            raise ValueError(f"{currency} 历史数据不足")
    n        = min(len(returns) - 1, len(iv_vals) - 1, 300)
    iv_tail  = iv_vals[-(n+1):]
    ret_tail = returns[-(n+1):]

    iv_changes  = [iv_tail[i+1] - iv_tail[i] for i in range(n)]
    price_rets  = ret_tail[1:]

    # 4. 分上涨/下跌计算beta
    up_idx = [i for i in range(n) if price_rets[i] > 0]
    dn_idx = [i for i in range(n) if price_rets[i] < 0]

    beta_plus  = pearson_corr([price_rets[i] for i in up_idx],
                               [iv_changes[i] for i in up_idx])
    beta_minus = pearson_corr([price_rets[i] for i in dn_idx],
                               [iv_changes[i] for i in dn_idx])
    kappa      = beta_plus + beta_minus

    # 5. 当前IV分位
    current_iv   = iv_vals[-1]
    sorted_iv    = sorted(iv_vals[-90:])   # 90天分位
    pct_rank     = sum(1 for v in sorted_iv if v <= current_iv) / len(sorted_iv) * 100

    # 6. IV Regime
    if kappa < -0.3:
        iv_regime = "Risk-off"      # 下跌时IV暴涨，市场恐慌
    elif kappa > 0.3:
        iv_regime = "Up-vol"        # 上涨时IV也涨，逼空特征
    else:
        iv_regime = "Neutral"

    # 7. 30日实现波动率 HV30（对数收益率，年化，%）
    ret30 = price_rets[-30:]
    mean30 = sum(ret30) / len(ret30)
    hv30 = math.sqrt(sum((r - mean30)**2 for r in ret30) / len(ret30)) * math.sqrt(252) * 100

    return {
        "ts":           time.time(),
        "currency":     currency,
        "current_iv":   round(current_iv, 2),
        "iv_pct_rank":  round(pct_rank, 1),
        "beta_plus":    round(beta_plus, 4),
        "beta_minus":   round(beta_minus, 4),
        "kappa":        round(kappa, 4),
        "iv_regime":    iv_regime,
        "hv30":         round(hv30, 2),
        "iv_premium":   round(current_iv - hv30, 2),  # IV-HV溢价(%)
        "days_used":    n,
    }

def run(currency: str = "ETH", verbose: bool = False):
    try:
        r = calc_vol_beta(currency)
    except Exception as e:
        print(f"[vol_beta] ❌ {e}", file=sys.stderr)
        sys.exit(1)

    OUT_FILE.parent.mkdir(exist_ok=True)

    # 读取已有状态（多币种）
    existing = {}
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text())
        except Exception:
            pass
    existing[currency] = r
    OUT_FILE.write_text(json.dumps(existing, ensure_ascii=False))

    # 追加IV历史日志
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({
            "ts": r["ts"], "currency": currency,
            "iv": r["current_iv"], "pct": r["iv_pct_rank"],
            "kappa": r["kappa"], "regime": r["iv_regime"]
        }) + "\n")

    regime_emoji = {"Risk-off":"🔴","Up-vol":"🟢","Neutral":"⚖️"}.get(r["iv_regime"],"")
    if verbose:
        print(f"[vol_beta] {currency}")
        print(f"  IV={r['current_iv']:.1f}%  分位={r['iv_pct_rank']:.0f}pct  HV30={r['hv30']:.1f}%  溢价={r['iv_premium']:+.1f}%")
        print(f"  β⁺={r['beta_plus']:.3f}  β⁻={r['beta_minus']:.3f}  κ={r['kappa']:.3f}")
        print(f"  Regime: {regime_emoji}{r['iv_regime']}  (数据:{r['days_used']}天)")
    else:
        print(f"[vol_beta] ✅ {currency} IV={r['current_iv']:.1f}% "
              f"pct={r['iv_pct_rank']:.0f} κ={r['kappa']:.3f} {regime_emoji}{r['iv_regime']}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="ETH", choices=["ETH","BTC","SOL","BNB","XRP","DOGE","XAU","XAG"])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    run(args.currency, args.verbose)

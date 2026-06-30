import json, time, warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations

warnings.filterwarnings("ignore")
FIXED   = Path("data/backtest/fixed")
RESULTS = Path("dharma/results")
RESULTS.mkdir(exist_ok=True)
TAG = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
t0 = time.time()

# ── 指标函数 ────────────────────────────────────────────────────────────────

def ema(a, p):
    out = np.full(len(a), np.nan, dtype=np.float64)
    k = 2 / (p + 1)
    s = 0
    while s < len(a) and np.isnan(a[s]):
        s += 1
    if s >= len(a):
        return out
    out[s] = a[s]
    for i in range(s + 1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out

def rsi(c, p=14):
    c = c.astype(np.float64)
    d = np.diff(c, prepend=c[0])
    g = np.where(d > 0, d, 0.0)
    lv = np.where(d < 0, -d, 0.0)
    ag = np.full(len(c), np.nan)
    al = np.full(len(c), np.nan)
    if len(c) <= p:
        return ag
    ag[p] = g[1:p+1].mean()
    al[p] = lv[1:p+1].mean()
    k = 1 / p
    k1 = (p - 1) / p
    for i in range(p + 1, len(c)):
        ag[i] = ag[i-1] * k1 + g[i] * k
        al[i] = al[i-1] * k1 + lv[i] * k
    rs = np.where(al == 0, 100.0, ag / al)
    r = 100 - 100 / (1 + rs)
    r[:p] = np.nan
    return r

def atr_f(h, l, c, p=14):
    h = h.astype(np.float64)
    l = l.astype(np.float64)
    c = c.astype(np.float64)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    a = np.full(len(tr), np.nan)
    if len(tr) < p:
        return a
    a[p-1] = tr[:p].mean()
    k = 1 / p; k1 = (p - 1) / p
    for i in range(p, len(tr)):
        a[i] = tr[i] * k + a[i-1] * k1
    return a

def bb_bands(c, p=20, s=2.0):
    ser = pd.Series(c.astype(np.float64))
    m = ser.rolling(p).mean()
    sd = ser.rolling(p).std()
    return (m + s * sd).values, (m - s * sd).values

def macd_h(c):
    c = c.astype(np.float64)
    e12 = ema(c, 12); e26 = ema(c, 26)
    line = e12 - e26
    sig = ema(np.nan_to_num(line), 9)
    return line - sig

def stochrsi_vec(c, p=14, sp=14, sk=3):
    """Vectorized StochRSI using pandas rolling."""
    r = pd.Series(rsi(c, p))
    rmin = r.rolling(sp).min()
    rmax = r.rolling(sp).max()
    denom = rmax - rmin
    k = np.where(denom > 0, (r - rmin) / denom * 100.0, 50.0)
    k = pd.Series(k)
    k[:p+sp-1] = np.nan
    return k.rolling(sk).mean().values

def willr(h, l, c, p=14):
    hh = pd.Series(h.astype(np.float64)).rolling(p).max().values
    ll = pd.Series(l.astype(np.float64)).rolling(p).min().values
    denom = hh - ll
    return np.where(denom > 0, (hh - c) / denom * -100, -50)

def cci20_vec(h, l, c, p=20):
    """Vectorized CCI using pandas rolling."""
    tp = (h + l + c) / 3.0
    tp_s = pd.Series(tp.astype(np.float64))
    m = tp_s.rolling(p).mean().values
    # mean deviation via rolling apply
    md = tp_s.rolling(p).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).values
    return np.where(md > 0, (tp - m) / (0.015 * md), 0.0)

def get_regime_vec(df4h):
    c = df4h["close"].values.astype(np.float64)
    e21 = ema(c, 21); e55 = ema(c, 55); e200 = ema(c, 200)
    r = rsi(c, 14)
    res = []
    for i in range(len(c)):
        if np.isnan(e200[i]) or np.isnan(r[i]):
            res.append("CHOP"); continue
        p = c[i]; ri = r[i]
        if p > e21[i] > e55[i] > e200[i]:
            res.append("BULL_TREND" if ri > 60 else ("BULL_EARLY" if ri > 50 else "BULL_CORRECTION"))
        elif p < e21[i] < e55[i] < e200[i]:
            res.append("BEAR_TREND" if ri < 40 else ("BEAR_EARLY" if ri < 50 else "BEAR_RECOVERY"))
        elif p > e55[i] and ri > 52:
            res.append("BULL_EARLY")
        elif p < e55[i] and ri < 48:
            res.append("BEAR_EARLY")
        else:
            res.append("CHOP")
    return np.array(res)

def map15m_vec(df15, df_ref, arr):
    """Fully vectorized mapping using searchsorted."""
    idx = df_ref.index.searchsorted(df15.index, "right") - 1
    idx = np.clip(idx, 0, len(df_ref) - 1)
    return arr[idx]

# ── 完全向量化结算 ──────────────────────────────────────────────────────────

def settle_vec(h, l, c, indices, ep, sl_arr, tp_arr, hold, direction):
    """Fully vectorized settlement — no Python loops."""
    COST = 0.0004
    n = len(c)
    N = len(indices)
    if N == 0:
        return np.array([], dtype=object), np.array([], dtype=np.float64)

    # Build window index matrix [N, hold]
    col_idx = np.arange(1, hold + 1)               # [hold]
    idx_mat = np.minimum(indices[:, None] + col_idx[None, :], n - 1)  # [N, hold]

    h_mat = h[idx_mat]   # [N, hold]
    l_mat = l[idx_mat]   # [N, hold]
    c_timeout = c[np.minimum(indices + hold, n - 1)]

    if direction == "SHORT":
        sl_hit = h_mat >= sl_arr[:, None]
        tp_hit = l_mat <= tp_arr[:, None]
    else:
        sl_hit = l_mat <= sl_arr[:, None]
        tp_hit = h_mat >= tp_arr[:, None]

    any_sl = sl_hit.any(axis=1)
    any_tp = tp_hit.any(axis=1)

    sl_bar = np.where(any_sl, np.argmax(sl_hit, axis=1), hold)
    tp_bar = np.where(any_tp, np.argmax(tp_hit, axis=1), hold)

    sl_first = any_sl & (sl_bar < tp_bar)
    tp_first = any_tp & (tp_bar <= sl_bar)

    exit_p = np.where(sl_first, sl_arr, np.where(tp_first, tp_arr, c_timeout))

    if direction == "SHORT":
        pnl = (ep - exit_p) / ep - COST
    else:
        pnl = (exit_p - ep) / ep - COST

    pnl_pct = np.round(pnl * 100, 4)
    result_codes = np.where(sl_first, "SL", np.where(tp_first, "TP", "TIMEOUT"))

    return result_codes, pnl_pct

# ── 主逻辑 ──────────────────────────────────────────────────────────────────

print("=" * 60)
print("达摩院 v3.1 全能力盲测 [向量化版]")
print("18指标 x 153组合 x BTC+ETH x 4层周期联动")
print("=" * 60, flush=True)

ALL_CONDS = [f"T{i}" for i in range(1, 19)]
COMBOS = list(combinations(ALL_CONDS, 2))
print(f"组合数: {len(COMBOS)} x 2方向 = {len(COMBOS)*2}", flush=True)

all_records = []

for sym in ["BTCUSDT", "ETHUSDT"]:
    t1 = time.time()
    print(f"\n▶ {sym}", flush=True)
    df15 = pd.read_parquet(FIXED / f"{sym.lower()}_15m_fixed.parquet")
    df4h = pd.read_parquet(FIXED / f"{sym.lower()}_4h_fixed.parquet")
    df1d = pd.read_parquet(FIXED / f"{sym.lower()}_1d_fixed.parquet")

    c  = df15["close"].values.astype(np.float64)
    h  = df15["high"].values.astype(np.float64)
    l  = df15["low"].values.astype(np.float64)
    v  = df15["volume"].values.astype(np.float64)
    o15 = df15["open"].values.astype(np.float64)
    n = len(c)
    years = (df15.index[-1] - df15.index[0]).days / 365
    print(f"  {n:,}根K线 {years:.1f}年 计算指标...", flush=True)

    R14 = rsi(c, 14); R7 = rsi(c, 7)
    A14 = atr_f(h, l, c, 14)
    E13 = ema(c, 13); E21 = ema(c, 21); E55 = ema(c, 55); E200 = ema(c, 200)
    BU, BD = bb_bands(c, 20, 2.0)
    MH = macd_h(c); MHP = np.roll(MH, 1); MHP[0] = 0
    SR = stochrsi_vec(c, 14, 14, 3)
    WR = willr(h, l, c, 14)
    CCI = cci20_vec(h, l, c, 20)

    VM = pd.Series(v).rolling(20).mean().values
    VR = np.where(VM > 0, v / VM, 1.0)
    VWAP = np.cumsum((h + l + c) / 3 * v) / np.maximum(np.cumsum(v), 1)
    VDEV = (c - VWAP) / (VWAP + 1e-10) * 100

    # Heikin-Ashi open (vectorized via cumulative mean shortcut)
    HAC = (o15 + h + l + c) / 4
    HAO = np.empty(n)
    HAO[0] = (o15[0] + c[0]) / 2
    for i in range(1, n):
        HAO[i] = (HAO[i-1] + HAC[i-1]) / 2

    SW_H10 = pd.Series(h).rolling(10).max().shift(1).values
    SW_L10 = pd.Series(l).rolling(10).min().shift(1).values
    SW_H20 = pd.Series(h).rolling(20).max().shift(1).values
    SW_L20 = pd.Series(l).rolling(20).min().shift(1).values
    TOL = A14 * 0.4

    print(f"  映射4H/1D...", flush=True)
    reg4h = get_regime_vec(df4h)
    regime_15m = map15m_vec(df15, df4h, reg4h)

    e200_1d = ema(df1d["close"].values.astype(np.float64), 200)
    d1bull = (df1d["close"].values.astype(np.float64) > e200_1d).astype(float)
    d1bull_15m = map15m_vec(df15, df1d, d1bull).astype(bool)

    h4 = df4h["high"].values.astype(np.float64)
    l4 = df4h["low"].values.astype(np.float64)
    c4 = df4h["close"].values.astype(np.float64)
    a4 = atr_f(h4, l4, c4, 14)
    sh4 = pd.Series(h4).rolling(10).max().shift(1).values
    sl4 = pd.Series(l4).rolling(10).min().shift(1).values
    a4f = pd.Series(a4).ffill().values

    SH4 = map15m_vec(df15, df4h, sh4).astype(np.float64)
    SL4 = map15m_vec(df15, df4h, sl4).astype(np.float64)
    A4  = map15m_vec(df15, df4h, a4f).astype(np.float64)
    TOL4 = A4 * 0.4

    print(f"  构建条件矩阵...", flush=True)
    CS = {
        "T1":  R14 > 70,
        "T2":  R14 > 65,
        "T3":  c >= BU * 0.998,
        "T4":  (c >= SW_H10 - TOL) & (c <= SW_H10 + TOL * 0.3),
        "T5":  (c >= SW_H20 - TOL) & (c <= SW_H20 + TOL * 0.3),
        "T6":  (MH < 0) & (MHP >= 0),
        "T7":  E13 < E21,
        "T8":  (VR > 1.5) & (c < E55),
        "T9":  SR > 80,
        "T10": WR > -20,
        "T11": CCI > 100,
        "T12": A14 > 0,
        "T13": VDEV > 1.0,
        "T14": (c < E200) & (R14 < 50),
        "T15": ~d1bull_15m,
        "T16": (c >= SH4 - TOL4) & (c <= SH4 + TOL4 * 0.3),
        "T17": HAC < HAO,
        "T18": R7 > 75,
    }
    CL = {
        "T1":  R14 < 30,
        "T2":  R14 < 35,
        "T3":  c <= BD * 1.002,
        "T4":  (c >= SW_L10 - TOL * 0.3) & (c <= SW_L10 + TOL),
        "T5":  (c >= SW_L20 - TOL * 0.3) & (c <= SW_L20 + TOL),
        "T6":  (MH > 0) & (MHP <= 0),
        "T7":  E13 > E21,
        "T8":  (VR > 1.5) & (c > E55),
        "T9":  SR < 20,
        "T10": WR < -80,
        "T11": CCI < -100,
        "T12": A14 > 0,
        "T13": VDEV < -1.0,
        "T14": (c > E200) & (R14 > 50),
        "T15": d1bull_15m,
        "T16": (c >= SL4 - TOL4 * 0.3) & (c <= SL4 + TOL4),
        "T17": HAC > HAO,
        "T18": R7 < 25,
    }

    valid_base = (
        ~np.isnan(R14) & ~np.isnan(A14) & ~np.isnan(E200) & ~np.isnan(SR) &
        (np.arange(n) >= 220) & (np.arange(n) < n - 17)
    )
    VALID = np.where(valid_base)[0]

    HOLD = 16; SL_M = 2.0; TP_M = 1.5; COOL = 4

    # Pre-compute cooldown-filtered valid indices per combo via vectorized cooldown
    def apply_cooldown(hit_idx, cool):
        if len(hit_idx) == 0:
            return hit_idx
        kept = [hit_idx[0]]
        last = hit_idx[0]
        for idx in hit_idx[1:]:
            if idx - last >= cool:
                kept.append(idx)
                last = idx
        return np.array(kept, dtype=np.int64)

    sym_combos = []  # list of (combo_str, direction, kept_idx, result_codes, pnl_pct, regime_arr)

    for direction, CONDS in [("SHORT", CS), ("LONG", CL)]:
        cnt = 0
        for ca, cb in COMBOS:
            mask = CONDS[ca] & CONDS[cb]
            hit = np.intersect1d(VALID, np.where(mask)[0])
            if len(hit) == 0:
                continue
            kept = apply_cooldown(hit, COOL)
            if len(kept) == 0:
                continue
            ep = c[kept]
            sl_p = np.where(direction == "SHORT",
                            ep + A14[kept] * SL_M,
                            ep - A14[kept] * SL_M)
            tp_p = np.where(direction == "SHORT",
                            ep - A14[kept] * TP_M,
                            ep + A14[kept] * TP_M)
            res_codes, pnl_arr = settle_vec(h, l, c, kept, ep, sl_p, tp_p, HOLD, direction)
            combo_str = f"{ca}+{cb}"
            sym_combos.append((combo_str, direction, kept, res_codes, pnl_arr))
            cnt += len(kept)
        print(f"  {direction} done: {cnt:,}条 ({time.time()-t1:.0f}s)", flush=True)

    # Convert to records
    sym_recs = []
    for combo_str, direction, kept, res_codes, pnl_arr in sym_combos:
        ts_arr = [str(df15.index[i])[:13] for i in kept]
        reg_arr = regime_15m[kept]
        for k in range(len(kept)):
            sym_recs.append({
                "sym": sym,
                "combo": combo_str,
                "direction": direction,
                "regime": str(reg_arr[k]),
                "ts": ts_arr[k],
                "result": res_codes[k],
                "pnl_pct": float(pnl_arr[k]),
            })

    print(f"  {sym}: {len(sym_recs):,}条 ({(time.time()-t1)/60:.1f}min)", flush=True)
    out = RESULTS / f"blind_v3_{sym.lower()}_{TAG}.jsonl"
    with open(out, "w") as f:
        for r in sym_recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    all_records.extend(sym_recs)

# ── 统计 ───────────────────────────────────────────────────────────────────

def st(rs):
    if not rs:
        return {"n": 0, "wr": 0, "avg_pnl": 0, "tp": 0, "sl": 0, "to": 0}
    tp = sum(1 for r in rs if r["result"] == "TP")
    sl = sum(1 for r in rs if r["result"] == "SL")
    to = sum(1 for r in rs if r["result"] == "TIMEOUT")
    wr = tp / (tp + sl) if tp + sl > 0 else 0
    return {
        "n": len(rs), "tp": tp, "sl": sl, "to": to,
        "wr": round(wr, 4),
        "avg_pnl": round(float(np.mean([r["pnl_pct"] for r in rs])), 4),
    }

total = len(all_records)
print(f"\n总信号: {total:,}  耗时: {(time.time()-t0)/60:.1f}分钟")

by_c = {}; by_rd = {}; by_crd = {}
for r in all_records:
    for d, k in [
        (by_c,   r["combo"]),
        (by_rd,  f"{r['regime']}_{r['direction']}"),
        (by_crd, f"{r['combo']}|{r['regime']}_{r['direction']}"),
    ]:
        if k not in d:
            d[k] = []
        d[k].append(r)

cut = pd.Timestamp("2024-01-01")
tr_c = {}; os_c = {}
for r in all_records:
    k = r["combo"]
    bucket = tr_c if pd.Timestamp(r["ts"] + "T00:00") < cut else os_c
    if k not in bucket:
        bucket[k] = []
    bucket[k].append(r)

MIN_N = 1000

print(f"\n【排行榜1：条件组合 WR TOP20 (n>={MIN_N})】")
lb1 = sorted(
    [(k, st(v)) for k, v in by_c.items() if len(v) >= MIN_N],
    key=lambda x: x[1]["wr"], reverse=True
)[:20]
for i, (k, s) in enumerate(lb1, 1):
    bar = "█" * int(s["wr"] * 25)
    print(f"{i:>3}. {k:<12} n={s['n']:>9,}  WR={s['wr']:.1%}  avgPnL={s['avg_pnl']:+.3f}%  {bar}")

print(f"\n【排行榜2：条件组合 avgPnL TOP15 (n>={MIN_N})】")
lb2 = sorted(
    [(k, st(v)) for k, v in by_c.items() if len(v) >= MIN_N],
    key=lambda x: x[1]["avg_pnl"], reverse=True
)[:15]
for i, (k, s) in enumerate(lb2, 1):
    print(f"{i:>3}. {k:<12} n={s['n']:>9,}  WR={s['wr']:.1%}  avgPnL={s['avg_pnl']:+.4f}%")

print(f"\n【排行榜3：体制x方向 WR (n>={MIN_N})】")
lb3 = sorted(
    [(k, st(v)) for k, v in by_rd.items() if len(v) >= MIN_N],
    key=lambda x: x[1]["wr"], reverse=True
)
for k, s in lb3:
    flag = "✅" if s["wr"] >= 0.65 else ("❌" if s["wr"] < 0.50 else "➖")
    print(f"{flag} {k:<40} n={s['n']:>9,}  WR={s['wr']:.1%}  avgPnL={s['avg_pnl']:+.3f}%")

print(f"\n【排行榜4：最强组合x体制x方向 TOP20 (n>=500)】")
lb4 = sorted(
    [(k, st(v)) for k, v in by_crd.items() if len(v) >= 500],
    key=lambda x: x[1]["wr"], reverse=True
)[:20]
for i, (k, s) in enumerate(lb4, 1):
    p = k.split("|")
    print(f"{i:>3}. {p[0]:<12} {p[1]:<40} n={s['n']:>7,}  WR={s['wr']:.1%}  avgPnL={s['avg_pnl']:+.3f}%")

print(f"\n【WFV验证 TOP15 (训练<2024 / OOS 2024+)】")
for k, s in lb1[:15]:
    ts_ = st(tr_c.get(k, []))
    os_ = st(os_c.get(k, []))
    if ts_["n"] == 0 or os_["n"] == 0:
        print(f"  {k} 样本不足")
        continue
    diff = abs(ts_["wr"] - os_["wr"])
    flag = "✅稳健" if diff < 0.05 else ("➖可用" if diff < 0.10 else ("⚠️偏差" if diff < 0.15 else "❌过拟合"))
    print(f"  {flag} {k:<12} Train={ts_['wr']:.1%}(n={ts_['n']:,}) OOS={os_['wr']:.1%}(n={os_['n']:,}) Delta={diff:.1%}")

rp = RESULTS / f"blind_v3_report_{TAG}.json"
rp.write_text(json.dumps({
    "version": "v3.1",
    "tag": TAG,
    "total": total,
    "elapsed_min": round((time.time() - t0) / 60, 2),
    "lb1": [(k, s) for k, s in lb1],
    "lb2": [(k, s) for k, s in lb2],
    "lb3": [(k, s) for k, s in lb3],
    "lb4": [(k, s) for k, s in lb4],
}, indent=2, ensure_ascii=False, default=str))
print(f"报告: {rp.name}")
print(f"达摩院 v3.1 完成 ✅  总耗时: {(time.time()-t0)/60:.1f}分钟")

"""
HCME - Historical Context Matching Engine  (M3)
================================================
Matches current signal against 410 historical signals using cosine similarity.
Pure stdlib — no numpy, no sklearn.

Author: brahma-subagent / 2026-08-08
"""

from __future__ import annotations
import json
import math
import os
from datetime import datetime, timezone
from typing import Optional

# ── paths ────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "..", "data")
SIGNAL_LOG_PATH  = os.path.join(_DATA, "live_signal_log.jsonl")
HCME_INDEX_PATH  = os.path.join(_DATA, "hcme_index.json")

# ── regime encoder ───────────────────────────────────────────────────────────
REGIME_MAP = {
    "BULL_TREND":    1.0,
    "BULL_EARLY":    0.7,
    "CHOP_HIGH":     0.2,
    "CHOP_MID":      0.0,
    "CHOP_LOW":     -0.2,
    "BEAR_RECOVERY": -0.5,
    "BEAR_TREND":   -1.0,
}
DIRECTION_MAP = {"LONG": 1.0, "SHORT": -1.0}

# outcome → win?
WIN_OUTCOMES  = {"TP1", "TP2", "WIN"}
LOSS_OUTCOMES = {"SL", "LOSS", "STOPPED"}


def _safe_float(v, default: float = 0.0) -> float:
    """Coerce to float, fallback to default on None/empty."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _cosine(a: list, b: list) -> float:
    """Cosine similarity between two equal-length lists."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── ATH lookup (approximate, from 4H OHLCV tail scan) ────────────────────────
_ATH_CACHE: dict = {}


def _get_ath(symbol: str) -> float:
    """Return approximate all-time-high from backtest data (up to last bar)."""
    if symbol in _ATH_CACHE:
        return _ATH_CACHE[symbol]
    candidate_files = [
        os.path.join(_DATA, "backtest", f"{symbol}_4h.json"),
        os.path.join(_DATA, "backtest", f"{symbol}_1h.json"),
    ]
    for path in candidate_files:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    rows = json.load(f)
                ath = max(_safe_float(r[2]) for r in rows)  # col-2 = high
                _ATH_CACHE[symbol] = ath
                return ath
            except Exception:
                pass
    _ATH_CACHE[symbol] = 0.0
    return 0.0


class HCMEMatcher:
    """
    Matches a live signal against historical context for confidence adjustment.

    Usage
    -----
    m = HCMEMatcher()
    result = m.find_similar(signal_dict, top_k=5)
    """

    def __init__(self):
        self.signals: list[dict] = self._load_signals()
        self.index: list[dict] = self._build_or_load_index()

    # ── data loading ──────────────────────────────────────────────────────────

    def _load_signals(self) -> list[dict]:
        signals = []
        with open(SIGNAL_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        signals.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return signals

    def _build_or_load_index(self) -> list[dict]:
        """Load pre-built index or rebuild from signals."""
        if os.path.exists(HCME_INDEX_PATH):
            try:
                with open(HCME_INDEX_PATH) as f:
                    existing = json.load(f)
                if len(existing) == len(self.signals):
                    return existing
            except Exception:
                pass
        return self._build_index()

    def _build_index(self) -> list[dict]:
        """Pre-compute feature vectors for all signals and persist."""
        index = []
        for sig in self.signals:
            vec  = self.build_feature_vector(sig)
            outcome = sig.get("outcome") or sig.get("result") or "UNKNOWN"
            is_win  = outcome in WIN_OUTCOMES
            is_loss = outcome in LOSS_OUTCOMES

            regime    = sig.get("regime") or sig.get("market_regime") or "CHOP_MID"
            direction = sig.get("direction") or sig.get("signal_dir") or "LONG"

            entry = {
                "signal_id": sig.get("signal_id", ""),
                "ts":        sig.get("ts", 0),
                "symbol":    sig.get("symbol", "BTCUSDT"),
                "regime":    regime,
                "direction": direction,
                "outcome":   outcome,
                "is_win":    is_win,
                "is_loss":   is_loss,
                "score":     _safe_float(sig.get("score")),
                "pnl_pct":   _safe_float(sig.get("pnl_pct")),
                "vec":       vec,
            }
            index.append(entry)

        # Persist
        try:
            os.makedirs(os.path.dirname(HCME_INDEX_PATH), exist_ok=True)
            with open(HCME_INDEX_PATH, "w") as f:
                json.dump(index, f, separators=(",", ":"))
        except Exception as e:
            print(f"[HCME] Warning: could not persist index: {e}")

        return index

    # ── feature engineering ───────────────────────────────────────────────────

    def build_feature_vector(self, signal: dict) -> list:
        """
        Convert signal → 15-dim normalized feature vector.

        Dims:
          0  regime_enc       [-1, +1]
          1  direction_enc    {-1, +1}
          2  score_norm       [0, 1]  (score / 130)
          3  rsi_norm         [0, 1]  (rsi_4h / 100)
          4  sl_pct           [0, 1]  (sl_pct / 10)
          5  vol_ratio        [0, 1]  placeholder (0.5 if absent)
          6  oi_chg           [0, 1]  placeholder (0.5 if absent)
          7  fr               [0, 1]  placeholder (0.5 if absent)
          8  dist_ath_norm    [0, 1]  (price / ATH)
          9  atr_pct          [0, 1]  derived from sl_pct proxy
          10 bb_width         [0, 1]  placeholder
          11 hour_of_day      [0, 1]  (hour / 23)
          12 day_of_week      [0, 1]  (dow / 6)
          13 month            [0, 1]  (month / 12)
          14 bull_bear_days   [0, 1]  placeholder (0.5)
        """
        regime    = signal.get("regime") or signal.get("market_regime") or "CHOP_MID"
        direction = signal.get("direction") or signal.get("signal_dir") or "LONG"
        score     = _safe_float(signal.get("score"),   default=80.0)
        rsi_4h    = _safe_float(signal.get("rsi_4h"),  default=50.0)
        sl_pct    = _safe_float(signal.get("sl_pct"),  default=2.0)
        price     = _safe_float(signal.get("price") or signal.get("generated_price"), default=0.0)
        symbol    = signal.get("symbol", "BTCUSDT")

        # temporal
        ts = signal.get("ts") or signal.get("timestamp") or 0
        try:
            if ts:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            elif signal.get("ts_iso"):
                dt = datetime.fromisoformat(signal["ts_iso"].replace("Z", "+00:00"))
            else:
                dt = datetime.now(tz=timezone.utc)
        except Exception:
            dt = datetime.now(tz=timezone.utc)

        # ATH proximity
        ath = _get_ath(symbol)
        dist_ath = (price / ath) if ath > 0 and price > 0 else 0.5

        # atr proxy from sl
        atr_pct = min(sl_pct / 3.0, 1.0)  # rough: SL ≈ 3×ATR

        vec = [
            (REGIME_MAP.get(regime, 0.0) + 1.0) / 2.0,   # 0  → [0,1]
            (DIRECTION_MAP.get(direction, 1.0) + 1.0) / 2.0,  # 1 → [0,1]
            min(score / 130.0, 1.0),                       # 2
            rsi_4h / 100.0,                                # 3
            min(sl_pct / 10.0, 1.0),                       # 4
            0.5,                                           # 5  vol_ratio (absent)
            0.5,                                           # 6  oi_chg    (absent)
            0.5,                                           # 7  fr        (absent)
            min(dist_ath, 1.0),                            # 8
            min(atr_pct, 1.0),                             # 9
            0.5,                                           # 10 bb_width  (absent)
            dt.hour / 23.0,                                # 11
            dt.weekday() / 6.0,                            # 12
            (dt.month - 1) / 11.0,                        # 13
            0.5,                                           # 14 bull_bear_days (absent)
        ]
        return vec

    # ── similarity search ─────────────────────────────────────────────────────

    def find_similar(self, current_signal: dict, top_k: int = 5) -> dict:
        """
        Find top-k most similar historical signals via cosine similarity.

        Returns
        -------
        {
          similar_cases      : list of dicts
          historical_wr      : float   win-rate among similar cases
          confidence         : float   average similarity of top-k
          regime_wr          : float   WR for same regime+direction globally
          hcme_score_adj     : int     score adjustment (-20 ~ +20)
          context_summary    : str
        }
        """
        cur_vec   = self.build_feature_vector(current_signal)
        cur_regime    = current_signal.get("regime") or current_signal.get("market_regime") or "CHOP_MID"
        cur_direction = current_signal.get("direction") or current_signal.get("signal_dir") or "LONG"

        # score every historical entry
        # [设计院修复 2026-08-12 苏摩111封印] 方向一致性校验
        # 修复前：所有历史案例参与匹配（UP/DOWN混用），SHORT信号可能被UP案例错误加分
        # 修复后：优先匹配同方向案例；同方向案例不足top_k时，降级为全量匹配
        scored_same_dir = []
        scored_all = []
        for entry in self.index:
            sim = _cosine(cur_vec, entry["vec"])
            scored_all.append((sim, entry))
            if entry["direction"] == cur_direction:
                scored_same_dir.append((sim, entry))

        scored_all.sort(key=lambda x: x[0], reverse=True)
        scored_same_dir.sort(key=lambda x: x[0], reverse=True)

        # 同方向案例足够时优先使用，不足时降级全量（记录标志供context_summary说明）
        _dir_filtered = len(scored_same_dir) >= top_k
        top = scored_same_dir[:top_k] if _dir_filtered else scored_all[:top_k]

        # stats on top-k
        decided = [(s, e) for s, e in top if e["is_win"] or e["is_loss"]]
        if decided:
            wins_in_top = sum(1 for _, e in decided if e["is_win"])
            historical_wr = wins_in_top / len(decided)
        else:
            historical_wr = 0.5  # unknown → neutral

        confidence = sum(s for s, _ in top) / len(top) if top else 0.0

        # regime+direction global WR
        global_decided = [e for e in self.index
                          if e["regime"] == cur_regime
                          and e["direction"] == cur_direction
                          and (e["is_win"] or e["is_loss"])]
        if global_decided:
            regime_wr = sum(1 for e in global_decided if e["is_win"]) / len(global_decided)
        else:
            regime_wr = 0.5

        # score adjustment: compare historical_wr vs baseline
        baseline = regime_wr if regime_wr > 0 else 0.5
        delta = historical_wr - baseline          # -1 .. +1
        # scale to -20 .. +20, weighted by confidence
        hcme_score_adj = int(round(delta * 20.0 * min(confidence, 1.0)))
        hcme_score_adj = max(-20, min(20, hcme_score_adj))

        # build similar_cases list
        similar_cases = []
        for sim, entry in top:
            similar_cases.append({
                "signal_id":    entry["signal_id"],
                "ts":           entry["ts"],
                "symbol":       entry["symbol"],
                "regime":       entry["regime"],
                "direction":    entry["direction"],
                "outcome":      entry["outcome"],
                "score":        entry["score"],
                "pnl_pct":      entry["pnl_pct"],
                "similarity":   round(sim, 4),
            })

        # natural language summary
        top_outcomes = [e["outcome"] for _, e in top]
        win_pct = int(historical_wr * 100)
        adj_word = "raise" if hcme_score_adj > 0 else ("lower" if hcme_score_adj < 0 else "keep")
        _dir_note = f"dir={cur_direction} filtered" if _dir_filtered else f"fallback all-dir (same-dir cases<{top_k})"
        context_summary = (
            f"Top-{top_k} similar cases [{_dir_note}]: outcomes={top_outcomes}. "
            f"Historical WR={win_pct}% vs regime baseline={int(regime_wr*100)}%. "
            f"Confidence={confidence:.2f}. "
            f"Suggestion: {adj_word} score by {abs(hcme_score_adj)} pts "
            f"(adj={hcme_score_adj:+d})."
        )

        return {
            "similar_cases":   similar_cases,
            "historical_wr":   round(historical_wr, 4),
            "confidence":      round(confidence, 4),
            "regime_wr":       round(regime_wr, 4),
            "hcme_score_adj":  hcme_score_adj,
            "context_summary": context_summary,
        }

    def get_price_context(self, symbol: str, current_price: float) -> dict:
        """
        Return historical structure background for a given price.
        Looks at all signals for the symbol, finds those near current price (±5%).
        """
        nearby = []
        for sig in self.signals:
            if sig.get("symbol") != symbol:
                continue
            sig_price = _safe_float(sig.get("price") or sig.get("generated_price"))
            if sig_price <= 0:
                continue
            pct_diff = abs(current_price - sig_price) / sig_price * 100
            if pct_diff <= 5.0:
                outcome = sig.get("outcome") or sig.get("result") or "UNKNOWN"
                nearby.append({
                    "signal_id": sig.get("signal_id", ""),
                    "price":     sig_price,
                    "direction": sig.get("direction") or sig.get("signal_dir") or "LONG",
                    "regime":    sig.get("regime") or "CHOP_MID",
                    "outcome":   outcome,
                    "score":     _safe_float(sig.get("score")),
                    "pct_diff":  round(pct_diff, 2),
                })

        nearby.sort(key=lambda x: x["pct_diff"])

        wins  = sum(1 for n in nearby if n["outcome"] in WIN_OUTCOMES)
        losses = sum(1 for n in nearby if n["outcome"] in LOSS_OUTCOMES)
        total = wins + losses
        price_wr = (wins / total) if total > 0 else None

        ath = _get_ath(symbol)
        dist_ath_pct = ((ath - current_price) / ath * 100) if ath > 0 else None

        return {
            "symbol":          symbol,
            "current_price":   current_price,
            "ath":             round(ath, 2),
            "dist_ath_pct":    round(dist_ath_pct, 2) if dist_ath_pct is not None else None,
            "nearby_signals":  nearby[:10],
            "nearby_count":    len(nearby),
            "price_zone_wr":   round(price_wr, 4) if price_wr is not None else None,
            "price_zone_wins": wins,
            "price_zone_losses": losses,
        }


# ── CLI / smoke test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[HCME] Building / loading index …")
    m = HCMEMatcher()
    print(f"[HCME] Index size: {len(m.index)} entries")

    # Smoke test with synthetic signal
    test_signal = {
        "symbol":    "BTCUSDT",
        "direction": "LONG",
        "regime":    "BULL_TREND",
        "score":     88.0,
        "rsi_4h":    62.0,
        "sl_pct":    2.1,
        "price":     64300.0,
        "ts":        1783641600,
    }
    result = m.find_similar(test_signal, top_k=5)
    print("\n── find_similar smoke test ──")
    print(json.dumps(result, indent=2, default=str))

    ctx = m.get_price_context("BTCUSDT", 64300.0)
    print("\n── get_price_context smoke test ──")
    print(json.dumps(ctx, indent=2, default=str))

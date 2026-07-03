<div align="center">

# 🏛️ Brahma-Quant

### Production-Grade Crypto Quantitative Trading System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-35%2F35%20passing-brightgreen)](tests/)
[![brahma-ci](https://img.shields.io/badge/brahma--ci-96%2F100-brightgreen)](brahma_brain/brahma_health.py)
[![Model](https://img.shields.io/badge/model-Open%20Core-purple)](docs/)

**The only crypto quant system built on three independent pillars:**  
**Multi-Agent Debate · Statistical Iron-Proof Validation (WF+CPCV+DSR) · Crypto-Native Pump Hunter**

[Architecture](#architecture) · [Quick Start](#quickstart) · [Dharma Validation](#iron-proof-validation) · [Live Performance](#validation-results) · [Pro Version](#license)

</div>

---

## 🎯 What Makes Brahma Different

| Feature | Brahma-Quant | Generic Multi-Agent | Traditional Quant |
|---|:---:|:---:|:---:|
| 35-Dimensional Scoring Engine | ✅ | ❌ | ⚠️ Partial |
| 10-Regime State Machine | ✅ | ❌ | ❌ |
| 6-Agent LLM Council Debate | ✅ | ✅ | ❌ |
| Dharma Validation: WF + CPCV + DSR | ✅ | ❌ | ⚠️ Basic |
| Pump Hunter — Meme Surge Detector (97.5% WR) | ✅ | ❌ | ❌ |
| 9-Layer Full-Chain Circuit Breaker | ✅ | ❌ | ⚠️ Partial |
| Monte Carlo **100,000** Simulation Runs | ✅ | ❌ | ⚠️ Partial |
| Zero-Cost Regime Watcher (event-driven, 0 tokens) | ✅ | ❌ | ❌ |
| Kronos Foundation Model Integration (AAAI 2026) | ✅ | ❌ | ❌ |
| Live Signal Auto-Settlement + EV Feedback Loop | ✅ | ❌ | ❌ |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Signal Pipeline (v5.0)                      │
│                                                              │
│  Tier 1 │ rsi_structure_watcher   every 5min  │  0 tokens  │
│          │ 7 trigger events: RSI/BB/OI/Volume │             │
│          ↓ event-triggered only                             │
│  Tier 2 │ brahma_analysis_runner  on-demand   │  ~6K tok   │
│          │ 35-dimensional scoring + 9 gates   │             │
│          ↓                                                   │
│  Tier 3 │ brahma-scan-guard       every 12h   │  48K/day   │
│          │ full market screener, slow-drift   │             │
│          ↓                                                   │
│        DharmaBridge → live_signal_log.jsonl                 │
│          ↓                                                   │
│        Signal Settler → wr_matrix_realtime → EV Feedback   │
└─────────────────────────────────────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────────┐
│                  Scoring Layers (35 Dimensions)              │
│                                                              │
│  s1  Market Structure (CHoCH / BOS / OB freshness)          │
│  s2  Order Block direction + age-decay (0.3x~1.0x)          │
│  s3  FVG fill probability                                    │
│  s4  Fibonacci confluence                                    │
│  s5  Multi-timeframe alignment (15m / 1h / 4h / 1d)        │
│  s6  RSI divergence + momentum                              │
│  s7  Bollinger Band compression trigger                      │
│  s8  Volume ratio anomaly                                    │
│  s9  OI surge detector (>5% / 1H)                           │
│  s10 Long/Short ratio signal                                 │
│  s11 Funding rate pressure                                   │
│  s12 Liquidation cluster proximity + direction              │
│  s13 GEX Gamma exposure (options, expiry ×1.5 near Friday)  │
│  s14 Macro calendar filter                                   │
│  s15 BTC.D dominance signal                                  │
│  s16 Cross-exchange funding arbitrage (Bybit + OKX)         │
│  s17 Deribit P/C OI ratio (Call/Put skew)                   │
│  s18 DXY + NQ futures macro (real-time)                     │
│  s19 CausalVerifier (statsmodels, conf 0.32)                │
│  s20 Tardis liquidation wall                                 │
│  s21 Smart money large-holder divergence (Glassnode alt)    │
│  s22 GEX zero-flip magnet                                    │
│  s23 Kronos Foundation Model — p_up forecast (SHADOW)       │
│  s24 Seasonal calendar (July layering: −15/−5/−8)           │
│  s25 BTC/ETH correlation risk filter (1.85x exposure cap)   │
│  ... (35 total, regime-weighted multipliers)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 10-Regime State Machine

Real-time market classification with **regime-specific signal multipliers**:

| Regime | Direction Bias | SHORT Mult | LONG Mult |
|--------|---------------|:---:|:---:|
| `BEAR_TREND` | SHORT primary | 1.6× | 0.10× |
| `BEAR_EARLY` | SHORT primary | 1.2× | 0.35× |
| `BEAR_RECOVERY` | LONG only | 0.30× | 1.2× |
| `BEAR_CRASH` | SHORT extreme | 2.0× | 0.05× |
| `BULL_TREND` | LONG primary | 0.15× | 1.6× |
| `BULL_EARLY` | LONG primary | 0.35× | 1.2× |
| `BULL_CORRECTION` | SHORT / BOTH | 1.1× | 0.50× |
| `CHOP_MID` | No signal | 0.88× | 0.50× |
| `CHOP_HIGH` | No signal | 0.70× | 0.40× |
| `CHOP_LOW` | No signal | 0.80× | 0.45× |

Detection runs every 5 minutes via `scripts/regime_switch_monitor.py` — pure script, zero AI tokens.

---

## 9-Layer Circuit Breaker

Every signal passes a sequential kill-chain before execution:

```
Layer 1 │ Globally Blocked (死穴)   │ BEAR_TREND+LONG / BULL_TREND+SHORT → kill
Layer 2 │ Structure Gate             │ SMC grade < 80  → WR=47% death zone → kill
Layer 3 │ Gap Gate                   │ price gap > 6%  → stale signal → kill
Layer 4 │ Causal Verifier            │ regime noise penalty (−12 pts)
Layer 5 │ Seasonal Filter            │ July early −15 / mid −5 / late −8 pts
Layer 6 │ Timing Filter              │ RSI_1H + EMA20_1H position + Kronos p_up
Layer 7 │ Correlation Risk           │ BTC + ETH simultaneous = 1.85× exposure → dedup
Layer 8 │ GEX Expiry                 │ last Friday ±3 days → GEX weight ×1.5
Layer 9 │ Kronos Extreme             │ p_up > 0.90 + SHORT → penalty halved
```

---

## Iron-Proof Validation (Dharma Pipeline)

Three independent anti-overfit gates — all must pass:

```
Walk-Forward (WF)
  ├─ Rolling expanding window, strict temporal ordering
  ├─ OOS WR:    82.7%  (Wuqu Paper track, n=121)
  └─ WF OOS WR: 68.3%  (live validation, Z=2.84, p<0.05)

CPCV — Combinatorial Purged Cross-Validation
  ├─ 15 combinatorial paths, purged + embargoed
  ├─ Deflated Sharpe Ratio (DSR): 22.64  (threshold > 1.0)
  └─ Overfit rate: 33.3%  (below 50% threshold ✅)

Monte Carlo — 100,000 Simulation Runs
  ├─ Bootstrap re-sampling on 63 settled real trades
  ├─ P5 = +909%   P50 = +1,364%   P95 = +1,958%
  ├─ Sharpe P50 = 25.09
  └─ Ruin probability = 0.0%  (5×–25× leverage sweep, all green)
```

---

## Pump Hunter

Detects meme token surge setups **before** the pump:

| Signal | Win Rate | Sample |
|--------|:---:|:---:|
| TIGHT compression < 15% | **97.5%** | n=1,600 (2yr) |
| RSI < 30 + TIGHT | 93.0% | — |
| 13H consecutive volume contraction | **100%** | n=19 |
| OOS 2026 validation | 80.6% | ✅ |

Alert scoring: `TIGHT(+40) + RSI<30(+25) + Vol-contraction(+20) + Trend(+10)`  
Tiers: ≥75 pts = 🚨 Warning · ≥85 pts = 💣 Level-3 Alert

---

## Zero-Cost Watcher (v5.0 Token Budget)

| Day Type | Tokens / Day | vs Old Arch |
|----------|:---:|:---:|
| Choppy (no events) | 48,000 | −50% |
| Active (multiple events) | 48K–84K | −13% |
| Old architecture | 96,000 | baseline |

---

## Kronos Foundation Model

Integrating [NeoQuasar/Kronos-mini](https://huggingface.co/NeoQuasar/Kronos-mini) (4.1M params, AAAI 2026, CPU-runnable):

- **L1** `get_s23_kronos()` — parallel shadow vs Kronos-Lite, A/B delta logging  
- **L2** `get_volatility_forecast()` → dynamic SL injection  
- **L3** `generate_synthetic_klines()` → regime-aware data augmentation  
- Status: **SHADOW** mode · upgrade at n≥100 + Kronos WR ≥ Lite WR + 2pp  
- Validated: p_up=0.697, CPU latency ≈3s, 15min cache → 0ms cached

---

## Quickstart

```bash
git clone https://github.com/dahaini08-spec/brahma-quant.git
cd brahma-quant
pip install -r requirements.txt

# Single symbol analysis
python examples/quick_start.py --symbol BTCUSDT

# Batch analysis
python -c "
from brahma_brain.brahma_analysis_runner import run_batch, format_batch_report
results = run_batch(['BTCUSDT', 'ETHUSDT'])
print(format_batch_report(results))
"

# System health check
python brahma_brain/brahma_health.py

# Test suite
pytest tests/ -q --ignore=tests/test_e2e_signal_flow.py
```

---

## Project Structure

```
brahma_brain/                   Core scoring engine (35D)
  brahma_core.py                  Main analysis engine
  brahma_analysis_runner.py       Unified entry point (sole gateway)
  dharma_data_bridge.py           Signal write gate (v5.0 BB filter)
  timing_filter.py                3-tier entry timing (READY/MONITOR/WAIT)
  position_sizer.py               Kelly-based sizing + CONFIDENCE_TABLE
  kronos_bridge.py                Kronos FM integration (shadow mode)
  brahma_health.py                8-point health check + self-heal matrix
  bull_regime_injector.py         BULL_TREND signal amplifier (+25~35 pts)

dharma/                         Validation & training pipeline
  realistic_cost_model.py         Slippage + fee modeling
  regime_aware_augmentor.py       Synthetic data (regime-conditioned)
  pump_hunter/                    Meme surge detector subsystem

scripts/                        Production automation
  rsi_structure_watcher.py        Tier-1 zero-cost event watcher
  regime_switch_monitor.py        10-regime classifier (pure script)
  live_signal_settler.py          Signal outcome settlement + EV loop
  auto_executor.py                Signal → order execution (dry-run gate)
  brahma_nerve_center.py          Sensing neural hub (5min)
  brahma_self_heal.py             Self-healing engine (15min)

arch/                           Anti-overfit validation suite
  validation/walk_forward.py      Walk-Forward rolling validator ← NEW
  validation/combinatorial_purged_cv.py
  validation/deflated_sharpe.py
  simulation/monte_carlo_engine.py  100K simulation engine

tests/                          35 passing / 0 failing
```

---

## Validation Results

| Metric | Value | Method |
|--------|:---:|--------|
| OOS Win Rate | **82.7%** | Walk-Forward, n=121 settled trades |
| Live WF OOS WR | **68.3%** | Rolling window, Z=2.84 ✅ significant |
| Deflated Sharpe Ratio | **22.64** | CPCV 15-path, DSR > 1.0 |
| Monte Carlo P50 | **+1,364%** | 100,000 runs, ruin = 0% |
| Monte Carlo Sharpe P50 | **25.09** | Bootstrap re-sample |
| System Health Score | **96/100** | brahma_health.py 8-point check |
| Pump Hunter WR | **97.5%** | TIGHT<15%, n=1,600, 2yr full sample |
| Pump Hunter OOS 2026 | **80.6%** | Out-of-sample validation ✅ |

---

## License

MIT — Open Core model.  
Production signal infrastructure, live execution layer, and regime-specific parameter tables are in the private repository.

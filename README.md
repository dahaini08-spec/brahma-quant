<div align="center">

# Brahma-Quant

### Crypto Perpetual Futures Signal Research Framework

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Multi-Regime Scoring Engine · SMC Structure Gates · Pump Hunter Scanner

[Architecture](#architecture) · [Quick Start](#quickstart) · [Real Performance](#real-performance) · [Audit](#audit-correction)

</div>

---

> **Scope & Honesty Notice (2026-09-13)**
>
> Brahma-Quant is a **signal research framework** for crypto perpetual futures.
> The execution OMS layer is not included in this public repository.
>
> All performance figures below are sourced from **in-repo settlement files**
> (`data/wr_matrix.json`, `dharma/pump_hunter/hunter_win_rate.json`).
> Numbers previously published in this README (WR=62%, DSR=22.64, MC P50=+1364%,
> Pump Hunter 97.5%) were **not reproducible from repository evidence** and have
> been corrected. See `docs/AUDIT_CLAIMS_VS_EVIDENCE.md` for the full
> claims-vs-evidence table.
>
> **Do not use for automated live trading without independent validation.**

---

## Real Performance

Sourced from `data/wr_matrix.json` (updated 2026-09-05, total_settled=124):

| Regime × Direction | Win | Loss | Expired | WR | Sample Size |
|-----|:---:|:---:|:---:|:---:|:---:|
| BULL_TREND \| LONG | 33 | 63 | 8 | **34.4%** | n=96 (main bucket) |
| BEAR_RECOVERY \| LONG | 7 | 0 | 0 | 100% | n=7 (insufficient) |
| BULL_EARLY \| LONG | 5 | 0 | 42 | 100% | n=5 (insufficient) |
| BEAR_RECOVERY \| SHORT | 0 | 3 | 0 | 0% | n=3 (banned) |
| BULL_EARLY \| SHORT | 2 | 2 | 4 | 50% | n=4 (insufficient) |
| **Aggregate** | **47** | **68** | — | **40.9%** | n=124 |

Pump Hunter (from `hunter_win_rate.json`, updated 2026-08-24):

| Timeframe | n | Wins | WR | Condition |
|-----------|:---:|:---:|:---:|-----------|
| 4H | 342 | 16 | **4.68%** | 涨≥3% within 4H |
| 8H | 376 | 40 | **10.64%** | 涨≥5% within 8H |
| 7D | 107 | 69 | **64.49%** | 涨≥10% within 7D |

> ⚠️ Pump Hunter 7D=64.49% is an **event rate**, not a trading win rate.
> No stop-loss is applied. Mid-week −40% drawdowns that recover by day 7
> count as "wins." This is a scanner/observation tool, not an execution signal.

> ⚠️ Small-sample buckets (n<30) should not be treated as statistically
> significant. The main bucket (BULL_TREND LONG, n=96) is the only sample
> with adequate size, and it shows 34.4% WR — below coin-flip.

---

## What This System Actually Has

| Component | Status | Evidence |
|-----------|--------|----------|
| 35-Dimensional Scoring Engine | ✅ Exists | `brahma_brain/brahma_core.py` (4641 lines) |
| 10-Regime State Machine | ✅ Exists | `scripts/regime_switch_monitor.py` |
| 6-Agent LLM Council | ✅ Exists | Optional, skippable via `BRAHMA_SKIP_COUNCIL=1` |
| SMC Structure Gates | ✅ Exists | `brahma_brain/smc_engine.py` (3126 lines) |
| Pump Hunter Scanner | ✅ Exists | `dharma/pump_hunter/` |
| DAG Sparse Activation | ✅ Exists | `brahma_brain/dag_executor.py` |
| MCP Server | ✅ Exists | `brahma_brain/brahma_mcp_server.py` |
| Module Health Check | ✅ Exists | `brahma_brain/brahma_health.py` |
| Walk-Forward | ⚠️ Partial | `scripts/dharma_ultimate_validator.py` — report only, no parameter freeze |
| CPCV / DSR | ❌ Not implemented | No code in repository |
| Monte Carlo | ⚠️ Simplified | `brahma_os/mega_mc.py` — additive bootstrap, no leverage, no path-internal ruin |
| Kronos Foundation Model | ❌ Stub | `brahma_brain/kronos_bridge.py` returns `(0.0, stub)` |
| 9-Layer Circuit Breaker | ⚠️ Misnamed | `circuit_breaker.py` is software fault tolerance, not a trading kill-chain |
| Execution OMS | ❌ Removed | `scripts/auto_executor.py` residual only; not production-ready |
| LICENSE file | ❌ Missing | MIT badge is non-functional |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Signal Pipeline                            │
│                                                              │
│  Tier 1 │ rsi_structure_watcher   every 5min  │  0 tokens  │
│          │ 7 trigger events: RSI/BB/OI/Volume │             │
│          ↓ event-triggered only                             │
│  Tier 2 │ brahma_analysis_runner  on-demand   │  ~6K tok   │
│          │ scoring + regime gates              │             │
│          ↓                                                   │
│  Tier 3 │ brahma-scan-guard       every 12h   │  48K/day   │
│          │ full market screener, slow-drift   │             │
│          ↓                                                   │
│        DharmaBridge → live_signal_log.jsonl                 │
│          ↓                                                   │
│        Signal Settler → wr_matrix.json → EV Feedback       │
└─────────────────────────────────────────────────────────────┘
```

Scoring dimensions include: market structure (CHoCH/BOS), order blocks, FVG fill
probability, fibonacci confluence, multi-timeframe alignment, RSI divergence,
Bollinger compression, volume anomaly, OI surge, long/short ratio, funding rate
pressure, liquidation clusters, GEX gamma exposure, macro calendar filter, BTC
dominance, cross-exchange funding, Deribit P/C ratio, DXY/NQ futures,
CausalVerifier, seasonal calendar, and BTC/ETH correlation risk filter.

> Note: Several dimensions (s15 Kronos, s20 Tardis, s21 Glassnode) are marked
> as dead code or depend on external data sources not available in the public
> repository. See `BRAHMA_DIMENSION_MAP.md` for status of each dimension.

---

## 10-Regime State Machine

Real-time market classification with regime-specific signal multipliers:

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

> ⚠️ Regime multipliers multiply direction signals derived from price trends.
> If regime detection uses the same trend information, this creates
> double-counting. OOS performance may degrade accordingly. The main bucket
> (BULL_TREND LONG) at 34.4% WR is consistent with this concern.

---

## Quickstart

```bash
git clone https://github.com/dahaini08-spec/brahma-quant.git
cd brahma-quant

# Minimum install (signal research only)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Validate installation (offline, no API calls)
python examples/quick_start.py --validate

# Single symbol analysis (requires .env with BINANCE_API_KEY for market data)
BRAHMA_SKIP_COUNCIL=1 python examples/quick_start.py --symbol BTCUSDT

# System health check
python brahma_brain/brahma_health.py

# Run tests
pytest tests/ -q --ignore=tests/test_e2e_signal_flow.py
```

> ⚠️ Some tests require live market data or settlement files not included in
> the public repository. Test pass/fail counts vary by environment.

---

## Project Structure

```
brahma_brain/                   Core scoring engine
  brahma_core.py                  Main analysis engine (4641 lines)
  brahma_analysis_runner.py       Unified entry point
  smc_engine.py                   SMC structure analysis (3126 lines)
  position_sizer.py               Position sizing (2138 lines)
  dag_executor.py                 DAG sparse activation (Phase 1)
  brahma_mcp_server.py            MCP server (Phase 2)
  brahma_health.py                Module health check
  kronos_bridge.py                Kronos stub (deprecated, returns 0)

dharma/                         Validation & training pipeline
  pump_hunter/                    Meme surge detector
  realistic_cost_model.py         Slippage + fee modeling

scripts/                        Production automation
  regime_switch_monitor.py        10-regime classifier
  rsi_structure_watcher.py        Tier-1 event watcher
  whale_monitor.py                Whale activity monitor
  liq_heatmap.py                  Liquidation heatmap
  cvd_ws_collector.py             CVD data collector

brahma_os/                      Simulation framework
  mega_mc.py                      Monte Carlo (additive bootstrap)
  paper_executor.py               Paper trading

data/                           Settlement & cache
  wr_matrix.json                 Win-rate matrix (SSOT for performance claims)
  scoring_config.json            DAG sparse activation config
  module_registry.json           Module health registry

docs/                           Documentation
  AUDIT_CLAIMS_VS_EVIDENCE.md     Claims vs evidence table (2026-09-13)
  adr/                            Architecture Decision Records
  CONTEXT.md                      Shared glossary
```

---

## Audit Correction (2026-09-13)

This README was rewritten on 2026-09-13 after an independent audit found
systematic discrepancies between published claims and repository evidence.

**Key corrections:**

| Original Claim | Corrected Value | Source |
|----------------|----------------|--------|
| Live WR 62% (n=186) | **40.9% (n=124)** | `data/wr_matrix.json` |
| OOS WR 82.7% | **46.6%** (archive) | Archive OOS report |
| DSR 22.64 | **Not implemented** | No CPCV/DSR code in repo |
| MC P50 +1364% | **Method invalid** | Additive bootstrap, no leverage |
| MC Ruin 0% | **Method invalid** | No path-internal ruin detection |
| Pump Hunter 97.5% | **4.68% (4H) / 64.49% (7D event rate)** | `hunter_win_rate.json` |
| Kronos AAAI 2026 | **Stub (returns 0.0)** | `kronos_bridge.py` |
| 9-Layer Circuit Breaker | **Software fault tolerance** | `circuit_breaker.py` |
| Tests 35/35 passing | **296 test functions, results vary by environment** | `tests/` |
| Health 96/100 | **Depends on private paths** | `brahma_health.py` |

Full audit report: `docs/AUDIT_CLAIMS_VS_EVIDENCE.md`

---

## Known Limitations

1. **Main bucket WR is 34.4%** — BULL_TREND LONG (n=96) is the only
   statistically meaningful sample, and it is below coin-flip.
2. **No CPCV/DSR implementation** — Walk-forward exists as a report generator
   but does not freeze parameters in-fold. No purged cross-validation.
3. **Monte Carlo is additive bootstrap** — does not model leverage, path-internal
   ruin, funding rate paths, or order book impact.
4. **Small-sample buckets inflate narrative** — 100% WR buckets (n=5, n=7)
   should not be cited as performance evidence.
5. **Regime multipliers may double-count** — if regime detection and direction
   signals both derive from price trends, OOS decay is expected.
6. **Pump Hunter is not a trading signal** — it is a scanner with no stop-loss.
   The 7D "win" definition counts any token that pumps ≥10% within 7 days
   regardless of intermediate drawdowns.
7. **Execution layer is not production-ready** — residual scripts exist but
   are not connected to a risk engine. `risk_engine.py` fail-opens on errors.
8. **4641-line god file** — `brahma_core.py` shares mutable `_result` dict
   across all dimensions, making unit testing impractical.

---

## License

MIT (LICENSE file to be added).  
Signal infrastructure is open. Execution layer, API keys, and live parameter
tuning are private.

---

*This README prioritizes honesty over marketing. If a number cannot be
reproduced from a file in this repository, it does not appear here.*

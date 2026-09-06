# Directory review — brahma_brain / dharma / scripts

Date: 2026-09-06  
Ref: `main` @ 7e40aab (plus this PR's hygiene diffs)

## Verdict

The three trees look like a **working trading desk workspace**, not a layered library.

- Many modules are real and large.
- Many others are 150–200 byte shims or empty stubs.
- Docs describe files that no longer exist (`dharma_core.py`, `dharma_runner.py`, `experiments/`).
- `scripts/` contains live-adjacent executors, hardcoded machine paths (`/root/.openclaw/...`), and (before this PR) hardcoded exchange credentials.

Do not treat README feature counts as an inventory of working code.

## brahma_brain/

Scoring + analysis package. Real entry points:

- `brahma_analysis_runner.py` (~85 KB) — advertised gateway
- `brahma_core.py` (~275 KB) plus `brahma_core_block_*.py` / `brahma_core_entry.py` / `brahma_core_step4.py`
- `smc_engine.py` (~134 KB), `fangcang_engine.py` (~121 KB), `position_sizer.py` (~88 KB)
- `brahma_health.py`, `market_state.py`, `regime_scorer.py`, `timing_filter.py`

Problems:

1. God module: `brahma_core.py` cannot be reviewed as a unit.
2. Shim farm: `capital_allocator.py`, `cvd_engine.py`, `divergence_engine.py`, `dynamic_sl.py`, `macro_calendar.py`, `signal_integrity_gate.py`, `kronos_bridge.py` are re-exports or stubs. `kronos_bridge.get_s23_kronos()` returns a neutral stub.
3. `brahma_brain/__init__.py` injects `sys.path`, aliases modules, monkeypatches `json.dumps`, and disables TLS verification for `urllib.request.urlopen`.
4. Runtime state in git: `brahma_state.json`, `gex_history_full.jsonl` (~849 KB).
5. Duplicate archives inside the package.
6. `_patch_module_interface` hides inconsistent function names.

## dharma/

`DHARMA.md` describes `dharma_core.py`, `dharma_runner.py`, `experiments/`, `results/` — those files are not in this tree.

What exists: `dharma_bus.py`, `dharma_factor_engine.py`, `dharma_weekly_report.py`, weight JSON/YAML, and `pump_hunter/`.

`pump_hunter/` is an operational scanner (`scan_and_alert.py` ~45 KB) plus committed dumps (`last_alerts.json` / `new_alerts.json` ~887 KB each).

Walk-forward / CPCV / Monte Carlo live under `arch/` and `scripts/dharma_backtest_*.py`. Documented regime win rates in `DHARMA.md` (roughly 24–41%) conflict with homepage 60–97% claims.

## scripts/

Desk automation. Largest / most sensitive: `auto_executor.py` (~119 KB), `brahma_1hao_analysis.py` (~112 KB), `rsi_structure_watcher.py` (~51 KB), `oi_advanced_scanner.py` (~59 KB), `cron_register_all.sh` (hardcoded OpenClaw paths and Jarvis IDs).

Problems: multiple analysis entries; machine coupling; secrets in source (fixed in this PR for two monitors); duplicated clients; extra archive folders.

## Tests / CI

Several tests skip deleted packages (`brahma_v6`, `dharma_simfactory`). This PR's workflow only runs import smoke + `examples/quick_start.py --validate`.

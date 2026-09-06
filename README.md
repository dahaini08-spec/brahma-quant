# Brahma-Quant

Crypto **signal research** framework for perpetual futures.

This public tree is the research / scoring layer (`brahma_brain`, `dharma`, watchers).  
It is **not** a turnkey live trading bot. Do not wire it to a funded mainnet account without your own validation.

## What it is

- Multi-factor scoring and regime classification (`brahma_brain`)
- Local validation helpers (`dharma`, `arch/`)
- Cron-style watchers under `scripts/` (many are machine-specific)

## What it is not

- Independently audited live performance
- A complete OMS (execution layer is not part of the public release)
- A small, tidy repo yet — clone size is large because archives and caches were committed

## Quick start

```bash
git clone https://github.com/dahaini08-spec/brahma-quant.git
cd brahma-quant
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Offline import check (no market data)
python examples/quick_start.py --validate

# Unit smoke
pytest tests/test_import_smoke.py -q
```

Optional research extras: `pip install -e ".[dev,research]"`.

To run a live-data analysis you need a local `.env` (copy from `.env.example`).  
Keep `BINANCE_TESTNET=true` unless you know why you need mainnet.  
Never commit `.env`.

```bash
cp .env.example .env
BRAHMA_SKIP_COUNCIL=1 python examples/quick_start.py --symbol BTCUSDT
```

## Layout

| Path | Role |
|---|---|
| `brahma_brain/` | Scoring engines. Real entry: `brahma_analysis_runner.py`. `brahma_core.py` is very large. |
| `dharma/` | Validation notes + `pump_hunter/` scanner. Docs in `DHARMA.md` describe files that are not all present. |
| `scripts/` | Ops / watchers / paper / executor. Treat `auto_executor.py` as unsafe on mainnet. |
| `tests/` | Mixed smoke / stress. Several tests skip deleted packages. |
| `archive/` | Historical dumps. Do not add more binaries here. |

Longer directory review: [`docs/OSS_AUDIT.md`](docs/OSS_AUDIT.md).

## Claims vs this tree

README marketing numbers (high win rates, DSR, Monte Carlo Sharpe) are **internal research claims**.  
They are not reproduced by CI. Treat them as hypotheses until a pinned dataset + script is published.

## Security

- API keys belong in `.env` or a secret manager only.
- If you forked this repo before 2026-09-06, rotate any Binance keys that may have been hardcoded in `scripts/whale_monitor.py` and `scripts/liq_heatmap.py`.
- `brahma_brain/__init__.py` currently disables TLS hostname verification for `urllib`. That is not acceptable for production.

## License

MIT. See `LICENSE`.

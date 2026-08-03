# sherweb-strategy

Public-source, credential-free Coinbase market research for [`Jbullis29/Sher-strat`](https://github.com/Jbullis29/Sher-strat) and the planned `sherweb.ai` website.

> **Current state:** source published on GitHub. GitHub Pages and the `sherweb.ai` custom domain are not yet enabled.

## Public/private boundary

This repository is designed to contain only:

- public Coinbase Exchange market-data access;
- deterministic strategy rules and tests;
- timestamped research snapshots;
- a static GitHub Pages site;
- sanitized, completed personal strategy results.

It must never contain Coinbase credentials, JWT/private-key code, account IDs, order IDs, quantities, balances, open positions, transaction identifiers, or local private-ledger paths.

## Data meanings

### Algorithm findings

`site/data/findings/` contains approximately two-hour public-market observations. Every qualifying finding from a completed scan is published when the market-wide risk gate is normal. Repeated appearances are observations, not additional paper trades. Risk-off, no-findings, and data-failure scans are preserved.

A finding is **not** an executed trade and is not automatically included in performance.

### Personal realized performance

`site/data/performance/realized-results.json` contains a sanitized export of completed personal strategy trades. It excludes open positions and ordinary holdings. It is not the performance of every public finding.

## Local verification

Requires Python 3.13 and Node.js only for the optional JavaScript syntax check. The scanner itself uses the Python standard library.

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scanner/*.py tools/*.py
node --check site/assets/site.js
python3 tools/build_site.py
```

The builder validates all JSON, removes stale `dist/`, and copies an exact whitelist. Only `dist/` is suitable for website deployment.

Run a live credential-free scan locally:

```bash
python3 -m scanner.run_scan --output-root site/data/findings --source-commit local
python3 tools/build_site.py
```

Run a local preview:

```bash
python3 -m http.server 8790 --bind 127.0.0.1 --directory dist
```

## GitHub automation

`.github/workflows/scan-and-publish.yml` is configured to:

1. run at minute 17 every second UTC hour and on manual dispatch;
2. run the full test suite;
3. fetch unauthenticated Coinbase public data;
4. append one immutable snapshot;
5. build the validated static artifact;
6. commit only `site/data/findings`;
7. upload only `dist/` to GitHub Pages.

Official GitHub actions are pinned to immutable commit SHAs. The workflow requires `contents: write`, `pages: write`, and `id-token: write` for snapshot commits and Pages deployment.

## Watchlist

BTC, ETH, SOL, XLM, DOGE, ADA, AVAX, LINK, LTC, MORPHO.

## Disclosures

This is an early personal research project, not financial advice, an execution service, or a guarantee. Crypto assets can lose substantial value. Findings may be stale, data and code can be wrong, and execution costs can differ materially.

This project is not affiliated with, sponsored by, endorsed by, or operated by Sherweb Inc. It is not affiliated with, sponsored by, or endorsed by Coinbase.

## License

No open-source license has been selected. Public visibility does not itself grant broad reuse rights; licensing remains a separate decision.

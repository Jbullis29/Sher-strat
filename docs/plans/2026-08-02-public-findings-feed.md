# Public Two-Hour Findings Feed Implementation Plan

> **For Hermes:** Implement task-by-task with strict RED-GREEN-REFACTOR and independent review before completion.

**Goal:** Build a public, credential-free Coinbase market scanner that publishes every qualifying two-hour finding as an immutable research snapshot and serves findings separately from personal realized performance on one GitHub Pages site.

**Architecture:** A pure-stdlib Python package fetches Coinbase Exchange public candles/tickers, applies the existing conservative strategy formulas, classifies market regime, and writes validated JSON snapshots. The static site reads `latest.json`, an append-only snapshot index, and a separately sanitized personal-performance dataset. A GitHub Actions workflow runs at minute 17 every second UTC hour, commits generated snapshot data, and deploys a whitelisted Pages artifact.

**Tech Stack:** Python 3.13 stdlib, `unittest`, static HTML/CSS/JavaScript, GitHub Actions, GitHub Pages.

---

## Task 1: Public rules and deterministic scoring core

**Files:**
- Create: `scanner/rules.py`
- Create: `tests/test_rules.py`

**Steps:**
1. Write failing tests for targets, fee tiers, spread, reward/risk, filters, ranking, and market-risk thresholds.
2. Run `python3 -m unittest tests.test_rules -v` and confirm RED failures from missing production code.
3. Implement the minimum pure functions with finite-number validation.
4. Re-run the focused and full suite; keep it green.

## Task 2: Public Coinbase data adapter

**Files:**
- Create: `scanner/coinbase_public.py`
- Create: `tests/test_coinbase_public.py`

**Steps:**
1. Write failing tests around normalized candles/tickers using injected transport fixtures.
2. Require at least 169 hourly candles, valid ascending timestamps, positive finite OHLC values, and graceful network/schema failure.
3. Implement HTTPS-only public API fetching with timeout and explicit User-Agent.
4. Run focused and full tests.

## Task 3: Snapshot schema and append-only publisher

**Files:**
- Create: `scanner/snapshot.py`
- Create: `tests/test_snapshot.py`
- Create: `site/data/findings/latest.json`
- Create: `site/data/findings/index.json`

**Steps:**
1. Write failing tests for statuses `normal`, `risk_off`, `no_findings`, and `data_failure`.
2. Require UTC timestamps, scan expiration, source commit, algorithm version, finite public numbers, constrained symbols, and no private fields.
3. Write snapshots with collision-resistant filenames using atomic replacement; reject overwrite of an existing snapshot.
4. Maintain `latest.json` and append-only `index.json` without deleting prior entries.
5. Run focused and full tests.

## Task 4: Scanner command and live dry run

**Files:**
- Create: `scanner/run_scan.py`
- Create: `tests/test_run_scan.py`

**Steps:**
1. Write failing orchestration tests with injected market fixtures.
2. Implement scan of BTC, ETH, SOL, XLM, DOGE, ADA, AVAX, LINK, LTC, MORPHO.
3. Publish every qualifying finding without active-position suppression or performance attribution.
4. Fail closed into a `data_failure` snapshot when BTC or market breadth coverage is insufficient.
5. Execute one real public-data dry run and inspect its sanitized JSON.

## Task 5: Findings and performance website separation

**Files:**
- Create: `site/index.html`
- Create: `site/findings/index.html`
- Create: `site/performance/index.html`
- Create: `site/methodology/index.html`
- Create: `site/disclosures/index.html`
- Create: `site/data/performance/realized-results.js`
- Create: `tools/build_site.py`
- Create: `tests/test_site_build.py`

**Steps:**
1. Write failing build tests for an exact output whitelist, no private fields, and required non-affiliation/risk labels.
2. Adapt the existing verified design and sanitized personal ledger.
3. Make findings explicitly “research snapshots—not executed trades.”
4. Make performance explicitly “personal realized results—not performance of every finding.”
5. Build atomically into `dist/` and verify browser rendering, contrast, mobile behavior, and no JS errors.

## Task 6: GitHub Actions and Pages configuration

**Files:**
- Create: `.github/workflows/scan-and-publish.yml`
- Create: `tests/test_workflow.py`
- Modify: `README.md`

**Steps:**
1. Write failing workflow-structure tests.
2. Add `schedule: '17 */2 * * *'` and `workflow_dispatch`.
3. Use minimal permissions, concurrency protection, tests before publishing, bot commits only for generated findings, and official Pages actions pinned to immutable commit SHAs before public release.
4. Document public/private boundaries and local verification.

## Task 7: Verification and review

1. Run all unit tests and Python compilation.
2. Run live public scan twice; verify unique immutable snapshots and correct latest/index behavior.
3. Scan the repository and `dist/` for secrets, private paths, IDs, quantities, balances, JWT code, and Coinbase credentials.
4. Verify the Tailnet-hosted local preview in a browser.
5. Obtain independent review of math, privacy, GitHub workflow permissions, append-only semantics, website wording, accessibility, and packaging.
6. Fix blockers and re-review until passed.
7. Stop before any GitHub remote creation or push; request explicit publication authorization.

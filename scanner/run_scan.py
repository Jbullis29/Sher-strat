"""Run the credential-free watchlist scan and publish a validated snapshot."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .coinbase_public import fetch_market_inputs
from .rules import PRODUCTS, market_risk_regime, score_market_input
from .snapshot import create_snapshot, publish_snapshot

Fetcher = Callable[[str], dict[str, Any] | None]
Scorer = Callable[[str, dict[str, Any]], dict[str, Any] | None]


def collect_scan(*, fetcher: Fetcher = fetch_market_inputs, scorer: Scorer = score_market_input) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    errors: list[str] = []
    for product_id in PRODUCTS:
        try:
            market_input = fetcher(product_id)
        except Exception:
            errors.append(f"{product_id}: market_data_unavailable")
            continue
        if market_input is None:
            errors.append(f"{product_id}: market_data_unavailable")
            continue
        try:
            item = scorer(product_id, market_input)
        except Exception:
            errors.append(f"{product_id}: scoring_failed")
            continue
        if item is None:
            errors.append(f"{product_id}: no_score")
            continue
        scored.append(item)
    return scored, market_risk_regime(scored, errors)


def run_and_publish(
    output_root: Path,
    *,
    observed_at: dt.datetime | None = None,
    source_commit: str = "local",
    fetcher: Fetcher = fetch_market_inputs,
    scorer: Scorer = score_market_input,
) -> tuple[dict[str, Any], Path]:
    observed_at = observed_at or dt.datetime.now(dt.timezone.utc)
    scored, regime = collect_scan(fetcher=fetcher, scorer=scorer)
    snapshot = create_snapshot(scored, regime, observed_at, source_commit=source_commit)
    relative = publish_snapshot(snapshot, output_root)
    return snapshot, relative


def _source_commit() -> str:
    value = os.environ.get("GITHUB_SHA", "local").lower()
    if value != "local" and (not 6 <= len(value) <= 40 or any(character not in "0123456789abcdef" for character in value)):
        return "local"
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a public Coinbase research snapshot")
    parser.add_argument("--output-root", type=Path, default=Path("site/data/findings"))
    parser.add_argument("--source-commit", default=_source_commit())
    arguments = parser.parse_args()
    snapshot, relative = run_and_publish(arguments.output_root, source_commit=arguments.source_commit)
    print(json.dumps({
        "observed_at": snapshot["observed_at"],
        "status": snapshot["status"],
        "finding_count": snapshot["finding_count"],
        "snapshot": relative.as_posix(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

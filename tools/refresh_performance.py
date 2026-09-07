#!/usr/bin/env python3
"""Create the sanitized public performance JSON from a private completed-trade ledger."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_site import _validate_performance

APY = 0.04
ASSET_PATTERN = re.compile(r"^[A-Z0-9]{2,12}$")
METHODOLOGY = (
    "Realized, fee-inclusive Coinbase strategy trades only. Buy prices are rounded effective unit cost "
    "including exposed buy fees; sell prices are rounded effective unit proceeds net of exposed sell fees. "
    "Quantities and transaction identifiers remain private. Results exclude unrealized/open positions and "
    "non-strategy holdings. The 4% APY comparison uses each trade's actual deployed capital and holding period."
)


def finite_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def public_asset(value: Any, field: str) -> str:
    if not isinstance(value, str) or not ASSET_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a public asset symbol")
    return value


def public_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc).date().isoformat()


def private_timestamp(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def rounded_price(value: float) -> float:
    digits = 4 if value >= 10 else 6 if value >= 1 else 8
    return round(value, digits)


def yield_benchmark(cost: float, held_days: float) -> float:
    return cost * ((1 + APY) ** (held_days / 365) - 1)


def build_public_data(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("closed"), list) or not ledger["closed"]:
        raise ValueError("private ledger must contain completed trades")
    generated_at = ledger.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError("ledger generated_at must be an ISO timestamp")
    try:
        generated = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ledger generated_at must be an ISO timestamp") from exc
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("ledger generated_at must include a timezone")

    trades: list[dict[str, Any]] = []
    cumulative = 0.0
    for number, row in enumerate(ledger["closed"], start=1):
        if not isinstance(row, dict):
            raise ValueError("closed ledger rows must be objects")
        if row.get("status") != "closed":
            raise ValueError(f"trade {number} is not explicitly closed")
        asset = public_asset(row.get("asset"), f"trade {number} asset")
        entry_time = private_timestamp(row.get("entry_date"), f"trade {number} entry_date")
        exit_time = private_timestamp(row.get("exit_date"), f"trade {number} exit_date")
        entry_date = entry_time.date().isoformat()
        exit_date = exit_time.date().isoformat()
        cost = finite_number(row.get("cost"), f"trade {number} cost")
        pnl = finite_number(row.get("pnl"), f"trade {number} pnl")
        return_pct = finite_number(row.get("return_pct"), f"trade {number} return_pct")
        held_days = finite_number(row.get("held_days"), f"trade {number} held_days")
        buy_price = finite_number(row.get("buy_price"), f"trade {number} buy_price")
        sell_price = finite_number(row.get("sell_price"), f"trade {number} sell_price")
        if cost <= 0 or held_days < 0 or buy_price <= 0 or sell_price <= 0:
            raise ValueError(f"trade {number} contains invalid nonpositive values")
        if exit_time < entry_time:
            raise ValueError(f"trade {number} exits before entry")
        expected_days = (exit_time - entry_time).total_seconds() / 86400
        expected_return = pnl / cost * 100
        price_return = (sell_price / buy_price - 1) * 100
        if abs(held_days - expected_days) > 1e-6:
            raise ValueError(f"trade {number} holding duration is inconsistent")
        if abs(return_pct - expected_return) > 1e-6 or abs(return_pct - price_return) > 1e-6:
            raise ValueError(f"trade {number} accounting values are inconsistent")
        public_buy_price = rounded_price(buy_price)
        public_sell_price = rounded_price(sell_price)
        if public_buy_price <= 0 or public_sell_price <= 0:
            raise ValueError(f"trade {number} price is below public precision")
        public_cost = round(cost, 2)
        public_pnl = round(pnl, 2)
        public_days = round(held_days, 2)
        benchmark = round(yield_benchmark(public_cost, public_days), 2)
        cumulative += public_pnl
        trades.append({
            "number": number,
            "asset": asset,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "buy_price": public_buy_price,
            "sell_price": public_sell_price,
            "held_days": public_days,
            "cost": public_cost,
            "pnl": public_pnl,
            "return_pct": round(return_pct, 2),
            "yield_benchmark": benchmark,
            "beat_yield": public_pnl > benchmark,
            "cumulative_pnl": round(cumulative, 2),
        })

    pnls = [trade["pnl"] for trade in trades]
    costs = [trade["cost"] for trade in trades]
    returns = [trade["return_pct"] for trade in trades]
    days = [trade["held_days"] for trade in trades]
    gross_wins = sum(value for value in pnls if value >= 0)
    gross_losses = sum(value for value in pnls if value < 0)
    total_pnl = sum(pnls)
    total_cost = sum(costs)
    benchmark_total = sum(trade["yield_benchmark"] for trade in trades)
    wins = sum(value >= 0 for value in pnls)
    summary = {
        "capital_deployed": round(total_cost, 2),
        "completed_trades": len(trades),
        "gross_losses": round(gross_losses, 2),
        "gross_wins": round(gross_wins, 2),
        "largest_loss": round(min(pnls), 2),
        "losses": len(trades) - wins,
        "median_holding_days": round(statistics.median(days), 2),
        "median_trade_return_pct": round(statistics.median(returns), 2),
        "net_realized_pnl": round(total_pnl, 2),
        "outperformance_vs_yield": round(total_pnl - benchmark_total, 2),
        "profit_factor": round(gross_wins / abs(gross_losses), 2) if gross_losses else 0.0,
        "realized_return_pct": round(total_pnl / total_cost * 100, 2),
        "win_rate_pct": round(wins / len(trades) * 100, 1),
        "wins": wins,
        "yield_benchmark_pnl": round(benchmark_total, 2),
    }
    return {
        "benchmark_apy_pct": APY * 100,
        "generated_at": generated.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "methodology": METHODOLOGY,
        "summary": summary,
        "trades": trades,
    }


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True, help="Path to a private ledger JSON file")
    parser.add_argument("--output", default="site/data/performance/realized-results.json")
    args = parser.parse_args()
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    public_data = build_public_data(ledger)
    _validate_performance(public_data)
    write_atomic(Path(args.output), public_data)
    print(f"Wrote {args.output} with {len(public_data['trades'])} completed trades")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

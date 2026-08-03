"""Build a strictly whitelisted GitHub Pages artifact."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.snapshot import validate_snapshot

STATIC_FILES = (
    "index.html", "404.html", "robots.txt", "sitemap.xml", "assets/styles.css", "assets/site.js",
    "findings/index.html", "performance/index.html", "methodology/index.html", "disclosures/index.html",
)
PERFORMANCE_FIELDS = {"benchmark_apy_pct", "generated_at", "methodology", "summary", "trades"}
SUMMARY_FIELDS = {
    "capital_deployed", "completed_trades", "gross_losses", "gross_wins", "largest_loss", "losses",
    "median_holding_days", "median_trade_return_pct", "net_realized_pnl", "outperformance_vs_yield",
    "profit_factor", "realized_return_pct", "win_rate_pct", "wins", "yield_benchmark_pnl",
}
SUMMARY_COUNT_FIELDS = {"completed_trades", "losses", "wins"}
TRADE_FIELDS = {"asset", "beat_yield", "cost", "cumulative_pnl", "entry_date", "exit_date", "held_days", "number", "pnl", "return_pct", "yield_benchmark"}
ASSET_PATTERN = re.compile(r"^[A-Z0-9]{2,12}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRIVATE_FIELDS = {"account_id", "order_id", "entry_order_id", "exit_order_id", "quantity", "balance", "filled_size", "credentials", "api_key", "secret", "jwt"}


def _require_source_file(source: Path, path: Path) -> None:
    try:
        relative = path.relative_to(source)
    except ValueError as exc:
        raise ValueError("public input is outside the site source") from exc
    current = source
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlinked public input is forbidden: {relative.as_posix()}")
    if not path.is_file():
        raise FileNotFoundError(f"required public file missing: {relative.as_posix()}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("public input cannot be resolved") from exc
    if not resolved.is_relative_to(source):
        raise ValueError("public input resolves outside the site source")


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _finite_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid finite number for {field}")
    return float(value)


def _date(value: Any, field: str) -> dt.date:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError(f"invalid date for {field}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date for {field}") from exc


def _validate_performance(data: Any) -> None:
    if not isinstance(data, dict) or set(data) != PERFORMANCE_FIELDS:
        raise ValueError("performance data does not match public schema")
    if set(_walk_keys(data)) & PRIVATE_FIELDS:
        raise ValueError("private field in performance data")
    benchmark = _finite_number(data["benchmark_apy_pct"], "benchmark_apy_pct")
    if not 0 < benchmark <= 100:
        raise ValueError("invalid benchmark APY")
    generated_at = data["generated_at"]
    if not isinstance(generated_at, str):
        raise ValueError("generated_at must be an ISO timestamp")
    try:
        generated = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid generated_at timestamp") from exc
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    methodology = data["methodology"]
    if not isinstance(methodology, str) or not 20 <= len(methodology) <= 1000 or any(ord(char) < 32 for char in methodology) or "<" in methodology or ">" in methodology:
        raise ValueError("invalid public methodology")
    summary = data["summary"]
    if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
        raise ValueError("performance summary does not match public schema")
    for field in SUMMARY_COUNT_FIELDS:
        if not isinstance(summary[field], int) or isinstance(summary[field], bool) or summary[field] < 0:
            raise ValueError(f"invalid summary count: {field}")
    for field in SUMMARY_FIELDS - SUMMARY_COUNT_FIELDS:
        _finite_number(summary[field], field)
    if not isinstance(data["trades"], list) or not data["trades"]:
        raise ValueError("performance trades required")
    cumulative = 0.0
    wins = 0
    losses = 0
    capital = 0.0
    gross_wins = 0.0
    gross_losses = 0.0
    yield_total = 0.0
    holding_days: list[float] = []
    trade_returns: list[float] = []
    trade_pnls: list[float] = []
    for position, trade in enumerate(data["trades"], start=1):
        if not isinstance(trade, dict) or set(trade) != TRADE_FIELDS:
            raise ValueError("invalid public trade fields")
        if not isinstance(trade["number"], int) or isinstance(trade["number"], bool) or trade["number"] != position:
            raise ValueError("invalid public trade number")
        if not isinstance(trade["asset"], str) or not ASSET_PATTERN.fullmatch(trade["asset"]):
            raise ValueError("invalid public trade asset")
        entry_date = _date(trade["entry_date"], "entry_date")
        exit_date = _date(trade["exit_date"], "exit_date")
        if exit_date < entry_date:
            raise ValueError("trade exit precedes entry")
        if not isinstance(trade["beat_yield"], bool):
            raise ValueError("invalid benchmark verdict")
        cost = _finite_number(trade["cost"], "cost")
        held_days = _finite_number(trade["held_days"], "held_days")
        pnl = _finite_number(trade["pnl"], "pnl")
        return_pct = _finite_number(trade["return_pct"], "return_pct")
        yield_benchmark = _finite_number(trade["yield_benchmark"], "yield_benchmark")
        cumulative_value = _finite_number(trade["cumulative_pnl"], "cumulative_pnl")
        if cost <= 0 or held_days < 0 or yield_benchmark < 0:
            raise ValueError("invalid nonnegative trade values")
        calendar_days = (exit_date - entry_date).days
        if abs(held_days - calendar_days) >= 1:
            raise ValueError("holding duration is incompatible with public dates")
        expected_yield = cost * (benchmark / 100) * held_days / 365
        if abs(yield_benchmark - expected_yield) > 0.02:
            raise ValueError("trade yield benchmark mismatch")
        if abs(return_pct - pnl / cost * 100) > 0.02:
            raise ValueError("trade return does not match P/L and cost")
        cumulative += pnl
        if abs(cumulative_value - round(cumulative, 2)) > 0.005:
            raise ValueError("trade cumulative P/L mismatch")
        if trade["beat_yield"] != (pnl > yield_benchmark):
            raise ValueError("trade benchmark verdict mismatch")
        capital += cost
        yield_total += yield_benchmark
        holding_days.append(held_days)
        trade_returns.append(return_pct)
        trade_pnls.append(pnl)
        if pnl >= 0:
            wins += 1
            gross_wins += pnl
        else:
            losses += 1
            gross_losses += pnl
    if summary["completed_trades"] != len(data["trades"]) or summary["wins"] != wins or summary["losses"] != losses:
        raise ValueError("performance summary count mismatch")
    if gross_losses == 0:
        raise ValueError("profit factor is undefined without a realized loss")
    expected = {
        "capital_deployed": (capital, 2),
        "gross_wins": (gross_wins, 2),
        "gross_losses": (gross_losses, 2),
        "largest_loss": (min(trade_pnls), 2),
        "median_holding_days": (statistics.median(holding_days), 2),
        "median_trade_return_pct": (statistics.median(trade_returns), 2),
        "net_realized_pnl": (cumulative, 2),
        "yield_benchmark_pnl": (yield_total, 2),
        "outperformance_vs_yield": (cumulative - yield_total, 2),
        "profit_factor": (gross_wins / abs(gross_losses), 2),
        "realized_return_pct": (cumulative / capital * 100, 2),
        "win_rate_pct": (wins / len(data["trades"]) * 100, 1),
    }
    for field, (value, digits) in expected.items():
        tolerance = 0.5 * 10 ** (-digits)
        if abs(float(summary[field]) - round(value, digits)) >= tolerance:
            raise ValueError(f"performance summary mismatch: {field}")
    json.dumps(data, allow_nan=False)


def _validated_data_files(source: Path) -> list[Path]:
    findings = source / "data" / "findings"
    latest_path = findings / "latest.json"
    index_path = findings / "index.json"
    _require_source_file(source, latest_path)
    _require_source_file(source, index_path)
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    validate_snapshot(latest)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, dict) or set(index) != {"schema_version", "snapshots"} or index["schema_version"] != 1 or not isinstance(index["snapshots"], list):
        raise ValueError("invalid findings index")
    if not index["snapshots"]:
        raise ValueError("findings archive must contain at least one indexed snapshot")
    snapshot_files: list[Path] = []
    indexed_paths: set[str] = set()
    indexed_times: set[str] = set()
    previous_time: str | None = None
    newest_snapshot: dict[str, Any] | None = None
    for entry in index["snapshots"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "observed_at", "current_until", "status", "finding_count", "source_commit"}:
            raise ValueError("invalid findings index entry")
        relative = str(entry["path"])
        if relative.startswith("/") or ".." in Path(relative).parts or not relative.startswith("snapshots/") or not relative.endswith(".json"):
            raise ValueError("invalid findings snapshot path")
        path = findings / relative
        _require_source_file(source, path)
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        validate_snapshot(snapshot)
        for field in ("observed_at", "current_until", "status", "finding_count", "source_commit"):
            if snapshot[field] != entry[field]:
                raise ValueError("findings index does not match snapshot")
        if relative in indexed_paths or entry["observed_at"] in indexed_times:
            raise ValueError("duplicate findings index entry")
        if previous_time is not None and entry["observed_at"] > previous_time:
            raise ValueError("findings index is not newest-first")
        if newest_snapshot is None:
            newest_snapshot = snapshot
        indexed_paths.add(relative)
        indexed_times.add(entry["observed_at"])
        previous_time = entry["observed_at"]
        snapshot_files.append(path)
    actual_paths = {path.relative_to(findings).as_posix() for path in (findings / "snapshots").rglob("*.json")}
    if actual_paths != indexed_paths:
        raise ValueError("findings index and snapshot archive differ")
    if index["snapshots"] and latest != newest_snapshot:
        raise ValueError("latest findings do not exactly match newest archived snapshot")
    performance_path = source / "data" / "performance" / "realized-results.json"
    _require_source_file(source, performance_path)
    _validate_performance(json.loads(performance_path.read_text(encoding="utf-8")))
    return [latest_path, index_path, performance_path, *snapshot_files]


def build_site(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    data_files = _validated_data_files(source)
    for relative in STATIC_FILES:
        path = source / relative
        _require_source_file(source, path)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.build-", dir=destination.parent))
    try:
        for relative in STATIC_FILES:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        for path in data_files:
            relative = path.relative_to(source)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        files = {path.relative_to(temporary).as_posix() for path in temporary.rglob("*") if path.is_file()}
        expected = set(STATIC_FILES) | {path.relative_to(source).as_posix() for path in data_files}
        if files != expected:
            raise RuntimeError("unexpected deployment bundle")
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    build_site(root / "site", root / "dist")
    print("Built validated GitHub Pages artifact in dist/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

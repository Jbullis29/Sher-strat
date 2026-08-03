"""Validated public research snapshots and append-only filesystem publishing."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

ALGORITHM_VERSION = "1.0.0"
CURRENT_WINDOW = dt.timedelta(hours=2)
PRODUCT_PATTERN = re.compile(r"^[A-Z0-9]{2,12}-USD$")
SOURCE_PATTERN = re.compile(r"^(?:local|[0-9a-f]{6,40})$")
STATUSES = {"normal", "no_findings", "risk_off", "data_failure"}
PRIVATE_FIELDS = {"account_id", "order_id", "entry_order_id", "exit_order_id", "quantity", "balance", "filled_size", "credentials", "api_key", "secret", "jwt"}
FINDING_NUMBERS = {
    "reference_price", "suggested_limit_buy", "do_not_chase_above", "possible_target", "invalidation",
    "gross_upside", "estimated_net_upside", "estimated_round_trip_cost", "change_24h", "change_7d",
    "current_vs_sma72", "pullback_from_7d_high", "spread", "reward_to_risk",
    "downside_to_invalidation", "ranking_score",
}
FINDING_FIELDS = {"product_id", *FINDING_NUMBERS, "reasons", "cautions"}
TOP_FIELDS = {
    "schema_version", "algorithm_version", "source_commit", "observed_at", "current_until",
    "status", "market_regime", "findings", "finding_count", "notice",
}
REGIME_FIELDS = {"data_complete", "risk_off", "products_loaded", "below_sma_fraction", "reasons", "errors", "btc"}


def _utc_text(value: dt.datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("UTC timestamp ending in Z required")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid UTC timestamp") from exc
    if parsed.microsecond:
        raise ValueError("fractional timestamp not allowed")
    if value != _utc_text(parsed):
        raise ValueError("noncanonical UTC timestamp")
    return parsed


def _safe_text(value: Any, *, maximum: int = 300) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid public text")
    if any(ord(char) < 32 for char in value) or "<" in value or ">" in value:
        raise ValueError("unsafe public text")
    return value


def _finite(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("public numbers must be finite")
    return float(value)


def _sanitize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("invalid public text list")
    return [_safe_text(item) for item in value]


def _sanitize_finding(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("finding must be an object")
    lowered = {str(key).lower() for key in item}
    if lowered & PRIVATE_FIELDS:
        raise ValueError("private field is not publishable")
    permitted_input = FINDING_FIELDS | {"qualifies"}
    if set(item) - permitted_input:
        raise ValueError(f"unknown finding fields: {sorted(set(item) - permitted_input)}")
    product_id = item.get("product_id")
    if not isinstance(product_id, str) or not PRODUCT_PATTERN.fullmatch(product_id):
        raise ValueError("invalid product symbol")
    result: dict[str, Any] = {"product_id": product_id}
    for field in FINDING_NUMBERS:
        value = _finite(item.get(field))
        if field in {"reference_price", "suggested_limit_buy", "do_not_chase_above", "possible_target", "invalidation", "estimated_round_trip_cost", "spread", "reward_to_risk", "downside_to_invalidation"} and value < 0:
            raise ValueError(f"{field} cannot be negative")
        result[field] = value
    result["reasons"] = _sanitize_text_list(item.get("reasons"))
    result["cautions"] = _sanitize_text_list(item.get("cautions"))
    return result


def _sanitize_regime(regime: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(regime, dict):
        raise ValueError("market regime must be an object")
    required = {"data_complete", "risk_off", "products_loaded", "below_sma_fraction", "reasons", "errors"}
    if not required.issubset(regime) or set(regime) - REGIME_FIELDS:
        raise ValueError("invalid market regime fields")
    if not isinstance(regime["data_complete"], bool) or not isinstance(regime["risk_off"], bool):
        raise ValueError("market regime flags must be booleans")
    products = regime["products_loaded"]
    if not isinstance(products, int) or isinstance(products, bool) or not 0 <= products <= 10:
        raise ValueError("invalid loaded-product count")
    breadth = regime["below_sma_fraction"]
    if breadth is not None and not 0 <= _finite(breadth) <= 1:
        raise ValueError("invalid market breadth")
    btc = regime.get("btc")
    btc_public = None
    if btc is not None:
        if not isinstance(btc, dict):
            raise ValueError("invalid BTC regime summary")
        btc_public = {
            "change_24h": _finite(btc.get("change_24h")),
            "change_7d": _finite(btc.get("change_7d")),
            "current_vs_sma72": _finite(btc.get("current_vs_sma72")),
        }
    return {
        "data_complete": regime["data_complete"],
        "risk_off": regime["risk_off"],
        "products_loaded": products,
        "below_sma_fraction": None if breadth is None else float(breadth),
        "btc": btc_public,
        "reasons": _sanitize_text_list(regime["reasons"]),
        "errors": _sanitize_text_list(regime["errors"]),
    }


def create_snapshot(
    scored: list[dict[str, Any]],
    regime: dict[str, Any],
    observed_at: dt.datetime,
    *,
    source_commit: str = "local",
) -> dict[str, Any]:
    observed_text = _utc_text(observed_at)
    if not isinstance(source_commit, str) or not SOURCE_PATTERN.fullmatch(source_commit):
        raise ValueError("source commit must be 'local' or a hexadecimal git commit")
    clean_regime = _sanitize_regime(regime)
    qualifying: list[dict[str, Any]] = []
    for item in scored:
        if not isinstance(item, dict):
            raise ValueError("scored item must be an object")
        lowered = {str(key).lower() for key in item}
        if lowered & PRIVATE_FIELDS:
            raise ValueError("private field is not publishable")
        if item.get("qualifies") is True:
            qualifying.append(_sanitize_finding(item))
    qualifying.sort(key=lambda item: (-item["ranking_score"], item["product_id"]))
    if not clean_regime["data_complete"]:
        status = "data_failure"
        qualifying = []
    elif clean_regime["risk_off"]:
        status = "risk_off"
        qualifying = []
    elif qualifying:
        status = "normal"
    else:
        status = "no_findings"
    snapshot = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "source_commit": source_commit,
        "observed_at": observed_text,
        "current_until": _utc_text(observed_at + CURRENT_WINDOW),
        "status": status,
        "market_regime": clean_regime,
        "findings": qualifying,
        "finding_count": len(qualifying),
        "notice": "Research snapshot, not an executed trade or financial advice. Prices may change before the next scan.",
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != TOP_FIELDS:
        raise ValueError("snapshot fields do not match the public schema")
    if snapshot["schema_version"] != 1 or snapshot["algorithm_version"] != ALGORITHM_VERSION:
        raise ValueError("unsupported snapshot schema or algorithm version")
    if not isinstance(snapshot["source_commit"], str) or not SOURCE_PATTERN.fullmatch(snapshot["source_commit"]):
        raise ValueError("invalid source commit")
    observed = _parse_utc(snapshot["observed_at"])
    current_until = _parse_utc(snapshot["current_until"])
    if current_until - observed != CURRENT_WINDOW:
        raise ValueError("snapshot freshness window must be two hours")
    if snapshot["status"] not in STATUSES:
        raise ValueError("invalid snapshot status")
    clean_regime = _sanitize_regime(snapshot["market_regime"])
    if clean_regime != snapshot["market_regime"]:
        raise ValueError("market regime is not normalized")
    if not isinstance(snapshot["findings"], list):
        raise ValueError("findings must be a list")
    if [_sanitize_finding(item) for item in snapshot["findings"]] != snapshot["findings"]:
        raise ValueError("findings are not normalized")
    if snapshot["finding_count"] != len(snapshot["findings"]):
        raise ValueError("finding count mismatch")
    if not clean_regime["data_complete"]:
        expected_status = "data_failure"
    elif clean_regime["risk_off"]:
        expected_status = "risk_off"
    elif snapshot["findings"]:
        expected_status = "normal"
    else:
        expected_status = "no_findings"
    if snapshot["status"] != expected_status:
        raise ValueError("snapshot status contradicts market regime or findings")
    _safe_text(snapshot["notice"], maximum=500)
    json.dumps(snapshot, allow_nan=False)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


INDEX_ENTRY_FIELDS = {"path", "observed_at", "current_until", "status", "finding_count", "source_commit"}


def _load_valid_index(root: Path) -> dict[str, Any]:
    index_path = root / "index.json"
    if not index_path.exists():
        return {"schema_version": 1, "snapshots": []}
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid existing snapshot index") from exc
    if not isinstance(index, dict) or set(index) != {"schema_version", "snapshots"} or index["schema_version"] != 1 or not isinstance(index["snapshots"], list):
        raise ValueError("invalid existing snapshot index")
    seen_paths: set[str] = set()
    seen_times: set[str] = set()
    previous_time: str | None = None
    for entry in index["snapshots"]:
        if not isinstance(entry, dict) or set(entry) != INDEX_ENTRY_FIELDS:
            raise ValueError("invalid existing snapshot index entry")
        relative_text = entry["path"]
        if not isinstance(relative_text, str):
            raise ValueError("invalid snapshot path in index")
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or not relative_text.startswith("snapshots/") or not relative_text.endswith(".json"):
            raise ValueError("invalid snapshot path in index")
        observed = _parse_utc(entry["observed_at"])
        current_until = _parse_utc(entry["current_until"])
        if current_until - observed != CURRENT_WINDOW or entry["status"] not in STATUSES:
            raise ValueError("invalid snapshot metadata in index")
        if not isinstance(entry["finding_count"], int) or isinstance(entry["finding_count"], bool) or entry["finding_count"] < 0:
            raise ValueError("invalid finding count in index")
        if not isinstance(entry["source_commit"], str) or not SOURCE_PATTERN.fullmatch(entry["source_commit"]):
            raise ValueError("invalid source commit in index")
        if relative_text in seen_paths or entry["observed_at"] in seen_times:
            raise ValueError("duplicate snapshot in index")
        if previous_time is not None and entry["observed_at"] > previous_time:
            raise ValueError("snapshot index is not newest-first")
        referenced = root / relative
        if not referenced.is_file():
            raise ValueError("snapshot index references a missing file")
        try:
            saved = json.loads(referenced.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid indexed snapshot file") from exc
        validate_snapshot(saved)
        for field in ("observed_at", "current_until", "status", "finding_count", "source_commit"):
            if saved[field] != entry[field]:
                raise ValueError("snapshot index metadata mismatch")
        seen_paths.add(relative_text)
        seen_times.add(entry["observed_at"])
        previous_time = entry["observed_at"]
    return index


def _exclusive_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _restore_text(path: Path, previous: str | None) -> None:
    if previous is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.restore-", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)


def publish_snapshot(snapshot: dict[str, Any], root: Path) -> Path:
    validate_snapshot(snapshot)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".publish.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("another snapshot publisher holds the archive lock") from exc
    try:
        with os.fdopen(lock_descriptor, "w", encoding="utf-8") as lock:
            lock.write(str(os.getpid()))
            lock.flush()
            os.fsync(lock.fileno())
        index = _load_valid_index(root)
        observed = _parse_utc(snapshot["observed_at"])
        filename = observed.strftime("%Y-%m-%dT%H-%M-%SZ.json")
        relative = Path("snapshots") / observed.strftime("%Y") / observed.strftime("%m") / filename
        destination = root / relative
        entry = {
            "path": relative.as_posix(),
            "observed_at": snapshot["observed_at"],
            "current_until": snapshot["current_until"],
            "status": snapshot["status"],
            "finding_count": snapshot["finding_count"],
            "source_commit": snapshot["source_commit"],
        }
        if destination.exists():
            raise FileExistsError(f"snapshot already exists: {relative.as_posix()}")
        if any(existing["observed_at"] == entry["observed_at"] or existing["path"] == entry["path"] for existing in index["snapshots"]):
            raise ValueError("snapshot index already contains this scan")
        new_index = {"schema_version": 1, "snapshots": [*index["snapshots"], entry]}
        new_index["snapshots"].sort(key=lambda item: item["observed_at"], reverse=True)
        newest_entry = new_index["snapshots"][0]
        if newest_entry["path"] == relative.as_posix():
            latest_snapshot = snapshot
        else:
            latest_snapshot = json.loads((root / newest_entry["path"]).read_text(encoding="utf-8"))
            validate_snapshot(latest_snapshot)
        index_path = root / "index.json"
        latest_path = root / "latest.json"
        old_index = index_path.read_text(encoding="utf-8") if index_path.exists() else None
        old_latest = latest_path.read_text(encoding="utf-8") if latest_path.exists() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        _exclusive_json(destination, snapshot)
        try:
            _atomic_json(index_path, new_index)
            _atomic_json(latest_path, latest_snapshot)
        except Exception:
            destination.unlink(missing_ok=True)
            _restore_text(index_path, old_index)
            _restore_text(latest_path, old_latest)
            raise
        return relative
    finally:
        lock_path.unlink(missing_ok=True)

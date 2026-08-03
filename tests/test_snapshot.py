import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path

from scanner.snapshot import ALGORITHM_VERSION, create_snapshot, publish_snapshot, validate_snapshot


UTC = dt.timezone.utc


def finding(product_id="ADA-USD"):
    return {
        "product_id": product_id,
        "qualifies": True,
        "reference_price": 0.5,
        "suggested_limit_buy": 0.49,
        "do_not_chase_above": 0.51,
        "possible_target": 0.54,
        "invalidation": 0.46,
        "gross_upside": 0.08,
        "estimated_net_upside": 0.067,
        "estimated_round_trip_cost": 0.013,
        "change_24h": -0.01,
        "change_7d": -0.02,
        "current_vs_sma72": 0.01,
        "pullback_from_7d_high": -0.05,
        "spread": 0.001,
        "reward_to_risk": 1.6,
        "downside_to_invalidation": 0.08,
        "ranking_score": 8.1,
        "reasons": ["controlled pullback"],
        "cautions": [],
    }


def regime(*, complete=True, risk_off=False):
    return {
        "data_complete": complete,
        "risk_off": risk_off,
        "products_loaded": 10 if complete else 4,
        "below_sma_fraction": 0.3 if complete else None,
        "btc": None,
        "reasons": ["broad weakness"] if risk_off else [],
        "errors": [] if complete else ["six products unavailable"],
    }


class SnapshotCreationTests(unittest.TestCase):
    def setUp(self):
        self.observed = dt.datetime(2026, 8, 2, 18, 17, tzinfo=UTC)

    def test_normal_snapshot_contains_only_qualifying_findings_and_expires_in_two_hours(self):
        nonqualifying = dict(finding("BTC-USD"), qualifies=False)
        snapshot = create_snapshot([finding(), nonqualifying], regime(), self.observed, source_commit="abc123")
        self.assertEqual(snapshot["status"], "normal")
        self.assertEqual([item["product_id"] for item in snapshot["findings"]], ["ADA-USD"])
        self.assertEqual(snapshot["observed_at"], "2026-08-02T18:17:00Z")
        self.assertEqual(snapshot["current_until"], "2026-08-02T20:17:00Z")
        self.assertEqual(snapshot["algorithm_version"], ALGORITHM_VERSION)
        self.assertEqual(snapshot["source_commit"], "abc123")
        self.assertNotIn("qualifies", snapshot["findings"][0])

    def test_no_findings_risk_off_and_data_failure_are_distinct(self):
        self.assertEqual(create_snapshot([], regime(), self.observed)["status"], "no_findings")
        risk = create_snapshot([finding()], regime(risk_off=True), self.observed)
        self.assertEqual(risk["status"], "risk_off")
        self.assertEqual(risk["findings"], [])
        failed = create_snapshot([], regime(complete=False, risk_off=True), self.observed)
        self.assertEqual(failed["status"], "data_failure")

    def test_naive_time_private_fields_nonfinite_numbers_and_bad_symbols_are_rejected(self):
        with self.assertRaises(ValueError):
            create_snapshot([], regime(), dt.datetime(2026, 8, 2, 18, 17))
        for key, value in (("order_id", "secret-order"), ("quantity", 5), ("account_id", "acct")):
            bad = finding()
            bad[key] = value
            with self.assertRaises(ValueError, msg=key):
                create_snapshot([bad], regime(), self.observed)
        bad = finding()
        bad["reference_price"] = math.nan
        with self.assertRaises(ValueError):
            create_snapshot([bad], regime(), self.observed)
        bad = finding("<img src=x onerror=alert(1)>")
        with self.assertRaises(ValueError):
            create_snapshot([bad], regime(), self.observed)

    def test_noncanonical_basic_iso_timestamp_is_rejected(self):
        snapshot = create_snapshot([], regime(), self.observed)
        snapshot["observed_at"] = "20260802T181700Z"
        with self.assertRaises(ValueError):
            validate_snapshot(snapshot)

    def test_snapshot_validation_rejects_unknown_top_level_fields(self):
        snapshot = create_snapshot([], regime(), self.observed)
        snapshot["private_note"] = "do not publish"
        with self.assertRaises(ValueError):
            validate_snapshot(snapshot)

    def test_status_must_match_regime_and_finding_semantics(self):
        normal = create_snapshot([finding()], regime(), self.observed)
        invalid_cases = [
            dict(normal, status="no_findings"),
            dict(normal, findings=[], finding_count=0),
            dict(normal, status="risk_off"),
            dict(normal, status="data_failure"),
        ]
        for invalid in invalid_cases:
            with self.assertRaises(ValueError):
                validate_snapshot(invalid)


class SnapshotPublishingTests(unittest.TestCase):
    def setUp(self):
        self.observed = dt.datetime(2026, 8, 2, 18, 17, tzinfo=UTC)
        self.snapshot = create_snapshot([finding()], regime(), self.observed, source_commit="abc123")

    def test_publish_writes_immutable_snapshot_latest_and_append_only_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = publish_snapshot(self.snapshot, root)
            self.assertEqual(relative.as_posix(), "snapshots/2026/08/2026-08-02T18-17-00Z.json")
            saved = json.loads((root / relative).read_text())
            latest = json.loads((root / "latest.json").read_text())
            index = json.loads((root / "index.json").read_text())
            self.assertEqual(saved, self.snapshot)
            self.assertEqual(latest, self.snapshot)
            self.assertEqual(index["snapshots"][0]["path"], relative.as_posix())
            self.assertEqual(index["snapshots"][0]["finding_count"], 1)

    def test_existing_snapshot_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish_snapshot(self.snapshot, root)
            altered = dict(self.snapshot, status="no_findings", findings=[], finding_count=0)
            with self.assertRaises(FileExistsError):
                publish_snapshot(altered, root)
            saved = json.loads(next((root / "snapshots").rglob("*.json")).read_text())
            self.assertEqual(saved["status"], "normal")

    def test_second_snapshot_appends_newest_first_without_deleting_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = publish_snapshot(self.snapshot, root)
            later = create_snapshot([], regime(), self.observed + dt.timedelta(hours=2), source_commit="def456")
            second_path = publish_snapshot(later, root)
            index = json.loads((root / "index.json").read_text())
            self.assertEqual([entry["observed_at"] for entry in index["snapshots"]], [later["observed_at"], self.snapshot["observed_at"]])
            self.assertTrue((root / first_path).exists())
            self.assertTrue((root / second_path).exists())

    def test_backfill_does_not_replace_latest_newest_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            newer = create_snapshot([], regime(), self.observed + dt.timedelta(hours=2), source_commit="def456")
            publish_snapshot(newer, root)
            publish_snapshot(self.snapshot, root)
            latest = json.loads((root / "latest.json").read_text())
            index = json.loads((root / "index.json").read_text())
            self.assertEqual(latest, newer)
            self.assertEqual(index["snapshots"][0]["observed_at"], newer["observed_at"])

    def test_malformed_or_private_index_is_rejected_before_snapshot_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.json").write_text(json.dumps({"schema_version": 1, "snapshots": [{"path": "snapshots/old.json", "account_id": "private"}]}))
            with self.assertRaises(ValueError):
                publish_snapshot(self.snapshot, root)
            self.assertFalse((root / "snapshots").exists())

    def test_publish_lock_prevents_concurrent_writer_without_creating_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".publish.lock").write_text("busy")
            with self.assertRaises(RuntimeError):
                publish_snapshot(self.snapshot, root)
            self.assertFalse((root / "snapshots").exists())


if __name__ == "__main__":
    unittest.main()

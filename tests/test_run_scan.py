import datetime as dt
import tempfile
import unittest
from pathlib import Path

from scanner.rules import PRODUCTS
from scanner.run_scan import collect_scan, run_and_publish


UTC = dt.timezone.utc


def public_item(product_id, *, qualifies=True, change_24h=0.0):
    return {
        "product_id": product_id,
        "qualifies": qualifies,
        "reference_price": 100.0,
        "suggested_limit_buy": 99.0,
        "do_not_chase_above": 101.0,
        "possible_target": 105.0,
        "invalidation": 95.0,
        "gross_upside": 0.05,
        "estimated_net_upside": 0.037,
        "estimated_round_trip_cost": 0.013,
        "change_24h": change_24h,
        "change_7d": 0.01,
        "current_vs_sma72": 0.01,
        "pullback_from_7d_high": -0.05,
        "spread": 0.001,
        "reward_to_risk": 1.5,
        "downside_to_invalidation": 0.05,
        "ranking_score": 5.0,
        "reasons": ["test reason"],
        "cautions": [],
    }


class CollectScanTests(unittest.TestCase):
    def test_all_watchlist_products_are_scanned_and_all_qualifiers_retained(self):
        fetched = []

        def fetcher(product_id):
            fetched.append(product_id)
            return {"product_id": product_id}

        def scorer(product_id, _data):
            return public_item(product_id, qualifies=product_id in {"BTC-USD", "ADA-USD", "LTC-USD"})

        scored, regime = collect_scan(fetcher=fetcher, scorer=scorer)
        self.assertEqual(tuple(fetched), PRODUCTS)
        self.assertEqual(len(scored), 10)
        self.assertTrue(regime["data_complete"])
        self.assertFalse(regime["risk_off"])
        self.assertEqual({item["product_id"] for item in scored if item["qualifies"]}, {"BTC-USD", "ADA-USD", "LTC-USD"})

    def test_three_missing_products_fail_market_breadth_closed(self):
        missing = set(PRODUCTS[-3:])

        def fetcher(product_id):
            return None if product_id in missing else {"product_id": product_id}

        scored, regime = collect_scan(fetcher=fetcher, scorer=lambda pid, _data: public_item(pid))
        self.assertEqual(len(scored), 7)
        self.assertFalse(regime["data_complete"])
        self.assertTrue(regime["risk_off"])
        self.assertEqual(len(regime["errors"]), 3)

    def test_one_product_exception_is_recorded_without_aborting_other_products(self):
        def scorer(product_id, _data):
            if product_id == "DOGE-USD":
                raise ArithmeticError("bad candle")
            return public_item(product_id)

        scored, regime = collect_scan(fetcher=lambda pid: {"product_id": pid}, scorer=scorer)
        self.assertEqual(len(scored), 9)
        self.assertTrue(regime["data_complete"])
        self.assertEqual(regime["errors"], ["DOGE-USD: scoring_failed"])


class RunAndPublishTests(unittest.TestCase):
    def test_run_publishes_snapshot_with_every_qualifying_finding(self):
        observed = dt.datetime(2026, 8, 2, 18, 17, tzinfo=UTC)

        def scorer(product_id, _data):
            return public_item(product_id, qualifies=product_id.endswith("A-USD") or product_id == "BTC-USD")

        with tempfile.TemporaryDirectory() as tmp:
            snapshot, relative = run_and_publish(
                Path(tmp),
                observed_at=observed,
                source_commit="abc123",
                fetcher=lambda pid: {"product_id": pid},
                scorer=scorer,
            )
            expected = {pid for pid in PRODUCTS if pid.endswith("A-USD") or pid == "BTC-USD"}
            self.assertEqual({item["product_id"] for item in snapshot["findings"]}, expected)
            self.assertTrue((Path(tmp) / relative).exists())

    def test_risk_off_run_publishes_status_but_no_actionable_findings(self):
        observed = dt.datetime(2026, 8, 2, 18, 17, tzinfo=UTC)

        def scorer(product_id, _data):
            return public_item(product_id, change_24h=-0.051 if product_id == "BTC-USD" else 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot, _ = run_and_publish(Path(tmp), observed_at=observed, fetcher=lambda pid: {}, scorer=scorer)
            self.assertEqual(snapshot["status"], "risk_off")
            self.assertEqual(snapshot["findings"], [])


if __name__ == "__main__":
    unittest.main()

import math
import unittest

from scanner.rules import (
    PRODUCTS,
    bid_ask_spread,
    build_market_metrics,
    buy_price_guidance,
    clears_strategy,
    market_risk_regime,
    product_rules,
    ranking_score,
    score_market_input,
)


class ProductRulesTests(unittest.TestCase):
    def test_watchlist_is_exact_and_stable(self):
        self.assertEqual(
            PRODUCTS,
            ("BTC-USD", "ETH-USD", "SOL-USD", "XLM-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "MORPHO-USD"),
        )

    def test_asset_specific_fee_and_spread_tiers(self):
        btc = product_rules("BTC-USD")
        alt = product_rules("ADA-USD")
        morpho = product_rules("MORPHO-USD")
        self.assertEqual((btc["round_trip_fee_est"], btc["min_net_upside"], btc["max_spread"]), (0.010, 0.015, 0.003))
        self.assertEqual((alt["round_trip_fee_est"], alt["min_net_upside"], alt["max_spread"]), (0.013, 0.018, 0.005))
        self.assertEqual((morpho["round_trip_fee_est"], morpho["min_net_upside"], morpho["max_spread"]), (0.018, 0.03, 0.004))
        self.assertGreaterEqual(morpho["min_gross_upside"], morpho["round_trip_fee_est"] + morpho["min_net_upside"])


class MetricsTests(unittest.TestCase):
    def fixture(self):
        closes = [90 + i * 0.05 for i in range(169)]
        closes[-1] = 98.0
        return {
            "timestamps": [1_700_000_000 + i * 3600 for i in range(169)],
            "closes": closes,
            "highs": [value * 1.03 for value in closes],
            "lows": [value * 0.97 for value in closes],
            "volumes": [1000.0] * 169,
            "bid": 97.9,
            "ask": 98.1,
        }

    def test_bid_ask_spread_uses_midpoint_and_invalid_quotes_are_nan(self):
        self.assertAlmostEqual(bid_ask_spread(100, 101), 1 / 100.5)
        self.assertTrue(math.isnan(bid_ask_spread(0, 101)))
        self.assertTrue(math.isnan(bid_ask_spread(101, 100)))

    def test_metrics_use_bounded_target_fee_and_reward_risk(self):
        data = self.fixture()
        rules = product_rules("ADA-USD")
        metrics = build_market_metrics(data, rules)
        expected_sma = sum(data["closes"][-72:]) / 72
        expected_high = max(data["highs"][-168:])
        expected_target = min(expected_high * 0.992, 98.0 * 1.08, expected_sma * 1.06)
        self.assertAlmostEqual(metrics["sma72"], expected_sma)
        self.assertAlmostEqual(metrics["target"], expected_target)
        self.assertAlmostEqual(metrics["net_upside"], metrics["gross_upside"] - 0.013)
        self.assertGreater(metrics["reward_to_risk"], 0)

    def test_invalid_or_short_market_input_fails_closed(self):
        data = self.fixture()
        data["closes"] = data["closes"][:100]
        self.assertIsNone(build_market_metrics(data, product_rules("BTC-USD")))
        data = self.fixture()
        data["closes"][-1] = float("nan")
        self.assertIsNone(build_market_metrics(data, product_rules("BTC-USD")))

    def test_buy_guidance_preserves_required_upside_and_never_chases(self):
        metrics = {"current": 100.0, "target": 104.8}
        rules = product_rules("MORPHO-USD")
        guidance = buy_price_guidance(metrics, rules)
        self.assertAlmostEqual(guidance["max_buy_price"], 100.0)
        self.assertEqual(guidance["suggested_limit_buy"], 100.0)
        metrics["current"] = 99.0
        self.assertEqual(buy_price_guidance(metrics, rules)["suggested_limit_buy"], 99.0)


class QualificationTests(unittest.TestCase):
    def clear_metrics(self):
        return {
            "current": 100.0,
            "target": 105.0,
            "gross_upside": 0.05,
            "net_upside": 0.037,
            "lend_7d": 0.00075,
            "reward_to_risk": 1.5,
            "pullback_from_high": -0.05,
            "spread": 0.001,
            "change24": -0.01,
            "change7d": -0.02,
            "sma72": 100.0,
        }

    def test_every_required_gate_must_clear(self):
        rules = product_rules("ADA-USD")
        base = self.clear_metrics()
        self.assertTrue(clears_strategy(base, rules))
        for field, blocked_value in (
            ("net_upside", 0.0179),
            ("reward_to_risk", 1.19),
            ("pullback_from_high", -0.121),
            ("spread", 0.0051),
            ("change24", -0.071),
            ("change7d", -0.151),
            ("current", 95.9),
        ):
            candidate = dict(base)
            candidate[field] = blocked_value
            self.assertFalse(clears_strategy(candidate, rules), field)

    def test_nonfinite_or_missing_metrics_never_clear(self):
        rules = product_rules("ADA-USD")
        for field in self.clear_metrics():
            for value in (math.nan, math.inf, -math.inf):
                candidate = dict(self.clear_metrics())
                candidate[field] = value
                self.assertFalse(clears_strategy(candidate, rules), (field, value))
        candidate = dict(self.clear_metrics())
        candidate.pop("spread")
        self.assertFalse(clears_strategy(candidate, rules))

    def test_ranking_rewards_better_upside_and_liquidity(self):
        rules = product_rules("ADA-USD")
        lower = self.clear_metrics()
        higher = dict(lower, net_upside=0.05, spread=0.0002)
        self.assertGreater(ranking_score(higher, rules), ranking_score(lower, rules))

    def test_score_market_input_returns_public_fields_only(self):
        data = MetricsTests().fixture()
        item = score_market_input("ADA-USD", data)
        self.assertEqual(item["product_id"], "ADA-USD")
        self.assertIn("qualifies", item)
        self.assertNotIn("order_id", item)
        self.assertNotIn("quantity", item)
        self.assertNotIn("account_id", item)


class MarketRegimeTests(unittest.TestCase):
    def item(self, pid, change24=0.0, change7d=0.0, vs_sma=0.01):
        return {"product_id": pid, "change_24h": change24, "change_7d": change7d, "current_vs_sma72": vs_sma}

    def test_incomplete_breadth_fails_closed(self):
        regime = market_risk_regime([self.item("BTC-USD")] * 7, ["missing"])
        self.assertFalse(regime["data_complete"])
        self.assertTrue(regime["risk_off"])

    def test_severe_btc_decline_is_risk_off(self):
        scored = [self.item("BTC-USD", change24=-0.051)] + [self.item(pid) for pid in PRODUCTS[1:8]]
        regime = market_risk_regime(scored, [])
        self.assertTrue(regime["data_complete"])
        self.assertTrue(regime["risk_off"])

    def test_normal_regime_with_eight_products(self):
        scored = [self.item(pid) for pid in PRODUCTS[:8]]
        regime = market_risk_regime(scored, [])
        self.assertTrue(regime["data_complete"])
        self.assertFalse(regime["risk_off"])

    def test_nonfinite_regime_metrics_fail_closed(self):
        scored = [self.item(pid) for pid in PRODUCTS[:8]]
        scored[0]["change_24h"] = math.nan
        regime = market_risk_regime(scored, [])
        self.assertFalse(regime["data_complete"])
        self.assertTrue(regime["risk_off"])

    def test_untrusted_error_details_are_not_copied_to_public_regime(self):
        regime = market_risk_regime([self.item("BTC-USD")] * 7, ["https://private.invalid token=secret"])
        serialized = str(regime)
        self.assertNotIn("private.invalid", serialized)
        self.assertNotIn("secret", serialized)


if __name__ == "__main__":
    unittest.main()

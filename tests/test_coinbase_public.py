import unittest

from scanner.coinbase_public import PUBLIC_API_BASE, fetch_market_inputs, normalize_market_data


class NormalizeMarketDataTests(unittest.TestCase):
    def candles(self, count=169):
        return [
            [1_700_000_000 + i * 3600, 90 + i / 100, 92 + i / 100, 91 + i / 100, 91 + i / 100, 1000 + i]
            for i in reversed(range(count))
        ]

    def test_newest_first_coinbase_candles_are_normalized_oldest_first(self):
        result = normalize_market_data(self.candles(), {"bid": "92.5", "ask": "92.7"})
        self.assertEqual(len(result["timestamps"]), 169)
        self.assertEqual(result["timestamps"], sorted(result["timestamps"]))
        self.assertEqual(result["closes"][0], 91.0)
        self.assertEqual(result["bid"], 92.5)

    def test_short_malformed_duplicate_or_nonpositive_candles_fail_closed(self):
        self.assertIsNone(normalize_market_data(self.candles(168), {"bid": "1", "ask": "2"}))
        malformed = self.candles()
        malformed[0] = [1, 2]
        self.assertIsNone(normalize_market_data(malformed, {"bid": "1", "ask": "2"}))
        duplicated = self.candles()
        duplicated[0][0] = duplicated[1][0]
        self.assertIsNone(normalize_market_data(duplicated, {"bid": "1", "ask": "2"}))
        nonpositive = self.candles()
        nonpositive[0][4] = 0
        self.assertIsNone(normalize_market_data(nonpositive, {"bid": "1", "ask": "2"}))

    def test_invalid_ticker_fails_closed(self):
        self.assertIsNone(normalize_market_data(self.candles(), {"bid": "not-a-number", "ask": None}))

    def test_ohlc_bounds_and_hourly_continuity_are_required(self):
        bad_open = self.candles()
        bad_open[0][3] = 0
        self.assertIsNone(normalize_market_data(bad_open, {"bid": "92.5", "ask": "92.7"}))
        close_outside = self.candles()
        close_outside[0][4] = close_outside[0][2] + 1
        self.assertIsNone(normalize_market_data(close_outside, {"bid": "92.5", "ask": "92.7"}))
        gap = self.candles()
        gap[0][0] += 1800
        self.assertIsNone(normalize_market_data(gap, {"bid": "92.5", "ask": "92.7"}))


class FetchMarketInputsTests(unittest.TestCase):
    def test_fetch_uses_only_public_product_endpoints(self):
        calls = []
        candles = NormalizeMarketDataTests().candles()

        def transport(url, params=None):
            calls.append((url, params))
            return candles if url.endswith("/candles") else {"bid": "92.5", "ask": "92.7"}

        result = fetch_market_inputs("BTC-USD", transport=transport)
        self.assertIsNotNone(result)
        self.assertEqual(calls[0], (f"{PUBLIC_API_BASE}/products/BTC-USD/candles", {"granularity": 3600}))
        self.assertEqual(calls[1], (f"{PUBLIC_API_BASE}/products/BTC-USD/ticker", None))
        self.assertTrue(all("account" not in url and "order" not in url for url, _ in calls))

    def test_unsupported_products_and_transport_failures_fail_closed(self):
        with self.assertRaises(ValueError):
            fetch_market_inputs("FAKE-USD", transport=lambda *_args, **_kwargs: {})

        def broken(*_args, **_kwargs):
            raise TimeoutError("offline")

        self.assertIsNone(fetch_market_inputs("BTC-USD", transport=broken))


if __name__ == "__main__":
    unittest.main()

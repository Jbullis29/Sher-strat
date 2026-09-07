import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.refresh_performance import build_public_data


ROOT = Path(__file__).resolve().parents[1]


class PerformanceRefreshTests(unittest.TestCase):
    def ledger(self):
        return {
            "generated_at": "2026-09-07T12:56:37+00:00",
            "summary": {"private": "ignored"},
            "open_or_unmatched_entries": [{"asset": "BTC", "quantity": 99}],
            "closed": [
                {
                    "status": "closed",
                    "asset": "AVAX",
                    "entry_date": "2026-08-24T10:56:47Z",
                    "exit_date": "2026-09-07T12:52:21Z",
                    "held_days": 14.0802546296,
                    "quantity": 36.84,
                    "entry_id": "private-entry",
                    "exit_id": "private-exit",
                    "cost": 276.2476109707,
                    "pnl": 17.6483448047,
                    "return_pct": 6.3885963548,
                    "buy_price": 7.49867437,
                    "sell_price": 7.9777344,
                }
            ],
        }

    def test_builds_exact_sanitized_schema_and_excludes_open_private_fields(self):
        data = build_public_data(self.ledger())
        self.assertEqual(data["summary"]["completed_trades"], 1)
        self.assertEqual(data["summary"]["wins"], 1)
        self.assertEqual(data["trades"][0], {
            "number": 1,
            "asset": "AVAX",
            "entry_date": "2026-08-24",
            "exit_date": "2026-09-07",
            "buy_price": 7.498674,
            "sell_price": 7.977734,
            "held_days": 14.08,
            "cost": 276.25,
            "pnl": 17.65,
            "return_pct": 6.39,
            "yield_benchmark": 0.42,
            "beat_yield": True,
            "cumulative_pnl": 17.65,
        })
        serialized = json.dumps(data).lower()
        for private in ("quantity", "entry_id", "exit_id", "open_or_unmatched_entries", "private-entry", "private-exit"):
            self.assertNotIn(private, serialized)

    def test_rejects_nonfinite_or_nonpositive_prices(self):
        for field, value in (("buy_price", 0), ("sell_price", -1), ("buy_price", 1e-9), ("buy_price", float("nan")), ("sell_price", True)):
            with self.subTest(field=field, value=value):
                ledger = self.ledger()
                ledger["closed"][0][field] = value
                with self.assertRaises(ValueError):
                    build_public_data(ledger)

    def test_rejects_open_or_internally_inconsistent_rows(self):
        mutations = {
            "open": ("status", "open"),
            "bad pnl": ("pnl", 999),
            "bad return": ("return_pct", 99),
            "bad sell price": ("sell_price", 8.5),
            "bad duration": ("held_days", 1),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                ledger = self.ledger()
                ledger["closed"][0][field] = value
                with self.assertRaises(ValueError):
                    build_public_data(ledger)

    def test_cli_requires_explicit_private_input_and_writes_only_requested_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "private.json"
            output = Path(tmp) / "public.json"
            ledger_data = self.ledger()
            loss = copy.deepcopy(ledger_data["closed"][0])
            loss.update({
                "asset": "ETH", "entry_date": "2026-09-05T00:00:00Z",
                "exit_date": "2026-09-06T00:00:00Z", "held_days": 1.0,
                "quantity": 10.0, "cost": 100.0, "pnl": -10.0,
                "return_pct": -10.0, "buy_price": 10.0, "sell_price": 9.0,
            })
            ledger_data["closed"].append(loss)
            ledger.write_text(json.dumps(ledger_data))
            result = subprocess.run(
                [sys.executable, "tools/refresh_performance.py", "--ledger", str(ledger), "--output", str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["summary"]["completed_trades"], 2)


if __name__ == "__main__":
    unittest.main()

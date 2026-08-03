import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.build_site import build_site


ROOT = Path(__file__).resolve().parents[1]


class SiteBuildTests(unittest.TestCase):
    def build(self, directory):
        destination = Path(directory) / "dist"
        build_site(ROOT / "site", destination)
        return destination

    def test_build_contains_only_whitelisted_static_and_validated_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            files = {path.relative_to(dist).as_posix() for path in dist.rglob("*") if path.is_file()}
            required = {
                "index.html", "404.html", "robots.txt", "sitemap.xml", "assets/styles.css", "assets/site.js",
                "findings/index.html", "performance/index.html", "methodology/index.html", "disclosures/index.html",
                "data/findings/latest.json", "data/findings/index.json", "data/performance/realized-results.json",
            }
            self.assertTrue(required.issubset(files))
            self.assertTrue(all(path in required or path.startswith("data/findings/snapshots/") for path in files))
            self.assertFalse(any(path.endswith((".py", ".pyc", ".env", ".key")) for path in files))

    def test_findings_and_performance_are_explicitly_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            home = (dist / "index.html").read_text()
            findings = (dist / "findings/index.html").read_text()
            performance = (dist / "performance/index.html").read_text()
            self.assertIn("Algorithm findings", home)
            self.assertIn("Personal realized results", home)
            self.assertIn("Research snapshots—not executed trades", findings)
            self.assertIn("not the performance of every published finding", performance)
            self.assertIn("not affiliated with, sponsored by, endorsed by, or operated by Sherweb Inc.", home)

    def test_public_data_has_no_private_account_fields(self):
        forbidden = {"account_id", "order_id", "entry_order_id", "exit_order_id", "quantity", "balance", "filled_size", "credentials", "api_key", "secret", "jwt"}
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            for path in (dist / "data").rglob("*.json"):
                value = json.loads(path.read_text())
                stack = [value]
                while stack:
                    item = stack.pop()
                    if isinstance(item, dict):
                        self.assertFalse({str(key).lower() for key in item} & forbidden, path.as_posix())
                        stack.extend(item.values())
                    elif isinstance(item, list):
                        stack.extend(item)

    def test_public_footers_do_not_claim_copyright(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            for relative in ("index.html", "findings/index.html", "performance/index.html", "methodology/index.html", "disclosures/index.html"):
                page = (dist / relative).read_text()
                self.assertNotIn("©", page, relative)
                self.assertIn("sherweb.ai · Independent research project", page, relative)

    def test_performance_data_is_complete_and_consistent_with_verified_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            data = json.loads((dist / "data/performance/realized-results.json").read_text())
            self.assertEqual(data["summary"]["completed_trades"], 7)
            self.assertEqual(len(data["trades"]), 7)
            self.assertAlmostEqual(data["summary"]["net_realized_pnl"], 27.78)
            self.assertAlmostEqual(sum(trade["pnl"] for trade in data["trades"]), 27.78)
            self.assertEqual(sum(1 for trade in data["trades"] if trade["pnl"] < 0), 1)
            for trade in data["trades"]:
                self.assertGreater(trade["buy_price"], 0)
                self.assertGreater(trade["sell_price"], 0)
                effective_return = (trade["sell_price"] / trade["buy_price"] - 1) * 100
                self.assertAlmostEqual(effective_return, trade["return_pct"], delta=0.02)

    def test_performance_table_displays_effective_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.build(tmp)
            performance = (dist / "performance/index.html").read_text()
            script = (dist / "assets/site.js").read_text()
            self.assertIn("Buy price", performance)
            self.assertIn("Sell price", performance)
            self.assertIn("priceMoney(trade.buy_price)", script)
            self.assertIn("priceMoney(trade.sell_price)", script)
            self.assertIn("timeZone:'UTC'", script)
            data = json.loads((dist / "data/performance/realized-results.json").read_text())
            self.assertIn("effective unit cost including exposed buy fees", data["methodology"])
            self.assertIn("effective unit proceeds net of exposed sell fees", data["methodology"])

    def test_performance_price_fields_fail_closed(self):
        invalid_values = [True, 0, -1, float("nan"), float("inf"), float("-inf")]
        for field in ("buy_price", "sell_price"):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                    source = self.mutable_site(tmp)
                    path = source / "data/performance/realized-results.json"
                    data = json.loads(path.read_text())
                    data["trades"][0][field] = invalid
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        build_site(source, Path(tmp) / "dist")
        for mutation in ("missing", "unknown", "inconsistent"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                source = self.mutable_site(tmp)
                path = source / "data/performance/realized-results.json"
                data = json.loads(path.read_text())
                trade = data["trades"][0]
                if mutation == "missing":
                    del trade["buy_price"]
                elif mutation == "unknown":
                    trade["execution_quantity"] = 123
                else:
                    trade["sell_price"] = trade["buy_price"] * (1 + (trade["return_pct"] + 0.03) / 100)
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    build_site(source, Path(tmp) / "dist")

    def mutable_site(self, directory):
        source = Path(directory) / "site"
        shutil.copytree(ROOT / "site", source)
        return source

    def test_performance_schema_rejects_unknown_summary_fields_and_bad_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            path = source / "data/performance/realized-results.json"
            data = json.loads(path.read_text())
            data["summary"]["private_ledger_path"] = "/private/ledger.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            path = source / "data/performance/realized-results.json"
            data = json.loads(path.read_text())
            data["trades"][0]["held_days"] = "25.21"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_latest_must_equal_newest_archive_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            latest_path = source / "data/findings/latest.json"
            latest = json.loads(latest_path.read_text())
            latest["notice"] = "Different but schema-valid public notice."
            latest_path.write_text(json.dumps(latest))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_every_derived_performance_summary_field_is_verified(self):
        fields = {
            "largest_loss": 999.0,
            "median_holding_days": 999.0,
            "median_trade_return_pct": 999.0,
            "profit_factor": 999.0,
            "realized_return_pct": 999.0,
            "win_rate_pct": 0.0,
        }
        for field, invalid in fields.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                source = self.mutable_site(tmp)
                path = source / "data/performance/realized-results.json"
                data = json.loads(path.read_text())
                data["summary"][field] = invalid
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    build_site(source, Path(tmp) / "dist")

    def test_trade_yield_benchmark_and_holding_duration_are_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            path = source / "data/performance/realized-results.json"
            data = json.loads(path.read_text())
            data["trades"][0]["yield_benchmark"] += 1
            data["summary"]["yield_benchmark_pnl"] += 1
            data["summary"]["outperformance_vs_yield"] -= 1
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            path = source / "data/performance/realized-results.json"
            data = json.loads(path.read_text())
            original_yield = data["trades"][0]["yield_benchmark"]
            data["trades"][0]["held_days"] = 0.0
            data["trades"][0]["yield_benchmark"] = 0.0
            data["summary"]["yield_benchmark_pnl"] -= original_yield
            data["summary"]["outperformance_vs_yield"] += original_yield
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_symlinked_static_or_data_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            external = Path(tmp) / "external-robots.txt"
            external.write_text("external sentinel")
            target = source / "robots.txt"
            target.unlink()
            target.symlink_to(external)
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            target = source / "data/findings/latest.json"
            external = Path(tmp) / "external-latest.json"
            external.write_text(target.read_text())
            target.unlink()
            target.symlink_to(external)
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_empty_archive_cannot_publish_standalone_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            index_path = source / "data/findings/index.json"
            index_path.write_text(json.dumps({"schema_version": 1, "snapshots": []}))
            shutil.rmtree(source / "data/findings/snapshots")
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_index_metadata_must_exactly_match_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.mutable_site(tmp)
            index_path = source / "data/findings/index.json"
            index = json.loads(index_path.read_text())
            index["snapshots"][0]["source_commit"] = "abcdef"
            index_path.write_text(json.dumps(index))
            with self.assertRaises(ValueError):
                build_site(source, Path(tmp) / "dist")

    def test_direct_build_command_succeeds_from_repository_root(self):
        result = subprocess.run(
            [sys.executable, "tools/build_site.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Built validated GitHub Pages artifact", result.stdout)

    def test_builder_clears_stale_files_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            (dist / "private-ledger.json").write_text("secret")
            build_site(ROOT / "site", dist)
            self.assertFalse((dist / "private-ledger.json").exists())


if __name__ == "__main__":
    unittest.main()

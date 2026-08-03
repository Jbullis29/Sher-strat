import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scan-and-publish.yml"


class WorkflowTests(unittest.TestCase):
    def text(self):
        return WORKFLOW.read_text()

    def test_schedule_is_every_two_hours_off_the_top_of_hour_and_manual_dispatch_exists(self):
        text = self.text()
        self.assertIn("cron: '17 */2 * * *'", text)
        self.assertIn("workflow_dispatch:", text)

    def test_workflow_tests_before_scanning_building_and_deploying(self):
        text = self.text()
        positions = [
            text.index("python -m unittest discover"),
            text.index("python -m scanner.run_scan"),
            text.index("python tools/build_site.py"),
            text.index("actions/deploy-pages"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_official_actions_are_pinned_to_immutable_commit_shas(self):
        text = self.text()
        uses = re.findall(r"uses:\s*([^\s]+)", text)
        self.assertGreaterEqual(len(uses), 5)
        for action in uses:
            self.assertRegex(action, r"^actions/[a-z0-9-]+@[0-9a-f]{40}$")

    def test_permissions_and_concurrency_are_explicit(self):
        text = self.text()
        self.assertIn("contents: write", text)
        self.assertIn("pages: write", text)
        self.assertIn("id-token: write", text)
        self.assertIn("group: sherweb-pages", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_only_generated_findings_are_committed_and_only_dist_is_uploaded(self):
        text = self.text()
        self.assertIn("git add -- site/data/findings", text)
        self.assertNotIn("git add -A", text)
        self.assertRegex(text, r"path:\s*dist\b")
        self.assertNotRegex(text, r"path:\s*\.\s*$")


if __name__ == "__main__":
    unittest.main()

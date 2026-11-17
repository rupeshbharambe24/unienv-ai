"""Offline doctor checks; never change the caller's installed environment."""

import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("unienv", Path(__file__).parents[2] / "src/unienv.py")
unienv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unienv)


class DoctorFixTests(unittest.TestCase):
    def run_doctor(self, *, missing_manifest=False, warning=False, json_output=True):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            manifest = root / "manifest.json"
            if not missing_manifest:
                manifest.write_text("{}")
            if warning:
                (root / ".bashrc").write_text("export EXAMPLE_API_KEY=synthetic-test-value\n")
            for name, value in {
                "HOME": root,
                "UNIENV_HOME": root,
                "PY_DIR": root / "py",
                "MANIFEST_FILE": manifest,
                "IS_MAC": False,
            }.items():
                stack.enter_context(patch.object(unienv, name, value))
            for name, value in {
                "load_recipes": {},
                "load_state": {"options": {"exclude": []}},
                "active_recipes": [],
                "wanted": {"node": [], "bins": [], "skipped_bins": []},
                "node_packages": {},
                "agent_targets": [],
                "mcp_clients": {},
                "brew_prefix": None,
                "render_agent_block": "",
                "_clean_shell_probe": str(unienv.BIN / "ue-py"),
            }.items():
                stack.enter_context(patch.object(unienv, name, return_value=value))
            sync = stack.enter_context(patch.object(unienv, "do_sync"))
            sync.side_effect = lambda *a, **kw: manifest.write_text("{}")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = unienv.cmd_doctor(argparse.Namespace(json=json_output, fix=True, quiet_ok=False))
            return result, stdout.getvalue(), stderr.getvalue(), sync.call_count

    def test_json_fix_is_one_document(self):
        result, stdout, stderr, count = self.run_doctor(missing_manifest=True)
        report = json.loads(stdout)
        self.assertEqual(count, 1)
        self.assertIn("Applying safe fixes", stderr)
        self.assertNotIn("manifest", [check["id"] for check in report["checks"]])
        self.assertEqual(result, 1)  # Missing Python is still reported, not hidden.

    def test_unfixable_failure_and_warning_do_not_trigger_sync(self):
        _, stdout, stderr, count = self.run_doctor(warning=True)
        report = json.loads(stdout)
        self.assertEqual(count, 0)
        self.assertEqual(report["warnings"], 1)
        self.assertEqual(stderr, "")

    def test_text_mode_still_reports_repairs(self):
        _, stdout, _, count = self.run_doctor(missing_manifest=True, json_output=False)
        self.assertEqual(count, 1)
        self.assertIn("Applying safe fixes", stdout)


if __name__ == "__main__":
    unittest.main()

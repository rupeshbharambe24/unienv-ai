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
    def run_doctor(self, *, missing_manifest=False, warning=False, json_output=True, unreadable_agent=False):
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
            if unreadable_agent:
                inst = root / "CLAUDE.md"
                inst.write_text("private")
                stack.enter_context(
                    patch.object(
                        unienv, "agent_targets", return_value=[{"id": "claude", "name": "Claude", "instructions": inst}]
                    )
                )
                stack.enter_context(patch.object(unienv, "detected", return_value=True))
                original_read = unienv._read_block_text

                def read(path):
                    if path == inst:
                        raise PermissionError("denied")
                    return original_read(path)

                stack.enter_context(patch.object(unienv, "_read_block_text", side_effect=read))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = unienv.cmd_doctor(argparse.Namespace(json=json_output, fix=True, quiet_ok=False))
            return result, stdout.getvalue(), stderr.getvalue(), sync.call_count

    def test_unreadable_agent_file_is_reported_without_crashing(self):
        _, stdout, _, _ = self.run_doctor(unreadable_agent=True)
        checks = {c["id"]: c for c in json.loads(stdout)["checks"]}
        self.assertEqual(checks["agent:claude"]["status"], "warn")
        self.assertIn("cannot read", checks["agent:claude"]["message"])

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

    def test_invalid_mcp_json_warns_and_continues(self):
        cases = [
            ('{"mcpServers": {},}\n', "not valid JSON"),
            ('{"mcpServers": 123}', "invalid mcpServers"),
            ('{"mcpServers": ["unienv"]}', "invalid mcpServers"),
            ('{"mcpServers": "unienv"}', "invalid mcpServers"),
            ('{"mcpServers": true}', "invalid mcpServers"),
        ]
        for raw, expected_msg in cases:
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                    root = Path(directory)
                    bad_cfg = root / "cursor.json"
                    bad_cfg.write_text(raw)
                    manifest = root / "manifest.json"
                    manifest.write_text("{}")
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
                        "mcp_clients": {"cursor": bad_cfg},
                        "brew_prefix": None,
                        "render_agent_block": "",
                        "_clean_shell_probe": str(unienv.BIN / "ue-py"),
                    }.items():
                        stack.enter_context(patch.object(unienv, name, return_value=value))
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        result = unienv.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))

                report = json.loads(stdout.getvalue())
                mcp_checks = [c for c in report["checks"] if c["id"] == "mcp:cursor"]
                self.assertEqual(len(mcp_checks), 1)
                self.assertEqual(mcp_checks[0]["status"], "warn")
                self.assertIn(expected_msg, mcp_checks[0]["message"])
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(result, 1)  # Python dir missing, so doctor continued past invalid MCP config

    def test_mcp_configs_null_missing_and_mixed_valid_malformed(self):
        for raw in ('{"mcpServers": null}', "{}"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                    root = Path(directory)
                    cfg = root / "cursor.json"
                    cfg.write_text(raw)
                    manifest = root / "manifest.json"
                    manifest.write_text("{}")
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
                        "mcp_clients": {"cursor": cfg},
                        "brew_prefix": None,
                        "render_agent_block": "",
                        "_clean_shell_probe": str(unienv.BIN / "ue-py"),
                    }.items():
                        stack.enter_context(patch.object(unienv, name, return_value=value))
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        unienv.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
                report = json.loads(stdout.getvalue())
                self.assertNotIn("mcp:cursor", [c["id"] for c in report["checks"]])
                self.assertNotIn("mcp", [c["id"] for c in report["checks"]])

        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            bad_cfg = root / "cursor.json"
            bad_cfg.write_text('{"mcpServers": 123}')
            good_cfg = root / "claude.json"
            good_cfg.write_text('{"mcpServers": {"unienv": {"command": "unienv"}}}')
            manifest = root / "manifest.json"
            manifest.write_text("{}")
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
                "mcp_clients": {"cursor": bad_cfg, "claude-desktop": good_cfg},
                "brew_prefix": None,
                "render_agent_block": "",
                "_clean_shell_probe": str(unienv.BIN / "ue-py"),
                "mcp_handshake": (True, "protocol 2025-06-18", 0.1),
            }.items():
                stack.enter_context(patch.object(unienv, name, return_value=value))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                unienv.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))

        report = json.loads(stdout.getvalue())
        checks = {c["id"]: c for c in report["checks"]}
        self.assertEqual(checks["mcp:cursor"]["status"], "warn")
        self.assertEqual(checks["mcp"]["status"], "ok")
        self.assertIn("claude-desktop", checks["mcp"]["message"])


if __name__ == "__main__":
    unittest.main()

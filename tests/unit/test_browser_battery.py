"""Unit tests for browser battery Playwright management and MCP env (#3)."""

import argparse
import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]

spec_u = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec_u)
spec_u.loader.exec_module(u)

spec_m = importlib.util.spec_from_file_location("unienv_mcp", ROOT / "src" / "unienv_mcp.py")
m = importlib.util.module_from_spec(spec_m)
spec_m.loader.exec_module(m)


class BrowserBatteryTests(unittest.TestCase):
    def test_mcp_tool_env_propagates_playwright_browsers_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ue_home = Path(tmp) / ".unienv"
            bdir = ue_home / "browsers"
            with patch.object(m, "UNIENV_HOME", ue_home), patch.dict(os.environ, {}, clear=True):
                self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", m.tool_env())
                bdir.mkdir(parents=True)
                self.assertEqual(m.tool_env().get("PLAYWRIGHT_BROWSERS_PATH"), str(bdir))
            with (
                patch.object(m, "UNIENV_HOME", ue_home),
                patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "0"}, clear=True),
            ):
                self.assertEqual(m.tool_env().get("PLAYWRIGHT_BROWSERS_PATH"), "0")

    def test_hermetic_and_relative_paths_do_not_mkdir_in_cwd_and_no_system_skips_deps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "cwd"
            cwd.mkdir()
            ue_home = root / ".unienv"
            py = ue_home / "py/.venv/bin/python"
            py.parent.mkdir(parents=True)
            py.write_text("#!/bin/sh\n")
            calls = []

            def fake_run(cmd, **kw):
                calls.append((cmd, kw.get("env")))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                for val in ("0", "rel/browsers"):
                    calls.clear()
                    with (
                        patch.object(u, "UNIENV_HOME", ue_home),
                        patch.object(u, "PY_DIR", ue_home / "py"),
                        patch.object(u, "platform_key", return_value="debian"),
                        patch.object(u, "run", side_effect=fake_run),
                        patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": val}),
                    ):
                        u.install_browsers(assume_yes=True, no_system=True)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0][1], {"PLAYWRIGHT_BROWSERS_PATH": val})
                    self.assertEqual(list(cwd.iterdir()), [])
            finally:
                os.chdir(old_cwd)

    def test_cmd_upgrade_and_need_refresh_browsers_only_when_battery_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ue_home = root / ".unienv"
            (ue_home / "py").mkdir(parents=True)
            (ue_home / "py/pyproject.toml").write_text('[project]\nname="x"\n')
            for batteries, expect_called in ((["browser"], True), (["office"], False)):
                with self.subTest(batteries=batteries):
                    with (
                        patch.object(u, "UNIENV_HOME", ue_home),
                        patch.object(u, "STATE_FILE", ue_home / "state.json"),
                        patch.object(u, "BACKUP_DIR", ue_home / "backups"),
                        patch.object(u, "PY_DIR", ue_home / "py"),
                        patch.object(u, "NODE_DIR", ue_home / "node"),
                        patch.object(u, "LOG_FILE", ue_home / "log" / "events.jsonl"),
                        patch.object(u, "find_uv", return_value="/usr/bin/uv"),
                        patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
                        patch.object(u, "install_python", return_value=None),
                        patch.object(u, "_verify_python_imports", return_value={}),
                        patch.object(u, "do_sync", return_value={}),
                        patch.object(u, "install_browsers") as mock_ib,
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        state = u.default_state(u.load_recipes())
                        state["batteries"] = batteries
                        u.save_state(state)
                        rc = u.cmd_upgrade(argparse.Namespace(packages=["playwright"], node=False, dry_run=False))
                        self.assertEqual(rc, 0)
                        self.assertEqual(mock_ib.called, expect_called)
                        mock_ib.reset_mock()
                        rc2 = u.cmd_need(
                            argparse.Namespace(packages=["somepkg"], node=False, bin=False, cask=False, yes=False)
                        )
                        self.assertEqual(rc2, 0)
                        self.assertEqual(mock_ib.called, expect_called)


if __name__ == "__main__":
    unittest.main()

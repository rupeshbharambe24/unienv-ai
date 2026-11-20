"""Unit tests for ZDOTDIR zsh wiring and doctor checks (#23)."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class ZdotdirTests(unittest.TestCase):
    def test_wire_shells_and_uninstall_include_zdotdir_zshenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            zdot = home / ".config/zsh"
            unienv_home = home / ".unienv"
            env_sh = unienv_home / "env.sh"
            shell_files = [home / ".zshenv", home / ".profile"]

            with (
                patch.dict(os.environ, {"ZDOTDIR": str(zdot)}),
                patch.object(u, "HOME", home),
                patch.object(u, "UNIENV_HOME", unienv_home),
                patch.object(u, "ENV_SH", env_sh),
                patch.object(u, "SHELL_FILES", shell_files),
            ):
                changed = u.wire_shells()
                self.assertIn(str(zdot / ".zshenv").replace(str(home), "~"), changed)
                self.assertIsNotNone(u.read_block(zdot / ".zshenv", u.SHELL_BEGIN, u.SHELL_END))
                self.assertIsNotNone(u.read_block(home / ".zshenv", u.SHELL_BEGIN, u.SHELL_END))
                saved = json.loads((unienv_home / "state.json").read_text())
                self.assertIn(str((zdot / ".zshenv").resolve()), saved["wired_shell_files"])

                with (
                    patch.dict(os.environ, {"ZDOTDIR": ""}),
                    patch.object(u, "agent_targets", return_value=[]),
                    patch.object(u, "mcp_clients", return_value={}),
                    patch.object(u, "LAUNCH_AGENT", home / "none.plist"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    u.cmd_uninstall(argparse.Namespace(purge=False))

                self.assertIsNone(u.read_block(zdot / ".zshenv", u.SHELL_BEGIN, u.SHELL_END))
                self.assertIsNone(u.read_block(home / ".zshenv", u.SHELL_BEGIN, u.SHELL_END))

    def test_zdotdir_equal_to_home_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shell_files = [home / ".zshenv", home / ".profile"]
            with (
                patch.dict(os.environ, {"ZDOTDIR": str(home)}),
                patch.object(u, "HOME", home),
                patch.object(u, "SHELL_FILES", shell_files),
            ):
                self.assertIsNone(u._zdotdir())
                self.assertEqual(u._shell_files(), shell_files)

    @unittest.skipUnless(Path("/bin/zsh").exists(), "/bin/zsh required")
    def test_doctor_probes_zdotdir_and_zsh_finds_ue_py_after_wire(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            home = Path(tmp)
            zdot = home / ".config/zsh"
            zdot.mkdir(parents=True)
            unienv_home = home / ".unienv"
            bin_dir = unienv_home / "bin"
            bin_dir.mkdir(parents=True)
            ue_py = bin_dir / "ue-py"
            ue_py.write_text("#!/bin/sh\nexit 0\n")
            ue_py.chmod(0o755)
            manifest = unienv_home / "manifest.json"
            manifest.write_text("{}")

            stack.enter_context(patch.dict(os.environ, {"ZDOTDIR": str(zdot), "UNIENV_HOME": str(unienv_home)}))
            for name, value in {
                "HOME": home,
                "UNIENV_HOME": unienv_home,
                "BIN": bin_dir,
                "PY_DIR": unienv_home / "py",
                "ENV_SH": unienv_home / "env.sh",
                "MANIFEST_FILE": manifest,
                "SHELL_FILES": [home / ".zshenv", home / ".profile"],
                "IS_MAC": False,
            }.items():
                stack.enter_context(patch.object(u, name, value))
            for name, value in {
                "load_recipes": {},
                "load_state": {"options": {"exclude": [], "wire_homebrew": False}},
                "active_recipes": [],
                "wanted": {"node": [], "bins": [], "skipped_bins": []},
                "node_packages": {},
                "agent_targets": [],
                "mcp_clients": {},
                "brew_prefix": None,
                "render_agent_block": "",
            }.items():
                stack.enter_context(patch.object(u, name, return_value=value))

            # Wire only ~/.zshenv first (simulating pre-fix behavior): ZDOTDIR probe fails
            u.write_env_sh({"options": {"wire_homebrew": False}})
            u.upsert_block(home / ".zshenv", f'[ -f "{u.ENV_SH}" ] && . "{u.ENV_SH}"', u.SHELL_BEGIN, u.SHELL_END)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
            checks = {c["id"]: c for c in json.loads(out.getvalue())["checks"]}
            self.assertEqual(checks["shell:zsh -c"]["status"], "ok")
            self.assertEqual(checks["shell:ZDOTDIR zsh -c"]["status"], "fail")

            # After wire_shells(), $ZDOTDIR/.zshenv is wired and the ZDOTDIR probe passes
            u.wire_shells()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
            checks = {c["id"]: c for c in json.loads(out.getvalue())["checks"]}
            self.assertEqual(checks["shell:ZDOTDIR zsh -c"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()

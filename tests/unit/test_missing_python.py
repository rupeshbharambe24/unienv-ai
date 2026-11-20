"""Unit tests for missing Python interpreter fallback and repair messaging (#24)."""

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class MissingPythonFallbackTests(unittest.TestCase):
    def test_unienv_shim_prints_repair_message_when_interpreter_and_uv_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            gone_py = root / "gone/python3.12"
            with (
                patch.object(u, "UNIENV_HOME", root),
                patch.object(u, "BIN", bin_dir),
                patch.object(u.sys, "executable", str(gone_py)),
                patch.object(u, "which", return_value=None),
            ):
                u.write_shims()

            r = subprocess.run(
                [str(bin_dir / "unienv"), "--version"],
                env={"PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("UniEnv's Python 3.12 interpreter is missing. Repair with:", r.stderr)
            self.assertIn("install.sh | bash", r.stderr)

    def test_unienv_shim_finds_absolute_uv_with_gui_minimal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            fake_uv_dir = root / "uv bin"
            fake_uv_dir.mkdir()
            fake_uv = fake_uv_dir / "uv"
            fake_uv.write_text(f'#!/bin/sh\necho "{sys.executable}"\n')
            fake_uv.chmod(0o755)

            gone_py = root / "gone/python3.12"
            with (
                patch.object(u, "UNIENV_HOME", root),
                patch.object(u, "BIN", bin_dir),
                patch.object(u.sys, "executable", str(gone_py)),
                patch.object(u, "which", side_effect=lambda name: str(fake_uv) if name == "uv" else None),
            ):
                u.write_shims()

            r = subprocess.run(
                [str(bin_dir / "unienv"), "--version"],
                env={"PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn(f"unienv {u.VERSION}", r.stdout)

    def test_ue_py_prints_repair_message_when_venv_python_is_missing_or_dangling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            venv_bin = root / "py/.venv/bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").symlink_to(root / "deleted/python3.12")

            with (
                patch.object(u, "UNIENV_HOME", root),
                patch.object(u, "BIN", bin_dir),
            ):
                u.write_shims()

            r = subprocess.run(
                [str(bin_dir / "ue-py"), "-c", "print(1)"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("UniEnv's Python 3.12 interpreter is missing. Repair with:", r.stderr)

    def test_doctor_reports_dangling_venv_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            venv_bin = root / "py/.venv/bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").symlink_to(root / "deleted/python3.12")
            manifest = root / "manifest.json"
            manifest.write_text("{}")

            for name, value in {
                "HOME": root,
                "UNIENV_HOME": root,
                "PY_DIR": root / "py",
                "MANIFEST_FILE": manifest,
                "IS_MAC": False,
            }.items():
                stack.enter_context(patch.object(u, name, value))
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
                "_clean_shell_probe": str(u.BIN / "ue-py"),
            }.items():
                stack.enter_context(patch.object(u, name, return_value=value))

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))

            self.assertEqual(rc, 1)
            checks = {c["id"]: c for c in json.loads(out.getvalue())["checks"]}
            self.assertEqual(checks["python"]["status"], "fail")
            self.assertIn("dangling symlink", checks["python"]["message"])
            self.assertIn("install.sh", checks["python"]["fix"])


if __name__ == "__main__":
    unittest.main()

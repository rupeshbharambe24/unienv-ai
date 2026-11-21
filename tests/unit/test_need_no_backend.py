#!/usr/bin/env python3
"""Unit tests for Issue #18: `unienv need --bin` on platforms without a system package backend."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
import unienv  # noqa: E402


class NeedNoBackendTest(unittest.TestCase):
    def test_install_system_returns_failures_and_hint_on_unsupported_platform(self):
        w = {"brew": ["ffmpeg"], "cask": [], "apt": [], "bins": ["uv", "node"]}
        err = io.StringIO()
        with (
            mock.patch.object(unienv, "platform_key", return_value="linux"),
            contextlib.redirect_stderr(err),
        ):
            failures = unienv.install_system(w, assume_yes=False)
        self.assertEqual(failures, ["linux:ffmpeg"])
        self.assertIn("No system package backend for linux yet", err.getvalue())
        self.assertIn("ffmpeg", err.getvalue())

    def test_installed_bins_and_empty_requirements_are_not_failures(self):
        w_bins = {"brew": [], "cask": [], "apt": [], "bins": ["jq"]}
        with (
            mock.patch.object(unienv, "platform_key", return_value="freebsd"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(unienv.install_system(w_bins, assume_yes=False), [])

        w_empty = {"brew": [], "cask": [], "apt": [], "bins": []}
        with (
            mock.patch.object(unienv, "platform_key", return_value="freebsd"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(unienv.install_system(w_empty, assume_yes=False), [])

    def test_all_requested_package_types_are_reported_without_shim_names(self):
        w = {"brew": ["ffmpeg"], "cask": ["libreoffice"], "apt": ["pandoc"], "bins": ["soffice-safe"]}
        err = io.StringIO()
        with mock.patch.object(unienv, "platform_key", return_value="linux"), contextlib.redirect_stderr(err):
            self.assertEqual(
                unienv.install_system(w, assume_yes=False), ["linux:ffmpeg", "linux:libreoffice", "linux:pandoc"]
            )
        self.assertNotIn("soffice-safe", err.getvalue())

    def test_cmd_need_bin_on_unsupported_platform_exits_nonzero_and_leaves_state_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state_file = home / "state.json"
            initial_state = {
                "version": unienv.VERSION,
                "batteries": ["data"],
                "extras": {"python": [], "node": [], "brew": [], "cask": [], "apt": []},
                "options": {"wire_homebrew": True, "exclude": []},
            }
            state_file.write_text(json.dumps(initial_state, indent=2) + "\n")
            before_bytes = state_file.read_bytes()

            args = argparse.Namespace(
                packages=["ffmpeg"],
                node=False,
                bin=True,
                cask=False,
                yes=False,
            )
            out = io.StringIO()
            err = io.StringIO()
            with (
                mock.patch.object(unienv, "UNIENV_HOME", home),
                mock.patch.object(unienv, "STATE_FILE", state_file),
                mock.patch.object(unienv, "BACKUP_DIR", home / "backups"),
                mock.patch.object(unienv, "LOG_FILE", home / "log" / "unienv.log"),
                mock.patch.object(unienv, "PY_DIR", home / "py"),
                mock.patch.object(unienv, "NODE_DIR", home / "node"),
                mock.patch.object(unienv, "platform_key", return_value="linux"),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                rc = unienv.cmd_need(args)

            self.assertNotEqual(rc, 0)
            self.assertEqual(state_file.read_bytes(), before_bytes)
            self.assertNotIn("✓ installed", out.getvalue())
            self.assertIn("rolling back", err.getvalue())


if __name__ == "__main__":
    unittest.main()

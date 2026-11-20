#!/usr/bin/env python3
"""Unit tests for Issue #8: Windows support (winget/Scoop, .cmd shims, venv python path, recipe schema)."""

import contextlib
import json
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
import unienv  # noqa: E402


def _result(cmd, stdout="", code=0):
    return subprocess.CompletedProcess(cmd, code, stdout, "")


class WindowsPlatformAndRecipesTest(unittest.TestCase):
    def test_platform_key_returns_windows_on_win32(self):
        with (
            mock.patch.object(unienv, "IS_MAC", False),
            mock.patch.object(unienv, "IS_WIN", True),
        ):
            self.assertEqual(unienv.platform_key(), "windows")

    def test_venv_python_uses_scripts_on_windows_and_bin_on_posix(self):
        with mock.patch.object(unienv, "IS_WIN", True):
            self.assertEqual(unienv.venv_python(), unienv.PY_DIR / ".venv/Scripts/python.exe")
        with mock.patch.object(unienv, "IS_WIN", False):
            self.assertEqual(unienv.venv_python(), unienv.PY_DIR / ".venv/bin/python")

    def test_wanted_collects_winget_and_scoop_and_honours_exclude(self):
        recipe = {
            "system": {
                "windows": {
                    "winget": ["JohnMacFarlane.Pandoc", "TheDocumentFoundation.LibreOffice"],
                    "scoop": ["pandoc", "libreoffice"],
                },
            },
            "verify": {"bins": ["pandoc", "soffice-safe"], "provided_by": {"soffice-safe": "libreoffice"}},
        }
        state = {
            "options": {"exclude": ["libreoffice"]},
            "extras": {k: [] for k in ("python", "node", "brew", "cask", "apt", "winget", "scoop")},
        }
        with mock.patch.object(unienv, "platform_key", return_value="windows"):
            w = unienv.wanted([recipe], state)
        self.assertEqual(w["winget"], ["JohnMacFarlane.Pandoc"])
        self.assertEqual(w["scoop"], ["pandoc"])
        self.assertEqual(w["skipped_bins"], ["soffice-safe"])

    def test_validate_recipe_accepts_windows_winget_and_scoop(self):
        base = {"schema": "unienv/recipe/v0", "id": "test", "summary": "Test"}
        valid = {
            **base,
            "system": {"windows": {"winget": ["jqlang.jq"], "scoop": ["jq"]}},
        }
        self.assertEqual(unienv.validate_recipe(valid, "test"), [])

        invalid = {**base, "system": {"windows": {"choco": ["jq"]}}}
        self.assertTrue(any("unknown [system.windows].choco" in p for p in unienv.validate_recipe(invalid, "test")))

    def test_all_shipped_recipes_define_windows_section(self):
        recipes = unienv.load_recipes()
        for rid, r in recipes.items():
            with self.subTest(recipe=rid):
                self.assertIn("windows", r.get("system", {}))
                self.assertIn("winget", r["system"]["windows"])
                self.assertIn("scoop", r["system"]["windows"])


class WindowsInstallSystemAndShimsTest(unittest.TestCase):
    def test_install_system_prefers_scoop_when_available(self):
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            if cmd == ["C:\\scoop\\shims\\scoop.cmd", "list"]:
                return _result(cmd, stdout="Installed apps:\n  jq 1.7.1\n")
            return _result(cmd, code=0)

        w = {"scoop": ["jq", "ripgrep"], "winget": ["jqlang.jq", "BurntSushi.ripgrep.MSVC"], "bins": []}
        with (
            mock.patch.object(unienv, "platform_key", return_value="windows"),
            mock.patch.object(
                unienv,
                "which",
                side_effect=lambda b: "C:\\scoop\\shims\\scoop.cmd" if b == "scoop" else None,
            ),
            mock.patch.object(unienv, "run", side_effect=fake_run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            failures = unienv.install_system(w, assume_yes=True)
        self.assertEqual(failures, [])
        self.assertIn(["C:\\scoop\\shims\\scoop.cmd", "install", "ripgrep"], cmds)
        self.assertNotIn(["C:\\scoop\\shims\\scoop.cmd", "install", "jq"], cmds)

    def test_install_system_uses_winget_when_scoop_absent(self):
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            if "list" in cmd:
                if "jqlang.jq" in cmd:
                    return _result(cmd, stdout="jq jqlang.jq 1.7.1\n", code=0)
                return _result(cmd, stdout="No installed package found matching input criteria.\n", code=1)
            return _result(cmd, code=0)

        w = {"scoop": ["jq", "ripgrep"], "winget": ["jqlang.jq", "BurntSushi.ripgrep.MSVC"], "bins": []}
        with (
            mock.patch.object(unienv, "platform_key", return_value="windows"),
            mock.patch.object(
                unienv,
                "which",
                side_effect=lambda b: "C:\\Windows\\System32\\winget.exe" if b == "winget" else None,
            ),
            mock.patch.object(unienv, "run", side_effect=fake_run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            failures = unienv.install_system(w, assume_yes=True)
        self.assertEqual(failures, [])
        install_cmds = [c for c in cmds if "install" in c]
        self.assertEqual(len(install_cmds), 1)
        self.assertIn("BurntSushi.ripgrep.MSVC", install_cmds[0])

    def test_write_shims_generates_cmd_wrappers_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / "bin"
            py_dir = home / "py"
            with (
                mock.patch.object(unienv, "IS_WIN", True),
                mock.patch.object(unienv, "UNIENV_HOME", home),
                mock.patch.object(unienv, "BIN", bin_dir),
                mock.patch.object(unienv, "PY_DIR", py_dir),
                mock.patch.object(unienv, "which", return_value="C:\\Program Files\\nodejs\\node.exe"),
            ):
                unienv.write_shims()
            for name in ("ue-py.cmd", "ue-node.cmd", "unienv.cmd", "ue.cmd", "soffice-safe.cmd"):
                path = bin_dir / name
                self.assertTrue(path.exists(), f"{name} should be created on Windows")
            self.assertIn("Scripts", (bin_dir / "ue-py.cmd").read_text())
            self.assertIn("PYTHONUTF8=1", (bin_dir / "ue-py.cmd").read_text())
            self.assertIn("NODE_PATH", (bin_dir / "ue-node.cmd").read_text())
            self.assertIn("Scripts/python.exe", (bin_dir / "ue-py").read_text())
            self.assertTrue((bin_dir / "ue-py").read_text().startswith("#!/bin/sh\n"))
            self.assertIn("_soffice-safe", (bin_dir / "soffice-safe").read_text())
            self.assertNotIn(".venv/bin", (bin_dir / "ue-py").read_text())

    def test_windows_does_not_write_posix_profiles(self):
        with mock.patch.object(unienv, "IS_WIN", True), mock.patch.object(unienv, "upsert_block") as write:
            self.assertEqual(unienv.wire_shells(), [])
            write.assert_not_called()

    def test_windows_lock_is_released_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            locking = mock.Mock()
            module = mock.Mock(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
            with (
                mock.patch.object(unienv, "IS_WIN", True),
                mock.patch.object(unienv, "UNIENV_HOME", Path(tmp)),
                mock.patch.dict(sys.modules, {"msvcrt": module}),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    with unienv.state_lock():
                        raise RuntimeError("failed")
            self.assertEqual([call.args[1] for call in locking.call_args_list], [1, 2])

    def test_soffice_wrapper_uses_isolated_profile_and_preserves_arguments(self):
        from types import SimpleNamespace

        with (
            mock.patch.object(unienv, "libreoffice_path", return_value="office.exe"),
            mock.patch.object(unienv, "run", return_value=SimpleNamespace(returncode=9)) as run,
        ):
            self.assertEqual(unienv.main(["_soffice-safe", "--convert-to", "pdf", "a b.docx"]), 9)
        argv = run.call_args.args[0]
        self.assertTrue(argv[1].startswith("-env:UserInstallation=file:"))
        self.assertEqual(argv[-3:], ["--convert-to", "pdf", "a b.docx"])

    def test_uninstall_removes_only_owned_path_entry(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            entry = str(home / "bin")
            marker = home / "windows-path.json"
            marker.write_text(json.dumps({"entry": entry}), encoding="utf-8-sig")
            key = mock.MagicMock()
            registry = SimpleNamespace(
                HKEY_CURRENT_USER=1,
                KEY_READ=2,
                KEY_WRITE=4,
                OpenKey=mock.Mock(return_value=key),
                QueryValueEx=mock.Mock(return_value=(entry + ";other;later", 2)),
                SetValueEx=mock.Mock(),
            )
            with (
                mock.patch.object(unienv, "IS_WIN", True),
                mock.patch.object(unienv, "UNIENV_HOME", home),
                mock.patch.dict(sys.modules, {"winreg": registry}),
            ):
                unienv._remove_windows_path()
            self.assertEqual(registry.SetValueEx.call_args.args[-1], "other;later")
            self.assertFalse(marker.exists())

    def test_install_ps1_exists(self):
        ps1 = Path(__file__).resolve().parents[2] / "install.ps1"
        self.assertTrue(ps1.exists())
        text = ps1.read_text()
        self.assertIn("python install 3.12", text)
        self.assertIn("PYTHONUTF8", text)


if __name__ == "__main__":
    unittest.main()

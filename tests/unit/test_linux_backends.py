#!/usr/bin/env python3
"""Unit tests for Issue #7: Linux dnf (Fedora/RHEL) and pacman (Arch) backends."""

import contextlib
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


class LinuxPlatformKeyTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(unienv, "IS_WIN", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_detects_fedora_via_os_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            os_rel = Path(tmp) / "os-release"
            os_rel.write_text("ID=fedora\nVERSION_ID=41\n")
            with (
                mock.patch.object(unienv, "IS_MAC", False),
                mock.patch.object(unienv, "_os_release_tokens", return_value=unienv._os_release_tokens(os_rel)),
                mock.patch.object(Path, "exists", return_value=False),
            ):
                self.assertEqual(unienv.platform_key(), "fedora")

    def test_rhel_clones_do_not_use_fedora_packages_without_epel(self):
        with tempfile.TemporaryDirectory() as tmp:
            os_rel = Path(tmp) / "os-release"
            os_rel.write_text('ID="rocky"\nID_LIKE="rhel centos fedora"\n')
            with (
                mock.patch.object(unienv, "IS_MAC", False),
                mock.patch.object(unienv, "_os_release_tokens", return_value=unienv._os_release_tokens(os_rel)),
                mock.patch.object(Path, "exists", return_value=False),
            ):
                self.assertEqual(unienv.platform_key(), sys.platform)

    def test_detects_arch_via_os_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            os_rel = Path(tmp) / "os-release"
            os_rel.write_text("ID=arch\n")
            with (
                mock.patch.object(unienv, "IS_MAC", False),
                mock.patch.object(unienv, "_os_release_tokens", return_value=unienv._os_release_tokens(os_rel)),
                mock.patch.object(Path, "exists", return_value=False),
            ):
                self.assertEqual(unienv.platform_key(), "arch")

    def test_detects_fedora_or_arch_via_which_fallback(self):
        with (
            mock.patch.object(unienv, "IS_MAC", False),
            mock.patch.object(unienv, "_os_release_tokens", return_value=set()),
            mock.patch.object(Path, "exists", return_value=False),
            mock.patch.object(unienv, "which", side_effect=lambda b: "/usr/bin/dnf" if b == "dnf" else None),
        ):
            self.assertEqual(unienv.platform_key(), "fedora")

        with (
            mock.patch.object(unienv, "IS_MAC", False),
            mock.patch.object(unienv, "_os_release_tokens", return_value=set()),
            mock.patch.object(Path, "exists", return_value=False),
            mock.patch.object(unienv, "which", side_effect=lambda b: "/usr/bin/pacman" if b == "pacman" else None),
        ):
            self.assertEqual(unienv.platform_key(), "arch")


class LinuxWantedAndRecipesTest(unittest.TestCase):
    def test_wanted_collects_dnf_and_pacman_and_honours_exclude(self):
        recipe = {
            "system": {
                "fedora": {"dnf": ["pandoc", "libreoffice-writer", "libreoffice-calc"]},
                "arch": {"pacman": ["pandoc-cli", "libreoffice-fresh"]},
            },
            "verify": {"bins": ["pandoc", "soffice-safe"], "provided_by": {"soffice-safe": "libreoffice"}},
        }
        state = {
            "options": {"exclude": ["libreoffice"]},
            "extras": {k: [] for k in ("python", "node", "brew", "cask", "apt", "dnf", "pacman")},
        }
        with mock.patch.object(unienv, "platform_key", return_value="fedora"):
            w_fed = unienv.wanted([recipe], state)
        self.assertEqual(w_fed["dnf"], ["pandoc"])

        with mock.patch.object(unienv, "platform_key", return_value="arch"):
            w_arch = unienv.wanted([recipe], state)
        self.assertEqual(w_arch["pacman"], ["pandoc-cli"])

    def test_validate_recipe_accepts_fedora_and_arch_and_rejects_invalid(self):
        base = {"schema": "unienv/recipe/v0", "id": "test", "summary": "Test recipe"}
        valid = {
            **base,
            "system": {
                "fedora": {"dnf": ["ripgrep"]},
                "arch": {"pacman": ["ripgrep"]},
            },
        }
        self.assertEqual(unienv.validate_recipe(valid, "test"), [])

        bad_mgr = {**base, "system": {"fedora": {"apt": ["ripgrep"]}}}
        self.assertTrue(any("unknown [system.fedora].apt" in p for p in unienv.validate_recipe(bad_mgr, "test")))

        bad_type = {**base, "system": {"arch": {"pacman": "ripgrep"}}}
        self.assertTrue(
            any("[system.arch].pacman must be a list" in p for p in unienv.validate_recipe(bad_type, "test"))
        )

    def test_all_shipped_recipes_define_fedora_and_arch(self):
        recipes = unienv.load_recipes()
        for rid, r in recipes.items():
            with self.subTest(recipe=rid):
                self.assertIn("fedora", r.get("system", {}))
                self.assertIn("dnf", r["system"]["fedora"])
                self.assertIn("arch", r.get("system", {}))
                self.assertIn("pacman", r["system"]["arch"])


class LinuxInstallSystemTest(unittest.TestCase):
    def test_fedora_dnf_install_with_assume_yes(self):
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            if cmd[:2] == ["rpm", "-q"]:
                return _result(cmd, code=0 if cmd[2] == "jq" else 1)
            return _result(cmd, code=0)

        w = {"dnf": ["jq", "ripgrep"], "bins": []}
        with (
            mock.patch.object(unienv, "platform_key", return_value="fedora"),
            mock.patch.object(unienv, "run", side_effect=fake_run),
            mock.patch.object(unienv.os, "geteuid", return_value=1000, create=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            failures = unienv.install_system(w, assume_yes=True)
        self.assertEqual(failures, [])
        self.assertIn(["sudo", "dnf", "install", "-y", "ripgrep"], cmds)

    def test_fedora_dnf_without_assume_yes_warns_and_returns_failures(self):
        def fake_run(cmd, **kw):
            return _result(cmd, code=1)

        w = {"dnf": ["ripgrep"], "bins": []}
        err = io.StringIO()
        with (
            mock.patch.object(unienv, "platform_key", return_value="fedora"),
            mock.patch.object(unienv, "run", side_effect=fake_run),
            mock.patch.object(unienv.os, "geteuid", return_value=1000, create=True),
            contextlib.redirect_stderr(err),
        ):
            failures = unienv.install_system(w, assume_yes=False)
        self.assertEqual(failures, ["dnf:ripgrep"])
        self.assertIn("sudo dnf install -y ripgrep", err.getvalue())

    def test_dnf_retries_individual_packages_and_reports_only_failed_packages(self):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "rpm":
                return _result(cmd, code=1)
            return _result(cmd, code=1 if "missing" in cmd else 0)

        with (
            mock.patch.object(unienv, "platform_key", return_value="fedora"),
            mock.patch.object(unienv, "run", side_effect=run),
            mock.patch.object(unienv.os, "geteuid", return_value=0, create=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            failures = unienv.install_system({"dnf": ["missing", "jq"], "bins": []}, assume_yes=True)
        self.assertEqual(failures, ["dnf:missing"])
        self.assertIn(["dnf", "install", "-y", "missing"], calls)
        self.assertIn(["dnf", "install", "-y", "jq"], calls)

    def test_media_recipe_uses_stock_fedora_ffmpeg(self):
        media = unienv.load_recipes()["media"]
        self.assertIn("ffmpeg-free", media["system"]["fedora"]["dnf"])
        self.assertNotIn("ffmpeg", media["system"]["fedora"]["dnf"])

    def test_arch_pacman_install_as_root(self):
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            if cmd[:2] == ["pacman", "-Qi"]:
                return _result(cmd, code=1)
            return _result(cmd, code=0)

        w = {"pacman": ["ripgrep", "jq"], "bins": []}
        with (
            mock.patch.object(unienv, "platform_key", return_value="arch"),
            mock.patch.object(unienv, "run", side_effect=fake_run),
            mock.patch.object(unienv.os, "geteuid", return_value=0, create=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            failures = unienv.install_system(w, assume_yes=False)
        self.assertEqual(failures, [])
        self.assertIn(["pacman", "-S", "--noconfirm", "--needed", "ripgrep", "jq"], cmds)


if __name__ == "__main__":
    unittest.main()

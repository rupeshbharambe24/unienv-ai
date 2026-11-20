"""Sandbox settings merge and ownership tests."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class SandboxProfileTests(unittest.TestCase):
    def test_profiles_only_grant_the_unienv_directory(self):
        claude = json.loads(u.sandbox_profile("claude"))
        self.assertFalse(any(rule.startswith("Bash(") for rule in claude["permissions"]["allow"]))
        self.assertEqual(claude["sandbox"]["filesystem"]["allowWrite"], [str(u.UNIENV_HOME)])
        codex = tomllib.loads(u.sandbox_profile("codex"))
        self.assertNotIn("sandbox_mode", codex)
        self.assertNotIn("readable_roots", codex["sandbox_workspace_write"])
        self.assertEqual(codex["sandbox_workspace_write"]["writable_roots"], [str(u.UNIENV_HOME)])

    def test_codex_merge_preserves_tables_mode_and_existing_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "config.toml"
            original = {
                "sandbox_mode": "read-only",
                "model": "test-model",
                "sandbox_workspace_write": {"network_access": False, "writable_roots": ["/existing"]},
                "profiles": {"work": {"model": "other", "approval_policy": "on-request"}},
            }
            config.write_text(u._toml_text(original))
            with patch.object(u, "UNIENV_HOME", root / "unienv"):
                self.assertTrue(u._install_codex_sandbox(config))
                self.assertFalse(u._install_codex_sandbox(config))
                current = tomllib.loads(config.read_text())
                self.assertEqual(current["sandbox_mode"], "read-only")
                self.assertEqual(current["profiles"], original["profiles"])
                self.assertEqual(
                    current["sandbox_workspace_write"]["writable_roots"], ["/existing", str(u.UNIENV_HOME)]
                )
                current["model"] = "user-changed-after-install"
                config.write_text(u._toml_text(current))
                u._uninstall_sandbox()
            restored = tomllib.loads(config.read_text())
            self.assertEqual(restored["sandbox_workspace_write"], original["sandbox_workspace_write"])
            self.assertEqual(restored["model"], "user-changed-after-install")
            self.assertEqual(restored["profiles"], original["profiles"])
            self.assertTrue(list(root.glob("config.toml.unienv-backup-*")))

    def test_claude_preserves_existing_rules_and_removes_only_owned_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cfg = root / "settings.json"
            cfg.write_text(json.dumps({"permissions": {"allow": ["Bash(git status)"]}, "theme": "dark"}))
            with patch.object(u, "UNIENV_HOME", root / "unienv"):
                self.assertTrue(u._install_claude_sandbox(cfg))
                self.assertFalse(u._install_claude_sandbox(cfg))
                current = json.loads(cfg.read_text())
                current["permissions"]["allow"].append("Read(/user-added)")
                cfg.write_text(json.dumps(current))
                u._uninstall_sandbox()
            restored = json.loads(cfg.read_text())
            self.assertEqual(
                restored, {"permissions": {"allow": ["Bash(git status)", "Read(/user-added)"]}, "theme": "dark"}
            )

    def test_preexisting_unienv_root_is_not_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cfg = root / "config.toml"
            with patch.object(u, "UNIENV_HOME", root / "unienv"):
                cfg.write_text(u.sandbox_profile("codex"))
                self.assertFalse(u._install_codex_sandbox(cfg))
                u._uninstall_sandbox()
                self.assertEqual(
                    tomllib.loads(cfg.read_text())["sandbox_workspace_write"]["writable_roots"], [str(u.UNIENV_HOME)]
                )

    def test_uninstall_command_removes_new_config_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            with (
                patch.object(u, "HOME", home),
                patch.object(u, "UNIENV_HOME", home / "unienv"),
                patch.object(u, "SHELL_FILES", []),
                patch.object(u, "agent_targets", return_value=[]),
                patch.object(u, "mcp_clients", return_value={}),
                patch.object(u, "LAUNCH_AGENT", home / "absent.plist"),
                patch.dict(os.environ, {"CODEX_HOME": str(home / "codex")}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(u.cmd_sandbox(argparse.Namespace(action="install", tool="all")), 0)
                self.assertEqual(u.cmd_uninstall(argparse.Namespace(purge=False)), 0)
                self.assertFalse((home / "codex/config.toml").exists())
                self.assertFalse((home / ".claude/settings.json").exists())

    def test_invalid_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cfg = root / "config.toml"
            cfg.write_text("[broken")
            with patch.object(u, "UNIENV_HOME", root / "unienv"), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    u._install_codex_sandbox(cfg)
            self.assertEqual(cfg.read_text(), "[broken")

    def test_seatbelt_profile_includes_write_access_to_unienv(self):
        text = u.sandbox_profile("seatbelt")
        self.assertIn("file-write*", text)
        self.assertIn(json.dumps(str(u.UNIENV_HOME)), text)


if __name__ == "__main__":
    unittest.main()

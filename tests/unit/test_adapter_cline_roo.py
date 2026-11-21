"""Unit tests for the Cline and Roo Code agent adapters (#6)."""

import argparse
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class ClineRooAdapterTests(unittest.TestCase):
    def _manifest(self):
        return {
            "batteries": ["core"],
            "bins": {"jq": "/usr/bin/jq"},
            "python": {"packages": {"pandas": "2.2.3"}},
            "node": {"packages": {}},
        }

    def test_cline_detection_via_dirs_vscode_ext_or_bin(self):
        for ext_dir in (
            ".vscode/extensions/saoudrizwan.claude-dev-3.8.0",
            ".vscode-insiders/extensions/saoudrizwan.claude-dev-3.8.0",
            ".cursor/extensions/saoudrizwan.claude-dev-3.8.0",
            ".windsurf/extensions/saoudrizwan.claude-dev-3.8.0",
        ):
            with self.subTest(ext_dir=ext_dir), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                with patch.object(u, "HOME", home), patch.object(u, "which", return_value=None):
                    cline = next(a for a in u.agent_targets() if a["id"] == "cline")
                    self.assertFalse(u.detected(cline))

                    (home / ext_dir).mkdir(parents=True)
                    cline = next(a for a in u.agent_targets() if a["id"] == "cline")
                    self.assertTrue(u.detected(cline))

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".cline").mkdir()
            with patch.object(u, "HOME", home), patch.object(u, "which", return_value=None):
                cline = next(a for a in u.agent_targets() if a["id"] == "cline")
                self.assertTrue(u.detected(cline))

    def test_absent_cline_does_not_probe_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = Path.exists

            def exists(path):
                if path == home / "Documents/Cline":
                    raise AssertionError("Documents must not be probed for detection")
                return original(path)

            with (
                patch.object(u, "HOME", home),
                patch.object(u, "which", return_value=None),
                patch.object(Path, "exists", exists),
            ):
                cline = next(a for a in u.agent_targets() if a["id"] == "cline")
                self.assertFalse(u.detected(cline))

    def test_roo_code_detection_via_dir_or_vscode_ext(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(u, "HOME", home), patch.object(u, "which", return_value=None):
                roo = next(a for a in u.agent_targets() if a["id"] == "roo-code")
                self.assertFalse(u.detected(roo))

                (home / ".vscode/extensions/rooveterinaryinc.roo-cline-3.11.0").mkdir(parents=True)
                roo = next(a for a in u.agent_targets() if a["id"] == "roo-code")
                self.assertTrue(u.detected(roo))

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".roo").mkdir()
            with patch.object(u, "HOME", home), patch.object(u, "which", return_value=None):
                roo = next(a for a in u.agent_targets() if a["id"] == "roo-code")
                self.assertTrue(u.detected(roo))

    def test_sync_and_uninstall_for_cline_and_roo(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            unienv_home = home / ".unienv"
            unienv_home.mkdir()
            (home / ".vscode/extensions/saoudrizwan.claude-dev-3.8.0").mkdir(parents=True)
            (home / ".roo").mkdir(parents=True)
            manifest = self._manifest()

            with (
                patch.object(u, "HOME", home),
                patch.object(u, "UNIENV_HOME", unienv_home),
                patch.object(u, "AGENTS_COPY", unienv_home / "AGENTS.md"),
                patch.object(u, "which", return_value=None),
            ):
                done = u.sync_agents(manifest, [])
                self.assertEqual(done, ["Cline", "Roo Code"])

                cline_rules = home / ".cline/rules/unienv.md"
                roo_rules = home / ".roo/rules/unienv.md"
                expected = u.render_agent_block(manifest).rstrip()
                self.assertEqual(u.read_block(cline_rules, u.MD_BEGIN, u.MD_END), expected)
                self.assertEqual(u.read_block(roo_rules, u.MD_BEGIN, u.MD_END), expected)

                # Even if Cline extension was uninstalled before `unienv uninstall`,
                # `~/.cline/rules/unienv.md` must still be cleaned up.
                import shutil
                shutil.rmtree(home / ".vscode")

                with (
                    patch.object(u, "SHELL_FILES", []),
                    patch.object(u, "LAUNCH_AGENT", home / "none.plist"),
                    patch.object(u, "mcp_clients", return_value={}),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    u.cmd_uninstall(argparse.Namespace(purge=False))

                self.assertFalse(cline_rules.exists())
                self.assertFalse(roo_rules.exists())


if __name__ == "__main__":
    unittest.main()

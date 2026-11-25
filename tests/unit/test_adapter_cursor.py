"""Unit tests for the Cursor global rules agent adapter (#4)."""

import argparse
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import isolated_home  # noqa: E402

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class CursorAdapterTests(unittest.TestCase):
    def _manifest(self, *, packages=None):
        return {
            "batteries": ["core"],
            "bins": {"jq": "/usr/bin/jq"},
            "python": {"packages": packages or {"pandas": "2.2.3"}},
            "node": {"packages": {}},
        }

    def test_cursor_detected_by_dir_or_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(u, "HOME", home), patch.object(u, "which", return_value=None):
                target = next(a for a in u.agent_targets() if a["id"] == "cursor")
                self.assertFalse(u.detected(target))
                self.assertEqual(target["instructions"], home / ".cursor/rules/unienv.mdc")
                (home / ".cursor").mkdir(parents=True)
                self.assertTrue(u.detected(target))

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                patch.object(u, "HOME", home),
                patch.object(
                    u, "which", side_effect=lambda name: "/usr/local/bin/cursor" if name == "cursor" else None
                ),
            ):
                target = next(a for a in u.agent_targets() if a["id"] == "cursor")
                self.assertTrue(u.detected(target))

    def test_sync_creates_mdc_with_frontmatter_and_uninstall_removes_owned_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            unienv_home = home / ".unienv"
            unienv_home.mkdir()
            (home / ".cursor").mkdir()
            rules_path = home / ".cursor/rules/unienv.mdc"
            sibling_rule = home / ".cursor/rules/personal.mdc"
            manifest = self._manifest()

            with isolated_home(u, home, unienv_home=unienv_home, shell_files=[]):
                # Directory ~/.cursor/rules does not exist yet
                self.assertFalse(rules_path.parent.exists())
                done = u.sync_agents(manifest, [])
                self.assertEqual(done, ["Cursor"])
                self.assertTrue(rules_path.exists())

                text = rules_path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                self.assertIn("alwaysApply: true\n", text)
                self.assertIn("description: UniEnv AI shared Python, Node.js, and system tools environment\n", text)
                self.assertEqual(
                    u.read_block(rules_path, u.MD_BEGIN, u.MD_END),
                    u.render_agent_block(manifest).rstrip(),
                )

                # Repeated sync is idempotent (no duplicate frontmatter or blocks)
                u.sync_agents(manifest, [])
                self.assertEqual(rules_path.read_text(encoding="utf-8"), text)
                self.assertEqual(rules_path.read_text(encoding="utf-8").count("alwaysApply: true"), 1)
                self.assertEqual(rules_path.read_text(encoding="utf-8").count(u.MD_BEGIN), 1)

                # Updating manifest updates managed block while preserving frontmatter
                updated_manifest = self._manifest(packages={"pandas": "2.2.3", "seaborn": "0.13.2"})
                u.sync_agents(updated_manifest, [])
                self.assertIn("seaborn", u.read_block(rules_path, u.MD_BEGIN, u.MD_END))
                self.assertEqual(rules_path.read_text(encoding="utf-8").count("alwaysApply: true"), 1)

                # Sibling user rules in ~/.cursor/rules/ are untouched on sync and uninstall
                sibling_rule.write_text("---\nalwaysApply: true\n---\nPrefer functional style.\n", encoding="utf-8")
                with (
                    patch.object(u, "LAUNCH_AGENT", home / "none.plist"),
                    patch.object(u, "mcp_clients", return_value={}),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    u.cmd_uninstall(argparse.Namespace(purge=False))

                self.assertFalse(rules_path.exists())
                self.assertTrue(sibling_rule.exists())
                self.assertIn("Prefer functional style.", sibling_rule.read_text(encoding="utf-8"))

    def test_sync_and_uninstall_preserve_existing_user_content_in_unienv_mdc(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            unienv_home = home / ".unienv"
            unienv_home.mkdir()
            rules_path = home / ".cursor/rules/unienv.mdc"
            rules_path.parent.mkdir(parents=True)
            user_content = "---\ndescription: Custom user rule\nalwaysApply: true\n---\n\nAlways run tests first.\n"
            rules_path.write_text(user_content, encoding="utf-8")
            manifest = self._manifest()

            with isolated_home(u, home, unienv_home=unienv_home, shell_files=[]):
                done = u.sync_agents(manifest, [])
                self.assertEqual(done, ["Cursor"])
                self.assertTrue(rules_path.read_text(encoding="utf-8").startswith(user_content))
                self.assertEqual(
                    u.read_block(rules_path, u.MD_BEGIN, u.MD_END),
                    u.render_agent_block(manifest).rstrip(),
                )

                with (
                    patch.object(u, "LAUNCH_AGENT", home / "none.plist"),
                    patch.object(u, "mcp_clients", return_value={}),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    u.cmd_uninstall(argparse.Namespace(purge=False))

                self.assertTrue(rules_path.exists())
                self.assertIsNone(u.read_block(rules_path, u.MD_BEGIN, u.MD_END))
                self.assertEqual(rules_path.read_text(encoding="utf-8"), user_content)

    def test_all_agents_writes_cursor_rule_even_when_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            unienv_home = home / ".unienv"
            unienv_home.mkdir()
            manifest = self._manifest()

            with isolated_home(u, home, unienv_home=unienv_home, shell_files=[]):
                done = u.sync_agents(manifest, [], only_detected=False)
                self.assertIn("Cursor", done)
                rules_path = home / ".cursor/rules/unienv.mdc"
                self.assertTrue(rules_path.exists())
                self.assertIn("alwaysApply: true", rules_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    u.read_block(rules_path, u.MD_BEGIN, u.MD_END),
                    u.render_agent_block(manifest).rstrip(),
                )


if __name__ == "__main__":
    unittest.main()

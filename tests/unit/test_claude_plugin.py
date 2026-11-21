"""Unit tests for #40 (Claude Code plugin manifests, skill, and MCP launcher)."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ClaudePluginTests(unittest.TestCase):
    def test_single_plugin_manifest_uses_default_skill_discovery(self):
        self.assertFalse((ROOT / ".claude-plugin/plugin.json").exists())
        path = ROOT / "plugins/unienv/.claude-plugin/plugin.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["name"], "unienv")
        self.assertNotIn("version", data)
        self.assertNotIn("skills", data)
        self.assertTrue((ROOT / "plugins/unienv/skills/unienv/SKILL.md").exists())
        self.assertIn("unienv-mcp.sh", data["mcpServers"]["unienv"]["command"])

    def test_marketplace_manifest_is_valid(self):
        path = ROOT / ".claude-plugin" / "marketplace.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["name"], "unienv")
        self.assertEqual(data["plugins"][0]["name"], "unienv")
        self.assertEqual(data["plugins"][0]["source"], "./plugins/unienv")
        self.assertNotIn("version", data["plugins"][0])

    def test_plugin_skill_reads_manifest_and_shows_install_command(self):
        skill = (ROOT / "plugins/unienv/skills/unienv/SKILL.md").read_text()
        self.assertIn("name: unienv", skill)
        self.assertIn("do not run it automatically", skill)
        self.assertNotIn("tell the user or run", skill)
        self.assertIn("~/.unienv/manifest.json", skill)
        self.assertIn(
            "curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash", skill
        )

    def test_plugin_mcp_launcher_prints_install_hint_when_unienv_missing(self):
        script = ROOT / "plugins/unienv/scripts/unienv-mcp.sh"
        self.assertTrue(os.access(script, os.X_OK))
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [str(script)],
                env={"HOME": tmp, "UNIENV_HOME": f"{tmp}/.unienv", "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("UniEnv is not installed", r.stderr)
            self.assertIn("install.sh | bash", r.stderr)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for #41 (Dockerfile, Dev Container Feature, and release workflow publishing)."""

import importlib.util
import json
import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class DevcontainerDockerTests(unittest.TestCase):
    def test_devcontainer_feature_json_matches_schema_and_version(self):
        path = ROOT / ".devcontainer" / "unienv" / "devcontainer-feature.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["id"], "unienv")
        self.assertEqual(data["version"], u.VERSION)
        self.assertIn("name", data)
        self.assertIn("description", data)
        self.assertIn("options", data)
        self.assertIn("batteries", data["options"])
        self.assertEqual(data["options"]["batteries"]["type"], "string")
        self.assertEqual(data["options"]["batteries"]["default"], "")

    def test_devcontainer_install_script_is_pinned_and_avoids_sudo(self):
        script = ROOT / ".devcontainer" / "unienv" / "install.sh"
        self.assertTrue(script.exists())
        self.assertTrue(os.access(script, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(script)], check=False).returncode, 0)
        text = script.read_text()
        self.assertIn("BATTERIES", text)
        self.assertIn("--all-agents", text)
        self.assertIn("--no-system", text)
        self.assertIn("FEATURE_VERSION", text)
        self.assertNotIn("/unienv-ai/main/install.sh", text)
        code_lines = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("sudo", code_lines)

    def test_dockerfile_uses_debian_bookworm_slim_and_all_agents_only(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM debian:bookworm-slim", dockerfile)
        self.assertIn("./install.sh --yes --all-agents", dockerfile)
        self.assertNotIn("cp /root/.unienv/AGENTS.md /root/AGENTS.md", dockerfile)
        self.assertNotIn("cp /root/.unienv/AGENTS.md /root/CLAUDE.md", dockerfile)

    def test_release_workflow_publishes_multiarch_docker_and_devcontainer_feature(self):
        wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertIn("linux/amd64,linux/arm64", wf)
        self.assertIn("ghcr.io/rupeshbharambe24/unienv:", wf)
        self.assertIn("devcontainers/action@v1", wf)
        self.assertIn("devcontainer-feature.json", wf)


if __name__ == "__main__":
    unittest.main()

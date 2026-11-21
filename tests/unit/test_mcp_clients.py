import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class McpClientsTests(unittest.TestCase):
    def test_mcp_clients_paths_and_keys_on_mac_and_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            with patch.object(u, "HOME", home), patch.object(u, "IS_MAC", True):
                mac_clients = u.mcp_clients()
            self.assertEqual(
                set(mac_clients),
                {"claude-desktop", "cursor", "windsurf", "lm-studio", "vscode", "zed"},
            )
            self.assertEqual(
                mac_clients["vscode"],
                home / "Library/Application Support/Code/User/mcp.json",
            )
            self.assertEqual(mac_clients["windsurf"], home / ".codeium/windsurf/mcp_config.json")
            self.assertEqual(mac_clients["lm-studio"], home / ".lmstudio/mcp.json")
            self.assertEqual(mac_clients["zed"], home / ".config/zed/settings.json")

            with patch.object(u, "HOME", home), patch.object(u, "IS_MAC", False):
                linux_clients = u.mcp_clients()
            self.assertEqual(linux_clients["vscode"], home / ".config/Code/User/mcp.json")
            self.assertEqual(
                linux_clients["claude-desktop"],
                home / ".config/Claude/claude_desktop_config.json",
            )

    def test_all_skips_commented_zed_and_never_touches_claude_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            zed = home / ".config/zed/settings.json"
            zed.parent.mkdir(parents=True)
            raw = b'{\n// Zed comment\n"context_servers": {}\n}\n'
            zed.write_bytes(raw)
            claude = home / ".claude.json"
            claude.write_bytes(b'{"session":"leave unchanged"}')
            cursor = home / ".cursor/mcp.json"
            cursor.parent.mkdir()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(u, "HOME", home),
                patch.object(u, "UNIENV_HOME", home),
                patch.object(u, "BIN", home / "bin"),
                patch.object(u, "PY_DIR", home / "py"),
                patch.object(u, "MANIFEST_FILE", home / "manifest.json"),
                patch.object(u, "IS_MAC", False),
                patch.object(u, "LOG_FILE", home / "log"),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(u.cmd_mcp(argparse.Namespace(action="install", client="all")), 0)
                self.assertIn("context_servers", stdout.getvalue())
                self.assertIn("unienv mcp config --client zed", stdout.getvalue())
                self.assertIn("unienv", json.loads(cursor.read_text())["mcpServers"])
                self.assertEqual(u.cmd_mcp(argparse.Namespace(action="uninstall", client="all")), 0)
                self.assertEqual(stderr.getvalue(), "")

                # Doctor also skips commented Zed settings quietly without warning
                (home / "manifest.json").write_text("{}")
                doc_out = io.StringIO()
                with (
                    patch.object(u, "load_recipes", return_value={}),
                    patch.object(u, "load_state", return_value={"options": {"exclude": []}}),
                    patch.object(u, "active_recipes", return_value=[]),
                    patch.object(u, "wanted", return_value={"node": [], "bins": [], "skipped_bins": []}),
                    patch.object(u, "node_packages", return_value={}),
                    patch.object(u, "agent_targets", return_value=[]),
                    patch.object(u, "brew_prefix", return_value=None),
                    patch.object(u, "render_agent_block", return_value=""),
                    patch.object(u, "_clean_shell_probe", return_value=str(home / "bin/ue-py")),
                    contextlib.redirect_stdout(doc_out),
                ):
                    u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
                report = json.loads(doc_out.getvalue())
                self.assertNotIn("mcp:zed", {c["id"] for c in report["checks"]})
            self.assertEqual(zed.read_bytes(), raw)
            self.assertEqual(claude.read_bytes(), b'{"session":"leave unchanged"}')

    def test_install_doctor_and_uninstall_preserve_existing_servers_and_backups(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            home = pathlib.Path(tmp)
            manifest = home / "manifest.json"
            manifest.write_text("{}")
            for name, value in {
                "HOME": home,
                "UNIENV_HOME": home,
                "BIN": home / "bin",
                "LOG_FILE": home / "log" / "unienv.log",
                "PY_DIR": home / "py",
                "MANIFEST_FILE": manifest,
                "IS_MAC": False,
            }.items():
                stack.enter_context(patch.object(u, name, value))

            clients = u.mcp_clients()
            for client_name, cfg_path in clients.items():
                key = u.mcp_client_key(client_name)
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                cfg_path.write_text(json.dumps({key: {"existing": {"command": "other"}}, "extra": True}))

                with contextlib.redirect_stdout(io.StringIO()):
                    rc = u.cmd_mcp(argparse.Namespace(action="install", client=client_name))
                self.assertEqual(rc, 0)

                data = json.loads(cfg_path.read_text())
                self.assertEqual(data["extra"], True)
                self.assertEqual(data[key]["existing"], {"command": "other"})
                self.assertEqual(data[key]["unienv"], u.mcp_entry(client_name))
                if client_name == "vscode":
                    self.assertEqual(data[key]["unienv"]["type"], "stdio")
                backups = list(cfg_path.parent.glob(cfg_path.name + ".unienv-backup-*"))
                self.assertTrue(backups, f"expected backup for {client_name}")

            # Verify doctor detects all configured clients across root keys
            for name, value in {
                "load_recipes": {},
                "load_state": {"options": {"exclude": []}},
                "active_recipes": [],
                "wanted": {"node": [], "bins": [], "skipped_bins": []},
                "node_packages": {},
                "agent_targets": [],
                "brew_prefix": None,
                "render_agent_block": "",
                "_clean_shell_probe": str(u.BIN / "ue-py"),
                "mcp_handshake": (True, "6 tools", 0.1),
            }.items():
                stack.enter_context(patch.object(u, name, return_value=value))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
            report = json.loads(stdout.getvalue())
            mcp_check = next(c for c in report["checks"] if c["id"] == "mcp")
            self.assertEqual(mcp_check["status"], "ok")
            for client_name in clients:
                self.assertIn(client_name, mcp_check["message"])

            # Verify uninstall removes only unienv and preserves existing entries
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_mcp(argparse.Namespace(action="uninstall", client="all"))
            for client_name, cfg_path in clients.items():
                key = u.mcp_client_key(client_name)
                data = json.loads(cfg_path.read_text())
                self.assertEqual(data, {key: {"existing": {"command": "other"}}, "extra": True})


if __name__ == "__main__":
    unittest.main()

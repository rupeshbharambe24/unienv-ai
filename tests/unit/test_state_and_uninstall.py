"""Regression tests for #13 (uninstall leaves MCP entries) and #21 (parallel `need`, corrupt state)."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class UninstallMcpTests(unittest.TestCase):
    def test_uninstall_removes_only_the_unienv_server(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            home = pathlib.Path(tmp)
            for name, value in {"HOME": home, "SHELL_FILES": [], "LAUNCH_AGENT": home / "none.plist"}.items():
                stack.enter_context(patch.object(u, name, value))
            configs = list(u.mcp_clients().values())
            for path in configs:
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"mcpServers": {"unienv": u.mcp_entry(), "other": {"command": "x"}}}))
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_uninstall(argparse.Namespace(purge=False))
            for path in configs:
                with self.subTest(config=path.name):
                    self.assertEqual(json.loads(path.read_text()), {"mcpServers": {"other": {"command": "x"}}})
                    self.assertTrue(list(path.parent.glob(path.name + ".unienv-backup-*")))

    def test_uninstall_without_configs_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            home = pathlib.Path(tmp)
            for name, value in {"HOME": home, "SHELL_FILES": [], "LAUNCH_AGENT": home / "none.plist"}.items():
                stack.enter_context(patch.object(u, name, value))
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_uninstall(argparse.Namespace(purge=False))
            self.assertEqual(list(home.iterdir()), [])


class StateTests(unittest.TestCase):
    def test_atomic_write_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "state.json"
            u.atomic_write(path, "{}\n")
            u.atomic_write(path, '{"a": 1}\n')
            self.assertEqual(path.read_text(), '{"a": 1}\n')
            self.assertEqual([p.name for p in pathlib.Path(tmp).iterdir()], ["state.json"])

    def test_corrupt_state_falls_back_to_newest_backup(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = pathlib.Path(tmp)
            stack.enter_context(patch.object(u, "STATE_FILE", root / "state.json"))
            stack.enter_context(patch.object(u, "BACKUP_DIR", root / "backups"))
            (root / "state.json").write_text('{"batteries": ["of')  # cut off mid-write
            for stamp, batteries in (("20260101-000000", ["pdf"]), ("20260102-000000", ["pdf", "ocr"])):
                (root / "backups" / stamp).mkdir(parents=True)
                (root / "backups" / stamp / ".unienv__state.json").write_text(json.dumps({"batteries": batteries}))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                state = u.load_state({})
            self.assertEqual(state["batteries"], ["pdf", "ocr"])
            self.assertIn("corrupt", err.getvalue())

    @unittest.skipIf(sys.platform == "win32", "flock is POSIX-only")
    def test_state_lock_serialises_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import importlib.util, sys, time\n"
                 f"spec = importlib.util.spec_from_file_location('unienv', {str(ROOT / 'src' / 'unienv.py')!r})\n"
                 "u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)\n"
                 "with u.state_lock():\n"
                 "    print('locked', flush=True); time.sleep(1.5)\n"],
                env={**os.environ, "UNIENV_HOME": tmp}, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                start = time.monotonic()
                with patch.object(u, "UNIENV_HOME", pathlib.Path(tmp)), \
                        contextlib.redirect_stderr(io.StringIO()) as err, u.state_lock():
                    waited = time.monotonic() - start
                self.assertGreater(waited, 0.5)
                self.assertIn("waiting for another unienv command", err.getvalue())
            finally:
                holder.wait(timeout=10)
                holder.stdout.close()


if __name__ == "__main__":
    unittest.main()

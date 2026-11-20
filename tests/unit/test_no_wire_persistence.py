"""Unit tests for persisting `--no-wire` in `state["options"]["wire_shells"]` (#58)."""

import argparse
import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class NoWirePersistenceTests(unittest.TestCase):
    def _setup_home(self, stack, tmp_dir):
        root = pathlib.Path(tmp_dir)
        home = root / "home"
        home.mkdir(parents=True, exist_ok=True)
        shell_files = [home / ".zshenv", home / ".profile"]
        stack.enter_context(patch.object(u, "HOME", home))
        stack.enter_context(patch.object(u, "UNIENV_HOME", root))
        stack.enter_context(patch.object(u, "BIN", root / "bin"))
        stack.enter_context(patch.object(u, "PY_DIR", root / "py"))
        stack.enter_context(patch.object(u, "NODE_DIR", root / "node"))
        stack.enter_context(patch.object(u, "STATE_FILE", root / "state.json"))
        stack.enter_context(patch.object(u, "MANIFEST_FILE", root / "manifest.json"))
        stack.enter_context(patch.object(u, "ENV_SH", root / "env.sh"))
        stack.enter_context(patch.object(u, "AGENTS_COPY", root / "AGENTS.md"))
        stack.enter_context(patch.object(u, "BACKUP_DIR", root / "backups"))
        stack.enter_context(patch.object(u, "LOG_FILE", root / "log" / "unienv.log"))
        stack.enter_context(patch.object(u, "SHELL_FILES", shell_files))
        return root, shell_files

    def test_install_no_wire_persists_through_need_sync_and_doctor_fix(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            _root, shell_files = self._setup_home(stack, tmp)
            stack.enter_context(patch.object(u, "install_system", return_value=[]))
            stack.enter_context(patch.object(u, "install_python", return_value=None))
            stack.enter_context(patch.object(u, "install_node", return_value=None))
            stack.enter_context(patch.object(u, "cmd_doctor", return_value=0))

            install_args = argparse.Namespace(
                batteries=None,
                add=None,
                exclude=None,
                no_system=True,
                no_wire=True,
                all_agents=False,
                yes=True,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_install(install_args)

            state = u.load_state(u.load_recipes())
            self.assertFalse(state["options"]["wire_shells"])
            for sf in shell_files:
                self.assertFalse(sf.exists())

            # `unienv need` must respect persisted wire_shells=False
            need_args = argparse.Namespace(packages=["seaborn"], node=False, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_need(need_args)
            for sf in shell_files:
                self.assertFalse(sf.exists())

            # `unienv sync` (without --no-wire) must respect persisted wire_shells=False
            sync_args = argparse.Namespace(no_wire=False, all_agents=False)
            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_sync(sync_args)
            for sf in shell_files:
                self.assertFalse(sf.exists())

            # `do_sync` called from `doctor --fix` (wire omitted) must respect wire_shells=False
            u.do_sync(u.load_state(u.load_recipes()), u.load_recipes(), quiet=True)
            for sf in shell_files:
                self.assertFalse(sf.exists())

    def test_wire_command_takes_state_lock(self):
        with (
            patch.object(sys, "argv", ["unienv", "wire"]),
            patch.object(u, "state_lock") as lock,
            patch.object(u, "cmd_wire", return_value=0) as wire,
        ):
            self.assertEqual(u.main(), 0)
        lock.assert_called_once_with()
        lock.return_value.__enter__.assert_called_once()
        wire.assert_called_once()

    def test_cmd_wire_reenables_wire_shells(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            _root, shell_files = self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            state["options"]["wire_shells"] = False
            u.save_state(state)

            with contextlib.redirect_stdout(io.StringIO()):
                u.cmd_wire(argparse.Namespace(gui=False))

            state_after = u.load_state(recipes)
            self.assertTrue(state_after["options"]["wire_shells"])
            for sf in shell_files:
                self.assertTrue(sf.exists())
                self.assertIsNotNone(u.read_block(sf, u.SHELL_BEGIN, u.SHELL_END))


if __name__ == "__main__":
    unittest.main()

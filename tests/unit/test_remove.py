"""Unit tests for `unienv remove` (#26)."""

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class RemoveTests(unittest.TestCase):
    def _setup_home(self, stack, tmp_dir):
        root = pathlib.Path(tmp_dir)
        stack.enter_context(patch.object(u, "HOME", root / "home"))
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
        stack.enter_context(patch.object(u, "SHELL_FILES", []))
        return root

    def test_need_then_remove_restores_state_pyproject_and_agent_block(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            state["batteries"] = []
            u.save_state(state)

            installed_py = {"requests": "2.32.0"}

            def fake_install_python(packages):
                u.write_pyproject(packages)
                for p in list(installed_py):
                    if p != "requests" and p not in [u.py_name(x) for x in packages]:
                        installed_py.pop(p, None)
                for p in packages:
                    installed_py.setdefault(u.py_name(p), "1.0.0")

            stack.enter_context(patch.object(u, "install_python", side_effect=fake_install_python))
            stack.enter_context(patch.object(u, "python_packages", side_effect=lambda: dict(installed_py)))

            w = u.wanted(u.active_recipes(recipes, state), state)
            u.install_python(w["python"])
            u.do_sync(state, recipes, quiet=True, wire=False)

            before_state = u.STATE_FILE.read_text()
            before_pyproject = (u.PY_DIR / "pyproject.toml").read_text()
            before_agents = u.AGENTS_COPY.read_text()

            with contextlib.redirect_stdout(io.StringIO()):
                rc_need = u.cmd_need(
                    argparse.Namespace(packages=["seaborn>=0.13"], node=False, bin=False, cask=False, yes=False)
                )
            self.assertEqual(rc_need, 0)
            self.assertIn("seaborn", u.AGENTS_COPY.read_text())

            with contextlib.redirect_stdout(io.StringIO()):
                rc_rm = u.cmd_remove(
                    argparse.Namespace(packages=["seaborn"], node=False, bin=False, cask=False, yes=False)
                )
            self.assertEqual(rc_rm, 0)
            self.assertEqual(u.STATE_FILE.read_text(), before_state)
            self.assertEqual((u.PY_DIR / "pyproject.toml").read_text(), before_pyproject)
            self.assertEqual(u.AGENTS_COPY.read_text(), before_agents)

    def test_refuses_battery_provided_package_with_helpful_message(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            u.save_state(state)

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_remove(argparse.Namespace(packages=["pandas"], node=False, bin=False, cask=False, yes=False))

            self.assertEqual(rc, 1)
            self.assertIn("battery", err.getvalue())
            self.assertIn("--exclude", err.getvalue())

    def test_refuses_uninstalled_package(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            u.save_state(u.default_state(recipes))

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_remove(
                    argparse.Namespace(packages=["not-installed-pkg"], node=False, bin=False, cask=False, yes=False)
                )

            self.assertEqual(rc, 1)
            self.assertIn("not installed", err.getvalue())

    def test_remove_rolls_back_on_install_failure(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            state["extras"]["python"] = ["seaborn"]
            u.save_state(state)
            w = u.wanted(u.active_recipes(recipes, state), state)
            u.write_pyproject(w["python"])
            before_state = u.STATE_FILE.read_text()
            before_pyproject = (u.PY_DIR / "pyproject.toml").read_text()

            def failing_install(packages):
                u.write_pyproject(packages)
                raise subprocess.CalledProcessError(1, ["uv", "sync"])

            stack.enter_context(patch.object(u, "install_python", side_effect=failing_install))

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_remove(
                    argparse.Namespace(packages=["seaborn"], node=False, bin=False, cask=False, yes=False)
                )

            self.assertEqual(rc, 1)
            self.assertIn("rolling back", err.getvalue())
            self.assertEqual(u.STATE_FILE.read_text(), before_state)
            self.assertEqual((u.PY_DIR / "pyproject.toml").read_text(), before_pyproject)

    def test_remove_bin_with_and_without_yes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            state["extras"]["brew"] = ["htop", "jq"]
            u.save_state(state)

            run_calls = []
            stack.enter_context(patch.object(u, "platform_key", return_value="macos"))
            stack.enter_context(patch.object(u, "which", return_value="/opt/homebrew/bin/brew"))
            stack.enter_context(patch.object(u, "run", side_effect=lambda cmd, **kw: run_calls.append(cmd)))
            stack.enter_context(patch.object(u, "do_sync", return_value={}))

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc1 = u.cmd_remove(argparse.Namespace(packages=["htop"], node=False, bin=True, cask=False, yes=False))
            self.assertEqual(rc1, 0)
            self.assertEqual(run_calls, [])
            self.assertIn("brew uninstall htop", out.getvalue())

            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = u.cmd_remove(argparse.Namespace(packages=["jq"], node=False, bin=True, cask=False, yes=True))
            self.assertEqual(rc2, 0)
            self.assertEqual(run_calls, [["/opt/homebrew/bin/brew", "uninstall", "jq"]])
            self.assertEqual(json.loads(u.STATE_FILE.read_text())["extras"]["brew"], [])


if __name__ == "__main__":
    unittest.main()

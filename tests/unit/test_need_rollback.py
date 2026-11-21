"""Unit tests for complete rollback in `unienv need` (#20)."""

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


class NeedRollbackTests(unittest.TestCase):
    def _setup_home(self, stack, tmp_dir):
        root = pathlib.Path(tmp_dir)
        stack.enter_context(patch.object(u, "UNIENV_HOME", root))
        stack.enter_context(patch.object(u, "PY_DIR", root / "py"))
        stack.enter_context(patch.object(u, "NODE_DIR", root / "node"))
        stack.enter_context(patch.object(u, "STATE_FILE", root / "state.json"))
        stack.enter_context(patch.object(u, "BACKUP_DIR", root / "backups"))
        stack.enter_context(patch.object(u, "LOG_FILE", root / "log" / "unienv.log"))
        return root

    def test_first_node_need_removes_newly_created_files_and_node_modules_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = self._setup_home(stack, tmp)
            # Only state.json exists initially; NODE_DIR has no package.json
            (root / "state.json").write_text(json.dumps(u.default_state({})) + "\n")
            initial_state_bytes = (root / "state.json").read_bytes()

            def fake_install_node(packages, **kwargs):
                u.write_package_json(packages)
                (u.NODE_DIR / "package-lock.json").write_text("{}\n")
                (u.NODE_DIR / "node_modules" / "bad-pkg").mkdir(parents=True)
                raise subprocess.CalledProcessError(1, ["npm", "install"])

            stack.enter_context(patch.object(u, "load_recipes", return_value={}))
            stack.enter_context(patch.object(u, "install_node", side_effect=fake_install_node))

            args = argparse.Namespace(packages=["bad-pkg"], node=True, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_need(args)

            self.assertEqual(rc, 1)
            self.assertIn("rolling back", err.getvalue())
            self.assertEqual((root / "state.json").read_bytes(), initial_state_bytes)
            self.assertFalse((u.NODE_DIR / "package.json").exists())
            self.assertFalse((u.NODE_DIR / "package-lock.json").exists())
            self.assertFalse((u.NODE_DIR / "node_modules").exists())

    def test_existing_node_need_restores_package_json_and_reruns_install_node(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            state = u.default_state({})
            state["extras"]["node"] = ["good-pkg"]
            u.save_state(state)
            u.NODE_DIR.mkdir(parents=True, exist_ok=True)
            orig_pj = '{"name": "unienv-node", "dependencies": {"good-pkg": "^1.0.0"}}\n'
            orig_lock = '{"lockfileVersion": 3}\n'
            (u.NODE_DIR / "package.json").write_text(orig_pj)
            (u.NODE_DIR / "package-lock.json").write_text(orig_lock)

            calls = []

            def fake_install_node(packages, **kwargs):
                calls.append(list(packages))
                if "bad-pkg" in packages:
                    (u.NODE_DIR / "package.json").write_text('{"corrupted": true}\n')
                    (u.NODE_DIR / "package-lock.json").write_text('{"corrupted": true}\n')
                    raise subprocess.CalledProcessError(1, ["npm", "install"])

            stack.enter_context(patch.object(u, "load_recipes", return_value={}))
            stack.enter_context(patch.object(u, "install_node", side_effect=fake_install_node))

            args = argparse.Namespace(packages=["bad-pkg"], node=True, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = u.cmd_need(args)

            self.assertEqual(rc, 1)
            self.assertEqual(calls, [["good-pkg", "bad-pkg"], ["good-pkg"]])
            self.assertEqual((u.NODE_DIR / "package.json").read_text(), orig_pj)
            self.assertEqual((u.NODE_DIR / "package-lock.json").read_text(), orig_lock)

    def test_failing_rollback_reinstall_warns_cleanly_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            u.PY_DIR.mkdir(parents=True, exist_ok=True)
            orig_pyproject = "# original\n"
            (u.PY_DIR / "pyproject.toml").write_text(orig_pyproject)

            def always_fail(packages):
                raise RuntimeError("uv sync failed")

            stack.enter_context(patch.object(u, "load_recipes", return_value={}))
            stack.enter_context(patch.object(u, "install_python", side_effect=always_fail))

            args = argparse.Namespace(packages=["nonexistent-pkg"], node=False, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_need(args)

            self.assertEqual(rc, 1)
            self.assertIn("rollback re-install failed", err.getvalue())
            self.assertIn("run unienv install to repair", err.getvalue())
            self.assertEqual((u.PY_DIR / "pyproject.toml").read_text(), orig_pyproject)

    def test_first_python_need_removes_newly_created_pyproject_and_lock_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)

            def fail_after_writing(packages):
                u.write_pyproject(packages)
                (u.PY_DIR / "uv.lock").write_text("version = 1\n")
                (u.PY_DIR / ".python-version").write_text("3.12\n")
                (u.PY_DIR / ".venv").mkdir()
                (u.PY_DIR / ".venv" / "created").write_text("temporary")
                raise subprocess.CalledProcessError(1, ["uv", "sync"])

            stack.enter_context(patch.object(u, "load_recipes", return_value={}))
            stack.enter_context(patch.object(u, "install_python", side_effect=fail_after_writing))

            args = argparse.Namespace(packages=["nonexistent-pkg"], node=False, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = u.cmd_need(args)

            self.assertEqual(rc, 1)
            self.assertIn("rolling back", err.getvalue())
            self.assertFalse((u.PY_DIR / "pyproject.toml").exists())
            self.assertFalse((u.PY_DIR / "uv.lock").exists())
            self.assertFalse((u.PY_DIR / ".python-version").exists())
            self.assertFalse((u.PY_DIR / ".venv").exists())

    def test_failed_first_project_preserves_preexisting_venv_and_python_version(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            venv = u.PY_DIR / ".venv"
            venv.mkdir(parents=True)
            marker = venv / "keep"
            marker.write_bytes(b"existing interpreter")
            version = u.PY_DIR / ".python-version"
            version.write_bytes(b"3.12.1\n")

            def fail(packages):
                u.write_pyproject(packages)
                version.write_text("3.12.9")
                raise RuntimeError("failed")

            stack.enter_context(patch.object(u, "load_recipes", return_value={}))
            stack.enter_context(patch.object(u, "install_python", side_effect=fail))
            args = argparse.Namespace(packages=["bad-pkg"], node=False, bin=False, cask=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(u.cmd_need(args), 1)
            self.assertEqual(marker.read_bytes(), b"existing interpreter")
            self.assertEqual(version.read_bytes(), b"3.12.1\n")


if __name__ == "__main__":
    unittest.main()

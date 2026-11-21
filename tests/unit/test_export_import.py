"""Unit tests for `unienv export` and `unienv import` (#34)."""

import argparse
import contextlib
import importlib.util
import io
import os
import pathlib
import tempfile
import tomllib
import json
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class ExportImportTests(unittest.TestCase):
    def _setup_home(self, stack, tmp_dir):
        root = pathlib.Path(tmp_dir)
        stack.enter_context(patch.object(u, "HOME", root / "home"))
        stack.enter_context(patch.object(u, "UNIENV_HOME", root))
        stack.enter_context(patch.object(u, "PY_DIR", root / "py"))
        stack.enter_context(patch.object(u, "NODE_DIR", root / "node"))
        stack.enter_context(patch.object(u, "STATE_FILE", root / "state.json"))
        stack.enter_context(patch.object(u, "LOG_FILE", root / "log" / "unienv.log"))
        stack.enter_context(patch.object(u, "BACKUP_DIR", root / "backups"))
        stack.enter_context(patch.object(u, "cmd_doctor", return_value=0))
        return root

    def test_round_trip_export_and_import(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            with contextlib.ExitStack() as stack1:
                self._setup_home(stack1, tmp1)
                recipes = u.load_recipes()
                state1 = u.default_state(recipes)
                state1["batteries"] = ["pdf", "media"]
                state1["options"]["exclude"] = ["libreoffice"]
                state1["extras"] = {
                    "python": ["seaborn>=0.13"],
                    "node": ["chart.js@4.4.0"],
                    "brew": ["jq"],
                    "cask": [],
                    "apt": [],
                }
                u.save_state(state1)
                u.PY_DIR.mkdir(parents=True, exist_ok=True)
                u.NODE_DIR.mkdir(parents=True, exist_ok=True)
                (u.PY_DIR / "uv.lock").write_text("uv-lock-content\n")
                (u.NODE_DIR / "package-lock.json").write_text("npm-lock-content\n")

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc_exp = u.cmd_export(argparse.Namespace())
                self.assertEqual(rc_exp, 0)
                exported_toml = out.getvalue()

            doc = tomllib.loads(exported_toml)
            self.assertEqual(doc["schema"], "unienv/env/v0")
            self.assertEqual(doc["batteries"], ["pdf", "media"])
            self.assertEqual(doc["options"]["exclude"], ["libreoffice"])
            self.assertEqual(doc["extras"]["python"], ["seaborn>=0.13"])
            self.assertEqual(doc["extras"]["node"], ["chart.js@4.4.0"])
            self.assertEqual(doc["extras"]["brew"], ["jq"])
            self.assertNotIn("locks", doc)

            with contextlib.ExitStack() as stack2:
                root2 = self._setup_home(stack2, tmp2)
                env_file = root2 / "unienv-env.toml"
                env_file.write_text(exported_toml)

                py_installed = []
                node_installed = []
                stack2.enter_context(patch.object(u, "install_system", return_value=[]))
                stack2.enter_context(
                    patch.object(u, "install_python", side_effect=lambda pkgs: py_installed.extend(pkgs))
                )
                stack2.enter_context(
                    patch.object(u, "install_node", side_effect=lambda pkgs: node_installed.extend(pkgs))
                )
                stack2.enter_context(patch.object(u, "do_sync", return_value={}))

                with contextlib.redirect_stdout(io.StringIO()):
                    rc_imp = u.cmd_import(
                        argparse.Namespace(file=str(env_file), no_system=False, no_wire=True, yes=True)
                    )
                self.assertEqual(rc_imp, 0)

                state2 = u.load_state(u.load_recipes())
                self.assertEqual(state2["batteries"], ["pdf", "media"])
                self.assertEqual(state2["options"]["exclude"], ["libreoffice"])
                self.assertEqual(state2["extras"]["python"], ["seaborn>=0.13"])
                self.assertEqual(state2["extras"]["node"], ["chart.js@4.4.0"])
                self.assertEqual(state2["extras"]["brew"], ["jq"])
                self.assertIn("seaborn>=0.13", py_installed)
                self.assertIn("chart.js@4.4.0", node_installed)

    def test_export_excludes_local_paths_and_env_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = self._setup_home(stack, tmp)
            stack.enter_context(
                patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-token-12345", "UNIENV_HOME": str(root)})
            )
            recipes = u.load_recipes()
            state = u.default_state(recipes)
            u.save_state(state)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                u.cmd_export(argparse.Namespace())
            text = out.getvalue()

            self.assertNotIn(str(root), text)
            self.assertNotIn(str(u.HOME), text)
            self.assertNotIn("sk-secret-token-12345", text)
            self.assertNotIn("OPENAI_API_KEY", text)

    def test_unsafe_specs_rejected_before_install(self):
        for kind, value in (
            ("apt", "-oAPT::Update::Pre-Invoke::=evil"),
            ("python", "https://example.invalid/x.whl"),
            ("node", "--ignore-scripts"),
            ("brew", "../outside"),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                root = self._setup_home(stack, tmp)
                file = root / "import.toml"
                file.write_text('schema="unienv/env/v0"\nbatteries=[]\n[extras]\n' + kind + "=" + json.dumps([value]))
                install = stack.enter_context(patch.object(u, "cmd_install"))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    u.cmd_import(argparse.Namespace(file=str(file), yes=True, no_system=True, no_wire=True))
                install.assert_not_called()
                self.assertFalse(u.STATE_FILE.exists())

    def test_declined_import_leaves_state_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = self._setup_home(stack, tmp)
            file = root / "import.toml"
            file.write_text('schema="unienv/env/v0"\nbatteries=[]\n')
            install = stack.enter_context(patch.object(u, "cmd_install"))
            stack.enter_context(patch("builtins.input", return_value="n"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    u.cmd_import(argparse.Namespace(file=str(file), yes=False, no_system=True, no_wire=True)), 1
                )
            install.assert_not_called()
            self.assertFalse(u.STATE_FILE.exists())

    def test_failures_and_interrupts_restore_state_and_project_files(self):
        for failure in (RuntimeError("failed"), KeyboardInterrupt(), SystemExit(2)):
            with (
                self.subTest(failure=type(failure).__name__),
                tempfile.TemporaryDirectory() as tmp,
                contextlib.ExitStack() as stack,
            ):
                root = self._setup_home(stack, tmp)
                file = root / "import.toml"
                file.write_text('schema="unienv/env/v0"\nbatteries=[]\n[extras]\npython=["bad-pkg"]\n')
                u.save_state(u.default_state(u.load_recipes()))
                initial = u.STATE_FILE.read_bytes()
                u.PY_DIR.mkdir()
                project = u.PY_DIR / "pyproject.toml"
                project.write_bytes(b"original\n")

                def fail(args):
                    project.write_bytes(b"changed")
                    (u.PY_DIR / ".venv").mkdir()
                    (u.PY_DIR / ".python-version").write_text("changed")
                    raise failure

                stack.enter_context(patch.object(u, "cmd_install", side_effect=fail))
                stack.enter_context(patch.object(u, "install_python"))
                stack.enter_context(patch.object(u, "do_sync"))
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        u.cmd_import(argparse.Namespace(file=str(file), yes=True, no_system=True, no_wire=True)), 1
                    )
                self.assertEqual(u.STATE_FILE.read_bytes(), initial)
                self.assertEqual(project.read_bytes(), b"original\n")
                self.assertFalse((u.PY_DIR / ".venv").exists())
                self.assertFalse((u.PY_DIR / ".python-version").exists())

    def test_import_from_stdin_and_validation_errors(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            self._setup_home(stack, tmp)
            stack.enter_context(patch.object(u, "install_system", return_value=[]))
            stack.enter_context(patch.object(u, "install_python", return_value=None))
            stack.enter_context(patch.object(u, "install_node", return_value=None))
            stack.enter_context(patch.object(u, "do_sync", return_value={}))

            valid_toml = (
                'schema = "unienv/env/v0"\nbatteries = ["pdf"]\n[options]\nexclude = []\n[extras]\npython = ["httpx"]\n'
            )
            stack.enter_context(patch.object(u.sys, "stdin", io.StringIO(valid_toml)))
            with contextlib.redirect_stdout(io.StringIO()):
                rc = u.cmd_import(argparse.Namespace(file="-", no_system=True, no_wire=True, yes=True))
            self.assertEqual(rc, 0)
            self.assertEqual(u.load_state(u.load_recipes())["extras"]["python"], ["httpx"])

            bad_toml = 'schema = "wrong/v0"\nbatteries = ["nonexistent_battery"]\n'
            stack.enter_context(patch.object(u.sys, "stdin", io.StringIO(bad_toml)))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit):
                    u.cmd_import(argparse.Namespace(file="-", no_system=True, no_wire=True, yes=True))
            self.assertIn("invalid environment file", err.getvalue())


if __name__ == "__main__":
    unittest.main()

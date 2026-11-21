#!/usr/bin/env python3
"""Unit tests for Issue #64: `unienv need` verifies version specifiers and extras on installed packages."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
import unienv  # noqa: E402


class SpecSatisfiedUnitTest(unittest.TestCase):
    def test_conservative_unknowns_and_npm_partial_versions(self):
        for spec in ("docx@9", "docx@9.x", "docx@^8 || ^9", "docx@next", "docx@<=9", "docx@>9", "docx@=9", "docx@>=8 <=9"):
            with self.subTest(spec=spec):
                self.assertIsNot(unienv._spec_satisfied("node", spec, {"docx": "9.5.0"})[0], False)
        self.assertIsNone(unienv._spec_satisfied("node", "docx@<=9", {"docx": "9.5.0"})[0])
        self.assertIsNone(unienv._spec_satisfied("node", "docx@>9", {"docx": "9.5.0"})[0])
        self.assertIsNone(unienv._spec_satisfied("node", "docx@9", {"docx": "9.5.0-beta.1"})[0])
        self.assertTrue(unienv._spec_satisfied("node", "docx@~1.2", {"docx": "1.2.8"})[0])
        self.assertFalse(unienv._spec_satisfied("node", "docx@~1.2", {"docx": "1.3.0"})[0])
        self.assertIsNone(unienv._spec_satisfied("python", "pandas>=2.0", {"pandas": "2.0.0rc1"})[0])

    def test_python_bare_name_always_satisfied(self):
        ok, ver = unienv._spec_satisfied("python", "pandas", {"pandas": "2.2.3"})
        self.assertTrue(ok)
        self.assertEqual(ver, "2.2.3")

    def test_python_version_comparisons(self):
        pkgs = {"pandas": "2.2.3"}
        self.assertTrue(unienv._spec_satisfied("python", "pandas>=2.2", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("python", "pandas>=2.2,<3.0", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("python", "pandas==2.2.3", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("python", "pandas==2.2.*", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("python", "pandas~=2.2.0", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("python", "pandas!=2.1.0", pkgs)[0])

        self.assertFalse(unienv._spec_satisfied("python", "pandas>=99", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("python", "pandas<2.0", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("python", "pandas==2.1.*", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("python", "pandas~=2.3.0", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("python", "pandas!=2.2.3", pkgs)[0])

    def test_python_extras_check(self):
        pkgs = {"markitdown": "0.1.1"}
        configured = ["markitdown[docx,pptx,xlsx]>=0.1", "markitdown[pdf]>=0.1"]
        self.assertTrue(unienv._spec_satisfied("python", "markitdown[docx]>=0.1", pkgs, configured)[0])
        self.assertTrue(unienv._spec_satisfied("python", "markitdown[pdf,xlsx]", pkgs, configured)[0])
        self.assertIsNone(unienv._spec_satisfied("python", "markitdown[audio]>=0.1", pkgs, configured)[0])

    def test_node_version_comparisons(self):
        pkgs = {"docx": "8.5.0", "@scope/pkg": "1.2.3"}
        self.assertTrue(unienv._spec_satisfied("node", "docx", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("node", "docx@8.5.0", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("node", "docx@^8.0.0", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("node", "docx@>=8.0.0", pkgs)[0])
        self.assertTrue(unienv._spec_satisfied("node", "@scope/pkg@^1.2.0", pkgs)[0])

        self.assertFalse(unienv._spec_satisfied("node", "docx@1.0.0", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("node", "docx@^9.0.0", pkgs)[0])
        self.assertFalse(unienv._spec_satisfied("node", "@scope/pkg@~1.3.0", pkgs)[0])


class CmdNeedVersionCheckTest(unittest.TestCase):
    def _run_need(self, packages, *, node=False, py_installed=None, node_installed=None):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state_file = home / "state.json"
            manifest_file = home / "manifest.json"
            state = {
                "version": unienv.VERSION,
                "batteries": ["data", "office"],
                "extras": {"python": [], "node": [], "brew": [], "cask": [], "apt": []},
                "options": {"wire_homebrew": True, "exclude": []},
            }
            state_file.write_text(json.dumps(state, indent=2) + "\n")
            manifest = {
                "python": {"packages": py_installed or {"pandas": "2.2.3", "markitdown": "0.1.1"}},
                "node": {"packages": node_installed or {"docx": "8.5.0"}},
            }
            manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")

            args = argparse.Namespace(packages=packages, node=node, bin=False, cask=False, yes=False)
            out = io.StringIO()
            err = io.StringIO()
            with (
                mock.patch.object(unienv, "UNIENV_HOME", home),
                mock.patch.object(unienv, "STATE_FILE", state_file),
                mock.patch.object(unienv, "MANIFEST_FILE", manifest_file),
                mock.patch.object(unienv, "PY_DIR", home / "py"),
                mock.patch.object(unienv, "NODE_DIR", home / "node"),
                mock.patch.object(unienv, "python_packages", return_value=manifest["python"]["packages"]),
                mock.patch.object(unienv, "node_packages", return_value=manifest["node"]["packages"]),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                rc = unienv.cmd_need(args)
            return rc, out.getvalue(), err.getvalue()

    def test_bare_names_do_not_start_installed_version_lookup(self):
        with mock.patch.object(unienv, "_installed_versions", side_effect=AssertionError("unexpected lookup")):
            rc, _, _ = self._run_need(["pandas"])
            self.assertEqual(rc, 0)

    def test_unsatisfied_python_version_exits_nonzero_without_checkmark(self):
        rc, out, err = self._run_need(["pandas>=99"])
        self.assertEqual(rc, 1)
        self.assertNotIn("✓", out)
        self.assertIn("pandas 2.2.3 is installed; `pandas>=99` isn't satisfied.", err)
        self.assertIn("unienv upgrade", err)

    def test_satisfied_python_version_prints_already_available(self):
        rc, out, err = self._run_need(["pandas>=2.2"])
        self.assertEqual(rc, 0)
        self.assertIn("✓ already available: pandas>=2.2", out)
        self.assertEqual(err, "")

    def test_unsatisfied_node_version_exits_nonzero_without_checkmark(self):
        rc, out, err = self._run_need(["docx@1.0.0"], node=True)
        self.assertEqual(rc, 1)
        self.assertNotIn("✓", out)
        self.assertIn("docx 8.5.0 is installed; `docx@1.0.0` isn't satisfied.", err)

    def test_satisfied_node_version_prints_already_available(self):
        rc, out, err = self._run_need(["docx@8.5.0"], node=True)
        self.assertEqual(rc, 0)
        self.assertIn("✓ already available: docx@8.5.0", out)
        self.assertEqual(err, "")

    def test_unsatisfied_python_extra_exits_nonzero(self):
        rc, out, err = self._run_need(["markitdown[audio]>=0.1"])
        self.assertEqual(rc, 0)
        self.assertIn("✓", out)
        self.assertNotIn("markitdown 0.1.1 is installed; `markitdown[audio]>=0.1` isn't satisfied.", err)


if __name__ == "__main__":
    unittest.main()

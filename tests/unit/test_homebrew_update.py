"""Unit tests for #39 (Homebrew formula and Cellar detection in `unienv update`)."""

import argparse
import contextlib
import importlib.util
import io
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class HomebrewUpdateTests(unittest.TestCase):
    def test_is_homebrew_install_detects_cellar_path(self):
        with patch.object(u, "__file__", "/opt/homebrew/Cellar/unienv/0.2.2/libexec/src/unienv.py"):
            self.assertTrue(u._is_homebrew_install())
        with patch.object(u, "__file__", "/Users/alice/.unienv/share/src/unienv.py"):
            self.assertFalse(u._is_homebrew_install())

    def test_cmd_update_recommends_brew_upgrade_when_in_cellar(self):
        out = io.StringIO()
        with (
            patch.object(u, "__file__", "/opt/homebrew/Cellar/unienv/0.2.2/libexec/src/unienv.py"),
            contextlib.redirect_stdout(out),
        ):
            rc = u.cmd_update(argparse.Namespace(ref="main", no_wire=False))
        self.assertEqual(rc, 0)
        self.assertIn("UniEnv was installed via Homebrew; update with: brew upgrade unienv", out.getvalue())

    def test_formula_template_exists_and_has_required_stanzas(self):
        formula = (ROOT / "Formula" / "unienv.rb").read_text()
        self.assertIn("class Unienv < Formula", formula)
        self.assertIn('depends_on "uv"', formula)
        self.assertIn('libexec.install "src", "recipes", "templates", "shims"', formula)
        self.assertIn("unienv install", formula)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import pathlib
import tempfile
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class HelperTests(unittest.TestCase):
    def test_package_names(self):
        self.assertEqual(u.py_name('Some_Pkg[extra]>=1; python_version>"3"'), "some-pkg")
        for spec, name, version in [
            ("@scope/pkg@2", "@scope/pkg", "2"),
            ("pkg@latest", "pkg", "latest"),
            ("pkg", "pkg", "latest"),
        ]:
            with self.subTest(spec=spec):
                self.assertEqual(u.node_name(spec), name)
                self.assertEqual(u.node_version(spec), version)

    def test_block_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config"
            path.write_text("existing")
            self.assertTrue(u.upsert_block(path, "first", "# BEGIN", "# END"))
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "first")
            self.assertFalse(u.upsert_block(path, "first", "# BEGIN", "# END"))
            self.assertTrue(u.upsert_block(path, "second", "# BEGIN", "# END"))
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "second")
            self.assertTrue(u.remove_block(path, "# BEGIN", "# END"))
            self.assertEqual(path.read_bytes(), b"existing")
            self.assertIsNone(u.read_block(path, "# BEGIN", "# END"))

    def test_missing_end_marker_is_not_read_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config"
            content = "before\n# BEGIN\nunfinished\n"
            path.write_text(content)
            self.assertIsNone(u.read_block(path, "# BEGIN", "# END"))
            self.assertFalse(u.remove_block(path, "# BEGIN", "# END"))
            self.assertEqual(path.read_text(), content)

    def test_orphaned_begin_marker_does_not_delete_user_lines_Across_two_syncs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".zshenv"
            initial = "export A=1\n# >>> unienv >>>\nsource ~/.unienv/env.sh\nalias gs='git status'\nexport B=2\n"
            path.write_text(initial)
            self.assertTrue(u._has_malformed_block(initial, u.SHELL_BEGIN, u.SHELL_END))
            self.assertFalse(u.upsert_block(path, "source ~/.unienv/env.sh", u.SHELL_BEGIN, u.SHELL_END))
            self.assertEqual(path.read_text(), initial)
            self.assertFalse(u.upsert_block(path, "source ~/.unienv/env.sh  # v2", u.SHELL_BEGIN, u.SHELL_END))
            self.assertEqual(path.read_text(), initial)
            self.assertIn("alias gs='git status'", path.read_text())
            self.assertIn("export B=2", path.read_text())
            self.assertFalse(u.remove_block(path, u.SHELL_BEGIN, u.SHELL_END))
            self.assertEqual(path.read_text(), initial)

    def test_whitespace_orphan_markers_preserve_bytes_across_two_syncs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".zshenv"
            for marker in (u.SHELL_BEGIN + " ", "\t" + u.SHELL_BEGIN + "\t", u.SHELL_END + " "):
                with self.subTest(marker=marker):
                    initial = ("export A=1\r\n" + marker + "\r\nalias gs='git status'\r\n").encode()
                    path.write_bytes(initial)
                    for body in ("first", "second"):
                        self.assertFalse(u.upsert_block(path, body, u.SHELL_BEGIN, u.SHELL_END))
                        self.assertEqual(path.read_bytes(), initial)
                    self.assertFalse(u.remove_block(path, u.SHELL_BEGIN, u.SHELL_END))
                    self.assertEqual(path.read_bytes(), initial)

    def test_malformed_block_variants_detected_and_reported_by_doctor(self):
        import argparse
        import io
        import json
        from contextlib import redirect_stdout
        from unittest.mock import patch

        self.assertTrue(u._has_malformed_block("# END\n# BEGIN\nx\n# END\n", "# BEGIN", "# END"))
        self.assertTrue(u._has_malformed_block("# BEGIN\nx\n# BEGIN\ny\n# END\n", "# BEGIN", "# END"))
        self.assertFalse(u._has_malformed_block("a\n# BEGIN\nx\n# END\nb\n", "# BEGIN", "# END"))

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            ue_home = home / ".unienv"
            ue_home.mkdir()
            zshenv = home / ".zshenv"
            zshenv.write_text(f"export A=1\n{u.SHELL_BEGIN}\nsource ~/.unienv/env.sh\nalias gs='git status'\n")
            with (
                patch.object(u, "HOME", home),
                patch.object(u, "UNIENV_HOME", ue_home),
                patch.object(u, "SHELL_FILES", [zshenv]),
                patch.object(u, "MANIFEST_FILE", ue_home / "manifest.json"),
                patch.object(u, "PY_DIR", ue_home / "py"),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))
                report = json.loads(buf.getvalue())
                block_checks = [c for c in report["checks"] if c["id"] == "block:.zshenv"]
                self.assertEqual(len(block_checks), 1)
                self.assertEqual(block_checks[0]["status"], "fail")

    def test_crlf_block_can_be_read_replaced_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config"
            path.write_bytes(b"before\r\n# BEGIN\r\nold\r\n# END\r\nafter\r\n")
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "old")
            self.assertTrue(u.upsert_block(path, "new", "# BEGIN", "# END"))
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "new")
            self.assertTrue(u.remove_block(path, "# BEGIN", "# END"))
            self.assertEqual(path.read_text(), "before\nafter\n")

    def test_block_operations_preserve_file_line_endings(self):
        for eol in (b"\n", b"\r\n", b"\r"):
            with self.subTest(eol=eol):
                with tempfile.TemporaryDirectory() as tmp:
                    path = pathlib.Path(tmp) / "config"
                    path.write_bytes(b"# My rules" + eol + b"be nice" + eol)

                    self.assertTrue(u.upsert_block(path, "unienv stuff", "# BEGIN", "# END"))
                    self.assertEqual(
                        path.read_bytes(),
                        eol.join(
                            [
                                b"# My rules",
                                b"be nice",
                                b"# BEGIN",
                                b"unienv stuff",
                                b"# END",
                                b"",
                            ]
                        ),
                    )

                    self.assertTrue(u.remove_block(path, "# BEGIN", "# END"))
                    self.assertEqual(path.read_bytes(), b"# My rules" + eol + b"be nice" + eol)

    def test_missing_file_and_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "nested" / "config"
            self.assertIsNone(u.read_block(path, "# BEGIN", "# END"))
            self.assertFalse(u.remove_block(path, "# BEGIN", "# END"))
            self.assertTrue(u.upsert_block(path, "text", "# BEGIN", "# END", top=True))
            self.assertEqual(path.read_text(), "# BEGIN\ntext\n# END\n")

    def test_all_recipes_validate(self):
        recipes = list((ROOT / "recipes").glob("*.toml"))
        self.assertTrue(recipes)
        for path in recipes:
            with self.subTest(recipe=path.name):
                with path.open("rb") as f:
                    self.assertEqual(u.validate_recipe(tomllib.load(f), path.stem), [])

    def test_browser_recipe_and_env_sh_export_playwright_browsers_path(self):
        from unittest.mock import patch

        path = ROOT / "recipes" / "browser.toml"
        self.assertTrue(path.exists())
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "#:schema ../schema/recipe.schema.json")
        with path.open("rb") as f:
            recipe = tomllib.load(f)
        self.assertEqual(u.validate_recipe(recipe, "browser"), [])
        self.assertEqual(recipe["id"], "browser")
        self.assertIs(recipe.get("default"), False)
        self.assertEqual(recipe["python"]["packages"], ["playwright"])
        self.assertEqual(recipe["python"]["imports"], ["playwright"])
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", recipe["agent"]["notes"])
        self.assertIn("--no-sandbox", recipe["agent"]["notes"])

        with tempfile.TemporaryDirectory() as tmp:
            unienv_home = pathlib.Path(tmp) / ".unienv"
            unienv_home.mkdir(parents=True, exist_ok=True)
            env_sh = unienv_home / "env.sh"
            state = {"batteries": ["browser"], "options": {"wire_homebrew": False}}
            with patch.object(u, "UNIENV_HOME", unienv_home), patch.object(u, "ENV_SH", env_sh):
                u.write_env_sh(state)
            text = env_sh.read_text(encoding="utf-8")
            self.assertIn("PLAYWRIGHT_BROWSERS_PATH", text)
            self.assertIn("$UNIENV_HOME/browsers", text)
            self.assertNotIn("$UNIENV_HOME/share/browsers", text)

            # When `browser` battery is not active, PLAYWRIGHT_BROWSERS_PATH is not exported
            inactive_state = {"batteries": ["office"], "options": {"wire_homebrew": False}}
            with patch.object(u, "UNIENV_HOME", unienv_home), patch.object(u, "ENV_SH", env_sh):
                u.write_env_sh(inactive_state)
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", env_sh.read_text(encoding="utf-8"))

    def test_excluded_system_packages_and_bins(self):
        from unittest.mock import patch

        recipe = {
            "system": {"debian": {"apt": ["libreoffice", "libreoffice-writer", "curl"]}},
            "verify": {"bins": ["soffice", "curl"], "provided_by": {"soffice": "libreoffice"}},
        }
        state = {
            "options": {"exclude": ["libreoffice"]},
            "extras": {k: [] for k in ("python", "node", "brew", "cask", "apt")},
        }
        with patch.object(u, "platform_key", return_value="debian"):
            result = u.wanted([recipe], state)
        self.assertEqual(result["apt"], ["curl"])
        self.assertEqual(result["bins"], ["curl"])
        self.assertEqual(result["skipped_bins"], ["soffice"])


if __name__ == "__main__":
    unittest.main()

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
            self.assertEqual(path.read_text(), "existing\n")
            self.assertIsNone(u.read_block(path, "# BEGIN", "# END"))

    def test_missing_end_marker_is_not_read_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config"
            content = "before\n# BEGIN\nunfinished\n"
            path.write_text(content)
            self.assertIsNone(u.read_block(path, "# BEGIN", "# END"))
            self.assertFalse(u.remove_block(path, "# BEGIN", "# END"))
            self.assertEqual(path.read_text(), content)

    def test_crlf_block_can_be_read_replaced_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config"
            path.write_bytes(b"before\r\n# BEGIN\r\nold\r\n# END\r\nafter\r\n")
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "old")
            self.assertTrue(u.upsert_block(path, "new", "# BEGIN", "# END"))
            self.assertEqual(u.read_block(path, "# BEGIN", "# END"), "new")
            self.assertTrue(u.remove_block(path, "# BEGIN", "# END"))
            self.assertEqual(path.read_text(), "before\nafter\n")

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

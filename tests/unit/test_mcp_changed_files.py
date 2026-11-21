import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
MCP_PATH = ROOT / "src" / "unienv_mcp.py"
spec = importlib.util.spec_from_file_location("unienv_mcp", MCP_PATH)
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


class ChangedFilesSubfolderTests(unittest.TestCase):
    def test_changed_files_reports_nested_subfolders_up_to_depth_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            (work / "existing.txt").write_text("before")
            before = mcp.snapshot(work)

            (work / "sub").mkdir()
            (work / "sub" / "x.txt").write_text("hello")
            (work / "a" / "b" / "c").mkdir(parents=True)
            (work / "a" / "b" / "c" / "deep3.txt").write_text("depth 3")
            (work / "a" / "b" / "c" / "d").mkdir(parents=True)
            (work / "a" / "b" / "c" / "d" / "deep4.txt").write_text("depth 4")

            changed = mcp.changed_files(work, before)
            self.assertEqual(
                changed,
                [
                    str(work / "a" / "b" / "c" / "deep3.txt"),
                    str(work / "sub" / "x.txt"),
                ],
            )

    def test_snapshot_skips_git_node_modules_venv_pycache_and_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            before = mcp.snapshot(work)

            for skipped in (".git", "node_modules", ".venv", "__pycache__", ".hidden_dir"):
                d = work / skipped / "inner"
                d.mkdir(parents=True)
                (d / "ignored.txt").write_text("skip me")
            (work / ".DS_Store").write_text("skip hidden file")
            (work / "out").mkdir()
            (work / "out" / "report.docx").write_text("keep me")

            changed = mcp.changed_files(work, before)
            self.assertEqual(changed, [str(work / "out" / "report.docx")])

    def test_snapshot_caps_directories_even_when_they_contain_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            for i in range(20):
                (work / f"dir{i:02}").mkdir()
            opened = []
            original = pathlib.Path.iterdir

            def tracked(path):
                opened.append(path)
                return original(path)

            with patch.object(mcp, "_SNAPSHOT_MAX_DIRS", 5), patch.object(pathlib.Path, "iterdir", tracked):
                self.assertEqual(mcp.snapshot(work), {})
            self.assertEqual(len(opened), 5)

    def test_t_run_python_reports_file_created_in_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            ue_py = bin_dir / "ue-py"
            ue_py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
            ue_py.chmod(0o755)
            code = (
                "import pathlib\n"
                "p = pathlib.Path('sub/x.txt')\n"
                "p.parent.mkdir(parents=True, exist_ok=True)\n"
                "p.write_text('created in subfolder')\n"
            )
            with patch.object(mcp, "BIN", bin_dir), patch.object(mcp, "WORKDIR", work):
                report = mcp.t_run_python({"code": code, "cwd": str(work)})
            self.assertIn("files created/modified:", report)
            self.assertIn("sub/x.txt", report)


if __name__ == "__main__":
    unittest.main()

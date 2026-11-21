"""Unit tests for #66 (safe tar extraction on Python 3.11.0-3.11.3 and 3.11.4+)."""

import importlib.util
import io
import pathlib
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def _build_tar(entries: list[tuple[str, bytes | None, int, str]]) -> tarfile.TarFile:
    """Build an in-memory TarFile from (name, content, type, linkname) tuples."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, ttype, linkname in entries:
            info = tarfile.TarInfo(name=name)
            info.type = ttype
            if ttype == tarfile.DIRTYPE:
                info.mode = 0o755
            if linkname:
                info.linkname = linkname
            if data is not None:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.size = 0
                tar.addfile(info)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:gz")


class SafeTarExtractTests(unittest.TestCase):
    def test_normal_extraction_with_data_filter(self):
        with (
            _build_tar([("unienv-ai-main/src/unienv.py", b"print('ok')\n", tarfile.REGTYPE, "")]) as tar,
            tempfile.TemporaryDirectory() as tmp,
        ):
            dest = pathlib.Path(tmp)
            u._safe_extract_tar(tar, dest)
            self.assertEqual((dest / "unienv-ai-main/src/unienv.py").read_text(), "print('ok')\n")

    def test_fallback_extracts_only_regular_files_and_directories(self):
        entries = [
            ("unienv-ai-main", None, tarfile.DIRTYPE, ""),
            ("unienv-ai-main/unienv.py", b"print('ok')", tarfile.REGTYPE, ""),
        ]
        with _build_tar(entries) as tar, tempfile.TemporaryDirectory() as tmp:
            with patch.object(tarfile, "data_filter", None):
                u._safe_extract_tar(tar, pathlib.Path(tmp))
            self.assertEqual((pathlib.Path(tmp) / "unienv-ai-main/unienv.py").read_bytes(), b"print('ok')")

    def test_fallback_sanitizes_directory_and_file_modes(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            d = tarfile.TarInfo(name="unienv-ai-main")
            d.type = tarfile.DIRTYPE
            d.mode = 0o644  # missing execute bit in archive
            tar.addfile(d)
            f = tarfile.TarInfo(name="unienv-ai-main/script.sh")
            f.type = tarfile.REGTYPE
            f.mode = 0o4777  # setuid + world writable
            data = b"#!/bin/sh\n"
            f.size = len(data)
            tar.addfile(f, io.BytesIO(data))
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp)
            with patch.object(tarfile, "data_filter", None):
                u._safe_extract_tar(tar, dest)
            self.assertEqual((dest / "unienv-ai-main/script.sh").read_bytes(), b"#!/bin/sh\n")
            self.assertEqual((dest / "unienv-ai-main").stat().st_mode & 0o7777, 0o755)
            self.assertEqual((dest / "unienv-ai-main/script.sh").stat().st_mode & 0o7777, 0o755)

    def test_fallback_rejects_chained_symlink_escape_before_extracting(self):
        entries = [
            ("t", None, tarfile.SYMTYPE, "."),
            ("s", None, tarfile.SYMTYPE, "t/.."),
            ("s/evil", b"escaped", tarfile.REGTYPE, ""),
        ]
        with tempfile.TemporaryDirectory() as tmp, _build_tar(entries) as tar:
            dest = pathlib.Path(tmp) / "dest"
            dest.mkdir()
            with patch.object(tarfile, "data_filter", None):
                with self.assertRaises(ValueError):
                    u._safe_extract_tar(tar, dest)
            self.assertEqual(list(dest.iterdir()), [])
            self.assertFalse((pathlib.Path(tmp) / "evil").exists())

    def test_fallback_rejects_even_internal_links(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                with _build_tar([("file", b"ok", tarfile.REGTYPE, ""), ("link", None, kind, "file")]) as tar:
                    with patch.object(tarfile, "data_filter", None), self.assertRaises(ValueError):
                        u._safe_extract_tar(tar, pathlib.Path(tmp))
                self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_fallback_rejects_parent_traversal(self):
        with (
            _build_tar([("../evil.txt", b"pwn", tarfile.REGTYPE, "")]) as tar,
            tempfile.TemporaryDirectory() as tmp,
            patch.object(tarfile, "data_filter", None),
        ):
            delattr(tarfile, "data_filter")
            with self.assertRaises(ValueError):
                u._safe_extract_tar(tar, pathlib.Path(tmp))

    def test_fallback_rejects_absolute_path(self):
        with (
            _build_tar([("/abs/path.txt", b"pwn", tarfile.REGTYPE, "")]) as tar,
            tempfile.TemporaryDirectory() as tmp,
            patch.object(tarfile, "data_filter", None),
        ):
            delattr(tarfile, "data_filter")
            with self.assertRaises(ValueError):
                u._safe_extract_tar(tar, pathlib.Path(tmp))

    def test_fallback_rejects_outside_symlink(self):
        with (
            _build_tar([("unienv-ai-main/escape", None, tarfile.SYMTYPE, "../../../etc/passwd")]) as tar,
            tempfile.TemporaryDirectory() as tmp,
            patch.object(tarfile, "data_filter", None),
        ):
            delattr(tarfile, "data_filter")
            with self.assertRaises(ValueError):
                u._safe_extract_tar(tar, pathlib.Path(tmp))

    def test_fallback_rejects_fifo_and_devices(self):
        with (
            _build_tar([("unienv-ai-main/fifo", None, tarfile.FIFOTYPE, "")]) as tar,
            tempfile.TemporaryDirectory() as tmp,
            patch.object(tarfile, "data_filter", None),
        ):
            delattr(tarfile, "data_filter")
            with self.assertRaises(ValueError):
                u._safe_extract_tar(tar, pathlib.Path(tmp))


if __name__ == "__main__":
    unittest.main()

"""Unit tests for #28 (`unienv update` release tag resolution, `--check`, SHA256SUMS verification, and `share.prev` rollback)."""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def _make_tarball_bytes(version: str = "0.3.0") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for part, filename, content in (
            ("src", "unienv.py", f'VERSION = "{version}"\n'),
            ("recipes", "core.toml", 'schema = "unienv/recipe/v0"\nid = "core"\nsummary = "c"\n'),
            ("templates", "SKILL.md", "# Skill\n"),
            ("shims", "ue-py", "#!/bin/sh\n"),
        ):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"unienv-ai-{version}/{part}/{filename}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class UpdateReleaseTests(unittest.TestCase):
    def test_update_check_reports_newer_version(self):
        out = io.StringIO()
        with (
            patch.object(u, "VERSION", "0.2.2"),
            patch.object(u, "_latest_release_tag", return_value="v0.3.0"),
            contextlib.redirect_stdout(out),
        ):
            rc = u.cmd_update(argparse.Namespace(check=True, ref=None, sha256=None, no_wire=True))
        self.assertEqual(rc, 0)
        self.assertIn("newer version is available: v0.3.0", out.getvalue())

    def test_update_check_reports_up_to_date(self):
        out = io.StringIO()
        with (
            patch.object(u, "VERSION", "0.2.2"),
            patch.object(u, "_latest_release_tag", return_value="v0.2.2"),
            contextlib.redirect_stdout(out),
        ):
            rc = u.cmd_update(argparse.Namespace(check=True, ref=None, sha256=None, no_wire=True))
        self.assertEqual(rc, 0)
        self.assertIn("up to date", out.getvalue())

    def test_update_defaults_to_latest_release_and_verifies_sha256(self):
        tar_bytes = _make_tarball_bytes("0.3.0")
        sha = hashlib.sha256(tar_bytes).hexdigest()

        @contextlib.contextmanager
        def fake_urlopen(req_or_url, *args, **kwargs):
            url = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url)
            if url == u.LATEST_RELEASE_API:
                yield io.BytesIO(json.dumps({"tag_name": "v0.3.0"}).encode("utf-8"))
            elif "SHA256SUMS" in url:
                yield io.BytesIO(f"{'1' * 64}  unienv.mcpb\n{sha}  unienv-ai-v0.3.0.tar.gz\n".encode("utf-8"))
            else:
                yield io.BytesIO(tar_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=fake_urlopen),
                patch("subprocess.call", return_value=0),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = u.cmd_update(argparse.Namespace(check=False, ref=None, sha256=None, no_wire=True))
            self.assertEqual(rc, 0)
            self.assertIn('VERSION = "0.3.0"', (home / "share/src/unienv.py").read_text())
            self.assertFalse((home / "share.prev").exists())

    def test_update_refuses_mismatched_checksum(self):
        tar_bytes = _make_tarball_bytes("0.3.0")
        wrong_sha = "0" * 64

        @contextlib.contextmanager
        def fake_urlopen(req_or_url, *args, **kwargs):
            yield io.BytesIO(tar_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            err = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=fake_urlopen),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
            ):
                with self.assertRaises(SystemExit) as cm:
                    u.cmd_update(argparse.Namespace(check=False, ref="v0.3.0", sha256=wrong_sha, no_wire=True))
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("SHA-256 mismatch", err.getvalue())

    def test_update_rolls_back_share_prev_when_install_fails(self):
        tar_bytes = _make_tarball_bytes("0.3.0")
        sha = hashlib.sha256(tar_bytes).hexdigest()

        @contextlib.contextmanager
        def fake_urlopen(req_or_url, *args, **kwargs):
            yield io.BytesIO(tar_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            old_src = home / "share" / "src"
            old_src.mkdir(parents=True)
            (old_src / "unienv.py").write_text('VERSION = "0.2.2"\n')
            err = io.StringIO()
            calls = []

            def fake_call(cmd):
                calls.append(cmd)
                return 1 if len(calls) == 1 else 0

            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=fake_urlopen),
                patch("subprocess.call", side_effect=fake_call),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
            ):
                rc = u.cmd_update(argparse.Namespace(check=False, ref="v0.3.0", sha256=sha, no_wire=True))
            self.assertEqual(rc, 1)
            self.assertEqual((home / "share/src/unienv.py").read_text(), 'VERSION = "0.2.2"\n')
            self.assertEqual(len(calls), 2)
            self.assertIn("restoring previous share", err.getvalue())

    def test_update_fails_closed_when_latest_release_or_sha256sums_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            err = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(u, "_latest_release_tag", return_value=None),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
            ):
                with self.assertRaises(SystemExit) as cm:
                    u.cmd_update(argparse.Namespace(check=False, ref=None, sha256=None, no_wire=True))
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("--ref main", err.getvalue())

            err2 = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(u, "_latest_release_tag", return_value="v0.3.0"),
                patch(
                    "urllib.request.urlopen",
                    return_value=contextlib.nullcontext(io.BytesIO(b"")),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err2),
            ):
                with self.assertRaises(SystemExit) as cm2:
                    u.cmd_update(argparse.Namespace(check=False, ref=None, sha256=None, no_wire=True))
            self.assertEqual(cm2.exception.code, 1)
            self.assertIn("SHA256SUMS", err2.getvalue())

            # Missing release tarball asset also fails closed without falling back to archive/
            import urllib.error
            from email.message import Message

            requested = []

            @contextlib.contextmanager
            def fake_urlopen_missing_asset(req_or_url, *args, **kwargs):
                url = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url)
                requested.append(url)
                if "SHA256SUMS" in url:
                    yield io.BytesIO(f"{'a' * 64}  unienv-ai-v0.3.0.tar.gz\n".encode("utf-8"))
                else:
                    raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=Message(), fp=None)

            err3 = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=fake_urlopen_missing_asset),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err3),
            ):
                with self.assertRaises(SystemExit) as cm3:
                    u.cmd_update(argparse.Namespace(check=False, ref="v0.3.0", sha256=None, no_wire=True))
            self.assertEqual(cm3.exception.code, 1)
            self.assertEqual(
                requested,
                [
                    "https://github.com/rupeshbharambe24/unienv-ai/releases/download/v0.3.0/SHA256SUMS",
                    "https://github.com/rupeshbharambe24/unienv-ai/releases/download/v0.3.0/unienv-ai-v0.3.0.tar.gz",
                ],
            )
            self.assertIn("could not download UniEnv ref 'v0.3.0'", err3.getvalue())


if __name__ == "__main__":
    unittest.main()

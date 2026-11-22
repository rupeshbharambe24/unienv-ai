"""Unit tests for #63 (`unienv update --ref <tag>` URL and error handling)."""

import argparse
import contextlib
import importlib.util
import io
import pathlib
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def _make_tarball_bytes(ref_suffix: str = "0.2.1") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for part, filename in (
            ("src", "unienv.py"),
            ("recipes", "core.toml"),
            ("templates", "SKILL.md"),
            ("shims", "ue-py"),
        ):
            data = b"# placeholder\n"
            info = tarfile.TarInfo(name=f"unienv-ai-{ref_suffix}/{part}/{filename}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class UpdateRefTests(unittest.TestCase):
    def test_repo_tarball_url_supports_tags_and_branches(self):
        self.assertEqual(
            u.REPO_TARBALL.format(ref="v0.2.1"),
            "https://github.com/rupeshbharambe24/unienv-ai/archive/v0.2.1.tar.gz",
        )
        self.assertEqual(
            u.REPO_TARBALL.format(ref="main"),
            "https://github.com/rupeshbharambe24/unienv-ai/archive/main.tar.gz",
        )

    def test_cmd_update_succeeds_for_tag_ref(self):
        import hashlib

        tar_bytes = _make_tarball_bytes("0.2.1")
        sha = hashlib.sha256(tar_bytes).hexdigest()
        requested_urls = []

        @contextlib.contextmanager
        def fake_urlopen(url, *args, **kwargs):
            requested_urls.append(url)
            if "SHA256SUMS" in str(url):
                yield io.BytesIO(f"{sha}  unienv-ai-v0.2.1.tar.gz\n".encode("utf-8"))
            else:
                yield io.BytesIO(tar_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=fake_urlopen),
                patch("subprocess.call", return_value=0) as mock_call,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = u.cmd_update(argparse.Namespace(ref="v0.2.1", no_wire=True))
            self.assertEqual(rc, 0)
            self.assertEqual(
                requested_urls,
                [
                    "https://github.com/rupeshbharambe24/unienv-ai/releases/download/v0.2.1/SHA256SUMS",
                    "https://github.com/rupeshbharambe24/unienv-ai/releases/download/v0.2.1/unienv-ai-v0.2.1.tar.gz",
                ],
            )
            self.assertTrue((home / "share" / "src" / "unienv.py").exists())
            mock_call.assert_called_once()

    def test_update_preserves_saved_no_wire_preference(self):
        for saved, explicit, expected in ((False, False, True), (True, False, False), (True, True, True)):
            with self.subTest(saved=saved, explicit=explicit), tempfile.TemporaryDirectory() as tmp:
                with (
                    patch.object(u, "UNIENV_HOME", pathlib.Path(tmp)),
                    patch.object(u, "load_recipes", return_value={}),
                    patch.object(u, "load_state", return_value={"options": {"wire_shells": saved}}),
                    patch.object(urllib.request, "urlopen", return_value=io.BytesIO(_make_tarball_bytes())),
                    patch("subprocess.call", return_value=0) as call,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(u.cmd_update(argparse.Namespace(ref="main", no_wire=explicit)), 0)
                self.assertEqual("--no-wire" in call.call_args.args[0], expected)

    def test_cmd_update_handles_http_404_cleanly(self):
        err_404 = urllib.error.HTTPError(
            url="https://github.com/rupeshbharambe24/unienv-ai/archive/nonexistent.tar.gz",
            code=404,
            msg="Not Found",
            hdrs=Message(),
            fp=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            out = io.StringIO()
            err = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=err_404),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                with self.assertRaises(SystemExit) as cm:
                    u.cmd_update(argparse.Namespace(ref="nonexistent", no_wire=True))
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("could not download UniEnv ref 'nonexistent'", err.getvalue())
            self.assertIn("check the branch or tag name", err.getvalue())

    def test_cmd_update_handles_url_error_cleanly(self):
        url_err = urllib.error.URLError("Name or service not known")
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            out = io.StringIO()
            err = io.StringIO()
            with (
                patch.object(u, "UNIENV_HOME", home),
                patch.object(urllib.request, "urlopen", side_effect=url_err),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                with self.assertRaises(SystemExit) as cm:
                    u.cmd_update(argparse.Namespace(ref="v0.2.1", no_wire=True))
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("could not download UniEnv ref 'v0.2.1'", err.getvalue())
            self.assertIn("check the branch or tag name", err.getvalue())


if __name__ == "__main__":
    unittest.main()

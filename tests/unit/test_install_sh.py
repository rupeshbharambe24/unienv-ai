import pathlib
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _make_mock_archive(dest_tar: pathlib.Path, marker: str) -> None:
    with tempfile.TemporaryDirectory() as src_tmp:
        pkg = pathlib.Path(src_tmp) / "unienv-ai-test"
        for sub in ("src", "recipes", "templates", "shims"):
            (pkg / sub).mkdir(parents=True)
        (pkg / "src" / "unienv.py").write_text(
            "import os, pathlib\n"
            "p = pathlib.Path(os.environ['UNIENV_HOME']) / 'ran_source.txt'\n"
            f"p.write_text({marker!r})\n"
        )
        with tarfile.open(dest_tar, "w:gz") as tf:
            tf.add(pkg, arcname="unienv-ai-test")


class InstallShTests(unittest.TestCase):
    def test_piped_stdin_ignores_local_checkout_in_pwd_and_finds_custom_uv_install_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            fake_pwd = tmp_path / "pwd_with_stale_checkout"
            (fake_pwd / "src").mkdir(parents=True)
            (fake_pwd / "src" / "unienv.py").write_text("raise SystemExit('WRONG_LOCAL_PWD_USED')\n")

            tarball = tmp_path / "repo.tar.gz"
            _make_mock_archive(tarball, "DOWNLOADED_TARBALL_USED")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            custom_uv_dir = tmp_path / "custom_uv"
            custom_uv_dir.mkdir()

            # Mock curl: either serves the uv installer or the repo tarball
            curl_script = fake_bin / "curl"
            curl_script.write_text(
                "#!/usr/bin/env bash\n"
                'for arg in "$@"; do\n'
                '  case "$arg" in\n'
                "    *astral.sh/uv/install.sh*)\n"
                f"      cat <<'EOF'\nmkdir -p \"{custom_uv_dir}\"\n"
                f"cat > \"{custom_uv_dir}/uv\" <<'UVEOF'\n#!/usr/bin/env bash\n"
                'if [ "$1" = "python" ] && [ "$2" = "find" ]; then command -v python3; exit 0; fi\n'
                "exit 0\n"
                "UVEOF\n"
                f'chmod +x "{custom_uv_dir}/uv"\n'
                "EOF\n"
                "      exit 0 ;;\n"
                "    *github.com/rupeshbharambe24/unienv-ai/archive*)\n"
                f'      printf "%s\\n" "$arg" > "{tmp_path / "download-url"}"\n'
                f'      cat "{tarball}"\n'
                "      exit 0 ;;\n"
                "  esac\n"
                "done\n"
                "exit 1\n"
            )
            curl_script.chmod(0o755)

            env = {
                "HOME": str(tmp_path / "home"),
                "UNIENV_HOME": str(tmp_path / "unienv_home"),
                "UV_INSTALL_DIR": str(custom_uv_dir),
                "UNIENV_REF": "0123456789abcdef0123456789abcdef01234567",
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
            pathlib.Path(env["HOME"]).mkdir()

            proc = subprocess.run(
                ["bash", "-s", "--", "--no-system"],
                input=INSTALL_SH.read_text(),
                text=True,
                cwd=fake_pwd,
                env=env,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("Downloading UniEnv AI", proc.stdout)
            self.assertEqual(
                (tmp_path / "download-url").read_text().strip(),
                "https://github.com/rupeshbharambe24/unienv-ai/archive/" + env["UNIENV_REF"] + ".tar.gz",
            )
            marker_file = pathlib.Path(env["UNIENV_HOME"]) / "ran_source.txt"
            self.assertTrue(marker_file.exists())
            self.assertEqual(marker_file.read_text(), "DOWNLOADED_TARBALL_USED")

    def test_file_execution_uses_local_checkout_and_xdg_bin_home_uv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            checkout = tmp_path / "checkout"
            for sub in ("src", "recipes", "templates", "shims"):
                (checkout / sub).mkdir(parents=True)
            (checkout / "install.sh").write_text(INSTALL_SH.read_text())
            (checkout / "install.sh").chmod(0o755)
            (checkout / "src" / "unienv.py").write_text(
                "import os, pathlib\n"
                "p = pathlib.Path(os.environ['UNIENV_HOME']) / 'ran_source.txt'\n"
                "p.write_text('LOCAL_CHECKOUT_USED')\n"
            )

            xdg_bin = tmp_path / "xdg_bin"
            xdg_bin.mkdir()
            uv_bin = xdg_bin / "uv"
            uv_bin.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = "python" ] && [ "$2" = "find" ]; then command -v python3; exit 0; fi\n'
                "exit 0\n"
            )
            uv_bin.chmod(0o755)

            env = {
                "HOME": str(tmp_path / "home"),
                "UNIENV_HOME": str(tmp_path / "unienv_home"),
                "XDG_BIN_HOME": str(xdg_bin),
                "PATH": "/usr/bin:/bin",
            }
            pathlib.Path(env["HOME"]).mkdir()

            proc = subprocess.run(
                [str(checkout / "install.sh"), "--no-system"],
                text=True,
                cwd=tmp_path,
                env=env,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertNotIn("Downloading UniEnv AI", proc.stdout)
            marker_file = pathlib.Path(env["UNIENV_HOME"]) / "ran_source.txt"
            self.assertEqual(marker_file.read_text(), "LOCAL_CHECKOUT_USED")


if __name__ == "__main__":
    unittest.main()

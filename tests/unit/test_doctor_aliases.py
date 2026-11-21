"""Unit tests for live shell hostile-alias probing in `unienv doctor` (#22)."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("unienv", ROOT / "src" / "unienv.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class DoctorAliasTests(unittest.TestCase):
    def test_timeout_is_reported_and_probe_uses_fifteen_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".bashrc").write_text("exec tmux")
            diagnostics = []
            with (
                patch.object(u, "HOME", home),
                patch.object(Path, "exists", return_value=True),
                patch.object(u.subprocess, "run", side_effect=subprocess.TimeoutExpired("bash", 15)) as run,
            ):
                self.assertEqual(u._probe_shell_aliases(diagnostics), [])
            self.assertTrue(any("timed out" in message for _, message in diagnostics))
            self.assertTrue(all(call.kwargs["timeout"] == 15 for call in run.call_args_list))
            bash = next(call for call in run.call_args_list if call.args[0][0] == "/bin/bash")
            self.assertNotIn(". ~/.bashrc", bash.args[0][-1])

    def test_early_exit_without_marker_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".zshrc").write_text("exit 0\n")
            diagnostics = []
            fake_completed = subprocess.CompletedProcess(args=["/bin/zsh"], returncode=0, stdout="", stderr="")
            with (
                patch.object(u, "HOME", home),
                patch.object(Path, "exists", return_value=True),
                patch.object(u.subprocess, "run", return_value=fake_completed),
            ):
                self.assertEqual(u._probe_shell_aliases(diagnostics), [])
            self.assertTrue(any("exited before printing aliases" in message for _, message in diagnostics))

    def test_parse_alias_output_handles_multiple_aliases_per_line_and_shell_formats(self):
        sample = """
        # Commented alias ls='eza'
        alias ll='eza -l' la='eza -a'
        ls='eza --icons'
        cat=bat
        alias rm="rm -i"
        """
        parsed = u._parse_alias_output(sample)
        self.assertEqual(
            parsed,
            [
                ("ll", "eza -l"),
                ("la", "eza -a"),
                ("ls", "eza --icons"),
                ("cat", "bat"),
                ("rm", "rm -i"),
            ],
        )
        for name, target in parsed:
            self.assertIsNotNone(u._hostile_alias_reason(name, target))

    @unittest.skipUnless(Path("/bin/bash").exists(), "/bin/bash required")
    def test_guarded_aliases_ignored_and_unguarded_or_sourced_aliases_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # Sourced file defines hostile aliases on a single line
            (home / ".aliases").write_text("alias ll='eza -l' la='eza -a'\n")
            # .bashrc has a guarded alias (should NOT be flagged) and an unguarded one
            # in the same file that mentions CLAUDECODE (SHOULD be flagged), plus sources ~/.aliases
            (home / ".bashrc").write_text(
                'if [ -z "${AI_AGENT:-}${CLAUDECODE:-}" ]; then\n'
                "  alias ls='eza --icons'\n"
                "fi\n"
                "alias rm='rm -i'\n"
                ". ~/.aliases\n"
            )
            with patch.object(u, "HOME", home):
                findings = u._probe_shell_aliases()

            bash_names = {name for shell, name, _, _ in findings if shell == "bash"}
            self.assertNotIn("ls", bash_names)
            self.assertIn("rm", bash_names)
            self.assertIn("ll", bash_names)
            self.assertIn("la", bash_names)

    @unittest.skipUnless(Path("/bin/zsh").exists(), "/bin/zsh required")
    def test_zdotdir_sourced_aliases_detected_by_doctor(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            home = Path(tmp)
            zdot = home / ".config/zsh"
            zdot.mkdir(parents=True)
            (zdot / ".zshrc").write_text("if [ -z \"$CLAUDECODE\" ]; then\n  alias cat='bat'\nfi\nalias ls='eza'\n")
            manifest = home / "manifest.json"
            manifest.write_text("{}")

            stack.enter_context(patch.dict(os.environ, {"ZDOTDIR": str(zdot)}))
            for name, value in {
                "HOME": home,
                "UNIENV_HOME": home,
                "PY_DIR": home / "py",
                "MANIFEST_FILE": manifest,
                "IS_MAC": False,
            }.items():
                stack.enter_context(patch.object(u, name, value))
            for name, value in {
                "load_recipes": {},
                "load_state": {"options": {"exclude": []}},
                "active_recipes": [],
                "wanted": {"node": [], "bins": [], "skipped_bins": []},
                "node_packages": {},
                "agent_targets": [],
                "mcp_clients": {},
                "brew_prefix": None,
                "render_agent_block": "",
                "_clean_shell_probe": str(u.BIN / "ue-py"),
            }.items():
                stack.enter_context(patch.object(u, name, return_value=value))

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                u.cmd_doctor(argparse.Namespace(json=True, fix=False, quiet_ok=False))

            report = json.loads(out.getvalue())
            ids = {c["id"] for c in report["checks"]}
            self.assertIn("alias:zsh:ls", ids)
            self.assertNotIn("alias:zsh:cat", ids)


if __name__ == "__main__":
    unittest.main()

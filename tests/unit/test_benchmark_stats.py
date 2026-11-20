import importlib.util
import contextlib
import io
import json
import pathlib
import tempfile
import shutil
import subprocess
import sys
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_suite", ROOT / "bench" / "run-suite.py")
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class BenchmarkStatsTests(unittest.TestCase):
    def test_parse_runner_args_defaults_and_flags(self):
        ns = suite.parse_runner_args(["unienv"])
        self.assertEqual(ns.mode, "unienv")
        self.assertEqual(ns.runs, 10)
        self.assertEqual(ns.agent, "claude")
        self.assertEqual(ns.task, "docx")

        ns2 = suite.parse_runner_args(["baseline", "--runs", "12", "--agent", "codex", "--task", "xlsx"])
        self.assertEqual(ns2.mode, "baseline")
        self.assertEqual(ns2.runs, 12)
        self.assertEqual(ns2.agent, "codex")
        self.assertEqual(ns2.task, "xlsx")

        ns3 = suite.parse_runner_args(["unienv", "5", "--agent", "gemini", "--task", "ocr"])
        self.assertEqual(ns3.runs, 5)
        self.assertEqual(ns3.agent, "gemini")
        self.assertEqual(ns3.task, "ocr")

    def test_compute_stats_median_min_max_iqr(self):
        vals = [8.0, 9.0, 9.5, 10.0, 10.5, 11.0, 12.0, 14.0]
        st = suite.compute_stats(vals)
        self.assertEqual(st["count"], 8)
        self.assertEqual(st["median"], 10.25)
        self.assertEqual(st["min"], 8.0)
        self.assertEqual(st["max"], 14.0)
        self.assertGreater(st["iqr"], 0.0)
        self.assertEqual(round(st["q3"] - st["q1"], 2), st["iqr"])

    def test_native_agent_events_and_cache_tokens(self):
        cases = [
            (
                [
                    {
                        "type": "item.completed",
                        "item": {"id": "c1", "type": "command_execution", "command": "python -m venv v"},
                    },
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20},
                    },
                ],
                100,
                20,
                "python -m venv v",
            ),
            (
                [
                    {
                        "type": "tool_use",
                        "tool_id": "g1",
                        "tool_name": "run_shell_command",
                        "parameters": {"command": "pip install pandas"},
                    },
                    {"type": "result", "stats": {"input_tokens": 120, "output_tokens": 30, "cached": 50}},
                ],
                120,
                30,
                "pip install pandas",
            ),
            (
                [
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "a1",
                                    "name": "Bash",
                                    "input": {"command": "ue-py script.py"},
                                }
                            ]
                        },
                    },
                    {
                        "type": "result",
                        "usage": {
                            "input_tokens": 10,
                            "cache_read_input_tokens": 50,
                            "cache_creation_input_tokens": 5,
                            "output_tokens": 4,
                        },
                    },
                ],
                65,
                4,
                "ue-py script.py",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "events.jsonl"
            for events, inputs, outputs, command in cases:
                with self.subTest(command=command):
                    path.write_text("\n".join(json.dumps(e) for e in events))
                    result = suite.parse_transcript(path)
                    self.assertEqual(result["tool_calls"], 1)
                    self.assertEqual(result["cmds"], [command])
                    self.assertEqual(result["input_tokens"], inputs)
                    self.assertEqual(result["output_tokens"], outputs)
                    self.assertTrue(result["telemetry_complete"])

    def test_inputs_exist_before_runs_and_error_text_does_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for task, name in (("pdf-extract", "sample.pdf"), ("ocr", "scan.png"), ("docx-to-pdf", "source.docx")):
                suite.prepare_task(task, root)
                self.assertGreater((root / name).stat().st_size, 50)
            self.assertTrue((root / "scan.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertTrue((root / "sample.pdf").read_bytes().startswith(b"%PDF"))
            with zipfile.ZipFile(root / "source.docx") as zf:
                self.assertIn(b"UNIENV CONVERSION CHECK", zf.read("word/document.xml"))
            (root / "ocr.txt").write_text("Error: OCR failed")
            self.assertFalse(suite.verify_task_output("ocr", root))
            (root / "ocr.txt").write_text("UNIENV WORKS\n")
            self.assertTrue(suite.verify_task_output("ocr", root))
            (root / "extracted.csv").write_text("Region,Revenue\nError,missing\n")
            self.assertFalse(suite.verify_task_output("pdf-extract", root))
            (root / "extracted.csv").write_text("Region,Revenue\nNorth,100\nSouth,200\n")
            self.assertTrue(suite.verify_task_output("pdf-extract", root))

    def test_summary_cli_applies_agent_and_task_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rows.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps({"mode": "unienv", "agent": agent, "task": task, "wall_s": 1})
                    for agent, task in (("codex", "ocr"), ("codex", "docx"), ("claude", "ocr"))
                )
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    suite.main(["summary", "--results", str(path), "--agent", "codex", "--task", "ocr"]), 0
                )
            self.assertIn("unienv:codex:ocr", output.getvalue())
            self.assertNotIn("unienv:codex:docx", output.getvalue())
            self.assertNotIn("unienv:claude:ocr", output.getvalue())

    @unittest.skipIf(sys.platform == "win32" or not shutil.which("bash"), "requires POSIX bash")
    def test_shell_summary_forwards_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in ("run-suite.py", "word-task.sh"):
                shutil.copy2(ROOT / "bench" / name, root / name)
            rows = [
                {"mode": "unienv", "agent": agent, "task": task, "wall_s": 1}
                for agent, task in (("codex", "ocr"), ("codex", "docx"), ("claude", "ocr"))
            ]
            (root / "results.jsonl").write_text("\n".join(map(json.dumps, rows)))
            result = subprocess.run(
                ["bash", str(root / "word-task.sh"), "summary", "--agent", "codex", "--task", "ocr"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("unienv:codex:ocr", result.stdout)
            self.assertNotIn("unienv:codex:docx", result.stdout)
            self.assertNotIn("unienv:claude:ocr", result.stdout)

    def test_record_run_and_summarize_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = pathlib.Path(tmp)
            run_dir = tmp_dir / "run1"
            run_dir.mkdir()
            results = tmp_dir / "results.jsonl"

            # Create a valid report.docx
            with zipfile.ZipFile(run_dir / "report.docx", "w") as zf:
                zf.writestr(
                    "word/document.xml",
                    "<w:document>Quarterly Update Highlights Two new enterprise customers</w:document>",
                )

            transcript = [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ue-py make_doc.py"}},
                            {"type": "tool_use", "name": "Read", "input": {"file_path": "report.docx"}},
                        ]
                    },
                },
                {
                    "type": "result",
                    "num_turns": 2,
                    "total_cost_usd": 0.142,
                    "usage": {"input_tokens": 1200, "output_tokens": 350},
                    "modelUsage": {"claude-opus-5-5": {}},
                },
            ]
            (run_dir / "transcript.jsonl").write_text("\n".join(json.dumps(x) for x in transcript))

            row = suite.record_run(
                mode="unienv",
                agent="claude",
                task="docx",
                run_idx=1,
                start=100.0,
                end=109.2,
                run_dir=run_dir,
                results_path=results,
            )
            self.assertEqual(row["wall_s"], 9.2)
            self.assertEqual(row["tool_calls"], 2)
            self.assertEqual(row["bash_commands"], 1)
            self.assertEqual(row["total_tokens"], 1550)
            self.assertTrue(row["used_ue_py"])
            self.assertTrue(row["task_correct"])

            summary = suite.summarize_results(results, mode="unienv", agent="claude", task="docx")
            self.assertIn("unienv:claude:docx", summary)
            self.assertEqual(summary["unienv:claude:docx"]["runs"], 1)
            self.assertEqual(summary["unienv:claude:docx"]["correct"], "1/1")
            self.assertEqual(summary["unienv:claude:docx"]["total_tokens"]["median"], 1550.0)

    def test_word_task_codex_uses_skip_git_repo_check_and_network_access(self):
        script = (ROOT / "bench" / "word-task.sh").read_text()
        self.assertIn("--skip-git-repo-check", script)
        self.assertIn("sandbox_workspace_write.network_access=true", script)
        self.assertFalse((ROOT / "bench" / "examples" / "codex-real-run.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

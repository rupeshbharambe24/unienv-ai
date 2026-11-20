#!/usr/bin/env python3
"""Benchmark suite helper: task prompts, output verification, transcript parsing, and summary stats.

Supports multiple agents (`claude`, `codex`, `gemini`) and tasks (`docx`, `xlsx`, `pptx`,
`pdf-extract`, `ocr`, `docx-to-pdf`), computing median, min, max, and IQR/spread across runs.
"""

from __future__ import annotations

import argparse
import csv
import struct
import subprocess
import zlib
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

AGENTS = ("claude", "codex", "gemini")

TASKS: dict[str, dict[str, str]] = {
    "docx": {
        "output": "report.docx",
        "prompt": (
            "Put the following text into a Word document named report.docx in the current folder. "
            'Use the first line as the title (Heading 1), "Highlights" as a Heading 2, and the '
            "three highlight lines as a bulleted list.\n\n"
            "Quarterly Update: Team Atlas\n"
            "This quarter the team shipped the new onboarding flow, reduced page load time by 38%, "
            "and closed 142 customer tickets.\n"
            "Highlights\n"
            "Onboarding completion rose from 61% to 79%.\n"
            "Average support response time fell to 3.2 hours.\n"
            "Two new enterprise customers signed annual contracts."
        ),
    },
    "xlsx": {
        "output": "budget.xlsx",
        "prompt": (
            "Create an Excel workbook named budget.xlsx in the current folder with a sheet containing "
            "columns Item and Cost for Server (1200), Storage (450), and Network (350), and a Total row "
            "that uses an Excel SUM formula (=SUM(B2:B4))."
        ),
    },
    "pptx": {
        "output": "deck.pptx",
        "prompt": (
            "Create a PowerPoint presentation named deck.pptx in the current folder from this outline:\n"
            "Slide 1: Title 'Quarterly Roadmap', subtitle 'Engineering & Product'.\n"
            "Slide 2: Title 'Key Milestones', bullets 'Launch v2 API', 'Reduce latency by 40%', 'SOC2 audit'."
        ),
    },
    "pdf-extract": {
        "output": "extracted.csv",
        "prompt": (
            "Extract the table from sample.pdf in the current folder and save it as extracted.csv "
            "with headers Region,Revenue."
        ),
    },
    "ocr": {
        "output": "ocr.txt",
        "prompt": ("Run OCR on scan.png in the current folder and write the extracted text to ocr.txt."),
    },
    "docx-to-pdf": {
        "output": "source.pdf",
        "prompt": ("Convert source.docx in the current folder to a PDF named source.pdf in the current folder."),
    },
}


def parse_runner_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="word-task.sh", description="UniEnv benchmark suite runner")
    p.add_argument("mode", choices=["unienv", "baseline", "summary"], help="benchmark mode")
    p.add_argument("pos_runs", nargs="?", type=int, help="optional positional runs count")
    p.add_argument("--runs", "-n", type=int, default=None, help="number of runs per side (default: 10)")
    p.add_argument("--agent", choices=AGENTS, default="claude", help="agent CLI to benchmark")
    p.add_argument("--task", choices=tuple(TASKS.keys()), default="docx", help="benchmark task")
    p.add_argument("--results", default=None, help="path to results.jsonl")
    ns = p.parse_args(argv)
    ns.runs = ns.runs if ns.runs is not None else (ns.pos_runs if ns.pos_runs is not None else 10)
    if ns.runs < 1:
        p.error("--runs must be >= 1")
    return ns


def compute_stats(values: list[float]) -> dict[str, float]:
    """Return count, median, min, max, q1, q3, and iqr for a non-empty list of numbers."""
    if not values:
        return {"count": 0, "median": 0.0, "min": 0.0, "max": 0.0, "q1": 0.0, "q3": 0.0, "iqr": 0.0}
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    med = float(statistics.median(ordered))
    if n == 1:
        q1 = q3 = ordered[0]
    elif n < 4:
        q1, q3 = ordered[0], ordered[-1]
    else:
        qs = statistics.quantiles(ordered, n=4, method="inclusive")
        q1, q3 = float(qs[0]), float(qs[2])
    q1_r = round(q1, 2)
    q3_r = round(q3, 2)
    return {
        "count": n,
        "median": round(med, 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
        "q1": q1_r,
        "q3": q3_r,
        "iqr": round(q3_r - q1_r, 2),
    }


def prepare_task(task: str, run_dir: Path) -> None:
    """Generate deterministic inputs before timing, using only the standard library."""
    run_dir.mkdir(parents=True, exist_ok=True)
    if task == "pdf-extract":
        content = b"BT /F1 16 Tf 50 750 Td (Region) Tj 150 0 Td (Revenue) Tj -150 -30 Td (North) Tj 150 0 Td (100) Tj -150 -30 Td (South) Tj 150 0 Td (200) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
        start = len(pdf)
        pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010} 00000 n \n".encode())
        pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
        (run_dir / "sample.pdf").write_bytes(pdf)
    elif task == "docx-to-pdf":
        with zipfile.ZipFile(run_dir / "source.docx", "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            )
            zf.writestr(
                "_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
            )
            zf.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>UNIENV CONVERSION CHECK</w:t></w:r></w:p></w:body></w:document>',
            )
    elif task == "ocr":
        glyphs = {
            "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
            "N": ["10001", "11001", "11001", "10101", "10011", "10011", "10001"],
            "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
            "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
            "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
            "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
            "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
            "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
            "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
            "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
            " ": ["00000"] * 7,
        }
        text, scale, margin = "UNIENV WORKS", 8, 32
        width, height = len(text) * 6 * scale + margin * 2, 7 * scale + margin * 2
        rows = [bytearray([255] * width) for _ in range(height)]
        for index, char in enumerate(text):
            for y, row in enumerate(glyphs[char]):
                for x, bit in enumerate(row):
                    if bit == "1":
                        for yy in range(scale):
                            for xx in range(scale):
                                rows[margin + y * scale + yy][margin + (index * 6 + x) * scale + xx] = 0

        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows)))
        png += chunk(b"IEND", b"")
        (run_dir / "scan.png").write_bytes(png)


def verify_task_output(task: str, run_dir: Path) -> bool:
    try:
        if task == "docx":
            p = run_dir / "report.docx"
            if not p.exists():
                return False
            xml = zipfile.ZipFile(p).read("word/document.xml").decode(errors="ignore")
            return all(s in xml for s in ("Quarterly Update", "Highlights", "Two new enterprise customers"))
        if task == "xlsx":
            p = run_dir / "budget.xlsx"
            if not p.exists():
                return False
            with zipfile.ZipFile(p) as zf:
                blobs = "\n".join(zf.read(n).decode(errors="ignore") for n in zf.namelist() if n.endswith(".xml"))
            return "SUM(" in blobs.upper() and "1200" in blobs
        if task == "pptx":
            p = run_dir / "deck.pptx"
            if not p.exists():
                return False
            with zipfile.ZipFile(p) as zf:
                slides = [
                    zf.read(n).decode(errors="ignore")
                    for n in zf.namelist()
                    if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                ]
            joined = "\n".join(slides)
            return len(slides) >= 2 and "Quarterly Roadmap" in joined and "Key Milestones" in joined
        if task == "pdf-extract":
            p = run_dir / "extracted.csv"
            if not p.exists():
                return False
            with p.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            return {(r.get("Region", "").strip(), r.get("Revenue", "").strip()) for r in rows} == {
                ("North", "100"),
                ("South", "200"),
            }
        if task == "ocr":
            p = run_dir / "ocr.txt"
            text = p.read_text(errors="ignore").upper() if p.exists() else ""
            return "UNIENV" in text and "WORKS" in text
        if task == "docx-to-pdf":
            p = run_dir / "source.pdf"
            if not p.exists() or not p.read_bytes().startswith(b"%PDF"):
                return False
            result = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True, timeout=30)
            return result.returncode == 0 and "UNIENV CONVERSION CHECK" in result.stdout
    except Exception:
        return False
    return False


def parse_transcript(transcript_path: Path) -> dict:
    """Read native Claude, Codex and Gemini events without double-counting usage."""
    result = {}
    commands = []
    seen = set()
    tool_calls = 0
    input_tokens = output_tokens = turns = 0
    model = None
    usage_seen = False
    if not transcript_path.exists():
        lines = []
    else:
        lines = transcript_path.read_text(errors="replace").splitlines()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        model = model or event.get("model")
        if kind == "turn.completed":
            usage = event.get("usage") or {}
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))
            usage_seen = True
            turns += 1
        if kind == "result":
            result = event
            usage = event.get("usage") or event.get("stats") or {}
            if usage:
                input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
                input_tokens += int(usage.get("cache_read_input_tokens", 0))
                input_tokens += int(usage.get("cache_creation_input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
                usage_seen = True
        tools = []
        if kind == "assistant":
            tools = [
                b
                for b in event.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
        elif kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
                tools = [item]
        elif kind in ("tool_use", "tool_call", "command_execution", "function_call"):
            tools = [event]
        for tool in tools:
            identity = tool.get("id") or tool.get("tool_id")
            if identity is not None and identity in seen:
                continue
            if identity is not None:
                seen.add(identity)
            tool_calls += 1
            name = tool.get("name") or tool.get("tool_name") or tool.get("type")
            if name in ("Bash", "run_shell_command", "command_execution"):
                command = tool.get("command") or (tool.get("input") or tool.get("parameters") or {}).get("command")
                if isinstance(command, str):
                    commands.append(command)
    model = model or next(iter((result.get("modelUsage") or {}).keys()), None)
    return {
        "turns": result.get("num_turns", turns or None),
        "cost_usd": round(float(result["total_cost_usd"]), 4) if "total_cost_usd" in result else None,
        "tool_calls": tool_calls,
        "bash_commands": len(commands),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cmds": commands,
        "model": model,
        "telemetry_complete": usage_seen,
    }


def record_run(
    *,
    mode: str,
    agent: str,
    task: str,
    run_idx: int,
    start: float,
    end: float,
    run_dir: Path,
    results_path: Path,
) -> dict:
    parsed = parse_transcript(run_dir / "transcript.jsonl")
    joined = "\n".join(parsed["cmds"])
    ok = verify_task_output(task, run_dir)
    row = {
        "mode": mode,
        "agent": agent,
        "task": task,
        "run": int(run_idx),
        "wall_s": round(float(end) - float(start), 1),
        "turns": parsed["turns"],
        "cost_usd": parsed["cost_usd"],
        "tool_calls": parsed["tool_calls"],
        "bash_commands": parsed["bash_commands"],
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "total_tokens": parsed["total_tokens"],
        "created_venv": bool(re.search(r"\b(venv|virtualenv)\b", joined)),
        "pip_or_npm_install": bool(re.search(r"(pip3?|uv pip|npm) (install|i )|uv add|--with ", joined)),
        "used_ue_py": "ue-py" in joined,
        "task_correct": ok,
        "docx_correct": ok if task == "docx" else None,
        "model": parsed["model"],
        "telemetry_complete": parsed["telemetry_complete"],
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def summarize_results(
    results_path: Path,
    *,
    mode: str | None = None,
    agent: str | None = None,
    task: str | None = None,
) -> dict[str, dict]:
    if not results_path.exists():
        return {}
    groups: dict[str, list[dict]] = {}
    for line in results_path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        r_mode = row.get("mode", "unienv")
        r_agent = row.get("agent", "claude")
        r_task = row.get("task", "docx")
        if mode and r_mode != mode:
            continue
        if agent and r_agent != agent:
            continue
        if task and r_task != task:
            continue
        key = f"{r_mode}:{r_agent}:{r_task}"
        groups.setdefault(key, []).append(row)

    summaries: dict[str, dict] = {}
    for key, rows in sorted(groups.items()):
        walls = [float(r["wall_s"]) for r in rows if r.get("wall_s") is not None]
        tools = [float(r.get("tool_calls", r.get("bash_commands", 0))) for r in rows]
        tokens = [float(r.get("total_tokens", 0)) for r in rows]
        correct = sum(bool(r.get("task_correct", r.get("docx_correct"))) for r in rows)
        summaries[key] = {
            "runs": len(rows),
            "wall_s": compute_stats(walls),
            "tool_calls": compute_stats(tools),
            "total_tokens": compute_stats(tokens),
            "correct": f"{correct}/{len(rows)}",
        }
    return summaries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark suite utilities")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_prompt = sub.add_parser("prompt")
    s_prompt.add_argument("--task", choices=tuple(TASKS.keys()), default="docx")

    s_prepare = sub.add_parser("prepare")
    s_prepare.add_argument("--task", choices=tuple(TASKS), required=True)
    s_prepare.add_argument("--run-dir", required=True)

    s_rec = sub.add_parser("record")
    s_rec.add_argument("--mode", required=True)
    s_rec.add_argument("--agent", default="claude")
    s_rec.add_argument("--task", default="docx")
    s_rec.add_argument("--run", type=int, required=True)
    s_rec.add_argument("--start", type=float, required=True)
    s_rec.add_argument("--end", type=float, required=True)
    s_rec.add_argument("--run-dir", required=True)
    s_rec.add_argument("--results", required=True)

    s_sum = sub.add_parser("summary")
    s_sum.add_argument("--results", required=True)
    s_sum.add_argument("--mode", default=None)
    s_sum.add_argument("--agent", default=None)
    s_sum.add_argument("--task", default=None)

    args = p.parse_args(argv)
    if args.cmd == "prepare":
        prepare_task(args.task, Path(args.run_dir))
        return 0
    if args.cmd == "prompt":
        print(TASKS[args.task]["prompt"])
        return 0
    if args.cmd == "record":
        row = record_run(
            mode=args.mode,
            agent=args.agent,
            task=args.task,
            run_idx=args.run,
            start=args.start,
            end=args.end,
            run_dir=Path(args.run_dir),
            results_path=Path(args.results),
        )
        print("   ", json.dumps(row))
        return 0
    if args.cmd == "summary":
        summaries = summarize_results(
            Path(args.results),
            mode=args.mode,
            agent=args.agent,
            task=args.task,
        )
        for key, stats in summaries.items():
            w = stats["wall_s"]
            tc = stats["tool_calls"]
            tk = stats["total_tokens"]
            print(
                f"==> Summary [{key}] (n={stats['runs']}, correct={stats['correct']}): "
                f"wall_s median={w['median']}s (IQR={w['iqr']}s, min={w['min']}s, max={w['max']}s) | "
                f"tool_calls median={tc['median']} | total_tokens median={tk['median']}"
            )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

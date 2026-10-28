#!/usr/bin/env python3
"""unienv-mcp: UniEnv's tools as a Model Context Protocol (MCP) server.

Lets AI apps that can't run shell commands themselves (Claude Desktop chat,
LM Studio, other MCP clients) use the same always-ready environment as
terminal agents: read and convert documents, run Python with the shared
packages, run UniEnv's tools, and install missing packages.

Transport: stdio, newline-delimited JSON-RPC 2.0. Standard library only, so it
starts instantly and can't break from missing packages. Logs go to stderr;
stdout carries protocol messages only.

    unienv mcp            # normally launched by the MCP client, not by hand
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

SERVER_NAME = "unienv"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]  # newest first

HOME = Path.home()
UNIENV_HOME = Path(os.environ.get("UNIENV_HOME", HOME / ".unienv")).expanduser()
BIN = UNIENV_HOME / "bin"
MANIFEST_FILE = UNIENV_HOME / "manifest.json"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"
WORKDIR = Path(os.environ.get("UNIENV_WORKDIR", HOME / "UniEnv")).expanduser()

MAX_OUTPUT = 20_000          # characters of tool output returned to the model
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900

OFFICE_EXT = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"}
PANDOC_IN = {".md", ".markdown", ".html", ".htm", ".txt", ".rst", ".tex", ".epub", ".docx", ".odt"}
PANDOC_OUT = {"md": "markdown", "markdown": "markdown", "html": "html", "docx": "docx", "odt": "odt",
              "txt": "plain", "rst": "rst", "epub": "epub"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def log(msg: str) -> None:
    print(f"[unienv-mcp] {msg}", file=sys.stderr, flush=True)


# ── environment ─────────────────────────────────────────────────────────────

def tool_env() -> dict[str, str]:
    """Environment for child processes: UniEnv + Homebrew + system PATH.

    GUI apps start MCP servers with launchd's minimal PATH, so build it here
    instead of relying on shell startup files.
    """
    path = [str(BIN)]
    for p in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin"):
        if Path(p).is_dir():
            path.append(p)
    path += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL")}
    env.update({"PATH": os.pathsep.join(path), "UNIENV_HOME": str(UNIENV_HOME),
                "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"})
    tmp = os.environ.get("TMPDIR", "/tmp")
    if sys.platform == "darwin" and (tmp == "/tmp" or tmp.startswith("/tmp/")):
        tmp = "/private" + tmp  # Leptonica rewrites /tmp paths (see env.sh)
    env["TMPDIR"] = tmp
    return env


def manifest() -> dict:
    try:
        return json.loads(MANIFEST_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def which(name: str) -> str | None:
    return shutil.which(name, path=tool_env()["PATH"])


def resolve(path: str, *, base: Path | None = None) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else (base or WORKDIR) / p


def workdir(cwd: str | None) -> Path:
    d = resolve(cwd) if cwd else WORKDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(cmd: list[str], *, cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    """Run without a shell, stdin closed (agent-safe), output captured and capped."""
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=tool_env(), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, errors="replace", timeout=timeout)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        partial = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        code, out, err = 124, partial, f"timed out after {timeout}s (raise timeout_s, max {MAX_TIMEOUT})"
    except FileNotFoundError:
        code, out, err = 127, "", f"command not found: {cmd[0]}"
    return code, out, err, time.monotonic() - start


def cap(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[… truncated {len(text) - limit:,} more characters]"


def snapshot(d: Path) -> dict[str, float]:
    try:
        return {p.name: p.stat().st_mtime for p in d.iterdir() if p.is_file()}
    except OSError:
        return {}


def changed_files(d: Path, before: dict[str, float]) -> list[str]:
    after = snapshot(d)
    return sorted(str(d / n) for n, m in after.items() if before.get(n) != m)


def free_name(p: Path) -> Path:
    """Never overwrite: report.pdf -> report-1.pdf -> report-2.pdf ..."""
    if not p.exists():
        return p
    for i in range(1, 1000):
        q = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not q.exists():
            return q
    raise RuntimeError(f"no free file name for {p}")


class ToolError(Exception):
    """Reported to the model as a tool result with isError=true."""


# ── tools ───────────────────────────────────────────────────────────────────

def t_capabilities(_: dict) -> str:
    m = manifest()
    if not m:
        raise ToolError("UniEnv is not installed (no manifest.json). Run ./install.sh or `unienv install`.")
    py = [f"{k} {v}" for k, v in m.get("python", {}).get("packages", {}).items() if v]
    node = [f"{k} {v}" for k, v in m.get("node", {}).get("packages", {}).items() if v]
    tools = [b for b, p in m.get("bins", {}).items() if p]
    notes = []
    for f in sorted(RECIPES_DIR.glob("*.toml")):
        try:
            r = tomllib.loads(f.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if r.get("id") in m.get("batteries", []) and r.get("agent", {}).get("notes"):
            notes.append(f"[{r['id']}] {r['summary']}\n{r['agent']['notes'].strip()}")
    return "\n".join([
        f"UniEnv {m.get('unienv', '?')} on {m.get('platform', '?')}",
        f"Working folder (default cwd, outputs land here): {WORKDIR}",
        f"Batteries: {', '.join(m.get('batteries', []))}",
        f"Python {m.get('python', {}).get('version', '')} packages (use run_python): {', '.join(py)}",
        f"Node packages (run_tool with tool='ue-node'): {', '.join(node) or '(none)'}",
        f"Tools (use run_tool): {', '.join(tools)}",
        "",
        "Usage notes (shell commands map to run_tool / run_python):",
        "\n\n".join(notes),
    ])


def t_read_document(a: dict) -> str:
    path = resolve(a["path"])
    if not path.exists():
        raise ToolError(f"file not found: {path}")
    limit = max(1000, min(int(a.get("max_chars") or MAX_OUTPUT), 200_000))
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log", ".tsv", ".xml"}:
        return cap(path.read_text(errors="replace"), limit)
    if ext in IMAGE_EXT:
        if not which("tesseract"):
            raise ToolError("OCR needs tesseract: install_package(name='tesseract', kind='brew')")
        code, out, err, _ = run(["tesseract", str(path), "-"], cwd=path.parent, timeout=120)
        if code != 0:
            raise ToolError(f"tesseract failed: {err.strip()[:500]}")
        return cap(f"[OCR text of {path.name}]\n{out}", limit)
    code, out, err, _ = run([str(BIN / "ue-py"), "-m", "markitdown", str(path)], cwd=path.parent, timeout=180)
    if code == 0 and out.strip():
        return cap(out, limit)
    if ext == ".pdf" and which("pdftotext"):  # fallback for PDFs markitdown can't parse
        code2, out2, err2, _ = run(["pdftotext", "-layout", str(path), "-"], cwd=path.parent, timeout=180)
        if code2 == 0 and out2.strip():
            return cap(out2, limit)
        if code2 == 0:
            raise ToolError("the PDF has no text layer (scanned?). Render pages with run_tool pdftoppm, then OCR them.")
    raise ToolError(f"could not read {path.name}: {(err or 'no text extracted').strip()[:800]}")


def t_convert_document(a: dict) -> str:
    src = resolve(a["input_path"])
    if not src.exists():
        raise ToolError(f"file not found: {src}")
    fmt = a["output_format"].lower().lstrip(".")
    out_dir = workdir(a.get("output_dir")) if a.get("output_dir") else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = free_name(out_dir / f"{src.stem}.{fmt}")
    ext = src.suffix.lower()
    soffice = BIN / "soffice-safe"

    def via_soffice(input_path: Path, target_fmt: str) -> Path:
        with tempfile.TemporaryDirectory(dir=tool_env()["TMPDIR"]) as tmp:
            code, out, err, _ = run([str(soffice), "--convert-to", target_fmt, "--outdir", tmp, str(input_path)],
                                    cwd=Path(tmp), timeout=300)
            produced = list(Path(tmp).glob(f"*.{target_fmt.split(':')[0]}"))
            if code != 0 or not produced:
                raise ToolError(f"LibreOffice conversion failed: {(err or out).strip()[:800] or 'no output'}")
            shutil.move(str(produced[0]), dest)
        return dest

    def via_pandoc(input_path: Path, to: str, target: Path) -> Path:
        code, out, err, _ = run(["pandoc", str(input_path), "-t", to, "-o", str(target)], cwd=input_path.parent, timeout=180)
        if code != 0:
            raise ToolError(f"pandoc failed: {err.strip()[:800]}")
        return target

    if ext == ".pdf":
        if fmt in ("txt", "text"):
            dest = free_name(out_dir / f"{src.stem}.txt")
            code, _, err, _ = run(["pdftotext", "-layout", str(src), str(dest)], cwd=src.parent, timeout=180)
            if code != 0:
                raise ToolError(f"pdftotext failed: {err.strip()[:500]}")
            return f"Converted to {dest}"
        if fmt in ("png", "jpg", "jpeg"):
            prefix = free_name(out_dir / src.stem)
            code, _, err, _ = run(["pdftoppm", f"-{'png' if fmt == 'png' else 'jpeg'}", "-r", "110", str(src), str(prefix)],
                                  cwd=src.parent, timeout=300)
            if code != 0:
                raise ToolError(f"pdftoppm failed: {err.strip()[:500]}")
            pages = sorted(out_dir.glob(f"{prefix.name}-*.{'png' if fmt == 'png' else 'jpg'}"))
            return "Rendered pages:\n" + "\n".join(str(p) for p in pages)
        raise ToolError("PDF can be converted to txt, png or jpg. To get editable text, use read_document.")

    if fmt == "pdf":
        if ext in OFFICE_EXT:
            return f"Converted to {via_soffice(src, 'pdf')}"
        if ext in PANDOC_IN:  # pandoc → docx → LibreOffice → pdf (no LaTeX needed)
            with tempfile.TemporaryDirectory(dir=tool_env()["TMPDIR"]) as tmp:
                mid = via_pandoc(src, "docx", Path(tmp) / f"{src.stem}.docx")
                return f"Converted to {via_soffice(mid, 'pdf')}"
    if ext in OFFICE_EXT and fmt in {"docx", "xlsx", "pptx", "odt", "ods", "odp", "csv", "html"} and \
            not (ext == ".docx" and fmt in PANDOC_OUT):
        return f"Converted to {via_soffice(src, fmt)}"
    if ext in PANDOC_IN and fmt in PANDOC_OUT:
        return f"Converted to {via_pandoc(src, PANDOC_OUT[fmt], dest)}"
    raise ToolError(f"don't know how to convert {ext or 'this file'} to {fmt}. "
                    "Try run_python (python-docx/openpyxl/pandas) or run_tool with pandoc/soffice-safe.")


def t_run_python(a: dict) -> str:
    cwd = workdir(a.get("cwd"))
    before = snapshot(cwd)
    with tempfile.NamedTemporaryFile("w", suffix=".py", prefix="unienv_", dir=tool_env()["TMPDIR"],
                                     delete=False) as f:
        f.write(a["code"])
        script = f.name
    try:
        code, out, err, secs = run([str(BIN / "ue-py"), script], cwd=cwd, timeout=a.get("timeout_s", DEFAULT_TIMEOUT))
    finally:
        os.unlink(script)
    return _report(code, out, err, secs, cwd, before)


def allowed_tools() -> dict[str, str]:
    tools = {b: p for b, p in manifest().get("bins", {}).items() if p}
    tools["ue-node"] = str(BIN / "ue-node")
    tools.pop("uv", None)  # environment management goes through install_package
    return tools


def t_run_tool(a: dict) -> str:
    tools = allowed_tools()
    name = a["tool"]
    if name not in tools:
        raise ToolError(f"'{name}' is not a UniEnv tool. Available: {', '.join(sorted(tools))}. "
                        "For anything else use run_python, or install_package to add a tool.")
    args = a.get("args") or []
    if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        raise ToolError("args must be a list of strings (no shell; one argument per item)")
    cwd = workdir(a.get("cwd"))
    before = snapshot(cwd)
    code, out, err, secs = run([tools[name], *args], cwd=cwd, timeout=a.get("timeout_s", DEFAULT_TIMEOUT))
    return _report(code, out, err, secs, cwd, before)


def t_install_package(a: dict) -> str:
    kind = a.get("kind", "python")
    flag = {"python": [], "node": ["--node"], "brew": ["--bin"], "cask": ["--cask"]}.get(kind)
    if flag is None:
        raise ToolError("kind must be one of: python, node, brew, cask")
    name = a["name"].strip()
    if not re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9._\-/@\[\],<>=!~ ]*", name):
        raise ToolError(f"invalid package name: {name!r}")
    code, out, err, secs = run([str(BIN / "unienv"), "need", *flag, *name.split()], cwd=UNIENV_HOME, timeout=900)
    text = (out + ("\n" + err if err.strip() else "")).strip()
    if code != 0:
        raise ToolError(cap(text or f"install failed (exit {code})", 4000))
    return cap(text, 4000)


def _report(code: int, out: str, err: str, secs: float, cwd: Path, before: dict) -> str:
    parts = [f"exit code: {code}  ({secs:.1f}s, cwd: {cwd})"]
    files = changed_files(cwd, before)
    if files:
        parts.append("files created/modified:\n" + "\n".join(f"  {f}" for f in files))
    if out.strip():
        parts.append("stdout:\n" + cap(out, MAX_OUTPUT // 2))
    if err.strip():
        parts.append("stderr:\n" + cap(err, MAX_OUTPUT // 2))
    text = "\n\n".join(parts)
    if code != 0:
        raise ToolError(text)
    return text


TOOLS = [
    {
        "name": "unienv_capabilities",
        "title": "What's installed",
        "description": "List the document, PDF, OCR, data and media tools and Python/Node packages that are "
                       "pre-installed on this computer, with usage notes. Call this first when unsure what is available.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "fn": t_capabilities,
    },
    {
        "name": "read_document",
        "title": "Read a document",
        "description": "Extract the text of a local file as Markdown: Word, Excel, PowerPoint, PDF, HTML, CSV, "
                       "plain text, or an image (OCR). Paths may start with ~ ; relative paths are in the UniEnv working folder.",
        "inputSchema": {"type": "object", "required": ["path"], "additionalProperties": False, "properties": {
            "path": {"type": "string", "description": "File path, e.g. ~/Downloads/report.pdf"},
            "max_chars": {"type": "integer", "description": "Maximum characters to return (default 20000)"},
        }},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "fn": t_read_document,
    },
    {
        "name": "convert_document",
        "title": "Convert a document",
        "description": "Convert a local file to another format and save it next to the original (or in output_dir). "
                       "Examples: docx/xlsx/pptx/md/html → pdf, md/html → docx, doc → docx, docx → md, pdf → txt/png. "
                       "Never overwrites: adds -1, -2 … to the name if needed. Returns the new file's path.",
        "inputSchema": {"type": "object", "required": ["input_path", "output_format"], "additionalProperties": False,
                        "properties": {
                            "input_path": {"type": "string"},
                            "output_format": {"type": "string", "description": "pdf, docx, md, html, txt, xlsx, pptx, csv, png …"},
                            "output_dir": {"type": "string", "description": "Optional folder for the result"},
                        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "fn": t_convert_document,
    },
    {
        "name": "run_python",
        "title": "Run Python",
        "description": "Run a Python 3.12 script with UniEnv's pre-installed packages (python-docx, openpyxl, "
                       "python-pptx, pandas, matplotlib, pypdf, reportlab, duckdb …). Use it to create or edit Word/Excel/"
                       "PowerPoint files, analyse data and make charts. Runs in the UniEnv working folder unless cwd is "
                       "given; reports stdout/stderr and the files it created. Never pip install here: use install_package.",
        "inputSchema": {"type": "object", "required": ["code"], "additionalProperties": False, "properties": {
            "code": {"type": "string", "description": "Complete Python script"},
            "cwd": {"type": "string", "description": "Working directory (default: the UniEnv working folder)"},
            "timeout_s": {"type": "integer", "description": f"Default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}"},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
        "fn": t_run_python,
    },
    {
        "name": "run_tool",
        "title": "Run a UniEnv tool",
        "description": "Run one of UniEnv's command-line tools (pandoc, soffice-safe, pdftotext, pdftoppm, qpdf, gs, "
                       "tesseract, duckdb, jq, rg, ffmpeg, magick, ue-node …) with a list of arguments. No shell: pass each "
                       "argument as a separate list item. See unienv_capabilities for the exact list.",
        "inputSchema": {"type": "object", "required": ["tool"], "additionalProperties": False, "properties": {
            "tool": {"type": "string", "description": "Tool name, e.g. pandoc"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments, e.g. [\"in.md\", \"-o\", \"out.docx\"]"},
            "cwd": {"type": "string"},
            "timeout_s": {"type": "integer"},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        "fn": t_run_tool,
    },
    {
        "name": "install_package",
        "title": "Install a package permanently",
        "description": "Permanently add a missing package to UniEnv so every AI tool on this computer can use it: "
                       "kind=python (PyPI, default), node (npm), brew (Homebrew formula, e.g. ffmpeg) or cask (macOS app).",
        "inputSchema": {"type": "object", "required": ["name"], "additionalProperties": False, "properties": {
            "name": {"type": "string", "description": "Package name, e.g. seaborn"},
            "kind": {"type": "string", "enum": ["python", "node", "brew", "cask"]},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        "fn": t_install_package,
    },
]
TOOL_INDEX = {t["name"]: t for t in TOOLS}

INSTRUCTIONS = (
    "UniEnv gives you a ready, persistent environment on the user's computer for documents, spreadsheets, "
    "presentations, PDFs, OCR and data. Prefer these tools over asking the user to install software. "
    "read_document to read local files; convert_document for format changes; run_python (python-docx, openpyxl, "
    "python-pptx, pandas, matplotlib …) to create or edit files; run_tool for pandoc, LibreOffice (soffice-safe), "
    "Poppler, tesseract and friends; install_package if something is missing. New files go to "
    f"{WORKDIR} unless you give a path. Tell the user the full path of every file you create."
)


# ── JSON-RPC / MCP plumbing ─────────────────────────────────────────────────

def handle(msg: dict) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notification (initialized, cancelled, ...): nothing to send back
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def error(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return ok({"protocolVersion": version,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "title": "UniEnv AI", "version": SERVER_VERSION},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]})
    if method == "tools/call":
        tool = TOOL_INDEX.get(params.get("name"))
        if tool is None:
            return error(-32602, f"unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        if missing:
            return error(-32602, f"missing required argument(s): {', '.join(missing)}")
        start = time.monotonic()
        try:
            text, is_error = tool["fn"](args), False
        except ToolError as e:
            text, is_error = str(e), True
        except Exception as e:  # never crash the server on a bad call
            text, is_error = f"{type(e).__name__}: {e}", True
        log(f"{tool['name']} {'failed' if is_error else 'ok'} in {time.monotonic() - start:.1f}s")
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return error(-32601, f"method not found: {method}")


def serve() -> int:
    log(f"started (UniEnv home {UNIENV_HOME}, working folder {WORKDIR})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (handle(m) for m in batch if isinstance(m, dict)) if r]
            reply = replies if isinstance(msg, list) else (replies[0] if replies else None)
        if reply:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())

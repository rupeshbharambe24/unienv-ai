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

import base64
import contextlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

SERVER_NAME = "unienv"
SERVER_VERSION = "0.2.2"
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]  # newest first

HOME = Path.home()
UNIENV_HOME = Path(os.environ.get("UNIENV_HOME", HOME / ".unienv")).expanduser()
BIN = UNIENV_HOME / "bin"
MANIFEST_FILE = UNIENV_HOME / "manifest.json"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


def resolve_workdir(raw: str | None = None, *, home: Path | None = None) -> Path:
    """Resolve UNIENV_WORKDIR safely against home.

    Unset, empty, or whitespace-only values default to ``home / "UniEnv"``.
    Relative paths resolve relative to ``home`` instead of the process cwd
    (which GUI MCP hosts often set to ``/``).
    """
    base = (home or HOME).expanduser()
    value = (os.environ.get("UNIENV_WORKDIR") if raw is None else raw) or ""
    value = value.strip()
    if not value:
        return (base / "UniEnv").resolve()
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


WORKDIR = resolve_workdir()

# -P: never put the cwd or the script's folder first on sys.path, so a stray json.py or
# pandas.py next to a document (e.g. in ~/Downloads) can't shadow a real module
SAFE_PATH = ["-P"]

MAX_OUTPUT = 20_000  # characters of tool output returned to the model
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900

OFFICE_EXT = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"}
PANDOC_IN = {".md", ".markdown", ".html", ".htm", ".txt", ".rst", ".tex", ".epub", ".docx", ".odt"}
PANDOC_OUT = {
    "md": "markdown",
    "markdown": "markdown",
    "html": "html",
    "docx": "docx",
    "odt": "odt",
    "txt": "plain",
    "rst": "rst",
    "epub": "epub",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_INLINE_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_INLINE_IMAGES = 4
MAX_INLINE_IMAGE_BYTES = 512 * 1024
MAX_INLINE_TOTAL_BYTES = 2 * 1024 * 1024


def _valid_image(raw: bytes, mime: str) -> bool:
    """Reject mismatched signatures and obviously truncated image containers."""
    if mime == "image/png":
        return (
            len(raw) >= 45
            and raw.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            and int.from_bytes(raw[16:20], "big") > 0
            and int.from_bytes(raw[20:24], "big") > 0
            and raw.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82")
        )
    if mime == "image/jpeg":
        return len(raw) >= 4 and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9")
    if mime == "image/gif":
        return len(raw) >= 14 and raw[:6] in (b"GIF87a", b"GIF89a") and raw.endswith(b";")
    if mime == "image/webp":
        return (
            len(raw) >= 20
            and raw[:4] == b"RIFF"
            and raw[8:12] == b"WEBP"
            and int.from_bytes(raw[4:8], "little") + 8 == len(raw)
        )
    return False


def _image_blocks(
    paths: list[Path],
    *,
    max_images: int = MAX_INLINE_IMAGES,
    max_file_bytes: int = MAX_INLINE_IMAGE_BYTES,
    max_total_bytes: int = MAX_INLINE_TOTAL_BYTES,
) -> list[dict]:
    blocks: list[dict] = []
    total = 0
    for p in paths:
        if len(blocks) >= max_images:
            break
        mime = _INLINE_IMAGE_MIME.get(p.suffix.lower())
        if not mime:
            continue
        try:
            if p.stat().st_size > max_file_bytes:
                continue
            raw = p.read_bytes()
        except OSError:
            continue
        if not _valid_image(raw, mime) or len(raw) > max_file_bytes or total + len(raw) > max_total_bytes:
            continue
        total += len(raw)
        blocks.append({"type": "image", "data": base64.b64encode(raw).decode("ascii"), "mimeType": mime})
    return blocks


def _with_images(text: str, image_blocks: list[dict]) -> str | list[dict]:
    return [{"type": "text", "text": text}, *image_blocks] if image_blocks else text


def log(msg: str) -> None:
    print(f"[unienv-mcp] {msg}", file=sys.stderr, flush=True)


# ── environment ─────────────────────────────────────────────────────────────


_TOOL_ENV_ALLOWLIST = {
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "SHELL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "UNIENV_HOME",
    "UNIENV_WORKDIR",
    "UNIENV_SOFFICE",
    "TESSDATA_PREFIX",
}

_INSTALL_ENV_ALLOWLIST = {
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_INDEX",
    "UV_NATIVE_TLS",
    "UV_ALLOW_INSECURE_HOST",
    "UV_NO_CACHE",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_MIRROR",
    "UV_OFFLINE",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_TRUSTED_HOST",
    "PIP_CERT",
    "HOMEBREW_NO_AUTO_UPDATE",
    "HOMEBREW_NO_ENV_HINTS",
    "HOMEBREW_NO_INSTALL_CLEANUP",
    "HOMEBREW_API_DOMAIN",
    "HOMEBREW_BOTTLE_DOMAIN",
    "HOMEBREW_PIP_INDEX_URL",
}


def tool_env() -> dict[str, str]:
    """Environment for child processes: UniEnv + Homebrew + system PATH.

    GUI apps start MCP servers with launchd's minimal PATH, so build it here
    instead of relying on shell startup files. Only an explicit allowlist of
    identity, proxy, CA, and UniEnv variables passes through so host secrets
    and package-publishing credentials never leak to arbitrary tool runs.
    """
    path = [str(BIN)]
    for p in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin"):
        if Path(p).is_dir():
            path.append(p)
    path += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {k: v for k, v in os.environ.items() if k in _TOOL_ENV_ALLOWLIST}
    env.update(
        {"PATH": os.pathsep.join(path), "UNIENV_HOME": str(UNIENV_HOME), "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"}
    )
    tmp = os.environ.get("TMPDIR", "/tmp")
    if sys.platform == "darwin" and (tmp == "/tmp" or tmp.startswith("/tmp/")):
        tmp = "/private" + tmp  # Leptonica rewrites /tmp paths (see env.sh)
    env["TMPDIR"] = tmp
    return env


def install_env() -> dict[str, str]:
    """Environment for install_package: tool_env plus package-index and safe Homebrew settings."""
    env = tool_env()
    for k in _INSTALL_ENV_ALLOWLIST:
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def manifest() -> dict:
    try:
        return json.loads(MANIFEST_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def which(name: str) -> str | None:
    return shutil.which(name, path=tool_env()["PATH"])


_READONLY_TOOLS = {"unienv_capabilities", "read_document", "convert_document"}


def allowed_roots() -> list[Path] | None:
    """Return resolved allowed root directories when UNIENV_MCP_ROOTS is set, else None."""
    raw = os.environ.get("UNIENV_MCP_ROOTS", "").strip()
    if not raw:
        return None
    roots = [Path(os.path.expanduser(part.strip())).resolve() for part in raw.split(os.pathsep) if part.strip()]
    if not roots:
        raise ToolError("UNIENV_MCP_ROOTS must contain at least one directory")
    return roots


def check_path_allowed(path: Path) -> Path:
    """Resolve symlinks and verify path is inside UNIENV_MCP_ROOTS when configured."""
    roots = allowed_roots()
    if roots is None:
        return path
    resolved = path.resolve()
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise ToolError(f"path {path} is outside allowed UNIENV_MCP_ROOTS")
    return resolved


def active_tools() -> list[dict]:
    """Return tools enabled by the server's configured policy."""
    mode = os.environ.get("UNIENV_MCP_MODE", "").strip().lower()
    if mode == "readonly":
        return [t for t in TOOLS if t["name"] in _READONLY_TOOLS]
    disabled = set()
    # A cwd check cannot constrain arbitrary Python or command arguments.
    if os.environ.get("UNIENV_MCP_ROOTS", "").strip():
        disabled.update({"run_python", "run_tool", "install_package"})
    allow_install = os.environ.get("UNIENV_MCP_ALLOW_INSTALL", "1").strip().lower()
    if allow_install in ("0", "false", "no", "off"):
        disabled.add("install_package")
    return [t for t in TOOLS if t["name"] not in disabled]


def resolve(path: str, *, base: Path | None = None) -> Path:
    p = Path(os.path.expanduser(path))
    target = p if p.is_absolute() else (base or WORKDIR) / p
    return check_path_allowed(target)


def workdir(cwd: str | None) -> Path:
    d = resolve(cwd) if cwd else check_path_allowed(WORKDIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_CURRENT_CTX = threading.local()
_REQUEST_TOOLS: dict[object, str] = {}
_CANCELLED_IDS: set[object] = set()
_PROGRESS_INTERVAL = 10.0
_POLL_INTERVAL = 0.1


def _send(payload: dict | list) -> None:
    with _WRITE_LOCK:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _terminate_proc(proc: subprocess.Popen) -> None:
    """Stop the process group, allowing the launcher time to clean up first."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=3.0)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)


def _stop_and_drain(proc: subprocess.Popen, partial: str | bytes | None = None) -> str:
    """Bound pipe draining even if a detached descendant keeps a pipe open."""
    _terminate_proc(proc)
    out = partial.decode(errors="replace") if isinstance(partial, bytes) else (partial or "")
    try:
        drained, _ = proc.communicate(timeout=2.0)
        return drained or out
    except subprocess.TimeoutExpired as exc:
        if exc.stdout:
            out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        return out
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)


def cancel_request(request_id: object) -> bool:
    """Ignore unknown/completed IDs and installs, which must finish or roll back."""
    if request_id is None or not _valid_id(request_id):
        return False
    with _LOCK:
        name = _REQUEST_TOOLS.get(request_id)
        if name is None or name == "install_package":
            return False
        _CANCELLED_IDS.add(request_id)
    return True


def _is_cancelled() -> bool:
    req_id = getattr(_CURRENT_CTX, "request_id", None)
    with _LOCK:
        return req_id in _CANCELLED_IDS


def emit_progress(message: str) -> None:
    tok = getattr(_CURRENT_CTX, "progress_token", None)
    if tok is None or isinstance(tok, bool) or not isinstance(tok, (str, int)) or _is_cancelled():
        return
    value = getattr(_CURRENT_CTX, "progress_value", -1) + 1
    _CURRENT_CTX.progress_value = value
    _send(
        {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"progressToken": tok, "progress": value, "message": message},
        }
    )


def run(cmd: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None = None) -> tuple[int, str, str, float]:
    """Run a cancellable process group with periodic progress and bounded cleanup."""
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    start = time.monotonic()
    if _is_cancelled():
        return 130, "", "cancelled", 0.0
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=tool_env() if env is None else env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}", time.monotonic() - start

    next_progress = start + _PROGRESS_INTERVAL
    while True:
        if _is_cancelled():
            return 130, _stop_and_drain(proc), "cancelled", time.monotonic() - start
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            out = _stop_and_drain(proc)
            return (
                124,
                out,
                f"timed out after {timeout}s (raise timeout_s, max {MAX_TIMEOUT})",
                time.monotonic() - start,
            )
        try:
            out, err = proc.communicate(timeout=min(_POLL_INTERVAL, remaining))
            return proc.returncode, out, err, time.monotonic() - start
        except subprocess.TimeoutExpired as exc:
            if time.monotonic() >= next_progress:
                emit_progress(f"Waiting for {Path(cmd[0]).name}")
                next_progress = time.monotonic() + _PROGRESS_INTERVAL
            if time.monotonic() - start >= timeout:
                out = _stop_and_drain(proc, exc.stdout)
                return (
                    124,
                    out,
                    f"timed out after {timeout}s (raise timeout_s, max {MAX_TIMEOUT})",
                    time.monotonic() - start,
                )


def cap(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[… truncated {len(text) - limit:,} more characters]"


_SNAPSHOT_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}
_SNAPSHOT_MAX_DEPTH = 3
_SNAPSHOT_MAX_FILES = 2000
_SNAPSHOT_MAX_DIRS = 500


def snapshot(d: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    visited_dirs = 0

    def _walk(cur: Path, depth: int) -> None:
        nonlocal visited_dirs
        if len(out) >= _SNAPSHOT_MAX_FILES or visited_dirs >= _SNAPSHOT_MAX_DIRS:
            return
        visited_dirs += 1
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for p in entries:
            if len(out) >= _SNAPSHOT_MAX_FILES or visited_dirs >= _SNAPSHOT_MAX_DIRS:
                return
            name = p.name
            if name in _SNAPSHOT_SKIP_DIRS or name.startswith("."):
                continue
            try:
                if p.is_symlink():
                    continue
                if p.is_dir():
                    if depth < _SNAPSHOT_MAX_DEPTH:
                        _walk(p, depth + 1)
                elif p.is_file():
                    out[p.relative_to(d).as_posix()] = p.stat().st_mtime
            except OSError:
                continue

    _walk(d, 0)
    return out


def changed_files(d: Path, before: dict[str, float]) -> list[str]:
    after = snapshot(d)
    return sorted(str(d / n) for n, m in after.items() if before.get(n) != m)


def free_name(p: Path) -> Path:
    """Never overwrite: report.pdf -> report-1.pdf -> report-2.pdf ..."""
    if not os.path.lexists(p):
        return p
    for i in range(1, 1000):
        q = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not os.path.lexists(q):
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
    return "\n".join(
        [
            f"UniEnv {m.get('unienv', '?')} on {m.get('platform', '?')}",
            f"Working folder (default cwd, outputs land here): {WORKDIR}",
            f"Batteries: {', '.join(m.get('batteries', []))}",
            f"Python {m.get('python', {}).get('version', '')} packages (use run_python): {', '.join(py)}",
            f"Node packages (run_tool with tool='ue-node'): {', '.join(node) or '(none)'}",
            f"Tools (use run_tool): {', '.join(tools)}",
            "",
            "Usage notes (shell commands map to run_tool / run_python):",
            "\n\n".join(notes),
        ]
    )


def t_read_document(a: dict) -> str:
    path = resolve(a["path"])
    if not path.exists():
        raise ToolError(f"file not found: {path}")
    limit = max(1000, min(int(a.get("max_chars") or MAX_OUTPUT), 200_000))
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log", ".tsv", ".xml"}:
        return cap(path.read_text(errors="replace"), limit)
    if ext in IMAGE_EXT:
        imgs = _image_blocks([path], max_file_bytes=MAX_INLINE_IMAGE_BYTES) if a.get("include_image") else []
        if not which("tesseract"):
            if imgs:
                return _with_images(f"[Image {path.name}]", imgs)
            raise ToolError("OCR needs tesseract: install_package(name='tesseract', kind='brew')")
        code, out, err, _ = run(["tesseract", str(path), "-"], cwd=path.parent, timeout=120)
        if code != 0:
            if imgs:
                return _with_images(f"[Image {path.name}]", imgs)
            raise ToolError(f"tesseract failed: {err.strip()[:500]}")
        return _with_images(cap(f"[OCR text of {path.name}]\n{out}", limit), imgs)
    code, out, err, _ = run(
        [str(BIN / "ue-py"), *SAFE_PATH, "-m", "markitdown", str(path)], cwd=path.parent, timeout=180
    )
    if code == 0 and out.strip():
        return cap(out, limit)
    if ext == ".pdf" and which("pdftotext"):  # fallback for PDFs markitdown can't parse
        code2, out2, err2, _ = run(["pdftotext", "-layout", str(path), "-"], cwd=path.parent, timeout=180)
        if code2 == 0 and out2.strip():
            return cap(out2, limit)
        if code2 == 0:
            raise ToolError("the PDF has no text layer (scanned?). Render pages with run_tool pdftoppm, then OCR them.")
    raise ToolError(f"could not read {path.name}: {(err or 'no text extracted').strip()[:800]}")


# CSV options: token 9 preserves displayed values; token 12 exports every sheet.
# https://help.libreoffice.org/latest/en-US/text/shared/guide/csv_params.html
_CSV_ALL_SHEETS_FILTER = "csv:Text - txt - csv (StarCalc):44,34,UTF8,1,,0,false,true,true,false,false,-1"
_SPREADSHEET_EXT = {".xlsx", ".xls", ".ods"}


def t_convert_document(a: dict) -> str:
    src = resolve(a["input_path"])
    if not src.exists():
        raise ToolError(f"file not found: {src}")
    fmt = a["output_format"].lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]+", fmt):
        raise ToolError("output_format must be a file format such as pdf or docx")
    out_dir = resolve(a["output_dir"]) if a.get("output_dir") else src.parent
    if os.environ.get("UNIENV_MCP_MODE", "").strip().lower() == "readonly":
        if out_dir.resolve() not in {src.resolve().parent, WORKDIR.resolve()}:
            raise ToolError("readonly conversions must write next to the source or into UNIENV_WORKDIR")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = free_name(out_dir / f"{src.stem}.{fmt}")
    ext = src.suffix.lower()
    soffice = BIN / "soffice-safe"

    def via_soffice(input_path: Path, target_fmt: str) -> list[Path]:
        with tempfile.TemporaryDirectory(dir=tool_env()["TMPDIR"]) as tmp:
            code, out, err, _ = run(
                [str(soffice), "--convert-to", target_fmt, "--outdir", tmp, str(input_path)], cwd=Path(tmp), timeout=300
            )
            produced = sorted(Path(tmp).glob(f"*.{target_fmt.split(':')[0]}"))
            if code != 0 or not produced:
                raise ToolError(f"LibreOffice conversion failed: {(err or out).strip()[:800] or 'no output'}")
            if len(produced) == 1:
                shutil.move(str(produced[0]), dest)
                return [dest]
            moved = []
            for p in produced:
                target = free_name(out_dir / p.name)
                shutil.move(str(p), target)
                moved.append(target)
            return moved

    def format_converted(paths: list[Path]) -> str:
        if len(paths) == 1:
            return f"Converted to {paths[0]}"
        return f"Converted to {len(paths)} files:\n" + "\n".join(str(p) for p in paths)

    def via_pandoc(input_path: Path, to: str, target: Path) -> Path:
        safe_mode = bool(os.environ.get("UNIENV_MCP_ROOTS", "").strip()) or (
            os.environ.get("UNIENV_MCP_MODE", "").strip().lower() == "readonly"
        )
        cmd = ["pandoc", *(["--sandbox"] if safe_mode else []), str(input_path), "-t", to, "-o", str(target)]
        code, out, err, _ = run(cmd, cwd=input_path.parent, timeout=180)
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
            # render into a scratch folder, then move the pages into a fresh <stem>-pages folder,
            # so earlier renders are never overwritten and only this call's pages are listed
            with tempfile.TemporaryDirectory(dir=tool_env()["TMPDIR"]) as tmp:
                code, _, err, _ = run(
                    [
                        "pdftoppm",
                        f"-{'png' if fmt == 'png' else 'jpeg'}",
                        "-r",
                        "110",
                        str(src),
                        str(Path(tmp) / src.stem),
                    ],
                    cwd=src.parent,
                    timeout=300,
                )
                if code != 0:
                    raise ToolError(f"pdftoppm failed: {err.strip()[:500]}")
                pages_dir = free_name(out_dir / f"{src.stem}-pages")
                pages_dir.mkdir()
                pages = [Path(shutil.move(p, pages_dir / p.name)) for p in sorted(Path(tmp).iterdir())]
            summary = f"Rendered {len(pages)} page(s) into {pages_dir}:\n" + "\n".join(str(p) for p in pages)
            imgs = _image_blocks(pages, max_file_bytes=MAX_INLINE_IMAGE_BYTES) if a.get("include_images", True) else []
            return _with_images(summary, imgs)
        raise ToolError("PDF can be converted to txt, png or jpg. To get editable text, use read_document.")

    if fmt == "pdf":
        if ext in OFFICE_EXT:
            return format_converted(via_soffice(src, "pdf"))
        if ext in PANDOC_IN:  # pandoc → docx → LibreOffice → pdf (no LaTeX needed)
            with tempfile.TemporaryDirectory(dir=tool_env()["TMPDIR"]) as tmp:
                mid = via_pandoc(src, "docx", Path(tmp) / f"{src.stem}.docx")
                return format_converted(via_soffice(mid, "pdf"))
    if (
        ext in OFFICE_EXT
        and fmt in {"docx", "xlsx", "pptx", "odt", "ods", "odp", "csv", "html"}
        and not (ext == ".docx" and fmt in PANDOC_OUT)
    ):
        target_fmt = _CSV_ALL_SHEETS_FILTER if fmt == "csv" and ext in _SPREADSHEET_EXT else fmt
        return format_converted(via_soffice(src, target_fmt))
    if ext in PANDOC_IN and fmt in PANDOC_OUT:
        return f"Converted to {via_pandoc(src, PANDOC_OUT[fmt], dest)}"
    raise ToolError(
        f"don't know how to convert {ext or 'this file'} to {fmt}. "
        "Try run_python (python-docx/openpyxl/pandas) or run_tool with pandoc/soffice-safe."
    )


def t_run_python(a: dict) -> str | list[dict]:
    cwd = workdir(a.get("cwd"))
    before = snapshot(cwd)
    with tempfile.NamedTemporaryFile("w", suffix=".py", prefix="unienv_", dir=tool_env()["TMPDIR"], delete=False) as f:
        f.write(a["code"])
        script = f.name
    try:
        code, out, err, secs = run(
            [str(BIN / "ue-py"), *SAFE_PATH, script], cwd=cwd, timeout=a.get("timeout_s", DEFAULT_TIMEOUT)
        )
    finally:
        os.unlink(script)
    files = changed_files(cwd, before)
    report = _report(code, out, err, secs, cwd, before, files=files)
    if a.get("include_images", True):
        new_files = [Path(p) for p in files]
        imgs = _image_blocks([p for p in new_files if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
        return _with_images(report, imgs)
    return report


def allowed_tools() -> dict[str, str]:
    tools = {b: p for b, p in manifest().get("bins", {}).items() if p}
    tools["ue-node"] = str(BIN / "ue-node")
    tools.pop("uv", None)  # environment management goes through install_package
    return tools


def t_run_tool(a: dict) -> str:
    tools = allowed_tools()
    name = a["tool"]
    if name not in tools:
        raise ToolError(
            f"'{name}' is not a UniEnv tool. Available: {', '.join(sorted(tools))}. "
            "For anything else use run_python, or install_package to add a tool."
        )
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
    names = [] if any(c in a["name"] for c in "\r\n\t") else a["name"].split()
    # every token must look like a package spec: no leading "-", so a name can't smuggle in
    # CLI flags such as -y (non-interactive sudo) or --cask (a different kind than approved)
    if not names or not all(re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9._\-/@\[\],<>=!~*^]*", n) for n in names):
        raise ToolError(f"invalid package name: {a['name']!r}")
    code, out, err, secs = run(
        [str(BIN / "unienv"), "need", *flag, "--", *names], cwd=UNIENV_HOME, timeout=900, env=install_env()
    )
    text = (out + ("\n" + err if err.strip() else "")).strip()
    if code != 0:
        raise ToolError(cap(text or f"install failed (exit {code})", 4000))
    return cap(text, 4000)


def _report(
    code: int, out: str, err: str, secs: float, cwd: Path, before: dict, *, files: list[str] | None = None
) -> str:
    parts = [f"exit code: {code}  ({secs:.1f}s, cwd: {cwd})"]
    if files is None:
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
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "description": "File path, e.g. ~/Downloads/report.pdf"},
                "max_chars": {"type": "integer", "description": "Maximum characters to return (default 20000)"},
                "include_image": {
                    "type": "boolean",
                    "description": "When reading an image file, also return the image itself inline",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "fn": t_read_document,
    },
    {
        "name": "convert_document",
        "title": "Convert a document",
        "description": "Convert a local file to another format and save it next to the original (or in output_dir). "
        "Examples: docx/xlsx/pptx/md/html → pdf, md/html → docx, doc → docx, docx → md, pdf → txt/png "
        "(pages go into a new <name>-pages folder). Never overwrites: adds -1, -2 … to the name if "
        "needed. Returns the new file's path.",
        "inputSchema": {
            "type": "object",
            "required": ["input_path", "output_format"],
            "additionalProperties": False,
            "properties": {
                "input_path": {"type": "string"},
                "output_format": {"type": "string", "description": "pdf, docx, md, html, txt, xlsx, pptx, csv, png …"},
                "output_dir": {"type": "string", "description": "Optional folder for the result"},
                "include_images": {
                    "type": "boolean",
                    "description": "For PDF → PNG/JPG, attach up to 4 rendered page images inline (default true)",
                },
            },
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "fn": t_convert_document,
    },
    {
        "name": "run_python",
        "title": "Run Python",
        "description": "Run a Python 3.12 script with UniEnv's pre-installed packages (python-docx, openpyxl, "
        "python-pptx, pandas, matplotlib, pypdf, reportlab, duckdb …). Use it to create or edit Word/Excel/"
        "PowerPoint files, analyse data and make charts. Runs in the UniEnv working folder unless cwd is "
        "given; reports stdout/stderr and the files it created. Never pip install here: use install_package.",
        "inputSchema": {
            "type": "object",
            "required": ["code"],
            "additionalProperties": False,
            "properties": {
                "code": {"type": "string", "description": "Complete Python script"},
                "cwd": {"type": "string", "description": "Working directory (default: the UniEnv working folder)"},
                "timeout_s": {"type": "integer", "description": f"Default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}"},
                "include_images": {
                    "type": "boolean",
                    "description": "Attach newly created PNG/JPEG images inline (default true)",
                },
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
        "fn": t_run_python,
    },
    {
        "name": "run_tool",
        "title": "Run a UniEnv tool",
        "description": "Run one of UniEnv's command-line tools (pandoc, soffice-safe, pdftotext, pdftoppm, qpdf, gs, "
        "tesseract, duckdb, jq, rg, ffmpeg, magick, ue-node …) with a list of arguments. No shell: pass each "
        "argument as a separate list item. See unienv_capabilities for the exact list.",
        "inputSchema": {
            "type": "object",
            "required": ["tool"],
            "additionalProperties": False,
            "properties": {
                "tool": {"type": "string", "description": "Tool name, e.g. pandoc"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Arguments, e.g. ["in.md", "-o", "out.docx"]',
                },
                "cwd": {"type": "string"},
                "timeout_s": {"type": "integer"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        "fn": t_run_tool,
    },
    {
        "name": "install_package",
        "title": "Install a package permanently",
        "description": "Permanently add a missing package to UniEnv so every AI tool on this computer can use it: "
        "kind=python (PyPI, default), node (npm), brew (Homebrew formula, e.g. ffmpeg) or cask (macOS app).",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "description": "Package name, e.g. seaborn"},
                "kind": {"type": "string", "enum": ["python", "node", "brew", "cask"]},
            },
        },
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


def _invalid_request(mid=None) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "Invalid Request"}}


def _valid_id(val) -> bool:
    return val is None or isinstance(val, str) or (isinstance(val, (int, float)) and not isinstance(val, bool))


def handle(msg: dict) -> dict | None:
    has_id = "id" in msg
    raw_id = msg.get("id")
    mid = raw_id if has_id and _valid_id(raw_id) else None
    if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _invalid_request(mid)
    if has_id and not _valid_id(raw_id):
        return _invalid_request(None)
    if not has_id:  # notification (initialized, cancelled, ...): nothing to send back
        if msg.get("method") == "notifications/cancelled":
            raw_params = msg.get("params")
            if isinstance(raw_params, dict) and "requestId" in raw_params:
                cancel_request(raw_params["requestId"])
        return None
    method = msg["method"]

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def error(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    raw_params = msg.get("params")
    if raw_params is None:
        params = {}
    elif isinstance(raw_params, dict):
        params = raw_params
    else:
        return error(-32602, "params must be an object")

    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return ok(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "UniEnv AI", "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{k: v for k, v in t.items() if k != "fn"} for t in active_tools()]})
    if method == "tools/call":
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return error(-32602, "tool name must be a string")
        enabled = {t["name"]: t for t in active_tools()}
        tool = enabled.get(tool_name)
        if tool is None:
            if tool_name in TOOL_INDEX:
                return ok(
                    {
                        "content": [{"type": "text", "text": f"tool '{tool_name}' is disabled by MCP safe mode"}],
                        "isError": True,
                    }
                )
            return error(-32602, f"unknown tool: {tool_name}")
        raw_args = params.get("arguments")
        if raw_args is None:
            args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            return error(-32602, "arguments must be an object")
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        if missing:
            return error(-32602, f"missing required argument(s): {', '.join(missing)}")
        meta = params.get("_meta")
        progress_token = meta.get("progressToken") if isinstance(meta, dict) else None
        _CURRENT_CTX.request_id = mid
        _CURRENT_CTX.progress_token = progress_token
        _CURRENT_CTX.progress_value = -1
        with _LOCK:
            _REQUEST_TOOLS.setdefault(mid, tool_name)
        start = time.monotonic()
        try:
            with _LOCK:
                pre_cancelled = mid is not None and mid in _CANCELLED_IDS
            if pre_cancelled:
                raise ToolError("cancelled")
            emit_progress(f"Starting {tool['name']}")
            res, is_error = tool["fn"](args), False
            emit_progress(f"Completed {tool['name']}")
        except ToolError as e:
            res, is_error = str(e), True
        except Exception as e:  # never crash the server on a bad call
            res, is_error = f"{type(e).__name__}: {e}", True
        finally:
            _CURRENT_CTX.request_id = None
            _CURRENT_CTX.progress_token = None
        with _LOCK:
            cancelled = mid in _CANCELLED_IDS
            _CANCELLED_IDS.discard(mid)
            _REQUEST_TOOLS.pop(mid, None)
        if cancelled:
            return None
        log(f"{tool['name']} {'failed' if is_error else 'ok'} in {time.monotonic() - start:.1f}s")
        content = res if isinstance(res, list) else [{"type": "text", "text": res}]
        return ok({"content": content, "isError": is_error})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return error(-32601, f"method not found: {method}")


def _safe_handle(msg) -> dict | None:
    if not isinstance(msg, dict):
        return _invalid_request()
    try:
        return handle(msg)
    except Exception as e:
        log(f"internal error handling {msg.get('method')!r}: {type(e).__name__}: {e}")
        if "id" not in msg:
            return None
        raw_id = msg["id"]
        mid = raw_id if _valid_id(raw_id) else None
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"},
        }


class _RawReply:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


def _dispatch_message(msg) -> dict | list | None:
    if isinstance(msg, _RawReply):
        return msg.payload
    if isinstance(msg, list):
        if not msg:
            return _invalid_request()
        replies = [r for r in (_dispatch_message(m) for m in msg) if r is not None]
        return replies or None
    try:
        reply = _safe_handle(msg)
        if isinstance(msg, dict) and _valid_id(msg.get("id")):
            with _LOCK:
                if msg.get("id") in _CANCELLED_IDS:
                    return None
        return reply
    finally:
        if isinstance(msg, dict) and msg.get("method") == "tools/call" and _valid_id(msg.get("id")):
            with _LOCK:
                _REQUEST_TOOLS.pop(msg.get("id"), None)
                _CANCELLED_IDS.discard(msg.get("id"))


def _register_requests(msg) -> None:
    """Register queued calls before the reader can receive their cancellation."""
    if isinstance(msg, list):
        for item in msg:
            _register_requests(item)
    elif (
        isinstance(msg, dict)
        and msg.get("jsonrpc") == "2.0"
        and msg.get("method") == "tools/call"
        and "id" in msg
        and _valid_id(msg["id"])
    ):
        params = msg.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if isinstance(name, str):
            with _LOCK:
                _REQUEST_TOOLS.setdefault(msg["id"], name)


def serve() -> int:
    log(f"started (UniEnv home {UNIENV_HOME}, working folder {WORKDIR})")
    work_q: queue.Queue = queue.Queue()
    stop = object()

    def _worker() -> None:
        while True:
            item = work_q.get()
            if item is stop:
                work_q.task_done()
                break
            try:
                reply = _dispatch_message(item)
                if reply:
                    _send(reply)
            finally:
                work_q.task_done()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            work_q.put(_RawReply({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}))
            continue
        if isinstance(msg, dict) and "id" not in msg and msg.get("method") == "notifications/cancelled":
            _safe_handle(msg)
            continue
        with _LOCK:
            busy = bool(_REQUEST_TOOLS)
        if (
            busy
            and isinstance(msg, dict)
            and msg.get("jsonrpc") == "2.0"
            and msg.get("method") == "ping"
            and "id" in msg
            and _valid_id(msg.get("id"))
        ):
            reply = _safe_handle(msg)
            if reply:
                _send(reply)
            continue
        _register_requests(msg)
        work_q.put(msg)

    work_q.put(stop)
    worker.join()
    return 0


if __name__ == "__main__":
    sys.exit(serve())

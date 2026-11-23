#!/usr/bin/env python3
"""UniEnv AI: one always-ready environment shared by every AI tool.

Installs a curated set of tools once into a stable prefix (~/.unienv), wires it
into every shell mode AI agents use, and tells each agent (Claude Code, Codex,
Gemini CLI, OpenCode, ...) what is installed and how to use it.

Standard library only (Python >= 3.11), so the CLI keeps working even when the
environment it manages is broken. Run `unienv --help`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import difflib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path

VERSION = "0.3.0"
PY_VERSION = "3.12"

HOME = Path.home()
UNIENV_HOME = Path(os.environ.get("UNIENV_HOME", HOME / ".unienv")).expanduser()
ROOT = Path(__file__).resolve().parent.parent  # repo checkout, or ~/.unienv/share
RECIPES_DIR = ROOT / "recipes"
TEMPLATES_DIR = ROOT / "templates"
SHIMS_DIR = ROOT / "shims"

BIN = UNIENV_HOME / "bin"
PY_DIR = UNIENV_HOME / "py"
NODE_DIR = UNIENV_HOME / "node"
STATE_FILE = UNIENV_HOME / "state.json"
MANIFEST_FILE = UNIENV_HOME / "manifest.json"
ENV_SH = UNIENV_HOME / "env.sh"
AGENTS_COPY = UNIENV_HOME / "AGENTS.md"
LOG_FILE = UNIENV_HOME / "log" / "unienv.log"
BACKUP_DIR = UNIENV_HOME / "backups"

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
EXTRA_DIRS = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    str(HOME / ".local/bin"),
    str(HOME / ".cargo/bin"),
    *(
        [
            str(HOME / "scoop/shims"),
            str(Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local")) / "Microsoft/WinGet/Links"),
        ]
        if IS_WIN
        else []
    ),
]

SHELL_BEGIN, SHELL_END = "# >>> unienv >>>", "# <<< unienv <<<"
MD_BEGIN, MD_END = "<!-- >>> unienv (managed block, do not edit) >>> -->", "<!-- <<< unienv <<< -->"
LAUNCH_AGENT = HOME / "Library/LaunchAgents/ai.unienv.path.plist"
SHELL_FILES = [HOME / f for f in (".zshenv", ".profile", ".bashrc", ".bash_profile", ".bash_login")]

# ── output ──────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def info(msg: str) -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(_c("1;36", "==> ") + _c("1", msg), flush=True)


def warn(msg: str) -> None:
    print(_c("33", "! ") + msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(_c("31", "✗ ") + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def log_event(**fields) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields["ts"] = _dt.datetime.now().isoformat(timespec="seconds")
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(fields) + "\n")


# ── helpers ─────────────────────────────────────────────────────────────────


def search_path() -> str:
    """PATH used to locate tools: ours first, then common install dirs, then the caller's."""
    parts = [str(BIN)] + EXTRA_DIRS + os.environ.get("PATH", "").split(os.pathsep)
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


def which(name: str) -> str | None:
    return shutil.which(name, path=search_path())


def libreoffice_path() -> str | None:
    if IS_MAC:
        app = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if os.access(app, os.X_OK):
            return app
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files (x86)"):
            exe = Path(base) / "LibreOffice/program/soffice.exe"
            if exe.exists():
                return str(exe)
    return shutil.which("soffice", path=search_path()) or shutil.which("libreoffice", path=search_path())


def venv_python() -> Path:
    return PY_DIR / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")


def resolve_bin(name: str) -> str | None:
    """Like which(), but wrapper shims only count if the tool they wrap exists."""
    if name == "soffice-safe":
        return libreoffice_path() and which(name)
    return which(name)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    check: bool = True,
    capture: bool = False,
    quiet: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    if not quiet and not capture:
        info(_c("2", "  $ " + " ".join(cmd)))
    full_env = {**os.environ, "PATH": search_path(), **(env or {})}
    try:
        r = subprocess.run(cmd, cwd=cwd, env=full_env, text=True, timeout=timeout, capture_output=capture)
    except FileNotFoundError:
        if check:
            die(f"command not found: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 127, "", "not found")
    except subprocess.TimeoutExpired:
        if check:
            die(f"timed out: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    if check and r.returncode != 0:
        if capture:
            sys.stderr.write((r.stdout or "") + (r.stderr or ""))
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return r


def norm(name: str) -> str:
    """Normalise a Python distribution name (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def py_name(spec: str) -> str:
    return norm(re.split(r"[\s<>=!~\[;@]", spec.strip(), maxsplit=1)[0])


def node_name(spec: str) -> str:
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        return "@" + scope + "/" + rest.split("@", 1)[0]
    return spec.split("@", 1)[0]


def node_version(spec: str) -> str:
    name = node_name(spec)
    rest = spec[len(name) :]
    return rest[1:] if rest.startswith("@") and len(rest) > 1 else "latest"


def uniq(items) -> list:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _os_release_info(path: Path = Path("/etc/os-release")) -> tuple[str, set[str]]:
    if not path.exists():
        return "", set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", set()
    distro_id = ""
    tokens: set[str] = set()
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.strip().partition("=")
        vals = v.strip("\"'").lower().split()
        if k == "ID" and vals:
            distro_id = vals[0]
            tokens.add(distro_id)
        elif k == "ID_LIKE":
            tokens.update(vals)
    return distro_id, tokens


def _os_release_tokens(path: Path = Path("/etc/os-release")) -> set[str]:
    return _os_release_info(path)[1]


def platform_key() -> str:
    if IS_MAC:
        return "macos"
    if IS_WIN:
        return "windows"
    if Path("/etc/debian_version").exists():
        return "debian"
    distro_id, tokens = _os_release_info()
    if tokens & {"debian", "ubuntu"}:
        return "debian"
    if distro_id == "fedora" or Path("/etc/fedora-release").exists():
        return "fedora"
    if tokens & {"arch", "manjaro", "endeavouros"} or Path("/etc/arch-release").exists():
        return "arch"
    if which("pacman"):
        return "arch"
    return sys.platform


def _read_block_text(path: Path) -> tuple[str, str]:
    """Read a text file as normalized `\\n` text plus its dominant line ending."""
    with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        raw = f.read()
    crlf = raw.count("\r\n")
    cr = raw.count("\r") - crlf
    lf = raw.count("\n") - crlf
    if crlf == 0 and cr == 0 and lf == 0:
        eol = os.linesep
    elif crlf >= cr and crlf >= lf:
        eol = "\r\n"
    elif cr >= lf:
        eol = "\r"
    else:
        eol = "\n"
    return raw.replace("\r\n", "\n").replace("\r", "\n"), eol


def _write_block_text(path: Path, text: str, eol: str) -> None:
    """Write normalized `\\n` text using the file's own line ending, without OS translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text.replace("\n", eol))


def _block_layout_marker(begin: str) -> str:
    return "# unienv:restore-no-final-newline" if begin.startswith("#") else "<!-- unienv:restore-no-final-newline -->"


def _has_malformed_block(text: str, begin: str, end: str) -> bool:
    """True when begin/end markers are unbalanced or orphaned."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    in_block = False
    for line in normalized.splitlines():
        if line.strip() == begin:
            if in_block:
                return True
            in_block = True
        elif line.strip() == end:
            if not in_block:
                return True
            in_block = False
    return in_block


def upsert_block(path: Path, body: str, begin: str, end: str, *, top: bool = False) -> bool:
    """Insert or replace a block without losing the surrounding file's layout."""
    old, eol = _read_block_text(path) if path.exists() else ("", os.linesep)
    if _has_malformed_block(old, begin, end):
        warn(
            f"{path} has an incomplete UniEnv block (missing end marker); fix or remove the orphaned marker line first"
        )
        return False
    pattern = re.compile(re.escape(begin) + r"\n.*?" + re.escape(end) + r"(?:\n|$)", re.S)
    match = pattern.search(old)
    marker = _block_layout_marker(begin)
    restore_eof = marker in match.group(0).splitlines() if match else bool(old and not top and not old.endswith("\n"))
    prefix = marker + "\n" if restore_eof else ""
    block = f"{begin}\n{prefix}{body.rstrip()}\n{end}\n"
    if match:
        new = pattern.sub(lambda _: block, old, count=1)
    elif top:
        new = block + old
    else:
        new = old + ("\n" if restore_eof else "") + block
    if new == old:
        return False
    _write_block_text(path, new, eol)
    return True


def remove_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    old, eol = _read_block_text(path)
    if _has_malformed_block(old, begin, end):
        warn(
            f"{path} has an incomplete UniEnv block (missing end marker); fix or remove the orphaned marker line first"
        )
        return False
    pattern = re.compile(re.escape(begin) + r"\n.*?" + re.escape(end) + r"(?:\n|$)", re.S)
    new = old
    for match in reversed(list(pattern.finditer(old))):
        start = match.start()
        if (
            _block_layout_marker(begin) in match.group(0).splitlines()
            and match.end() == len(old)
            and start
            and old[start - 1] == "\n"
        ):
            start -= 1
        new = new[:start] + new[match.end() :]
    if new == old:
        return False
    _write_block_text(path, new, eol)
    return True


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename, so readers (agents, shells) never see a half-written file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def state_lock():
    """Serialise commands that change ~/.unienv: parallel agents often run `unienv need` at once."""
    UNIENV_HOME.mkdir(parents=True, exist_ok=True)
    # Keep the lock outside the install tree on Windows so `--purge` can remove UNIENV_HOME while locked.
    lock_path = UNIENV_HOME.parent / ("." + UNIENV_HOME.name + ".lock") if IS_WIN else UNIENV_HOME / ".lock"
    with open(lock_path, "a+b") as f:
        if IS_WIN:
            import msvcrt

            f.seek(0, 2)
            if not f.tell():
                f.write(b"0")
                f.flush()
            while True:
                f.seek(0)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("waiting for another unienv command to finish…", file=sys.stderr, flush=True)
                fcntl.flock(f, fcntl.LOCK_EX)
            yield


def read_block(path: Path, begin: str, end: str) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if _has_malformed_block(text, begin, end):
        return None
    m = re.search(re.escape(begin) + r"\n(.*?)\n" + re.escape(end), text, re.S)
    if not m:
        return None
    body = m.group(1)
    prefix = _block_layout_marker(begin) + "\n"
    return body[len(prefix) :] if body.startswith(prefix) else body


# ── recipes & state ─────────────────────────────────────────────────────────


def load_recipes() -> dict[str, dict]:
    recipes = {}
    for p in sorted(RECIPES_DIR.glob("*.toml")):
        with p.open("rb") as f:
            r = tomllib.load(f)
        problems = validate_recipe(r, p.stem)
        if problems:
            die(f"invalid recipe {p.name}: " + "; ".join(problems))
        recipes[r["id"]] = r
    return recipes


def validate_recipe(r: dict, stem: str) -> list[str]:
    problems = []
    if r.get("schema") != "unienv/recipe/v0":
        problems.append('schema must be "unienv/recipe/v0"')
    if r.get("id") != stem:
        problems.append(f"id must match file name ({stem!r})")
    if not r.get("summary"):
        problems.append("summary is required")
    for section, key in [
        ("python", "packages"),
        ("python", "imports"),
        ("node", "packages"),
        ("node", "modules"),
        ("verify", "bins"),
    ]:
        v = r.get(section, {}).get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            problems.append(f"[{section}].{key} must be a list of strings")
    sys_sec = r.get("system", {})
    allowed_system = {
        "macos": {"brew", "cask"},
        "debian": {"apt"},
        "windows": {"winget", "scoop"},
        "fedora": {"dnf"},
        "arch": {"pacman"},
    }
    if not isinstance(sys_sec, dict):
        problems.append("[system] must be a table")
    else:
        for pk, pval in sys_sec.items():
            if pk not in allowed_system:
                problems.append(f"unknown [system.{pk}] platform")
                continue
            if not isinstance(pval, dict):
                problems.append(f"[system.{pk}] must be a table")
                continue
            for mgr, pkgs in pval.items():
                if mgr not in allowed_system[pk]:
                    problems.append(f"unknown [system.{pk}].{mgr} key")
                elif not isinstance(pkgs, list) or not all(isinstance(x, str) for x in pkgs):
                    problems.append(f"[system.{pk}].{mgr} must be a list of strings")
    return problems


def default_state(recipes: dict) -> dict:
    return {
        "version": VERSION,
        "batteries": [k for k, r in recipes.items() if r.get("default") and not r.get("always")],
        "extras": {k: [] for k in ("python", "node", "brew", "cask", "apt", "winget", "scoop", "dnf", "pacman")},
        "options": {"wire_homebrew": True, "wire_shells": True, "exclude": []},
        "wired_shell_files": [],
    }


def load_state(recipes: dict) -> dict:
    state = default_state(recipes)
    sf = STATE_FILE
    if sf.exists():
        try:
            saved = json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError):
            backups = sorted(BACKUP_DIR.glob("*/*__state.json"))
            warn(f"{sf} is corrupt; " + (f"using the backup {backups[-1]}" if backups else "using defaults"))
            try:
                saved = json.loads(backups[-1].read_text()) if backups else {}
            except (json.JSONDecodeError, OSError):
                saved = {}
        if not isinstance(saved, dict):
            saved = {}
        state.update({k: v for k, v in saved.items() if k in state and k != "options"})
        if isinstance(saved.get("options"), dict):
            state["options"].update(saved.get("options", {}))
        if not isinstance(state.get("wired_shell_files"), list):
            state["wired_shell_files"] = []
        for k in default_state(recipes)["extras"]:
            state["extras"].setdefault(k, [])
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = VERSION
    atomic_write(STATE_FILE, json.dumps(state, indent=2) + "\n")


def active_recipes(recipes: dict, state: dict) -> list[dict]:
    ids = [k for k, r in recipes.items() if r.get("always")] + state["batteries"]
    missing = [i for i in ids if i not in recipes]
    if missing:
        warn(f"unknown batteries ignored: {', '.join(missing)}")
    return [recipes[i] for i in uniq(ids) if i in recipes]


def wanted(recipes: list[dict], state: dict) -> dict[str, list[str]]:
    pk = platform_key()
    excluded = set(state["options"].get("exclude", []))

    def system(kind: str) -> list[str]:
        pkgs = [p for r in recipes for p in r.get("system", {}).get(pk, {}).get(kind, [])]
        # "libreoffice" also excludes apt's "libreoffice-writer", "libreoffice-calc", ...
        # and winget's "TheDocumentFoundation.LibreOffice" (case-insensitive tail/prefix match)
        return [
            p
            for p in uniq(pkgs + state.get("extras", {}).get(kind, []))
            if not any(
                p.lower() == x.lower() or p.lower().startswith(x.lower() + "-") or p.lower().endswith("." + x.lower())
                for x in excluded
            )
        ]

    bins, skipped = [], []
    for r in recipes:
        provided_by = r.get("verify", {}).get("provided_by", {})
        for b in r.get("verify", {}).get("bins", []):
            (skipped if provided_by.get(b) in excluded else bins).append(b)
    return {
        "python": uniq(
            [p for r in recipes for p in r.get("python", {}).get("packages", [])] + state["extras"]["python"]
        ),
        "node": uniq([p for r in recipes for p in r.get("node", {}).get("packages", [])] + state["extras"]["node"]),
        "brew": system("brew"),
        "cask": system("cask"),
        "apt": system("apt"),
        "winget": system("winget"),
        "scoop": system("scoop"),
        "dnf": system("dnf"),
        "pacman": system("pacman"),
        "bins": uniq(bins),
        "skipped_bins": uniq(skipped),
    }


def _apt_installed(pkg: str) -> bool:
    """True only when dpkg reports the package as fully installed.

    `dpkg -s` also exits 0 for a package that was removed but not purged
    (`deinstall ok config-files`), which would wrongly skip a reinstall.
    """
    r = run(["dpkg-query", "-W", "-f=${Status}", pkg], capture=True, check=False)
    return r.stdout.strip() == "install ok installed"


def _winget_installed(winget: str, pkg: str) -> bool:
    r = run([winget, "list", "--exact", "--id", pkg, "--accept-source-agreements"], capture=True, check=False)
    return r.returncode == 0 and pkg.lower() in (r.stdout or "").lower()


def _dnf_installed(pkg: str) -> bool:
    return run(["rpm", "-q", "--whatprovides", pkg], capture=True, check=False).returncode == 0


def _pacman_installed(pkg: str) -> bool:
    return run(["pacman", "-Qi", pkg], capture=True, check=False).returncode == 0


# ── installers ──────────────────────────────────────────────────────────────


def install_system(w: dict, *, assume_yes: bool) -> list[str]:
    """Install system packages. Returns a list of failures."""
    failures: list[str] = []
    pk = platform_key()
    if pk == "macos":
        brew = which("brew")
        if not brew:
            warn("Homebrew not found. Install it from https://brew.sh, then run `unienv install` again.")
            return ["homebrew"]
        # never let our installs trigger `brew cleanup` of the user's other caches
        env = {"HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1"}
        have_f = set(run([brew, "list", "--formula", "-1"], capture=True, env=env).stdout.split())
        have_c = set(run([brew, "list", "--cask", "-1"], capture=True, env=env).stdout.split())
        formulas = [f for f in w["brew"] if f.split("/")[-1] not in have_f]
        casks = [c for c in w["cask"] if c.split("/")[-1] not in have_c and not _cask_present(c)]
        if formulas:
            r = run([brew, "install", *formulas], env=env, check=False)
            if r.returncode != 0:
                # retry one by one so a single bad formula doesn't block the rest
                for f in formulas:
                    if run([brew, "install", f], env=env, check=False, quiet=True).returncode != 0:
                        failures.append(f"brew:{f}")
        for c in casks:
            if run([brew, "install", "--cask", c], env=env, check=False).returncode != 0:
                failures.append(f"cask:{c}")
        if not formulas and not casks:
            info("  all system packages already installed")
    elif pk in ("debian", "fedora", "arch"):
        mgr_cfg = {
            "debian": ("apt", _apt_installed, ["sudo", "apt-get", "install", "-y"]),
            "fedora": ("dnf", _dnf_installed, ["sudo", "dnf", "install", "-y"]),
            "arch": ("pacman", _pacman_installed, ["sudo", "pacman", "-S", "--noconfirm", "--needed"]),
        }
        kind, is_installed, base_cmd = mgr_cfg[pk]
        pkgs = [p for p in w.get(kind, []) if not is_installed(p)]
        if pkgs:
            cmd = [*base_cmd, *pkgs]
            if assume_yes or os.geteuid() == 0:
                if os.geteuid() == 0:
                    cmd = cmd[1:]
                if run(cmd, check=False).returncode != 0:
                    if pk == "fedora":
                        prefix = cmd[: -len(pkgs)]
                        for package in pkgs:
                            if run([*prefix, package], check=False).returncode != 0:
                                failures.append(f"{kind}:{package}")
                    else:
                        failures.extend(f"{kind}:{p}" for p in pkgs)
            else:
                warn("System packages needed. Run this, then `unienv install` again:\n    " + " ".join(cmd))
                failures.extend(f"{kind}:{p}" for p in pkgs)
        else:
            info("  all system packages already installed")
    elif pk == "windows":
        scoop = which("scoop")
        winget = which("winget")
        if scoop and w.get("scoop"):
            have_s = {
                line.split()[0].lower()
                for line in run([scoop, "list"], capture=True, check=False).stdout.splitlines()
                if line.strip() and not line.strip().startswith(("Installed", "---"))
            }
            pkgs = [p for p in w["scoop"] if p.split("/")[-1].lower() not in have_s]
            if "libreoffice" in pkgs:
                buckets = run([scoop, "bucket", "list"], capture=True, check=False)
                has_extras = any(line.split()[0] == "extras" for line in buckets.stdout.splitlines() if line.strip())
                bucket_failed = not has_extras and run([scoop, "bucket", "add", "extras"], check=False).returncode
                if bucket_failed:
                    failures.append("scoop:extras")
                    pkgs = [p for p in pkgs if p != "libreoffice"]
            for p in pkgs:
                if run([scoop, "install", p], check=False).returncode != 0:
                    failures.append(f"scoop:{p}")
            if not pkgs:
                info("  all system packages already installed")
        elif winget and w.get("winget"):
            pkgs = [p for p in w["winget"] if not _winget_installed(winget, p)]
            for p in pkgs:
                cmd = [
                    winget,
                    "install",
                    "--exact",
                    "--id",
                    p,
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
                if run(cmd, check=False).returncode != 0:
                    failures.append(f"winget:{p}")
            if not pkgs:
                info("  all system packages already installed")
        elif w.get("winget") or w.get("scoop"):
            warn("Neither winget nor Scoop found. Install Scoop (https://scoop.sh) or App Installer (winget).")
            failures.extend(f"windows:{p}" for p in (w.get("winget") or w.get("scoop") or []))
    else:
        req = uniq(w.get("brew", []) + w.get("cask", []) + w.get("apt", []))
        if not req:
            return []
        hint = " ".join(req) if req else "<packages>"
        warn(
            f"No system package backend for {pk} yet; install these yourself "
            f"(e.g. `sudo dnf install {hint}` or `sudo pacman -S {hint}`): {', '.join(req)}"
        )
        return [f"{pk}:{b}" for b in req]
    return failures


def _cask_present(cask: str) -> bool:
    apps = {"libreoffice": "/Applications/LibreOffice.app"}
    return bool(apps.get(cask)) and Path(apps[cask]).exists()


def find_uv() -> str:
    uv = which("uv")
    if not uv:
        die("uv not found. Install it (https://docs.astral.sh/uv/) or run ./install.sh")
    return uv


def write_pyproject(packages: list[str]) -> None:
    PY_DIR.mkdir(parents=True, exist_ok=True)
    deps = "".join(f"  {json.dumps(p)},\n" for p in packages)
    # The env lives on one machine: resolve only for this platform. It is faster,
    # and it can't fail on wheels that only exist for other platforms.
    marker = f"sys_platform == '{sys.platform}' and platform_machine == '{platform.machine()}'"
    (PY_DIR / "pyproject.toml").write_text(
        "# Generated by `unienv`; edit recipes or use `unienv need` instead.\n"
        "[project]\n"
        'name = "unienv-batteries"\n'
        'version = "0"\n'
        f'requires-python = "=={PY_VERSION}.*"\n'
        f"dependencies = [\n{deps}]\n\n"
        "[tool.uv]\n"
        "package = false\n"
        f'environments = ["{marker}"]\n'
    )
    (PY_DIR / ".python-version").write_text(PY_VERSION + "\n")


def install_python(packages: list[str]) -> None:
    uv = find_uv()
    write_pyproject(packages)
    run(
        [uv, "sync", "--project", str(PY_DIR), "--managed-python", "--quiet"],
        env={"UV_PROJECT_ENVIRONMENT": str(PY_DIR / ".venv")},
    )


def install_browsers(*, assume_yes: bool = False, no_system: bool = False) -> None:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if custom == "0":
        browsers_env = "0"
    elif custom:
        p = Path(custom).expanduser()
        if p.is_absolute():
            p.mkdir(parents=True, exist_ok=True)
        browsers_env = str(p)
    else:
        browsers_dir = UNIENV_HOME / "browsers"
        browsers_dir.mkdir(parents=True, exist_ok=True)
        browsers_env = str(browsers_dir)
    py = PY_DIR / ".venv/bin/python"
    if not py.exists():
        return
    r = run(
        [str(py), "-P", "-m", "playwright", "install", "chromium"],
        env={"PLAYWRIGHT_BROWSERS_PATH": browsers_env},
        check=False,
    )
    if r.returncode != 0:
        warn(
            f"playwright browser download failed (exit {r.returncode}); "
            f"run `PLAYWRIGHT_BROWSERS_PATH={browsers_env} ue-py -m playwright install chromium`"
        )
    if not no_system and platform_key() == "debian":
        deps_cmd = [str(py), "-P", "-m", "playwright", "install-deps", "chromium"]
        if assume_yes or os.geteuid() == 0:
            cmd = deps_cmd if os.geteuid() == 0 else ["sudo", *deps_cmd]
            if run(cmd, check=False).returncode != 0:
                warn("playwright install-deps failed; run: " + " ".join(cmd))
        else:
            warned_marker = UNIENV_HOME / "browsers" / ".deps-hint-shown"
            if not warned_marker.exists():
                warn("Browser system libraries needed on Linux. Run:\n    sudo " + " ".join(deps_cmd))
                with contextlib.suppress(OSError):
                    warned_marker.parent.mkdir(parents=True, exist_ok=True)
                    warned_marker.write_text("1\n")


def write_package_json(packages: list[str]) -> None:
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    pj = NODE_DIR / "package.json"
    data = {
        "name": "unienv-node",
        "private": True,
        "description": "Generated by unienv",
        "dependencies": {node_name(p): node_version(p) for p in packages},
    }
    if pj.exists():  # keep resolved ranges npm already wrote for unchanged packages
        old = json.loads(pj.read_text()).get("dependencies", {})
        for k, v in data["dependencies"].items():
            if v == "latest" and k in old:
                data["dependencies"][k] = old[k]
    pj.write_text(json.dumps(data, indent=2) + "\n")


def _uses_puppeteer(packages: list[str]) -> bool:
    return any(node_name(p) in ("@mermaid-js/mermaid-cli", "puppeteer", "puppeteer-core") for p in packages)


def _puppeteer_cache_env(packages: list[str]) -> dict[str, str] | None:
    if not _uses_puppeteer(packages):
        return None
    cache_dir = os.environ.get("PUPPETEER_CACHE_DIR") or str(UNIENV_HOME / "cache" / "puppeteer")
    return {"PUPPETEER_CACHE_DIR": cache_dir}


def install_node(packages: list[str], *, require_npm: bool = False) -> None:
    if not packages:
        return
    npm = which("npm")
    if not npm:
        if require_npm:
            raise RuntimeError("npm not found: install Node.js (`unienv need --bin node`)")
        warn("npm not found; skipping Node batteries (install Node.js, then `unienv install`).")
        return
    write_package_json(packages)
    run(
        [npm, "install", "--no-audit", "--no-fund", "--loglevel=error", "--progress=false", "--no-update-notifier"],
        cwd=NODE_DIR,
        env=_puppeteer_cache_env(packages),
    )


# ── generated files: shims, env.sh, manifest, agent files ───────────────────


def prune_backups(keep: int = 10, min_age_days: int = 7) -> list[Path]:
    """Keep the `keep` most recent backup snapshots and any newer than `min_age_days`; delete older ones."""
    if not BACKUP_DIR.exists():
        return []
    snaps = sorted([p for p in BACKUP_DIR.iterdir() if p.is_dir()], key=lambda p: p.name)
    keep_set = set(snaps[-keep:]) if keep > 0 else set()
    cutoff = time.time() - (min_age_days * 86400)
    removed = []
    for s in snaps:
        if s in keep_set:
            continue
        try:
            ts = _dt.datetime.strptime(s.name[:15], "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            try:
                ts = s.stat().st_mtime
            except OSError:
                continue
        if ts < cutoff:
            shutil.rmtree(s, ignore_errors=True)
            removed.append(s)
    return removed


def write_shims() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    node = which("node") or "node"
    tracker = BIN / ".shims"
    prev_shims: set[str] = set()
    if tracker.exists():
        with contextlib.suppress(OSError):
            prev_shims = {ln.strip() for ln in tracker.read_text().splitlines() if ln.strip()}
    else:
        # Older installs have no tracker. Only adopt files carrying our own header.
        for candidate in BIN.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            with contextlib.suppress(OSError, UnicodeError):
                with candidate.open(encoding="utf-8") as stream:
                    if "# Generated by `unienv sync`" in stream.read(1024):
                        prev_shims.add(candidate.name)

    current_shims: list[str] = []
    for tpl in sorted(SHIMS_DIR.iterdir()):
        if tpl.name.startswith("."):
            continue
        text = tpl.read_text().replace("@UNIENV_HOME@", str(UNIENV_HOME)).replace("@NODE@", node)
        dst = BIN / tpl.name
        dst.write_text(text, newline="")
        dst.chmod(0o755)
        current_shims.append(tpl.name)

    for old in prev_shims - set(current_shims) - {"unienv", "ue"}:
        if Path(old).name != old or old in {".", ".."}:
            continue
        stale = BIN / old
        if stale.is_symlink() or stale.exists():
            with contextlib.suppress(OSError):
                stale.unlink()
    atomic_write(tracker, "\n".join(current_shims) + ("\n" if current_shims else ""))

    # the CLI itself: always run with the interpreter that is running us now
    # (a uv-managed 3.12, never Homebrew's python), falling back to uv python find
    cli = BIN / "unienv"
    script = Path(__file__).resolve()
    cli.write_text(
        "#!/bin/sh\n"
        f"PY={shlex.quote(sys.executable)}\n"
        f"UV={shlex.quote(which('uv') or '')}\n"
        'if [ ! -x "$PY" ]; then\n'
        '  if [ -z "$UV" ] || [ ! -x "$UV" ]; then\n'
        '    UV="$(command -v uv 2>/dev/null || true)"\n'
        "  fi\n"
        '  if [ -n "$UV" ] && [ -x "$UV" ]; then\n'
        f'    PY="$("$UV" python find {PY_VERSION} --managed-python 2>/dev/null || true)"\n'
        "  fi\n"
        "fi\n"
        'if [ -z "$PY" ] || [ ! -x "$PY" ]; then\n'
        "  echo \"UniEnv's Python 3.12 interpreter is missing. Repair with: "
        'curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec "$PY" "{script}" "$@"\n',
        newline="",
    )
    cli.chmod(0o755)
    ue = BIN / "ue"
    if ue.is_symlink() or ue.exists():
        ue.unlink()
    if IS_WIN:
        py_exe = venv_python()
        (BIN / "ue-py.cmd").write_text(f'@echo off\r\nset PYTHONUTF8=1\r\n"{py_exe}" %*\r\n', newline="")
        (BIN / "ue-node.cmd").write_text(
            f'''@echo off\r\nset "NODE_PATH={UNIENV_HOME / "node" / "node_modules"};%NODE_PATH%"\r\n"{node}" %*\r\n''',
            newline="",
        )
        cli_cmd = (
            "@echo off\r\n"
            "set PYTHONUTF8=1\r\n"
            f'if /i not "%~1"=="uninstall" "{sys.executable}" "{Path(__file__).resolve()}" %* & exit /b\r\n'
            f'copy /y "{Path(__file__).resolve()}" "%TEMP%\\unienv-uninstall.py" >nul\r\n'
            '> "%TEMP%\\unienv-uninstall.cmd" echo @echo off\r\n'
            f'>> "%TEMP%\\unienv-uninstall.cmd" echo "{sys.executable}" "%%TEMP%%\\unienv-uninstall.py" %%*\r\n'
            '>> "%TEMP%\\unienv-uninstall.cmd" echo set "RC=%%ERRORLEVEL%%"\r\n'
            '>> "%TEMP%\\unienv-uninstall.cmd" echo del /f /q "%%TEMP%%\\unienv-uninstall.py" 2^>nul\r\n'
            '>> "%TEMP%\\unienv-uninstall.cmd" echo (goto) 2^>nul ^& del /f /q "%%~f0" ^& cmd /c exit %%RC%%\r\n'
            '"%TEMP%\\unienv-uninstall.cmd" %*\r\n'
        )
        (BIN / "unienv.cmd").write_text(cli_cmd, newline="")
        (BIN / "ue.cmd").write_text(cli_cmd, newline="")
        wrappers = {
            "ue-py": [str(py_exe)],
            "ue": [sys.executable, str(Path(__file__).resolve())],
            "unienv": [sys.executable, str(Path(__file__).resolve())],
            "soffice-safe": [sys.executable, str(Path(__file__).resolve()), "_soffice-safe"],
        }
        for name, argv in wrappers.items():
            command = " ".join((shlex.quote(arg.replace("\\", "/")) for arg in argv))
            (BIN / name).write_text(f'#!/bin/sh\nexport PYTHONUTF8=1\nexec {command} "$@"\n', newline="")
        node_path = str(NODE_DIR / "node_modules").replace("\\", "/")
        (BIN / "ue-node").write_text(
            f'''#!/bin/sh\nexport NODE_PATH="{node_path};$NODE_PATH"\nexec "{node.replace(chr(92), "/")}" "$@"\n''',
            newline="",
        )
        (BIN / "soffice-safe.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" "{Path(__file__).resolve()}" _soffice-safe %*\r\n', newline=""
        )
    else:
        ue.symlink_to("unienv")


def brew_prefix() -> str | None:
    for p in ("/opt/homebrew", "/usr/local", "/home/linuxbrew/.linuxbrew"):
        if Path(p, "bin/brew").exists():
            return p
    return None


def write_env_sh(state: dict) -> None:
    lines = [
        "# UniEnv AI environment. Generated by `unienv sync`; do not edit.",
        "# Sourced from ~/.zshenv, ~/.profile and ~/.bashrc so every shell, including the",
        "# non-interactive ones AI agents spawn for each command, sees the same tools.",
        f'export UNIENV_HOME="{UNIENV_HOME}"',
    ]
    bp = brew_prefix()
    if state["options"].get("wire_homebrew", True) and bp:
        lines += [
            "# Homebrew's own setup lives in ~/.zprofile, which `zsh -c` never reads.",
            f'case ":$PATH:" in *":{bp}/bin:"*) ;; *) PATH="{bp}/bin:{bp}/sbin:$PATH" ;; esac',
        ]
    lines += [
        'case ":$PATH:" in *":$UNIENV_HOME/bin:"*) ;; *) PATH="$UNIENV_HOME/bin:$PATH" ;; esac',
        "export PATH",
    ]
    if (
        "diagrams" in state.get("batteries", [])
        or _uses_puppeteer(state.get("extras", {}).get("node", []))
        or (UNIENV_HOME / "cache" / "puppeteer").is_dir()
    ):
        lines.append('export PUPPETEER_CACHE_DIR="${PUPPETEER_CACHE_DIR:-$UNIENV_HOME/cache/puppeteer}"')
    if "browser" in state.get("batteries", []):
        lines.append('export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$UNIENV_HOME/browsers}"')
    lines += [
        "# `bash -c` (non-login, non-interactive) reads only $BASH_ENV.",
        '[ -z "${BASH_ENV:-}" ] && export BASH_ENV="$UNIENV_HOME/env.sh"',
    ]
    if IS_MAC:
        lines += [
            "# Leptonica (used by tesseract) rewrites paths starting with /tmp to the per-user",
            "# temp dir, so files written under /tmp 'vanish'. /private/tmp is the same",
            "# directory spelled so it isn't rewritten. Agents often run with TMPDIR unset or",
            "# under /tmp.",
            'case "${TMPDIR:-/tmp}" in /tmp|/tmp/*) export TMPDIR="/private${TMPDIR:-/tmp}" ;; esac',
        ]
    lines += [
        "# Agents run non-interactive shells: never block on a pager.",
        'case $- in *i*) ;; *) export GIT_PAGER="${GIT_PAGER:-cat}" PAGER="${PAGER:-cat}" ;; esac',
        "",
    ]
    atomic_write(ENV_SH, "\n".join(lines))


def python_packages() -> dict[str, str]:
    py = venv_python()
    if not py.exists():
        return {}
    r = run(
        [
            str(py),
            "-P",  # don't import modules from the cwd (e.g. a stray json.py)
            "-c",
            "import importlib.metadata as m, json;"
            "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))",
        ],
        capture=True,
        check=False,
    )
    try:
        return {norm(k): v for k, v in json.loads(r.stdout).items()}
    except (json.JSONDecodeError, TypeError):
        return {}


def node_packages(packages: list[str]) -> dict[str, str | None]:
    out = {}
    for p in packages:
        name = node_name(p)
        pj = NODE_DIR / "node_modules" / name / "package.json"
        out[name] = json.loads(pj.read_text()).get("version") if pj.exists() else None
    return out


def write_manifest(recipes: list[dict], state: dict, w: dict) -> dict:
    pkgs = python_packages()
    top = {py_name(p): pkgs.get(py_name(p)) for p in w["python"]}
    manifest = {
        "unienv": VERSION,
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "home": str(UNIENV_HOME),
        "platform": f"{platform.system()} {platform.machine()}",
        "batteries": [r["id"] for r in recipes],
        "python": {
            "command": str(BIN / ("ue-py.cmd" if IS_WIN else "ue-py")),
            "executable": str(venv_python()),
            "version": PY_VERSION,
            "packages": top,
        },
        "node": {"command": str(BIN / ("ue-node.cmd" if IS_WIN else "ue-node")), "packages": node_packages(w["node"])},
        "bins": {b: resolve_bin(b) for b in w["bins"]},
        "extras": state["extras"],
    }
    atomic_write(MANIFEST_FILE, json.dumps(manifest, indent=2) + "\n")
    return manifest


def _fmt_list(items, empty="(none)") -> str:
    items = [i for i in items if i]
    return ", ".join(f"`{i}`" for i in items) if items else empty


def render_agent_block(manifest: dict) -> str:
    tpl = (TEMPLATES_DIR / "agent-block.md").read_text()
    tools = [b for b, p in manifest["bins"].items() if p]
    py = [f"{k}" for k, v in manifest["python"]["packages"].items() if v]
    node = [k for k, v in manifest["node"]["packages"].items() if v]
    return (
        tpl.replace("{{BIN}}", str(BIN))
        .replace("{{HOME_DIR}}", str(UNIENV_HOME))
        .replace("{{TOOLS}}", _fmt_list(tools))
        .replace("{{PY_PACKAGES}}", _fmt_list(py))
        .replace("{{NODE_PACKAGES}}", _fmt_list(node))
    ).rstrip() + "\n"


def render_skill(manifest: dict, recipes: list[dict]) -> str:
    tpl = (TEMPLATES_DIR / "SKILL.md").read_text()
    notes = []
    for r in recipes:
        if r.get("agent", {}).get("notes"):
            notes.append(f"### {r['id']}: {r['summary']}\n\n{r['agent']['notes'].strip()}\n")
    py = [f"{k} {v}" for k, v in manifest["python"]["packages"].items() if v]
    return (
        tpl.replace("{{BIN}}", str(BIN))
        .replace("{{HOME_DIR}}", str(UNIENV_HOME))
        .replace("{{BATTERIES}}", ", ".join(manifest["batteries"]))
        .replace("{{NOTES}}", "\n".join(notes).strip())
        .replace("{{PY_PACKAGES}}", "\n".join(f"- {p}" for p in py) or "(none)")
    )


def _vscode_ext_dirs(prefix: str) -> list[Path]:
    out: list[Path] = []
    for ext_root in (
        HOME / ".vscode/extensions",
        HOME / ".vscode-server/extensions",
        HOME / ".vscode-insiders/extensions",
        HOME / ".cursor/extensions",
        HOME / ".windsurf/extensions",
    ):
        if ext_root.is_dir():
            out.extend(sorted(ext_root.glob(f"{prefix}*")))
    return out


def agent_targets() -> list[dict]:
    codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
    return [
        {
            "id": "claude-code",
            "name": "Claude Code",
            "detect": [HOME / ".claude"],
            "bin": "claude",
            "instructions": HOME / ".claude/CLAUDE.md",
            "skills": HOME / ".claude/skills",
        },
        {
            "id": "codex",
            "name": "Codex CLI",
            "detect": [codex_home],
            "bin": "codex",
            "instructions": codex_home / "AGENTS.md",
            "skills": codex_home / "skills",
        },
        {
            "id": "gemini",
            "name": "Gemini CLI",
            "detect": [HOME / ".gemini"],
            "bin": "gemini",
            "instructions": HOME / ".gemini/GEMINI.md",
            "skills": None,
        },
        {
            "id": "opencode",
            "name": "OpenCode",
            "detect": [HOME / ".config/opencode"],
            "bin": "opencode",
            "instructions": HOME / ".config/opencode/AGENTS.md",
            "skills": None,
        },
        {
            # https://docs.windsurf.com/windsurf/cascade/memories#rules
            "id": "windsurf",
            "name": "Windsurf",
            "detect": [HOME / ".codeium/windsurf"],
            "bin": "windsurf",
            "instructions": HOME / ".codeium/windsurf/memories/global_rules.md",
            "skills": None,
        },
        {
            "id": "cline",
            "name": "Cline",
            "detect": [HOME / ".cline", *_vscode_ext_dirs("saoudrizwan.claude-dev")],
            "bin": "cline",
            "instructions": HOME / ".cline/rules/unienv.md",
            "skills": None,
            "owned_rules_file": True,
        },
        {
            "id": "roo-code",
            "name": "Roo Code",
            "detect": [HOME / ".roo", *_vscode_ext_dirs("rooveterinaryinc.roo-cline")],
            "bin": None,
            "instructions": HOME / ".roo/rules/unienv.md",
            "skills": None,
            "owned_rules_file": True,
        },
    ]


def detected(agent: dict) -> bool:
    return any(p.exists() for p in agent["detect"]) or bool(agent.get("bin") and which(agent["bin"]))


def sync_agents(manifest: dict, recipes: list[dict], *, only_detected: bool = True) -> list[str]:
    block = render_agent_block(manifest)
    skill = render_skill(manifest, recipes)
    atomic_write(AGENTS_COPY, block)  # canonical copy, e.g. to paste into Cursor's User Rules
    done = []
    for a in agent_targets():
        if only_detected and not detected(a):
            continue
        try:
            upsert_block(a["instructions"], block, MD_BEGIN, MD_END)
            if a["skills"] is not None:
                d = a["skills"] / "unienv"
                d.mkdir(parents=True, exist_ok=True)
                (d / "SKILL.md").write_text(skill, encoding="utf-8")
        except OSError as e:
            warn(f"could not update {a['instructions']} ({e}); copy {AGENTS_COPY} manually")
            continue
        done.append(a["name"])
    return done


def _zdotdir() -> Path | None:
    raw = os.environ.get("ZDOTDIR", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        same = p.resolve() == HOME.resolve()
    except OSError:
        same = p == HOME
    return None if same else p


def _shell_files(state: dict | None = None) -> list[Path]:
    if not SHELL_FILES:
        return []
    files = list(SHELL_FILES)
    zdot = _zdotdir()
    if zdot is not None:
        zenv = zdot / ".zshenv"
        if zenv not in files:
            files.append(zenv)
    if state is None:
        state = load_state(load_recipes()) if STATE_FILE.exists() else {}
    for raw in state.get("wired_shell_files", []):
        if isinstance(raw, str) and Path(raw).is_absolute() and Path(raw) not in files and Path(raw).exists():
            files.append(Path(raw))
    return files


def wire_shells(state: dict | None = None) -> list[str]:
    if IS_WIN:
        return []
    own_state = state is None
    if state is None:
        state = load_state(load_recipes())
    paths = state.setdefault("wired_shell_files", [])
    before_paths = list(paths)
    body = f'[ -f "{ENV_SH}" ] && . "{ENV_SH}"'
    changed = []
    for rc in _shell_files(state):
        # always wire zsh's .zshenv and the POSIX .profile; bash's own files only if they exist
        # (login bash reads .bash_profile or .bash_login *instead of* .profile when present)
        if rc.name not in (".zshenv", ".profile") and not rc.exists():
            continue
        try:
            if upsert_block(rc, body, SHELL_BEGIN, SHELL_END):
                changed.append(str(rc).replace(str(HOME), "~"))
            if rc.name == ".zshenv" and rc.resolve().parent != HOME.resolve():
                saved = str(rc.resolve())
                if saved not in paths:
                    paths.append(saved)
        except OSError as e:
            warn(f"could not update {rc} ({e}); add `source {ENV_SH}` yourself")
    if own_state and paths != before_paths:
        save_state(state)
    return changed


def do_sync(
    state: dict,
    recipes_all: dict,
    *,
    quiet: bool = False,
    wire: bool | None = None,
    all_agents: bool = False,
) -> dict:
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)
    write_shims()
    prune_backups()
    write_env_sh(state)
    manifest = write_manifest(recipes, state, w)
    agents = sync_agents(manifest, recipes, only_detected=not all_agents)
    before_wired = list(state.get("wired_shell_files", []))
    should_wire = state.get("options", {}).get("wire_shells", True) if wire is None else wire
    wired = wire_shells(state) if should_wire else []
    if state.get("wired_shell_files", []) != before_wired:
        save_state(state)
    if not quiet:
        info(f"  shims: {', '.join(sorted(p.name for p in BIN.iterdir() if not p.name.startswith('.')))}")
        info(f"  agents configured: {', '.join(agents) or '(none detected)'}")
        if wired:
            info(f"  shell files updated: {', '.join(wired)}")
    return manifest


# ── commands ────────────────────────────────────────────────────────────────


def _tty():
    """(reader, writer) for the user's terminal, even when stdin is a pipe (curl | bash)."""
    if not sys.stdout.isatty() or os.environ.get("CI"):
        return None
    try:  # separate handles: a text-mode "r+" open of /dev/tty is rejected as unseekable
        return open("/dev/tty"), open("/dev/tty", "w")
    except OSError:
        return None


def pick_batteries(recipes_all: dict, current: list[str]) -> list[str]:
    """Interactive checklist shown on first install. Enter accepts; numbers toggle."""
    tty = _tty()
    if tty is None:
        return current
    tin, tout = tty
    choices = sorted(
        (k for k, r in recipes_all.items() if not r.get("always")), key=lambda k: (not recipes_all[k].get("default"), k)
    )
    selected = set(current)
    while True:
        tout.write("\n" + _c("1", "Choose batteries") + _c("2", "  (core is always installed)") + "\n")
        for i, k in enumerate(choices, 1):
            mark = _c("32", "[x]") if k in selected else "[ ]"
            tout.write(f"  {i}. {mark} {k:<7} {recipes_all[k]['summary']}\n")
        tout.write(_c("2", "Enter to continue, or numbers to toggle (e.g. 5 6): "))
        tout.flush()
        answer = tin.readline().strip()
        if not answer:
            tin.close()
            tout.close()
            return [k for k in choices if k in selected]
        for tok in answer.replace(",", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= len(choices):
                selected ^= {choices[int(tok) - 1]}


def cmd_install(args, *, run_doctor: bool = True) -> int:
    recipes_all = load_recipes()
    first_run = not STATE_FILE.exists()
    state = load_state(recipes_all)
    if first_run and not (args.batteries or args.add or args.yes):
        state["batteries"] = pick_batteries(recipes_all, state["batteries"])
    if args.batteries:
        req = [b.strip() for b in args.batteries.split(",") if b.strip()]
        bad = [b for b in req if b not in recipes_all]
        if bad:
            die(f"unknown batteries: {', '.join(bad)}. Available: {', '.join(recipes_all)}")
        state["batteries"] = [b for b in req if not recipes_all[b].get("always")]
    if args.exclude:
        state["options"]["exclude"] = uniq(
            state["options"]["exclude"] + [x.strip() for x in args.exclude.split(",") if x.strip()]
        )
    if args.add:
        req = [b.strip() for b in args.add.split(",") if b.strip()]
        bad = [b for b in req if b not in recipes_all]
        if bad:
            hints = []
            for b in bad:
                close = difflib.get_close_matches(b, recipes_all, n=1)
                hints.append(f"unknown battery: {b}" + (f" (did you mean {close[0]}?)" if close else ""))
            die("; ".join(hints) + f". Available: {', '.join(recipes_all)}")
        state["batteries"] = uniq(state["batteries"] + [b for b in req if not recipes_all[b].get("always")])
    state["options"]["wire_shells"] = not args.no_wire
    UNIENV_HOME.mkdir(parents=True, exist_ok=True)
    save_state(state)
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)
    info(_c("1", f"UniEnv AI {VERSION}") + f"  →  {UNIENV_HOME}")
    info(f"  batteries: {', '.join(r['id'] for r in recipes)}")

    failures = []
    if not args.no_system:
        step("System tools")
        failures = install_system(w, assume_yes=args.yes)
    step(f"Python {PY_VERSION} environment ({len(w['python'])} packages)")
    install_python(w["python"])
    if "browser" in state.get("batteries", []):
        install_browsers(assume_yes=args.yes, no_system=args.no_system)
    step(f"Node packages ({len(w['node'])})")
    install_node(w["node"])
    step("Wiring shells and AI tools")
    do_sync(state, recipes_all, wire=not args.no_wire, all_agents=args.all_agents)
    log_event(event="install", batteries=[r["id"] for r in recipes], failures=failures)
    if failures:
        warn("Some system packages failed: " + ", ".join(failures))
    if not run_doctor:
        return 0
    step("Checking")
    rc = cmd_doctor(argparse.Namespace(json=False, fix=False, quiet_ok=True))
    info("\nOpen a new terminal (or restart your AI tools) so they pick up the new PATH.")
    return 1 if failures else rc


def cmd_sync(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    step("Regenerating shims, env.sh, manifest and agent files")
    do_sync(state, recipes_all, wire=False if args.no_wire else None, all_agents=args.all_agents)
    return 0


def _ver_tuple(v: str) -> tuple[int, ...] | None:
    # Complex versions require a full PEP 440 / npm parser. Never guess.
    if not re.fullmatch(r"\d+(?:\.\d+)*", v):
        return None
    parts = [int(n) for n in v.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _version_matches(installed: str, op: str, target: str) -> bool | None:
    iv = _ver_tuple(installed)
    wildcard = target.endswith(".*")
    tv = _ver_tuple(target[:-2] if wildcard else target)
    if iv is None or tv is None:
        return None
    if wildcard:
        if op not in ("==", "!="):
            return None
        prefix = tuple(int(n) for n in target[:-2].split("."))
        values = iv + (0,) * max(0, len(prefix) - len(iv))
        result = values[: len(prefix)] == prefix
        return result if op == "==" else not result
    if op in ("==", "=", "==="):
        return iv == tv
    if op == "!=":
        return iv != tv
    if op == ">=":
        return iv >= tv
    if op == "<=":
        return iv <= tv
    if op == ">":
        return iv > tv
    if op == "<":
        return iv < tv
    raw = [int(n) for n in target.split(".")]
    if op == "~=":
        if len(raw) < 2:
            return None
        upper = tuple(raw[:-2] + [raw[-2] + 1])
        return tv <= iv < upper
    if op == "~":
        upper = (raw[0] + 1,) if len(raw) == 1 else (raw[0], raw[1] + 1)
        return tv <= iv < upper
    if op == "^":
        if len(raw) < 3:
            return None
        while len(raw) < 3:
            raw.append(0)
        index = next((i for i, n in enumerate(raw) if n), len(raw) - 1)
        upper = tuple(raw[:index] + [raw[index] + 1])
        return tv <= iv < upper
    return None


def _installed_versions(kind: str, pkgs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if MANIFEST_FILE.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError, KeyError, TypeError):
            m = json.loads(MANIFEST_FILE.read_text())
            raw = m.get(kind, {}).get("packages", {}) or {}
            out.update({(norm(k) if kind == "python" else k): v for k, v in raw.items() if v})
    if kind == "python":
        for k, v in python_packages().items():
            if v:
                out[k] = v
    elif kind == "node":
        for k, v in node_packages(pkgs).items():
            if v:
                out[k] = v
    return out


def _spec_satisfied(
    kind: str,
    spec: str,
    manifest_pkgs: dict[str, str],
    configured_specs: list[str] | None = None,
) -> tuple[bool | None, str | None]:
    if kind == "python":
        name = py_name(spec)
        installed = manifest_pkgs.get(name)
        m = re.match(r"^[A-Za-z0-9_.-]+(?:\[([^\]]+)\])?\s*(.*)$", spec.strip().split(";", 1)[0])
        extras_str, ver_part = (m.group(1), m.group(2).strip()) if m else (None, "")
        if not extras_str and not ver_part:
            return True, installed
        if extras_str:
            req_extras = {e.strip().lower() for e in extras_str.split(",") if e.strip()}
            have_extras: set[str] = set()
            for cs in configured_specs or []:
                if py_name(cs) == name:
                    em = re.search(r"\[([^\]]+)\]", cs)
                    if em:
                        have_extras.update(e.strip().lower() for e in em.group(1).split(",") if e.strip())
            if not req_extras.issubset(have_extras):
                return None, installed
        if ver_part:
            if not installed:
                return None, None
            matches = []
            for clause in (c.strip() for c in ver_part.split(",") if c.strip()):
                cm = re.fullmatch(r"(===|==|!=|~=|>=|<=|>|<)\s*(.+)", clause)
                matches.append(_version_matches(installed, cm.group(1), cm.group(2).strip()) if cm else None)
            if None in matches:
                return None, installed
            return all(matches), installed
        return True, installed
    if kind == "node":
        name = node_name(spec)
        installed = manifest_pkgs.get(name)
        ver = node_version(spec)
        if ver in ("latest", "*"):
            return True, installed
        if not installed:
            return None, None
        if re.fullmatch(r"\d+(?:\.\d+){0,1}", ver):
            prefix = tuple(int(n) for n in ver.split("."))
            iv = _ver_tuple(installed)
            if iv is None:
                return None, installed
            padded = iv + (0,) * max(0, len(prefix) - len(iv))
            return padded[: len(prefix)] == prefix, installed
        clauses = [c.strip() for c in ver.split() if c.strip()]
        matches = []
        for clause in clauses:
            cm = re.fullmatch(r"(\^|~|>=|<=|>|<|=)?\s*(\d+(?:\.\d+)*)", clause)
            if not cm:
                matches.append(None)
                continue
            op = cm.group(1) or "=="
            ver_str = cm.group(2)
            n_parts = len(ver_str.split("."))
            if n_parts != 3 and not (op == "~" and n_parts == 2 and len(clauses) == 1):
                matches.append(None)
            else:
                matches.append(_version_matches(installed, op, ver_str))
        if None in matches:
            return None, installed
        return all(matches), installed
    return True, manifest_pkgs.get(spec)


def cmd_need(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    kind = "node" if args.node else "brew" if args.bin else "cask" if args.cask else "python"
    if kind in ("brew", "cask"):
        pk = platform_key()
        kind = {"debian": "apt", "fedora": "dnf", "arch": "pacman"}.get(pk, kind)
        if pk == "windows":
            kind = "scoop" if which("scoop") else "winget"
    pkgs = args.packages
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)

    key = {"python": py_name, "node": node_name}.get(kind, lambda s: s)
    present = {key(p) for p in w[kind]}
    already = [p for p in pkgs if key(p) in present]
    constrained = [
        p
        for p in already
        if (kind == "python" and ("[" in p or re.search(r"[<>=!~]", p)))
        or (kind == "node" and node_version(p) not in ("latest", "*"))
    ]
    if constrained and kind in ("python", "node"):
        manifest_pkgs = _installed_versions(kind, w[kind] + already)
        unsatisfied = False
        for p in constrained:
            ok, inst_ver = _spec_satisfied(kind, p, manifest_pkgs, w[kind])
            if ok is False:
                ver_label = f" {inst_ver}" if inst_ver else ""
                warn(
                    f"{key(p)}{ver_label} is installed; `{p}` isn't satisfied. "
                    "Change the pin with `unienv upgrade` (#27)"
                )
                unsatisfied = True
        if unsatisfied:
            return 1
    new = [p for p in pkgs if key(p) not in present]
    if not new:
        info(f"✓ already available: {', '.join(pkgs)}")
        return 0
    curated = {
        key(p)
        for r in recipes_all.values()
        for p in (
            r.get(kind, {}).get("packages", [])
            if kind in ("python", "node")
            else [x for s in r.get("system", {}).values() for x in s.get(kind, [])]
        )
    }
    uncurated = [p for p in new if key(p) not in curated]
    if uncurated:
        info(f"  note: not in a curated recipe, installing from the public registry: {', '.join(uncurated)}")

    # snapshot so a failed install can be rolled back exactly
    tracked = [
        STATE_FILE,
        PY_DIR / "pyproject.toml",
        PY_DIR / "uv.lock",
        PY_DIR / ".python-version",
        NODE_DIR / "package.json",
        NODE_DIR / "package-lock.json",
    ]
    existed = {f: f.exists() for f in tracked}
    venv = PY_DIR / ".venv"
    venv_existed = os.path.lexists(venv)
    snap = BACKUP_DIR / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap.mkdir(parents=True, exist_ok=True)
    for f in tracked:
        if existed[f]:
            shutil.copy2(f, snap / f"{f.parent.name}__{f.name}")

    state["options"]["exclude"] = [x for x in state["options"]["exclude"] if x not in pkgs]
    state["extras"][kind] = uniq(state["extras"][kind] + new)
    w = wanted(recipes, state)
    try:
        if kind == "python":
            install_python(w["python"])
            if "browser" in state.get("batteries", []):
                install_browsers(assume_yes=args.yes, no_system=True)
        elif kind == "node":
            install_node(w["node"], require_npm=True)
        else:
            failed = install_system(
                {
                    **w,
                    kind: new,
                    "brew": new if kind == "brew" else [],
                    "cask": new if kind == "cask" else [],
                    "apt": new if kind == "apt" else [],
                    "winget": new if kind == "winget" else [],
                    "scoop": new if kind == "scoop" else [],
                    "dnf": new if kind == "dnf" else [],
                    "pacman": new if kind == "pacman" else [],
                },
                assume_yes=args.yes,
            )
            if failed:
                raise RuntimeError(", ".join(failed))
    except (subprocess.CalledProcessError, RuntimeError, SystemExit) as e:
        warn(f"install failed ({e}); rolling back")
        for f in tracked:
            if existed[f]:
                shutil.copy2(snap / f"{f.parent.name}__{f.name}", f)
            elif f.exists():
                f.unlink()
        if not existed[NODE_DIR / "package.json"] and (NODE_DIR / "node_modules").exists():
            shutil.rmtree(NODE_DIR / "node_modules", ignore_errors=True)
        if not venv_existed and os.path.lexists(venv):
            if venv.is_symlink():
                venv.unlink()
            else:
                shutil.rmtree(venv)
        state = load_state(recipes_all)
        try:
            if kind == "python" and existed[PY_DIR / "pyproject.toml"]:
                install_python(wanted(recipes, state)["python"])
            elif kind == "node" and existed[NODE_DIR / "package.json"]:
                install_node(wanted(recipes, state)["node"])
        except (subprocess.CalledProcessError, RuntimeError, SystemExit) as re_err:
            warn(f"rollback re-install failed ({re_err}); run unienv install to repair")
        log_event(event="need-failed", kind=kind, packages=new)
        return 1
    save_state(state)
    do_sync(state, recipes_all, quiet=True)
    log_event(event="need", kind=kind, packages=new, curated=not uncurated)
    via = {"python": "ue-py", "node": "ue-node"}.get(kind, "PATH")
    info(f"✓ installed {', '.join(new)}; available to every AI tool via {via}")
    return 0


def cmd_remove(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    kind = "node" if args.node else "brew" if args.bin else "cask" if args.cask else "python"
    if kind in ("brew", "cask") and platform_key() == "debian":
        kind = "apt"
    pkgs = args.packages
    recipes = active_recipes(recipes_all, state)
    pk = platform_key()
    key = {"python": py_name, "node": node_name}.get(kind, lambda s: s)

    extras_keys = {key(p) for p in state["extras"][kind]}
    battery_owner: dict[str, str] = {}
    for r in recipes:
        r_pkgs = (
            r.get(kind, {}).get("packages", [])
            if kind in ("python", "node")
            else r.get("system", {}).get(pk, {}).get(kind, [])
        )
        for p in r_pkgs:
            battery_owner.setdefault(key(p), r["id"])

    for p in pkgs:
        k = key(p)
        if k not in extras_keys:
            if k in battery_owner:
                bat = battery_owner[k]
                if kind in ("brew", "cask", "apt"):
                    warn(
                        f"{p} is provided by the '{bat}' battery (not added with `unienv need`); "
                        f"use `unienv install --exclude {p}` to skip it"
                    )
                else:
                    warn(
                        f"{p} is provided by the '{bat}' battery (not added with `unienv need`); "
                        f"disable the battery with `unienv install --batteries ...` or `unienv install --exclude {p}`"
                    )
            else:
                warn(f"{p} is not installed in extras ({kind})")
            return 1

    tracked = [
        STATE_FILE,
        PY_DIR / "pyproject.toml",
        PY_DIR / "uv.lock",
        NODE_DIR / "package.json",
        NODE_DIR / "package-lock.json",
    ]
    existed = {f: f.exists() for f in tracked}
    snap = BACKUP_DIR / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap.mkdir(parents=True, exist_ok=True)
    for f in tracked:
        if existed[f]:
            shutil.copy2(f, snap / f"{f.parent.name}__{f.name}")

    remove_keys = {key(p) for p in pkgs}
    removed = [p for p in state["extras"][kind] if key(p) in remove_keys]
    state["extras"][kind] = [p for p in state["extras"][kind] if key(p) not in remove_keys]
    w = wanted(recipes, state)
    still_wanted = {key(x) for x in w[kind]}
    to_uninstall = [p for p in removed if key(p) not in still_wanted]
    try:
        if kind == "python":
            install_python(w["python"])
        elif kind == "node":
            if w["node"]:
                install_node(w["node"], require_npm=True)
            elif (NODE_DIR / "package.json").exists():
                write_package_json([])
                shutil.rmtree(NODE_DIR / "node_modules", ignore_errors=True)
                (NODE_DIR / "package-lock.json").unlink(missing_ok=True)
        elif kind in ("brew", "cask") and to_uninstall:
            brew = which("brew")
            cmd = [brew or "brew", "uninstall", *(["--cask"] if kind == "cask" else []), *to_uninstall]
            if args.yes and brew:
                run(cmd)
            else:
                info(f"  removed from UniEnv extras; to also uninstall from Homebrew, run: {' '.join(cmd)}")
        elif kind == "apt" and to_uninstall:
            cmd = ["sudo", "apt-get", "remove", "-y", *to_uninstall]
            if args.yes:
                sim = run(["apt-get", "-s", "remove", *to_uninstall], capture=True, check=False)
                extra_removed = [
                    m.group(1)
                    for m in re.finditer(r"(?m)^Remv\s+(\S+)", sim.stdout or "")
                    if m.group(1) not in to_uninstall
                ]
                if extra_removed:
                    warn(
                        "aborting apt-get remove because it would also remove dependent packages: "
                        + ", ".join(extra_removed)
                    )
                    raise RuntimeError("apt-get remove would remove extra packages")
                run(cmd[1:] if os.geteuid() == 0 else cmd)
            else:
                info(f"  removed from UniEnv extras; to also uninstall from apt, run: {' '.join(cmd)}")
    except BaseException as e:
        warn(f"remove failed ({e}); rolling back")
        for f in tracked:
            if existed[f]:
                shutil.copy2(snap / f"{f.parent.name}__{f.name}", f)
            elif f.exists():
                f.unlink()
        state = load_state(recipes_all)
        try:
            if kind == "python" and existed[PY_DIR / "pyproject.toml"]:
                install_python(wanted(recipes, state)["python"])
            elif kind == "node" and existed[NODE_DIR / "package.json"]:
                install_node(wanted(recipes, state)["node"])
        except (subprocess.CalledProcessError, RuntimeError, SystemExit) as re_err:
            warn(f"rollback re-install failed ({re_err}); run unienv install to repair")
        log_event(event="remove-failed", kind=kind, packages=removed)
        if not isinstance(e, (subprocess.CalledProcessError, RuntimeError, SystemExit)):
            raise
        return 1

    save_state(state)
    do_sync(state, recipes_all, quiet=True)
    log_event(event="remove", kind=kind, packages=removed)
    info(f"✓ removed {', '.join(removed)}")
    return 0


def _verify_python_imports(recipes: list[dict]) -> dict[str, str]:
    """Verify battery Python imports. Returns a dict of {module: error_string} for failures."""
    imports = uniq([m for r in recipes for m in r.get("python", {}).get("imports", [])])
    if not imports:
        return {}
    py = venv_python()
    if not py.exists():
        return {"python": "shared Python env missing"}
    code = (
        "import importlib, json\nbad = {}\n"
        f"for m in {imports!r}:\n"
        "    try: importlib.import_module(m)\n"
        "    except Exception as e: bad[m] = f'{type(e).__name__}: {e}'[:120]\n"
        "print(json.dumps(bad))"
    )
    r = run([str(py), "-P", "-c", code], capture=True, check=False, timeout=120)
    if r.returncode != 0:
        return {"python": (r.stderr or r.stdout or "import check failed").strip()[:200]}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"python": "could not parse import check output"}


def cmd_upgrade(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)
    pkgs = args.packages or []
    do_py = not args.node
    do_node = bool(args.node) or (not pkgs and bool(w["node"]))

    if args.dry_run:
        if do_py and (PY_DIR / "pyproject.toml").exists():
            uv = find_uv()
            uv_cmd = [uv, "lock", "--project", str(PY_DIR), "--managed-python", "--dry-run"]
            if pkgs:
                for p in pkgs:
                    uv_cmd.extend(["--upgrade-package", py_name(p)])
            else:
                uv_cmd.append("--upgrade")
            r = run(uv_cmd, capture=True, check=False, env={"UV_PROJECT_ENVIRONMENT": str(PY_DIR / ".venv")})
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            info(out or "Python packages: all up to date")
        if do_node and (NODE_DIR / "package.json").exists():
            npm = which("npm")
            if npm:
                r = run([npm, "outdated", *pkgs], cwd=NODE_DIR, capture=True, check=False)
                out = (r.stdout or "").strip()
                info(out or "Node packages: all up to date")
        return 0

    before_py = python_packages() if do_py else {}
    before_node = node_packages(w["node"]) if do_node else {}

    tracked = [
        STATE_FILE,
        PY_DIR / "pyproject.toml",
        PY_DIR / "uv.lock",
        NODE_DIR / "package.json",
        NODE_DIR / "package-lock.json",
    ]
    existed = {f: f.exists() for f in tracked}
    snap = BACKUP_DIR / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap.mkdir(parents=True, exist_ok=True)
    for f in tracked:
        if existed[f]:
            shutil.copy2(f, snap / f"{f.parent.name}__{f.name}")

    try:
        if do_py:
            uv = find_uv()
            write_pyproject(w["python"])
            uv_cmd = [uv, "lock", "--project", str(PY_DIR), "--managed-python"]
            if pkgs:
                for p in pkgs:
                    uv_cmd.extend(["--upgrade-package", py_name(p)])
            else:
                uv_cmd.append("--upgrade")
            run(uv_cmd, env={"UV_PROJECT_ENVIRONMENT": str(PY_DIR / ".venv")})
            install_python(w["python"])
            if "browser" in state.get("batteries", []):
                install_browsers(assume_yes=False, no_system=True)
            bad_imports = _verify_python_imports(recipes)
            if bad_imports:
                details = "; ".join(f"{m}: {err}" for m, err in bad_imports.items())
                raise RuntimeError(f"import check failed after upgrade ({details})")
        if do_node and w["node"]:
            npm = which("npm")
            if not npm:
                if args.node:
                    raise RuntimeError("npm not found")
                warn("npm not found; skipping Node upgrade")
            else:
                if not (NODE_DIR / "package.json").exists():
                    write_package_json(w["node"])
                run(
                    [
                        npm,
                        "update",
                        "--save",
                        "--no-audit",
                        "--no-fund",
                        "--loglevel=error",
                        "--progress=false",
                        "--no-update-notifier",
                        *pkgs,
                    ],
                    cwd=NODE_DIR,
                    env=_puppeteer_cache_env(w["node"]),
                )
    except (subprocess.CalledProcessError, RuntimeError, SystemExit) as e:
        warn(f"upgrade failed ({e}); rolling back")
        for f in tracked:
            if existed[f]:
                shutil.copy2(snap / f"{f.parent.name}__{f.name}", f)
            elif f.exists():
                f.unlink()
        state = load_state(recipes_all)
        try:
            if do_py and existed[PY_DIR / "pyproject.toml"]:
                install_python(wanted(recipes, state)["python"])
            if do_node and existed[NODE_DIR / "package.json"]:
                install_node(wanted(recipes, state)["node"])
        except (subprocess.CalledProcessError, RuntimeError, SystemExit) as re_err:
            warn(f"rollback re-install failed ({re_err}); run unienv install to repair")
        log_event(event="upgrade-failed", packages=pkgs, node=bool(args.node))
        return 1

    after_py = python_packages() if do_py else {}
    after_node = node_packages(w["node"]) if do_node else {}
    changes = []
    for k in sorted(set(before_py) | set(after_py)):
        b, a = before_py.get(k), after_py.get(k)
        if b != a and a is not None:
            changes.append(f"{k} {b or '(new)'} -> {a}")
    for k in sorted(set(before_node) | set(after_node)):
        b, a = before_node.get(k), after_node.get(k)
        if b != a and a is not None:
            changes.append(f"node:{k} {b or '(new)'} -> {a}")

    do_sync(state, recipes_all, quiet=True)
    log_event(event="upgrade", packages=pkgs, changes=changes)
    if changes:
        info("✓ upgraded: " + ", ".join(changes))
    else:
        info("✓ all packages already at latest compatible versions")
    return 0


def cmd_list(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    active = {r["id"] for r in active_recipes(recipes_all, state)}
    if args.json:
        print(MANIFEST_FILE.read_text() if MANIFEST_FILE.exists() else "{}")
        return 0
    info(_c("1", "Batteries"))
    for k, r in recipes_all.items():
        mark = _c("32", "●") if k in active else _c("2", "○")
        info(f"  {mark} {k:<8} {r['summary']}")
    if MANIFEST_FILE.exists():
        m = json.loads(MANIFEST_FILE.read_text())
        info(_c("1", "\nPython packages") + f"  ({m['python']['command']})")
        info("  " + ", ".join(f"{k} {v}" for k, v in m["python"]["packages"].items() if v))
        info(_c("1", "\nNode packages") + f"  ({m['node']['command']})")
        info("  " + (", ".join(f"{k} {v}" for k, v in m["node"]["packages"].items() if v) or "(none)"))
        info(_c("1", "\nSystem tools"))
        for b, p in m["bins"].items():
            info(f"  {b:<14} {p or _c('31', 'missing')}")
    extras = {k: v for k, v in state["extras"].items() if v}
    if extras:
        info(_c("1", "\nAdded with `unienv need`"))
        for k, v in extras.items():
            info(f"  {k}: {', '.join(v)}")
    info("\nEnable an opt-in battery: unienv install --add media,fonts")
    return 0


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(str(x)) for x in items) + "]"


def render_export_toml(state: dict) -> str:
    batteries = list(state.get("batteries", []))
    exclude = list(state.get("options", {}).get("exclude", []))
    extras = state.get("extras", {})
    lines = [
        "# Exported by `unienv export`; restore with `unienv import <file>`.",
        'schema = "unienv/env/v0"',
        f"batteries = {_toml_list(batteries)}",
        "",
        "[options]",
        f"exclude = {_toml_list(exclude)}",
        "",
        "[extras]",
    ]
    for kind in ("python", "node", "brew", "cask", "apt"):
        lines.append(f"{kind} = {_toml_list(list(extras.get(kind, [])))}")

    return "\n".join(lines) + "\n"


def validate_env_spec(doc: dict, recipes_all: dict) -> list[str]:
    problems = []
    if doc.get("schema") != "unienv/env/v0":
        problems.append('schema must be "unienv/env/v0"')
    batteries = doc.get("batteries", [])
    if not isinstance(batteries, list) or not all(isinstance(x, str) for x in batteries):
        problems.append("batteries must be a list of strings")
    else:
        bad = [b for b in batteries if b not in recipes_all]
        if bad:
            problems.append(f"unknown batteries: {', '.join(bad)}")
    options = doc.get("options", {})
    if not isinstance(options, dict):
        problems.append("[options] must be a table")
    else:
        exc = options.get("exclude", [])
        if not isinstance(exc, list) or not all(isinstance(x, str) for x in exc):
            problems.append("[options].exclude must be a list of strings")
    extras = doc.get("extras", {})
    if not isinstance(extras, dict):
        problems.append("[extras] must be a table")
    else:
        for k, v in extras.items():
            if k not in ("python", "node", "brew", "cask", "apt"):
                problems.append(f"unknown [extras] key: {k}")
            elif not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                problems.append(f"[extras].{k} must be a list of strings")
            else:
                for package in v:
                    if not _valid_import_package(k, package):
                        problems.append(
                            f"[extras].{k} contains an unsafe or unsupported package specification: {package!r}"
                        )
    return problems


def cmd_export(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    sys.stdout.write(render_export_toml(state))
    return 0


def _valid_import_package(kind: str, value: str) -> bool:
    if kind == "python":
        return bool(
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~][A-Za-z0-9.*+!,<>=~_-]+)?", value
            )
        )
    if kind == "node":
        return bool(
            re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9.^~*<>=|_-]+)?", value)
        )
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.@-]*", value))


def cmd_import(args) -> int:
    recipes_all = load_recipes()
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        doc = tomllib.loads(raw)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        die(f"invalid environment file {args.file!r}: {exc}")
    problems = validate_env_spec(doc, recipes_all)
    if problems:
        die(f"invalid environment file {args.file!r}: " + "; ".join(problems))
    if "locks" in doc:
        warn("Imported lock hashes are informational only and ignored; package resolution is platform-specific.")
    previous = load_state(recipes_all)
    state = json.loads(json.dumps(previous))
    state["batteries"] = [b for b in uniq(doc.get("batteries", [])) if not recipes_all[b].get("always")]
    state["options"]["exclude"] = uniq(doc.get("options", {}).get("exclude", []))
    for kind in ("python", "node", "brew", "cask", "apt"):
        state["extras"][kind] = uniq(doc.get("extras", {}).get(kind, []))
    info("Import plan:")
    info("  batteries: " + ", ".join(state["batteries"]))
    if state["options"]["exclude"]:
        info("  exclude: " + ", ".join(state["options"]["exclude"]))
    for kind, packages in state["extras"].items():
        if packages:
            info(f"  {kind}: {', '.join(packages)}")
        removed_extras = [p for p in previous.get("extras", {}).get(kind, []) if p not in packages]
        if removed_extras:
            info(f"  remove {kind}: {', '.join(removed_extras)}")
    if not args.yes:
        confirmed = False
        if args.file == "-":
            tty = _tty()
            if tty is not None:
                tin, tout = tty
                try:
                    tout.write("Apply this environment? [y/N] ")
                    tout.flush()
                    confirmed = tin.readline().strip().lower() in ("y", "yes")
                finally:
                    tin.close()
                    tout.close()
            else:
                warn("cannot prompt for confirmation when reading from stdin without a TTY; pass -y/--yes")
        else:
            try:
                confirmed = input("Apply this environment? [y/N] ").strip().lower() in ("y", "yes")
            except EOFError:
                confirmed = False
        if not confirmed:
            info("Import cancelled; no state changed.")
            return 1
    tracked = [
        STATE_FILE,
        PY_DIR / "pyproject.toml",
        PY_DIR / "uv.lock",
        PY_DIR / ".python-version",
        NODE_DIR / "package.json",
        NODE_DIR / "package-lock.json",
    ]
    saved = {path: path.read_bytes() if path.exists() else None for path in tracked}
    dirs = {path: os.path.lexists(path) for path in (PY_DIR / ".venv", NODE_DIR / "node_modules")}

    def restore_files():
        for path, content in saved.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        for path, existed in dirs.items():
            if not existed and os.path.lexists(path):
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)

    try:
        save_state(state)
        result = cmd_install(
            argparse.Namespace(
                batteries=None,
                add=None,
                exclude=None,
                no_system=args.no_system,
                no_wire=args.no_wire,
                all_agents=False,
                yes=args.yes,
            ),
            run_doctor=False,
        )
        if result:
            raise RuntimeError(f"installation returned {result}")
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        warn(f"import failed ({exc}); rolling back")
        restore_files()
        try:
            wanted_before = wanted(active_recipes(recipes_all, previous), previous)
            try:
                if saved[PY_DIR / "pyproject.toml"] is not None:
                    install_python(wanted_before["python"])
                if saved[NODE_DIR / "package.json"] is not None:
                    install_node(wanted_before["node"], require_npm=True)
            finally:
                do_sync(previous, recipes_all, wire=not args.no_wire)
        except (Exception, SystemExit, KeyboardInterrupt) as repair_error:
            warn(f"rollback repair failed ({repair_error}); run unienv install to repair")
        finally:
            restore_files()
        if isinstance(exc, KeyboardInterrupt):
            raise
        return 1
    log_event(event="import", batteries=state["batteries"])
    step("Checking")
    doctor_rc = cmd_doctor(argparse.Namespace(json=False, fix=False, quiet_ok=True))
    if doctor_rc:
        warn("Imported environment, but `unienv doctor` reported issues (see above).")
    info("Imported environment.")
    return 0


def cmd_status(args) -> int:
    """`unienv` with no arguments: a one-screen overview."""
    info(_c("1;36", "UniEnv AI") + f" {VERSION}  " + _c("2", "one always-ready environment for every AI tool"))
    if not MANIFEST_FILE.exists():
        info("\nNot installed yet. Run " + _c("1", "./install.sh") + " or " + _c("1", "unienv install") + ".")
        return 1
    m = json.loads(MANIFEST_FILE.read_text())
    py = [k for k, v in m["python"]["packages"].items() if v]
    bins = [b for b, path in m["bins"].items() if path]
    agents = [
        a["name"]
        for a in agent_targets()
        if detected(a) and read_block(a["instructions"], MD_BEGIN, MD_END) is not None
    ]
    info("")
    info(f"  {_c('2', 'home     ')} {str(UNIENV_HOME).replace(str(HOME), '~', 1)}")
    info(f"  {_c('2', 'batteries')} {', '.join(m['batteries'])}")
    info(f"  {_c('2', 'python   ')} ue-py · {len(py)} packages")
    info(f"  {_c('2', 'node     ')} ue-node · {len([v for v in m['node']['packages'].values() if v])} packages")
    info(f"  {_c('2', 'tools    ')} {', '.join(bins)}")
    info(f"  {_c('2', 'agents   ')} {', '.join(agents) or '(none configured)'}")
    info("")
    info(_c("2", "  unienv doctor      check everything      unienv need <pkg>   add a package"))
    info(_c("2", "  unienv list        show all packages     unienv --help       all commands"))
    return 0


REPO_TARBALL = "https://github.com/rupeshbharambe24/unienv-ai/archive/{ref}.tar.gz"
LATEST_RELEASE_API = "https://api.github.com/repos/rupeshbharambe24/unienv-ai/releases/latest"
RELEASE_TARBALL = "https://github.com/rupeshbharambe24/unienv-ai/releases/download/{ref}/unienv-ai-{ref}.tar.gz"
RELEASE_SHA256SUMS = "https://github.com/rupeshbharambe24/unienv-ai/releases/download/{ref}/SHA256SUMS"


# ── MCP server for desktop apps ─────────────────────────────────────────────


MCP_CLIENT_KEYS = {
    "claude-desktop": "mcpServers",
    "cursor": "mcpServers",
    "windsurf": "mcpServers",
    "lm-studio": "mcpServers",
    "vscode": "servers",
    "zed": "context_servers",
}


def mcp_clients() -> dict[str, Path]:
    """Config files of MCP clients that can't run shell commands themselves (or benefit anyway)."""
    return {
        "claude-desktop": (HOME / "Library/Application Support/Claude/claude_desktop_config.json")
        if IS_MAC
        else (HOME / ".config/Claude/claude_desktop_config.json"),
        "cursor": HOME / ".cursor/mcp.json",
        "windsurf": HOME / ".codeium/windsurf/mcp_config.json",
        "lm-studio": HOME / ".lmstudio/mcp.json",
        "vscode": (HOME / "Library/Application Support/Code/User/mcp.json")
        if IS_MAC
        else (HOME / ".config/Code/User/mcp.json"),
        "zed": HOME / ".config/zed/settings.json",
    }


def mcp_client_key(client: str | Path) -> str:
    if isinstance(client, Path):
        for name, path in mcp_clients().items():
            if path == client:
                return MCP_CLIENT_KEYS.get(name, "mcpServers")
        return "mcpServers"
    return MCP_CLIENT_KEYS.get(client, "mcpServers")


def _mcp_client_installed(name: str, path: Path) -> bool:
    return path.exists() or path.parent.exists()


def mcp_entry(client: str | None = None) -> dict:
    # absolute path: GUI apps start servers with a minimal PATH (the `spawn ENOENT` bug class)
    entry: dict = {"command": str(BIN / "unienv"), "args": ["mcp"]}
    if client == "vscode":
        return {"type": "stdio", **entry}
    return entry


def _zed_has_comments(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not re.search(r"(^|\s)//|/\*", raw, re.M):
        return False
    try:
        json.loads(raw)
        return False
    except json.JSONDecodeError:
        return True


def _load_json(path: Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        die(f"{path} is not valid JSON; fix it first (UniEnv won't overwrite it).")
    if not isinstance(data, dict):
        die(f"{path} is not a JSON object; fix it first (UniEnv won't overwrite it).")
    return data


def mcp_set(path: Path, entry: dict | None, *, key: str | None = None) -> bool:
    """Add (entry) or remove (None) the unienv server in an MCP client config, keeping a backup.

    Other servers are never touched. Returns True if the file changed.
    """
    if entry is None and not path.exists():
        return False
    resolved_key = key or mcp_client_key(path)
    data = _load_json(path)
    if entry is None:
        changed = False
        for k in uniq([resolved_key, "mcpServers"]):
            servers = data.get(k)
            if isinstance(servers, dict) and "unienv" in servers:
                servers.pop("unienv")
                changed = True
        if not changed:
            return False
    else:
        servers = data.get(resolved_key)
        if servers is None:
            servers = {}
            data[resolved_key] = servers
        elif not isinstance(servers, dict):
            die(f"{path} has invalid {resolved_key} (expected an object); fix it first.")
        if servers.get("unienv") == entry:
            return False
        servers["unienv"] = entry
    if path.exists():
        backup = path.with_name(path.name + f".unienv-backup-{_dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def cmd_mcp(args) -> int:
    if args.action == "serve":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import unienv_mcp

        return unienv_mcp.serve()
    if args.action == "config":
        root_key = mcp_client_key(args.client) if args.client != "all" else "mcpServers"
        entry_client = args.client if args.client != "all" else None
        print(json.dumps({root_key: {"unienv": mcp_entry(entry_client)}}, indent=2))
        return 0
    clients = mcp_clients()
    targets = list(clients) if args.client == "all" else [args.client]
    failed = False
    for name in targets:
        path = clients[name]
        if (
            args.action == "install"
            and name != "claude-desktop"
            and args.client == "all"
            and not _mcp_client_installed(name, path)
        ):
            continue  # only touch clients that are actually installed
        if name == "zed" and _zed_has_comments(path):
            if args.action == "install":
                info(f"  i {path} contains comments; paste this snippet into Zed (`unienv mcp config --client zed`):")
                print(json.dumps({mcp_client_key("zed"): {"unienv": mcp_entry("zed")}}, indent=2))
            continue
        try:
            changed = mcp_set(path, mcp_entry(name) if args.action == "install" else None, key=mcp_client_key(name))
        except SystemExit:
            failed = True
            warn(f"skipped {name}: fix its JSON configuration and retry")
            continue
        if changed:
            verb = "added to" if args.action == "install" else "removed from"
            info(f"  ✓ unienv MCP server {verb} {name} ({path})")
        elif args.action == "install":
            info(f"  ✓ {name}: already configured ({path})")
    if args.action == "install":
        info("\nRestart the app (quit it fully, then reopen) to load the UniEnv tools.")
        log_event(event="mcp-install", clients=targets)
    return int(failed)


def mcp_handshake(timeout: float = 15) -> tuple[bool, str, float]:
    """Start the MCP server exactly like a client would and complete initialize + tools/list."""
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "unienv-doctor", "version": VERSION},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    start = time.monotonic()
    try:
        r = subprocess.run(
            [mcp_entry()["command"], *mcp_entry()["args"]],
            input="\n".join(map(json.dumps, msgs)) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "HOME": str(HOME),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                **({"UNIENV_HOME": os.environ["UNIENV_HOME"]} if "UNIENV_HOME" in os.environ else {}),
            },
        )
        replies = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
        tools = next((m["result"]["tools"] for m in replies if m.get("id") == 2), None)
        ok = bool(tools) and replies[0].get("result", {}).get("serverInfo", {}).get("name") == "unienv"
        return (
            ok,
            (f"{len(tools)} tools" if tools else (r.stderr.strip()[-200:] or "no reply")),
            time.monotonic() - start,
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError, IndexError) as e:
        return False, f"{type(e).__name__}: {e}", time.monotonic() - start


def _version_tuple(ver: str) -> tuple[int, ...]:
    cleaned = ver.lstrip("v").split("-", 1)[0]
    return tuple(int(p) if p.isdigit() else 0 for p in cleaned.split("."))


def _latest_release_tag() -> str | None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(LATEST_RELEASE_API, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name")
        return str(tag) if tag else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def _is_release_tag(ref: str) -> bool:
    return bool(re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", ref))


def _fetch_release_sha256(ref: str) -> str:
    import urllib.error
    import urllib.request

    url = RELEASE_SHA256SUMS.format(ref=ref)
    target_name = f"unienv-ai-{ref}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, UnicodeDecodeError) as e:
        die(
            f"could not download UniEnv ref {ref!r} ({e}); "
            "check the branch or tag name, or pass `--sha256 <digest>` / `--ref main` to override"
        )
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        digest = parts[0].lower()
        name = parts[-1].lstrip("*")
        if re.fullmatch(r"[0-9a-f]{64}", digest) and name == target_name:
            return digest
    die(
        f"could not verify SHA256SUMS for release {ref!r} ({target_name} entry missing); "
        "pass `--sha256 <digest>` or `--ref main` to override"
    )
    return ""


def _safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball safely on both Python 3.11.4+ (filter='data') and 3.11.0-3.11.3."""
    dest_path = Path(dest).resolve()
    if getattr(tarfile, "data_filter", None) is not None:
        tar.extractall(dest_path, filter="data")
        return
    for member in tar.getmembers():
        name = member.name
        p = Path(name)
        if name.startswith("/") or p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe tar member path: {name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsafe tar member type: {name!r}; fallback accepts only files and directories")
        target = (dest_path / name).resolve()
        if target != dest_path and dest_path not in target.parents:
            raise ValueError(f"tar member escapes destination: {name!r}")
        if member.isdir():
            member.mode = 0o755
        else:
            member.mode = (member.mode | 0o600) & 0o755
    with contextlib.ExitStack() as stack:
        if hasattr(tar, "extraction_filter"):
            orig_filter = tar.extraction_filter
            tar.extraction_filter = staticmethod(lambda member, path: member)
            stack.callback(setattr, tar, "extraction_filter", orig_filter)
        tar.extractall(dest_path)


def cmd_update(args) -> int:
    """Download the latest UniEnv, replace ~/.unienv/share, then re-run install with the new code."""
    import hashlib
    import tempfile
    import urllib.error
    import urllib.request

    if getattr(args, "check", False):
        latest = _latest_release_tag()
        if not latest:
            die("could not query the latest UniEnv release tag")
        if _version_tuple(latest) > _version_tuple(VERSION):
            info(f"A newer version is available: {latest} (installed: v{VERSION}). Run `unienv update`.")
        else:
            info(f"UniEnv v{VERSION} is up to date (latest: {latest}).")
        return 0

    share = UNIENV_HOME / "share"
    if share.is_symlink():
        die(f"{share} links to a development checkout; update it with `git pull` instead.")
    ref = getattr(args, "ref", None)
    explicit_ref = bool(ref)
    if not ref:
        ref = _latest_release_tag()
        if not ref:
            die(
                "could not resolve the latest UniEnv release tag; "
                "pass `unienv update --ref main` to install from main without release verification"
            )
    is_release = not explicit_ref or _is_release_tag(ref)
    explicit_sha = getattr(args, "sha256", None)
    expected_sha = explicit_sha
    if not expected_sha and is_release:
        expected_sha = _fetch_release_sha256(ref)
    if not is_release and not expected_sha:
        warn(f"installing {ref} without checksum verification")

    no_wire = getattr(args, "no_wire", False) or not load_state(load_recipes()).get("options", {}).get(
        "wire_shells", True
    )
    use_release_asset = is_release and not explicit_sha
    url = RELEASE_TARBALL.format(ref=ref) if use_release_asset else REPO_TARBALL.format(ref=ref)
    step(f"Downloading {url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "unienv.tar.gz"
        try:
            with urllib.request.urlopen(url) as resp, archive.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            die(f"could not download UniEnv ref {ref!r} ({e}); check the branch or tag name")
        if expected_sha:
            actual_sha = hashlib.sha256(archive.read_bytes()).hexdigest().lower()
            if actual_sha != expected_sha.lower():
                die(f"SHA-256 mismatch for {ref}: expected {expected_sha.lower()}, got {actual_sha}")

        prev_share = UNIENV_HOME / "share.prev"
        new_share = UNIENV_HOME / "share.new"
        shutil.rmtree(prev_share, ignore_errors=True)
        shutil.rmtree(new_share, ignore_errors=True)
        if share.exists():
            shutil.copytree(share, prev_share)
        install_args = ["install"] + (["--no-wire"] if no_wire else [])
        try:
            with tarfile.open(archive) as tar:
                _safe_extract_tar(tar, Path(tmp))
            src = next(Path(tmp).glob("unienv-ai-*"))
            new_share.mkdir(parents=True)
            for part in ("src", "recipes", "templates", "shims"):
                shutil.copytree(src / part, new_share / part)
            shutil.rmtree(share, ignore_errors=True)
            new_share.rename(share)
            step("Re-installing with the new version")
            cli = share / "src/unienv.py"
            rc = subprocess.call([sys.executable, str(cli), *install_args])
            if rc != 0:
                raise RuntimeError(f"install exited with code {rc}")
        except BaseException as e:
            warn(f"update failed ({e}); restoring previous share from {prev_share}")
            shutil.rmtree(new_share, ignore_errors=True)
            if prev_share.exists():
                shutil.rmtree(share, ignore_errors=True)
                prev_share.rename(share)
                old_cli = share / "src/unienv.py"
                if old_cli.exists():
                    try:
                        restore_rc = subprocess.call([sys.executable, str(old_cli), *install_args])
                        if restore_rc != 0:
                            warn(f"rollback restore exited with code {restore_rc}; run unienv install to repair")
                    except BaseException as restore_err:
                        warn(f"rollback restore failed ({restore_err}); run unienv install to repair")
            if not isinstance(e, Exception):
                raise
            return 1
        shutil.rmtree(prev_share, ignore_errors=True)
        return 0


# ── doctor ──────────────────────────────────────────────────────────────────


def _clean_shell_probe(argv: list[str], *, extra_env: dict[str, str] | None = None) -> str | None:
    """Run `command -v ue-py` in a shell started from a near-empty environment, like an agent or GUI app."""
    env = {
        "HOME": str(HOME),
        "USER": os.environ.get("USER", ""),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "dumb",
    }
    if "UNIENV_HOME" in os.environ:
        env["UNIENV_HOME"] = os.environ["UNIENV_HOME"]
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip().splitlines()
    return out[-1] if r.returncode == 0 and out else None


def cmd_doctor(args) -> int:
    checks: list[dict] = []

    def add(cid, status, msg, fix=None):
        checks.append({"id": cid, "status": status, "message": msg, **({"fix": fix} if fix else {})})

    recipes_all = load_recipes()
    state = load_state(recipes_all)
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)

    if not UNIENV_HOME.exists():
        add("prefix", "fail", f"{UNIENV_HOME} does not exist", "./install.sh  (or: unienv install)")
        return _report(checks, args)
    add("prefix", "ok", str(UNIENV_HOME))

    # Python environment and imports
    py = venv_python()
    imports = uniq([m for r in recipes for m in r.get("python", {}).get("imports", [])])
    if not py.exists() or not os.access(py, os.X_OK):
        dangling = py.is_symlink()
        add(
            "python",
            "fail",
            f"shared Python base interpreter missing (dangling symlink: {os.readlink(py)})"
            if dangling
            else "shared Python env missing",
            "curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash"
            if dangling
            else "unienv install",
        )
    else:
        code = (
            "import importlib, json, sys\nbad = {}\n"
            f"for m in {imports!r}:\n"
            "    try: importlib.import_module(m)\n"
            "    except Exception as e: bad[m] = f'{type(e).__name__}: {e}'[:120]\n"
            "print(json.dumps({'v': sys.version.split()[0], 'bad': bad}))"
        )
        r = run([str(py), "-P", "-c", code], capture=True, check=False, timeout=120)  # -P: ignore modules in the cwd
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
            add("python", "ok", f"Python {res['v']} at {py}")
            for m, err in res["bad"].items():
                add(f"import:{m}", "fail", f"cannot import {m}: {err}", "unienv install")
            if not res["bad"] and imports:
                add("python-imports", "ok", f"{len(imports)} battery modules import cleanly")
        except (json.JSONDecodeError, IndexError, KeyError):
            add("python", "fail", f"shared Python is broken: {(r.stderr or '').strip()[:200]}", "unienv install")

    # Node packages
    for name, ver in node_packages(w["node"]).items():
        if ver:
            add(f"node:{name}", "ok", f"{name:<13} {ver} (node)")
        else:
            add(f"node:{name}", "fail", f"Node package {name} missing", "unienv install")
    if w["node"] and not which("node"):
        add("node", "fail", "node not found", "unienv need --bin node")

    # System binaries
    for b in w["bins"]:
        p = resolve_bin(b)
        if p:
            add(f"bin:{b}", "ok", f"{b:<13} {p}")
        else:
            owner = next((r["id"] for r in recipes if b in r.get("verify", {}).get("bins", [])), "?")
            add(f"bin:{b}", "fail", f"{b} not found (battery: {owner})", "unienv install")

    for b in w["skipped_bins"]:
        add(
            f"bin:{b}",
            "info",
            f"{b} skipped by your choice (exclude: {', '.join(state['options']['exclude'])})",
            "to add it later: unienv need --cask/--bin <package>",
        )

    # Shell wiring: what agents and GUI apps actually see
    for rc in SHELL_FILES:
        if not rc.exists():
            continue
        try:
            rc_text, _ = _read_block_text(rc)
        except OSError:
            continue
        if _has_malformed_block(rc_text, SHELL_BEGIN, SHELL_END):
            short = str(rc).replace(str(HOME), "~")
            add(
                f"block:{rc.name}",
                "fail",
                f"{short} has an incomplete UniEnv block (missing end marker)",
                f"restore `{SHELL_END}` or delete the orphaned `{SHELL_BEGIN}` line in {short}, then run `unienv sync`",
            )
    target = str(BIN / "ue-py")
    probes = [
        ("zsh -c", ["/bin/zsh", "-c", "command -v ue-py"], None),
        ("zsh -lc", ["/bin/zsh", "-lc", "command -v ue-py"], None),
        ("bash -lc", ["/bin/bash", "-lc", "command -v ue-py"], None),
    ]
    zdot = _zdotdir()
    if zdot is not None:
        probes.append(("ZDOTDIR zsh -c", ["/bin/zsh", "-c", "command -v ue-py"], {"ZDOTDIR": str(zdot)}))
    for label, argv, extra_env in probes:
        if not Path(argv[0]).exists():
            continue
        got = _clean_shell_probe(argv) if extra_env is None else _clean_shell_probe(argv, extra_env=extra_env)
        if got == target:
            add(f"shell:{label}", "ok", f"`{label}` finds ue-py")
        else:
            add(
                f"shell:{label}",
                "fail",
                f"`{label}` (fresh environment) cannot find ue-py",
                "unienv sync   # rewrites ~/.zshenv, ~/.profile, ~/.bashrc blocks",
            )

    if IS_MAC:
        r = run(["launchctl", "getenv", "PATH"], capture=True, check=False)
        if str(BIN) in (r.stdout or ""):
            add("gui-path", "ok", "launchd PATH includes UniEnv (GUI apps see it)")
        else:
            add(
                "gui-path",
                "info",
                "GUI apps (Claude Desktop, Dock-launched Cursor) use the system PATH; "
                "use absolute paths from manifest.json in their configs",
                "optional, experimental: unienv wire --gui",
            )

    # Agent instruction files are present and current
    if MANIFEST_FILE.exists():
        manifest = json.loads(MANIFEST_FILE.read_text())
        expected = render_agent_block(manifest).rstrip()
        for a in agent_targets():
            if not detected(a):
                continue
            inst = a["instructions"]
            short = str(inst).replace(str(HOME), "~")
            try:
                inst_text = _read_block_text(inst)[0] if inst.exists() else ""
            except OSError as exc:
                add(f"agent:{a['id']}", "warn", f"{a['name']}: cannot read {short}: {exc}")
                continue
            if _has_malformed_block(inst_text, MD_BEGIN, MD_END):
                add(
                    f"agent:{a['id']}",
                    "fail",
                    f"{a['name']}: {short} has an incomplete UniEnv block (missing end marker)",
                    f"restore `{MD_END}` or delete the orphaned `{MD_BEGIN}` line in {short}, then run `unienv sync`",
                )
                continue
            cur = read_block(inst, MD_BEGIN, MD_END)
            if cur is None:
                add(f"agent:{a['id']}", "fail", f"{a['name']}: no UniEnv block in {short}", "unienv sync")
            elif cur.rstrip() != expected:
                add(f"agent:{a['id']}", "warn", f"{a['name']}: UniEnv block in {short} is outdated", "unienv sync")
            else:
                add(f"agent:{a['id']}", "ok", f"{a['name']}: {short}")
    else:
        add("manifest", "fail", "manifest.json missing", "unienv sync")

    # Homebrew Python is externally managed; agents must not pip-install into it
    bp = brew_prefix()
    if bp and Path(bp, "bin/python3").exists():
        r = run(
            [
                f"{bp}/bin/python3",
                "-I",  # isolated: no cwd on sys.path, whatever this Python's version
                "-c",
                "import sysconfig,os;"
                "print(os.path.exists(os.path.join(sysconfig.get_path('stdlib'),'EXTERNALLY-MANAGED')))",
            ],
            capture=True,
            check=False,
        )
        if r.stdout.strip() == "True":
            add(
                "pep668",
                "info",
                f"{bp}/bin/python3 is externally managed (PEP 668); "
                "agents are told to use ue-py / `unienv need` instead of pip",
            )

    # MCP server for desktop apps: configured clients must point at a server that answers
    configured = []
    for n, p in mcp_clients().items():
        if not p.exists():
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace").strip()
            data = json.loads(raw) if raw else {}
        except (OSError, json.JSONDecodeError):
            if n == "zed" and _zed_has_comments(p):
                continue
            add(f"mcp:{n}", "warn", f"{p} is not valid JSON; can't check the unienv entry")
            continue
        if not isinstance(data, dict):
            add(f"mcp:{n}", "warn", f"{p} is not a JSON object; can't check the unienv entry")
            continue
        key = mcp_client_key(n)
        servers = data.get(key)
        if servers is None:
            continue
        if not isinstance(servers, dict):
            add(f"mcp:{n}", "warn", f"{p} has invalid {key} (expected an object); can't check the unienv entry")
            continue
        if "unienv" in servers:
            configured.append(n)
    if configured:
        ok, detail, secs = mcp_handshake()
        if ok:
            add("mcp", "ok", f"MCP server answers in {secs:.1f}s ({detail}); configured for {', '.join(configured)}")
        else:
            add(
                "mcp",
                "fail",
                f"MCP server configured for {', '.join(configured)} but the handshake failed: {detail}",
                "unienv mcp install --client all   # rewrites the entry with absolute paths",
            )
    elif IS_MAC and mcp_clients()["claude-desktop"].parent.exists():
        add("mcp", "info", "Claude Desktop chat can't use UniEnv yet", "unienv mcp install")

    # Aliases that hang or prompt in agent shells. Claude Code replays your aliases into
    # every command; eza >= 0.23 waits on an open stdin, `-i` flags wait for a "y".
    hostile = {
        r"eza|exa": "reads file names from stdin when it isn't a terminal, so agent commands hang until timeout",
        r"lsd|bat|batcat|less|more": "pages or decorates output meant for programs",
        r"(rm|cp|mv)\b.*\s-i\b|(rm|cp|mv)\s+-\w*i": "asks for confirmation, which an agent can never answer",
    }
    alias_re = re.compile(r"""^\s*alias\s+(ls|ll|la|cat|grep|find|rm|cp|mv|cd|less)=['"]?([^'"\n]+)""", re.M)
    for rc in (".zshrc", ".bashrc", ".bash_aliases", ".zshenv", ".zprofile", ".profile"):
        p = HOME / rc
        if not p.exists():
            continue
        text = p.read_text(errors="ignore")
        guarded = "CLAUDECODE" in text or "AI_AGENT" in text
        for name, target in alias_re.findall(text):
            why = next(
                (
                    w
                    for pat, w in hostile.items()
                    if re.match(rf"\s*({pat})\b", target)
                    or (name in ("rm", "cp", "mv") and re.search(pat, f"{name} {target}"))
                ),
                None,
            )
            if why and not guarded:
                add(
                    f"alias:{rc}:{name}",
                    "warn",
                    f"~/{rc}: alias {name}='{target.strip()}' {why}",
                    'wrap interactive-only aliases: if [ -z "${AI_AGENT:-}${CLAUDECODE:-}" ]; then ... fi',
                )

    # Secrets hygiene: report variable names only, never values
    pat = re.compile(r"^\s*export\s+([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=", re.M)
    for rc in (".zshrc", ".zshenv", ".zprofile", ".bashrc", ".bash_profile", ".profile"):
        p = HOME / rc
        if p.exists():
            names = pat.findall(p.read_text(errors="ignore"))
            if names:
                add(
                    f"secrets:{rc}",
                    "warn",
                    f"~/{rc} exports secrets to every process an agent runs: {', '.join(sorted(set(names)))}",
                    "load them only where needed (e.g. a keychain lookup)",
                )
    cdc = HOME / "Library/Application Support/Claude/claude_desktop_config.json"
    if cdc.exists():
        try:
            servers = json.loads(cdc.read_text()).get("mcpServers", {}) or {}
            leaky = [
                f"{s}.{k}"
                for s, cfg in servers.items()
                for k, v in (cfg.get("env") or {}).items()
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and v and not str(v).startswith("$")
            ]
            if leaky:
                add(
                    "secrets:claude-desktop",
                    "warn",
                    f"plaintext secrets in claude_desktop_config.json: {', '.join(leaky)}",
                )
        except (json.JSONDecodeError, AttributeError):
            pass

    sync_needed = any(
        c["status"] in ("fail", "warn") and (c["id"] == "manifest" or c["id"].startswith(("shell:", "agent:")))
        for c in checks
    )
    if getattr(args, "fix", False) and sync_needed:
        print("Applying safe fixes: unienv sync", file=sys.stderr if args.json else sys.stdout, flush=True)
        do_sync(state, recipes_all, quiet=True)
        return cmd_doctor(argparse.Namespace(json=args.json, fix=False, quiet_ok=False))
    return _report(checks, args)


def _report(checks: list[dict], args) -> int:
    failed = sum(c["status"] == "fail" for c in checks)
    warned = sum(c["status"] == "warn" for c in checks)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"unienv": VERSION, "ok": failed == 0, "failed": failed, "warnings": warned, "checks": checks}, indent=2
            )
        )
        return 1 if failed else 0
    icon = {"ok": _c("32", "✓"), "fail": _c("31", "✗"), "warn": _c("33", "!"), "info": _c("34", "i")}
    quiet_ok = getattr(args, "quiet_ok", False)
    oks = 0
    for c in checks:
        if c["status"] == "ok" and quiet_ok:
            oks += 1
            continue
        info(f"  {icon[c['status']]} {c['message']}")
        if c.get("fix") and c["status"] != "ok":
            info(_c("2", f"      fix: {c['fix']}"))
    if quiet_ok and oks:
        info(f"  {icon['ok']} {oks} checks passed")
    summary = f"{failed} failed, {warned} warnings" if failed or warned else "all good"
    info(("\n" if not quiet_ok else "") + _c("1", f"unienv doctor: {summary}"))
    return 1 if failed else 0


# ── wire / uninstall ────────────────────────────────────────────────────────


def cmd_wire(args) -> int:
    if args.gui:
        if not IS_MAC:
            die("--gui is macOS only")
        path = os.pathsep.join(
            [str(BIN)]
            + ([f"{brew_prefix()}/bin"] if brew_prefix() else [])
            + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        )
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.unienv.path</string>
  <key>ProgramArguments</key><array>
    <string>/bin/launchctl</string><string>setenv</string><string>PATH</string><string>{path}</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""")
        run(["launchctl", "setenv", "PATH", path])
        info("✓ GUI PATH set (experimental). Quit and reopen desktop apps to pick it up.")
        return 0
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    changed = wire_shells(state)
    state["options"]["wire_shells"] = True
    save_state(state)
    info("✓ shell files updated: " + (", ".join(changed) if changed else "already wired"))
    return 0


def _remove_windows_path() -> None:
    marker = UNIENV_HOME / "windows-path.json"
    if not IS_WIN or not marker.exists():
        return
    import winreg

    owned = json.loads(marker.read_text(encoding="utf-8-sig"))["entry"]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        current, kind = winreg.QueryValueEx(key, "Path")
        parts = current.split(";")
        for i, part in enumerate(parts):
            if os.path.normcase(part.rstrip("\\\\/")) == os.path.normcase(owned.rstrip("\\\\/")):
                del parts[i]
                break
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts))
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x001A,  # WM_SETTINGCHANGE
            0,
            "Environment",
            0x0002,  # SMTO_ABORTIFHUNG
            5000,
            None,
        )
    marker.unlink()


def cmd_uninstall(args) -> int:
    _uninstall_sandbox()
    _remove_windows_path()
    for rc in _shell_files():
        try:
            if remove_block(rc, SHELL_BEGIN, SHELL_END):
                info(f"  removed block from {rc}")
        except OSError as e:
            warn(f"could not update {rc} ({e}); remove the UniEnv block manually")
    for a in agent_targets():
        try:
            if remove_block(a["instructions"], MD_BEGIN, MD_END):
                info(f"  removed block from {a['instructions']}")
            if a.get("owned_rules_file") and a["instructions"].exists() and not a["instructions"].read_text().strip():
                a["instructions"].unlink()
        except OSError as e:
            warn(f"could not update {a['instructions']} ({e}); remove the UniEnv block manually")
        if a["skills"] is not None and (a["skills"] / "unienv").exists():
            try:
                shutil.rmtree(a["skills"] / "unienv")
                info(f"  removed {a['skills'] / 'unienv'}")
            except OSError as e:
                warn(f"could not remove {a['skills'] / 'unienv'} ({e})")
    # desktop apps would otherwise keep trying to start a server that no longer exists
    for name, path in mcp_clients().items():
        if name == "zed" and _zed_has_comments(path):
            continue
        try:
            if mcp_set(path, None, key=mcp_client_key(name)):
                info(f"  removed the unienv MCP server from {name} ({path})")
        except SystemExit:  # invalid JSON: _load_json already explained; keep uninstalling
            pass
    if LAUNCH_AGENT.exists():
        LAUNCH_AGENT.unlink()
        run(["launchctl", "unsetenv", "PATH"], check=False, quiet=True)
        info(f"  removed {LAUNCH_AGENT}")
    if args.purge and UNIENV_HOME.exists():
        shutil.rmtree(UNIENV_HOME)
        info(f"  deleted {UNIENV_HOME}")
    info("✓ UniEnv unwired. System packages installed with Homebrew/apt were left in place.")
    return 0


# ── sandbox profiles ────────────────────────────────────────────────────────


def _sandbox_read_roots() -> list[str]:
    return [str(UNIENV_HOME)]


def _toml_text(data: dict) -> str:
    """Serialize parsed TOML values without changing their types or table scope."""

    def value(item):
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if isinstance(item, bool):
            return str(item).lower()
        if isinstance(item, (int, float)):
            return repr(item)
        if isinstance(item, list):
            return "[" + ", ".join(value(v) for v in item) + "]"
        if isinstance(item, dict):
            return (
                "{ " + ", ".join(json.dumps(k, ensure_ascii=False) + " = " + value(v) for k, v in item.items()) + " }"
            )
        if isinstance(item, (_dt.datetime, _dt.date, _dt.time)):
            return item.isoformat()
        raise ValueError(f"unsupported TOML value: {type(item).__name__}")

    def fmt_key(k: str) -> str:
        return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)

    scalars = [f"{fmt_key(k)} = {value(v)}" for k, v in data.items() if not isinstance(v, dict)]
    tables = []
    for k, v in data.items():
        if isinstance(v, dict):
            body = "\n".join(f"{fmt_key(sk)} = {value(sv)}" for sk, sv in v.items())
            tables.append(f"[{fmt_key(k)}]\n{body}" if body else f"[{fmt_key(k)}]")
    parts = (["\n".join(scalars)] if scalars else []) + tables
    return "\n\n".join(parts) + "\n" if parts else ""


def _edit_codex_sandbox_toml(
    raw: str,
    desired_roots: list[str] | None,
    *,
    remove_table_if_empty: bool = False,
) -> str:
    """Surgically update only [sandbox_workspace_write].writable_roots in TOML text."""
    roots_lit = (
        "[" + ", ".join(json.dumps(r, ensure_ascii=False) for r in desired_roots) + "]"
        if desired_roots is not None
        else None
    )
    hdr = re.search(r"(?m)^[ \t]*\[sandbox_workspace_write\][ \t]*(?:#.*)?$", raw)
    if hdr is None:
        if roots_lit is None:
            return raw
        sep = "" if not raw else ("\n" if raw.endswith("\n") else "\n\n")
        return f"{raw}{sep}[sandbox_workspace_write]\nwritable_roots = {roots_lit}\n"

    next_hdr = re.search(r"(?m)^[ \t]*\[(?:\[)?[^\]\n]+\](?:\])?[ \t]*(?:#.*)?$", raw[hdr.end() :])
    sec_start = hdr.end()
    sec_end = sec_start + next_hdr.start() if next_hdr else len(raw)
    sec = raw[sec_start:sec_end]
    wr = re.search(
        r"(?ms)^([ \t]*writable_roots[ \t]*=[ \t]*)(\[[^\]]*\])([ \t]*(?:#[^\n]*)?)(?:\r?\n|$)",
        sec,
    )
    if roots_lit is not None:
        if wr is not None:
            new_sec = sec[: wr.start(2)] + roots_lit + sec[wr.end(2) :]
        else:
            new_sec = f"\nwritable_roots = {roots_lit}" + sec
        return raw[:sec_start] + new_sec + raw[sec_end:]

    # Removing writable_roots key (and optionally the table if empty)
    if wr is not None:
        new_sec = sec[: wr.start()] + sec[wr.end() :]
    else:
        new_sec = sec
    if remove_table_if_empty:
        remaining = [ln.strip() for ln in new_sec.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not remaining:
            prefix = raw[: hdr.start()].rstrip("\n")
            suffix = raw[sec_end:].lstrip("\n")
            if prefix and suffix:
                return prefix + "\n\n" + suffix
            if prefix:
                return prefix + "\n"
            return suffix
    return raw[:sec_start] + new_sec + raw[sec_end:]


def sandbox_profile(tool: str) -> str:
    root = str(UNIENV_HOME)
    if tool == "claude":
        return json.dumps(
            {
                "permissions": {"allow": [f"Read(/{UNIENV_HOME.as_posix()}/**)"]},
                "sandbox": {"filesystem": {"allowRead": [root], "allowWrite": [root]}},
            },
            indent=2,
            ensure_ascii=False,
        )
    if tool == "codex":
        return f"[sandbox_workspace_write]\nwritable_roots = [{json.dumps(root, ensure_ascii=False)}]\n"
    if tool in ("gemini", "seatbelt"):
        escaped = json.dumps(root, ensure_ascii=False)
        return (
            ";; Merge into an existing Seatbelt profile; this is not a complete policy.\n"
            f"(allow file-read* file-map-executable (subpath {escaped}))\n"
            f"(allow file-write* (subpath {escaped}))"
        )
    die(f"unknown sandbox tool: {tool}")


def _sandbox_load(path: Path, tool: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
        data = tomllib.loads(raw) if tool == "codex" else (json.loads(raw) if raw.strip() else {})
        if not isinstance(data, dict):
            raise ValueError("expected a configuration object")
        return data
    except (ValueError, OSError) as exc:
        die(f"cannot read sandbox settings {path}: {exc}")


def _sandbox_state() -> tuple[Path, dict]:
    path = UNIENV_HOME / "sandbox-settings.json"
    return path, _sandbox_load(path, "claude")


def _read_text_with_eol(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "", "\n"
    with path.open("r", encoding="utf-8", newline="") as f:
        raw = f.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _write_preserving_link_and_mode(path: Path, text: str, *, eol: str = "\n") -> None:
    target = Path(os.path.realpath(path)) if path.is_symlink() else path
    mode = (target.stat().st_mode & 0o7777) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp.{os.getpid()}"
    data = text.replace("\n", eol).encode("utf-8")
    try:
        tmp.write_bytes(data)
        if mode is not None:
            with contextlib.suppress(OSError):
                tmp.chmod(mode)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _sandbox_write(
    path: Path,
    data: dict,
    tool: str,
    *,
    raw_text: str | None = None,
    action: str = "install",
    eol: str = "\n",
) -> None:
    if tool == "codex":
        text = raw_text if raw_text is not None else _toml_text(data)
        hint = (
            f"remove {UNIENV_HOME} from writable_roots manually"
            if action == "uninstall"
            else f"add this snippet manually:\n{sandbox_profile('codex')}"
        )
        try:
            parsed = tomllib.loads(text)
        except ValueError as exc:
            die(f"cannot safely edit {path} ({exc}); {hint}")
        if parsed != data:
            die(f"cannot safely update {path} without altering existing tables; {hint}")
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        backup = path.with_name(path.name + f".unienv-backup-{_dt.datetime.now():%Y%m%d-%H%M%S-%f}")
        shutil.copy2(path, backup)
    _write_preserving_link_and_mode(path, text, eol=eol)


def _sandbox_fields(tool: str) -> dict[tuple[str, ...], list[str]]:
    root = str(UNIENV_HOME)
    if tool == "codex":
        return {("sandbox_workspace_write", "writable_roots"): [root]}
    return {
        ("permissions", "allow"): [f"Read(/{UNIENV_HOME.as_posix()}/**)"],
        ("sandbox", "filesystem", "allowRead"): [root],
        ("sandbox", "filesystem", "allowWrite"): [root],
    }


def _install_sandbox(path: Path, tool: str) -> bool:
    raw, eol = _read_text_with_eol(path)
    data = _sandbox_load(path, tool)
    state_path, state = _sandbox_state()
    key = str(path.resolve())
    record = state.get(key, {"tool": tool, "added": {}, "created": [], "new_file": not path.exists()})
    changed = False
    for fields, additions in _sandbox_fields(tool).items():
        parent = data
        for index, field in enumerate(fields[:-1]):
            if field not in parent:
                parent[field] = {}
                record["created"].append(list(fields[: index + 1]))
            if not isinstance(parent[field], dict):
                die(f"{path}: {'.'.join(fields[: index + 1])} must be a table/object")
            parent = parent[field]
        name = fields[-1]
        if name not in parent:
            parent[name] = []
            record["created"].append(list(fields))
        values = parent[name]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            die(f"{path}: {'.'.join(fields)} must be a list of strings")
        for item in additions:
            if item not in values:
                values.append(item)
                record["added"].setdefault(".".join(fields), []).append(item)
                changed = True
    if not changed:
        return False
    raw_text = (
        _edit_codex_sandbox_toml(raw, data["sandbox_workspace_write"]["writable_roots"]) if tool == "codex" else None
    )
    _sandbox_write(path, data, tool, raw_text=raw_text, action="install", eol=eol)
    state[key] = record
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return True


def _uninstall_sandbox() -> None:
    try:
        state_path, state = _sandbox_state()
    except (Exception, SystemExit) as exc:
        detail = "" if isinstance(exc, SystemExit) else f": {exc}"
        warn(f"could not read sandbox state{detail}")
        return
    for key, record in list(state.items()):
        path = Path(key)
        try:
            raw, eol = _read_text_with_eol(path)
            data = _sandbox_load(path, record["tool"])
            for dotted, items in record["added"].items():
                fields = dotted.split(".")
                parent = data
                for field in fields[:-1]:
                    parent = parent.get(field, {}) if isinstance(parent, dict) else {}
                values = parent.get(fields[-1]) if isinstance(parent, dict) else None
                if isinstance(values, list):
                    parent[fields[-1]] = [v for v in values if v not in items]
            for fields in sorted(record["created"], key=len, reverse=True):
                parent = data
                for field in fields[:-1]:
                    parent = parent.get(field, {}) if isinstance(parent, dict) else {}
                if isinstance(parent, dict) and parent.get(fields[-1]) in ({}, []):
                    parent.pop(fields[-1], None)
            if path.exists():
                if record["new_file"] and not data:
                    target = Path(os.path.realpath(path)) if path.is_symlink() else path
                    if path.is_symlink():
                        path.unlink()
                    if target.exists():
                        target.unlink()
                else:
                    raw_text = None
                    if record["tool"] == "codex":
                        sww = data.get("sandbox_workspace_write")
                        desired_roots = sww.get("writable_roots") if isinstance(sww, dict) else None
                        raw_text = _edit_codex_sandbox_toml(
                            raw,
                            desired_roots,
                            remove_table_if_empty=["sandbox_workspace_write"] in record["created"],
                        )
                    _sandbox_write(path, data, record["tool"], raw_text=raw_text, action="uninstall", eol=eol)
            del state[key]
        except (Exception, SystemExit) as exc:
            detail = "" if isinstance(exc, SystemExit) else f": {exc}"
            warn(f"could not remove sandbox settings from {path}{detail}")
    if state:
        atomic_write(state_path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    elif state_path.exists():
        state_path.unlink()


def _install_claude_sandbox(path: Path) -> bool:
    return _install_sandbox(path, "claude")


def _install_codex_sandbox(path: Path) -> bool:
    return _install_sandbox(path, "codex")


def cmd_sandbox(args) -> int:
    if args.action == "print":
        tools = ("claude", "codex", "seatbelt") if args.tool == "all" else (args.tool,)
        for tool in tools:
            print(sandbox_profile(tool))
        return 0
    if args.tool not in ("claude", "codex", "all"):
        die("sandbox install supports claude, codex, or all")
    tools = ("claude", "codex") if args.tool == "all" else (args.tool,)
    for tool in tools:
        path = (
            HOME / ".claude/settings.json"
            if tool == "claude"
            else Path(os.environ.get("CODEX_HOME", HOME / ".codex")) / "config.toml"
        )
        if args.tool == "all" and not path.parent.exists() and not which(tool):
            info(f"  {tool}: skipped (not detected)")
            continue
        changed = _install_sandbox(path, tool)
        info(f"  {tool}: {'updated' if changed else 'already configured'} ({path})")
        if tool == "codex":
            info("  Codex writable_roots applies in workspace-write mode; the current sandbox mode was not changed.")
    return 0


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        rpath = Path(root)
        for name in files:
            try:
                total += (rpath / name).lstat().st_size
            except OSError:
                pass
    return total


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{int(val)} B" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{int(n)} B"


def cmd_du(args) -> int:
    sections = [
        ("py", PY_DIR),
        ("node", NODE_DIR),
        ("backups", BACKUP_DIR),
    ]
    if (UNIENV_HOME / "cache").exists():
        sections.insert(2, ("cache", UNIENV_HOME / "cache"))
    for label, path in sections:
        info(f"  {label:<10} {_fmt_bytes(_dir_size(path)):>10}  ({path})")
    total = _dir_size(UNIENV_HOME)
    info(f"  {'total':<10} {_fmt_bytes(total):>10}  ({UNIENV_HOME})")
    return 0


# ── entry point ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "_soffice-safe":
        import tempfile

        soffice = os.environ.get("UNIENV_SOFFICE") or libreoffice_path()
        if not soffice:
            warn("LibreOffice is not installed")
            return 127
        with tempfile.TemporaryDirectory(prefix="ue_lo_") as profile:
            return run(
                [
                    soffice,
                    "-env:UserInstallation=" + Path(profile).as_uri(),
                    "--headless",
                    "--invisible",
                    "--norestore",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    *argv[1:],
                ],
                check=False,
                quiet=True,
            ).returncode
    p = argparse.ArgumentParser(
        prog="unienv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="UniEnv AI: one always-ready environment for every AI tool.",
        epilog="""examples:
  unienv                      status overview
  unienv doctor               check tools, shells and AI-tool wiring
  unienv need seaborn         add a Python package for every AI tool
  unienv need --bin ffmpeg    add a system tool (Homebrew formula / apt package)
  unienv install --add media  enable an opt-in battery

docs: https://github.com/rupeshbharambe24/unienv-ai""",
    )
    p.add_argument("--version", action="version", version=f"unienv {VERSION}")
    sub = p.add_subparsers(dest="cmd")
    p.set_defaults(fn=cmd_status)

    s = sub.add_parser("install", help="install batteries and wire every shell and AI tool")
    s.add_argument("--batteries", help="comma-separated list replacing the current selection")
    s.add_argument("--add", help="comma-separated batteries to add (e.g. media,fonts)")
    s.add_argument("--exclude", help="system packages to skip and remember (e.g. libreoffice)")
    s.add_argument("--no-system", action="store_true", help="skip Homebrew/apt packages")
    s.add_argument("--no-wire", action="store_true", help="don't edit shell startup files")
    s.add_argument("--all-agents", action="store_true", help="write instruction files even for agents not detected")
    s.add_argument(
        "-y", "--yes", action="store_true", help="skip battery picker and allow sudo apt-get/dnf/pacman on Linux"
    )
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("need", help="permanently add a missing package for every AI tool")
    s.add_argument("packages", nargs="+")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--node", action="store_true", help="npm package")
    g.add_argument("--bin", action="store_true", help="system tool (Homebrew formula / apt package)")
    g.add_argument("--cask", action="store_true", help="macOS app (Homebrew cask)")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_need)

    s = sub.add_parser("remove", help="remove a package previously added with `unienv need`")
    s.add_argument("packages", nargs="+")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--node", action="store_true", help="npm package")
    g.add_argument("--bin", action="store_true", help="system tool (Homebrew formula / apt package)")
    g.add_argument("--cask", action="store_true", help="macOS app (Homebrew cask)")
    s.add_argument("-y", "--yes", action="store_true", help="also uninstall system packages via brew/apt")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("upgrade", help="upgrade installed Python and Node packages with automatic rollback")
    s.add_argument("packages", nargs="*", help="specific packages to upgrade (default: all)")
    s.add_argument("--node", action="store_true", help="upgrade Node packages")
    s.add_argument("--dry-run", action="store_true", help="show what would be upgraded without changing anything")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("doctor", help="verify tools, shell wiring, agent files and secrets hygiene")
    s.add_argument("--json", action="store_true", help="machine-readable output for agents and CI")
    s.add_argument("--fix", action="store_true", help="apply safe fixes (regenerate wiring), then re-check")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("sync", help="regenerate shims, env.sh, manifest and agent instruction files")
    s.add_argument("--no-wire", action="store_true")
    s.add_argument("--all-agents", action="store_true")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("list", help="show batteries, packages and tools")
    s.add_argument("--json", action="store_true", help="print manifest.json")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("export", help="export batteries, excludes and extras as portable TOML")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("import", help="restore an environment exported with `unienv export`")
    s.add_argument("file", help="path to exported TOML file (or '-' for stdin)")
    s.add_argument("--no-system", action="store_true", help="skip Homebrew/apt packages")
    s.add_argument("--no-wire", action="store_true", help="don't edit shell startup files")
    s.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt and allow sudo apt-get on Linux")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("wire", help="(re)write shell startup blocks; --gui sets the macOS GUI PATH")
    s.add_argument("--gui", action="store_true", help="experimental: set launchd PATH for desktop apps")
    s.set_defaults(fn=cmd_wire)

    s = sub.add_parser("uninstall", help="remove all wiring; --purge also deletes ~/.unienv")
    s.add_argument("--purge", action="store_true")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("status", help="one-screen overview (also what plain `unienv` shows)")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("update", help="download the latest UniEnv and re-install with it")
    s.add_argument("--ref", default=None, help="branch or tag to install (default: latest release, or main)")
    s.add_argument("--sha256", default=None, help="expected SHA-256 checksum of the downloaded tarball")
    s.add_argument("--check", action="store_true", help="check whether a newer UniEnv release is available")
    s.add_argument("--no-wire", action="store_true")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("mcp", help="MCP server for desktop apps: serve (default), install, uninstall, config")
    s.add_argument("action", nargs="?", default="serve", choices=["serve", "install", "uninstall", "config"])
    s.add_argument(
        "--client",
        default="claude-desktop",
        choices=[*MCP_CLIENT_KEYS, "all"],
        help="which app to configure (default: claude-desktop)",
    )
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("path", help="print the UniEnv bin directory")
    s.set_defaults(fn=lambda a: print(BIN) or 0)

    s = sub.add_parser("sandbox", help="print or install sandbox allow-rules for Claude Code, Codex, Gemini/Seatbelt")
    s.add_argument("action", nargs="?", default="print", choices=["print", "install"])
    s.add_argument(
        "--tool",
        default="all",
        choices=["claude", "codex", "gemini", "seatbelt", "all"],
        help="which tool profile to print or install (default: all)",
    )
    s.set_defaults(fn=cmd_sandbox)

    s = sub.add_parser("du", help="show disk usage of py, node, backups and total UNIENV_HOME")
    s.set_defaults(fn=cmd_du)

    args = p.parse_args(argv)
    mutating = (
        args.cmd in ("install", "import", "need", "remove", "sync", "uninstall", "wire")
        or (args.cmd == "upgrade" and not args.dry_run)
        or (args.cmd == "doctor" and args.fix)
        or (args.cmd == "sandbox" and args.action == "install")
    )
    try:
        with state_lock() if mutating else contextlib.nullcontext():
            return args.fn(args)
    except subprocess.CalledProcessError as e:
        die(f"command failed ({e.returncode}): {' '.join(map(str, e.cmd))}")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

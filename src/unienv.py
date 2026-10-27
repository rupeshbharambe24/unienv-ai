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
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

VERSION = "0.1.0"
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
EXTRA_DIRS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin",
              str(HOME / ".local/bin"), str(HOME / ".cargo/bin")]

SHELL_BEGIN, SHELL_END = "# >>> unienv >>>", "# <<< unienv <<<"
MD_BEGIN, MD_END = "<!-- >>> unienv (managed block, do not edit) >>> -->", "<!-- <<< unienv <<< -->"
LAUNCH_AGENT = HOME / "Library/LaunchAgents/ai.unienv.path.plist"

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
    app = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if IS_MAC and os.access(app, os.X_OK):
        return app
    return shutil.which("soffice", path=search_path()) or shutil.which("libreoffice", path=search_path())


def resolve_bin(name: str) -> str | None:
    """Like which(), but wrapper shims only count if the tool they wrap exists."""
    if name == "soffice-safe":
        return libreoffice_path() and which(name)
    return which(name)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
        check: bool = True, capture: bool = False, quiet: bool = False,
        timeout: int | None = None) -> subprocess.CompletedProcess:
    if not quiet and not capture:
        info(_c("2", "  $ " + " ".join(cmd)))
    full_env = {**os.environ, "PATH": search_path(), **(env or {})}
    try:
        r = subprocess.run(cmd, cwd=cwd, env=full_env, text=True, timeout=timeout,
                           capture_output=capture)
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
    rest = spec[len(name):]
    return rest[1:] if rest.startswith("@") and len(rest) > 1 else "latest"


def uniq(items) -> list:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def platform_key() -> str:
    if IS_MAC:
        return "macos"
    if Path("/etc/debian_version").exists():
        return "debian"
    return sys.platform


def upsert_block(path: Path, body: str, begin: str, end: str, *, top: bool = False) -> bool:
    """Insert or replace a marked block. Returns True if the file changed."""
    old = path.read_text() if path.exists() else ""
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    if pattern.search(old):
        new = pattern.sub(lambda _: block, old, count=1)
    elif top:
        new = block + ("\n" + old if old else "")
    else:
        new = old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + block
    if new == old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return True


def remove_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    old = path.read_text()
    new = re.sub(r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", old, flags=re.S)
    new = new.strip("\n") + ("\n" if new.strip() else "")
    if new == old:
        return False
    path.write_text(new)
    return True


def read_block(path: Path, begin: str, end: str) -> str | None:
    if not path.exists():
        return None
    m = re.search(re.escape(begin) + r"\n(.*?)\n" + re.escape(end), path.read_text(), re.S)
    return m.group(1) if m else None


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
    for section, key in [("python", "packages"), ("python", "imports"), ("node", "packages"),
                         ("node", "modules"), ("verify", "bins")]:
        v = r.get(section, {}).get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            problems.append(f"[{section}].{key} must be a list of strings")
    return problems


def default_state(recipes: dict) -> dict:
    return {
        "version": VERSION,
        "batteries": [k for k, r in recipes.items() if r.get("default") and not r.get("always")],
        "extras": {"python": [], "node": [], "brew": [], "cask": [], "apt": []},
        "options": {"wire_homebrew": True, "exclude": []},
    }


def load_state(recipes: dict) -> dict:
    state = default_state(recipes)
    if STATE_FILE.exists():
        saved = json.loads(STATE_FILE.read_text())
        state.update({k: v for k, v in saved.items() if k in state and k != "options"})
        state["options"].update(saved.get("options", {}))
        for k in default_state(recipes)["extras"]:
            state["extras"].setdefault(k, [])
    return state


def save_state(state: dict) -> None:
    UNIENV_HOME.mkdir(parents=True, exist_ok=True)
    state["version"] = VERSION
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


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
        return [p for p in uniq(pkgs + state["extras"][kind])
                if not any(p == x or p.startswith(x + "-") for x in excluded)]

    bins, skipped = [], []
    for r in recipes:
        provided_by = r.get("verify", {}).get("provided_by", {})
        for b in r.get("verify", {}).get("bins", []):
            (skipped if provided_by.get(b) in excluded else bins).append(b)
    return {
        "python": uniq([p for r in recipes for p in r.get("python", {}).get("packages", [])] + state["extras"]["python"]),
        "node": uniq([p for r in recipes for p in r.get("node", {}).get("packages", [])] + state["extras"]["node"]),
        "brew": system("brew"),
        "cask": system("cask"),
        "apt": system("apt"),
        "bins": uniq(bins),
        "skipped_bins": uniq(skipped),
    }


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
        env = {"HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_AUTO_UPDATE": "1"}
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
    elif pk == "debian":
        pkgs = [p for p in w["apt"] if run(["dpkg", "-s", p], capture=True, check=False).returncode != 0]
        if pkgs:
            cmd = ["sudo", "apt-get", "install", "-y", *pkgs]
            if assume_yes or os.geteuid() == 0:
                if os.geteuid() == 0:
                    cmd = cmd[1:]
                if run(cmd, check=False).returncode != 0:
                    failures.extend(f"apt:{p}" for p in pkgs)
            else:
                warn("System packages needed. Run this, then `unienv install` again:\n    " + " ".join(cmd))
                failures.extend(f"apt:{p}" for p in pkgs)
    else:
        warn(f"No system package backend for {pk} yet; install these yourself: {', '.join(w['bins'])}")
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
    deps = "".join(f'  "{p}",\n' for p in packages)
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
    run([uv, "sync", "--project", str(PY_DIR), "--managed-python", "--quiet"],
        env={"UV_PROJECT_ENVIRONMENT": str(PY_DIR / ".venv")})


def write_package_json(packages: list[str]) -> None:
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    pj = NODE_DIR / "package.json"
    data = {"name": "unienv-node", "private": True, "description": "Generated by unienv",
            "dependencies": {node_name(p): node_version(p) for p in packages}}
    if pj.exists():  # keep resolved ranges npm already wrote for unchanged packages
        old = json.loads(pj.read_text()).get("dependencies", {})
        for k, v in data["dependencies"].items():
            if v == "latest" and k in old:
                data["dependencies"][k] = old[k]
    pj.write_text(json.dumps(data, indent=2) + "\n")


def install_node(packages: list[str]) -> None:
    if not packages:
        return
    npm = which("npm")
    if not npm:
        warn("npm not found; skipping Node batteries (install Node.js, then `unienv install`).")
        return
    write_package_json(packages)
    run([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=NODE_DIR)


# ── generated files: shims, env.sh, manifest, agent files ───────────────────

def write_shims() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    node = which("node") or "node"
    for tpl in sorted(SHIMS_DIR.iterdir()):
        if tpl.name.startswith("."):
            continue
        text = tpl.read_text().replace("@UNIENV_HOME@", str(UNIENV_HOME)).replace("@NODE@", node)
        dst = BIN / tpl.name
        dst.write_text(text)
        dst.chmod(0o755)
    # the CLI itself: always run with the interpreter that is running us now
    # (a uv-managed 3.12, never Homebrew's python), so it survives env breakage
    cli = BIN / "unienv"
    cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" "$@"\n')
    cli.chmod(0o755)
    ue = BIN / "ue"
    if ue.is_symlink() or ue.exists():
        ue.unlink()
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
        "# `bash -c` (non-login, non-interactive) reads only $BASH_ENV.",
        '[ -z "${BASH_ENV:-}" ] && export BASH_ENV="$UNIENV_HOME/env.sh"',
        "# Agents run non-interactive shells: never block on a pager.",
        'case $- in *i*) ;; *) export GIT_PAGER="${GIT_PAGER:-cat}" PAGER="${PAGER:-cat}" ;; esac',
        "",
    ]
    ENV_SH.write_text("\n".join(lines))


def python_packages() -> dict[str, str]:
    py = PY_DIR / ".venv/bin/python"
    if not py.exists():
        return {}
    r = run([str(py), "-c", "import importlib.metadata as m, json;"
             "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))"],
            capture=True, check=False)
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
        "python": {"command": str(BIN / "ue-py"), "executable": str(PY_DIR / ".venv/bin/python"),
                   "version": PY_VERSION, "packages": top},
        "node": {"command": str(BIN / "ue-node"), "packages": node_packages(w["node"])},
        "bins": {b: resolve_bin(b) for b in w["bins"]},
        "extras": state["extras"],
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _fmt_list(items, empty="(none)") -> str:
    items = [i for i in items if i]
    return ", ".join(f"`{i}`" for i in items) if items else empty


def render_agent_block(manifest: dict) -> str:
    tpl = (TEMPLATES_DIR / "agent-block.md").read_text()
    tools = [b for b, p in manifest["bins"].items() if p]
    py = [f"{k}" for k, v in manifest["python"]["packages"].items() if v]
    node = [k for k, v in manifest["node"]["packages"].items() if v]
    return (tpl.replace("{{BIN}}", str(BIN)).replace("{{HOME_DIR}}", str(UNIENV_HOME))
            .replace("{{TOOLS}}", _fmt_list(tools)).replace("{{PY_PACKAGES}}", _fmt_list(py))
            .replace("{{NODE_PACKAGES}}", _fmt_list(node))).rstrip() + "\n"


def render_skill(manifest: dict, recipes: list[dict]) -> str:
    tpl = (TEMPLATES_DIR / "SKILL.md").read_text()
    notes = []
    for r in recipes:
        if r.get("agent", {}).get("notes"):
            notes.append(f"### {r['id']}: {r['summary']}\n\n{r['agent']['notes'].strip()}\n")
    py = [f"{k} {v}" for k, v in manifest["python"]["packages"].items() if v]
    return (tpl.replace("{{BIN}}", str(BIN)).replace("{{HOME_DIR}}", str(UNIENV_HOME))
            .replace("{{BATTERIES}}", ", ".join(manifest["batteries"]))
            .replace("{{NOTES}}", "\n".join(notes).strip())
            .replace("{{PY_PACKAGES}}", "\n".join(f"- {p}" for p in py) or "(none)"))


def agent_targets() -> list[dict]:
    codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
    return [
        {"id": "claude-code", "name": "Claude Code", "detect": [HOME / ".claude"], "bin": "claude",
         "instructions": HOME / ".claude/CLAUDE.md", "skills": HOME / ".claude/skills"},
        {"id": "codex", "name": "Codex CLI", "detect": [codex_home], "bin": "codex",
         "instructions": codex_home / "AGENTS.md", "skills": codex_home / "skills"},
        {"id": "gemini", "name": "Gemini CLI", "detect": [HOME / ".gemini"], "bin": "gemini",
         "instructions": HOME / ".gemini/GEMINI.md", "skills": None},
        {"id": "opencode", "name": "OpenCode", "detect": [HOME / ".config/opencode"], "bin": "opencode",
         "instructions": HOME / ".config/opencode/AGENTS.md", "skills": None},
    ]


def detected(agent: dict) -> bool:
    return any(p.exists() for p in agent["detect"]) or bool(which(agent["bin"]))


def sync_agents(manifest: dict, recipes: list[dict], *, only_detected: bool = True) -> list[str]:
    block = render_agent_block(manifest)
    skill = render_skill(manifest, recipes)
    AGENTS_COPY.write_text(block)  # canonical copy, e.g. to paste into Cursor's User Rules
    done = []
    for a in agent_targets():
        if only_detected and not detected(a):
            continue
        upsert_block(a["instructions"], block, MD_BEGIN, MD_END)
        if a["skills"] is not None:
            d = a["skills"] / "unienv"
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(skill)
        done.append(a["name"])
    return done


def wire_shells() -> list[str]:
    body = f'[ -f "{ENV_SH}" ] && . "{ENV_SH}"'
    changed = []
    for rc in (HOME / ".zshenv", HOME / ".profile", HOME / ".bashrc"):
        if rc.name == ".bashrc" and not rc.exists():
            continue
        if upsert_block(rc, body, SHELL_BEGIN, SHELL_END):
            changed.append(str(rc).replace(str(HOME), "~"))
    return changed


def do_sync(state: dict, recipes_all: dict, *, quiet: bool = False, wire: bool = True,
            all_agents: bool = False) -> dict:
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)
    write_shims()
    write_env_sh(state)
    manifest = write_manifest(recipes, state, w)
    agents = sync_agents(manifest, recipes, only_detected=not all_agents)
    wired = wire_shells() if wire else []
    if not quiet:
        info(f"  shims: {', '.join(sorted(p.name for p in BIN.iterdir()))}")
        info(f"  agents configured: {', '.join(agents) or '(none detected)'}")
        if wired:
            info(f"  shell files updated: {', '.join(wired)}")
    return manifest


# ── commands ────────────────────────────────────────────────────────────────

def cmd_install(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    if args.batteries:
        req = [b.strip() for b in args.batteries.split(",") if b.strip()]
        bad = [b for b in req if b not in recipes_all]
        if bad:
            die(f"unknown batteries: {', '.join(bad)}. Available: {', '.join(recipes_all)}")
        state["batteries"] = [b for b in req if not recipes_all[b].get("always")]
    if args.exclude:
        state["options"]["exclude"] = uniq(state["options"]["exclude"]
                                           + [x.strip() for x in args.exclude.split(",") if x.strip()])
    if args.add:
        state["batteries"] = uniq(state["batteries"] + [b.strip() for b in args.add.split(",")])
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
    step(f"Node packages ({len(w['node'])})")
    install_node(w["node"])
    step("Wiring shells and AI tools")
    do_sync(state, recipes_all, wire=not args.no_wire, all_agents=args.all_agents)
    log_event(event="install", batteries=[r["id"] for r in recipes], failures=failures)
    if failures:
        warn("Some system packages failed: " + ", ".join(failures))
    step("Checking")
    rc = cmd_doctor(argparse.Namespace(json=False, fix=False, quiet_ok=True))
    info("\nOpen a new terminal (or restart your AI tools) so they pick up the new PATH.")
    return 1 if failures else rc


def cmd_sync(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    step("Regenerating shims, env.sh, manifest and agent files")
    do_sync(state, recipes_all, wire=not args.no_wire, all_agents=args.all_agents)
    return 0


def cmd_need(args) -> int:
    recipes_all = load_recipes()
    state = load_state(recipes_all)
    kind = "node" if args.node else "brew" if args.bin else "cask" if args.cask else "python"
    if kind in ("brew", "cask") and platform_key() == "debian":
        kind = "apt"
    pkgs = args.packages
    recipes = active_recipes(recipes_all, state)
    w = wanted(recipes, state)

    key = {"python": py_name, "node": node_name}.get(kind, lambda s: s)
    present = {key(p) for p in w[kind]}
    new = [p for p in pkgs if key(p) not in present]
    if not new:
        info(f"✓ already available: {', '.join(pkgs)}")
        return 0
    curated = {key(p) for r in recipes_all.values()
               for p in (r.get(kind, {}).get("packages", []) if kind in ("python", "node")
                         else [x for s in r.get("system", {}).values() for x in s.get(kind, [])])}
    uncurated = [p for p in new if key(p) not in curated]
    if uncurated:
        info(f"  note: not in a curated recipe, installing from the public registry: {', '.join(uncurated)}")

    # snapshot so a failed install can be rolled back exactly
    snap = BACKUP_DIR / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap.mkdir(parents=True, exist_ok=True)
    for f in [STATE_FILE, PY_DIR / "pyproject.toml", PY_DIR / "uv.lock",
              NODE_DIR / "package.json", NODE_DIR / "package-lock.json"]:
        if f.exists():
            shutil.copy2(f, snap / f"{f.parent.name}__{f.name}")

    state["options"]["exclude"] = [x for x in state["options"]["exclude"] if x not in pkgs]
    state["extras"][kind] = uniq(state["extras"][kind] + new)
    w = wanted(recipes, state)
    try:
        if kind == "python":
            install_python(w["python"])
        elif kind == "node":
            install_node(w["node"])
        else:
            failed = install_system({**w, kind: new, "brew": new if kind == "brew" else [],
                                     "cask": new if kind == "cask" else [], "apt": new if kind == "apt" else []},
                                    assume_yes=args.yes)
            if failed:
                raise RuntimeError(", ".join(failed))
    except (subprocess.CalledProcessError, RuntimeError, SystemExit) as e:
        warn(f"install failed ({e}); rolling back")
        for f in snap.iterdir():
            parent, name = f.name.split("__", 1)
            dest = {"py": PY_DIR, "node": NODE_DIR}.get(parent, UNIENV_HOME) / name
            shutil.copy2(f, dest)
        state = load_state(recipes_all)
        if kind == "python":
            install_python(wanted(recipes, state)["python"])
        log_event(event="need-failed", kind=kind, packages=new)
        return 1
    save_state(state)
    do_sync(state, recipes_all, quiet=True)
    log_event(event="need", kind=kind, packages=new, curated=not uncurated)
    via = {"python": "ue-py", "node": "ue-node"}.get(kind, "PATH")
    info(f"✓ installed {', '.join(new)}; available to every AI tool via {via}")
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


# ── doctor ──────────────────────────────────────────────────────────────────

def _clean_shell_probe(argv: list[str]) -> str | None:
    """Run `command -v ue-py` in a shell started from a near-empty environment, like an agent or GUI app."""
    env = {"HOME": str(HOME), "USER": os.environ.get("USER", ""), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "TERM": "dumb"}
    if "UNIENV_HOME" in os.environ:
        env["UNIENV_HOME"] = os.environ["UNIENV_HOME"]
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
    py = PY_DIR / ".venv/bin/python"
    imports = uniq([m for r in recipes for m in r.get("python", {}).get("imports", [])])
    if not py.exists():
        add("python", "fail", "shared Python env missing", "unienv install")
    else:
        code = ("import importlib, json, sys\nbad = {}\n"
                f"for m in {imports!r}:\n"
                "    try: importlib.import_module(m)\n"
                "    except Exception as e: bad[m] = f'{type(e).__name__}: {e}'[:120]\n"
                "print(json.dumps({'v': sys.version.split()[0], 'bad': bad}))")
        r = run([str(py), "-c", code], capture=True, check=False, timeout=120)
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
            add(f"node:{name}", "ok", f"{name} {ver}")
        else:
            add(f"node:{name}", "fail", f"Node package {name} missing", "unienv install")
    if w["node"] and not which("node"):
        add("node", "fail", "node not found", "unienv need --bin node")

    # System binaries
    for b in w["bins"]:
        p = resolve_bin(b)
        if p:
            add(f"bin:{b}", "ok", p)
        else:
            owner = next((r["id"] for r in recipes if b in r.get("verify", {}).get("bins", [])), "?")
            add(f"bin:{b}", "fail", f"{b} not found (battery: {owner})", "unienv install")

    for b in w["skipped_bins"]:
        add(f"bin:{b}", "info", f"{b} skipped by your choice (exclude: {', '.join(state['options']['exclude'])})",
            "to add it later: unienv need --cask/--bin <package>")

    # Shell wiring: what agents and GUI apps actually see
    target = str(BIN / "ue-py")
    probes = [("zsh -c", ["/bin/zsh", "-c", "command -v ue-py"]),
              ("zsh -lc", ["/bin/zsh", "-lc", "command -v ue-py"]),
              ("bash -lc", ["/bin/bash", "-lc", "command -v ue-py"])]
    for label, argv in probes:
        if not Path(argv[0]).exists():
            continue
        got = _clean_shell_probe(argv)
        if got == target:
            add(f"shell:{label}", "ok", f"`{label}` finds ue-py")
        else:
            add(f"shell:{label}", "fail", f"`{label}` (fresh environment) cannot find ue-py",
                "unienv sync   # rewrites ~/.zshenv, ~/.profile, ~/.bashrc blocks")

    if IS_MAC:
        r = run(["launchctl", "getenv", "PATH"], capture=True, check=False)
        if str(BIN) in (r.stdout or ""):
            add("gui-path", "ok", "launchd PATH includes UniEnv (GUI apps see it)")
        else:
            add("gui-path", "info", "GUI apps (Claude Desktop, Dock-launched Cursor) use the system PATH; "
                "use absolute paths from manifest.json in their configs",
                "optional, experimental: unienv wire --gui")

    # Agent instruction files are present and current
    if MANIFEST_FILE.exists():
        manifest = json.loads(MANIFEST_FILE.read_text())
        expected = render_agent_block(manifest).rstrip()
        for a in agent_targets():
            if not detected(a):
                continue
            cur = read_block(a["instructions"], MD_BEGIN, MD_END)
            short = str(a["instructions"]).replace(str(HOME), "~")
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
        r = run([f"{bp}/bin/python3", "-c", "import sysconfig,os;"
                 "print(os.path.exists(os.path.join(sysconfig.get_path('stdlib'),'EXTERNALLY-MANAGED')))"],
                capture=True, check=False)
        if r.stdout.strip() == "True":
            add("pep668", "info", f"{bp}/bin/python3 is externally managed (PEP 668); "
                "agents are told to use ue-py / `unienv need` instead of pip")

    # Secrets hygiene: report variable names only, never values
    pat = re.compile(r"^\s*export\s+([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=", re.M)
    for rc in (".zshrc", ".zshenv", ".zprofile", ".bashrc", ".bash_profile", ".profile"):
        p = HOME / rc
        if p.exists():
            names = pat.findall(p.read_text(errors="ignore"))
            if names:
                add(f"secrets:{rc}", "warn", f"~/{rc} exports secrets to every process an agent runs: "
                    f"{', '.join(sorted(set(names)))}", "load them only where needed (e.g. a keychain lookup)")
    cdc = HOME / "Library/Application Support/Claude/claude_desktop_config.json"
    if cdc.exists():
        try:
            servers = json.loads(cdc.read_text()).get("mcpServers", {}) or {}
            leaky = [f"{s}.{k}" for s, cfg in servers.items() for k, v in (cfg.get("env") or {}).items()
                     if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and v and not str(v).startswith("$")]
            if leaky:
                add("secrets:claude-desktop", "warn", f"plaintext secrets in claude_desktop_config.json: {', '.join(leaky)}")
        except (json.JSONDecodeError, AttributeError):
            pass

    if getattr(args, "fix", False) and any(c["status"] in ("fail", "warn") for c in checks):
        info("Applying safe fixes: unienv sync")
        do_sync(state, recipes_all, quiet=True)
        return cmd_doctor(argparse.Namespace(json=args.json, fix=False, quiet_ok=False))
    return _report(checks, args)


def _report(checks: list[dict], args) -> int:
    failed = sum(c["status"] == "fail" for c in checks)
    warned = sum(c["status"] == "warn" for c in checks)
    if getattr(args, "json", False):
        print(json.dumps({"unienv": VERSION, "ok": failed == 0, "failed": failed, "warnings": warned,
                          "checks": checks}, indent=2))
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
        path = os.pathsep.join([str(BIN)] + ([f"{brew_prefix()}/bin"] if brew_prefix() else [])
                               + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
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
    changed = wire_shells()
    info("✓ shell files updated: " + (", ".join(changed) if changed else "already wired"))
    return 0


def cmd_uninstall(args) -> int:
    for rc in (HOME / ".zshenv", HOME / ".profile", HOME / ".bashrc"):
        if remove_block(rc, SHELL_BEGIN, SHELL_END):
            info(f"  removed block from {rc}")
    for a in agent_targets():
        if remove_block(a["instructions"], MD_BEGIN, MD_END):
            info(f"  removed block from {a['instructions']}")
        if a["skills"] is not None and (a["skills"] / "unienv").exists():
            shutil.rmtree(a["skills"] / "unienv")
            info(f"  removed {a['skills'] / 'unienv'}")
    if LAUNCH_AGENT.exists():
        LAUNCH_AGENT.unlink()
        run(["launchctl", "unsetenv", "PATH"], check=False, quiet=True)
        info(f"  removed {LAUNCH_AGENT}")
    if args.purge and UNIENV_HOME.exists():
        shutil.rmtree(UNIENV_HOME)
        info(f"  deleted {UNIENV_HOME}")
    info("✓ UniEnv unwired. System packages installed with Homebrew/apt were left in place.")
    return 0


# ── entry point ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unienv", description="UniEnv AI: one always-ready environment for every AI tool.")
    p.add_argument("--version", action="version", version=f"unienv {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("install", help="install batteries and wire every shell and AI tool")
    s.add_argument("--batteries", help="comma-separated list replacing the current selection")
    s.add_argument("--add", help="comma-separated batteries to add (e.g. media,fonts)")
    s.add_argument("--exclude", help="system packages to skip and remember (e.g. libreoffice)")
    s.add_argument("--no-system", action="store_true", help="skip Homebrew/apt packages")
    s.add_argument("--no-wire", action="store_true", help="don't edit shell startup files")
    s.add_argument("--all-agents", action="store_true", help="write instruction files even for agents not detected")
    s.add_argument("-y", "--yes", action="store_true", help="allow sudo apt-get on Linux")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("need", help="permanently add a missing package for every AI tool")
    s.add_argument("packages", nargs="+")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--node", action="store_true", help="npm package")
    g.add_argument("--bin", action="store_true", help="system tool (Homebrew formula / apt package)")
    g.add_argument("--cask", action="store_true", help="macOS app (Homebrew cask)")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(fn=cmd_need)

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

    s = sub.add_parser("wire", help="(re)write shell startup blocks; --gui sets the macOS GUI PATH")
    s.add_argument("--gui", action="store_true", help="experimental: set launchd PATH for desktop apps")
    s.set_defaults(fn=cmd_wire)

    s = sub.add_parser("uninstall", help="remove all wiring; --purge also deletes ~/.unienv")
    s.add_argument("--purge", action="store_true")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("path", help="print the UniEnv bin directory")
    s.set_defaults(fn=lambda a: print(BIN) or 0)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except subprocess.CalledProcessError as e:
        die(f"command failed ({e.returncode}): {' '.join(map(str, e.cmd))}")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

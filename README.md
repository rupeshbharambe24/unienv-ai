# UniEnv AI

**One always-ready environment for every AI tool on your machine.**

Ask Claude Code, Codex, Gemini CLI or any other AI agent to "fix this Word file" or "summarise this spreadsheet", and it usually discovers `python-docx` or `openpyxl` is missing, makes a temporary virtualenv, installs everything, does the job, and throws it all away. Next time it does it again. UniEnv AI installs those tools **once**, in one place, and tells **every** AI tool where they are.

```console
$ ./install.sh
==> System tools          pandoc, LibreOffice, poppler, qpdf, tesseract, duckdb …
==> Python 3.12 env       python-docx, openpyxl, python-pptx, pandas, pypdf, markitdown …
==> Node packages         docx, pptxgenjs
==> Wiring shells and AI tools
  agents configured: Claude Code, Codex CLI, Gemini CLI
==> Checking
  ✓ 27 checks passed
unienv doctor: all good

$ ue need seaborn          # an agent needs something new: installed once, for every tool (~2 s)
✓ installed seaborn; available to every AI tool via ue-py
```

## Why this exists

AI agents are not bad at environments; they are **blind** to them ([research](docs/research.md)):

- **Every command runs in a fresh, non-interactive shell.** `source .venv/bin/activate`, conda, nvm and even Homebrew's PATH (set in `~/.zprofile`) are gone by the next command. Desktop apps get an even smaller PATH, hence the classic MCP `spawn npx ENOENT`.
- **Models expect a cloud sandbox's tools.** Office/PDF skills assume LibreOffice, pandoc, Poppler and 300+ Python libraries are "preinstalled". A normal laptop has almost none, and Homebrew's Python refuses `pip install` (PEP 668), so agents build throwaway venvs.
- **Nothing remembers.** The next session, and every other agent, starts from zero.

The cost is measurable. In SetupBench, even the best model wasted **69%** of its environment-setup steps and used **1.1M tokens per task**. JetBrains found that just telling agents where the right Python is lifted task success from **68% to 98%**.

## What UniEnv does

| Problem | UniEnv |
|---|---|
| Libraries missing, temp venvs every task | One shared, pinned **Python 3.12** env (`ue-py`) and Node package set (`ue-node`) with document, PDF, OCR and data batteries |
| System tools missing (pandoc, LibreOffice, Poppler, tesseract) | Installs them with your OS package manager (Homebrew today, apt next) |
| Agents' shells can't see tools | Wires `~/.zshenv`, `~/.profile`, `~/.bashrc` and `BASH_ENV`, so **`zsh -c`, `bash -lc` and login shells** all see the same PATH |
| Agents don't know what's installed | Writes a managed block into each agent's global instructions (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, OpenCode) and a **`unienv` skill** for Claude Code and Codex |
| "Install it again" loops | `unienv need <pkg>` installs **permanently** for every tool, with rollback if it breaks |
| "Is it broken or just missing?" | `unienv doctor [--json]` checks every tool, every shell mode, every agent file and secrets hygiene |
| LibreOffice hangs, locks or silently does nothing | `soffice-safe` gives each call a throwaway profile, so it is safe in parallel and in sandboxes |
| Project environments clobbered | UniEnv **never** puts its own `python`/`python3`/`node` on PATH. It uses namespaced `ue-*` commands, and agents are told to keep using a project's own env inside projects. |

It is **native-first**: no Docker or VM is required, and it runs on the host where your agents already run.

## Quick start

Requirements: macOS with [Homebrew](https://brew.sh) (Linux works for Python/Node batteries; apt support is in progress). [uv](https://docs.astral.sh/uv/) is installed automatically if missing.

```sh
git clone https://github.com/rupeshbharambe24/unienv-ai.git
cd unienv-ai
./install.sh              # default batteries: core, office, pdf, data, ocr
```

Then open a new terminal or restart your AI tools. Check everything with:

```sh
unienv doctor
```

Options:

```sh
./install.sh --batteries office,pdf      # choose batteries
unienv install --add media,fonts         # opt-in batteries later
./install.sh --no-system                 # skip Homebrew packages
./install.sh --dev                       # contributors: run straight from this checkout
```

## Commands

| Command | What it does |
|---|---|
| `unienv install` | Install batteries, wire shells and AI tools, then run `doctor` |
| `unienv need <pkg>` | Add a Python package permanently (`--node`, `--bin` for Homebrew formulae, `--cask` for apps) |
| `unienv doctor [--json] [--fix]` | Verify tools, shell wiring, agent files and secrets hygiene; `--json` is for agents and CI |
| `unienv list [--json]` | Show batteries, packages and tool paths (`--json` prints `manifest.json`) |
| `unienv sync` | Regenerate shims, `env.sh`, `manifest.json` and agent instruction blocks |
| `unienv wire [--gui]` | Re-write the shell blocks; `--gui` (experimental, macOS) sets the launchd PATH for desktop apps |
| `unienv uninstall [--purge]` | Remove every block UniEnv wrote; `--purge` also deletes `~/.unienv` |

`ue` is a short alias for `unienv`.

## Batteries

| Battery | Default | Gives agents |
|---|---|---|
| `core` | always | uv, Node.js, ripgrep, jq |
| `office` | ✓ | python-docx, openpyxl, xlsxwriter, python-pptx, markitdown, docx-js, pptxgenjs, pandoc, LibreOffice (`soffice-safe`) |
| `pdf` | ✓ | pypdf, pdfplumber, pypdfium2, reportlab, pdf2image, Poppler (`pdftotext`, `pdftoppm`), qpdf, Ghostscript |
| `data` | ✓ | pandas, numpy, pyarrow, duckdb, matplotlib, requests, httpx, beautifulsoup4, pyyaml |
| `ocr` | ✓ | tesseract + pytesseract (English) |
| `media` | opt-in | ffmpeg, ImageMagick |
| `fonts` | opt-in | Carlito, Caladea, Liberation and Noto CJK, so LibreOffice renders match Word |

Each battery is one small TOML file in [`recipes/`](recipes). Adding one is the easiest way to contribute; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Supported AI tools

| Tool | How UniEnv reaches it | Status |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` block + `~/.claude/skills/unienv/` + shell wiring | ✅ |
| Codex CLI | `~/.codex/AGENTS.md` block + `~/.codex/skills/unienv/` + shell wiring | ✅ |
| Gemini CLI | `~/.gemini/GEMINI.md` block + shell wiring | ✅ |
| OpenCode | `~/.config/opencode/AGENTS.md` block + shell wiring | ✅ |
| Cursor, Windsurf, Cline, Roo, Copilot | Shell wiring works today; paste `~/.unienv/AGENTS.md` into the tool's user rules | ◐ adapters planned |
| Claude Desktop, ChatGPT desktop, LM Studio | Use absolute paths from `~/.unienv/manifest.json` in MCP configs | ◐ `unienv-mcp` planned |

Instruction blocks are only written for tools UniEnv detects (`--all-agents` writes them all). Everything UniEnv writes sits between `>>> unienv >>>` markers and is removed by `unienv uninstall`.

## How it works

```
recipes/*.toml ──► unienv install ──► ~/.unienv/
                                        bin/       ue-py, ue-node, soffice-safe, unienv, ue
                                        py/        uv project (Python 3.12) + uv.lock + .venv
                                        node/      npm prefix (docx, pptxgenjs, …)
                                        env.sh     PATH for every shell mode
                                        manifest.json   what is installed, with absolute paths
                                        AGENTS.md  the instruction block, for tools without a global file
              wired into ──► ~/.zshenv · ~/.profile · ~/.bashrc · BASH_ENV
              described to ─► CLAUDE.md · AGENTS.md · GEMINI.md · skills/unienv/SKILL.md
```

- The CLI runs on a **uv-managed Python 3.12**, never Homebrew's or the OS Python, and uses only the standard library, so it keeps working even if the environment it manages breaks.
- `unienv need` snapshots `pyproject.toml`, `uv.lock` and `package.json` first and **rolls back** if the install fails.
- Every `need` is logged to `~/.unienv/log/unienv.log`.

## Security notes

- `unienv need` installs from public registries (PyPI, npm, Homebrew). Your AI tool's own permission prompt is the gate for that command. Packages outside a curated recipe are flagged in the output and the log.
- `doctor` warns about API keys exported from shell startup files (every process an agent runs inherits them) and plaintext secrets in `claude_desktop_config.json`. It reports **variable names only, never values**.
- UniEnv never writes credentials into any file it generates. No telemetry.

## Roadmap

- **0.1 (this prototype):** macOS, batteries, shell + agent wiring, `need`, `doctor`, smoke test
- **0.2:** apt/dnf backends, Cursor, Cline, Roo and Copilot adapters, recipe JSON Schema in CI, Homebrew tap
- **0.3:** `unienv-mcp` (one small MCP server for desktop apps), generated sandbox profiles (Claude Code `/sandbox`, Codex, Gemini), shared Playwright browser battery
- **1.0:** Windows (winget/Scoop) and WSL, signed recipe index, container/devcontainer export from the same lockfile, an open benchmark of local-machine environment failures

The full research behind these choices is in [docs/research.md](docs/research.md).

## Contributing

New batteries, agent adapters, Linux/Windows backends and bug reports with `unienv doctor --json` output are all welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option. Tools installed by recipes keep their own licenses (each recipe's `license_note`).

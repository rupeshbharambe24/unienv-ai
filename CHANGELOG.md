# Changelog

All notable changes to UniEnv AI are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security
- MCP safe mode: `UNIENV_MCP_MODE=readonly` blocks writes, `UNIENV_MCP_ALLOW_INSTALL` controls package installs, and `UNIENV_MCP_ROOTS` limits file access to the folders you list (arbitrary code execution is disabled under roots). Output paths that are dangling symlinks are refused, Pandoc conversions are sandboxed in safe mode, and the Desktop extension exposes these settings. By @KunjMaheshwari in #132. (#33)
- `unienv update` on Python 3.11.0–3.11.3, which lacks tar extraction filters, now validates the download itself: only regular files and folders are extracted (links and devices are rejected), and permissions are normalized like Python's `data` filter. By @KunjMaheshwari in #102. (#66)

### Added
- MCP server: clients can cancel running and queued tool calls, pings are answered during long calls, and long calls send a progress heartbeat every 10 s. Package installs can't be cancelled, so their rollback always runs. By @KunjMaheshwari in #131. (#32)
- MCP image content for document reads, PDF renders and generated charts, with signature validation, a consistent 512 KB image limit, and a shared changed-file scan. By @KunjMaheshwari in #128. (#31)
- `unienv mcp install` also configures VS Code, Zed, Windsurf and LM Studio. If Zed's `settings.json` has comments, it prints the snippet to paste instead of editing the file. By @KunjMaheshwari in #121. (#30)
- Cline and Roo Code adapters: `unienv sync` writes the UniEnv rules to `~/.cline/rules/unienv.md` and `~/.roo/rules/unienv.md`. By @KunjMaheshwari in #107. (#6)
- `unienv upgrade [packages] [--node] [--dry-run]` upgrades installed Python and Node packages, checks that every battery module still imports, and rolls back automatically if anything fails. By @KunjMaheshwari in #112. (#27)
- `unienv du` shows how much space the Python and Node environments, backups and `~/.unienv` as a whole take. Old backups are pruned automatically (the 10 most recent and anything from the last 7 days are kept). By @KunjMaheshwari in #123. (#38)
- Opt-in `ml` battery: scikit-learn, SciPy, LightGBM, imbalanced-learn and XGBoost (the CPU-only `xgboost-cpu` build on Linux, so no CUDA libraries are pulled in). By @KunjMaheshwari in #109. (#35)
- Opt-in `speech` battery: local audio and video transcription with faster-whisper (CTranslate2, no PyTorch) and ffmpeg, with the model cache shared under `~/.unienv/cache/hf`. About 470 MB on Linux, plus the model on first use. By @KunjMaheshwari in #114. (#36)
- Opt-in `latex` battery: Tectonic via Homebrew on macOS; on Linux, the battery notes point to Tectonic's upstream installer. By @KunjMaheshwari in #93. (#1)
- Windsurf adapter: `unienv sync` writes the UniEnv block into Windsurf's global rules (`~/.codeium/windsurf/memories/global_rules.md`). By @KunjMaheshwari in #101. (#5)
- Benchmark suite: several agents (`claude`, `codex`, `gemini`), six tasks with generated inputs, per-agent token and tool-call parsing, and median/IQR summaries. By @KunjMaheshwari in #126. (#46)
- CI tests the piped `curl … | bash` install and `unienv update`, and checks that uninstall restores dotfiles byte for byte. `install.sh` downloads tags and commit SHAs as well as branches. By @KunjMaheshwari in #116. (#44)
- CI: unit tests for the MCP helpers, a check that a release tag matches the version in all three files, and a weekly job that runs the LibreOffice conversions. By @KunjMaheshwari in #106. (#74)

### Fixed
- A failed `unienv need` now rolls back completely: new files are deleted, `node_modules` is restored, and the reinstall is guarded. By @KunjMaheshwari in #96. (#20)
- `unienv need --bin`/`--cask` fails and rolls back on a platform with no system package backend, instead of reporting success. By @KunjMaheshwari in #91. (#18)
- UniEnv refuses to edit a file whose UniEnv block markers are orphaned or duplicated, instead of guessing, and `unienv doctor` reports such files. By @KunjMaheshwari in #92. (#56)
- `--no-wire` is remembered in state, so `unienv update` no longer rewires your shell files afterwards; `unienv wire` now takes the state lock. By @KunjMaheshwari in #127. (#58)
- When the shared Python is missing, `unienv` and `ue-py` fall back through uv or print how to repair it, instead of failing with a confusing error. By @KunjMaheshwari in #125. (#24)
- MCP tools report files created or changed in subfolders up to 3 levels deep (capped at 2,000 files and 500 folders). By @KunjMaheshwari in #111. (#25)
- The first upgrade removes stale generated shims while leaving your own files alone. By @KunjMaheshwari in #123. (#38)
- Uninstall no longer joins two lines when something was appended after the UniEnv block in a file that had no final newline. By @KunjMaheshwari in #116. (#44)
- Wiring a file keeps its own line endings (CRLF, LF or CR) instead of converting the whole file. By @bfs2021 in #90. (#57)
- `unienv update --ref` accepts tags and commit SHAs as well as branches, and a failed download ends with a clear message instead of a traceback. By @KunjMaheshwari in #94. (#63)
- `unienv need --node` fails and rolls back when npm is missing, instead of reporting success. By @KunjMaheshwari in #95. (#59)
- `curl … | bash` always downloads UniEnv, even when run from inside a checkout, and finds uv in its install folders right after installing it. By @KunjMaheshwari in #100. (#71)
- When a tool call times out, the MCP server stops the whole process group, so no LibreOffice or other child processes are left running. By @KunjMaheshwari in #131. (#16)
- Converting a multi-sheet spreadsheet to CSV exports every sheet, with values as displayed (`12.5%`, not `0.125`), and every output file gets a free name, so existing files are never overwritten. By @KunjMaheshwari in #133. (#69)

## [0.2.2] - 2026-11-18

Nine community fixes and one security fix. Thank you @KunjMaheshwari, @bfs2021 and @sanjanamg0!

### Security
- The MCP server's `read_document` and `run_python`, and `unienv doctor`, start Python with `-P` (Homebrew's Python with `-I`), so a stray `.py` file in the document's folder or the current folder can't shadow a real module and run as you. Before, reading `~/Downloads/report.docx` with a `zipfile.py` next to it ran that file. Update with `unienv update`.

### Fixed
- MCP server no longer crashes on malformed JSON-RPC requests or lone-surrogate strings; bad requests get proper JSON-RPC errors. By @KunjMaheshwari in #79. (#67)
- MCP tools pass proxy and CA certificate variables through, and `install_package` also gets package-index settings (`UV_*`, `PIP_*`, safe `HOMEBREW_*`), so installs work behind corporate proxies. By @KunjMaheshwari in #83. (#68)
- MCP `install_package` accepts `*` and `^` version specs (`pandas==2.*`, `node-gyp@^9`). By @KunjMaheshwari in #76. (#73)
- Non-UTF-8 and read-only dotfiles no longer crash `sync`, `install` or `uninstall`: unknown bytes are kept as-is, and unwritable files get a warning. By @KunjMaheshwari in #81. (#61)
- `unienv doctor` warns and keeps going when an MCP client config has invalid JSON. By @KunjMaheshwari in #82. (#60)
- `unienv need` with double-quoted PEP 508 markers writes valid `pyproject.toml`. By @KunjMaheshwari in #80. (#62)
- Agent instructions and the skill call `unienv` by its absolute path. By @KunjMaheshwari in #77. (#72)
- MCP server: an empty or relative `UNIENV_WORKDIR` resolves against your home folder instead of `/`. By @KunjMaheshwari in #78. (#70)
- Linux: packages removed with `apt remove` (but not purged) are reinstalled instead of counted as installed. By @bfs2021 in #85. (#65)

### Added
- Lint job in CI: `ruff check`, `ruff format --check` and shellcheck on every PR. By @sanjanamg0 in #53. (#43)
- JSON Schema for recipes (`schema/recipe.schema.json`), validated in CI, with editor autocomplete via a `#:schema` line. By @sanjanamg0 in #54. (#11)

### Docs
- README, CONTRIBUTING, ROADMAP and docs brought up to date: how to run every CI check locally, the locking behaviour, new troubleshooting entries, a contributors list and a star request. (#75, #84)

## [0.2.1] - 2026-11-17

First release with community contributions. Thank you @bfs2021, @SantanDon and @CyberKun!

### Security
- MCP `install_package` rejects package names that smuggle in CLI flags (`"pandas -y"`, `"x --cask y"`) and passes names after `--`. (#14)

### Fixed
- `unienv uninstall` removes the `unienv` server from Claude Desktop and Cursor MCP configs (other servers untouched, backup kept). (#13)
- Commands that change `~/.unienv` take a lock, so parallel `unienv need` calls are safe; state, manifest, `env.sh` and `AGENTS.md` are written atomically; a corrupt `state.json` recovers from the newest backup. (#21)
- MCP PDF → PNG renders into a fresh `<name>-pages` folder and lists only that call's pages. (#15)
- `unienv doctor --json --fix` prints a single JSON document, and `--fix` only syncs when sync can repair something. By @CyberKun. (#17, #49)
- `unienv install --add` rejects unknown batteries with a "did you mean" hint. By @bfs2021. (#19, #47)

### Added
- Unit test suite in `tests/unit/`, run in CI. By @SantanDon. (#42, #48)
- CI checks for parallel `need`, `doctor --json --fix`, MCP flag injection and repeated PDF renders.

## [0.2.0] - 2026-10-28

### Added
- **UniEnv MCP server** (`unienv mcp`): Claude Desktop chat and other MCP apps can now read, create and convert documents and run Python with the shared environment. Six tools with safety annotations; standard library only; stdio; MCP 2024-11-05 → 2025-11-25.
- `unienv mcp install|uninstall|config` for Claude Desktop and Cursor (merges with a backup, absolute paths).
- **Claude Desktop Extension** (`unienv.mcpb`), built and validated in CI and attached to releases.
- `doctor` performs a real MCP handshake against configured apps.
- `tests/mcp_test.py`: end-to-end MCP test in CI.
- `bench/word-task.sh`: reproducible before/after benchmark with Claude Code. First results: ~2.3× faster on a Word task, 2 turns instead of 4–5, zero installs during the task.
- `doctor` warns about agent-hostile shell aliases (`ls`→eza, `cat`→bat, `rm -i`…) that hang or prompt inside agent shells.

## [0.1.0] - 2026-10-28

First public prototype.

### Added
- One shared, persistent environment in `~/.unienv`: pinned **Python 3.12** (`ue-py`) via uv, and Node packages (`ue-node`).
- Batteries as declarative TOML recipes: `core`, `office`, `pdf`, `data`, `ocr` (default), and `media`, `fonts` (opt-in).
- System tools through Homebrew on macOS (pandoc, LibreOffice, Poppler, qpdf, Ghostscript, tesseract, duckdb, ripgrep, jq).
- Shell wiring for every shell mode agents use: `~/.zshenv`, `~/.profile`, `~/.bashrc`, `~/.bash_profile`/`~/.bash_login` when present, and `BASH_ENV`.
- Agent wiring: managed instruction blocks for Claude Code, Codex CLI, Gemini CLI and OpenCode, plus a `unienv` skill for Claude Code and Codex.
- `unienv need` to add a Python, Node, Homebrew formula or cask permanently, with snapshot and rollback on failure.
- `unienv doctor [--json] [--fix]`: tools, imports, shell probes from a clean environment, agent files, PEP 668, and secrets hygiene (names only).
- `unienv` status dashboard, `list`, `sync`, `wire [--gui]`, `update`, `uninstall [--purge]`, and an interactive battery picker on first install.
- `soffice-safe`: LibreOffice with a throwaway profile per call (parallel- and sandbox-safe).
- One-line installer: `curl -fsSL .../install.sh | bash`.
- `--exclude` to skip and remember system packages (e.g. `libreoffice`).
- CI on macOS and Ubuntu: lint, install, doctor, smoke test from a clean shell, `need` and rollback.

### Fixed (found while testing on real machines)
- OCR failed in agent shells on macOS: Leptonica rewrites `/tmp` paths, so `env.sh` normalises `TMPDIR` to `/private/tmp`.
- Login bash on Linux skipped `~/.profile` when `~/.bash_profile` exists; both are now wired.
- Homebrew's automatic `brew cleanup` no longer runs as a side effect of UniEnv installs.

[Unreleased]: https://github.com/rupeshbharambe24/unienv-ai/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.2.2
[0.2.1]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.2.1
[0.2.0]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.2.0
[0.1.0]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.1.0

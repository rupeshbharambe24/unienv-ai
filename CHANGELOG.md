# Changelog

All notable changes to UniEnv AI are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/rupeshbharambe24/unienv-ai/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.2.0
[0.1.0]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.1.0

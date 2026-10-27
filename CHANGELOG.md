# Changelog

All notable changes to UniEnv AI are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/rupeshbharambe24/unienv-ai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rupeshbharambe24/unienv-ai/releases/tag/v0.1.0

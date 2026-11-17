# Roadmap

UniEnv's goal: **an AI tool should never have to install the same thing twice, and should always know what's available.** Success is measured as "installs per agent session trending to zero", not as how many packages we ship.

Items marked 🙋 are great first contributions. Comment on the linked issue to claim one. The full list is in [issues](https://github.com/rupeshbharambe24/unienv-ai/issues).

## Shipped
- **0.1:** one shared environment in `~/.unienv`, batteries as TOML recipes, shell and agent wiring, `need` with rollback, `doctor`
- **0.2:** `unienv mcp` server for Claude Desktop and other MCP apps, the one-click `.mcpb` extension, `mcp install` with absolute paths, a doctor handshake check, and a reproducible benchmark
- **0.2.1:** safe parallel `need` (locking and atomic writes), uninstall removes MCP entries, MCP flag-injection fix, unit tests
- **Since 0.2.1:** lint CI (ruff + shellcheck) and a recipe JSON Schema validated in CI

## Next: 0.3, solid everywhere it runs today
- [ ] Fix the open [bugs](https://github.com/rupeshbharambe24/unienv-ai/labels/bug), starting with dotfile safety (#56, #57, #61), `--no-wire` being forgotten (#58), and the MCP server crashing on bad requests (#67) 🙋
- [ ] apt backend that installs, not just prints (`--yes`), plus dnf and pacman (#7, #18) 🙋
- [ ] Adapters: Cursor (#4), Windsurf (#5), Cline and Roo Code (#6) 🙋
- [ ] `unienv remove` (#26) and `unienv upgrade` (#27)
- [ ] Install and update from the latest release, with verification (#28)
- [ ] Batteries: `latex` (#1), `diagrams` (#2), `browser` with one shared Playwright Chromium (#3), `ml` (#35), `speech` (#36) 🙋
- [ ] Homebrew tap: `brew install rupeshbharambe24/tap/unienv` (#39)
- [ ] CI coverage for LibreOffice conversions, the `curl | bash` install and uninstall (#44, #74) 🙋

## 0.4: desktop apps and sandboxes
- [ ] Generated sandbox profiles for Claude Code `/sandbox`, Codex `workspace-write` and Gemini Seatbelt, so sandboxes allow UniEnv instead of agents escalating to full access (#37)
- [ ] MCP: images of rendered pages and charts (#31), cancellation and progress (#32), a read-only safe mode (#33), more apps (#30)
- [ ] Ship as a Claude Code plugin (#40)
- [ ] Shared caches: one `HF_HOME`, one Playwright browser install

## 1.0: everywhere, verifiably
- [ ] Windows (winget/Scoop) and WSL (#8)
- [ ] `unienv export` / `import`: the same environment on another machine (#34)
- [ ] Dev Container Feature and Docker image from the same recipes (#41)
- [ ] Signed recipe index with checksum and attestation verification
- [ ] Optional, opt-in, anonymous usage stats (Homebrew-style), never on by default
- [ ] An open benchmark of local-machine environment failures for AI agents (#46)

Have an idea? Open a [discussion](https://github.com/rupeshbharambe24/unienv-ai/discussions).

# Roadmap

UniEnv's goal: **an AI tool should never have to install the same thing twice, and should always know what's available.** Success is measured as "installs per agent session trending to zero", not as how many packages we ship.

Items marked 🙋 are great first contributions. Comment on the linked issue to claim one.

## 0.2: breadth
- [ ] apt backend that installs, not just prints (`--yes`), plus dnf and pacman 🙋
- [ ] Adapters: Cursor, Windsurf, Cline, Roo Code, GitHub Copilot, Aider 🙋
- [ ] Recipe JSON Schema, validated in CI 🙋
- [ ] Homebrew tap: `brew install rupeshbharambe24/tap/unienv`
- [ ] Batteries: `latex`, `browser` (one shared Playwright Chromium), `diagrams` (graphviz, mermaid-cli) 🙋

## 0.3: desktop apps and sandboxes
- [x] `unienv mcp`: MCP server so desktop apps (Claude Desktop, LM Studio, …) can use the same tools, plus a Claude Desktop Extension (`.mcpb`)
- [x] MCP wiring with absolute paths (no `npx -y` cold starts), doctor handshake check
- [ ] Generated sandbox profiles for Claude Code `/sandbox`, Codex `workspace-write` and Gemini Seatbelt, so sandboxes allow UniEnv instead of agents escalating to full access
- [ ] Shared caches: one `HF_HOME`, one Playwright browser install

## 1.0: everywhere, verifiably
- [ ] Windows (winget/Scoop) and WSL
- [ ] Signed recipe index with checksum and attestation verification
- [ ] Export the same lockfile to a Dockerfile / Dev Container Feature for teams that want isolation
- [ ] Optional, opt-in, anonymous usage stats (Homebrew-style), never on by default
- [ ] An open benchmark of local-machine environment failures for AI agents

Have an idea? Open a [discussion](https://github.com/rupeshbharambe24/unienv-ai/discussions).

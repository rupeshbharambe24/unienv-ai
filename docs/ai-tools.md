# AI tool integrations

UniEnv reaches AI tools in two ways:

1. **Shell wiring.** Any tool that runs shell commands (almost all of them) finds `ue-py`, `ue-node`, `soffice-safe` and the system tools on PATH.
2. **Instructions.** Tools are *told* the environment exists, through their global instruction file and, where supported, a `unienv` skill. This is what stops the "create a venv and reinstall" reflex.

| Tool | Instructions | Skill | Detected by |
|---|---|---|---|
| **Claude Code** | `~/.claude/CLAUDE.md` | `~/.claude/skills/unienv/SKILL.md` | `~/.claude/` or `claude` on PATH |
| **Codex CLI** | `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`) | `~/.codex/skills/unienv/SKILL.md` | `~/.codex/` or `codex` |
| **Gemini CLI** | `~/.gemini/GEMINI.md` | none | `~/.gemini/` or `gemini` |
| **OpenCode** | `~/.config/opencode/AGENTS.md` | none | `~/.config/opencode/` or `opencode` |
| **Cursor** | paste `~/.unienv/AGENTS.md` into *Settings → Rules → User Rules* | none | manual for now |
| **Windsurf, Cline, Roo, Copilot, Aider** | paste `~/.unienv/AGENTS.md` into the tool's global rules | none | adapters planned, help wanted |
| **Claude Desktop chat** | **UniEnv MCP server**: `unienv mcp install` or the `.mcpb` extension ([guide](mcp.md)) | n/a | config file |
| **LM Studio, other MCP apps** | MCP server via `unienv mcp config` | n/a | manual |

Run `unienv sync --all-agents` to write instruction files even for tools that aren't installed yet.

## Notes per tool

**Claude Code** reads `CLAUDE.md`, not `AGENTS.md`, when both exist, so UniEnv writes to `CLAUDE.md`. Its Bash tool runs `zsh -c` (or your shell) per command, so `~/.zshenv` wiring is what makes tools visible. Restart Claude Code after installing so it re-reads its environment and skills.

**Codex CLI** runs commands through a login shell by default, which reads `~/.profile` (or `~/.bash_profile`). In `workspace-write` sandbox mode, network access is off by default. `unienv need` from inside Codex may need approval to reach PyPI or Homebrew.

**Gemini CLI** loads `~/.gemini/GEMINI.md` as global context.

**Desktop apps** don't start a shell and get the system's minimal PATH. That's why MCP servers configured as `npx …` or `uvx …` fail with `spawn ENOENT`. Use absolute paths, for example `"command": "/Users/you/.unienv/bin/ue-py"`.

## Adding a tool

See [CONTRIBUTING.md](../CONTRIBUTING.md#writing-an-agent-adapter). An adapter is a few lines in `agent_targets()` in `src/unienv.py`, plus links to the tool's official docs proving the paths.

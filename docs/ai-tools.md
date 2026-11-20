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
| **Windsurf** | `~/.codeium/windsurf/memories/global_rules.md` ([docs](https://docs.windsurf.com/windsurf/cascade/memories#rules)) | none | `~/.codeium/windsurf/` or `windsurf` |
| **Cursor** | paste `~/.unienv/AGENTS.md` into *Settings → Rules → User Rules*; MCP tools via `unienv mcp install --client cursor` | none | manual for now ([#4](https://github.com/rupeshbharambe24/unienv-ai/issues/4)) |
| **Cline, Roo, Copilot, Aider** | paste `~/.unienv/AGENTS.md` into the tool's global rules | none | adapters planned, help wanted |
| **Claude Desktop chat** | **UniEnv MCP server**: `unienv mcp install` or the `.mcpb` extension ([guide](mcp.md)) | n/a | config file |
| **LM Studio, other MCP apps** | MCP server via `unienv mcp config` | n/a | manual |

Run `unienv sync --all-agents` to write instruction files even for tools that aren't installed yet.

## Notes per tool

**Claude Code** reads `CLAUDE.md`, not `AGENTS.md`, when both exist, so UniEnv writes to `CLAUDE.md`. Its Bash tool runs `zsh -c` (or your shell) per command, so `~/.zshenv` wiring is what makes tools visible. Restart Claude Code after installing so it re-reads its environment and skills.

**Codex CLI** runs commands through a login shell by default, which reads `~/.profile` (or `~/.bash_profile`). In `workspace-write` sandbox mode, network access is off by default. `unienv need` from inside Codex may need approval to reach PyPI or Homebrew.

**Gemini CLI** loads `~/.gemini/GEMINI.md` as global context.

**Windsurf** loads global rules from `~/.codeium/windsurf/memories/global_rules.md` ([Windsurf Rules docs](https://docs.windsurf.com/windsurf/cascade/memories#rules)).

**Desktop apps** don't start a shell and get the system's minimal PATH. That's why MCP servers configured as `npx …` or `uvx …` fail with `spawn ENOENT`. Use absolute paths, for example `"command": "/Users/you/.unienv/bin/ue-py"`.

## Sandbox profiles (`unienv sandbox`)

Use these settings when an agent's sandbox needs to write UniEnv's cache, package state, or environment. The profiles grant access only to the configured UniEnv directory; they do not auto-approve arbitrary Python or shell commands.

```sh
unienv sandbox print --tool claude
unienv sandbox print --tool codex
unienv sandbox print --tool seatbelt
unienv sandbox install --tool claude
unienv sandbox install --tool codex
```

- Claude Code uses `sandbox.filesystem.allowRead` and `allowWrite` for the UniEnv directory plus a narrowly scoped Read permission. It does not change sandbox enablement or add Bash permissions. See the [sandbox settings documentation](https://code.claude.com/docs/en/sandboxing).
- Codex uses `sandbox_workspace_write.writable_roots`. The existing sandbox mode and network setting are preserved. The additional root is effective in `workspace-write` mode; choose that mode yourself if appropriate. The installer never silently changes a read-only policy. See the [configuration reference](https://developers.openai.com/codex/config-reference).
- Configuration is parsed before any write. Existing values and nested tables are retained, and timestamped backups are created. TOML is reserialized, so comments and formatting remain available in the backup rather than in the rewritten file.
- UniEnv records which entries it added in `~/.unienv/sandbox-settings.json`. `unienv uninstall` removes only those entries, retaining pre-existing grants and subsequent unrelated settings. Invalid or unreadable configurations are reported instead of overwritten.
- Seatbelt/Gemini output is a snippet to merge into an existing policy, not a complete sandbox policy. It grants file access to UniEnv only; keep the agent's normal temporary-directory and executable rules.

Package installation, upgrades, sync, and update need write access to UniEnv. Sandbox permissions do not provide network permission automatically, and organization-managed policy can override local settings.

## Adding a tool

See [CONTRIBUTING.md](../CONTRIBUTING.md#writing-an-agent-adapter). An adapter is a few lines in `agent_targets()` in `src/unienv.py`, plus links to the tool's official docs proving the paths.

# UniEnv MCP server: for Claude Desktop and other AI apps

Terminal agents (Claude Code, Codex, Gemini CLI…) use UniEnv directly through the shell. **Chat apps can't run shell commands**: when Claude Desktop chat creates a Word file, it normally does it in a cloud sandbox, not on your computer. The UniEnv MCP server closes that gap. It runs **on your computer** and gives any MCP-capable app six tools backed by your UniEnv environment.

| Tool | What it does | Changes files? |
|---|---|---|
| `unienv_capabilities` | Lists installed tools and packages, with usage notes | no |
| `read_document` | Word, Excel, PowerPoint, PDF, HTML, CSV, images (OCR) → text | no |
| `convert_document` | docx/xlsx/pptx/md/html → pdf, md → docx, doc → docx, docx → md, pdf → txt/png … (never overwrites; PDF pages go into a new `<name>-pages` folder) | creates new files |
| `run_python` | Runs a Python 3.12 script with python-docx, openpyxl, python-pptx, pandas, matplotlib … | yes |
| `run_tool` | Runs an allow-listed UniEnv tool (pandoc, soffice-safe, pdftotext, qpdf, tesseract, ue-node …), no shell | yes |
| `install_package` | `unienv need`: permanently add a package for every AI tool | installs software |

Each tool declares MCP safety hints (read-only vs. can change files), so apps like Claude Desktop can ask your permission accordingly. `install_package` only accepts real package names (no flags or shell characters), so the `kind` you approve is the kind that gets installed.

## Set it up

You need UniEnv installed first (`curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash`). Then pick **one** of these:

### Option A: one command (recommended)

```sh
unienv mcp install                      # Claude Desktop
unienv mcp install --client cursor      # Cursor
unienv mcp install --client windsurf    # Windsurf
unienv mcp install --client lm-studio   # LM Studio
unienv mcp install --client vscode      # VS Code / GitHub Copilot
unienv mcp install --client zed         # Zed
unienv mcp install --client all         # every supported app that's installed
```

| Client (`--client`) | Config path | Root JSON key |
|---|---|---|
| `claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) / `~/.config/Claude/claude_desktop_config.json` (Linux) | `mcpServers` |
| `cursor` | `~/.cursor/mcp.json` | `mcpServers` |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| `lm-studio` | `~/.lmstudio/mcp.json` | `mcpServers` |
| `vscode` | `~/Library/Application Support/Code/User/mcp.json` (macOS) / `~/.config/Code/User/mcp.json` (Linux) | `servers` |
| `zed` | `~/.config/zed/settings.json` | `context_servers` |

This merges a `unienv` entry into the app's MCP config (a timestamped backup is made first) using **absolute paths**, so it works even though desktop apps start with a minimal PATH. **Quit the app fully and reopen it.**

Zed settings that contain `//` or `/* */` comments are preserved: `unienv mcp install` prints the `unienv mcp config --client zed` snippet to paste manually, and `doctor` and `uninstall` skip the file quietly. Zed's current custom-server schema uses `command`, `args` and `env` under `context_servers` (https://zed.dev/docs/ai/mcp), and VS Code's `mcp.json` uses `"type": "stdio"` under `servers`. Claude Code uses UniEnv through the shell; its frequently rewritten `~/.claude.json` state file is never edited by these commands.

### Option B: Claude Desktop Extension (one click)

Download `unienv.mcpb` from the [latest release](https://github.com/rupeshbharambe24/unienv-ai/releases/latest) and double-click it (or drag it into *Claude → Settings → Extensions*). You can choose the output folder in the extension's settings. The extension is a thin launcher for your installed UniEnv, so it never goes stale when UniEnv updates.

> Use A **or** B, not both, otherwise Claude sees the tools twice.

### Any other MCP app (LM Studio, …)

```sh
unienv mcp config
```

prints the JSON snippet to paste into the app's MCP settings:

```json
{ "mcpServers": { "unienv": { "command": "/Users/you/.unienv/bin/unienv", "args": ["mcp"] } } }
```

## Try it

In Claude Desktop chat:

> *"Read ~/Downloads/contract.pdf and give me the key dates."*
>
> *"Create a Word document from this text, with the first line as a title, and a PDF copy."*
>
> *"Turn this table into an Excel file with a total row and a chart."*

New files go to **`~/UniEnv`** unless you or Claude give another path (override with `UNIENV_WORKDIR`, or the extension setting). Claude reports the full path of every file it creates. The first time it reads from `~/Downloads`, `~/Documents` or `~/Desktop`, macOS may ask whether Claude can access that folder.

## How it's built

- **Standard library only**, running on UniEnv's own uv-managed Python. It starts in milliseconds, with no `npx`/`uvx` downloads and no dependency to break.
- **stdio transport**, MCP protocol versions 2024-11-05 through 2025-11-25. Logs go to stderr and stdout carries only protocol messages.
- Child processes get an explicit PATH (UniEnv + Homebrew + system), **stdin closed** (no hangs) and capped output (20,000 characters) to protect the model's context.
- `unienv doctor` performs a real MCP handshake against every configured app.
- Tested end-to-end in CI (`tests/mcp_test.py`) and with a real MCP client: Claude Code headless with only this server and no shell tools, which created a Word file and its PDF in 24 s.

## Security model

MCP safety annotations are hints to the client, not access controls. Approval prompts depend on the client. The server enforces these environment settings independently of prompts:

- `UNIENV_MCP_MODE=readonly` exposes only `unienv_capabilities`, `read_document`, and `convert_document`. Conversion still writes files: its destination must be exactly the source directory or `UNIENV_WORKDIR` (default `~/UniEnv`). Other output directories are rejected before they are created.
- `UNIENV_MCP_ALLOW_INSTALL=0` hides and disables `install_package`. Boolean `false`, `no`, and `off` also disable it.
- `UNIENV_MCP_ROOTS=~/Documents:~/Downloads:~/UniEnv` restricts document input, conversion output and working-directory parameters to these folders and their descendants after resolving symlinks. Include the workdir if you want outputs there. Empty means unrestricted; a nonempty list with no directories is rejected. Paths are separated by `:` on supported macOS/Linux systems.
- Configuring roots also hides and disables `run_python`, `run_tool`, and `install_package`, even in full mode: arbitrary Python, command arguments and package installation cannot be confined by checking a working directory.
- Conversion uses Pandoc's `--sandbox` only when roots are configured or readonly mode is active, so referenced local or remote resources cannot be read implicitly in safe mode. Safe mode requires Pandoc 2.15 or newer; an older version fails rather than disabling the sandbox. Normal mode keeps compatibility with older Pandoc and embedded resources. Documents depending on external images or includes may lose those resources or fail to convert. Dangling symlinks count as occupied output names and are never reused.

The Claude Desktop extension exposes **MCP mode**, **Allow package installation**, and **Allowed folders** alongside **Output folder** in its settings. Defaults preserve full access. For other clients, put the variables in the server's `env` configuration and restart the server.

These controls restrict the server's exposed tools and explicit paths; they are not an OS sandbox. Document parsers and converters still run as your user, and concurrent filesystem changes can race path checks. Process untrusted documents using an OS sandbox or a separate account for stronger isolation. Without roots or readonly mode, `run_python` and `run_tool` have your account's filesystem and network access; disabling installation alone does not limit code execution.
## Cancellation and progress

Long-running tool calls run on a worker while the server continues answering pings and cancellation notifications. When a call supplies `params._meta.progressToken`, the server reports its start, sends progress approximately every 10 seconds while waiting for a child process, and reports completion. Progress is an increasing activity counter, not a percentage; the total is omitted because the amount of work is unknown.

Send `notifications/cancelled` with the original `requestId` to cancel a queued or running tool call. The server stops its process group, cleans up, and does not send a response for an accepted cancellation. Unknown, completed, and malformed cancellation IDs are ignored. Package installation is deliberately non-cancellable: cancellation is ignored and the normal response is sent after the install completes or rolls back. This protects package state from interruption.

Timeout and cancellation cleanup share a process-group termination helper and a bounded pipe drain. Descendants that deliberately detach into a new session cannot be terminated by killing the original process group; however, an inherited open pipe cannot hold the MCP worker indefinitely.

## What it can't do

- **Claude.ai on the web / ChatGPT on the web** can't reach your computer at all; MCP servers only run where the app runs.
- **Cowork** runs tasks in its own Linux virtual machine; UniEnv's macOS tools can't run inside it.
- **Windows**: not yet (see the [roadmap](../ROADMAP.md)).

## Remove it

```sh
unienv mcp uninstall --client all
```

`unienv uninstall` also removes the `unienv` entry from every app's config (other servers are left alone, and a backup is kept). If you installed the `.mcpb` extension, remove it in *Claude → Settings → Extensions*.

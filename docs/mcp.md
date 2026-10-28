# UniEnv MCP server: for Claude Desktop and other AI apps

Terminal agents (Claude Code, Codex, Gemini CLI…) use UniEnv directly through the shell. **Chat apps can't run shell commands**: when Claude Desktop chat creates a Word file, it normally does it in a cloud sandbox, not on your computer. The UniEnv MCP server closes that gap. It runs **on your computer** and gives any MCP-capable app six tools backed by your UniEnv environment.

| Tool | What it does | Changes files? |
|---|---|---|
| `unienv_capabilities` | Lists installed tools and packages, with usage notes | no |
| `read_document` | Word, Excel, PowerPoint, PDF, HTML, CSV, images (OCR) → text | no |
| `convert_document` | docx/xlsx/pptx/md/html → pdf, md → docx, doc → docx, docx → md, pdf → txt/png … (never overwrites) | creates new files |
| `run_python` | Runs a Python 3.12 script with python-docx, openpyxl, python-pptx, pandas, matplotlib … | yes |
| `run_tool` | Runs an allow-listed UniEnv tool (pandoc, soffice-safe, pdftotext, qpdf, tesseract, ue-node …), no shell | yes |
| `install_package` | `unienv need`: permanently add a package for every AI tool | installs software |

Each tool declares MCP safety hints (read-only vs. can change files), so apps like Claude Desktop can ask your permission accordingly.

## Set it up

You need UniEnv installed first (`curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash`). Then pick **one** of these:

### Option A: one command (recommended)

```sh
unienv mcp install                 # Claude Desktop
unienv mcp install --client cursor # Cursor
unienv mcp install --client all    # every supported app that's installed
```

This merges a `unienv` entry into the app's MCP config (a timestamped backup is made first) using **absolute paths**, so it works even though desktop apps start with a minimal PATH. **Quit the app fully and reopen it.**

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

## What it can't do

- **Claude.ai on the web / ChatGPT on the web** can't reach your computer at all; MCP servers only run where the app runs.
- **Cowork** runs tasks in its own Linux virtual machine; UniEnv's macOS tools can't run inside it.
- **Windows**: not yet (see the [roadmap](../ROADMAP.md)).

## Remove it

```sh
unienv mcp uninstall --client all
```

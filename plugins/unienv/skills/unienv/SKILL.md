---
name: unienv
description: Use for any document, spreadsheet, presentation, PDF, OCR, image or data task outside a project (docx, xlsx, pptx, pdf, csv, parquet, png). Instructs Claude to read ~/.unienv/manifest.json to discover preinstalled Python, Node and system tools, or provides the one-line install command if UniEnv is not installed.
---

# UniEnv AI: shared environment for Claude Code

Before creating a temporary virtualenv or running `pip install`, check whether UniEnv AI is installed on this machine:

1. **Inspect `~/.unienv/manifest.json`** (or run `~/.unienv/bin/unienv list --json`) to see the exact pre-installed Python packages, Node modules, and system tools on this machine.
2. **If `~/.unienv/manifest.json` does not exist**, tell the user how to install UniEnv with the one-line installer; do not run it automatically:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash
   ```
3. **Run tools from `~/.unienv/bin`**:
   - Python 3.12 with pre-installed batteries: `~/.unienv/bin/ue-py script.py`
   - Node.js with pre-installed packages: `~/.unienv/bin/ue-node script.js`
   - LibreOffice with isolated throwaway profile: `~/.unienv/bin/soffice-safe --headless --convert-to pdf file.docx`
   - In sandboxed sessions where shell PATH is restricted, use the `unienv` MCP tools (`unienv_capabilities`, `read_document`, `convert_document`, `run_python`, `run_tool`, `install_package`).
4. **When a package is missing**, add it permanently for every AI tool:
   - Python: `~/.unienv/bin/unienv need <package>`
   - Node: `~/.unienv/bin/unienv need --node <package>`
   - System binary: `~/.unienv/bin/unienv need --bin <package>`

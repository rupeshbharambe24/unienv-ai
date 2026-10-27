## UniEnv AI: shared tool environment on this machine

This machine has a persistent, pre-installed environment for ad-hoc work (documents, spreadsheets, PDFs, OCR, data). It is shared by every AI tool. **Use it instead of creating temporary virtualenvs or reinstalling packages.**

- Python 3.12 with batteries: `ue-py` (absolute path: `{{BIN}}/ue-py`), e.g. `ue-py script.py`, `ue-py -m markitdown file.docx`
- Node.js with batteries: `ue-node` (absolute path: `{{BIN}}/ue-node`); preinstalled: {{NODE_PACKAGES}}
- System tools: {{TOOLS}}
- Python packages: {{PY_PACKAGES}}
- Full machine-readable list: `{{HOME_DIR}}/manifest.json`, or run `unienv list`

Rules:
1. Inside a project that has its own environment (`pyproject.toml`, `.venv`, `package.json`), use that project's environment (`uv run`, `.venv/bin/python`, npm). UniEnv is for work outside projects.
2. Never create throwaway venvs for one-off tasks, and never use `pip install --user` or `--break-system-packages`.
3. If something is missing, install it permanently for every AI tool: `unienv need <python-package>`, `unienv need --node <package>`, `unienv need --bin <homebrew-formula>` or `unienv need --cask <homebrew-cask>`.
4. If a tool seems broken or missing, run `unienv doctor --json` before reinstalling anything.
5. Use `soffice-safe` instead of `soffice` or `libreoffice`.

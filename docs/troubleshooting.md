# Troubleshooting

Start with:

```sh
unienv doctor          # human-readable, with a fix hint under each problem
unienv doctor --fix    # regenerate wiring, then re-check
unienv doctor --json   # attach this to bug reports
```

## "command not found: ue-py" in my AI tool

- Restart the AI tool (or open a new terminal). Running apps keep the PATH they started with.
- Check the shell wiring: `unienv doctor` shows a ✗ for any shell mode that can't see UniEnv. `unienv wire` rewrites the blocks.
- Desktop apps never read shell files: use absolute paths from `~/.unienv/manifest.json`.

## The agent still creates a virtualenv

- Check that the instruction block exists: `unienv doctor` lists each agent file.
- Inside a project with its own `pyproject.toml` or `.venv`, using the project's env is correct behaviour.
- Agents sometimes ignore instructions. Asking "use ue-py" once in the conversation usually sticks. Please open an issue with the transcript; it helps us improve the instruction text.

## Agent commands hang for ~2 minutes (often starting with `ls`)

Claude Code replays your shell aliases into agent commands. If `ls` is aliased to **eza** (0.23+), eza waits for file names on stdin, which is never closed in agent shells, so the command blocks until the tool times out. `bat` (as `cat`) and `rm -i`/`cp -i`/`mv -i` cause similar trouble. `unienv doctor` warns about these. Keep them for your own terminal only:

```sh
# ~/.zshrc
if [ -z "${AI_AGENT:-}${CLAUDECODE:-}" ]; then
  alias ls='eza --icons=always --group-directories-first --git'
fi
```

## `unienv need` is slow or fails

- `need` re-resolves the shared env with uv. With a warm cache it takes about 2 seconds, but on a slow network it waits for PyPI. Set `UV_HTTP_TIMEOUT=120` if your connection is flaky.
- A failed `need` rolls back automatically. Check `~/.unienv/log/unienv.log`.
- Behind a corporate proxy, uv and npm need `HTTPS_PROXY` plus your CA certificate (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`).

## LibreOffice

- Always use `soffice-safe`. Plain `soffice` shares one profile, so a second call silently does nothing.
- The first conversion after install takes a few seconds while LibreOffice warms up.
- Layout differs from Word: `unienv install --add fonts` installs metric-compatible fonts (Carlito = Calibri, Caladea = Cambria).

## OCR on macOS says "image file not found"

Tesseract's image library rewrites paths starting with `/tmp`. UniEnv's `env.sh` sets `TMPDIR=/private/tmp` (the same folder) to avoid it. If you see this, your shell probably isn't sourcing `env.sh`: run `unienv doctor`.

## Undo everything

```sh
unienv uninstall           # remove every block UniEnv wrote
unienv uninstall --purge   # ...and delete ~/.unienv
```

Homebrew/apt packages are left installed; remove them with `brew uninstall` if you want.

#!/bin/sh
# Launcher used by the UniEnv Claude Code plugin.
# Delegates to ~/.unienv/bin/unienv mcp and shows the one-line installer if missing.
UE="${UNIENV_HOME:-$HOME/.unienv}/bin/unienv"
if [ ! -x "$UE" ]; then
  echo "UniEnv is not installed on this computer. Install it first:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash" >&2
  exit 1
fi
exec "$UE" mcp "$@"

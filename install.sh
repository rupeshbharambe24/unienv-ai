#!/usr/bin/env bash
# UniEnv AI bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.sh | bash
#   ./install.sh                     install default batteries and wire everything
#   ./install.sh --dev               same, but link ~/.unienv/share to this checkout
#   ./install.sh --no-system         skip Homebrew/apt packages
#   ./install.sh --batteries office,pdf
#
# Everything after the bootstrap flags is passed to `unienv install`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
REF="${UNIENV_REF:-main}"
UNIENV_HOME="${UNIENV_HOME:-$HOME/.unienv}"
export UNIENV_HOME

DEV=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ARGS+=("$a") ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }

# 0. Run via `curl -fsSL .../install.sh | bash`? Then there is no checkout next to us:
#    fetch the source tarball first.
if [ ! -f "$REPO/src/unienv.py" ]; then
  say "Downloading UniEnv AI ($REF)"
  TMP_SRC="$(mktemp -d)"
  trap 'rm -rf "$TMP_SRC"' EXIT
  curl -fsSL "https://github.com/rupeshbharambe24/unienv-ai/archive/refs/heads/$REF.tar.gz" \
    | tar -xz -C "$TMP_SRC" --strip-components=1
  REPO="$TMP_SRC"
  if [ "$DEV" = 1 ]; then echo "--dev needs a git checkout: git clone https://github.com/rupeshbharambe24/unienv-ai" >&2; exit 2; fi
fi

# 1. uv: manages the pinned Python for both the CLI and the shared environment
UV="$(command -v uv || true)"
for c in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
  if [ -z "$UV" ] && [ -x "$c" ]; then UV="$c"; fi
done
if [ -z "$UV" ]; then
  say "Installing uv (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

# 2. A uv-managed Python 3.12 runs the CLI: never Homebrew's or the OS Python,
#    so `brew upgrade` or a broken shared env can't take the CLI down with it.
say "Python 3.12 (uv-managed)"
"$UV" python install 3.12 --quiet
PY="$("$UV" python find 3.12 --managed-python)"

# 3. Put the CLI, recipes and templates into the prefix
mkdir -p "$UNIENV_HOME"
rm -rf "$UNIENV_HOME/share"
if [ "$DEV" = 1 ]; then
  ln -s "$REPO" "$UNIENV_HOME/share"
else
  mkdir -p "$UNIENV_HOME/share"
  cp -R "$REPO/src" "$REPO/recipes" "$REPO/templates" "$REPO/shims" "$UNIENV_HOME/share/"
fi

# 4. Hand over to the CLI
PATH="$(dirname "$UV"):$PATH" "$PY" "$UNIENV_HOME/share/src/unienv.py" install ${ARGS[@]+"${ARGS[@]}"}

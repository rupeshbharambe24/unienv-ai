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

if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  REPO=""
fi
REF="${UNIENV_REF:-}"
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
#    fetch the latest release tarball (or $UNIENV_REF) and verify SHA256SUMS when installing a release tag.
if [ -z "$REPO" ] || [ ! -f "$REPO/src/unienv.py" ]; then
  if [ "$DEV" = 1 ]; then echo "--dev needs a git checkout: git clone https://github.com/rupeshbharambe24/unienv-ai" >&2; exit 2; fi
  if [ -z "$REF" ]; then
    REF="$(curl -fsSL https://api.github.com/repos/rupeshbharambe24/unienv-ai/releases/latest 2>/dev/null \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    if [ -z "$REF" ]; then
      echo "Could not resolve the latest UniEnv release tag. Set UNIENV_REF=main to install from main without release verification." >&2
      exit 1
    fi
  fi
  say "Downloading UniEnv AI ($REF)"
  TMP_SRC="$(mktemp -d)"
  trap 'rm -rf "$TMP_SRC"' EXIT
  ARCHIVE="$TMP_SRC/unienv.tar.gz"
  case "$REF" in
    v[0-9]*)
      ASSET_NAME="unienv-ai-$REF.tar.gz"
      if ! curl -fsSL "https://github.com/rupeshbharambe24/unienv-ai/releases/download/$REF/SHA256SUMS" > "$TMP_SRC/SHA256SUMS" 2>/dev/null; then
        echo "Could not download SHA256SUMS for release $REF. Set UNIENV_REF=main to install from main without release verification." >&2
        exit 1
      fi
      if ! curl -fsSL "https://github.com/rupeshbharambe24/unienv-ai/releases/download/$REF/$ASSET_NAME" > "$ARCHIVE" 2>/dev/null; then
        echo "Could not download release asset $ASSET_NAME for $REF." >&2
        exit 1
      fi
      EXPECTED_SHA="$(awk -v f="$ASSET_NAME" '$2 == f || $2 == "*"f {print tolower($1)}' "$TMP_SRC/SHA256SUMS")"
      if [ -z "$EXPECTED_SHA" ]; then
        echo "SHA256SUMS for $REF did not contain $ASSET_NAME" >&2
        exit 1
      fi
      if command -v sha256sum >/dev/null 2>&1; then
        ACTUAL_SHA="$(sha256sum "$ARCHIVE" | awk '{print tolower($1)}')"
      elif command -v shasum >/dev/null 2>&1; then
        ACTUAL_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print tolower($1)}')"
      elif command -v openssl >/dev/null 2>&1; then
        ACTUAL_SHA="$(openssl dgst -sha256 "$ARCHIVE" | awk '{print tolower($NF)}')"
      else
        echo "No SHA-256 checksum tool (sha256sum, shasum, or openssl) found to verify $ASSET_NAME" >&2
        exit 1
      fi
      if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
        echo "SHA-256 mismatch for $ASSET_NAME: expected $EXPECTED_SHA, got $ACTUAL_SHA" >&2
        exit 1
      fi
      ;;
    *)
      curl -fsSL "https://github.com/rupeshbharambe24/unienv-ai/archive/$REF.tar.gz" > "$ARCHIVE"
      ;;
  esac
  tar -xzf "$ARCHIVE" -C "$TMP_SRC" --strip-components=1
  rm -f "$ARCHIVE" "$TMP_SRC/SHA256SUMS"
  REPO="$TMP_SRC"
fi

# 1. uv: manages the pinned Python for both the CLI and the shared environment
resolve_uv() {
  UV="$(command -v uv || true)"
  for c in \
    "${UV_INSTALL_DIR:+$UV_INSTALL_DIR/uv}" \
    "${CARGO_HOME:-$HOME/.cargo}/bin/uv" \
    "${XDG_BIN_HOME:+$XDG_BIN_HOME/uv}" \
    "$HOME/.local/bin/uv" \
    /opt/homebrew/bin/uv \
    /usr/local/bin/uv; do
    if [ -z "$UV" ] && [ -n "$c" ] && [ -x "$c" ]; then UV="$c"; fi
  done
}
resolve_uv
if [ -z "$UV" ]; then
  say "Installing uv (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  resolve_uv
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

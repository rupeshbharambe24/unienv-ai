#!/usr/bin/env bash
set -euo pipefail

FEATURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEATURE_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$FEATURE_DIR/devcontainer-feature.json" | head -n 1)"
UNIENV_REF="${UNIENV_REF:-v${FEATURE_VERSION:-0.2.2}}"

BATTERIES="${BATTERIES:-}"
NOSYSTEM="${NOSYSTEM:-false}"
TARGET_USER="${_REMOTE_USER:-root}"
TARGET_HOME="${_REMOTE_USER_HOME:-}"

if [ -z "$TARGET_HOME" ]; then
  if [ "$TARGET_USER" = "root" ]; then
    TARGET_HOME="/root"
  else
    TARGET_HOME="/home/$TARGET_USER"
  fi
fi

# Install system packages as root before switching to a non-root user,
# so images without sudo still get every system dependency.
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  if [ "$NOSYSTEM" = "true" ]; then
    apt-get install -y --no-install-recommends ca-certificates curl tar
  else
    apt-get install -y --no-install-recommends \
      ca-certificates curl tar git nodejs npm \
      pandoc poppler-utils qpdf ghostscript tesseract-ocr jq ripgrep
  fi
  rm -rf /var/lib/apt/lists/*
fi

ARGS=("--yes" "--all-agents" "--no-system")
if [ -n "$BATTERIES" ]; then
  ARGS+=("--batteries" "$BATTERIES")
fi

INSTALL_URL="https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/${UNIENV_REF}/install.sh"
TMP_INSTALLER="$(mktemp)"
trap 'rm -f "$TMP_INSTALLER"' EXIT
curl -fsSL "$INSTALL_URL" -o "$TMP_INSTALLER"
chmod 0644 "$TMP_INSTALLER"

if [ "$TARGET_USER" != "root" ] && command -v su >/dev/null 2>&1; then
  su - "$TARGET_USER" -c "UNIENV_REF='$UNIENV_REF' bash '$TMP_INSTALLER' ${ARGS[*]}"
else
  HOME="$TARGET_HOME" UNIENV_HOME="$TARGET_HOME/.unienv" UNIENV_REF="$UNIENV_REF" \
    bash "$TMP_INSTALLER" "${ARGS[@]}"
fi

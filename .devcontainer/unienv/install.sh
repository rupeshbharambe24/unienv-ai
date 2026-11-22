#!/usr/bin/env bash
set -euo pipefail

FEATURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEATURE_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$FEATURE_DIR/devcontainer-feature.json" | head -n 1)"
UNIENV_REF="${UNIENV_REF:-v${FEATURE_VERSION:-0.3.0}}"

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

# Bootstrap curl/tar/ca-certificates and fetch the pinned UniEnv release archive
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends ca-certificates curl tar
fi

TMP_SRC="$(mktemp -d)"
trap 'rm -rf "$TMP_SRC"' EXIT
ARCHIVE_URL="https://github.com/rupeshbharambe24/unienv-ai/archive/${UNIENV_REF}.tar.gz"
curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$TMP_SRC" --strip-components=1
chmod -R a+rX "$TMP_SRC"

# Install system packages as root from the chosen batteries' recipes/*.toml ([system.debian]),
# so images without sudo still get every system dependency (including LibreOffice and opt-in batteries).
if command -v apt-get >/dev/null 2>&1; then
  if [ "$NOSYSTEM" != "true" ]; then
    APT_PKGS=()
    # Parse [system.debian] apt = [...] from recipes/*.toml for selected batteries (+ always=true recipes)
    for recipe in "$TMP_SRC"/recipes/*.toml; do
      [ -f "$recipe" ] || continue
      rid="$(basename "$recipe" .toml)"
      include=0
      if grep -Eq '^[[:space:]]*always[[:space:]]*=[[:space:]]*true' "$recipe"; then
        include=1
      elif [ -z "$BATTERIES" ]; then
        if grep -Eq '^[[:space:]]*default[[:space:]]*=[[:space:]]*true' "$recipe"; then
          include=1
        fi
      else
        case ",${BATTERIES}," in
          *",${rid},"*) include=1 ;;
        esac
      fi
      if [ "$include" = 1 ]; then
        while IFS= read -r pkg; do
          [ -n "$pkg" ] && APT_PKGS+=("$pkg")
        done < <(
          awk '
            /^\[system\.debian\]/ { in_deb=1; next }
            /^\[/ { in_deb=0 }
            in_deb && /apt[[:space:]]*=/ {
              in_apt=1
            }
            in_deb && in_apt {
              line=$0
              while (match(line, /"[^"]+"/)) {
                print substr(line, RSTART+1, RLENGTH-2)
                line=substr(line, RSTART+RLENGTH)
              }
              if (index($0, "]") > 0) in_apt=0
            }
          ' "$recipe"
        )
      fi
    done
    if [ "${#APT_PKGS[@]}" -gt 0 ]; then
      apt-get install -y --no-install-recommends git "${APT_PKGS[@]}"
    else
      apt-get install -y --no-install-recommends git
    fi
  fi
  rm -rf /var/lib/apt/lists/*
fi

ARGS=("--yes" "--all-agents" "--no-system")
if [ -n "$BATTERIES" ]; then
  ARGS+=("--batteries" "$BATTERIES")
fi

if [ "$TARGET_USER" != "root" ] && command -v su >/dev/null 2>&1; then
  su - "$TARGET_USER" -c "UNIENV_REF='$UNIENV_REF' bash '$TMP_SRC/install.sh' ${ARGS[*]}"
else
  HOME="$TARGET_HOME" UNIENV_HOME="$TARGET_HOME/.unienv" UNIENV_REF="$UNIENV_REF" \
    bash "$TMP_SRC/install.sh" "${ARGS[@]}"
fi

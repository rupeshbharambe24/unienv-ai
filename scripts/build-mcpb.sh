#!/bin/sh
# Build dist/unienv.mcpb: the UniEnv Claude Desktop Extension (a zip with manifest.json at its root).
set -eu
cd "$(dirname "$0")/.."
mkdir -p dist
rm -f dist/unienv.mcpb
( cd mcpb && zip -qrX ../dist/unienv.mcpb manifest.json icon.png server )
echo "built dist/unienv.mcpb ($(wc -c < dist/unienv.mcpb | tr -d ' ') bytes)"

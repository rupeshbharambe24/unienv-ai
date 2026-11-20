#!/usr/bin/env bash
# Benchmark suite: compare AI agents across document, spreadsheet, presentation,
# PDF, and OCR tasks before vs after UniEnv.
#
#   bench/word-task.sh unienv   [--runs 10] [--agent claude|codex|gemini] [--task docx|xlsx|pptx|pdf-extract|ocr|docx-to-pdf]
#   bench/word-task.sh baseline [--runs 10] [--agent claude|codex|gemini] [--task docx|xlsx|pptx|pdf-extract|ocr|docx-to-pdf]
#   bench/word-task.sh summary  [--agent ...] [--task ...]
#
# Each run starts the selected agent CLI headless in a fresh empty folder
# outside any project, with a normal terminal PATH, and records wall time,
# turns, cost, tokens, tool calls, the shell commands run, and whether the task
# output is valid. Results are appended to bench/results.jsonl, followed by a
# median/min/max/IQR summary across runs.
#
# Baseline mode runs `unienv uninstall` (wiring only; ~/.unienv is kept) and
# `unienv sync` afterwards. It also snapshots Homebrew Python, user-site and
# global npm packages, and reports anything the baseline agent installed there.
set -euo pipefail

MODE="${1:?usage: word-task.sh unienv|baseline|summary [runs] [--runs N] [--agent claude|codex|gemini] [--task docx|xlsx|pptx|pdf-extract|ocr|docx-to-pdf]}"
shift

RUNS=10
AGENT="claude"
TASK="docx"
AGENT_FILTER=""
TASK_FILTER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --runs|-n) RUNS="${2:?missing value for --runs}"; shift 2 ;;
    --agent) AGENT="${2:?missing value for --agent}"; AGENT_FILTER="$AGENT"; shift 2 ;;
    --task) TASK="${2:?missing value for --task}"; TASK_FILTER="$TASK"; shift 2 ;;
    [0-9]*) RUNS="$1"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$HERE/results.jsonl"
SUITE="$HERE/run-suite.py"
UE="${UNIENV_HOME:-$HOME/.unienv}/bin/unienv"

if [ "$MODE" = summary ]; then
  FILTERS=(--results "$RESULTS")
  [ -z "$AGENT_FILTER" ] || FILTERS+=(--agent "$AGENT_FILTER")
  [ -z "$TASK_FILTER" ] || FILTERS+=(--task "$TASK_FILTER")
  python3 "$SUITE" summary "${FILTERS[@]}"
  exit 0
fi

WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ue_bench.XXXXXX")"
PROMPT="$(python3 "$SUITE" prompt --task "$TASK")"

# A "normal terminal" environment: Homebrew + system PATH, nothing from UniEnv.
clean_env() {
  env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" SHELL="${SHELL:-/bin/zsh}" TERM=xterm-256color \
    LANG="${LANG:-en_US.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
    PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" "$@"
}

run_agent() {
  case "$AGENT" in
    claude)
      clean_env claude -p "$PROMPT" --output-format stream-json --verbose \
        --permission-mode acceptEdits --allowedTools "Bash Read Write Edit Glob Grep Skill"
      ;;
    codex)
      clean_env codex exec --json --skip-git-repo-check --sandbox workspace-write \
        -c sandbox_workspace_write.network_access=true "$PROMPT"
      ;;
    gemini)
      clean_env gemini -p "$PROMPT" --output-format stream-json --yolo
      ;;
    *)
      echo "unsupported agent: $AGENT (expected claude, codex, or gemini)" >&2
      exit 2
      ;;
  esac
}

snapshot() {  # packages an agent might install outside a venv
  # shellcheck disable=SC2012  # ls only snapshots a sorted listing
  {
    echo "## brew-python"; /opt/homebrew/bin/python3 -m pip list --format=freeze 2>/dev/null | sort
    echo "## user-site"; ls "$(/opt/homebrew/bin/python3 -m site --user-site 2>/dev/null)" 2>/dev/null | sort
    echo "## npm-global"; npm ls -g --depth=0 --parseable 2>/dev/null | sort
  } > "$1"
}

restore() {
  if [ "$MODE" = baseline ]; then
    echo "==> Restoring UniEnv wiring"
    "$UE" sync >/dev/null
    for c in $MCP_RESTORE; do "$UE" mcp install --client "$c" >/dev/null; done
    "$UE" doctor | tail -1
  fi
}

if [ "$MODE" = baseline ]; then
  echo "==> Temporarily un-wiring UniEnv (restored automatically)"
  # uninstall also removes the MCP entry from desktop apps; remember which to put back
  MCP_RESTORE=""
  grep -qs '"unienv"' "$HOME/Library/Application Support/Claude/claude_desktop_config.json" \
    "$HOME/.config/Claude/claude_desktop_config.json" && MCP_RESTORE="claude-desktop"
  grep -qs '"unienv"' "$HOME/.cursor/mcp.json" && MCP_RESTORE="$MCP_RESTORE cursor"
  trap restore EXIT
  "$UE" uninstall >/dev/null
  snapshot "$WORK_ROOT/before.txt"
fi

for i in $(seq 1 "$RUNS"); do
  RUN_DIR="$WORK_ROOT/run$i"; mkdir -p "$RUN_DIR"; cd "$RUN_DIR"
  echo "==> $MODE ($AGENT / $TASK) run $i/$RUNS in $RUN_DIR"
  python3 "$SUITE" prepare --task "$TASK" --run-dir "$RUN_DIR"
  start=$(python3 -c 'import time; print(time.time())')
  run_agent > "$RUN_DIR/transcript.jsonl" 2>"$RUN_DIR/stderr.txt" || true
  end=$(python3 -c 'import time; print(time.time())')
  python3 "$SUITE" record \
    --mode "$MODE" --agent "$AGENT" --task "$TASK" --run "$i" \
    --start "$start" --end "$end" --run-dir "$RUN_DIR" --results "$RESULTS"
done

python3 "$SUITE" summary --results "$RESULTS" --mode "$MODE" --agent "$AGENT" --task "$TASK"

if [ "$MODE" = baseline ]; then
  snapshot "$WORK_ROOT/after.txt"
  if ! diff -q "$WORK_ROOT/before.txt" "$WORK_ROOT/after.txt" >/dev/null; then
    echo "==> The baseline agent installed packages outside a venv:"
    diff "$WORK_ROOT/before.txt" "$WORK_ROOT/after.txt" | grep '^>' || true
  else
    echo "==> No packages were installed into Homebrew Python, user-site or global npm."
  fi
fi
echo "==> Transcripts kept in $WORK_ROOT"

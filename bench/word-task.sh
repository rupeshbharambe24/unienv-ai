#!/usr/bin/env bash
# Benchmark: how long does Claude Code take to put some text into a Word
# document, before vs after UniEnv?
#
#   bench/word-task.sh unienv   [runs]   # with UniEnv wired in (normal state)
#   bench/word-task.sh baseline [runs]   # temporarily un-wires UniEnv, restores it on exit
#
# Each run starts Claude Code headless (`claude -p`) in a fresh empty folder
# outside any project, with a normal terminal PATH, and records wall time,
# turns, cost, the shell commands Claude ran, and whether report.docx is
# correct. Results are appended to bench/results.jsonl.
#
# Baseline mode runs `unienv uninstall` (wiring only; ~/.unienv is kept) and
# `unienv sync` afterwards. It also snapshots Homebrew Python, user-site and
# global npm packages, and reports anything the baseline agent installed there.
set -euo pipefail

MODE="${1:?usage: word-task.sh unienv|baseline [runs]}"
RUNS="${2:-3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$HERE/results.jsonl"
UE="${UNIENV_HOME:-$HOME/.unienv}/bin/unienv"
WORK_ROOT="$(mktemp -d /private/tmp/ue_bench.XXXXXX)"

PROMPT='Put the following text into a Word document named report.docx in the current folder. Use the first line as the title (Heading 1), "Highlights" as a Heading 2, and the three highlight lines as a bulleted list.

Quarterly Update: Team Atlas
This quarter the team shipped the new onboarding flow, reduced page load time by 38%, and closed 142 customer tickets.
Highlights
Onboarding completion rose from 61% to 79%.
Average support response time fell to 3.2 hours.
Two new enterprise customers signed annual contracts.'

# A "normal terminal" environment: Homebrew + system PATH, nothing from UniEnv.
clean_env() {
  env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" SHELL="${SHELL:-/bin/zsh}" TERM=xterm-256color \
    LANG="${LANG:-en_US.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
    PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" "$@"
}

snapshot() {  # packages an agent might install outside a venv
  {
    echo "## brew-python"; /opt/homebrew/bin/python3 -m pip list --format=freeze 2>/dev/null | sort
    echo "## user-site"; ls "$(/opt/homebrew/bin/python3 -m site --user-site 2>/dev/null)" 2>/dev/null | sort
    echo "## npm-global"; npm ls -g --depth=0 --parseable 2>/dev/null | sort
  } > "$1"
}

restore() {
  if [ "$MODE" = baseline ]; then
    echo "==> Restoring UniEnv wiring"
    "$UE" sync >/dev/null && "$UE" doctor | tail -1
  fi
}

if [ "$MODE" = baseline ]; then
  echo "==> Temporarily un-wiring UniEnv (restored automatically)"
  trap restore EXIT
  "$UE" uninstall >/dev/null
  snapshot "$WORK_ROOT/before.txt"
fi

for i in $(seq 1 "$RUNS"); do
  RUN_DIR="$WORK_ROOT/run$i"; mkdir -p "$RUN_DIR"; cd "$RUN_DIR"
  echo "==> $MODE run $i/$RUNS in $RUN_DIR"
  start=$(python3 -c 'import time; print(time.time())')
  clean_env claude -p "$PROMPT" --output-format stream-json --verbose \
    --permission-mode acceptEdits --allowedTools "Bash Read Write Edit Glob Grep Skill" \
    > "$RUN_DIR/transcript.jsonl" 2>"$RUN_DIR/stderr.txt" || true
  end=$(python3 -c 'import time; print(time.time())')
  python3 - "$MODE" "$i" "$start" "$end" "$RUN_DIR" "$RESULTS" <<'PYEOF'
import json, sys, zipfile, re, pathlib
mode, i, start, end, run_dir, results = sys.argv[1:7]
run = pathlib.Path(run_dir)
result, cmds = {}, []
for line in (run / "transcript.jsonl").read_text().splitlines():
    try: ev = json.loads(line)
    except json.JSONDecodeError: continue
    if ev.get("type") == "result": result = ev
    if ev.get("type") == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmds.append(block["input"].get("command", ""))
docx = run / "report.docx"
ok = False
if docx.exists():
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode()
    ok = all(s in xml for s in ("Quarterly Update", "Highlights", "Two new enterprise customers"))
joined = "\n".join(cmds)
row = {
    "mode": mode, "run": int(i), "wall_s": round(float(end) - float(start), 1),
    "turns": result.get("num_turns"), "cost_usd": round(result.get("total_cost_usd") or 0, 4),
    "bash_commands": len(cmds),
    "created_venv": bool(re.search(r"\b(venv|virtualenv)\b", joined)),
    "pip_or_npm_install": bool(re.search(r"(pip3?|uv pip|npm) (install|i )|uv add|--with ", joined)),
    "used_ue_py": "ue-py" in joined, "docx_correct": ok,
    "model": next(iter(result.get("modelUsage", {}) or {}), None),
}
with open(results, "a") as f: f.write(json.dumps(row) + "\n")
print("   ", json.dumps(row))
PYEOF
done

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

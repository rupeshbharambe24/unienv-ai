# Benchmarks

## Word-document task: before vs after UniEnv

**Task** (same prompt every run): *"Put the following text into a Word document named report.docx … first line as Heading 1, 'Highlights' as Heading 2, three bullets."*

**Setup:** Claude Code 2.1.287 headless (`claude -p`), model `claude-opus-5-5`, macOS on Apple Silicon. Each run starts in a fresh empty folder outside any project, with a normal terminal PATH (Homebrew + system). **Baseline** is the same machine with UniEnv's wiring removed (`unienv uninstall`): no `ue-py` on PATH, no CLAUDE.md block, no skill. System tools installed by Homebrew (pandoc etc.) were *still present* in the baseline, so it is slightly kinder to the baseline than a truly fresh Mac. Script: [`word-task.sh`](word-task.sh), raw data: [`results.jsonl`](results.jsonl).

| | Without UniEnv | With UniEnv |
|---|---|---|
| Wall time per run | 21.9 s · 22.5 s · 153.8 s* | **8.4 s · 10.7 s** · 142.8 s* |
| Typical run (excluding the * runs) | **~22 s** | **~9.6 s (2.3× faster)** |
| Agent turns (typical) | 4–5 | **2** |
| Cost per run (typical) | $0.184 | **$0.158** |
| Temp venv created | 1 of 3 runs | **0** |
| Packages installed during the task | **3 of 3 runs** (pip into temp venv, npm into temp dirs) | **0** |
| Used the shared env (`ue-py`) | 0 | 3 of 3 |
| Correct `report.docx` | 3/3 | 3/3 |

What the agent did **without** UniEnv:
- Run 1: loaded the docx skill → `Cannot find module 'docx'` → installed docx with npm, retried.
- Run 2: `import docx` failed → `python3 -m venv /tmp/docxenv && pip install python-docx` → retried.
- Run 3: node script hung (see *) → retries with timeouts → `npm install docx` into a temp folder.

**With** UniEnv, the typical run was one command: `ue-py - <<EOF … from docx import Document …` → done.

\* **One run in each group hit the same unrelated 2-minute hang.** The agent's first command started with `ls`, and on this machine `ls` is an alias for `eza`. Claude Code replays your shell aliases, and eza ≥ 0.23 waits on stdin when it isn't a terminal, so the command blocked until the 120 s tool timeout. This affects any agent, with or without UniEnv. `unienv doctor` now warns about such aliases (see [troubleshooting](../docs/troubleshooting.md)).

**Caveats:** n = 3 per group on one machine. This is a smoke-level benchmark, not a statistical study. Run it yourself and send a PR with your `results.jsonl`:

```sh
bench/word-task.sh unienv 5
bench/word-task.sh baseline 5   # temporarily un-wires UniEnv; restores it on exit
```

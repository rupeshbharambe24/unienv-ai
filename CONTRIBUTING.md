# Contributing to UniEnv AI

Thanks for helping make AI tools stop reinstalling the world. The most useful contributions, roughly in order:

1. **A new battery (recipe)**: a TOML file that gives agents a capability (e.g. `browser`, `latex`, `geo`, `audio`).
2. **An agent adapter**: teach `unienv sync` where another AI tool reads its global instructions or skills.
3. **A platform backend**: apt/dnf/pacman, winget/Scoop.
4. **A bug report with `unienv doctor --json` output**: the doctor output is the bug-report template.

## Development setup

```sh
git clone https://github.com/rupeshbharambe24/unienv-ai.git && cd unienv-ai
./install.sh --dev          # ~/.unienv/share links to your checkout, so edits apply immediately
unienv doctor
tests/smoke.sh
```

To experiment without touching your real dotfiles, use a throwaway home:

```sh
mkdir -p /tmp/uehome/.claude
HOME=/tmp/uehome ./install.sh --dev --no-system
```

The CLI (`src/unienv.py`) uses **only the Python standard library** (3.11+). Keep it that way: the CLI must keep working even when the environment it manages is broken.

## Writing a recipe

Recipes declare **what** to install, never **how**: there are no install scripts. That keeps them reviewable and safe. Create `recipes/<id>.toml`:

```toml
schema = "unienv/recipe/v0"
id = "latex"                          # must match the file name
summary = "Compile LaTeX documents to PDF"
default = false                       # opt-in unless almost everyone needs it
maintainers = ["github:your-handle"]
license_note = "TeX Live: various free licenses"

[python]                              # optional: PyPI packages for ue-py
packages = ["pylatexenc"]
imports = ["pylatexenc"]              # modules `doctor` imports to verify

[node]                                # optional: npm packages for ue-node
packages = []
modules = []

[system.macos]                        # Homebrew
brew = ["tectonic"]
cask = []

[system.debian]                       # apt
apt = ["tectonic"]

[verify]
bins = ["tectonic"]                   # commands `doctor` must find

[agent]                               # rendered into the unienv skill for every agent
notes = """
- Compile LaTeX: `tectonic paper.tex` (downloads packages on first use, then caches them).
"""
```

Checklist for a recipe PR:

- [ ] `id` matches the file name and the `summary` says what agents can now do.
- [ ] Packages are widely used and maintained. Pin a minimum version only when an older one is known to break.
- [ ] `[verify]` lists the binaries, and `[python].imports` the modules, that prove the battery works.
- [ ] `[agent].notes` gives **exact commands** an agent can copy, not prose. Mention pitfalls (like `soffice-safe` over `soffice`).
- [ ] Large downloads (>100 MB) are `default = false`, and the size is noted in the summary.
- [ ] `unienv install --add <id>` and `unienv doctor` pass on your machine. Say which OS in the PR.
- [ ] Upstream licenses are in `license_note`.

## Writing an agent adapter

Adapters live in `agent_targets()` in `src/unienv.py`. Each needs:

- how to **detect** the tool (a config dir and/or a binary name);
- the **global instructions file** it reads (with a link to the vendor's docs proving the path);
- the **skills directory**, if it supports `SKILL.md`.

Please link the official documentation for every path in your PR. Unverified paths are the main way adapters rot.

## Pull requests

- Keep PRs focused: one recipe or one feature.
- Run `python3 -m py_compile src/unienv.py` and `tests/smoke.sh` before pushing; CI runs them on macOS and Linux.
- Add a line to the README tables if you add a battery or adapter.

## Code of conduct

This project follows the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Be kind and assume good intent.

## License

By contributing, you agree your contributions are dual-licensed under MIT OR Apache-2.0.

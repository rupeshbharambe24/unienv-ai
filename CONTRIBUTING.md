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

## Running the checks

CI runs these on every PR. Run them locally before pushing:

```sh
python3 -m unittest discover -s tests/unit -v        # unit tests (stdlib only, no install needed)
pip install ruff==0.17.5                             # the version CI pins
ruff check src tests && ruff format --check src tests
shellcheck install.sh shims/* mcpb/server/*.sh scripts/*.sh bench/*.sh tests/*.sh
pip install jsonschema && python3 scripts/validate_recipes.py   # recipes match schema/recipe.schema.json
tests/smoke.sh                                       # needs an installed UniEnv
python3 tests/mcp_test.py                            # end-to-end MCP server test, needs an installed UniEnv
```

If `ruff format --check` fails, run `ruff format src tests` and commit the result.

Unit tests live in `tests/unit/` and use only `unittest`. They import `src/unienv.py` directly and work in temp folders (patching `HOME` and other paths where needed), so they never touch your real dotfiles. A bug fix should come with a test that fails before the fix.

## Writing a recipe

Recipes declare **what** to install, never **how**: there are no install scripts. That keeps them reviewable and safe. Create `recipes/<id>.toml`:

```toml
#:schema ../schema/recipe.schema.json
# ^ editor autocomplete + validation (Taplo / Even Better TOML); keep it on its own line
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

The format is described by [`schema/recipe.schema.json`](schema/recipe.schema.json). The `#:schema` line on top makes editors with [Taplo](https://taplo.tamasfe.com/) (VS Code "Even Better TOML", Zed, Neovim) autocomplete keys and flag typos as you type. CI validates every recipe against it; run the same check locally with:

```sh
pip install jsonschema && python3 scripts/validate_recipes.py
```

The schema only allows the platforms the CLI supports under `[system]` (`macos`, `debian`). A PR that adds a platform backend (e.g. `[system.fedora]`) must add it to the schema too.

Checklist for a recipe PR:

- [ ] `python3 scripts/validate_recipes.py` passes.
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

- **Claim the issue first.** Comment "I'd like to work on this" so two people don't do the same work.
- Keep PRs focused: one recipe, one fix or one feature. Put `Closes #<issue>` in the description.
- Run [the checks](#running-the-checks) before pushing. CI runs them all, with install and smoke tests on macOS and Linux.
- **First-time contributors:** GitHub holds your CI run until a maintainer approves it. That's normal, so just leave a comment if it's waiting.
- Add a line to the README tables if you add a battery or adapter, and a line under `[Unreleased]` in `CHANGELOG.md` for anything user-facing.
- Found a security problem? Don't open a public issue or PR; see [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Be kind and assume good intent.

## License

By contributing, you agree your contributions are dual-licensed under MIT OR Apache-2.0.

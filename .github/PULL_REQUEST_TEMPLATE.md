## What does this change?

<!-- One or two sentences. Link the issue it closes: "Closes #12" -->

## Type

- [ ] New battery (recipe)
- [ ] Agent adapter
- [ ] Platform backend (apt/dnf/winget/...)
- [ ] Bug fix
- [ ] Docs
- [ ] Other

## Checklist

- [ ] The CLI still uses only the Python standard library
- [ ] `python3 -m unittest discover -s tests/unit` passes (bug fixes: with a test that fails before the fix)
- [ ] `ruff check src tests` and `ruff format --check src tests` pass (ruff 0.17.5)
- [ ] For recipes: `python3 scripts/validate_recipes.py` passes
- [ ] `unienv doctor` and `tests/smoke.sh` pass on my machine (OS: ______)
- [ ] For recipes: exact agent commands in `[agent].notes`, `license_note` filled in, big downloads are `default = false`
- [ ] For adapters: links to official docs for every path
- [ ] README tables / CHANGELOG `[Unreleased]` updated if user-facing

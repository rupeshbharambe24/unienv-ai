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

- [ ] `python3 -m py_compile src/unienv.py` passes, and the CLI still uses only the standard library
- [ ] `unienv doctor` passes on my machine (OS: ______)
- [ ] `tests/smoke.sh` passes (or the new battery adds its own check)
- [ ] For recipes: exact agent commands in `[agent].notes`, `license_note` filled in, big downloads are `default = false`
- [ ] For adapters: links to official docs for every path
- [ ] README tables / CHANGELOG updated if user-facing

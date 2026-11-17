#!/usr/bin/env python3
"""Validate every recipes/*.toml against schema/recipe.schema.json.

Needs the `jsonschema` package (CI installs it). Also runs the CLI's own
validate_recipe() so the two sets of rules cannot drift apart silently.
"""

import json
import sys
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import unienv  # noqa: E402


def main() -> int:
    schema = json.loads((ROOT / "schema" / "recipe.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    failed = 0
    files = sorted((ROOT / "recipes").glob("*.toml"))
    if not files:
        print("no recipes found", file=sys.stderr)
        return 1
    for path in files:
        with path.open("rb") as f:
            recipe = tomllib.load(f)
        problems = [
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(recipe)
        ] + unienv.validate_recipe(recipe, path.stem)
        if problems:
            failed += 1
            print(f"FAIL {path.relative_to(ROOT)}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"ok   {path.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

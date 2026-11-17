#!/usr/bin/env python3
"""Regression test for `unienv install --add <typo>` (issue #19).

Loads unienv.py with a temporary HOME/UNIENV_HOME and stubbed install steps,
then checks that an unknown --add battery is rejected *before* state is saved:
the command must exit non-zero with a 'did you mean' hint and leave state.json
untouched. Exit code = number of failures.

    python3 tests/add_battery_test.py
"""

import argparse
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="ue_add_test."))
HOME = WORK / "home"
UE_HOME = WORK / "unienv"
HOME.mkdir()
UE_HOME.mkdir()

failures = 0


def check(label, cond):
    global failures
    print(f"  {'PASS' if cond else 'FAIL'} {label}")
    failures += not cond


def load_module():
    for key in (
        "HOME",
        "UNIENV_HOME",
        "STATE_FILE",
        "RECIPES_DIR",
        "MANIFEST_FILE",
        "ENV_SH",
        "LOG_FILE",
        "BACKUP_DIR",
    ):
        os.environ.pop(key, None)
    os.environ["HOME"] = str(HOME)
    os.environ["UNIENV_HOME"] = str(UE_HOME)
    spec = importlib.util.spec_from_file_location("unienv", REPO / "src" / "unienv.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["unienv"] = mod
    spec.loader.exec_module(mod)
    return mod


def stub(mod):
    # Neutralise every side-effecting step so we isolate validation + save.
    mod.install_system = lambda *a, **k: []
    mod.install_python = lambda *a, **k: None
    mod.install_node = lambda *a, **k: None
    mod.do_sync = lambda *a, **k: None
    mod.cmd_doctor = lambda *a, **k: 0
    mod.log_event = lambda *a, **k: None


import os  # noqa: E402  (env setup above must precede module load)

ue = load_module()
stub(ue)
state_file = UE_HOME / "state.json"
original_state = {"version": ue.VERSION, "batteries": ["media"], "extras": {}, "options": {}}
state_file.write_text(json.dumps(original_state))

args = argparse.Namespace(
    batteries=None,
    add="medai",
    exclude=None,
    yes=True,
    no_system=True,
    no_wire=True,
    all_agents=False,
)

err = io.StringIO()
real_stderr = sys.stderr
raised = False
exit_code = None
sys.stderr = err
try:
    try:
        ue.cmd_install(args)
    except SystemExit as e:
        raised = True
        exit_code = e.code
finally:
    sys.stderr = real_stderr
exit_msg = err.getvalue()

check("unknown --add battery exits non-zero", raised and exit_code != 0)
check("error names the typo", "medai" in exit_msg)
check("error suggests the close match", "did you mean media" in exit_msg)
check("state.json is left untouched", state_file.read_text() == json.dumps(original_state))

shutil.rmtree(WORK, ignore_errors=True)
print(f"\n{failures} failure(s)")
sys.exit(failures)

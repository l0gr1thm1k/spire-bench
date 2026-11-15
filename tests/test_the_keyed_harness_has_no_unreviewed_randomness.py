"""M3: every call in the keyed build that could decide a game without the key has a reviewed reason.

harness/audit_keyed.sh lists each call to the clock, Guid, a hash, a generator
other than the keyed one, or the seed. A guarded call throws under the key. Every
unguarded one must be in harness/keyed_audit_allowlist.json with why it can't
reach play, so a new call fails here until someone reads it, and a call that
went away fails until its reason is dropped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import GAME_VERSION

from spirebench.worker.headless import REPO, dotnet_binary, keyed_dir

ALLOWLIST = REPO / "harness" / "keyed_audit_allowlist.json"

pytestmark = pytest.mark.skipif(
    not (keyed_dir(GAME_VERSION) / "Sts2Headless.dll").exists()
    or not (Path(dotnet_binary()).exists() or shutil.which("dotnet")),
    reason=f"needs the keyed build for {GAME_VERSION} and dotnet")


def _audit() -> list[dict]:
    dotnet = Path(dotnet_binary()).parent
    env = {**os.environ, "DOTNET_ROOT": str(dotnet), "PATH": f"{dotnet}:{os.environ.get('PATH', '')}"}
    out = subprocess.run(["bash", str(REPO / "harness" / "audit_keyed.sh"), GAME_VERSION],
                         capture_output=True, text=True, timeout=600, check=True, env=env)
    return [json.loads(line) for line in out.stdout.splitlines()]


def test_every_unguarded_call_has_a_reviewed_reason():
    reviewed = json.loads(ALLOWLIST.read_text())
    assert GAME_VERSION in reviewed, f"no reviewed audit for {GAME_VERSION}: run harness/audit_keyed.sh and review it"
    rows = _audit()
    assert rows, "the audit found nothing, so it read the wrong files"
    unguarded = {f"{r['assembly']}|{r['method']}|{r['source']}" for r in rows if not r["guarded"]}
    allowed = reviewed[GAME_VERSION]
    assert sorted(unguarded - allowed.keys()) == [], "new unguarded calls: review each and give it a reason"
    assert sorted(allowed.keys() - unguarded) == [], "reviewed calls that are gone (or now guarded): drop them"
    assert all(why.strip() for why in allowed.values())

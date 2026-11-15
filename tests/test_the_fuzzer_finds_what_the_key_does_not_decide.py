"""M3: tools/fuzz_keyed.py passes the keyed build, and fails a game the key does not decide.

A short fuzz of the real build must pass; the same checks handed one key for
every game (so nothing the key decides can vary) must report leaks, so a pass
is not the checks seeing nothing.
"""

from __future__ import annotations

import importlib.util
import secrets

import pytest
from conftest import GAME_VERSION

from spirebench.worker.headless import REPO, harness_dll, keyed_dir

spec = importlib.util.spec_from_file_location("fuzz_keyed", REPO / "tools" / "fuzz_keyed.py")
assert spec and spec.loader
fuzz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fuzz)

pytestmark = pytest.mark.skipif(
    not (keyed_dir(GAME_VERSION) / "Sts2Headless.dll").exists() or not harness_dll(GAME_VERSION).exists(),
    reason=f"needs the keyed and upstream builds for {GAME_VERSION}")

KEYS = [secrets.token_bytes(32) for _ in range(10)]


def test_the_same_key_and_moves_show_the_same_game_in_two_processes():
    assert fuzz.check_determinism(GAME_VERSION, KEYS[:3], 60) == []


def test_no_field_the_seed_varies_is_fixed_under_every_key():
    assert fuzz.check_leaks(GAME_VERSION, KEYS, 40) == []


def test_one_key_for_every_game_is_reported_as_a_leak():
    assert fuzz.check_leaks(GAME_VERSION, [KEYS[0]] * len(KEYS), 40) != []


@pytest.mark.skipif(not (keyed_dir(GAME_VERSION, trace=True) / "Sts2Headless.dll").exists(),
                    reason=f"needs the trace build: harness/build_keyed.sh {GAME_VERSION} --trace-streams")
def test_a_one_bit_key_change_changes_every_stream():
    failures, _ = fuzz.check_streams(GAME_VERSION, KEYS[:3], 60)
    assert failures == []


@pytest.mark.skipif(not (keyed_dir(GAME_VERSION, deep=True) / "Sts2Headless.dll").exists(),
                    reason=f"needs the fuzz-deep build: harness/build_keyed.sh {GAME_VERSION} --fuzz-deep")
def test_the_deep_fuzz_plays_to_the_end_of_the_run_and_passes():
    assert fuzz.check_reach(GAME_VERSION, KEYS[:2], 3000) == []
    assert fuzz.check_determinism(GAME_VERSION, KEYS[:1], 3000, deep=True) == []
    failures, _ = fuzz.check_streams(GAME_VERSION, KEYS[:1], 3000, deep=True)
    assert failures == []

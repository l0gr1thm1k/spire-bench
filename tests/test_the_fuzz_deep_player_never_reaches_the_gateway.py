"""M3: the fuzz-deep build's undying player (build_keyed.sh --fuzz-deep) is never in a build a bot plays.

FuzzOnly lives in KeyedRng.dll, which every keyed build ships; only the fuzz-deep
builds' sts2.dll may call it. The deep build is the positive control, so a pass
is not the byte scan missing the reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GAME_VERSION

from spirebench.worker.headless import keyed_dir, seeded_deep_dll

GATEWAY = keyed_dir(GAME_VERSION)
DEEP = keyed_dir(GAME_VERSION, deep=True)


def calls_fuzz_only(build: Path) -> bool:
    return any(b"SpireBench.Keyed" in (data := path.read_bytes()) and b"FuzzOnly" in data
               for path in (build / "lib" / "sts2.dll", build / "Sts2Headless.dll"))


@pytest.mark.skipif(not (GATEWAY / "Sts2Headless.dll").exists(), reason=f"needs the keyed build for {GAME_VERSION}")
def test_the_gateways_keyed_build_never_calls_the_fuzz_only_player():
    assert not calls_fuzz_only(GATEWAY)
    for name in ("keyed-trace", "keyed-plant"):
        if (GATEWAY.parent.parent / name / GAME_VERSION / "Sts2Headless.dll").exists():
            assert not calls_fuzz_only(GATEWAY.parent.parent / name / GAME_VERSION)


@pytest.mark.skipif(not (DEEP / "Sts2Headless.dll").exists(),
                    reason=f"needs the fuzz-deep build: harness/build_keyed.sh {GAME_VERSION} --fuzz-deep")
def test_the_fuzz_deep_builds_do_call_it():
    assert calls_fuzz_only(DEEP)
    assert calls_fuzz_only(seeded_deep_dll(GAME_VERSION).parent)

"""M3: tools/distribution_check.py passes the keyed build, and fails a deal the real game would not make.

The statistics are checked on made-up samples: one distribution passes, and a
shuffle that always deals the marked card first fails. A short run of the real
builds must measure the deal and pass; the full check is 10,000 games per arm.
"""

from __future__ import annotations

import importlib.util
import random

import pytest
from conftest import GAME_VERSION

from spirebench.worker.headless import REPO, harness_dll, keyed_dir

spec = importlib.util.spec_from_file_location("distribution_check", REPO / "tools" / "distribution_check.py")
assert spec and spec.loader
dist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dist)

RARITIES = ["Common"] * 6 + ["Uncommon"] * 3 + ["Rare"]


def made_up(rng: random.Random, n: int, first: bool = False) -> list[dict[str, str]]:
    return [{"shuffle": f"deck 11 position {0 if first else rng.randrange(11)}",
             "rarity": ",".join(sorted(rng.choice(RARITIES) for _ in range(3)))} for _ in range(n)]


def test_two_arms_from_one_distribution_pass():
    rng = random.Random(1)
    failures, results = dist.compare(made_up(rng, 2000), made_up(rng, 2000))
    assert failures == []
    assert sorted(r.measure for r in results if r.p is not None) == ["rarity", "shuffle"]


def test_a_shuffle_that_deals_the_marked_card_first_fails():
    rng = random.Random(2)
    keyed = made_up(rng, 2000, first=True)
    failures, _ = dist.compare(keyed, made_up(rng, 2000))
    assert [f.split(":")[0] for f in failures] == ["shuffle"]


def test_too_few_samples_are_skipped_not_passed():
    rng = random.Random(3)
    _, results = dist.compare(made_up(rng, 10), made_up(rng, 10))
    assert all(r.p is None and r.note.startswith("skipped") for r in results)


@pytest.mark.skipif(
    not (keyed_dir(GAME_VERSION) / "Sts2Headless.dll").exists() or not harness_dll(GAME_VERSION).exists(),
    reason=f"needs the keyed and upstream builds for {GAME_VERSION}")
def test_a_short_run_of_the_real_builds_measures_the_deal_and_passes():
    keyed, seeded = dist.run(GAME_VERSION, 40, 8, 200)
    assert sum("shuffle" in s for s in keyed) >= 35 and sum("shuffle" in s for s in seeded) >= 35
    assert any(name.startswith("intent ") for s in keyed for name in s)
    failures, results = dist.compare(keyed, seeded, alpha=1e-3, min_cell=5, min_samples=20)
    assert failures == []
    assert "shuffle" in [r.measure for r in results if r.p is not None]

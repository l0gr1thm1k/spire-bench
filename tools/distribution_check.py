"""Check that the keyed game deals what the real game deals, in distribution.

  uv run python tools/distribution_check.py --version v0.111.0 --games 10000 --jobs 12

Random players play --games games under random keys in the keyed build and as
many under random seed strings in the upstream build. Each game gives one sample
per measure, so the samples are independent:

  shuffle             where Bash (the one marked card) lands in the first
                      combat's draw order, by deck size
  rarity              the rarities on the first card reward screen
  intent ENC round R  the moves the enemies of encounter ENC (its enemies'
                      names) intend in round R (1 or 2), the first time the
                      game meets ENC

Per measure, a chi-square test of homogeneity on the 2 x K table of counts;
categories with fewer than --min-cell pooled samples are merged, and a measure
with under --min-samples samples in either arm, or a single category, is
skipped and listed. The keyed game passes when every p > alpha / (tests run):
alpha (default 0.01) is family-wise, by Bonferroni, as in fuzz_keyed.py.

Measurements come from the harness's own view (draw order, enemy moves), which
the bot never sees. Exit status 1 if a test fails.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sys
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

from scipy.stats import chi2_contingency  # type: ignore[import-untyped]

from spirebench.worker.headless import harness_dll, keyed_dir

_spec = importlib.util.spec_from_file_location("fuzz_keyed", Path(__file__).with_name("fuzz_keyed.py"))
assert _spec and _spec.loader
fuzz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fuzz)

View = dict[str, Any]
Sample = dict[str, str]
MARKED = "CARD.BASH"
INTENT_ROUNDS = (1, 2)


def first_combat_views(views: list[View]) -> dict[tuple[int, int], View]:
    """The first view of each (floor, round) of combat."""
    firsts: dict[tuple[int, int], View] = {}
    for view in views:
        if view.get("decision") == "combat_play":
            firsts.setdefault((view["context"]["total_floor"], view["round"]), view)
    return firsts


def measures(views: list[View]) -> Sample:
    """One category per measure this game reached."""
    sample: Sample = {}
    rounds = first_combat_views(views)
    floors = sorted({floor for floor, _ in rounds})
    if floors and (floors[0], 1) in rounds:
        opening = rounds[(floors[0], 1)]
        order = [card["id"] for card in opening["hand"] + opening["draw_pile"]]
        if order.count(MARKED) == 1:
            sample["shuffle"] = f"deck {len(order)} position {order.index(MARKED)}"
    reward = next((v for v in views if v.get("decision") == "card_reward"), None)
    if reward is not None:
        sample["rarity"] = ",".join(sorted(card["rarity"] for card in reward["cards"]))
    for floor in floors:
        if (floor, 1) not in rounds:
            continue
        encounter = "+".join(sorted(enemy["name"] for enemy in rounds[(floor, 1)]["enemies"]))
        for r in INTENT_ROUNDS:
            name = f"intent {encounter} round {r}"
            if (floor, r) in rounds and name not in sample:
                sample[name] = "|".join(f"{e['name']}:{e['move_state']}" for e in rounds[(floor, r)]["enemies"])
    return sample


class Result(NamedTuple):
    measure: str
    p: float | None
    note: str


def chi_square(keyed: Counter[str], seeded: Counter[str], min_cell: int) -> tuple[float, int]:
    """The homogeneity p-value of two count tables and its degrees of freedom; rare categories merged."""
    pooled = keyed + seeded
    common = sorted(c for c, n in pooled.items() if n >= min_cell)
    rare = [c for c in pooled if c not in common]
    rows = [[arm[c] for c in common] + ([sum(arm[c] for c in rare)] if rare else []) for arm in (keyed, seeded)]
    if len(rows[0]) < 2:
        return 1.0, 0
    _, p, dof, _ = chi2_contingency(rows)
    return float(p), int(dof)


def compare(keyed: list[Sample], seeded: list[Sample], alpha: float = 0.01, min_cell: int = 10,
            min_samples: int = 50) -> tuple[list[str], list[Result]]:
    """The failures, and every measure's result, of the keyed samples against the seeded ones."""
    names = sorted({name for sample in keyed + seeded for name in sample})
    results: list[Result] = []
    for name in names:
        k = Counter(s[name] for s in keyed if name in s)
        s = Counter(x[name] for x in seeded if name in x)
        n_k, n_s = sum(k.values()), sum(s.values())
        if min(n_k, n_s) < min_samples:
            results.append(Result(name, None, f"skipped: {n_k} keyed, {n_s} seeded samples"))
            continue
        p, dof = chi_square(k, s, min_cell)
        note = f"{n_k} keyed, {n_s} seeded, {dof} df"
        results.append(Result(name, p if dof else None, note if dof else f"skipped: one category ({note})"))
    run = [r for r in results if r.p is not None]
    per_test = alpha / max(1, len(run))
    failures = [f"{r.measure}: p = {r.p:.2e} <= {per_test:.2e} ({r.note})"
                for r in run if r.p is not None and r.p <= per_test]
    return failures, results


def play_many(games: int, jobs: int, one: Callable[[int], list[View]]) -> list[Sample]:
    with ThreadPoolExecutor(jobs) as pool:
        return list(pool.map(lambda i: measures(one(i)), range(games)))


def run(version: str, games: int, jobs: int, max_moves: int) -> tuple[list[Sample], list[Sample]]:
    """``games`` random games each under random keys and under random seeds; samples only, views dropped."""
    keyed = play_many(games, jobs, lambda i: fuzz.keyed_game(version, secrets.token_bytes(32), i, max_moves)[1])
    seeded = play_many(games, jobs, lambda i: fuzz.seeded_game(version, secrets.token_hex(5).upper(), i, max_moves))
    return keyed, seeded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default=os.environ.get("SPIREBENCH_GAME_VERSION", "v0.107.1"))
    ap.add_argument("--games", type=int, default=10000, help="games per arm")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--max-moves", type=int, default=200)
    ap.add_argument("--alpha", type=float, default=0.01, help="family-wise")
    ap.add_argument("--out", type=Path, help="write the samples here as JSON")
    a = ap.parse_args()
    for build in (keyed_dir(a.version) / "Sts2Headless.dll", harness_dll(a.version)):
        if not build.exists():
            print(f"FAIL no build at {build}")
            return 1
    keyed, seeded = run(a.version, a.games, a.jobs, a.max_moves)
    if a.out:
        a.out.write_text(json.dumps({"version": a.version, "keyed": keyed, "seeded": seeded}))
    failures, results = compare(keyed, seeded, a.alpha)
    for r in sorted(results, key=lambda r: (r.p is None, r.p or 0.0)):
        print(f"  {'skip' if r.p is None else f'p {r.p:.3f}'}  {r.measure}  ({r.note})")
    tested = sum(r.p is not None for r in results)
    print(f"{tested} tests run, {len(results) - tested} skipped, family-wise alpha {a.alpha}")
    for failure in failures:
        print(f"FAIL {failure}")
    print("ok" if not failures and tested else "no test ran" if not tested else f"{len(failures)} failures")
    return 0 if not failures and tested else 1


if __name__ == "__main__":
    sys.exit(main())

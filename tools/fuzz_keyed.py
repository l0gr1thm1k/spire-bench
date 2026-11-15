"""Fuzz the keyed harness for randomness the episode key does not decide.

  uv run python tools/fuzz_keyed.py --version v0.107.1 --keys 8

harness/audit_keyed.sh reads the code; this plays it. Random players play
games under random keys and check three things:

  determinism  The same key and moves, in two fresh processes, show the bot the
               same thing at every decision. A difference is randomness the key
               does not decide (the clock, a hash order, a thread race).
  streams      (the trace build, harness/build_keyed.sh VERSION --trace-streams)
               Flipping one bit of the key changes the first draw of every stream
               the game opens. Streams two generators share (same seed, so the
               same draws) are listed: the game shares them too, so they are a
               report, not a failure.
  leaks        A field the seed changes in the upstream build must be one the key
               changes in the keyed build. A field that varies across seeds but is
               the same under every key is drawn from something public.

Exit status 1 if determinism, streams or leaks fail.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from spirebench.gateway.observe import observation
from spirebench.gateway.replay_core import view_digest
from spirebench.worker.headless import (Choice, HeadlessGame, harness_dll, keyed_dir, legal_choices)

CHARACTER, ASCENSION = "Ironclad", 10
View = dict[str, Any]


def play(game: HeadlessGame, start: Callable[[], View], policy: random.Random, max_moves: int,
         moves: list[Choice] | None = None) -> tuple[list[str], list[View], list[Choice]]:
    """Play from ``start``: the given moves, or random legal ones. Returns the digests, views and moves made."""
    view = start()
    digests, views = [view_digest(view)], [view]
    made: list[Choice] = []
    while len(made) < (len(moves) if moves is not None else max_moves):
        if view.get("type") == "error" or view.get("decision") == "game_over":
            break
        if moves is not None:
            choice = moves[len(made)]
            after = game.act(*choice)
        else:
            choices = legal_choices(game, view)
            after = {"type": "error"}
            while choices and after.get("type") == "error":
                choice = choices.pop(policy.randrange(len(choices)))
                after = game.act(*choice)
            if after.get("type") == "error":
                break
        made.append(choice)
        view = after
        digests.append(view_digest(view))
        views.append(view)
    return digests, views, made


def keyed_game(version: str, key: bytes, policy_seed: int, max_moves: int, moves: list[Choice] | None = None,
               trace: bool = False) -> tuple[list[str], list[View], list[Choice], list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as tmp:
        err = Path(tmp) / "stderr"
        game = HeadlessGame(version, trace=trace, stderr_path=err)
        try:
            digests, views, made = play(game, lambda: game.start_run(CHARACTER, ASCENSION, key),
                                        random.Random(policy_seed), max_moves, moves)
        finally:
            game.close()
        opened = [json.loads(line) for line in err.read_text().splitlines() if '"spirebench_trace"' in line]
    return digests, views, made, opened


def seeded_game(version: str, seed: str, policy_seed: int, max_moves: int) -> list[View]:
    game = HeadlessGame(version, keyed=False)
    try:
        return play(game, lambda: game.start_seeded_run(CHARACTER, ASCENSION, seed),
                    random.Random(policy_seed), max_moves)[1]
    finally:
        game.close()


def check_determinism(version: str, keys: list[bytes], max_moves: int) -> list[str]:
    failures = []
    for i, key in enumerate(keys):
        first, _, moves, _ = keyed_game(version, key, i, max_moves)
        again, _, _, _ = keyed_game(version, key, i, max_moves, moves)
        at = next((n for n, (a, b) in enumerate(zip(first, again)) if a != b), None)
        if at is not None or len(first) != len(again):
            where = f"decision {at}" if at is not None else f"length {len(first)} vs {len(again)}"
            failures.append(f"key {key.hex()[:16]}..: the replay differs at {where}")
        print(f"  determinism key {i}: {len(moves)} moves, {'same' if at is None else 'DIFFERS'}")
    return failures


def flip_bit(key: bytes, bit: int) -> bytes:
    out = bytearray(key)
    out[bit // 8] ^= 1 << (bit % 8)
    return bytes(out)


def check_streams(version: str, keys: list[bytes], max_moves: int) -> tuple[list[str], dict[str, int]]:
    """Each stream's first draw under a key and under the key with one bit flipped, played on the same moves."""
    failures: list[str] = []
    bits_changed: list[int] = []
    shared: dict[str, int] = defaultdict(int)
    for i, key in enumerate(keys):
        _, _, moves, opened = keyed_game(version, key, i, max_moves, trace=True)
        if not opened:
            failures.append("the trace build logged no streams: is it the trace build?")
            break
        flipped = flip_bit(key, random.Random(i).randrange(256))
        _, _, _, opened_flipped = keyed_game(version, flipped, i, max_moves, moves, trace=True)
        firsts = {o["seed"]: o["first"] for o in opened}
        for o in opened_flipped:
            if o["seed"] in firsts:
                changed = bin(firsts[o["seed"]] ^ o["first"]).count("1")
                bits_changed.append(changed)
                if changed == 0:
                    failures.append(f"stream {o['seed']} ({o['by'][-1]}) draws the same under two keys")
        by_seed: dict[int, list[str]] = defaultdict(list)
        for o in opened:
            by_seed[o["seed"]].append(o["by"][-1])
        for openers in by_seed.values():
            if len(openers) > 1:
                shared[" + ".join(sorted(set(openers))) + f" x{len(openers)}"] += 1
    if bits_changed:
        mean = sum(bits_changed) / len(bits_changed)
        print(f"  streams: {len(bits_changed)} compared, first draw changed by {mean:.1f}/64 bits on average"
              f" (min {min(bits_changed)}, max {max(bits_changed)})")
        if not 28 <= mean <= 36:
            failures.append(f"a one-bit key change moves the first draws by {mean:.1f}/64 bits, not about 32")
    return failures, dict(shared)


def leaves(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(value, list):
        for n, v in enumerate(value):
            yield from leaves(v, f"{path}[{n}]")
    else:
        yield path, json.dumps(value)


def firsts_by_decision(views: list[View]) -> dict[str, dict[str, str]]:
    """The leaves of the first view of each decision type."""
    out: dict[str, dict[str, str]] = {}
    for view in views:
        out.setdefault(str(view.get("decision")), dict(leaves(observation(view))))
    return out


def values(games: list[dict[str, dict[str, str]]]) -> dict[tuple[str, str], list[str]]:
    """Each (decision, leaf)'s value in every game that showed it."""
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for game in games:
        for decision, fields in game.items():
            for path, value in fields.items():
                out[decision, path].append(value)
    return out


def chance_constant(seeded: list[str], keyed: list[str]) -> float:
    """How likely every keyed game shows its one value, were it drawn as often as the seeded games draw it."""
    share = (seeded.count(keyed[0]) + 1) / (len(seeded) + 2)
    return share ** len(keyed)


def check_leaks(version: str, keys: list[bytes], max_moves: int, alpha: float = 1e-3) -> list[str]:
    """A leaf the seed varies but every key shows the same, where chance would make that rarer than ``alpha``."""
    under_keys = [firsts_by_decision(keyed_game(version, key, i, max_moves)[1]) for i, key in enumerate(keys)]
    under_seeds = [firsts_by_decision(seeded_game(version, secrets.token_hex(5).upper(), i, max_moves))
                   for i in range(len(keys))]
    by_key, by_seed = values(under_keys), values(under_seeds)
    vary = {k: v for k, v in by_seed.items() if len(set(v)) > 1}
    leaked = sorted(k for k, v in vary.items()
                    if k in by_key and len(set(by_key[k])) == 1 and chance_constant(v, by_key[k]) < alpha)
    print(f"  leaks: {len(vary)} fields vary with the seed; {len(leaked)} are fixed under every key past chance")
    return [f"{decision} {path} varies with the seed but not with the key" for decision, path in leaked]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default=os.environ.get("SPIREBENCH_GAME_VERSION", "v0.107.1"))
    ap.add_argument("--keys", type=int, default=6, help="games per check")
    ap.add_argument("--max-moves", type=int, default=200, help="moves per game")
    ap.add_argument("--only", choices=["determinism", "streams", "leaks"], action="append")
    a = ap.parse_args()
    checks = a.only or ["determinism", "streams", "leaks"]
    keys = [secrets.token_bytes(32) for _ in range(a.keys)]
    failures: list[str] = []
    if "determinism" in checks:
        failures += check_determinism(a.version, keys, a.max_moves)
    if "streams" in checks:
        if not (keyed_dir(a.version, trace=True) / "Sts2Headless.dll").exists():
            failures.append(f"no trace build: run harness/build_keyed.sh {a.version} --trace-streams")
        else:
            got, shared = check_streams(a.version, keys, a.max_moves)
            failures += got
            for openers, games in sorted(shared.items()):
                print(f"  shared stream (the game's own seeding, reported only): {openers} in {games} games")
    if "leaks" in checks:
        if not harness_dll(a.version).exists():
            failures.append(f"no upstream build at {harness_dll(a.version)} to compare seeds against")
        else:
            failures += check_leaks(a.version, keys, a.max_moves)
    for failure in failures:
        print(f"FAIL {failure}")
    print("ok" if not failures else f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

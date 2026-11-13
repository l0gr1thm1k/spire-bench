"""M0: play full runs in the headless game with a random player and measure them.

What it measures, per character and overall:
  - actions per run and per floor reached,
  - harness CPU and wall time per action,
  - how runs end (game over, a harness error, a stuck step, the step cap),
  - aggregate actions per second with P harness processes in parallel.

A random player dies early, mostly in act 1, so its actions per run are a floor,
not an estimate for real bots. Actions per floor is the number that carries over.

Usage:
  uv run python tools/m0_measure.py --runs-per-character 20 --procs 16 --out data/m0/run.jsonl
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from spirebench.protocol import CHARACTERS as _CHARACTER_NAMES
from spirebench.worker.headless import (DEFAULT_GAME_VERSION, HarnessError, HeadlessGame, legal_choices, run_act,
                                        run_floor)

CHARACTERS = list(_CHARACTER_NAMES.values())
MAX_STEPS = 20_000
STEP_TIMEOUT_S = 60.0  # the harness is killed if one command takes longer

WARM_AFTER = 200  # actions; CPU after this many is reported as the warm cost

_game_version = DEFAULT_GAME_VERSION
_policy = "random"


def choose(rng: random.Random, choices: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    """random: uniform over legal moves. greedy: in combat, play a card whenever one is playable."""
    if _policy == "greedy":
        plays = [c for c in choices if c[0] == "play_card"]
        if plays:
            return plays[rng.randrange(len(plays))]
    return choices[rng.randrange(len(choices))]


def _init(game_version: str, policy: str) -> None:
    global _game_version, _policy
    _game_version, _policy = game_version, policy


def _floor(view: dict[str, Any]) -> int:
    return run_floor(view) or 0


def _act(view: dict[str, Any]) -> int:
    return run_act(view) or 0


def _play(game: HeadlessGame, view: dict[str, Any], rng: random.Random, row: dict[str, Any]) -> None:
    """Play until the run ends. Fills row with the counts; timing is the caller's."""
    decisions: Counter[str] = Counter()
    row.update(actions=0, rejected=0, max_floor=0, max_act=0, terminal="max_steps", victory=None)
    cpu0 = game.cpu_times().total
    while row["actions"] < MAX_STEPS:
        dec = str(view.get("decision") or view.get("type"))
        row["max_floor"], row["max_act"] = max(row["max_floor"], _floor(view)), max(row["max_act"], _act(view))
        if dec == "game_over":
            row["terminal"], row["victory"] = "game_over", bool(view.get("victory"))
            break
        if view.get("type") == "error":
            row["terminal"], row["error"] = "harness_error", str(view.get("message"))[:300]
            break
        choices = legal_choices(game, view)
        if not choices:
            row["terminal"], row["stuck_decision"] = "no_choices", dec
            break
        after = game.act(*choose(rng, choices))
        if after.get("type") == "error":
            # A move the harness refuses: count it apart and pick again.
            row["rejected"] += 1
            if row["rejected"] > 200:
                row["terminal"], row["error"] = "too_many_rejects", str(after.get("message"))[:300]
                break
            continue
        decisions[dec] += 1
        row["actions"] += 1
        if row["actions"] == WARM_AFTER:
            row["cpu_at_warm_s"] = game.cpu_times().total - cpu0
        view = after
    row["decisions"] = dict(decisions)


def play_one(job: tuple[str, int, str]) -> dict[str, Any]:
    """One run in its own harness process: launch, start, play, close.

    The harness refuses a second start_run in one process ("State is already
    set"), so every run pays for a fresh process, as in spire-mentor's own tools.
    Boot and start_run are timed apart from play, and play timing stops before
    the process is closed.
    """
    character, ascension, seed = job
    row: dict[str, Any] = {"character": character, "ascension": ascension, "seed": seed, "terminal": "boot_failed"}
    wall0 = time.perf_counter()
    game: HeadlessGame | None = None
    try:
        with HeadlessGame(game_version=_game_version, timeout_s=STEP_TIMEOUT_S) as game:
            row["boot_s"], row["boot_cpu_s"] = time.perf_counter() - wall0, game.cpu_times().total
            view = game.start_run(character, ascension, seed)
            row["start_s"] = time.perf_counter() - wall0 - row["boot_s"]
            row["start_cpu_s"] = game.cpu_times().total - row["boot_cpu_s"]
            row["act1"] = (view.get("context") or {}).get("act_name")
            cpu0, play0 = game.cpu_times().total, time.perf_counter()
            _play(game, view, random.Random(f"m0:{seed}"), row)
            row["cpu_s"], row["wall_s"] = game.cpu_times().total - cpu0, time.perf_counter() - play0
            row["peak_rss_mb"] = game.peak_rss_mb()
    except (HarnessError, OSError, ValueError) as exc:
        row["terminal"] = "harness_timeout" if "timed out" in str(exc) else "harness_crash"
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if game is not None:  # read after close: the stderr drain has seen every line
            row["world_errors"] = game.world_errors.count
            row["world_errors_complete"] = game.world_errors.complete
            if game.world_errors.sample:
                row["world_error_sample"] = game.world_errors.sample[:3]
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def summarize(rows: list[dict[str, Any]], elapsed: float, procs: int) -> str:
    lines = []
    ok = [r for r in rows if r["terminal"] == "game_over"]
    lines.append(f"runs {len(rows)}, finished {len(ok)}, procs {procs}, elapsed {elapsed:.1f}s")
    lines.append("ends: " + ", ".join(f"{k} {v}" for k, v in Counter(r["terminal"] for r in rows).items()))
    lines.append("act 1: " + ", ".join(f"{k} {v}" for k, v in Counter(r.get("act1") for r in rows).items()))
    counted = [r for r in rows if r.get("world_errors_complete")]
    errs = [r["world_errors"] for r in counted]
    lines.append(f"game errors on stderr: {sum(errs)} in {sum(e > 0 for e in errs)} of {len(counted)} runs counted "
                 f"({len(rows) - len(counted)} runs not counted: the process never started, or stderr wasn't "
                 "read to its end)")
    hdr = f"{'character':<12}{'runs':>5}{'wins':>5}{'floor':>7}{'act3':>6}{'acts/run':>10}{'p90':>7}" \
          f"{'acts/floor':>11}{'cpu ms/act':>11}{'wall ms/act':>12}"
    lines.append(hdr)
    for ch in CHARACTERS + ["ALL"]:
        rs = [r for r in ok if ch == "ALL" or r["character"] == ch]
        if not rs:
            continue
        acts = [r["actions"] for r in rs]
        per_floor = sum(acts) / max(1, sum(r["max_floor"] for r in rs))
        cpu_ms = 1000 * sum(r["cpu_s"] for r in rs) / max(1, sum(acts))
        wall_ms = 1000 * sum(r["wall_s"] for r in rs) / max(1, sum(acts))
        lines.append(f"{ch:<12}{len(rs):>5}{sum(bool(r['victory']) for r in rs):>5}"
                     f"{statistics.mean(r['max_floor'] for r in rs):>7.1f}"
                     f"{sum(r['max_act'] >= 3 for r in rs):>6}{statistics.mean(acts):>10.0f}{_q(acts, .9):>7.0f}"
                     f"{per_floor:>11.1f}{cpu_ms:>11.2f}{wall_ms:>12.2f}")
    total_actions = sum(r["actions"] for r in rows)
    lines.append(f"aggregate: {total_actions} actions in {elapsed:.1f}s = {total_actions / elapsed:.0f} actions/s "
                 f"with {procs} processes")
    warm = [r for r in ok if "cpu_at_warm_s" in r and r["actions"] > WARM_AFTER]
    if warm:
        w_cpu = sum(r["cpu_s"] - r["cpu_at_warm_s"] for r in warm)
        w_act = sum(r["actions"] - WARM_AFTER for r in warm)
        lines.append(f"warm (after action {WARM_AFTER}): {len(warm)} runs, {w_act} actions, "
                     f"{1000 * w_cpu / w_act:.2f} CPU ms/action")
    if ok:
        boot = [r["boot_cpu_s"] + r["start_cpu_s"] for r in ok]
        boot_wall = [r["boot_s"] + r["start_s"] for r in ok]
        rss = [r["peak_rss_mb"] for r in ok if "peak_rss_mb" in r]
        lines.append(f"peak memory per process: median {statistics.median(rss):.0f} MiB, max {max(rss):.0f} MiB")
        lines.append(f"startup (process boot + start_run): median {statistics.median(boot_wall) * 1000:.0f} ms wall, "
                     f"{statistics.median(boot) * 1000:.0f} ms CPU")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-per-character", type=int, default=20)
    ap.add_argument("--ascension", type=int, default=10)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--seed-prefix", default="M0")
    ap.add_argument("--policy", choices=["random", "greedy"], default="random")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    jobs = [(ch, a.ascension, f"{a.seed_prefix}{ch[:2].upper()}{i:04d}")
            for i in range(a.runs_per_character) for ch in CHARACTERS]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.unlink(missing_ok=True)  # a stale file reads exactly like a result
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(a.procs, initializer=_init, initargs=(a.game_version, a.policy)) as pool, \
            a.out.open("w") as f:
        for row in pool.imap_unordered(play_one, jobs):
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            if len(rows) % 10 == 0:
                print(f"  {len(rows)}/{len(jobs)} runs", file=sys.stderr, flush=True)
    elapsed = time.perf_counter() - t0
    report = f"policy {a.policy}, ascension {a.ascension}\n" + summarize(rows, elapsed, a.procs)
    print(report)
    a.out.with_suffix(".summary.txt").write_text(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

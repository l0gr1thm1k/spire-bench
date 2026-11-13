"""Two harness processes given the same seed and the same moves reach the same end.

This runs the real game, so it is skipped where the harness hasn't been built.
"""

import random

import pytest

from spirebench.worker.headless import HeadlessGame, harness_dll, legal_choices

pytestmark = pytest.mark.skipif(not harness_dll().exists(), reason="Sts2Headless not built here")


def _play(seed: str, move_seed: int) -> tuple:
    rng = random.Random(move_seed)
    with HeadlessGame() as game:
        view = game.start_run("Ironclad", 10, seed)
        trace = []
        for _ in range(5000):
            if view.get("decision") == "game_over":
                break
            verb, args = rng.choice(legal_choices(game, view))
            trace.append((verb, tuple(sorted(args.items()))))
            after = game.act(verb, args)
            if after.get("type") != "error":
                view = after
        # A loop of rejected moves would repeat identically too, so the game must really end.
        assert view.get("decision") == "game_over", "the random game never ended"
        return tuple(trace), view.get("floor"), (view.get("player") or {}).get("hp")


def test_a_seeded_game_replays_to_the_same_end():
    assert _play("REPLAY1", 7) == _play("REPLAY1", 7)


def test_a_different_seed_gives_a_different_game():
    # Shown to fail: if the seed were ignored, these two would match.
    assert _play("REPLAY1", 7) != _play("REPLAY2", 7)


def test_a_harness_that_does_not_answer_in_time_is_killed():
    from spirebench.worker.headless import HarnessError

    with pytest.raises(HarnessError, match="timed out"):
        HeadlessGame(timeout_s=0.05)


def _act_one(seed: str) -> str:
    with HeadlessGame() as game:
        view = game.start_run("Ironclad", 10, seed)
    assert view.get("type") != "error", view
    return str((view.get("context") or {}).get("act_name"))


def test_the_seed_picks_act_one():
    # Shown to fail: without "act1": "roll" the harness starts every run in Overgrowth.
    acts = [_act_one(f"ACTROLL{i}") for i in range(12)]
    assert set(acts) == {"Overgrowth", "Underdocks"}, acts
    assert _act_one("ACTROLL0") == acts[0]  # the same seed picks the same act

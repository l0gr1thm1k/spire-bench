"""M1's done criterion: a random-bot A10 game reaches game_over through the library, and its transcript is saved.

This runs the real game, so it is skipped where the harness hasn't been built.
"""

import subprocess
from pathlib import Path

import jsonschema
import pytest

from spirebench import Client, RandomAgent
from spirebench.gateway.store import read_transcript
from spirebench.protocol import SCHEMA_VERSION, load_schema
from spirebench.worker.headless import (DEFAULT_GAME_VERSION, PYCLI, HeadlessGame, engine_identity, harness_dll,
                                        run_act, run_floor)

pytestmark = pytest.mark.skipif(not harness_dll().exists(), reason="Sts2Headless not built here")


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def _replay(header, moves):
    """Play the transcript's moves into a fresh game; the end must be the one recorded."""
    with HeadlessGame() as game:
        view = game.start_run(header["character"], header["ascension"], header["episode_key"])
        for move in moves:
            args = {k: v for k, v in move["action"].items() if k != "type"}
            view = game.act(move["action"]["type"], args)
            assert view.get("type") != "error", f"the recorded move {move} was refused on replay"
        return view


def test_a_random_bot_plays_a_game_to_the_end_through_the_library(gateway):
    url, store = gateway(HeadlessGame, engine=engine_identity())
    seen = []
    ends = Client(url, "dev", on_message=lambda d, m: seen.append(m)).run(
        RandomAgent(7), character="ironclad", ascension=10, episodes=1, agent_version="random-test")

    assert len(ends) == 1 and ends[0]["outcome"] in ("win", "loss")
    schema = load_schema()
    for msg in seen:
        jsonschema.validate(msg, schema)
        for d in msg.get("decisions", []):
            assert not {"rng", "ai", "move_state"} & set(_keys(d["observation"]))

    path, actions = store.db.execute("select transcript, actions from episodes").fetchone()
    lines = read_transcript(Path(path))
    moves = [x for x in lines if x["kind"] == "move"]
    # The header names the engine, so the replay can be checked after a harness update.
    engine = lines[0]["engine"]
    assert engine["harness_commit"] == subprocess.check_output(
        ["git", "-C", str(PYCLI.parent), "rev-parse", "HEAD"], text=True).strip()
    assert len(engine["game_dll_sha256"]) == len(engine["harness_dll_sha256"]) == 64
    assert engine["schema_version"] == SCHEMA_VERSION and engine["build_id"] == DEFAULT_GAME_VERSION
    assert actions == len(moves) == len(ends[0]["reveal"]["transcript"]) > 0

    end = _replay(lines[0], moves)
    assert end.get("decision") == "game_over"
    replayed = ("win" if end.get("victory") else "loss", run_floor(end), run_act(end))
    assert replayed == (lines[-1]["outcome"], lines[-1]["floor"], lines[-1]["act"])

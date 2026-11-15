"""M3 slice 2: the keyed harness deals the game the episode key decides, and nothing else.

The game never sees the key as a seed string: every generator draws from a
keyed ChaCha20 stream, a seed string is refused, one process plays one key,
and a draw the key does not decide stops the game. These run the real game,
so they are skipped where the keyed build hasn't been made
(``harness/build_keyed.sh <version>``).
"""

from __future__ import annotations

import pytest
from conftest import GAME_VERSION

from spirebench.gateway.replay_core import view_digest
from spirebench.worker.headless import HarnessError, HeadlessGame, keyed_dir

pytestmark = pytest.mark.skipif(not (keyed_dir(GAME_VERSION) / "Sts2Headless.dll").exists(),
                                reason="keyed harness not built here")

KEY = bytes(range(32))


def _opening(key: bytes) -> str:
    with HeadlessGame(GAME_VERSION) as game:
        return view_digest(game.start_run("Ironclad", 10, key))


def test_one_key_deals_one_game_in_every_process():
    assert _opening(KEY) == _opening(KEY)


def test_one_flipped_bit_of_the_key_deals_another_game():
    assert _opening(KEY) != _opening(bytes([KEY[0] ^ 1]) + KEY[1:])


def test_a_seed_string_is_refused():
    with HeadlessGame(GAME_VERSION) as game, pytest.raises(HarnessError, match="refuses seed strings"):
        game.start_seeded_run("Ironclad", 10, "ACTROLL1")


def test_a_short_key_is_refused():
    with HeadlessGame(GAME_VERSION) as game, pytest.raises(ValueError):
        game.start_run("Ironclad", 10, KEY[:16])


def test_one_process_plays_one_key():
    with HeadlessGame(GAME_VERSION) as game:
        game.start_run("Ironclad", 10, KEY)
        second = game.send({"cmd": "start_run", "character": "Ironclad", "ascension": 10, "seed": "11" * 32})
    assert second["type"] == "error" and "one game" in second["message"], second


@pytest.mark.skipif(not (keyed_dir(GAME_VERSION, plant=True) / "Sts2Headless.dll").exists(),
                    reason="planted build not made (harness/build_keyed.sh <version> --plant-unkeyed-draw)")
def test_a_draw_the_key_does_not_decide_stops_the_game():
    with HeadlessGame(GAME_VERSION, plant=True) as game:
        view = game.start_run("Ironclad", 10, KEY)
    assert view == {"type": "error", "message": "UnkeyedDrawException: unkeyed draw refused: System.Random..ctor"}

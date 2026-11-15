"""Foul Potion out of combat does what the game's own screens let it do.

At a merchant it is thrown for 100 gold. At the Fake Merchant event, which the
game shows as a shop, it starts the fight, whose rewards are the Merchant's Rug
and the stocked relics. Anywhere else out of combat it cannot be used. Runs the
upstream build, whose debug commands set the belt and enter the room.
"""

import random
from typing import Any

import pytest
from conftest import GAME_VERSION

from spirebench.worker.headless import HeadlessGame, harness_dll, legal_choices, take

pytestmark = pytest.mark.skipif(not harness_dll(GAME_VERSION).exists(), reason="Sts2Headless not built here")

FOUL = "FOUL_POTION"


def _game(seed: str, **player: Any) -> HeadlessGame:
    game = HeadlessGame(GAME_VERSION, keyed=False)
    try:
        game.start_seeded_run("Ironclad", 0, seed)
        assert game.send({"cmd": "set_player", "potions": [FOUL], **player}).get("type") == "ok"
        return game
    except BaseException:
        game.close()
        raise


def _foul(view: dict[str, Any]) -> dict[str, Any] | None:
    return next((p for p in view["player"]["potions"] if p["entry"] == FOUL), None)


def _moves(game: HeadlessGame, view: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return list(legal_choices(game, view))


def test_thrown_at_a_merchant_it_gives_100_gold():
    game = _game("FOUL1", gold=200)
    with game:
        shop = game.send({"cmd": "enter_room", "type": "shop"})
        assert shop["decision"] == "shop"
        foul = _foul(shop)
        assert foul and foul["can_use"]
        assert ("use_potion", {"potion_index": foul["index"]}) in _moves(game, shop)
        after = game.act("use_potion", {"potion_index": foul["index"]})
        assert after["decision"] == "shop"
        assert after["player"]["gold"] == 300
        assert _foul(after) is None


def test_it_cannot_be_used_away_from_a_merchant():
    game = _game("FOUL1")
    with game:
        rest = game.send({"cmd": "enter_room", "type": "rest_site"})
        foul = _foul(rest)
        assert foul and not foul["can_use"]
        assert all(verb != "use_potion" for verb, _ in _moves(game, rest))
        assert game.act("use_potion", {"potion_index": foul["index"]}).get("type") == "error"


def test_the_fake_merchant_is_a_shop_of_relics_the_player_may_buy_or_leave():
    game = _game("FOUL3", gold=200)
    with game:
        stall = game.send({"cmd": "enter_room", "type": "event", "event": "FAKE_MERCHANT"})
        assert stall["decision"] == "shop"
        assert stall["relics"] and not stall["cards"] and not stall["potions"]
        assert stall["card_removal_cost"] is None
        relic = stall["relics"][0]
        bought = game.act("buy_relic", {"relic_index": relic["index"]})
        assert bought["decision"] == "shop"
        assert bought["player"]["gold"] == 200 - relic["cost"]
        assert relic["id"] in [r["id"] for r in bought["player"]["relics"]]
        assert game.act("leave_room", {})["decision"] == "map_select"


def test_thrown_at_the_fake_merchant_it_starts_the_fight_and_wins_its_stock():
    game = _game("FOUL2", hp=999, max_hp=999)
    with game:
        stall = game.send({"cmd": "enter_room", "type": "event", "event": "FAKE_MERCHANT"})
        stocked = {r["id"] for r in stall["relics"] if r["is_stocked"]}
        foul = _foul(stall)
        assert foul and foul["can_use"]
        view = game.act("use_potion", {"potion_index": foul["index"]})
        assert view["decision"] == "combat_play"
        assert _foul(view) is None
        rng = random.Random(1)
        for _ in range(2000):
            if view["decision"] != "combat_play":
                break
            moves = [m for m in _moves(game, view) if m[0] == "play_card"] or _moves(game, view)
            view, _ = take(game, view, rng.choice(moves))
        assert view["decision"] == "rewards"
        relics = {r["id"] for r in view["rewards"] if r["kind"] == "relic"}
        assert "FAKE_MERCHANTS_RUG" in relics
        assert stocked <= relics
        view, _ = take(game, view, ("leave_rewards", {}))
        assert view["decision"] == "map_select"

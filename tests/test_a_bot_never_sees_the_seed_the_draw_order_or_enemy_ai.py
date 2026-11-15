"""The observation drops the run's RNG state, the draw order and enemy AI state."""

from spirebench.gateway.observe import observation

RAW = {
    "type": "decision", "decision": "combat_play", "round": 1,
    "rng": {"run_seed": 1417, "counters": {"shuffle": 11}},
    "draw_pile": [{"index": 0, "id": "CARD.STRIKE", "upgraded": False, "cost": 1},
                  {"index": 1, "id": "CARD.BASH", "upgraded": True, "cost": 2},
                  {"index": 2, "id": "CARD.STRIKE", "upgraded": False, "cost": 1},
                  {"index": 3, "id": "CARD.STRIKE", "upgraded": False, "cost": 0, "enchantment": "SHARP"}],
    "draw_pile_count": 4,
    "enemies": [{"index": 0, "hp": 40, "intents": [{"type": "Attack"}], "move_state": "BITE",
                 "ai": {"log": ["BITE"]}}],
}


def test_the_seed_and_counters_are_gone():
    assert "rng" in RAW  # shown to fail: the raw state carries them
    assert "rng" not in observation(RAW)


PILE = [
    {"card": {"cost": 0, "enchantment": "SHARP", "id": "CARD.STRIKE", "upgraded": False}, "count": 1},
    {"card": {"cost": 1, "id": "CARD.STRIKE", "upgraded": False}, "count": 2},
    {"card": {"cost": 2, "id": "CARD.BASH", "upgraded": True}, "count": 1},
]


def test_the_draw_pile_is_every_card_in_full_counted_without_order():
    assert observation(RAW)["draw_pile"] == PILE
    reordered = {**RAW, "draw_pile": [{**c, "index": i} for i, c in enumerate(reversed(RAW["draw_pile"]))]}
    assert observation(reordered) == observation(RAW)  # the order cannot be read back


def test_a_card_changed_in_combat_keeps_what_changed():
    # A cost, enchantment or affliction set on one copy is what the game shows on it.
    cards = [c["card"] for c in observation(RAW)["draw_pile"] if c["card"]["id"] == "CARD.STRIKE"]
    assert {c["cost"] for c in cards} == {0, 1} and any(c.get("enchantment") == "SHARP" for c in cards)


def test_enemy_ai_state_is_gone_but_the_intent_stays():
    enemy = observation(RAW)["enemies"][0]
    assert "ai" not in enemy and "move_state" not in enemy
    assert enemy["intents"] == [{"type": "Attack"}]


def test_the_raw_state_is_not_changed():
    observation(RAW)
    assert "rng" in RAW and isinstance(RAW["draw_pile"], list)


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def test_a_card_pick_opened_mid_fight_hides_its_nested_combat_board_too():
    # The harness nests the whole board under "combat" in a mid-fight card_select.
    pick = {"type": "decision", "decision": "card_select", "cards": [{"index": 0}],
            "combat": {k: v for k, v in RAW.items() if k not in ("type", "decision")}}
    assert {"rng", "ai", "move_state"} <= set(_keys(pick))  # shown to fail: the raw pick carries them
    obs = observation(pick)
    assert not {"rng", "ai", "move_state"} & set(_keys(obs))
    assert obs["combat"]["draw_pile"] == PILE

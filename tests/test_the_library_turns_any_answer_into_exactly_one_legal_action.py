"""resolve: an index, an action, or fields that match exactly one legal action."""

import pytest

from spirebench.client import ResolveError, resolve

DECISION = {"legal_actions": [
    {"action_id": 0, "type": "play_card", "card_index": 0, "target_index": 0},
    {"action_id": 1, "type": "play_card", "card_index": 0, "target_index": 1},
    {"action_id": 2, "type": "play_card", "card_index": 1},
    {"action_id": 3, "type": "end_turn"},
]}


def test_an_index_names_the_action_at_that_position():
    assert resolve(DECISION, 3) == 3


def test_an_action_is_taken_by_its_id():
    assert resolve(DECISION, DECISION["legal_actions"][1]) == 1


def test_fields_that_match_one_action_name_it():
    assert resolve(DECISION, {"type": "play_card", "card_index": 0, "target_index": 1}) == 1
    assert resolve(DECISION, {"type": "end_turn"}) == 3


@pytest.mark.parametrize("answer", [
    {"type": "play_card", "card_index": 0},           # two targets: ambiguous
    {"type": "play_card", "card_index": 7},           # no such card
    {"action_id": 9},                                  # not on the list
    4, -1, True, None, "end_turn",
])
def test_anything_else_is_refused_before_it_is_sent(answer):
    with pytest.raises(ResolveError):
        resolve(DECISION, answer)

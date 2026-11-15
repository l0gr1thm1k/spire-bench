"""legal_choices turns each non-combat screen into the moves a player may make there."""

import pytest

from spirebench.worker.headless import UnknownScreen, legal_choices, take


class _NoGame:
    def combat_actions(self):
        raise AssertionError("only combat asks the harness")


class _Recorder(_NoGame):
    def __init__(self):
        self.sent = []

    def act(self, verb, args):
        self.sent.append((verb, args))
        return {"type": "decision", "decision": "map_select"}


def _moves(view):
    return legal_choices(_NoGame(), view)


def test_a_locked_event_option_is_not_a_move():
    view = {"decision": "event_choice", "options": [{"index": 0}, {"index": 1, "is_locked": True}]}
    assert _moves(view) == [("choose_option", {"option_index": 0})]


def test_a_card_reward_can_be_skipped():
    view = {"decision": "card_reward", "cards": [{"index": 0}, {"index": 1}]}
    assert ("skip_card_reward", {}) in _moves(view)
    assert len(_moves(view)) == 3


def test_a_pick_two_selection_is_made_a_card_at_a_time_and_sent_as_one_move():
    view = {"decision": "card_select", "min_select": 2, "max_select": 2, "cards": [{"index": i} for i in range(3)]}
    assert _moves(view) == [("pick_card", {"card_index": i}) for i in range(3)]  # no skip: two are required
    game = _Recorder()
    after, sent = take(game, view, ("pick_card", {"card_index": 2}))
    assert sent is None and game.sent == [] and after["selected"] == [2]
    assert _moves(after) == [("pick_card", {"card_index": 0}), ("pick_card", {"card_index": 1})]
    after, sent = take(game, after, ("pick_card", {"card_index": 0}))
    assert sent == ("select_cards", {"indices": "0,2"}) and game.sent == [sent]
    assert after == {"type": "decision", "decision": "map_select"}


def test_a_selection_of_many_cards_offers_a_move_per_card_not_per_subset():
    # 40 cards, pick up to 10: C(40, 10) subsets, but a player clicks one card at a time.
    view = {"decision": "card_select", "min_select": 0, "max_select": 10, "cards": [{"index": i} for i in range(40)]}
    assert len(_moves(view)) == 41  # forty picks and a skip


def test_a_shop_offers_only_stocked_items_the_player_can_pay_for():
    view = {"decision": "shop", "player": {"gold": 100}, "card_removal_cost": 75,
            "cards": [{"index": 0, "cost": 150, "is_stocked": True}, {"index": 1, "cost": 50, "is_stocked": True},
                      {"index": 2, "cost": 40, "is_stocked": False}],
            "relics": [], "potions": []}
    assert _moves(view) == [("buy_card", {"card_index": 1}), ("remove_card", {}), ("leave_room", {})]


def test_a_used_card_removal_is_not_offered_but_leaving_always_is():
    view = {"decision": "shop", "player": {"gold": 500}, "card_removal_cost": None,
            "cards": [], "relics": [], "potions": []}
    assert _moves(view) == [("leave_room", {})]


def test_an_optional_selection_can_be_confirmed_early_or_skipped_before_any_pick():
    view = {"decision": "card_select", "min_select": 0, "max_select": 2, "cards": [{"index": i} for i in range(3)]}
    assert _moves(view)[-1] == ("skip_select", {})
    picked = {**view, "selected": [1]}
    assert _moves(picked) == [("pick_card", {"card_index": 0}), ("pick_card", {"card_index": 2}),
                              ("select_cards", {"indices": "1"})]


def test_a_required_pick_can_be_skipped_only_when_the_game_lets_it_be_cancelled():
    cards = [{"index": i} for i in range(2)]
    forced = {"decision": "card_select", "min_select": 1, "max_select": 1, "cards": cards, "cancelable": False}
    cancelable = {**forced, "cancelable": True}
    assert ("skip_select", {}) not in _moves(forced)
    assert ("skip_select", {}) in _moves(cancelable)


def test_a_selection_asking_for_more_cards_than_it_offers_takes_them_all():
    view = {"decision": "card_select", "min_select": 3, "max_select": 3, "cards": [{"index": 4}, {"index": 7}]}
    game = _Recorder()
    after, _ = take(game, view, ("pick_card", {"card_index": 7}))
    take(game, after, ("pick_card", {"card_index": 4}))
    assert game.sent == [("select_cards", {"indices": "4,7"})]


def test_an_unknown_screen_is_named_not_read_as_a_game_with_no_moves():
    with pytest.raises(UnknownScreen, match="'something_new' screen .*'Weird'"):
        _moves({"decision": "something_new", "room_type": "Weird"})


def test_every_open_event_option_is_a_move_and_there_is_no_generic_leave():
    # An STS2 event's way out is one of its own options; the harness sends them all.
    view = {"decision": "event_choice", "options": [{"index": 0}, {"index": 1, "is_enabled": True},
                                                    {"index": 2, "title": "Leave"}]}
    assert _moves(view) == [("choose_option", {"option_index": i}) for i in range(3)]


def test_a_disabled_rest_site_option_is_not_a_move():
    # The harness marks rest-site options with is_enabled (event options use is_locked).
    view = {"decision": "rest_site", "options": [{"index": 0, "is_enabled": True}, {"index": 1, "is_enabled": False}]}
    assert _moves(view) == [("choose_option", {"option_index": 0})]


def test_a_shop_offers_no_potion_on_a_full_belt():
    potions = [{"index": 0, "cost": 50, "is_stocked": True}]
    full = {"gold": 500, "potion_slots": 2, "potions": [{"index": 0}, {"index": 1}]}
    room = {"gold": 500, "potion_slots": 2, "potions": [{"index": 0}]}
    for player, offered in ((full, False), (room, True)):
        view = {"decision": "shop", "player": player, "cards": [], "relics": [], "potions": potions}
        assert (("buy_potion", {"potion_index": 0}) in _moves(view)) is offered

"""legal_choices turns each non-combat screen into the moves a player may make there."""

from spirebench.worker.headless import legal_choices


class _NoGame:
    def combat_actions(self):
        raise AssertionError("only combat asks the harness")


def _moves(view):
    return legal_choices(_NoGame(), view)


def test_a_locked_event_option_is_not_a_move():
    view = {"decision": "event_choice", "options": [{"index": 0}, {"index": 1, "is_locked": True}]}
    assert _moves(view) == [("choose_option", {"option_index": 0})]


def test_a_card_reward_can_be_skipped():
    view = {"decision": "card_reward", "cards": [{"index": 0}, {"index": 1}]}
    assert ("skip_card_reward", {}) in _moves(view)
    assert len(_moves(view)) == 3


def test_a_pick_two_selection_sends_both_indices_in_one_move():
    view = {"decision": "card_select", "min_select": 2, "max_select": 2, "cards": [{"index": i} for i in range(3)]}
    assert _moves(view) == [("select_cards", {"indices": "0,1"}), ("select_cards", {"indices": "0,2"}),
                            ("select_cards", {"indices": "1,2"})]


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


def test_an_optional_selection_offers_every_size_up_to_the_maximum_and_a_skip():
    view = {"decision": "card_select", "min_select": 0, "max_select": 2, "cards": [{"index": i} for i in range(3)]}
    sizes = [len(m[1]["indices"].split(",")) for m in _moves(view) if m[0] == "select_cards"]
    assert sizes == [1, 1, 1, 2, 2, 2]
    assert _moves(view)[-1] == ("skip_select", {})


def test_an_unknown_screen_has_no_moves():
    assert _moves({"decision": "something_new"}) == []

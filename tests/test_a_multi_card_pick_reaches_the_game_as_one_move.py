"""M4: a bot picks a multi-card selection a card at a time; the game and the transcript see one move.

The picks so far are part of what the bot is shown, but not of the game's
screen: a worker that dies mid-selection is restored to the screen the
transcript names, and the selection carries on.
"""

from __future__ import annotations

from typing import Any

from spirebench.gateway.episode import Episode
from spirebench.worker.headless import HarnessError

SELECT = {"type": "decision", "decision": "card_select", "min_select": 2, "max_select": 2,
          "cards": [{"index": i, "id": f"CARD.{i}"} for i in range(3)]}


class PickGame:
    dies_on_select = False
    booted = 0

    def __init__(self) -> None:
        PickGame.booted += 1
        self.first = PickGame.booted == 1
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def start_run(self, character: str, ascension: int, key: bytes) -> dict[str, Any]:
        return dict(SELECT)

    def act(self, verb: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.first and PickGame.dies_on_select:
            raise HarnessError("harness exited (code 137)")
        self.sent.append((verb, args))
        return {"type": "decision", "decision": "game_over", "victory": True}

    def combat_actions(self) -> list[Any]:
        raise AssertionError("no combat here")

    def close(self) -> None:
        pass


def _episode(start: dict[str, Any] | None = None) -> Episode:
    PickGame.booted = 0
    game = PickGame()
    if start is not None:
        game.start_run = lambda *_: dict(start)  # type: ignore[method-assign]
    ep = Episode("e", "s", "a", 1, "Ironclad", 0, b"k" * 32, game, PickGame)
    ep.start()
    return ep


def _pick(ep: Episode, card: int) -> None:
    moves = ep.decision(1000)["legal_actions"]
    action_id, = [m["action_id"] for m in moves if m == {**m, "type": "pick_card", "card_index": card}]
    assert ep.apply(ep.seq, action_id) is None


def test_two_picks_send_one_selection_and_record_one_move():
    ep = _episode()
    first_digest = ep.digest
    _pick(ep, 2)
    shown = ep.decision(1000)
    assert shown["observation"]["selected"] == [2] and ep.transcript == []
    assert ep.digest == first_digest  # the game's screen has not moved
    _pick(ep, 0)
    assert ep.game.sent == [("select_cards", {"indices": "0,2"})]  # type: ignore[attr-defined]
    assert [m["action"] for m in ep.transcript] == [{"type": "select_cards", "indices": "0,2"}]
    assert ep.transcript[0]["digest"] == first_digest and ep.outcome == "win"


def test_a_worker_that_dies_mid_selection_is_restored_and_the_selection_completes():
    PickGame.dies_on_select = True
    try:
        ep = _episode()
        _pick(ep, 1)
        _pick(ep, 2)
    finally:
        PickGame.dies_on_select = False
    assert (ep.outcome, ep.fault, ep.restores) == ("win", None, 1)
    assert ep.game.sent == [("select_cards", {"indices": "1,2"})]  # type: ignore[attr-defined]


def test_a_screen_nobody_has_mapped_voids_the_game_by_name():
    ep = _episode({"type": "decision", "decision": "something_new", "room_type": "Weird"})
    assert (ep.outcome, ep.fault) == ("void", "unknown_screen")

"""What a bot is shown: the harness's state with the hidden parts taken out.

M1 removes the fields we know are hidden: every ``rng`` block (run seed and
stream counters), the order of every draw pile, and each enemy's AI state.
They are removed at any depth, because the harness nests a whole combat board
inside a card selection opened mid-fight (``RunSimulator.cs``, ``["combat"] =
InCombatBoard(player)``). M2 replaces this with an allow-list, so a field the
harness adds later is dropped until someone decides a bot may see it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from spirebench.worker.headless import Choice

HIDDEN_KEYS = frozenset({"rng"})
HIDDEN_ENEMY_FIELDS = frozenset({"ai", "move_state"})
ENEMY_LISTS = frozenset({"enemies", "dead_enemies"})
DROPPED_TOP_LEVEL = frozenset({"type", "decision"})


def _card_key(card: dict[str, Any]) -> str:
    return str(card.get("id")) + ("+" if card.get("upgraded") else "")


def _scrub(value: Any, hide: frozenset[str] = HIDDEN_KEYS) -> Any:
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, v in value.items():
        if key in hide:
            continue
        if key == "draw_pile" and isinstance(v, list):
            # The real game shows the draw pile sorted, never in draw order.
            out[key] = dict(sorted(Counter(_card_key(c) for c in v).items()))
        elif key in ENEMY_LISTS and isinstance(v, list):
            out[key] = [_scrub(e, HIDDEN_KEYS | HIDDEN_ENEMY_FIELDS) for e in v]
        else:
            out[key] = _scrub(v)
    return out


def observation(view: dict[str, Any]) -> dict[str, Any]:
    """The bot's view of one decision. The input is not changed."""
    scrubbed: dict[str, Any] = _scrub({k: v for k, v in view.items() if k not in DROPPED_TOP_LEVEL})
    return scrubbed


def legal_actions(choices: list[Choice]) -> list[dict[str, Any]]:
    """Number the moves. In v0 an action's id is its position in the list."""
    return [{"action_id": i, "type": verb, **{k: v for k, v in args.items() if v is not None}}
            for i, (verb, args) in enumerate(choices)]

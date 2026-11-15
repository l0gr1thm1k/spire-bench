"""Turn whatever a bot returns into exactly one legal action.

A bot may answer a decision with an action's position in ``legal_actions``,
with the action itself (anything carrying its ``action_id``), or with
structured fields such as ``{"type": "play_card", "card_index": 2}``. Fields
must match exactly one legal action. Anything else raises here, on the bot's
machine, before a message is sent. The gateway checks legality again anyway.
"""

from __future__ import annotations

from typing import Any


class ResolveError(ValueError):
    """The bot's answer is not exactly one legal action."""


def resolve(decision: dict[str, Any], answer: Any) -> int:
    """The action_id the answer names."""
    legal = decision["legal_actions"]
    if isinstance(answer, bool) or answer is None:
        raise ResolveError(f"not an action: {answer!r}")
    if isinstance(answer, int):
        if not 0 <= answer < len(legal):
            raise ResolveError(f"index {answer} is out of range for {len(legal)} legal actions")
        return int(legal[answer]["action_id"])
    if not isinstance(answer, dict):
        raise ResolveError(f"an answer is an index or a dict, not {type(answer).__name__}")
    if "action_id" in answer:
        ids = {a["action_id"] for a in legal}
        if answer["action_id"] not in ids:
            raise ResolveError(f"action_id {answer['action_id']!r} is not legal here")
        return int(answer["action_id"])
    matches = [a for a in legal if all(a.get(k) == v for k, v in answer.items())]
    if len(matches) != 1:
        raise ResolveError(f"{answer!r} matches {len(matches)} legal actions, not exactly one")
    return int(matches[0]["action_id"])

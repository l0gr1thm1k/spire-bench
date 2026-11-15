"""Replaying recorded moves into a game, checking each observation's digest on the way.

Used both by the gateway, to restore a game whose worker died, and by
``python -m spirebench.replay``, to check a transcript after the fact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from spirebench.gateway.observe import observation
from spirebench.worker.headless import Game


def view_digest(view: dict[str, Any]) -> str:
    """SHA-256 of what the bot was shown at one decision.

    The legal-action list is left out: a refused move shrinks it without
    changing the game, so it would report a divergence that isn't one.
    """
    shown = {"decision_type": str(view.get("decision")), "observation": observation(view)}
    return hashlib.sha256(json.dumps(shown, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class Replayed:
    view: dict[str, Any]  # where the replay stopped
    diverged_at: int | None = None  # index of the first move whose decision or move differs, None if none did
    reason: str = ""


def replay(game: Game, character: str, ascension: int, seed: str, moves: list[dict[str, Any]]) -> Replayed:
    """Start the game and make each move, stopping at the first one that doesn't match the record."""
    view = game.start_run(character, ascension, seed)
    for i, move in enumerate(moves):
        if view_digest(view) != move["digest"]:
            return Replayed(view, i, "the decision differs")
        verb = move["action"]["type"]
        after = game.act(verb, {k: v for k, v in move["action"].items() if k != "type"})
        if after.get("type") == "error":
            return Replayed(view, i, "the game refused the recorded move")
        view = after
    return Replayed(view)

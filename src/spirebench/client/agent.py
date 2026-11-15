"""What a bot author subclasses."""

from __future__ import annotations

import random
from typing import Any


class Agent:
    """A bot. Implement ``act``, or ``act_batch`` to decide many games at once.

    Each decision is a dict with ``episode``, ``seq``, ``decision_type``,
    ``observation`` and ``legal_actions``. An answer is an index into
    ``legal_actions``, a legal action, or fields matching exactly one of them.
    """

    def act(self, decision: dict[str, Any]) -> Any:
        raise NotImplementedError("implement act or act_batch")

    def act_batch(self, decisions: list[dict[str, Any]]) -> list[Any]:
        return [self.act(d) for d in decisions]

    def episode_end(self, result: dict[str, Any]) -> None:
        """Called once per finished game with its episode_end message."""


class RandomAgent(Agent):
    """Picks uniformly among the legal actions. The baseline every bot must beat."""

    def __init__(self, seed: int | str | None = None) -> None:
        self.rng = random.Random(seed)

    def act(self, decision: dict[str, Any]) -> Any:
        return self.rng.randrange(len(decision["legal_actions"]))

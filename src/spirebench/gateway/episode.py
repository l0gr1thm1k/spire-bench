"""One game as the gateway runs it: the open decision, the moves made, the end.

The game object is anything with HeadlessGame's ``start_run``, ``act``,
``combat_actions`` and ``close``, so the gateway can be tested without the
real game. Every call into it blocks; the server runs them off its event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from spirebench.gateway.observe import action, legal_actions, observation
from spirebench.worker.headless import Choice, Game, legal_choices, run_act, run_floor

log = logging.getLogger(__name__)


def close_game(game: Game, label: str) -> None:
    """Stop a harness. Never raises: whatever happens, the game's result still has to be recorded."""
    try:
        game.close()
    except Exception:
        log.exception("could not close the harness of %s", label)


@dataclass
class Episode:
    episode_id: str
    number: int
    character: str  # the harness's spelling, for example "Ironclad"
    ascension: int
    seed: str
    game: Game
    seq: int = -1
    outcome: str | None = None  # win, loss or void once over
    floor: int = 0
    act: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    _view: dict[str, Any] = field(default_factory=dict)
    _choices: list[Choice] = field(default_factory=list)

    @property
    def over(self) -> bool:
        return self.outcome is not None

    def start(self) -> None:
        self._advance(self._call(lambda: self.game.start_run(self.character, self.ascension, self.seed)))

    def decision(self, deadline_ms: int) -> dict[str, Any]:
        """The decision waiting for an action. Only valid while not over."""
        return {"episode": self.episode_id, "seq": self.seq, "decision_type": str(self._view.get("decision")),
                "observation": observation(self._view), "legal_actions": legal_actions(self._choices),
                "deadline_ms": deadline_ms}

    def apply(self, seq: int, action_id: int) -> str | None:
        """Make one move. Returns an error code, or None when the move was made."""
        if self.over or seq != self.seq:
            return "bad_request"
        if not 0 <= action_id < len(self._choices):
            return "illegal_action"
        verb, args = self._choices[action_id]
        after = self._call(lambda: self.game.act(verb, args))
        if self.over:
            return "internal"
        if after.get("type") == "error":
            # The move list offered something the game refused. Drop it and ask again.
            self._choices = self._choices[:action_id] + self._choices[action_id + 1:]
            self.seq += 1
            if not self._choices:
                self._void()
            return "rejected"
        self.transcript.append({"seq": self.seq, "decision_type": self._view.get("decision"),
                                "action": action((verb, args))})
        self._advance(after)
        return None

    def close(self) -> None:
        close_game(self.game, self.episode_id)

    def _call(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Call the game. Anything it raises voids this game rather than escaping to the gateway."""
        try:
            return fn()
        except Exception:
            log.exception("the game of %s failed", self.episode_id)
            self._void()
            return {}

    def _void(self) -> None:
        self.outcome = "void"

    def _advance(self, view: dict[str, Any]) -> None:
        if self.over:
            return
        if view.get("type") == "error":
            self._void()
            return
        self._view = view
        self.floor = run_floor(view) or self.floor
        self.act = run_act(view) or self.act
        if view.get("decision") == "game_over":
            self.outcome = "win" if view.get("victory") else "loss"
            return
        try:
            self._choices = legal_choices(self.game, view)  # combat asks the harness
        except Exception:
            log.exception("could not list the moves of %s", self.episode_id)
            self._choices = []
        if not self._choices:
            self._void()
            return
        self.seq += 1

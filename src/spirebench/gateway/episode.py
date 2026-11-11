"""One game as the gateway runs it: the open decision, the moves made, the end.

The game object is anything with HeadlessGame's ``start_run``, ``act``,
``combat_actions`` and ``close``, so the gateway can be tested without the
real game. Every call into it blocks; the server runs them off its event loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from spirebench.gateway.observe import legal_actions, observation
from spirebench.worker.headless import Choice, HarnessError, legal_choices


class Game(Protocol):
    def start_run(self, character: str, ascension: int, seed: str) -> dict[str, Any]: ...
    def act(self, verb: str, args: dict[str, Any]) -> dict[str, Any]: ...
    def combat_actions(self) -> list[Choice]: ...
    def close(self) -> None: ...


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
                                "action": {"type": verb, **{k: v for k, v in args.items() if v is not None}}})
        self._advance(after)
        return None

    def close(self) -> None:
        try:
            self.game.close()
        except (HarnessError, OSError):
            pass

    def _call(self, fn: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = fn()
            return result
        except (HarnessError, OSError, ValueError):
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
        ctx = view.get("context") or {}
        self.floor = int(ctx.get("total_floor") or view.get("floor") or self.floor)
        self.act = int(ctx.get("act") or view.get("act") or self.act)
        if view.get("decision") == "game_over":
            self.outcome = "win" if view.get("victory") else "loss"
            return
        try:
            self._choices = legal_choices(self.game, view)  # combat asks the harness
        except (HarnessError, OSError, ValueError):
            self._choices = []
        if not self._choices:
            self._void()
            return
        self.seq += 1

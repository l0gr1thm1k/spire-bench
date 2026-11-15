"""One game as the gateway runs it: the open decision, the moves made, the end.

The game object is anything with HeadlessGame's ``start_run``, ``act``,
``combat_actions`` and ``close``, so the gateway can be tested without the
real game. Every call into it blocks; the server runs them off its event loop.

A worker that dies mid-game is restored, never handed back to the bot: a new
worker replays the same key and moves, every observation digest is checked,
and the failed call is made once more. A game the gateway can't carry on is
void, with the reason in ``fault``. Nothing the bot does is a fault.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from spirebench.gateway.keys import harness_seed
from spirebench.gateway.observe import action, legal_actions, observation
from spirebench.gateway.replay_core import replay, view_digest
from spirebench.protocol import commitment
from spirebench.worker.headless import Choice, Game, HarnessError, legal_choices, run_act, run_floor

log = logging.getLogger(__name__)

T = TypeVar("T")


def close_game(game: Game, label: str) -> None:
    """Stop a harness. Never raises: whatever happens, the game's result still has to be recorded."""
    try:
        game.close()
    except Exception:
        log.exception("could not close the harness of %s", label)


@dataclass
class Episode:
    episode_id: str
    season: str
    agent_version: str
    number: int  # this agent version's count in the season; with the two above it names the key
    character: str  # the harness's spelling, for example "Ironclad"
    ascension: int
    key: bytes
    game: Game
    factory: Callable[[], Game]  # boots a replacement worker for a restore
    seq: int = -1
    outcome: str | None = None  # win, loss or void once over
    fault: str | None = None  # why a void game could not go on
    floor: int = 0
    act: int = 0
    restores: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    digest: str = ""  # of the view now showing; the transcript's end line keeps the last one
    _view: dict[str, Any] = field(default_factory=dict)
    _observation: dict[str, Any] = field(default_factory=dict)
    _choices: list[Choice] = field(default_factory=list)

    @property
    def over(self) -> bool:
        return self.outcome is not None

    @property
    def commit(self) -> str:
        return commitment(self.key)

    def start(self) -> None:
        view = self._call(lambda: self.game.start_run(self.character, self.ascension, harness_seed(self.key)))
        if view is not None:
            self._advance(view)

    def decision(self, deadline_ms: int) -> dict[str, Any]:
        """The decision waiting for an action. Only valid while not over."""
        return {"episode": self.episode_id, "seq": self.seq, "decision_type": str(self._view.get("decision")),
                "observation": self._observation, "legal_actions": legal_actions(self._choices),
                "deadline_ms": deadline_ms}

    def apply(self, seq: int, action_id: int) -> str | None:
        """Make one move. Returns an error code, or None when the move was made."""
        if self.over or seq != self.seq:
            return "bad_request"
        if not 0 <= action_id < len(self._choices):
            return "illegal_action"
        verb, args = self._choices[action_id]
        after = self._call(lambda: self.game.act(verb, args))
        if after is None:
            return "internal"
        if after.get("type") == "error":
            # The move list offered something the game refused. Drop it and ask again.
            self._choices = self._choices[:action_id] + self._choices[action_id + 1:]
            self.seq += 1
            if not self._choices:
                self.void("all_refused")
            return "rejected"
        self.transcript.append({"seq": self.seq, "decision_type": self._view.get("decision"),
                                "action": action((verb, args)), "digest": self.digest})
        self._advance(after)
        return None

    def void(self, fault: str) -> None:
        self.outcome, self.fault = "void", fault

    def close(self) -> None:
        close_game(self.game, self.episode_id)

    def _call(self, fn: Callable[[], T]) -> T | None:
        """Call the game, restoring it once if its worker dies. None means the game is now void.

        ``fn`` must read ``self.game`` when called, so the retry reaches the new worker.
        """
        try:
            return fn()
        except HarnessError:
            log.exception("the worker of %s died; restoring it", self.episode_id)
        except Exception:
            log.exception("the game of %s failed", self.episode_id)
            self.void("game_error")
            return None
        if not self._restore():
            return None
        try:
            return fn()
        except Exception:
            log.exception("the restored game of %s failed on the same call", self.episode_id)
            self.void("restore_failed")
            return None

    def _restore(self) -> bool:
        """Swap in a new worker at the state the bot is looking at. False (and void) if it can't be."""
        self.restores += 1
        close_game(self.game, self.episode_id)
        try:
            self.game = self.factory()
            if not self._view:
                return True  # died booting: the retry boots the new worker
            got = replay(self.game, self.character, self.ascension, harness_seed(self.key), self.transcript)
        except Exception:
            log.exception("could not restore %s", self.episode_id)
            self.void("restore_failed")
            return False
        if got.diverged_at is not None or view_digest(got.view) != self.digest:
            where = "the current decision" if got.diverged_at is None else f"move {got.diverged_at}"
            log.error("the restore of %s diverged at %s: %s", self.episode_id, where, got.reason or "the view differs")
            self.void("restore_diverged")
            return False
        return True

    def _advance(self, view: dict[str, Any]) -> None:
        if self.over:
            return
        if view.get("type") == "error":
            self.void("error_screen")
            return
        self._view, self._observation, self.digest = view, observation(view), view_digest(view)
        self.floor = run_floor(view) or self.floor
        self.act = run_act(view) or self.act
        if view.get("decision") == "game_over":
            self.outcome = "win" if view.get("victory") else "loss"
            return
        choices = self._call(lambda: legal_choices(self.game, view))  # combat asks the harness
        if choices is None:
            return
        if not choices:
            self.void("no_moves")
            return
        self._choices = choices
        self.seq += 1

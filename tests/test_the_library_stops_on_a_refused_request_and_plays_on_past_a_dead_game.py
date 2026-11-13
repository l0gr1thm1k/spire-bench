"""The library's error rule: an error about one game is that game's, an error about a request ends the run.

Every run here is wrapped in a timeout, because the failure these guard against is a client that waits forever.
"""

from __future__ import annotations

import asyncio

import pytest

from spirebench import Client, RandomAgent
from spirebench.client import GatewayError
from spirebench.worker.headless import HarnessError
from test_the_gateway_refuses_what_a_bot_may_not_do import ScriptedGame


def _run(url, token="dev", **kw):
    args = {"character": "ironclad", "ascension": 10, "episodes": 1, "agent_version": "bot", **kw}
    return asyncio.run(asyncio.wait_for(Client(url, token).run_async(RandomAgent(3), **args), 10))


def test_an_agent_version_another_owner_holds_raises_instead_of_waiting_forever(gateway):
    url, _ = gateway(ScriptedGame, tokens={"a": "alice", "b": "bob"})
    _run(url, "a")
    with pytest.raises(GatewayError, match="rejected"):
        _run(url, "b")


class OneCrashingGame(ScriptedGame):
    """The first game booted dies on its first move; the others play normally."""

    booted = 0

    def __init__(self):
        super().__init__()
        self.crashes = OneCrashingGame.booted == 0
        OneCrashingGame.booted += 1

    def act(self, verb, args):
        if self.crashes:
            raise HarnessError("harness exited (code 134)")
        return super().act(verb, args)


def test_a_game_whose_harness_dies_is_void_and_the_other_games_play_on(gateway):
    OneCrashingGame.booted = 0
    url, _ = gateway(OneCrashingGame)
    ends = _run(url, episodes=3, in_flight=2)  # the random bot also hits the move the game refuses
    assert sorted(e["outcome"] for e in ends) == ["void", "win", "win"]


def test_zero_games_is_refused_before_connecting():
    with pytest.raises(ValueError):
        Client("ws://127.0.0.1:1", "dev").run(RandomAgent(), character="ironclad", ascension=0, episodes=0,
                                              agent_version="bot")

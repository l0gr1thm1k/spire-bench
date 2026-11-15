"""Connect to a gateway and play games with an Agent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from spirebench._version import __version__
from spirebench.client.agent import Agent
from spirebench.client.resolve import resolve
from spirebench.protocol import MAX_MESSAGE_BYTES, PRACTICE_KEY, PROTOCOL_VERSION, Message, decode, encode


class GatewayError(RuntimeError):
    """The gateway refused the connection or answered in a way the library can't continue from."""


class Client:
    def __init__(self, url: str, token: str, on_message: Callable[[str, Message], None] | None = None) -> None:
        """on_message(direction, msg) sees every message sent ("out") and received ("in")."""
        self.url, self.token, self.on_message = url, token, on_message
        self.welcome: Message = {}

    def run(self, agent: Agent, *, character: str, ascension: int, episodes: int, agent_version: str,
            in_flight: int = 1, key: str = PRACTICE_KEY, metadata: dict[str, Any] | None = None) -> list[Message]:
        """Play ``episodes`` games, ``in_flight`` at a time. Returns every episode_end message.

        This starts its own event loop. Inside one that is already running (a
        notebook, an async app), await ``run_async`` instead.
        """
        return asyncio.run(self.run_async(agent, character=character, ascension=ascension, episodes=episodes,
                                          agent_version=agent_version, in_flight=in_flight, key=key,
                                          metadata=metadata))

    async def run_async(self, agent: Agent, *, character: str, ascension: int, episodes: int, agent_version: str,
                        in_flight: int = 1, key: str = PRACTICE_KEY,
                        metadata: dict[str, Any] | None = None) -> list[Message]:
        """``run``, for code that already has an event loop."""
        if episodes < 1 or in_flight < 1:
            raise ValueError(f"episodes and in_flight must be at least 1, not {episodes} and {in_flight}")
        start = {"type": "start", "agent_version": agent_version, "character": character.lower(),
                 "ascension": ascension, "key": key, "metadata": metadata or {}}
        return await self._run(agent, start, episodes, min(in_flight, episodes))

    async def _run(self, agent: Agent, start: Message, episodes: int, in_flight: int) -> list[Message]:
        async with connect(self.url, max_size=MAX_MESSAGE_BYTES) as ws:
            await self._hello(ws)
            ends: list[Message] = []
            started = in_flight
            await self._send(ws, {**start, "count": in_flight})
            while len(ends) < episodes:
                msg = await self._recv(ws)
                if msg["type"] == "decisions":
                    await self._answer(ws, agent, msg["decisions"])
                elif msg["type"] == "episode_end":
                    ends.append(msg)
                    agent.episode_end(msg)
                    if started < episodes:
                        started += 1
                        await self._send(ws, {**start, "count": 1})
                elif msg["type"] == "errors":
                    self._check_errors(msg["errors"])
            return ends

    async def _hello(self, ws: ClientConnection) -> None:
        await self._send(ws, {"type": "hello", "token": self.token, "client_version": __version__,
                              "protocol_versions": [PROTOCOL_VERSION]})
        reply = await self._recv(ws)
        if reply["type"] != "welcome":
            raise GatewayError(f"gateway refused the connection: {reply}")
        self.welcome = reply

    async def _answer(self, ws: ClientConnection, agent: Agent, decisions: list[Message]) -> None:
        answers = agent.act_batch(decisions)
        if len(answers) != len(decisions):
            raise GatewayError(f"act_batch returned {len(answers)} answers for {len(decisions)} decisions")
        actions = [{"episode": d["episode"], "seq": d["seq"], "action_id": resolve(d, a)}
                   for d, a in zip(decisions, answers)]
        await self._send(ws, {"type": "actions", "actions": actions})

    @staticmethod
    def _check_errors(errors: list[Message]) -> None:
        # An error that names an episode is about that game alone, and the
        # gateway follows it with a fresh decision ("rejected": the game refused
        # a move the list offered) or the game's end ("internal": the harness
        # died, so the game is void). Those are not the library's to stop on.
        # An error with no episode is about a request: a refused start, an
        # agent version another owner holds, a game that could not boot. The
        # library can't continue from those, and waiting would hang.
        # illegal_action and bad_request mean the library itself sent
        # something wrong, so they are fatal wherever they come from.
        fatal = [e for e in errors if "episode" not in e or e.get("code") in ("illegal_action", "bad_request")]
        if fatal:
            raise GatewayError(f"gateway errors: {fatal}")

    async def _send(self, ws: ClientConnection, msg: Message) -> None:
        if self.on_message:
            self.on_message("out", msg)
        await ws.send(encode(msg))

    async def _recv(self, ws: ClientConnection) -> Message:
        msg = decode(await ws.recv())
        if self.on_message:
            self.on_message("in", msg)
        return msg

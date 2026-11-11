"""Connect to a gateway and play games with an Agent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from spirebench import __version__
from spirebench.client.agent import Agent
from spirebench.client.resolve import resolve
from spirebench.protocol import PROTOCOL_VERSION, Message, decode, encode


class GatewayError(RuntimeError):
    """The gateway refused the connection or answered in a way the library can't continue from."""


class Client:
    def __init__(self, url: str, token: str, on_message: Callable[[str, Message], None] | None = None) -> None:
        """on_message(direction, msg) sees every message sent ("out") and received ("in")."""
        self.url, self.token, self.on_message = url, token, on_message
        self.welcome: Message = {}

    def run(self, agent: Agent, *, character: str, ascension: int, episodes: int, agent_version: str,
            in_flight: int = 1, key: str = "practice", metadata: dict[str, Any] | None = None) -> list[Message]:
        """Play ``episodes`` games, ``in_flight`` at a time. Returns every episode_end message."""
        start = {"type": "start", "agent_version": agent_version, "character": character.lower(),
                 "ascension": ascension, "key": key, "metadata": metadata or {}}
        return asyncio.run(self._run(agent, start, episodes, max(1, min(in_flight, episodes))))

    async def _run(self, agent: Agent, start: Message, episodes: int, in_flight: int) -> list[Message]:
        async with connect(self.url, max_size=2**22) as ws:
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
        # "rejected" means the game refused a move the list offered; a fresh decision follows.
        fatal = [e for e in errors if e.get("code") != "rejected"]
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

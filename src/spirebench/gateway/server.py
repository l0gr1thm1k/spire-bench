"""The gateway: a WebSocket server that runs games for connected bots.

One connection is one client. It says hello, starts episodes, and answers
decisions with actions. Each episode owns one harness process; every call
into it runs in a thread so the event loop stays free.

M1 scope: one practice key with the harness's own seeded dice, a token check
against a fixed set, and the deadline sent but not enforced. Deadlines,
reconnect and the observation allow-list are M2.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import string
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from spirebench.gateway.episode import Episode, Game, close_game
from spirebench.gateway.store import Store
from spirebench.protocol import (CHARACTERS, MAX_ASCENSION, MAX_MESSAGE_BYTES, PRACTICE_KEY, PROTOCOL_VERSION,
                                 SCHEMA_VERSION, Message, decode, encode)
from spirebench.worker.headless import DEFAULT_GAME_VERSION

log = logging.getLogger(__name__)

SEED_ALPHABET = string.ascii_uppercase + string.digits


def new_seed() -> str:
    return "".join(secrets.choice(SEED_ALPHABET) for _ in range(12))


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)  # JSON true is not a count


@dataclass
class Settings:
    build_id: str = DEFAULT_GAME_VERSION
    tokens: dict[str, str] = field(default_factory=lambda: {"dev": "dev"})  # token -> owner
    decision_deadline_ms: int = 5000
    max_in_flight: int = 64  # open games per owner, across all of the owner's connections
    max_games: int = 128  # open games on this gateway, across all owners
    engine: dict[str, Any] = field(default_factory=dict)  # worker.headless.engine_identity(), for transcripts


@dataclass
class _Connection:
    ws: ServerConnection
    episodes: dict[str, Episode] = field(default_factory=dict)  # open games only
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    owner: str = ""  # who the token belongs to; owns the agent versions it plays
    admit: asyncio.Lock = field(default_factory=asyncio.Lock)  # starts are admitted in the order they arrived

    async def send(self, msg: Message) -> None:
        await self.ws.send(encode(msg))

    async def error(self, code: str, episode: str | None = None, seq: int | None = None) -> None:
        item: dict[str, Any] = {"code": code}
        if episode is not None:
            item["episode"] = episode
        if seq is not None:
            item["seq"] = seq
        await self.send({"type": "errors", "errors": [item]})


class Gateway:
    def __init__(self, store: Store, game_factory: Callable[[], Game], settings: Settings | None = None,
                 seed_fn: Callable[[], str] = new_seed) -> None:
        self.store, self.game_factory = store, game_factory
        self.settings = settings or Settings()
        self.seed_fn = seed_fn
        self._numbers: dict[str, int] = {}
        self._numbering = asyncio.Lock()
        # Game slots, reserved when a start is admitted and released when its
        # game ends or fails to boot. Counted per owner, not per connection,
        # so opening more connections doesn't buy more harness processes.
        self._slots: Counter[str] = Counter()

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> Server:
        return await serve(self.handle, host, port, max_size=MAX_MESSAGE_BYTES)

    async def handle(self, ws: ServerConnection) -> None:
        conn = _Connection(ws)
        tasks: set[asyncio.Task[None]] = set()
        try:
            if not await self._greet(conn):
                return
            async for text in ws:
                task = asyncio.create_task(self._dispatch(conn, text))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except ConnectionClosed:
            pass
        finally:
            # Let every game call in flight settle first, so nothing touches a
            # harness pipe while it is being closed and no game starts unseen.
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._abandon(conn)

    async def _greet(self, conn: _Connection) -> bool:
        try:
            hello = decode(await conn.ws.recv())
        except ValueError:
            hello = {}
        ok = (hello.get("type") == "hello" and PROTOCOL_VERSION in (hello.get("protocol_versions") or [])
              and hello.get("token") in self.settings.tokens)
        if not ok:
            await conn.error("rejected")
            await conn.ws.close()
            return False
        conn.owner = self.settings.tokens[str(hello["token"])]
        cfg = self.settings
        await conn.send({"type": "welcome", "protocol_version": PROTOCOL_VERSION, "build_id": cfg.build_id,
                         "schema_version": SCHEMA_VERSION,
                         "limits": {"decision_deadline_ms": cfg.decision_deadline_ms,
                                    "max_in_flight": cfg.max_in_flight}})
        return True

    async def _dispatch(self, conn: _Connection, text: str | bytes) -> None:
        try:
            msg = decode(text)
        except ValueError:
            await conn.error("bad_request")
            return
        try:
            if msg["type"] == "start":
                await self._start(conn, msg)
            elif msg["type"] == "actions":
                actions = msg.get("actions") or []
                results = await asyncio.gather(*(self._act(conn, a) for a in actions), return_exceptions=True)
                await self._report(conn, results, [a.get("episode") if isinstance(a, dict) else None
                                                   for a in actions])
            else:
                await conn.error("bad_request")
        except ConnectionClosed:
            pass
        except Exception:  # one bad message must not take the connection down
            log.exception("gateway failed on a %s message", msg.get("type"))
            await conn.error("internal")

    async def _start(self, conn: _Connection, msg: Message) -> None:
        character = CHARACTERS.get(str(msg.get("character")))
        ascension, count, agent = msg.get("ascension"), msg.get("count"), msg.get("agent_version")
        metadata = msg.get("metadata", {})
        if not (character and _is_int(ascension) and 0 <= ascension <= MAX_ASCENSION and _is_int(count)
                and count >= 1 and isinstance(agent, str) and agent and isinstance(metadata, dict)
                and msg.get("key") == PRACTICE_KEY):
            await conn.error("bad_request")
            return
        async with conn.admit:
            if not await asyncio.to_thread(self.store.claim_agent, agent, conn.owner, metadata):
                await conn.error("rejected")  # another owner's agent version: its streak isn't ours to extend
                return
            if (self._slots[conn.owner] + count > self.settings.max_in_flight
                    or sum(self._slots.values()) + count > self.settings.max_games):
                await conn.error("not_available")
                return
            self._slots[conn.owner] += count  # reserved before any await, so pipelined starts can't overshoot
        results = await asyncio.gather(*(self._run_new(conn, agent, str(character), ascension)
                                         for _ in range(count)), return_exceptions=True)
        await self._report(conn, results, [None] * count)

    @staticmethod
    async def _report(conn: _Connection, results: Sequence[BaseException | None], episodes: list[Any]) -> None:
        """Tell the client about each operation that failed unexpectedly, rather than leave it waiting."""
        for result, episode in zip(results, episodes):
            if isinstance(result, Exception) and not isinstance(result, ConnectionClosed):
                log.error("gateway failed on one operation", exc_info=result)
                await conn.error("internal", str(episode) if episode else None)

    async def _number(self, agent: str) -> int:
        async with self._numbering:  # one read of the store per agent version, off the loop
            if agent not in self._numbers:
                self._numbers[agent] = await asyncio.to_thread(self.store.next_number, agent) - 1
            self._numbers[agent] += 1
            return self._numbers[agent]

    async def _run_new(self, conn: _Connection, agent: str, character: str, ascension: int) -> None:
        episode_id = f"e-{uuid.uuid4().hex[:12]}"
        try:
            game = await asyncio.to_thread(self.game_factory)
        except Exception:
            self._slots[conn.owner] -= 1
            log.exception("could not start a game process")
            await conn.error("internal")
            return
        except BaseException:
            self._slots[conn.owner] -= 1
            raise
        try:
            ep = Episode(episode_id, await self._number(agent), character, ascension, self.seed_fn(), game)
            await asyncio.to_thread(self.store.begin, ep, agent, self.settings.build_id)
        except BaseException as exc:
            # Not registered yet, so nothing else would close this harness or give its slot back.
            self._slots[conn.owner] -= 1
            await asyncio.to_thread(close_game, game, episode_id)
            if not isinstance(exc, Exception):
                raise
            log.exception("could not set up a game")
            await conn.error("internal")
            return
        conn.episodes[episode_id], conn.locks[episode_id] = ep, asyncio.Lock()
        async with conn.locks[episode_id]:
            await asyncio.to_thread(ep.start)
            await self._next(conn, ep)

    async def _act(self, conn: _Connection, action: Any) -> None:
        if not isinstance(action, dict):
            await conn.error("bad_request")
            return
        episode_id, seq, action_id = action.get("episode"), action.get("seq"), action.get("action_id")
        ep = conn.episodes.get(str(episode_id))
        if ep is None:
            await conn.error("not_available", str(episode_id) if episode_id else None)
            return
        if not _is_int(seq) or not _is_int(action_id):
            await conn.error("bad_request", ep.episode_id)
            return
        async with conn.locks[ep.episode_id]:
            if ep.episode_id not in conn.episodes:  # finished while this action waited for the lock
                await conn.error("not_available", ep.episode_id)
                return
            code = await asyncio.to_thread(ep.apply, seq, action_id)
            if code is not None:
                await conn.error(code, ep.episode_id, seq)
            if ep.over or code in (None, "rejected"):
                await self._next(conn, ep)  # a game that failed is ended (void), not left open

    async def _next(self, conn: _Connection, ep: Episode) -> None:
        if not ep.over:
            try:
                # Built off the loop: scrubbing a full combat board is the hottest work per decision.
                decision = await asyncio.to_thread(ep.decision, self.settings.decision_deadline_ms)
            except Exception:
                log.exception("could not build the decision for %s", ep.episode_id)
                ep.outcome = "void"  # ended, not left open with the client waiting on it
            else:
                await conn.send({"type": "decisions", "decisions": [decision]})
                return
        await self._end(conn, ep)

    async def _finalize(self, conn: _Connection, ep: Episode) -> None:
        """Close the game and record the result, exactly once."""
        if conn.episodes.pop(ep.episode_id, None) is None:
            return
        self._slots[conn.owner] -= 1
        conn.locks.pop(ep.episode_id, None)
        await asyncio.to_thread(ep.close)
        engine = {**self.settings.engine, "build_id": self.settings.build_id, "schema_version": SCHEMA_VERSION}
        try:
            await asyncio.to_thread(self.store.finish, ep, engine)
        except Exception:
            # The game is over either way; the row keeps no end, so it shows as unfinished, not as a result.
            log.exception("could not record the end of %s", ep.episode_id)

    async def _end(self, conn: _Connection, ep: Episode) -> None:
        await self._finalize(conn, ep)
        await conn.send({"type": "episode_end", "episode": ep.episode_id, "number": ep.number,
                         "outcome": ep.outcome, "floor": ep.floor, "act": ep.act,
                         "reveal": {"episode_key": ep.seed, "transcript": ep.transcript}})

    async def _abandon(self, conn: _Connection) -> None:
        """A client that leaves loses its open games, so quitting can't protect a streak."""
        for ep in list(conn.episodes.values()):
            lock = conn.locks.get(ep.episode_id) or asyncio.Lock()
            async with lock:
                if not ep.over:
                    ep.outcome = "loss"
                await self._finalize(conn, ep)

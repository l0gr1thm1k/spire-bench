"""M3: each game's key is fixed and committed before it starts, never stored, and revealed at its end.

A worker that dies mid-game is restored from the key and the moves made, and
the game goes on as if nothing happened; a restore that lands anywhere else
voids the game instead of handing the bot a different one.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
import zstandard
from conftest import SEASON, SECRET, key_of
from test_the_gateway_refuses_what_a_bot_may_not_do import ScriptedGame, _act, _recv, _session

from spirebench import Client, RandomAgent
from spirebench.client import GatewayError
from spirebench.gateway import server
from spirebench.gateway.keys import derive_key, harness_seed
from spirebench.gateway.store import Store, read_transcript
from spirebench.worker.headless import HarnessError


def _play_to_the_end(url):
    with _session(url) as ws:
        msg, errors = _recv(ws), []
        while msg["type"] != "episode_end":
            if msg["type"] == "errors":
                errors.append(msg)
            else:
                _act(ws, msg["decisions"][0], 0)
            msg = _recv(ws)
    return msg, errors


def _moves(store):
    path, = store.db.execute("select transcript from episodes").fetchone()
    return [line for line in read_transcript(Path(path)) if line["kind"] == "move"]


class DiesOnceGame(ScriptedGame):
    """The first worker booted dies on the second move; every worker after it is healthy."""

    booted = 0

    def __init__(self):
        super().__init__()
        DiesOnceGame.booted += 1
        self.first = DiesOnceGame.booted == 1

    def act(self, verb, args):
        if self.first and self.moves == 1:
            raise HarnessError("harness exited (code 137)")
        return super().act(verb, args)


def test_a_worker_that_dies_once_is_restored_and_the_bot_never_knows(gateway):
    url, plain = gateway(ScriptedGame)
    _play_to_the_end(url)
    DiesOnceGame.booted = 0
    url, store = gateway(DiesOnceGame)
    end, errors = _play_to_the_end(url)
    assert (end["outcome"], errors, DiesOnceGame.booted) == ("win", [], 2)
    assert _moves(store) == _moves(plain)
    assert store.db.execute("select fault from episodes").fetchone() == (None,)


class RestoresElsewhereGame(ScriptedGame):
    """The first worker dies on the second move; the next one deals a different game."""

    booted = 0

    def __init__(self):
        super().__init__()
        RestoresElsewhereGame.booted += 1
        self.worker = RestoresElsewhereGame.booted

    def _view(self):
        view = super()._view()
        if self.worker > 1:
            view["context"] = {**view["context"], "total_floor": view["context"]["total_floor"] + 10}
        return view

    def act(self, verb, args):
        if self.worker == 1 and self.moves == 1:
            raise HarnessError("harness exited (code 137)")
        return super().act(verb, args)


def test_a_restore_that_lands_on_a_different_game_voids_it(gateway):
    RestoresElsewhereGame.booted = 0
    url, store = gateway(RestoresElsewhereGame)
    end, _ = _play_to_the_end(url)
    assert (end["outcome"], end["fault"]) == ("void", "restore_diverged")
    assert store.db.execute("select outcome, fault from episodes").fetchone() == ("void", "restore_diverged")


def test_two_gateways_on_one_database_never_hand_out_the_same_number(tmp_path):
    a = Store(tmp_path / "g.sqlite", tmp_path / "t")
    b = Store(tmp_path / "g.sqlite", tmp_path / "t")
    a.claim_agent("bot", "alice", {})
    got = [store.begin(f"e{i}", SEASON, "bot", "Ironclad", 10, "v") for i, store in enumerate((a, b, a))]
    assert got == [1, 2, 3]
    assert a.begin("e3", "next-season", "bot", "Ironclad", 10, "v") == 1


def test_no_key_is_written_to_the_database_or_a_transcript(gateway):
    url, store = gateway(ScriptedGame)
    end, _ = _play_to_the_end(url)
    key = end["reveal"]["episode_key"]
    assert key == key_of("t", 1).hex()
    path, = store.db.execute("select transcript from episodes").fetchone()
    raw = zstandard.ZstdDecompressor().stream_reader(Path(path).open("rb")).read().decode()
    for text in ("\n".join(store.db.iterdump()), raw):
        assert key not in text.lower() and harness_seed(bytes.fromhex(key)).lower() not in text.lower()


def test_every_field_of_a_key_changes_it():
    base = derive_key(SECRET, "practice", SEASON, "bot", 1)
    flipped = bytes([SECRET[0] ^ 1]) + SECRET[1:]
    others = [derive_key(flipped, "practice", SEASON, "bot", 1),
              derive_key(SECRET, "ranked", SEASON, "bot", 1),
              derive_key(SECRET, "practice", "other", "bot", 1),
              derive_key(SECRET, "practice", SEASON, "bot2", 1),
              derive_key(SECRET, "practice", SEASON, "bot", 2)]
    assert base not in others and len(set(others)) == len(others)
    # Field boundaries are part of the key: moving characters between fields gives another.
    assert derive_key(SECRET, "practice", "ab", "c", 1) != derive_key(SECRET, "practice", "a", "bc", 1)
    with pytest.raises(ValueError):
        derive_key(SECRET[:16], "practice", SEASON, "bot", 1)


def test_an_old_database_that_may_hold_keys_is_refused(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("create table episodes (episode_id text primary key, episode_key text)")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="layout 0"):
        Store(tmp_path / "old.sqlite", tmp_path / "t")


def _client_run(url):
    run = Client(url, "dev").run_async(RandomAgent(3), character="ironclad", ascension=10, episodes=1,
                                       agent_version="bot")
    return asyncio.run(asyncio.wait_for(run, 10))


def _rewrite_sends(monkeypatch, rewrite):
    send = server._Connection.send

    async def patched(self, msg):
        msg = rewrite(msg)
        if msg is not None:
            await send(self, msg)

    monkeypatch.setattr(server._Connection, "send", patched)


def test_the_client_refuses_a_reveal_that_does_not_match_the_commitment(gateway, monkeypatch):
    def forge(msg):
        if msg["type"] == "episode_end":
            msg = {**msg, "reveal": {**msg["reveal"], "episode_key": "00" * 32}}
        return msg

    _rewrite_sends(monkeypatch, forge)
    url, _ = gateway(ScriptedGame)
    with pytest.raises(GatewayError, match="commit"):
        _client_run(url)


def test_the_client_refuses_a_game_that_never_committed_to_a_key(gateway, monkeypatch):
    _rewrite_sends(monkeypatch, lambda msg: None if msg["type"] == "episode_start" else msg)
    url, _ = gateway(ScriptedGame)
    with pytest.raises(GatewayError, match="commit"):
        _client_run(url)


def test_the_client_accepts_an_honest_reveal(gateway):
    url, _ = gateway(ScriptedGame)
    ends = _client_run(url)
    assert ends[0]["reveal"]["episode_key"] == key_of("bot", 1).hex()

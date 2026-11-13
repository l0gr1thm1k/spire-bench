"""The gateway's refusals, against a scripted game so no real game is needed.

Each test plants the violation and checks the fixed error code comes back.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from websockets.sync.client import connect

from spirebench.gateway.store import read_transcript
from spirebench.worker.headless import HarnessError

HELLO = {"type": "hello", "token": "dev", "client_version": "test", "protocol_versions": [0]}
START = {"type": "start", "agent_version": "t", "character": "ironclad", "ascension": 10, "key": "practice",
         "count": 1}


class ScriptedGame:
    """An event with two options. Option 1 is refused by the game; option 0 moves on. Three moves win."""

    def __init__(self) -> None:
        self.moves = 0

    def _view(self):
        if self.moves >= 3:
            return {"type": "decision", "decision": "game_over", "victory": True, "context": {"total_floor": 3}}
        return {"type": "decision", "decision": "event_choice", "context": {"act": 1, "total_floor": self.moves},
                "options": [{"index": 0}, {"index": 1}], "rng": {"run_seed": 1}}

    def start_run(self, character, ascension, seed):
        return self._view()

    def act(self, verb, args):
        if args.get("option_index") == 1:
            return {"type": "error", "message": "refused"}
        self.moves += 1
        return self._view()

    def combat_actions(self):
        return []

    def close(self):
        pass


@contextmanager
def _session(url):
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        assert json.loads(ws.recv())["type"] == "welcome"
        ws.send(json.dumps(START))
        yield ws


def _recv(ws):
    return json.loads(ws.recv(timeout=10))


def _act(ws, decision, action_id, seq=None):
    seq = decision["seq"] if seq is None else seq
    ws.send(json.dumps({"type": "actions", "actions": [
        {"episode": decision["episode"], "seq": seq, "action_id": action_id}]}))


def test_a_wrong_token_is_turned_away(gateway):
    url, _ = gateway(ScriptedGame)
    with connect(url) as ws:
        ws.send(json.dumps({**HELLO, "token": "not-a-token"}))
        assert _recv(ws) == {"type": "errors", "errors": [{"code": "rejected"}]}


def test_an_action_id_off_the_list_is_illegal_and_the_decision_stays_open(gateway):
    url, _ = gateway(ScriptedGame)
    with _session(url) as ws:
        d = _recv(ws)["decisions"][0]
        _act(ws, d, 99)
        assert _recv(ws)["errors"][0]["code"] == "illegal_action"
        _act(ws, d, 0)  # the same decision still takes a legal move
        assert _recv(ws)["decisions"][0]["seq"] == d["seq"] + 1


def test_an_answer_to_an_old_decision_is_a_bad_request(gateway):
    url, _ = gateway(ScriptedGame)
    with _session(url) as ws:
        d = _recv(ws)["decisions"][0]
        _act(ws, d, 0)
        _recv(ws)
        _act(ws, d, 0, seq=d["seq"])  # seq already answered
        assert _recv(ws)["errors"][0]["code"] == "bad_request"


def test_a_move_the_game_refuses_is_dropped_and_asked_again(gateway):
    url, _ = gateway(ScriptedGame)
    with _session(url) as ws:
        d = _recv(ws)["decisions"][0]
        _act(ws, d, 1)
        assert _recv(ws)["errors"][0]["code"] == "rejected"
        again = _recv(ws)["decisions"][0]
        assert again["seq"] == d["seq"] + 1
        assert [a["option_index"] for a in again["legal_actions"]] == [0]


def test_a_finished_game_is_scored_and_its_transcript_kept(gateway):
    url, store = gateway(ScriptedGame)
    with _session(url) as ws:
        msg = _recv(ws)
        while msg["type"] != "episode_end":
            _act(ws, msg["decisions"][0], 0)
            msg = _recv(ws)
    assert (msg["outcome"], msg["number"], len(msg["reveal"]["transcript"])) == ("win", 1, 3)
    path, outcome = store.db.execute("select transcript, outcome from episodes").fetchone()
    lines = read_transcript(Path(path))
    assert outcome == "win" and [x["kind"] for x in lines] == ["header", "move", "move", "move", "end"]
    assert lines[0]["episode_key"] == msg["reveal"]["episode_key"]


def test_a_bot_that_leaves_mid_game_loses_it(gateway):
    url, store = gateway(ScriptedGame)
    with _session(url) as ws:
        _recv(ws)
    for _ in range(100):  # the gateway notices the close on its own loop
        row = store.db.execute("select outcome from episodes").fetchone()
        if row and row[0]:
            break
        time.sleep(0.05)
    assert row == ("loss",)


class CrashingGame(ScriptedGame):
    def act(self, verb, args):
        raise HarnessError("harness exited (code 134)")


def test_a_game_whose_harness_dies_is_ended_as_void_not_left_open(gateway):
    url, store = gateway(CrashingGame)
    with _session(url) as ws:
        d = _recv(ws)["decisions"][0]
        _act(ws, d, 0)
        assert _recv(ws)["errors"][0]["code"] == "internal"
        end = _recv(ws)
    assert (end["type"], end["outcome"]) == ("episode_end", "void")
    assert store.db.execute("select outcome from episodes").fetchone() == ("void",)


class SlowStartGame(ScriptedGame):
    """Boots only when the test lets it, so a second start can arrive while the first is booting."""

    booting = threading.Event()
    release = threading.Event()

    def __init__(self):
        SlowStartGame.booting.set()
        assert SlowStartGame.release.wait(10)
        super().__init__()


def test_starts_sent_back_to_back_cannot_open_more_games_than_the_limit(gateway):
    SlowStartGame.booting.clear()
    SlowStartGame.release.clear()
    url, store = gateway(SlowStartGame, max_in_flight=2)
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        _recv(ws)
        ws.send(json.dumps({**START, "count": 2}))
        assert SlowStartGame.booting.wait(10)
        ws.send(json.dumps({**START, "count": 1}))  # arrives while the first two are still booting
        first = _recv(ws)
        SlowStartGame.release.set()
        assert first == {"type": "errors", "errors": [{"code": "not_available"}]}
        assert {_recv(ws)["type"], _recv(ws)["type"]} == {"decisions"}
    assert store.db.execute("select count(*) from episodes").fetchone() == (2,)


def test_a_token_cannot_play_under_an_agent_version_another_owner_has(gateway):
    url, store = gateway(ScriptedGame, tokens={"alice-token": "alice", "bob-token": "bob"})
    with connect(url) as ws:
        ws.send(json.dumps({**HELLO, "token": "alice-token"}))
        _recv(ws)
        ws.send(json.dumps({**START, "agent_version": "alice-bot"}))
        assert _recv(ws)["type"] == "decisions"
    with connect(url) as ws:
        ws.send(json.dumps({**HELLO, "token": "bob-token"}))
        _recv(ws)
        ws.send(json.dumps({**START, "agent_version": "alice-bot"}))  # bob would extend alice's streak
        assert _recv(ws) == {"type": "errors", "errors": [{"code": "rejected"}]}
        ws.send(json.dumps({**START, "agent_version": "bob-bot"}))
        assert _recv(ws)["type"] == "decisions"
    owners = dict(store.db.execute("select agent_version, owner from agent_versions").fetchall())
    assert owners == {"alice-bot": "alice", "bob-bot": "bob"}


def test_more_connections_do_not_buy_an_owner_more_games(gateway):
    url, store = gateway(ScriptedGame, max_in_flight=2)
    with connect(url) as first, connect(url) as second:
        for ws in (first, second):
            ws.send(json.dumps(HELLO))
            _recv(ws)
        first.send(json.dumps({**START, "count": 2}))
        assert {_recv(first)["type"], _recv(first)["type"]} == {"decisions"}
        second.send(json.dumps(START))  # same owner, new connection
        assert _recv(second) == {"type": "errors", "errors": [{"code": "not_available"}]}
    # Both connections closed: their games ended, so the slots are free again.
    for _ in range(100):
        if store.db.execute("select count(*) from episodes where outcome is null").fetchone() == (0,):
            break
        time.sleep(0.05)
    with _session(url) as ws:
        assert _recv(ws)["type"] == "decisions"


def test_the_gateway_caps_games_across_all_owners(gateway):
    url, _ = gateway(ScriptedGame, tokens={"a": "alice", "b": "bob"}, max_games=1)
    with connect(url) as alice, connect(url) as bob:
        for ws, token in ((alice, "a"), (bob, "b")):
            ws.send(json.dumps({**HELLO, "token": token}))
            _recv(ws)
        alice.send(json.dumps({**START, "agent_version": "alice-bot"}))
        assert _recv(alice)["type"] == "decisions"
        bob.send(json.dumps({**START, "agent_version": "bob-bot"}))
        assert _recv(bob) == {"type": "errors", "errors": [{"code": "not_available"}]}


def test_a_boolean_is_not_accepted_where_a_number_is(gateway):
    url, store = gateway(ScriptedGame)
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        _recv(ws)
        for bad in ({"count": True}, {"ascension": True}, {"metadata": "not an object"}):
            ws.send(json.dumps({**START, **bad}))
            assert _recv(ws) == {"type": "errors", "errors": [{"code": "bad_request"}]}, bad
        ws.send(json.dumps(START))
        d = _recv(ws)["decisions"][0]
        _act(ws, d, True)
        assert _recv(ws)["errors"][0]["code"] == "bad_request"
    assert store.db.execute("select count(*) from episodes").fetchone() == (1,)


def _unbootable():
    raise ValueError("the harness printed something that isn't JSON")


def test_a_game_that_fails_to_boot_in_an_unexpected_way_is_reported_not_left_hanging(gateway):
    url, _ = gateway(_unbootable)
    with _session(url) as ws:
        assert _recv(ws) == {"type": "errors", "errors": [{"code": "internal"}]}


class UnclosableGame(ScriptedGame):
    def close(self):
        raise RuntimeError("the harness would not stop")


def test_a_harness_that_will_not_close_still_has_its_result_recorded(gateway):
    url, store = gateway(UnclosableGame)
    with _session(url) as ws:
        for _ in range(3):
            _act(ws, _recv(ws)["decisions"][0], 0)
        assert _recv(ws)["outcome"] == "win"
    assert store.db.execute("select outcome from episodes").fetchone() == ("win",)


def test_a_result_that_cannot_be_written_still_frees_the_game_slot(gateway):
    url, store = gateway(ScriptedGame, max_in_flight=1)

    def disk_full(*args):
        raise OSError("no space left on device")

    store.finish = disk_full
    with _session(url) as ws:
        for _ in range(3):
            _act(ws, _recv(ws)["decisions"][0], 0)
        assert _recv(ws)["type"] == "episode_end"
        ws.send(json.dumps(START))
        assert _recv(ws)["type"] == "decisions"  # the slot came back
    assert store.db.execute("select outcome from episodes order by number").fetchall() == [(None,), (None,)]


class TrackedGame(ScriptedGame):
    closed = 0

    def close(self):
        TrackedGame.closed += 1


def test_a_start_that_fails_after_the_game_boots_is_reported_and_gives_its_harness_and_slot_back(gateway):
    TrackedGame.closed = 0
    url, store = gateway(TrackedGame, max_in_flight=1)
    begin = store.begin

    def locked(*args):
        raise RuntimeError("database is locked")

    store.begin = locked
    with _session(url) as ws:
        assert _recv(ws) == {"type": "errors", "errors": [{"code": "internal"}]}
        assert TrackedGame.closed == 1
        store.begin = begin
        ws.send(json.dumps(START))
        assert _recv(ws)["type"] == "decisions"  # the slot came back


class SurprisingGame(ScriptedGame):
    def act(self, verb, args):
        raise KeyError("a bug in the harness wrapper, not a harness error")


def test_a_game_that_fails_in_an_unexpected_way_is_ended_as_void_not_left_open(gateway):
    url, store = gateway(SurprisingGame)
    with _session(url) as ws:
        _act(ws, _recv(ws)["decisions"][0], 0)
        assert _recv(ws)["errors"][0]["code"] == "internal"
        end = _recv(ws)
    assert (end["type"], end["outcome"]) == ("episode_end", "void")


def test_starts_are_admitted_in_the_order_they_arrived(gateway):
    url, store = gateway(ScriptedGame, max_in_flight=1)
    claim, calls = store.claim_agent, []

    def slow_first_claim(agent, *args):
        calls.append(agent)
        if len(calls) == 1:
            time.sleep(0.3)  # the first start's claim is slow; the second must still wait its turn
        return claim(agent, *args)

    store.claim_agent = slow_first_claim
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        _recv(ws)
        ws.send(json.dumps({**START, "agent_version": "first"}))
        ws.send(json.dumps({**START, "agent_version": "second"}))
        got = [_recv(ws)["type"], _recv(ws)["type"]]
    assert sorted(got) == ["decisions", "errors"]
    assert store.db.execute("select agent_version from episodes").fetchall() == [("first",)]

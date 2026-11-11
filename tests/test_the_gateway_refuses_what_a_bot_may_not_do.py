"""The gateway's refusals, against a scripted game so no real game is needed.

Each test plants the violation and checks the fixed error code comes back.
"""

from __future__ import annotations

import json
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
    def __init__(self):
        time.sleep(0.3)  # a harness process takes a while to boot
        super().__init__()


def test_starts_sent_back_to_back_cannot_open_more_games_than_the_limit(gateway):
    url, store = gateway(SlowStartGame, max_in_flight=2)
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        _recv(ws)
        ws.send(json.dumps({**START, "count": 2}))
        ws.send(json.dumps({**START, "count": 1}))  # arrives while the first two are still booting
        first = _recv(ws)
        assert first == {"type": "errors", "errors": [{"code": "not_available"}]}
        assert {_recv(ws)["type"], _recv(ws)["type"]} == {"decisions"}
    assert store.db.execute("select count(*) from episodes").fetchone() == (2,)

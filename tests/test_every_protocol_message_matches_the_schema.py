"""The schema accepts well-formed messages and rejects a planted bad one."""

import json

import jsonschema
import pytest
from test_the_gateway_refuses_what_a_bot_may_not_do import HELLO, START, ScriptedGame
from websockets.sync.client import connect

from spirebench.protocol import load_schema

SCHEMA = load_schema()

GOOD = [
    {"type": "hello", "token": "dev", "client_version": "0.1.0", "protocol_versions": [0]},
    {"type": "start", "agent_version": "a", "character": "silent", "ascension": 10, "key": "practice", "count": 4},
    {"type": "actions", "actions": [{"episode": "e-1", "seq": 0, "action_id": 2}]},
    {"type": "errors", "errors": [{"episode": "e-1", "seq": 0, "code": "illegal_action"}]},
    {"type": "episode_start", "episode": "e-1", "number": 1, "commit": "ab" * 32},
    {"type": "episode_end", "episode": "e-1", "number": 1, "outcome": "void", "floor": 3, "act": 1,
     "fault": "restore_diverged"},
]

BAD = [
    {"type": "start", "agent_version": "a", "character": "watcher", "ascension": 10, "key": "practice", "count": 1},
    {"type": "errors", "errors": [{"code": "the draw pile is Bash, Strike"}]},  # errors carry codes only
    {"type": "actions", "actions": [{"episode": "e-1", "seq": 0, "action_id": 2, "debug": "set_player"}]},
    {"type": "telemetry"},
    {"type": "episode_start", "episode": "e-1", "number": 1, "commit": "a seed, not a commitment"},
    {"type": "episode_start", "episode": "e-1", "number": 1, "commit": "ab" * 32, "episode_key": "cd" * 32},
]


def test_the_schema_is_itself_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("msg", GOOD)
def test_a_well_formed_message_passes(msg):
    jsonschema.validate(msg, SCHEMA)


@pytest.mark.parametrize("msg", BAD)
def test_a_planted_bad_message_fails(msg):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(msg, SCHEMA)


def test_every_message_a_gateway_sends_in_a_whole_game_passes(gateway):
    url, _ = gateway(ScriptedGame)
    with connect(url) as ws:
        ws.send(json.dumps(HELLO))
        sent = [json.loads(ws.recv(timeout=10))]
        ws.send(json.dumps(START))
        while sent[-1]["type"] != "episode_end":
            sent.append(json.loads(ws.recv(timeout=10)))
            if sent[-1]["type"] == "decisions":
                d = sent[-1]["decisions"][0]
                ws.send(json.dumps({"type": "actions", "actions": [
                    {"episode": d["episode"], "seq": d["seq"], "action_id": 0}]}))
    assert [m["type"] for m in sent[:3]] == ["welcome", "episode_start", "decisions"]
    for msg in sent:
        jsonschema.validate(msg, SCHEMA)

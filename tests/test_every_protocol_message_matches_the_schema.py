"""The schema accepts well-formed messages and rejects a planted bad one."""

import jsonschema
import pytest

from spirebench.protocol import load_schema

SCHEMA = load_schema()

GOOD = [
    {"type": "hello", "token": "dev", "client_version": "0.1.0", "protocol_versions": [0]},
    {"type": "start", "agent_version": "a", "character": "silent", "ascension": 10, "key": "practice", "count": 4},
    {"type": "actions", "actions": [{"episode": "e-1", "seq": 0, "action_id": 2}]},
    {"type": "errors", "errors": [{"episode": "e-1", "seq": 0, "code": "illegal_action"}]},
]

BAD = [
    {"type": "start", "agent_version": "a", "character": "watcher", "ascension": 10, "key": "practice", "count": 1},
    {"type": "errors", "errors": [{"code": "the draw pile is Bash, Strike"}]},  # errors carry codes only
    {"type": "actions", "actions": [{"episode": "e-1", "seq": 0, "action_id": 2, "debug": "set_player"}]},
    {"type": "telemetry"},
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

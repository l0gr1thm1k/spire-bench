"""Protocol v0: the messages between the library and the gateway (contract B).

``schema/protocol-v0.json`` is the source of truth. This module holds the
constants both sides share, so neither has to spell them out by hand.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 0
SCHEMA_VERSION = 0  # the observation format; not frozen until M4

CHARACTERS = {"ironclad": "Ironclad", "silent": "Silent", "defect": "Defect",
              "regent": "Regent", "necrobinder": "Necrobinder"}

PRACTICE_KEY = "practice"  # the only key in v0; no game is scored before M3 slice 4 and M5
MAX_ASCENSION = 10
MAX_MESSAGE_BYTES = 2**22  # both ends set this; a bigger observation would drop the connection

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "protocol-v0.json"

Message = dict[str, Any]


def encode(msg: Message) -> str:
    return json.dumps(msg, separators=(",", ":"))


def decode(text: str | bytes) -> Message:
    msg = json.loads(text)
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ValueError("a message is a JSON object with a string 'type'")
    return msg


def commitment(data: bytes) -> str:
    """SHA-256 of a key or secret: published first, so the revealed value can be checked against it."""
    return hashlib.sha256(data).hexdigest()


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())

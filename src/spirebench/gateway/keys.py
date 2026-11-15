"""Episode keys: 256-bit HMACs of the season secret, fixed before each game starts.

A key is derived from ``(season, agent_version, number)`` and is never
stored: the gateway rederives it when it needs it, and keeps only its
commitment. Once the season's secret is published, anyone can rederive every
key and replay every game.

    python -m spirebench.gateway.keys new-secret data/season-0.secret
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from spirebench.protocol import commitment

SECRET_BYTES = 32
KEY_LABEL_VERSION = 1


def _field(value: str | int) -> bytes:
    raw = str(value).encode()
    return len(raw).to_bytes(4, "big") + raw  # length-prefixed, so no two field lists encode alike


def derive_key(secret: bytes, kind: str, season: str, agent_version: str, number: int) -> bytes:
    """The key of one game. ``kind`` is the start's key (``practice``), so kinds never share a key."""
    if len(secret) != SECRET_BYTES:
        raise ValueError(f"a season secret is {SECRET_BYTES} bytes, not {len(secret)}")
    message = b"".join(_field(f) for f in (f"spirebench/{kind}/v{KEY_LABEL_VERSION}", season, agent_version,
                                           number))
    return hmac.new(secret, message, hashlib.sha256).digest()


def load_secret(path: Path) -> bytes:
    """A season secret file holds 64 hex digits."""
    text = path.read_text().strip()
    try:
        secret = bytes.fromhex(text)
    except ValueError:
        raise ValueError(f"{path} does not hold hex digits") from None
    if len(secret) != SECRET_BYTES:
        raise ValueError(f"{path} holds {len(secret)} bytes; a season secret is {SECRET_BYTES}")
    return secret


def new_secret(path: Path) -> str:
    """Write a fresh secret, readable by its owner only. Returns its commitment, which is published."""
    secret = secrets.token_bytes(SECRET_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite a season's secret
    with os.fdopen(fd, "w") as f:
        f.write(secret.hex() + "\n")
    return commitment(secret)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-secret", help="write a new season secret and print its commitment").add_argument(
        "path", type=Path)
    args = ap.parse_args()
    print(f"season commitment (publish this): {new_secret(args.path)}")


if __name__ == "__main__":
    main()

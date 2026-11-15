"""Check a transcript by playing it again: python -m spirebench.replay TRANSCRIPT --key HEX

The key comes from ``episode_end`` (owners), from the season secret
(operators), or from the published secret once the season is over (anyone).
The tool refuses a key that doesn't match the transcript's commit and an
engine that isn't the one the game ran on, naming the field. Then it replays
every move, compares what each decision showed, and prints ``identical`` or
the first move that differs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from spirebench.gateway.replay_core import replay, view_digest
from spirebench.gateway.store import read_transcript
from spirebench.protocol import SCHEMA_VERSION, commitment
from spirebench.worker.headless import Game, HeadlessGame, engine_identity


def check_engine(recorded: dict[str, Any], local: dict[str, Any]) -> list[str]:
    """The engine fields that differ between the transcript and this machine."""
    return sorted(k for k in recorded.keys() | local.keys() if recorded.get(k) != local.get(k))


def verify(lines: list[dict[str, Any]], key: bytes, game: Game) -> str:
    """Replay a transcript's moves under ``key``: ``identical``, or where it first differs."""
    header, end = lines[0], lines[-1]
    moves = [line for line in lines if line["kind"] == "move"]
    got = replay(game, header["character"], header["ascension"], key, moves)
    if got.diverged_at is not None:
        move = moves[got.diverged_at]
        return f"differs at move {got.diverged_at} (seq {move['seq']}): {got.reason}"
    if view_digest(got.view) != end["digest"]:
        return f"differs after the last move ({len(moves)} moves): the final screen is not the recorded one"
    return "identical"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--key", required=True, help="the episode key, as 64 hex digits")
    args = ap.parse_args()
    lines = read_transcript(args.transcript)
    header = lines[0]
    try:
        key = bytes.fromhex(args.key)
    except ValueError:
        sys.exit("--key is not hex")
    if commitment(key) != header["commit"]:
        print(f"refused: that key is not the one this game committed to ({header['commit']})", file=sys.stderr)
        sys.exit(2)
    recorded = header["engine"]
    local = {**engine_identity(recorded["build_id"]), "schema_version": SCHEMA_VERSION}
    if wrong := check_engine(recorded, local):
        print(f"refused: this machine's engine differs from the transcript's in {', '.join(wrong)}", file=sys.stderr)
        sys.exit(2)
    game = HeadlessGame(game_version=recorded["build_id"])
    try:
        result = verify(lines, key, game)
    finally:
        game.close()
    print(result)
    sys.exit(0 if result == "identical" else 1)


if __name__ == "__main__":
    main()

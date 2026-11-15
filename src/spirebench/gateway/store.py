"""Where the gateway keeps results: SQLite for episodes, one transcript file each.

A transcript is zstd-compressed JSON lines: a header with the episode key and
the engine the game ran on, then one line per move, then the end. Because the
harness replays a seed exactly, the key and the moves are enough to regenerate
every observation.

Every method blocks on disk, so the gateway calls them off its event loop. One
lock keeps those threads from interleaving on the shared connection, and each
write is a transaction (``with self.db``): it commits whole or rolls back, so a
failed write can't be committed later by someone else's.
"""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import zstandard

from spirebench.gateway.episode import Episode

_SCHEMA = """
create table if not exists agent_versions (
    agent_version text primary key,
    owner         text not null,
    metadata      text not null,
    first_seen    real not null
);
create table if not exists episodes (
    episode_id    text primary key,
    agent_version text not null references agent_versions(agent_version),
    number        integer not null,
    character     text not null,
    ascension     integer not null,
    build_id      text not null,
    episode_key   text not null,
    started_at    real not null,
    ended_at      real,
    outcome       text,
    floor         integer,
    act           integer,
    actions       integer,
    transcript    text,
    unique (agent_version, number)
);
"""


class Store:
    def __init__(self, db_path: Path, transcript_dir: Path) -> None:
        self.transcript_dir = transcript_dir
        transcript_dir.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript("pragma journal_mode = wal; pragma synchronous = normal;" + _SCHEMA)
        self._lock = threading.Lock()

    def claim_agent(self, agent_version: str, owner: str, metadata: dict[str, Any]) -> bool:
        """Register an agent version to its first owner. False if someone else owns it."""
        with self._lock, self.db:
            return self._claim_agent(agent_version, owner, metadata)

    def _claim_agent(self, agent_version: str, owner: str, metadata: dict[str, Any]) -> bool:
        self.db.execute("insert or ignore into agent_versions values (?, ?, ?, ?)",
                        (agent_version, owner, json.dumps(metadata, sort_keys=True), time.time()))
        row = self.db.execute("select owner from agent_versions where agent_version = ?", (agent_version,)).fetchone()
        return bool(row and row[0] == owner)

    def next_number(self, agent_version: str) -> int:
        with self._lock:
            row = self.db.execute("select coalesce(max(number), 0) from episodes where agent_version = ?",
                                  (agent_version,)).fetchone()
        return int(row[0]) + 1

    def begin(self, ep: Episode, agent_version: str, build_id: str) -> None:
        with self._lock, self.db:
            self.db.execute(
                "insert into episodes (episode_id, agent_version, number, character, ascension, build_id,"
                " episode_key, started_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (ep.episode_id, agent_version, ep.number, ep.character, ep.ascension, build_id, ep.seed, time.time()))

    def finish(self, ep: Episode, engine: dict[str, Any]) -> Path:
        """Write the transcript and the result. ``engine`` names the build it ran on (``build_id`` and hashes)."""
        path = self.transcript_dir / f"{ep.episode_id}.jsonl.zst"
        write_transcript(path, ep, engine)
        with self._lock, self.db:
            self.db.execute("update episodes set ended_at = ?, outcome = ?, floor = ?, act = ?, actions = ?,"
                            " transcript = ? where episode_id = ?",
                            (time.time(), ep.outcome, ep.floor, ep.act, len(ep.transcript), str(path), ep.episode_id))
        return path


def write_transcript(path: Path, ep: Episode, engine: dict[str, Any]) -> None:
    lines: list[dict[str, Any]] = [{"kind": "header", "episode": ep.episode_id, "episode_key": ep.seed,
                                    "character": ep.character, "ascension": ep.ascension,
                                    "build_id": engine["build_id"], "engine": engine}]
    lines += [{"kind": "move", **move} for move in ep.transcript]
    lines.append({"kind": "end", "outcome": ep.outcome, "floor": ep.floor, "act": ep.act})
    raw = "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines).encode()
    path.write_bytes(zstandard.ZstdCompressor(level=10).compress(raw))


def read_transcript(path: Path) -> list[dict[str, Any]]:
    with zstandard.ZstdDecompressor().stream_reader(path.open("rb")) as reader:
        return [json.loads(line) for line in io.TextIOWrapper(reader, encoding="utf-8")]

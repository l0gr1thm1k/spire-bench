"""``python -m spirebench.replay``: refuses the wrong key or engine by name, then finds the first move that differs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import zstandard
from test_keys_are_committed_hidden_and_restored_from import _play_to_the_end
from test_the_gateway_refuses_what_a_bot_may_not_do import ScriptedGame

from spirebench import replay
from spirebench.gateway.store import read_transcript


@pytest.fixture
def recorded(gateway):
    """(transcript path, revealed key) of one finished scripted game."""
    url, store = gateway(ScriptedGame)
    end, _ = _play_to_the_end(url)
    path, = store.db.execute("select transcript from episodes").fetchone()
    return Path(path), end["reveal"]["episode_key"]


def _rewrite(path, change):
    lines = read_transcript(path)
    change(lines)
    raw = "".join(json.dumps(line) + "\n" for line in lines).encode()
    path.write_bytes(zstandard.ZstdCompressor().compress(raw))


def _main(monkeypatch, capsys, path, key, local_engine):
    monkeypatch.setattr(sys, "argv", ["replay", str(path), "--key", key])
    monkeypatch.setattr(replay, "engine_identity", lambda build_id: local_engine)
    monkeypatch.setattr(replay, "HeadlessGame", lambda game_version: ScriptedGame())
    with pytest.raises(SystemExit) as stop:
        replay.main()
    out = capsys.readouterr()
    return stop.value.code, out.out + out.err


def _engine(path):
    return {k: v for k, v in read_transcript(path)[0]["engine"].items() if k != "schema_version"}


def test_the_right_key_on_the_right_engine_replays_identical(recorded, monkeypatch, capsys):
    path, key = recorded
    assert _main(monkeypatch, capsys, path, key, _engine(path)) == (0, "identical\n")


def test_a_key_that_is_not_the_committed_one_is_refused(recorded, monkeypatch, capsys):
    path, _ = recorded
    code, out = _main(monkeypatch, capsys, path, "00" * 32, _engine(path))
    assert code == 2 and "not the one this game committed to" in out


def test_a_different_engine_is_refused_by_field(recorded, monkeypatch, capsys):
    path, key = recorded
    code, out = _main(monkeypatch, capsys, path, key, {**_engine(path), "harness_dll_sha256": "other"})
    assert code == 2 and "harness_dll_sha256" in out


def test_one_changed_move_is_named(recorded):
    path, key = recorded

    def second_move_was_refused_one(lines):
        lines[2]["action"]["option_index"] = 1

    _rewrite(path, second_move_was_refused_one)
    result = replay.verify(read_transcript(path), bytes.fromhex(key), ScriptedGame())
    assert result == f"differs at move 1 (seq {read_transcript(path)[2]['seq']}): the game refused the recorded move"


def test_a_changed_screen_is_named(recorded):
    path, key = recorded

    def shown_something_else(lines):
        lines[3]["digest"] = "0" * 64

    _rewrite(path, shown_something_else)
    result = replay.verify(read_transcript(path), bytes.fromhex(key), ScriptedGame())
    assert result.startswith("differs at move 2 ") and "decision differs" in result

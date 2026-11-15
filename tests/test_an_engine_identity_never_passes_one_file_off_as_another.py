"""A transcript's engine header records a missing file as missing, never as the hash of a neighbor."""

from __future__ import annotations

import subprocess

from spirebench.worker import headless


def test_a_missing_shipped_dll_is_recorded_as_none_not_as_the_patched_one(tmp_path, monkeypatch):
    lib = tmp_path / "lib" / "v0.111.0"
    lib.mkdir(parents=True)
    (lib / "sts2.dll").write_bytes(b"patched")
    monkeypatch.setattr(headless, "PYCLI", tmp_path)
    engine = headless.engine_identity("v0.111.0")
    assert engine["game_dll_sha256"] is None
    assert len(engine["patched_game_dll_sha256"]) == 64
    assert engine["harness_dll_sha256"] is None


def test_the_engine_says_whether_the_harness_checkout_is_edited(monkeypatch):
    def git_says(code):
        return lambda *args: subprocess.CompletedProcess(args, code, "", "")

    for code, dirty in ((0, False), (1, True), (128, None)):  # git diff --quiet: same, differs, failed
        monkeypatch.setattr(headless, "_git", git_says(code))
        assert headless._harness_dirty() is dirty
    monkeypatch.setattr(headless, "_git", lambda *args: None)  # no git at all
    assert headless._harness_dirty() is None

"""A game exception the harness prints on stderr, instead of answering with, is counted.

The harness reports exceptions the game swallows only on stderr. A run that
reads clean while the game threw is not a clean run, so every HeadlessGame
counts them, with spire-mentor's own matcher.
"""

import io
import stat

from spirebench.worker import headless
from spirebench.worker.headless import HeadlessGame, WorldErrors, _world_error_matcher


def _drained(text: str) -> WorldErrors:
    game = HeadlessGame.__new__(HeadlessGame)  # the drain only, no process
    game.world_errors, game._is_world_error, game._stderr_file = WorldErrors(), _world_error_matcher(), None
    game._drain_stderr(io.StringIO(text))
    return game.world_errors


def test_an_exception_line_is_counted_once_and_its_stack_frames_are_not():
    errors = _drained("System.NullReferenceException: Object reference not set\n"
                      "   at MegaCrit.Sts2.Core.Foo.Bar(Exception e)\n"
                      "   at MegaCrit.Sts2.Core.Baz()\n")
    assert errors.count == 1
    assert errors.sample == ["System.NullReferenceException: Object reference not set"]


def test_ordinary_log_lines_are_not_errors():
    assert _drained("[INFO] Act 1 = OVERGROWTH (act1=roll)\nLocManager loaded <ValidationErrors>\n").count == 0


def test_the_kept_sample_is_capped_but_the_count_is_exact():
    errors = _drained("System.InvalidOperationException: boom\n" * 50)
    assert errors.count == 50 and len(errors.sample) == 20


# A stand-in for the harness: it reports one exception at boot and one on the
# way out, right before it exits, which is where a lost trailing line would hide.
FAKE_HARNESS = """#!/bin/sh
echo 'System.InvalidOperationException: at boot' >&2
echo '{"type": "ready", "game_version": "v0.111.0"}'
while read line; do
  case "$line" in
    *quit*) echo '{"type": "bye"}'; echo 'System.NullReferenceException: on the way out' >&2; exit 0 ;;
    *) echo '{"type": "error", "message": "fake"}' ;;
  esac
done
"""


def test_a_real_process_has_every_error_counted_by_the_time_it_is_closed(tmp_path, monkeypatch):
    fake = tmp_path / "fake_harness"
    fake.write_text(FAKE_HARNESS)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    dll = tmp_path / "Sts2Headless.dll"
    dll.write_text("")
    # The command line is [dotnet, dll]; the fake ignores the dll argument.
    monkeypatch.setenv("DOTNET", str(fake))
    monkeypatch.setattr(headless, "harness_dll", lambda *a, **k: dll)
    log = tmp_path / "stderr.log"
    with HeadlessGame(stderr_path=log) as game:
        pass
    assert game.world_errors.complete
    assert game.world_errors.count == 2, game.world_errors.sample
    assert "on the way out" in log.read_text()

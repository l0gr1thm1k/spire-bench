"""Drive one Sts2Headless process over its JSON-lines pipe.

Sts2Headless is Thomas Friedel's headless game harness (spire-mentor, MIT),
vendored at ``vendor/spire-mentor``. It runs the game's own ``sts2.dll`` with
Godot stubbed out. One command goes in as a JSON line on stdin, and one JSON
line comes back on stdout. Its log output goes to stderr.

This module is the worker side of contract A in the build plan. It does no
filtering: the raw state it returns still carries hidden fields (the ``rng``
block, the ordered draw pile), and the gateway's allow-list must remove them
before anything reaches a bot.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol

REPO = Path(__file__).resolve().parents[3]
PYCLI = REPO / "vendor" / "spire-mentor" / "sts2-pycli"
DEFAULT_GAME_VERSION = "v0.111.0"
MAX_SELECT_CHOICES = 64  # enough for a random player; the gateway will need the full rule
FAST_START_ENV = {"DOTNET_TieredPGO": "0", "DOTNET_TC_QuickJitForLoops": "1", "DOTNET_ReadyToRun": "1"}
ERROR_SAMPLE_MAX = 20  # the count is exact; only the kept lines are capped


def _world_error_matcher() -> Callable[[str], bool]:
    """spire-mentor's own test for a stderr line that reports a game exception.

    The harness reports exceptions the game swallows only on stderr, never in a
    reply, so this is the one place they show. Reused rather than copied, so
    both projects count the same lines.
    """
    path = str(PYCLI / "python")
    if path not in sys.path:
        sys.path.append(path)
    from sts2_client import is_world_error_line  # type: ignore[import-not-found]

    matcher: Callable[[str], bool] = is_world_error_line
    return matcher


def harness_dll(game_version: str = DEFAULT_GAME_VERSION, config: str = "Release") -> Path:
    return PYCLI / "src" / "Sts2Headless" / "bin" / game_version / config / "net9.0" / "Sts2Headless.dll"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", "-C", str(PYCLI.parent), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _harness_commit() -> str | None:
    out = _git("rev-parse", "HEAD")
    if out is None or out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _harness_dirty() -> bool | None:
    """Whether a tracked harness file differs from the commit, so a local edit isn't passed off as it."""
    out = _git("diff", "--quiet", "HEAD", "--")
    return None if out is None or out.returncode > 1 else out.returncode == 1


def engine_identity(game_version: str = DEFAULT_GAME_VERSION, config: str = "Release") -> dict[str, Any]:
    """What a game ran on, so a transcript can still be checked after the harness changes.

    The game DLL is hashed as shipped (``sts2.dll.original``, which the setup
    script keeps) and as patched for headless play (``sts2.dll``). A file
    that isn't there is recorded as None, never as another file's hash.
    """
    lib = PYCLI / "lib" / game_version
    return {
        "build_id": game_version,
        "game_dll_sha256": _sha256(lib / "sts2.dll.original"),
        "patched_game_dll_sha256": _sha256(lib / "sts2.dll"),
        "harness_dll_sha256": _sha256(harness_dll(game_version, config)),
        "harness_commit": _harness_commit(),
        "harness_dirty": _harness_dirty(),
    }


def dotnet_binary() -> str:
    return os.environ.get("DOTNET", str(Path.home() / ".dotnet" / "dotnet"))


class HarnessError(RuntimeError):
    """The harness answered with an error, or its process went away."""


@dataclass
class WorldErrors:
    """Game exceptions the harness printed on stderr instead of answering with."""

    count: int = 0
    sample: list[str] = field(default_factory=list)
    complete: bool = False  # True once stderr was read to its end; until then the count may be short
    sink_error: str | None = None  # why copying stderr to stderr_path stopped, if it did


@dataclass
class CpuTimes:
    """CPU seconds the harness process has used, read from /proc."""

    user: float
    system: float

    @property
    def total(self) -> float:
        return self.user + self.system


class HeadlessGame:
    """One harness process. It plays one run at a time and can start another."""

    def __init__(self, game_version: str = DEFAULT_GAME_VERSION, config: str = "Release",
                 stderr_path: Path | None = None, timeout_s: float = 60.0) -> None:
        self.timeout_s, self.timed_out = timeout_s, False
        dll = harness_dll(game_version, config)
        if not dll.exists():
            raise FileNotFoundError(f"harness not built: {dll}")
        env = dict(os.environ)
        env.setdefault("DOTNET_ROOT", str(Path(dotnet_binary()).parent))
        env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
        # Measured in M0 (docs/M0.md): with a fresh process per game these cut
        # CPU per action from 6.5 to 5.4 ms and startup from 1.25 to 1.15 s.
        for key, value in FAST_START_ENV.items():
            env.setdefault(key, value)
        self.world_errors = WorldErrors()
        self._is_world_error = _world_error_matcher()
        self._stderr_file = open(stderr_path, "a", encoding="utf-8") if stderr_path else None
        try:
            self.proc = subprocess.Popen(
                [dotnet_binary(), str(dll)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=env, cwd=str(PYCLI), errors="replace",
            )
        except BaseException:
            if self._stderr_file is not None:
                self._stderr_file.close()
            raise
        assert self.proc.stderr is not None
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self.proc.stderr,), daemon=True)
        self._stderr_thread.start()
        try:
            ready = self._read()
            if ready.get("type") != "ready":
                raise HarnessError(f"expected ready, got {ready.get('type')!r}")
            self.game_version = ready.get("game_version")
            if self.game_version != game_version:
                raise HarnessError(f"harness links {self.game_version}, asked for {game_version}")
        except BaseException:
            self.close()  # never leave a half-started process behind
            raise

    def _drain_stderr(self, stream: IO[str]) -> None:
        """Read stderr to the end: count game exceptions, and copy it to stderr_path if given.

        This thread owns the copy: it closes the file itself, so nothing else
        writes to or closes it mid-line. A failed copy stops the copy, never
        the reading, because a stderr nobody reads fills and blocks the harness.
        """
        sink = self._stderr_file
        try:
            for line in stream:
                if self._is_world_error(line):
                    self.world_errors.count += 1
                    if len(self.world_errors.sample) < ERROR_SAMPLE_MAX:
                        self.world_errors.sample.append(line.rstrip()[:300])
                if sink is not None:
                    try:
                        sink.write(line)
                    except (OSError, ValueError) as exc:
                        self.world_errors.sink_error = f"{type(exc).__name__}: {exc}"
                        sink.close()
                        sink = None
            self.world_errors.complete = True
        finally:
            if sink is not None:
                sink.close()

    def _kill_hung(self) -> None:
        self.timed_out = True
        self.proc.kill()

    def _read(self) -> dict[str, Any]:
        """Read one reply, killing the process if none comes within timeout_s."""
        timer = threading.Timer(self.timeout_s, self._kill_hung)
        timer.daemon = True
        timer.start()
        try:
            return self._read_reply()
        finally:
            timer.cancel()

    def _read_reply(self) -> dict[str, Any]:
        # The harness also prints some log lines on stdout (for example its
        # Sentry initializer), so skip anything that isn't a JSON object.
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                if self.timed_out:
                    raise HarnessError(f"harness timed out after {self.timeout_s:.0f} s")
                raise HarnessError(f"harness exited (code {self.proc.poll()})")
            if line.startswith("{"):
                return json.loads(line)

    def send(self, cmd: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()
        return self._read()

    def start_run(self, character: str, ascension: int, seed: str) -> dict[str, Any]:
        # act1 "roll": the seed picks act 1 as the game does. Without it the
        # harness always starts in Overgrowth (Thomas Friedel's review of PR 1).
        # interactive_potion_swap: a potion reward on a full belt is a decision,
        # as in the real game, rather than being dropped (one of cygn's fixes).
        return self.send({"cmd": "start_run", "character": character, "ascension": ascension,
                          "seed": seed, "act1": "roll", "interactive_potion_swap": True})

    def act(self, verb: str, args: dict[str, Any]) -> dict[str, Any]:
        cmd: dict[str, Any] = {"cmd": "action", "action": verb}
        # A null argument reaches the harness as an empty string, which it
        # rejects. Leave it out instead, as spire-mentor's own client does.
        present = {k: v for k, v in args.items() if v is not None}
        if present:
            cmd["args"] = present
        return self.send(cmd)

    def combat_actions(self) -> list[tuple[str, dict[str, Any]]]:
        reply = self.send({"cmd": "legal_actions"})
        return [(a["action"], {k: v for k, v in a.items() if k != "action"})
                for a in reply.get("actions") or []]

    def cpu_times(self) -> CpuTimes:
        """Process CPU time, children included (the dotnet host runs in-process)."""
        fields = Path(f"/proc/{self.proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
        tick = os.sysconf("SC_CLK_TCK")
        # fields[11..14] are utime, stime, cutime, cstime (stat fields 14-17).
        utime, stime, cutime, cstime = (int(x) for x in fields[11:15])
        return CpuTimes(user=(utime + cutime) / tick, system=(stime + cstime) / tick)

    def peak_rss_mb(self) -> float:
        """The process's peak resident memory so far (VmHWM), in MiB."""
        for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
        return float("nan")

    def close(self) -> None:
        try:
            if self.proc.poll() is None and not self.timed_out:
                self.send({"cmd": "quit"})
        except (HarnessError, BrokenPipeError, OSError, json.JSONDecodeError):
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=10)
            # The process is gone, so stderr ends and the drain finishes; if it
            # somehow hasn't, world_errors.complete stays False and says so.
            self._stderr_thread.join(timeout=10)

    def __enter__(self) -> HeadlessGame:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


Choice = tuple[str, dict[str, Any]]


class Game(Protocol):
    """What the gateway needs from a game: HeadlessGame, or a scripted stand-in in tests."""

    def start_run(self, character: str, ascension: int, seed: str) -> dict[str, Any]: ...
    def act(self, verb: str, args: dict[str, Any]) -> dict[str, Any]: ...
    def combat_actions(self) -> list[Choice]: ...
    def close(self) -> None: ...


def run_floor(view: dict[str, Any]) -> int | None:
    """The floor counted across the whole run (``total_floor``), not the per-act ``floor``."""
    floor = (view.get("context") or {}).get("total_floor")
    return int(floor) if floor else None


def run_act(view: dict[str, Any]) -> int | None:
    act = (view.get("context") or {}).get("act") or view.get("act")
    return int(act) if act else None


def _options(view: dict[str, Any]) -> list[Choice]:
    # Event options carry is_locked; rest-site options carry is_enabled.
    out = [("choose_option", {"option_index": o["index"]}) for o in view.get("options") or []
           if not o.get("is_locked") and o.get("is_enabled", True)]
    if view.get("decision") == "rest_site" and view.get("can_leave"):
        out.append(("leave_room", {}))
    return out


def _map(view: dict[str, Any]) -> list[Choice]:
    return [("select_map_node", {"col": c["col"], "row": c["row"]}) for c in view.get("choices") or []]


def _card_reward(view: dict[str, Any]) -> list[Choice]:
    out = [("select_card_reward", {"card_index": c["index"]}) for c in view.get("cards") or []]
    if view.get("can_skip", True):
        out.append(("skip_card_reward", {}))
    return out


def _card_select(view: dict[str, Any]) -> list[Choice]:
    idxs = [c["index"] for c in view.get("cards") or []]
    min_sel = int(view.get("min_select") or 0)
    max_sel = int(view.get("max_select") or max(min_sel, 1))
    sizes = range(max(min_sel, 1), min(max_sel, len(idxs)) + 1)
    combos = itertools.chain.from_iterable(itertools.combinations(idxs, n) for n in sizes)
    out: list[Choice] = [("select_cards", {"indices": ",".join(str(i) for i in combo)})
                         for combo in itertools.islice(combos, MAX_SELECT_CHOICES)]
    if min_sel == 0:
        out.append(("skip_select", {}))
    return out


def _potion_swap(view: dict[str, Any]) -> list[Choice]:
    return [("potion_swap", {"discard_slot": o.get("discard_slot")}) for o in view.get("options") or []]


def _bundle(view: dict[str, Any]) -> list[Choice]:
    return [("select_bundle", {"bundle_index": b["index"]}) for b in view.get("bundles") or []]


def _belt_is_full(player: dict[str, Any]) -> bool:
    slots = player.get("potion_slots")
    return slots is not None and len(player.get("potions") or []) >= int(slots)


def _shop(view: dict[str, Any]) -> list[Choice]:
    player = view.get("player") or {}
    gold = int(player.get("gold") or 0)
    kinds = [("cards", "buy_card", "card_index"), ("relics", "buy_relic", "relic_index")]
    if not _belt_is_full(player):
        kinds.append(("potions", "buy_potion", "potion_index"))
    out: list[Choice] = []
    for kind, verb, key in kinds:
        out.extend((verb, {key: item["index"]}) for item in view.get(kind) or []
                   if item.get("is_stocked", True) and int(item.get("cost") or 0) <= gold)
    removal = view.get("card_removal_cost")
    if removal is not None and int(removal) <= gold:
        out.append(("remove_card", {}))
    out.append(("leave_room", {}))  # leaving the shop is always allowed
    return out


_SCREENS = {
    "event_choice": _options, "rest_site": _options, "map_select": _map, "card_reward": _card_reward,
    "card_select": _card_select, "potion_swap": _potion_swap, "bundle_select": _bundle, "shop": _shop,
}


def legal_choices(game: Game, view: dict[str, Any]) -> list[Choice]:
    """Every move the player may make on this screen, as (verb, args) pairs.

    In combat the harness lists the moves itself. Elsewhere they are read off
    the decision payload. Adapted from spire-mentor's ``run_parity.enumerate_choices``
    (MIT). Differences: the shop reads ``cost`` and ``is_stocked``, which is
    what the harness sends, offers card removal, and offers no potion on a full
    belt; a rest-site option needs ``is_enabled``. Potions can't yet be used or
    discarded outside combat, because the harness doesn't support it. A multi-card pick
    lists at most MAX_SELECT_CHOICES selections, which is enough for a random
    player but not for the gateway.
    """
    dec = str(view.get("decision") or view.get("type"))
    if dec == "combat_play":
        return game.combat_actions()
    screen = _SCREENS.get(dec)
    return screen(view) if screen else []

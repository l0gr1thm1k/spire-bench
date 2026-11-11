# spire-bench

A shared benchmark for Slay the Spire 2 bots. Bots play the real game on a
referee they don't control, with dice nobody can predict, and every result can
be replayed and checked after the season.

Status: M1. A bot written against the library plays the real game through
the gateway on localhost, and every game's result and transcript are saved.
See [docs/M1.md](docs/M1.md) to try it, and [docs/M0.md](docs/M0.md) for what
a run costs.

```python
from spirebench import Agent, Client

class MyBot(Agent):
    def act(self, decision):
        return 0  # an index into decision["legal_actions"], a legal action, or matching fields

Client("ws://127.0.0.1:8765", token="dev").run(
    MyBot(), character="ironclad", ascension=10, episodes=10, agent_version="my-bot-v1")
```

**Caveat: how faithful the harness is, is still being measured.** It runs the
game's own code, headless, with the game's test mode on. Replaying recorded
human runs through it, 146 of 235 runs at A5 and above stay in sync to the end;
the rest are not traced yet, and no fault in the harness is confirmed so far.
Known gaps: rewards are taken automatically, and potions can't be used or
discarded outside combat. See [Fidelity](docs/PLAN.md#fidelity-does-the-harness-play-the-real-game).

- Proposal: [A Shared Benchmark for Slay the Spire 2 Bots](https://docs.google.com/document/d/17AXtsWNrIExi4oN7_xp_76xpGA9BN0kVhv9Z0MxMuCg/edit)
- Build plan: [docs/PLAN.md](docs/PLAN.md)

## Layout

| Path | What it is |
|---|---|
| `src/spirebench/client/` | The library bot authors use: `Agent`, `Client`, action resolution |
| `src/spirebench/gateway/` | The referee's front door: token check, observation filter, episodes, transcripts |
| `src/spirebench/worker/` | Runs one game per process by driving the headless game over a pipe |
| `schema/` | The protocol and observation JSON Schema, the source of truth for every client |
| `vendor/spire-mentor/` | Thomas Friedel's spire-mentor (MIT), pinned; we build its `Sts2Headless` harness |

## You need your own copy of the game

This repository contains no game files and never will. The headless harness is
built from the DLLs in your own Steam install (see
`vendor/spire-mentor/sts2-pycli/setup.sh`). This project is not affiliated
with Mega Crit.

## Credits

The referee design is cygn's Spire Bench. The headless game harness is Thomas
Friedel's (spire-mentor). Ideas from Cooper, maxoley, Panacea and ullnorse.

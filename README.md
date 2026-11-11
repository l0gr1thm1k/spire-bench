# spire-bench

A shared benchmark for Slay the Spire 2 bots. Bots play the real game on a
referee they don't control, with dice nobody can predict, and every result can
be replayed and checked after the season.

Status: M0. The headless game runs from your own install and plays full runs
through a Python wrapper, and what a run costs is measured in
[docs/M0.md](docs/M0.md). Bots cannot connect yet; that is M1.

- Proposal: [A Shared Benchmark for Slay the Spire 2 Bots](https://docs.google.com/document/d/17AXtsWNrIExi4oN7_xp_76xpGA9BN0kVhv9Z0MxMuCg/edit)
- Build plan: [docs/PLAN.md](docs/PLAN.md)

## Layout

| Path | What it is |
|---|---|
| `src/spirebench/client/` | The library bot authors use: `Agent`, `Client`, `LocalReferee`, action resolution |
| `src/spirebench/gateway/` | The referee's front door: accounts, deadlines, allow-list filter, transcripts, scoring |
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

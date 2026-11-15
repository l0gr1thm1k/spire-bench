# Harness fidelity: the screens a player sees

The pinned spire-mentor harness differed from the game on several screens:
it took rewards for the player, kept potions shut outside combat, sent no
map, stripped cards in the draw pile, and capped card selections at 64
combinations. This change makes each of those screens behave as the
game's own screen does. [PLAN.md](PLAN.md) has the status table.

## How it is built, on any game version

The harness changes are one patch, `harness/patches/01-fidelity.patch`,
against the pinned `vendor/spire-mentor` source. The submodule stays
untouched. `bash harness/build_harness.sh <version>` copies the pinned
source to `build/harness-src/<version>/`. It applies every patch with
fuzz 0, so a hunk that doesn't apply exactly stops the build, and then it
builds against that version's game DLLs. `build_keyed.sh` keys that build.

The patch calls the game's own code (its reward chain, its potion checks,
its map) instead of copying the rules. A version difference is one
`#if GAME_V0111` on the member that was renamed. Today that is only the
belt gate, `CanRemovePotions`, which v0.111.0 renamed to
`CanUseOrRemovePotions`. Both v0.107.1 and v0.111.0 build and pass the
whole suite. A new version needs a patch that applies, a build, and the
suite. A rename shows up as a compile error, not as a silent difference.

## What changed

**The map.** Every `map_select` carries the whole act map, the one the game
shows the player:

- every node's `col`, `row`, `type` and `children`;
- `start`, `boss`, `second_boss` (a coordinate or null), `current` and
  `visited`.

A node's `type` is its map icon. The game sets `PointType` only while it
generates the map or loads it from a save, and never when a room is entered.
So a `?` node reads `Unknown` before and after its room is rolled. This was
checked on both versions. `second_boss` is a location, never an encounter.
Only the current act's map is sent, as PLAN's visibility rules require.

**Cards keep what is on them.** A card outside a fight's hand (draw pile,
discard, deck, rewards, selections) is serialized the same way as a card in
hand: enchantment, affliction, cost and upgrade included.

**Selections are made one card at a time.** A `card_select` offers a
`pick_card` per remaining card, then `select_cards` once enough cards are
picked, and `skip_select` when the game allows it. The client sends the
whole set as one `select_cards`. A choice of 10 from 40 is 41 moves, not
C(40, 10) combinations, and no selection is truncated.

**Rewards are the player's.** Winning a fight opens the room's own rewards
screen, the one the game builds: room rewards, extra rewards and relic
hooks. The player takes rewards one at a time or leaves with some untaken.
A potion on a full belt stays on the screen, as in the game. A reward type
the build doesn't know is still listed by its class name and can be taken.
An event-nested fight goes back into its event afterwards.

**Chests are the player's.** In a treasure room the player opens the chest
or not, then takes its relic or not. The game rolls the relic on entering
the room, so leaving changes no later draw.

**Potions outside combat.** On every screen where the game lets you touch
the belt (map, event, rest site, shop, rewards, treasure), each potion
carries `can_use` and `can_discard`. These come from the game's own checks:
the potion's usage, the belt gate and the potion's own condition. One gap
remains. A potion whose condition reads the scene (Foul Potion at a
merchant) has no scene headless, so it reads as not usable there.

**Events already had a way out.** An STS2 event's Leave is one of its own
options, and the harness lists every option. No change was needed. A test
pins this.

**An unknown screen is named.** A decision nobody has mapped raises
`UnknownScreen`, naming the screen and the room. The game is voided with
the fault `unknown_screen` instead of being read as a screen with no moves.

## The 146 of 235 replay figure

That figure is upstream's. Thomas replayed SpireCodex runs through the host
with his own recorder and replayer. Neither the corpus nor the replayer is
public, and SpireCodex has no run-download API, so it can't be re-measured
here. Two of the host gaps the replays could have tripped on, auto-taken
rewards and potions outside combat, are now closed. The figure stays in the
README as upstream's until the replays are re-run on this build.

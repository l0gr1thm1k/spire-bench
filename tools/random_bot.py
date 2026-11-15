"""Play games against a gateway with the random bot.

  uv run python -m spirebench.gateway &                    # in one terminal
  uv run python tools/random_bot.py --episodes 3          # in another
"""

from __future__ import annotations

import argparse

from spirebench import Client, RandomAgent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--token", default="dev")
    ap.add_argument("--character", default="ironclad")
    ap.add_argument("--ascension", type=int, default=10)
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--in-flight", type=int, default=1)
    ap.add_argument("--seed", default=None, help="seed for the bot's own choices")
    a = ap.parse_args()
    ends = Client(a.url, a.token).run(RandomAgent(a.seed), character=a.character, ascension=a.ascension,
                                      episodes=a.episodes, in_flight=a.in_flight, agent_version="random-v0",
                                      metadata={"policy": "uniform random"})
    for end in ends:
        print(f"#{end['number']} {end['episode']}: {end['outcome']} on floor {end['floor']} (act {end['act']}), "
              f"{len(end['reveal']['transcript'])} moves, key {end['reveal']['episode_key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

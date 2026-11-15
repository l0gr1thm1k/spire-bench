"""Run a gateway on this machine:

    python -m spirebench.gateway.keys new-secret data/season-0.secret   # once per season
    python -m spirebench.gateway --season 0 --season-secret data/season-0.secret
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from spirebench.gateway.keys import load_secret
from spirebench.gateway.server import Gateway, Settings
from spirebench.gateway.store import Store
from spirebench.worker.headless import DEFAULT_GAME_VERSION, HeadlessGame, engine_identity


async def _main(args: argparse.Namespace, secret: bytes) -> None:
    store = Store(args.data / "gateway.sqlite", args.data / "transcripts")
    tokens = {tok.partition(":")[0]: tok.partition(":")[2] or tok for tok in args.token}
    settings = Settings(season=args.season, season_secret=secret, build_id=args.game_version, tokens=tokens,
                        engine=engine_identity(args.game_version))
    gateway = Gateway(store, lambda: HeadlessGame(game_version=args.game_version), settings)
    server = await gateway.serve(args.host, args.port)
    logging.info("gateway on ws://%s:%d, game %s, data in %s", args.host, args.port, args.game_version, args.data)
    await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", required=True, help="the season every key on this gateway belongs to")
    ap.add_argument("--season-secret", type=Path, required=True,
                    help="file holding the season secret (made by python -m spirebench.gateway.keys new-secret)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--data", type=Path, default=Path("data/gateway"))
    ap.add_argument("--token", action="append", default=None,
                    help="accepted token as TOKEN or TOKEN:OWNER; repeat for more (default: dev)")
    args = ap.parse_args()
    args.token = args.token or ["dev"]
    try:
        secret = load_secret(args.season_secret)
    except (OSError, ValueError) as exc:
        sys.exit(f"cannot read the season secret: {exc}\n"
                 f"make one with: python -m spirebench.gateway.keys new-secret {args.season_secret}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args, secret))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

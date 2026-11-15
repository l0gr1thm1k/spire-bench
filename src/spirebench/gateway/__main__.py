"""Run a gateway on this machine: python -m spirebench.gateway --port 8765"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from spirebench.gateway.server import Gateway, Settings
from spirebench.gateway.store import Store
from spirebench.worker.headless import DEFAULT_GAME_VERSION, HeadlessGame, engine_identity


async def _main(args: argparse.Namespace) -> None:
    store = Store(args.data / "gateway.sqlite", args.data / "transcripts")
    tokens = {tok.partition(":")[0]: tok.partition(":")[2] or tok for tok in args.token}
    settings = Settings(build_id=args.game_version, tokens=tokens, engine=engine_identity(args.game_version))
    gateway = Gateway(store, lambda: HeadlessGame(game_version=args.game_version), settings)
    server = await gateway.serve(args.host, args.port)
    logging.info("gateway on ws://%s:%d, game %s, data in %s", args.host, args.port, args.game_version, args.data)
    await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--data", type=Path, default=Path("data/gateway"))
    ap.add_argument("--token", action="append", default=None,
                    help="accepted token as TOKEN or TOKEN:OWNER; repeat for more (default: dev)")
    args = ap.parse_args()
    args.token = args.token or ["dev"]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

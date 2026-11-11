"""Run a gateway on this machine: python -m spirebench.gateway --port 8765"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from spirebench.gateway.server import Gateway, Settings
from spirebench.gateway.store import Store
from spirebench.worker.headless import HeadlessGame


async def _main(args: argparse.Namespace) -> None:
    store = Store(args.data / "gateway.sqlite", args.data / "transcripts")
    settings = Settings(build_id=args.game_version, tokens=frozenset(args.token))
    gateway = Gateway(store, lambda: HeadlessGame(game_version=args.game_version), settings)
    server = await gateway.serve(args.host, args.port)
    logging.info("gateway on ws://%s:%d, game %s, data in %s", args.host, args.port, args.game_version, args.data)
    await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--game-version", default="v0.111.0")
    ap.add_argument("--data", type=Path, default=Path("data/gateway"))
    ap.add_argument("--token", action="append", default=None, help="accepted token; repeat for more (default: dev)")
    args = ap.parse_args()
    args.token = args.token or ["dev"]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""A gateway running on a background event loop, for tests that talk to it over a real socket."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from spirebench.gateway.server import Gateway, Settings
from spirebench.gateway.store import Store


@contextmanager
def _running(store: Store, factory: Callable[[], Any], settings: Settings) -> Iterator[str]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box: dict[str, Any] = {}

    async def start() -> None:
        box["server"] = await Gateway(store, factory, settings).serve("127.0.0.1", 0)
        ready.set()

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(start(), loop)
    assert ready.wait(10), "gateway did not start"
    port = box["server"].sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        async def stop() -> None:
            box["server"].close()
            await box["server"].wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


@pytest.fixture
def gateway(tmp_path: Path):
    """gateway(factory, **settings) -> (url, store), torn down after the test."""
    stack = []

    def make(factory: Callable[[], Any], **settings: Any) -> tuple[str, Store]:
        store = Store(tmp_path / "gateway.sqlite", tmp_path / "transcripts")
        cm = _running(store, factory, Settings(**settings))
        stack.append(cm)
        return cm.__enter__(), store

    yield make
    for cm in reversed(stack):
        cm.__exit__(None, None, None)

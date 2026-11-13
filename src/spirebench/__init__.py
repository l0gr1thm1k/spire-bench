"""spire-bench: a shared, verifiable benchmark for Slay the Spire 2 bots."""

from spirebench._version import __version__
from spirebench.client import Agent, Client, RandomAgent

__all__ = ["Agent", "Client", "RandomAgent", "__version__"]

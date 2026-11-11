"""spire-bench: a shared, verifiable benchmark for Slay the Spire 2 bots."""

__version__ = "0.1.0"

from spirebench.client import Agent, Client, RandomAgent  # noqa: E402  (after __version__, which client reads)

__all__ = ["Agent", "Client", "RandomAgent", "__version__"]

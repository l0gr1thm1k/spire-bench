# Protocol and observation schema

`protocol-v0.json` is the source of truth for every message between the
library and the gateway (JSON Schema, draft 2020-12). Clients in other
languages can generate their types from it.

The observation inside a decision is described only as an object for now. Its
format is not frozen until real bots have used it (M4).

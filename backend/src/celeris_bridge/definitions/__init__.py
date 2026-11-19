"""
Shipped agent definitions: the documents that make an agent type an agent type.

Each file here is one published definition in plain JSON — the thing a template
version stores. They live beside the code, not in the database, so that a build
can be checked against them in CI: ``test_shipped_definitions`` runs every one
through :func:`celeris_bridge.domain.agent.definition_schema.validate_definition`.

Seeding copies a definition into a template version. From then on it is data a
platform admin edits, and this file is only its starting point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["DEFINITIONS_DIR", "load_definition", "shipped_definitions"]

DEFINITIONS_DIR = Path(__file__).parent


def load_definition(key: str) -> dict[str, Any]:
    """The shipped definition named ``key``, as plain JSON.

    Returned parsed but unvalidated: validating is the caller's job, and the
    caller that matters is the publish path, which has the ability registry.
    """
    raw = json.loads((DEFINITIONS_DIR / f"{key}.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f'definition "{key}" is not an object')
    return raw


def shipped_definitions() -> list[str]:
    """Every definition in this package, by key."""
    return sorted(path.stem for path in DEFINITIONS_DIR.glob("*.json"))

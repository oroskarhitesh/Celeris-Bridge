"""
Ports: the interfaces the domain states and the outside world satisfies.

They live here, with the domain, rather than next to the adapters that
implement them — the domain says what it needs and an adapter comes to meet it.
Only ports something actually uses exist; the rest arrive when there is a
caller for them.
"""

from celeris_bridge.ports.knowledge import (
    EmbeddingModel,
    KnowledgeScope,
    PassageHit,
    PassageIndex,
)

__all__ = ["EmbeddingModel", "KnowledgeScope", "PassageHit", "PassageIndex"]

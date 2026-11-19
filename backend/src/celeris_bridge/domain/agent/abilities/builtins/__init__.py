"""
The abilities this build has, one file each.

``builtin_abilities()`` is the registry composition hands around. Adding an
ability means writing a file here and naming it in the list below — there is no
discovery by import side effect, so what the build offers is readable in one
place, and an ability that is written but not yet wanted simply is not listed.
"""

from celeris_bridge.domain.agent.abilities.builtins.book_slot import book_slot
from celeris_bridge.domain.agent.abilities.builtins.find_slots import find_slots
from celeris_bridge.domain.agent.abilities.builtins.save_visitor_details import (
    save_visitor_details,
)
from celeris_bridge.domain.agent.abilities.builtins.search_knowledge import (
    search_knowledge,
)
from celeris_bridge.domain.agent.abilities.builtins.suggest_replies import (
    suggest_replies,
)
from celeris_bridge.domain.agent.abilities.registry import AbilityRegistry

__all__ = [
    "book_slot",
    "builtin_abilities",
    "find_slots",
    "save_visitor_details",
    "search_knowledge",
    "suggest_replies",
]

#: Declared in the order an agent tends to use them, which is also the order
#: they read in a prompt.
BUILTINS = (
    search_knowledge,
    save_visitor_details,
    find_slots,
    book_slot,
    suggest_replies,
)


def builtin_abilities() -> AbilityRegistry:
    """A fresh registry of every built-in ability."""
    return AbilityRegistry(BUILTINS)

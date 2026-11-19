"""
``suggest_replies`` — the tappable follow-ups under an answer.

The only ability with ``ends_turn``: it decorates an answer the model has
already written, so there is nothing left to go round for. The turn loop stops
as soon as it is called alongside some text.

Lenient in, strict out. The declared input allows up to ten suggestions of up
to 200 characters because refusing a model's list outright costs a round trip
and gets a similar list back; the handler then keeps only the ones that are
short enough and distinct. The visitor sees three buttons either way.
"""

from __future__ import annotations

from typing import Any, Mapping

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityResult,
)

__all__ = ["suggest_replies"]


async def _handler(
    arguments: Mapping[str, Any], context: AbilityContext
) -> AbilityResult:
    longest = int(context.settings["maxLength"])
    most = int(context.settings["maxSuggestions"])

    kept: list[str] = []
    seen: set[str] = set()
    for suggestion in arguments["replies"]:
        text = suggestion.strip()
        folded = text.lower()
        if not text or len(text) > longest or folded in seen:
            continue
        seen.add(folded)
        kept.append(text)
        if len(kept) >= most:
            break

    return AbilityResult(
        # Nothing useful to say to the model: it has written its answer and the
        # buttons are for the visitor. Saying how many were dropped would only
        # invite it to apologise for them.
        output={"shown": len(kept)},
        display={"quickReplies": kept},
    )


suggest_replies = Ability(
    key="suggest_replies",
    version="1.0.0",
    label="Suggest follow-ups",
    description="Shows two or three tappable follow-up questions under the agent's reply.",
    ai_description=(
        "Offer the visitor two or three very short follow-ups they can tap. Call it "
        "together with your final answer, not on its own."
    ),
    instructions=(
        "When it would help the visitor carry on, call suggest_replies with two or "
        "three follow-ups they might tap, each only a few words. Call it at most once, "
        "in the same reply as your answer — never by itself, or the visitor gets "
        "buttons and no answer."
    ),
    input={
        "replies": {
            "type": "stringList",
            "description": "Two or three very short follow-ups, a few words each.",
            "required": True,
            "maxItems": 10,
            "maxLength": 200,
        }
    },
    settings={
        "maxSuggestions": {
            "label": "How many to show",
            "type": "number",
            "integer": True,
            "min": 1,
            "max": 5,
            "default": 3,
            "editable": True,
        },
        "maxLength": {
            "label": "Characters per suggestion",
            "description": "Longer than this and it stops looking like a button.",
            "type": "number",
            "integer": True,
            "min": 10,
            "max": 80,
            "default": 40,
            "editable": True,
        },
    },
    effect="read",
    ends_turn=True,
    limits={"timeoutMs": 1_000, "maxCallsPerTurn": 1},
    handler=_handler,
)

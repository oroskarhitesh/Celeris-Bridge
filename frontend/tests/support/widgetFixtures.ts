/**
 * The shapes the *public* API sends.
 *
 * Kept apart from ``fixtures.ts`` for the same reason the widget keeps its
 * own types: these are the narrowed views a visitor gets, and a fixture file
 * that could hand the widget an admin shape is a fixture file that can hide
 * the day the widget starts reading one.
 */

import type { AgentFace, Reply } from "@/widget/types";

export function aFace(overrides: Partial<AgentFace> = {}): AgentFace {
  return {
    agentName: "Acme",
    greeting: "Hi there! Ask me anything.",
    launcher: { position: "right", text: "Chat with us" },
    templateVersion: 1,
    instanceVersion: 2,
    ...overrides,
  };
}

/**
 * One reply.
 *
 * The citation is a name and a link: no passage text, no score, no source id.
 * That is the public contract, and a fixture that carried more would let a
 * widget start showing it.
 */
export function aReply(overrides: Partial<Reply> = {}): Reply {
  return {
    conversationId: "conv_1",
    text: "We start at 29 per seat.",
    ending: "answered",
    open: true,
    sources: [{ name: "Pricing", url: "https://acme.test/pricing" }],
    quickReplies: ["Book a demo"],
    ...overrides,
  };
}

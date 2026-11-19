/**
 * The shapes the API sends, built once so a renamed field fails everywhere at
 * the same time instead of in whichever test is read first.
 */

import type { Agent } from "@/data/types/agent";
import type { CatalogueEntry } from "@/data/types/catalogue";
import type {
  ConversationSummary,
  Transcript,
  Turn,
} from "@/data/types/conversation";
import type { CrawlReport, Ingested, Source } from "@/data/types/knowledge";
import type { Lead } from "@/data/types/lead";
import type {
  Ability,
  AgentVersion,
  FieldSpec,
  Setting,
  SettingsForm,
} from "@/data/types/settings";

export function anAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent_1",
    templateKey: "marketing_v1",
    draftTemplateVersion: 1,
    label: "Acme website",
    status: "live",
    pausedReason: null,
    publishedVersion: 2,
    hasUnpublishedChanges: false,
    draftRevision: 3,
    lastPublishedAt: "2026-10-01T09:00:00Z",
    createdAt: "2026-09-20T09:00:00Z",
    deployment: {
      allowedOrigins: ["https://acme.test"],
      widgetEnabled: true,
      launcherPosition: "right",
      launcherText: "Chat with us",
      lastSeenAt: null,
      lastSeenOrigin: "",
    },
    ...overrides,
  };
}

export function aType(overrides: Partial<CatalogueEntry> = {}): CatalogueEntry {
  return {
    templateKey: "marketing_v1",
    version: 1,
    name: "Marketing agent",
    description: "Answers questions on your site and captures leads.",
    icon: "chat",
    category: "growth",
    channels: ["web_chat"],
    maxPerWorkspace: 3,
    owned: 0,
    ...overrides,
  };
}

/**
 * One setting, declaration and all.
 *
 * The spec is the interesting part: it is what the form renders from, so a
 * test that wants a dropdown gives it options rather than reaching for a
 * component that draws one.
 */
export function aSetting(overrides: Partial<Setting> = {}): Setting {
  const spec: FieldSpec = {
    label: "Greeting",
    type: "string",
    group: "Persona",
    editable: true,
    default: "Hi there!",
    maxLength: 200,
    ...overrides.spec,
  };
  return {
    key: "greeting",
    value: spec.default,
    overridden: false,
    ...overrides,
    spec,
  };
}

export function anAbility(overrides: Partial<Ability> = {}): Ability {
  return {
    key: "find_slots",
    label: "Find free times",
    enabled: true,
    editable: true,
    enabledByDefault: false,
    settings: { daysAhead: 7 },
    settingsSpec: {
      daysAhead: {
        label: "How far ahead to look",
        type: "number",
        integer: true,
        min: 1,
        max: 14,
        editable: true,
        default: 7,
      },
    },
    missing: false,
    ...overrides,
  };
}

/**
 * A whole settings form.
 *
 * ``agent`` is on it because the API sends it; the detail page reads the
 * agent from its own query instead, so a test changing one of them should
 * change the one the component under test actually uses.
 */
export function aSettingsForm(overrides: Partial<SettingsForm> = {}): SettingsForm {
  return {
    agent: anAgent(),
    name: "Marketing agent",
    description: "Answers questions on your site and captures leads.",
    templateVersion: 1,
    groups: ["Persona", "Conversation"],
    settings: [
      aSetting(),
      aSetting({
        key: "tone",
        value: "friendly",
        spec: {
          label: "Tone",
          type: "enum",
          group: "Persona",
          editable: true,
          default: "friendly",
          options: [
            { value: "friendly", label: "Friendly" },
            { value: "formal", label: "Formal" },
          ],
        },
      }),
      aSetting({
        key: "maxTurns",
        value: 20,
        spec: {
          label: "Turns before handover",
          type: "number",
          group: "Conversation",
          editable: true,
          integer: true,
          min: 4,
          max: 60,
          default: 20,
        },
      }),
      aSetting({
        key: "chatModel",
        value: "claude-opus-5",
        spec: {
          label: "Chat model",
          type: "string",
          group: "Conversation",
          // Locked: in the form so it can be read, refused in a patch.
          editable: false,
          default: "claude-opus-5",
        },
      }),
    ],
    abilities: [anAbility()],
    lockedRules: [{ id: "not_human", text: "Never claim to be a person." }],
    ...overrides,
  };
}

export function aVersion(overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    version: 2,
    templateKey: "marketing_v1",
    templateVersion: 1,
    changeSummary: "Warmer greeting",
    rolledBackFrom: null,
    publishedAt: "2026-10-01T09:00:00Z",
    publishedBy: "user_1",
    ...overrides,
  };
}

/**
 * One source, as the list sends it.
 *
 * ``status`` is passed through rather than derived here, even though the
 * backend derives it from ``passages`` and ``embeddingModel``: a test about
 * what "no text" looks like should be able to say so in one word, and the
 * rule that produces it is the backend's to test.
 */
export function aSource(overrides: Partial<Source> = {}): Source {
  return {
    sourceId: "up-1",
    name: "Pricing.pdf",
    kind: "application/pdf",
    bytesStored: 48_000,
    passages: 12,
    embeddingModel: "voyage-3",
    sourceUrl: null,
    approved: false,
    status: "indexed",
    ...overrides,
  };
}

/** The answer to an upload, and to each document a reindex touched. */
export function anIngested(overrides: Partial<Ingested> = {}): Ingested {
  return {
    sourceId: "up-1",
    sourceName: "Pricing.pdf",
    passages: 12,
    characters: 9_400,
    model: "voyage-3",
    kind: "application/pdf",
    indexed: true,
    skipped: "",
    ...overrides,
  };
}

export function aCrawlReport(overrides: Partial<CrawlReport> = {}): CrawlReport {
  return {
    startUrl: "https://acme.test/docs",
    robotsPresent: true,
    stoppedEarly: "",
    indexedPages: 2,
    passages: 9,
    pages: [
      {
        url: "https://acme.test/docs",
        depth: 0,
        title: "Docs",
        sourceId: "web-1",
        passages: 5,
        unchanged: false,
      },
      {
        url: "https://acme.test/docs/pricing",
        depth: 1,
        title: "Pricing",
        sourceId: "web-2",
        passages: 4,
        unchanged: false,
      },
    ],
    skipped: [],
    ...overrides,
  };
}

/**
 * One conversation, as the list sends it.
 *
 * ``visitorTurns`` is separate from ``turns`` and not derived here, because
 * that is how the API sends it: the total counts the agent's own replies, and
 * a test about an abandoned chat should be able to say "two turns, the
 * visitor said none of them" in one line.
 */
export function aConversation(
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
  return {
    id: "conv_1",
    instanceId: "agent_1",
    channel: "web_chat",
    turns: 4,
    visitorTurns: 2,
    startedAt: "2026-11-17T10:00:00Z",
    updatedAt: "2026-11-17T10:04:00Z",
    pageUrl: "https://acme.test/pricing",
    pageTitle: "Pricing",
    templateVersion: 1,
    instanceVersion: 2,
    inputTokens: 1_200,
    outputTokens: 340,
    preview: false,
    ...overrides,
  };
}

/** One turn. The agent's side by default: it is the one with detail on it. */
export function aTurn(overrides: Partial<Turn> = {}): Turn {
  return {
    speaker: "agent",
    text: "We start at 29 per seat.",
    at: "2026-11-17T10:01:00Z",
    ending: "answered",
    model: "claude-opus-5",
    inputTokens: 900,
    outputTokens: 120,
    calls: [],
    sources: [],
    ...overrides,
  };
}

export function aTranscript(overrides: Partial<Transcript> = {}): Transcript {
  return {
    ...aConversation(),
    turnsDetail: [
      aTurn({
        speaker: "visitor",
        text: "What does it cost?",
        model: "",
        inputTokens: 0,
        outputTokens: 0,
        ending: "",
      }),
      aTurn(),
    ],
    ...overrides,
  };
}

/**
 * One lead.
 *
 * ``details`` is a map under the keys this workspace asked for -- not name
 * and email columns -- so a test about a workspace that collects something
 * else just passes different keys.
 */
export function aLead(overrides: Partial<Lead> = {}): Lead {
  return {
    conversationId: "conv_1",
    instanceId: "agent_1",
    channel: "web_chat",
    details: { name: "Dana Fox", email: "dana@acme.test" },
    visitorType: "buyer",
    status: "qualifying",
    missing: ["phone"],
    reasons: [],
    turns: 3,
    startedAt: "2026-11-17T10:00:00Z",
    lastAt: "2026-11-17T10:06:00Z",
    pageUrl: "https://acme.test/pricing",
    openingMessage: "Do you integrate with our CRM?",
    complete: false,
    ...overrides,
  };
}

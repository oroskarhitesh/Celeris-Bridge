/**
 * The settings form, as the API generates it.
 *
 * A mirror of ``SettingsFormView``, and the shape the whole settings screen is
 * drawn from. Nothing in the presentation layer knows that ``tone`` is a
 * dropdown or that ``maxTurns`` has a floor of four: the server sends each
 * setting's declaration, and the form renders a control from it.
 *
 * That is the point of the type being this detailed. A ``FieldSpec`` is the
 * same declaration that produced the default, the validator that will judge a
 * change, and the JSON Schema the model is given, so a control built from it
 * cannot offer a value the backend is about to refuse. A hand-written form
 * would be the sixth copy of each setting's rules, and the one that disagrees.
 */

import type { Agent } from "./agent";

export type FieldType =
  | "string"
  | "enum"
  | "multiselect"
  | "boolean"
  | "number"
  | "url"
  | "color"
  | "stringList"
  | "list";

/** A row type: everything but ``list``, because nesting stops at one level. */
export type ItemType = Exclude<FieldType, "list">;

export interface Option {
  value: string;
  label: string;
}

/** One field of a ``list`` setting's row, and the base of a whole setting. */
export interface ItemSpec {
  label: string;
  description?: string;
  type: ItemType;

  /** Constraints. Each is only legal for some types, so each is optional. */
  minLength?: number;
  maxLength?: number;
  multiline?: boolean;
  pattern?: string;
  options?: Option[];
  maxItems?: number;
  min?: number;
  max?: number;
  integer?: boolean;
}

export interface FieldSpec extends Omit<ItemSpec, "type"> {
  type: FieldType;
  group?: string;
  /**
   * Whether this workspace may change it at all. A locked setting is still
   * sent -- a workspace should be able to read what its agent is set to --
   * but patching one is a ``SETTING_LOCKED`` rejection, so the form shows it
   * without a control rather than drawing one the backend will refuse.
   */
  editable: boolean;
  /** Never true in a workspace's copy: those specs are left out entirely. */
  adminOnly?: boolean;
  nullable?: boolean;
  /**
   * The value when nothing is overridden. Writing it back is how an override
   * is dropped: the backend stores a delta, so a value equal to the default
   * is not an override.
   *
   * Optional only because a null default is dropped from the JSON.
   */
  default?: unknown;

  /** ``list`` only: the shape of one row, and what makes a row unique. */
  item?: Record<string, ItemSpec>;
  uniqueBy?: string;
}

export interface Setting {
  key: string;
  spec: FieldSpec;
  value: unknown;
  /** True when this workspace set it, rather than inheriting the default. */
  overridden: boolean;
}

export interface Ability {
  key: string;
  label: string;
  enabled: boolean;
  /** Whether this workspace may switch it on or off. */
  editable: boolean;
  enabledByDefault: boolean;
  settings: Record<string, unknown>;
  settingsSpec: Record<string, FieldSpec>;
  /** The definition names it and this build does not have it. */
  missing: boolean;
}

export interface LockedRule {
  id: string;
  text: string;
}

export interface SettingsForm {
  /**
   * The agent, as of this read.
   *
   * The detail page deliberately does not use it: it reads the agent from
   * ``useAgentQuery`` so there is one copy on screen, and a mutation that
   * writes the fresh agent into that cache entry corrects everything at once.
   * Kept on the type because the API sends it.
   */
  agent: Agent;
  /** The agent type's own name and words, from the pinned template version. */
  name: string;
  description: string;
  templateVersion: number;
  /** The order to show groups in, from the definition. */
  groups: string[];
  settings: Setting[];
  abilities: Ability[];
  lockedRules: LockedRule[];
}

export interface AgentVersion {
  version: number;
  templateKey: string;
  templateVersion: number;
  changeSummary: string;
  /** Set when this version exists because someone rolled back to an older one. */
  rolledBackFrom: number | null;
  publishedAt: string | null;
  publishedBy: string | null;
}

/**
 * A publish or a rollback: the new version, and the agent as it is now.
 *
 * Both, because publishing bumps the draft revision -- a client given only
 * the version would send a stale ``expectRevision`` on its next write.
 */
export interface Published {
  version: AgentVersion;
  agent: Agent;
}

export interface Dropped {
  path: string;
  reason: string;
}

export interface UpgradePreview {
  fromVersion: number;
  toVersion: number;
  /** What the newer template version would no longer accept. */
  dropped: Dropped[];
  addedSettings: string[];
  losesNothing: boolean;
}

export interface PreviewConversation {
  conversationId: string;
  /** Always null for a preview: a draft has no published version. */
  instanceVersion: number | null;
  templateVersion: number | null;
}

export interface Citation {
  name: string;
  url: string | null;
}

export interface Reply {
  conversationId: string;
  text: string;
  /** Why the turn stopped: answered, decorated, rounds, empty, unavailable. */
  ending: string;
  /** False once ``maxTurns`` is reached, so the input box goes away. */
  open: boolean;
  sources: Citation[];
  quickReplies: string[];
}

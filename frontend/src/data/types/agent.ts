/**
 * An agent, as the API describes one.
 *
 * A mirror of ``AgentView``, and the three fields worth understanding before
 * writing any screen are the version ones:
 *
 * ``draftTemplateVersion``
 *     the agent type's version this agent is *pinned* to. It does not move
 *     when the platform publishes a newer one -- upgrading is an act.
 * ``publishedVersion``
 *     the snapshot visitors are served, or ``null`` for an agent that has
 *     never been published. A draft is invisible to the public.
 * ``draftRevision``
 *     optimistic concurrency. Every write sends the revision it read, and a
 *     mismatch is a 409 rather than a silent overwrite of somebody else's
 *     save, so any screen that mutates has to have read the agent first.
 */

export type AgentStatus = "draft" | "live" | "paused";

export interface Deployment {
  allowedOrigins: string[];
  widgetEnabled: boolean;
  launcherPosition: "left" | "right";
  launcherText: string;
  lastSeenAt: string | null;
  lastSeenOrigin: string;
}

export interface Agent {
  id: string;
  templateKey: string;
  draftTemplateVersion: number;
  label: string;
  status: AgentStatus;
  /** Only the platform sets one, and only for a pause it has to explain. */
  pausedReason: string | null;
  publishedVersion: number | null;
  hasUnpublishedChanges: boolean;
  draftRevision: number;
  lastPublishedAt: string | null;
  createdAt: string | null;
  deployment: Deployment;
}

/** A change to one ability, as a patch carries it. */
export interface AbilityChange {
  enabled?: boolean;
  settings?: Record<string, unknown>;
}

export interface CreateAgentBody {
  templateKey: string;
  label: string;
  /** Left out to pin to whatever the latest published template version is. */
  templateVersion?: number;
  settings?: Record<string, unknown>;
  abilities?: Record<string, AbilityChange>;
}

/** Everything a write needs besides its own fields. */
export interface Revised {
  id: string;
  expectRevision: number;
}

export interface AgentFilter {
  templateKey?: string;
  status?: AgentStatus;
}

/**
 * A change to the draft.
 *
 * All of it or none of it: one rejected value takes the whole request with
 * it and writes nothing, so a form never ends up half-applied. Both maps are
 * patches -- a setting not mentioned is left as it is, which is why a form
 * sends only what someone actually changed.
 */
export interface EditAgentBody extends Revised {
  settings?: Record<string, unknown>;
  abilities?: Record<string, AbilityChange>;
  label?: string;
}

/**
 * Drop overrides, so those settings follow the agent type's default again.
 *
 * A deletion rather than a write of today's default, which is the whole
 * point: a setting that has been reset follows a *better* default when the
 * platform ships one.
 */
export interface ResetAgentBody extends Revised {
  settings?: string[];
  abilities?: string[];
  everything?: boolean;
}

export interface PublishAgentBody extends Revised {
  changeSummary?: string;
}

export interface RollbackAgentBody extends Revised {
  toVersion: number;
}

/** The latest template version when ``toVersion`` is left out. */
export interface UpgradeAgentBody extends Revised {
  toVersion?: number;
}

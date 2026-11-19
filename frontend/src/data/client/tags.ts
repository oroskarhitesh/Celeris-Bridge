/**
 * Every cache tag the data layer uses, in one place.
 *
 * A tag is a promise between two files that never import each other: the
 * endpoint that *provides* it and the mutation that *invalidates* it. Spelling
 * it twice is how a list quietly stops refreshing after a delete, so the
 * strings live here and nowhere else.
 */

export const TAGS = [
  "Health",
  "Catalogue",
  "Agent",
  "AgentVersion",
  "SettingsForm",
  "Source",
  "Conversation",
  "Lead",
  "Template",
] as const;

export type Tag = (typeof TAGS)[number];

/** The sentinel id for "the whole collection". */
export const LIST = "LIST" as const;

/**
 * The agent types a workspace may build on.
 *
 * A mirror of ``CatalogueEntryView``. ``maxPerWorkspace`` and ``owned`` travel
 * together so a screen can disable a button instead of explaining a 409 after
 * the fact.
 */

export interface CatalogueEntry {
  templateKey: string;
  /** The template version a new agent would be pinned to. */
  version: number;
  name: string;
  description: string;
  icon: string;
  category: string;
  channels: string[];
  maxPerWorkspace: number;
  owned: number;
}

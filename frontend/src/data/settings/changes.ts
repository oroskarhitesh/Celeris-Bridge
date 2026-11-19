/**
 * What the unsaved draft actually changes, and the patch to send for it.
 *
 * Pure: it takes the form the server sent and the touched entries the draft
 * slice holds, and answers three questions -- what should a control show,
 * has anything really changed, and what should go on the wire.
 *
 * It exists because "touched" and "changed" are not the same thing. Someone
 * can type into a field and type it back; a row can be reordered and
 * reordered again. Comparing against the form's resolved values, rather than
 * trusting that a field was edited, is what lets the save button go dead
 * again on its own and keeps a no-op out of the request.
 *
 * Three narrowings are deliberate.
 *
 * **A patch carries only what differs.** The backend applies it as a patch,
 * so an unmentioned setting is left alone. Sending every value would also
 * work and would be wrong: it would turn a one-field edit into a write that
 * conflicts with every other one.
 *
 * **Locked settings never reach a patch.** A locked setting is in the form so
 * it can be read, and patching one is a ``SETTING_LOCKED`` rejection that
 * takes the whole request with it. Filtered here as well as not offered by
 * the form, because this is the function that decides what is sent.
 *
 * **A value equal to the default is still sent when it differs from the
 * current one.** That is how an override is dropped: the backend stores a
 * delta, so writing today's default deletes the override rather than
 * freezing the workspace on it. "Use the default" therefore needs no
 * endpoint of its own.
 */

import type { DraftState } from "@/data/store/slices/draftSlice";
import type { AbilityChange } from "@/data/types/agent";
import type { Ability, Setting, SettingsForm } from "@/data/types/settings";

/**
 * Whether two setting values are indistinguishable.
 *
 * Structural, because a list setting's value is rows of primitives and two
 * equal rows must compare equal however they were built. Unlike the Python
 * side this needs no guard against ``true == 1``: in JavaScript they are
 * already different, and the type check below is what makes that hold for
 * ``null`` and arrays too.
 */
export function same(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (typeof left !== typeof right) return false;
  if (left === null || right === null) return false;

  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    return left.length === right.length && left.every((one, at) => same(one, right[at]));
  }

  if (typeof left === "object" && typeof right === "object") {
    const theirs = right as Record<string, unknown>;
    const ours = left as Record<string, unknown>;
    const keys = Object.keys(ours);
    if (keys.length !== Object.keys(theirs).length) return false;
    return keys.every(
      (key) => key in theirs && same(ours[key], theirs[key]),
    );
  }

  return false;
}

/** What a control should show: the typed value if there is one, else the server's. */
export function valueOf(setting: Setting, draft: DraftState): unknown {
  return setting.key in draft.settings ? draft.settings[setting.key] : setting.value;
}

/** Whether this setting is one of the unsaved changes. */
export function changed(setting: Setting, draft: DraftState): boolean {
  if (!(setting.key in draft.settings)) return false;
  return !same(draft.settings[setting.key], setting.value);
}

/** Whether a control should show this setting as switched on. */
export function abilityEnabledIn(ability: Ability, draft: DraftState): boolean {
  const entry = draft.abilities[ability.key];
  return entry?.enabled ?? ability.enabled;
}

/** One of an ability's own settings, as a control should show it. */
export function abilityValueOf(
  ability: Ability,
  name: string,
  draft: DraftState,
): unknown {
  const typed = draft.abilities[ability.key]?.settings;
  return typed && name in typed ? typed[name] : ability.settings[name];
}

/**
 * Whether anything about this ability is an unsaved change.
 *
 * Gated on the same three things ``patchOf`` is gated on -- a missing
 * ability, a locked switch, a locked setting -- because a card saying
 * "unsaved" for something the request will not carry is a card that never
 * stops saying it.
 */
export function abilityChanged(ability: Ability, draft: DraftState): boolean {
  const entry = draft.abilities[ability.key];
  if (!entry || ability.missing) return false;
  if (ability.editable && entry.enabled !== undefined && entry.enabled !== ability.enabled) {
    return true;
  }
  return Object.entries(entry.settings ?? {}).some(
    ([name, value]) =>
      ability.settingsSpec[name]?.editable === true &&
      !same(value, ability.settings[name]),
  );
}

/** The patch body: every real change, and nothing else. */
export function patchOf(
  form: SettingsForm,
  draft: DraftState,
): { settings: Record<string, unknown>; abilities: Record<string, AbilityChange> } {
  const settings: Record<string, unknown> = {};
  for (const setting of form.settings) {
    // A locked setting, or one a template upgrade has since removed, is not
    // ours to send -- and either would be refused for the whole request.
    if (!setting.spec.editable) continue;
    if (changed(setting, draft)) settings[setting.key] = draft.settings[setting.key];
  }

  const abilities: Record<string, AbilityChange> = {};
  for (const ability of form.abilities) {
    const entry = draft.abilities[ability.key];
    if (!entry || ability.missing) continue;

    const change: AbilityChange = {};
    if (
      ability.editable &&
      entry.enabled !== undefined &&
      entry.enabled !== ability.enabled
    ) {
      change.enabled = entry.enabled;
    }

    const own: Record<string, unknown> = {};
    for (const [name, value] of Object.entries(entry.settings ?? {})) {
      if (ability.settingsSpec[name]?.editable !== true) continue;
      if (!same(value, ability.settings[name])) own[name] = value;
    }
    if (Object.keys(own).length) change.settings = own;

    // An ability mentioned with nothing to say would be an empty change the
    // backend has to parse and ignore.
    if (change.enabled !== undefined || change.settings) abilities[ability.key] = change;
  }

  return { settings, abilities };
}

/** How many settings and abilities the draft changes. Zero means saved. */
export function changeCount(form: SettingsForm, draft: DraftState): number {
  const patch = patchOf(form, draft);
  return Object.keys(patch.settings).length + Object.keys(patch.abilities).length;
}

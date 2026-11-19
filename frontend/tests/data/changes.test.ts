/**
 * What the draft changes, and what goes on the wire.
 *
 * The claims here are the ones a screen cannot show: that typing a value and
 * typing it back is not a change, that a locked setting never reaches a
 * patch, and that writing the default *is* the way to drop an override. Each
 * would otherwise be tested only through a button being enabled, which is the
 * same assertion with more scaffolding and a worse failure message.
 */

import { describe, expect, it } from "vitest";

import {
  abilityChanged,
  changeCount,
  patchOf,
  same,
  valueOf,
} from "@/data/settings/changes";
import type { DraftState } from "@/data/store/slices/draftSlice";

import { aSetting, aSettingsForm, anAbility } from "../support/fixtures";

function draft(overrides: Partial<DraftState> = {}): DraftState {
  return { agentId: "agent_1", settings: {}, abilities: {}, ...overrides };
}

describe("comparing values", () => {
  it("compares rows structurally, so an equal row is not a change", () => {
    expect(same([{ key: "a", on: true }], [{ key: "a", on: true }])).toBe(true);
    expect(same([{ key: "a", on: true }], [{ key: "a", on: false }])).toBe(false);
  });

  it("keeps true apart from 1 and null apart from an empty object", () => {
    expect(same(true, 1)).toBe(false);
    expect(same(null, {})).toBe(false);
    expect(same([], {})).toBe(false);
  });
});

describe("the patch", () => {
  it("carries only what differs from the value the server sent", () => {
    const form = aSettingsForm();
    // "greeting" typed and typed back; "tone" genuinely changed.
    const state = draft({ settings: { greeting: "Hi there!", tone: "formal" } });

    expect(changeCount(form, state)).toBe(1);
    expect(patchOf(form, state)).toEqual({ settings: { tone: "formal" }, abilities: {} });
  });

  it("still shows a touched value that is not a change", () => {
    // The control must draw what was typed even while the save button is
    // dead, or a field would snap back under someone's cursor.
    const setting = aSetting({ value: "Hi there!" });
    const state = draft({ settings: { greeting: "Hi" } });
    expect(valueOf(setting, state)).toBe("Hi");
  });

  it("sends the default, because that is how an override is dropped", () => {
    const form = aSettingsForm({
      settings: [aSetting({ value: "Hello you", overridden: true })],
    });
    const state = draft({ settings: { greeting: "Hi there!" } });

    // Equal to the spec's default and different from the stored value: the
    // backend drops the override when it receives it.
    expect(patchOf(form, state).settings).toEqual({ greeting: "Hi there!" });
  });

  it("never sends a locked setting", () => {
    // It is in the form so it can be read. Patching one is SETTING_LOCKED,
    // and that refusal would take every other value in the request with it.
    const form = aSettingsForm();
    const state = draft({ settings: { chatModel: "something-else" } });

    expect(patchOf(form, state).settings).toEqual({});
    expect(changeCount(form, state)).toBe(0);
  });

  it("drops a setting the pinned template version no longer has", () => {
    // An upgrade can remove a setting while an edit to it is still unsaved.
    const form = aSettingsForm({ settings: [aSetting()] });
    const state = draft({ settings: { goneAway: "value" } });

    expect(patchOf(form, state).settings).toEqual({});
  });
});

describe("abilities in a patch", () => {
  it("mentions an ability only when something about it changed", () => {
    const form = aSettingsForm();
    const state = draft({ abilities: { find_slots: { enabled: true } } });

    // Already enabled, so nothing to say.
    expect(abilityChanged(form.abilities[0]!, state)).toBe(false);
    expect(patchOf(form, state).abilities).toEqual({});
  });

  it("sends the switch and the setting separately", () => {
    const form = aSettingsForm();
    const state = draft({
      abilities: { find_slots: { enabled: false, settings: { daysAhead: 10 } } },
    });

    expect(patchOf(form, state).abilities).toEqual({
      find_slots: { enabled: false, settings: { daysAhead: 10 } },
    });
    expect(changeCount(form, state)).toBe(1);
  });

  it("will not switch an ability the agent type locks", () => {
    const form = aSettingsForm({
      abilities: [anAbility({ key: "search_knowledge", editable: false, settings: {}, settingsSpec: {} })],
    });
    const state = draft({ abilities: { search_knowledge: { enabled: false } } });

    expect(patchOf(form, state).abilities).toEqual({});
  });

  it("leaves a missing ability alone entirely", () => {
    // The build does not have it. A patch would be configuring something
    // that is not there.
    const form = aSettingsForm({ abilities: [anAbility({ missing: true })] });
    const state = draft({ abilities: { find_slots: { settings: { daysAhead: 3 } } } });

    expect(patchOf(form, state).abilities).toEqual({});
  });
});

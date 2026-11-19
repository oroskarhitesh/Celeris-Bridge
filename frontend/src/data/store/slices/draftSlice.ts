/**
 * Settings a person has changed on screen and not yet saved.
 *
 * The one piece of state in this application that is about server data and
 * still belongs in a slice. The rule is that a slice holds what the server
 * does not know, and the server does not know what somebody is halfway
 * through typing.
 *
 * It is not component state because the pieces that need it are not in one
 * subtree: a field writes it, the save bar reads whether anything is dirty
 * and sends the whole patch, and the publish panel has to refuse while it is
 * non-empty -- publishing would snapshot the saved draft, not what is on the
 * screen. Lifting it to the page would mean threading a setter through every
 * group and every control; a slice is the same thing without the thread.
 *
 * **Only touched entries are stored, and the resolved values are not copied
 * here.** Those live in the query cache, which stays the one source of what
 * the server said. A value typed and then typed back to what it was is still
 * "touched" by this slice, and :mod:`data/settings/changes` is what decides
 * it is no longer a change -- so the comparison happens against the server's
 * current answer rather than against a snapshot taken when the page opened.
 *
 * ``agentId`` is on the state rather than being a map keyed by it: two agents'
 * unsaved edits held at once would be a draft someone could save against the
 * wrong agent after navigating.
 */

import { createSlice } from "@reduxjs/toolkit";
import type { PayloadAction } from "@reduxjs/toolkit";

import type { AbilityChange } from "@/data/types/agent";

export interface DraftState {
  /** Whose edits these are. Empty when there are none. */
  agentId: string;
  settings: Record<string, unknown>;
  abilities: Record<string, AbilityChange>;
}

const initialState: DraftState = { agentId: "", settings: {}, abilities: {} };

function cleared(agentId: string): DraftState {
  return { agentId, settings: {}, abilities: {} };
}

export const draftSlice = createSlice({
  name: "draft",
  initialState,
  reducers: {
    /**
     * Say which agent is being edited.
     *
     * Dispatched by the detail page on every render of a new id. Edits for
     * another agent are dropped rather than carried over, and re-opening the
     * same agent keeps them -- a tab switch inside the page is not a reason
     * to lose what someone typed.
     */
    draftOpened(state, action: PayloadAction<string>) {
      if (state.agentId !== action.payload) return cleared(action.payload);
      return state;
    },

    settingTouched(
      state,
      action: PayloadAction<{ key: string; value: unknown }>,
    ) {
      state.settings[action.payload.key] = action.payload.value;
    },

    abilityToggled(state, action: PayloadAction<{ key: string; enabled: boolean }>) {
      const entry = (state.abilities[action.payload.key] ??= {});
      entry.enabled = action.payload.enabled;
    },

    abilitySettingTouched(
      state,
      action: PayloadAction<{ key: string; name: string; value: unknown }>,
    ) {
      const entry = (state.abilities[action.payload.key] ??= {});
      entry.settings = { ...entry.settings, [action.payload.name]: action.payload.value };
    },

    /** Throw the unsaved edits away. The next read redraws from the server. */
    draftDiscarded(state) {
      return cleared(state.agentId);
    },
  },
  selectors: {
    selectDraft: (state) => state,
  },
});

export const {
  draftOpened,
  settingTouched,
  abilityToggled,
  abilitySettingTouched,
  draftDiscarded,
} = draftSlice.actions;
export const { selectDraft } = draftSlice.selectors;

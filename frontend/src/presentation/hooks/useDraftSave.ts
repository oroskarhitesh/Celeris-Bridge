/**
 * Saving the unsaved draft, from wherever the person happens to be.
 *
 * A hook rather than a component because two panels need the same behaviour
 * and neither owns it: settings and abilities are two tabs over *one* draft,
 * and whichever one is open saves all of it. Splitting the save would mean a
 * person could change an ability, move to settings, save, and find their
 * ability change still pending -- with no indication that it had been left
 * behind.
 *
 * It also turns a refusal into per-field marks. ``OVERRIDES_REJECTED`` lists
 * every bad value with its own path (``settings.greeting``,
 * ``abilities.find_slots.settings.daysAhead``), so the caller gets a lookup
 * by path instead of parsing prose -- and the whole request was refused, so
 * nothing is half-saved while those marks are on screen.
 */

import { useCallback, useMemo } from "react";

import { useEditAgentMutation } from "@/data/api/agentSettingsApi";
import { codeOf, problemsOf } from "@/data/client/errors";
import { changeCount, patchOf } from "@/data/settings/changes";
import { useAppDispatch, useAppSelector } from "@/data/store/hooks";
import { draftDiscarded, selectDraft } from "@/data/store/slices/draftSlice";
import type { Agent } from "@/data/types/agent";
import type { SettingsForm } from "@/data/types/settings";

import { useToast } from "./useToast";

export function useDraftSave(agent: Agent, form: SettingsForm) {
  const draft = useAppSelector(selectDraft);
  const dispatch = useAppDispatch();
  const [edit, saving] = useEditAgentMutation();
  const { say, complain } = useToast();

  const changes = changeCount(form, draft);

  /** Every rejected field, by its path, for a form that marks its own. */
  const problems = useMemo(() => {
    const found = new Map<string, string>();
    for (const one of problemsOf(saving.error)) {
      if (!found.has(one.path)) found.set(one.path, one.message);
    }
    return found;
  }, [saving.error]);

  const save = useCallback(async () => {
    const patch = patchOf(form, draft);
    const result = await edit({
      id: agent.id,
      expectRevision: agent.draftRevision,
      ...patch,
    });

    if ("error" in result) {
      if (codeOf(result.error) === "REVISION_CONFLICT") {
        // The agent moved under us. The invalidated tags are already fetching
        // it, and the draft is kept: what someone typed is still what they
        // want, they only have to send it against the revision that exists.
        say("This agent changed somewhere else. Reloaded it — try saving again.");
        return;
      }
      complain(result.error);
      return;
    }

    // Only once the server has taken them: a draft cleared on a refusal is a
    // draft someone has to retype.
    dispatch(draftDiscarded());
    say(changes === 1 ? "One change saved." : `${changes} changes saved.`);
  }, [agent, changes, complain, dispatch, draft, edit, form, say]);

  const discard = useCallback(() => {
    dispatch(draftDiscarded());
    say("Unsaved changes discarded.");
  }, [dispatch, say]);

  return {
    draft,
    changes,
    problems,
    save,
    discard,
    saving: saving.isLoading,
    error: saving.error,
  };
}

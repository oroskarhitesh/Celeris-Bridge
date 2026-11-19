/**
 * What the agent can do, and the same save bar the settings use.
 *
 * One draft covers both tabs, so this panel saves settings changes too and
 * says so in its count. The alternative -- a save button per tab -- loses an
 * edit the moment someone changes an ability and then walks to settings.
 *
 * A missing ability is called out at the top as well as on its own card,
 * because it is the one thing on this screen that nobody here can fix: the
 * agent is running without a tool its definition asked for.
 */

import {
  abilityChanged,
  abilityEnabledIn,
  abilityValueOf,
} from "@/data/settings/changes";
import { useAppDispatch } from "@/data/store/hooks";
import { abilitySettingTouched, abilityToggled } from "@/data/store/slices/draftSlice";
import type { Agent } from "@/data/types/agent";
import type { SettingsForm } from "@/data/types/settings";
import { EmptyState } from "@/presentation/components/ui/EmptyState";
import { useDraftSave } from "@/presentation/hooks/useDraftSave";

import { AbilityCard } from "./AbilityCard";
import { SaveBar } from "./SaveBar";

export function AbilitiesPanel({ agent, form }: { agent: Agent; form: SettingsForm }) {
  const dispatch = useAppDispatch();
  const { draft, changes, problems, save, discard, saving, error } = useDraftSave(
    agent,
    form,
  );

  const missing = form.abilities.filter((one) => one.missing);

  if (!form.abilities.length) {
    return (
      <EmptyState
        title="This agent type declares no abilities"
        description="It can answer from its instructions and nothing else — no knowledge search, no booking."
      />
    );
  }

  return (
    <div className="space-y-4">
      {missing.length ? (
        <div
          role="alert"
          className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-3 text-sm text-ink"
        >
          <p className="font-medium text-warn">
            {missing.length === 1
              ? "One ability is not available in this deployment"
              : `${missing.length} abilities are not available in this deployment`}
          </p>
          <p className="mt-1 text-ink-soft">
            The agent is answering without {missing.map((one) => one.label).join(", ")}.
            Nothing here can turn them on — the server needs the ability before the
            setting means anything.
          </p>
        </div>
      ) : null}

      {form.abilities.map((ability) => (
        <AbilityCard
          key={ability.key}
          ability={ability}
          enabled={abilityEnabledIn(ability, draft)}
          changed={abilityChanged(ability, draft)}
          valueOf={(name) => abilityValueOf(ability, name, draft)}
          problemOf={(path) => problems.get(path)}
          onToggle={(enabled) => dispatch(abilityToggled({ key: ability.key, enabled }))}
          onSettingChange={(name, value) =>
            dispatch(abilitySettingTouched({ key: ability.key, name, value }))
          }
        />
      ))}

      <SaveBar
        changes={changes}
        busy={saving}
        error={error}
        onSave={() => void save()}
        onDiscard={discard}
      />
    </div>
  );
}

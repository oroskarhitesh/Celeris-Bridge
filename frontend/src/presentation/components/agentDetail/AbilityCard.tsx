/**
 * One ability: whether it runs, and its own settings.
 *
 * An ability is a thing the agent can *do* -- search the knowledge base,
 * offer a time, save a visitor's details -- so three states are worth
 * distinguishing and this is where they are said out loud.
 *
 * **Locked.** The agent type does not let a workspace switch it off. Shown
 * with the toggle disabled rather than hidden: knowing the agent searches
 * your knowledge on every question is useful even when it is not a choice.
 *
 * **Missing.** The definition names it and this build does not have it. That
 * is a deployment problem, not a configuration one: the agent is running with
 * one fewer tool right now, so it says so instead of drawing a toggle that
 * would change nothing.
 *
 * **Settings that only matter when it is on.** They stay visible and go
 * disabled, because hiding them makes the panel jump as toggles move and
 * leaves no way to see what a booking would be set to before enabling it.
 */

import type { ItemSpec, Ability } from "@/data/types/settings";
import { Field } from "@/presentation/components/ui/Field";
import { Toggle } from "@/presentation/components/ui/Toggle";

import { RowsControl } from "./RowsControl";
import { SettingControl } from "./SettingControl";

interface AbilityCardProps {
  ability: Ability;
  enabled: boolean;
  changed: boolean;
  /** One ability setting's value, after the draft. */
  valueOf: (name: string) => unknown;
  problemOf: (path: string) => string | undefined;
  onToggle: (enabled: boolean) => void;
  onSettingChange: (name: string, value: unknown) => void;
}

export function AbilityCard({
  ability,
  enabled,
  changed,
  valueOf,
  problemOf,
  onToggle,
  onSettingChange,
}: AbilityCardProps) {
  const own = Object.entries(ability.settingsSpec);
  const toggleId = `ability-${ability.key}`;
  const enabledProblem = problemOf(`abilities.${ability.key}.enabled`);
  const abilityProblem = problemOf(`abilities.${ability.key}`);

  return (
    <div className="space-y-4 rounded-xl border border-line bg-surface px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{ability.label}</p>
          <p className="mt-0.5 text-xs text-ink-soft">
            {ability.missing
              ? "This build does not have this ability. The agent is answering without it."
              : !ability.editable
                ? "Always on for this agent type."
                : ability.enabledByDefault
                  ? "On by default for this agent type."
                  : "Off by default for this agent type."}
          </p>
        </div>
        <span className="flex shrink-0 items-center gap-3">
          {changed ? <span className="text-xs text-warn">unsaved</span> : null}
          {ability.missing ? (
            <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-xs text-warn">
              unavailable
            </span>
          ) : (
            <Toggle
              id={toggleId}
              checked={enabled}
              disabled={!ability.editable}
              label={enabled ? "On" : "Off"}
              onChange={onToggle}
            />
          )}
        </span>
      </div>

      {enabledProblem || abilityProblem ? (
        <p role="alert" className="text-xs text-bad">
          {enabledProblem ?? abilityProblem}
        </p>
      ) : null}

      {own.length ? (
        <div className="space-y-4 border-t border-line pt-4">
          {own.map(([name, spec]) => {
            const id = `ability-${ability.key}-${name}`;
            const path = `abilities.${ability.key}.settings.${name}`;
            const problem = problemOf(path);
            const locked = !spec.editable;
            return (
              <Field
                key={name}
                id={id}
                label={spec.label}
                {...(spec.description ? { description: spec.description } : {})}
                {...(problem ? { problem } : {})}
                aside={
                  locked ? (
                    <span className="text-xs text-ink-soft">set by the platform</span>
                  ) : null
                }
              >
                {/* Not editable, not enabled, or not present: all three mean
                    a control that must not accept a change. */}
                {spec.type === "list" ? (
                  <RowsControl
                    id={id}
                    spec={spec}
                    value={
                      Array.isArray(valueOf(name))
                        ? (valueOf(name) as Record<string, unknown>[])
                        : []
                    }
                    disabled={locked || !enabled || ability.missing}
                    onChange={(value) => onSettingChange(name, value)}
                  />
                ) : (
                  <SettingControl
                    id={id}
                    spec={spec as ItemSpec}
                    value={valueOf(name)}
                    disabled={locked || !enabled || ability.missing}
                    problem={!!problem}
                    nullable={spec.nullable === true}
                    onChange={(value) => onSettingChange(name, value)}
                  />
                )}
              </Field>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

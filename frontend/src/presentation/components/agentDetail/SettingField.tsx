/**
 * One setting: its frame, its control, and what is true about its value.
 *
 * Pure markup -- it is given the value and told what to call when it changes,
 * so the panel above owns the draft and this owns the appearance. It is also
 * what decides the three things a person needs to know about a value that a
 * control alone cannot say:
 *
 * **Whose value it is.** ``overridden`` means this workspace chose it;
 * otherwise it is the agent type's default and will *follow* the default when
 * the platform publishes a better one. A form that showed every field as a
 * plain value would be pretending each one had been chosen.
 *
 * **That "use the default" is a deletion.** The button writes the default
 * into the draft, and saving it drops the override, because the backend keeps
 * a delta -- so this is genuinely "follow the default from now on" rather
 * than "set it to today's default". It is offered whether or not the setting
 * is currently overridden on the server, as long as the value on screen is
 * not already the default.
 *
 * **That a locked setting is readable but not editable.** It is shown, with
 * its control disabled and the reason said out loud, because a workspace
 * should be able to read the configuration its agent runs on even where the
 * platform keeps the decision.
 */

import { same } from "@/data/settings/changes";
import type { ItemSpec, Setting } from "@/data/types/settings";
import { Button } from "@/presentation/components/ui/Button";
import { Field } from "@/presentation/components/ui/Field";

import { RowsControl } from "./RowsControl";
import { SettingControl } from "./SettingControl";

interface SettingFieldProps {
  setting: Setting;
  value: unknown;
  /** True when this is one of the unsaved changes. */
  changed: boolean;
  problem?: string;
  onChange: (value: unknown) => void;
}

export function SettingField({
  setting,
  value,
  changed,
  problem,
  onChange,
}: SettingFieldProps) {
  const { key, spec } = setting;
  const id = `setting-${key}`;
  const locked = !spec.editable;
  const isDefault = same(value, spec.default ?? null);

  return (
    <Field
      id={id}
      label={spec.label}
      {...(spec.description ? { description: spec.description } : {})}
      {...(problem ? { problem } : {})}
      aside={
        <span className="flex items-center gap-2 text-xs">
          {changed ? <span className="text-warn">unsaved</span> : null}
          {locked ? (
            <span className="text-ink-soft" title="The platform decides this one">
              set by the platform
            </span>
          ) : setting.overridden ? (
            <span className="text-ink-soft">yours</span>
          ) : (
            <span className="text-ink-soft" title="Follows the agent type's default">
              default
            </span>
          )}
          {!locked && !isDefault ? (
            <Button
              look="ghost"
              type="button"
              className="px-1.5 py-0.5"
              onClick={() => onChange(spec.default ?? null)}
            >
              Use default
            </Button>
          ) : null}
        </span>
      }
    >
      {spec.type === "list" ? (
        <RowsControl
          id={id}
          spec={spec}
          value={Array.isArray(value) ? (value as Record<string, unknown>[]) : []}
          disabled={locked}
          onChange={onChange}
        />
      ) : (
        <SettingControl
          id={id}
          // A FieldSpec is an ItemSpec plus what only a whole setting has, and
          // its type is the wider union. With "list" ruled out the two agree,
          // which the compiler cannot see through the wider literal.
          spec={spec as ItemSpec}
          value={value}
          disabled={locked}
          problem={!!problem}
          nullable={spec.nullable === true}
          onChange={onChange}
        />
      )}
    </Field>
  );
}

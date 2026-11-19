/**
 * The generated settings form.
 *
 * Nothing in this file knows a setting by name. It walks the groups the
 * definition declared, in the order it declared them, and draws each setting
 * from its own spec -- so a new template version with a new setting grows a
 * control here without a line changing, and a renamed group moves on its own.
 *
 * It owns the draft (which the slice holds) and hands each field a value and
 * a callback. The fields stay pure markup, and the one place that knows how a
 * change becomes a request is :mod:`data/settings/changes`.
 *
 * "Reset everything" is the one deletion a patch cannot express. A field's own
 * "use default" is a local edit, because writing the default drops the
 * override; dropping *all* of them, including the abilities', would mean
 * sending every default value in the agent type -- so that one is its own
 * endpoint, applied straight away, and the unsaved draft goes with it.
 */

import { valueOf, changed as isChanged } from "@/data/settings/changes";
import { useAppDispatch } from "@/data/store/hooks";
import { draftDiscarded, settingTouched } from "@/data/store/slices/draftSlice";
import { useResetAgentMutation } from "@/data/api/agentSettingsApi";
import { codeOf } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import type { Setting, SettingsForm } from "@/data/types/settings";
import { Card } from "@/presentation/components/ui/Card";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";
import { useDraftSave } from "@/presentation/hooks/useDraftSave";
import { useToast } from "@/presentation/hooks/useToast";

import { LockedRules } from "./LockedRules";
import { SaveBar } from "./SaveBar";
import { SettingField } from "./SettingField";

const UNGROUPED = "Other";

/** The settings of each group, in the definition's order, ungrouped last. */
function grouped(form: SettingsForm): [string, Setting[]][] {
  const byGroup = new Map<string, Setting[]>();
  for (const setting of form.settings) {
    const name = setting.spec.group ?? UNGROUPED;
    const existing = byGroup.get(name);
    if (existing) existing.push(setting);
    else byGroup.set(name, [setting]);
  }
  // Declared groups first and in order; anything else keeps the order the
  // settings arrived in, so an undeclared group is still stable.
  const ordered = [...form.groups.filter((one) => byGroup.has(one))];
  for (const name of byGroup.keys()) if (!ordered.includes(name)) ordered.push(name);
  return ordered.map((name) => [name, byGroup.get(name) ?? []]);
}

export function SettingsPanel({ agent, form }: { agent: Agent; form: SettingsForm }) {
  const dispatch = useAppDispatch();
  const { draft, changes, problems, save, discard, saving, error } = useDraftSave(
    agent,
    form,
  );
  const [reset, resetting] = useResetAgentMutation();
  const { say, complain } = useToast();

  async function resetEverything() {
    const result = await reset({
      id: agent.id,
      expectRevision: agent.draftRevision,
      everything: true,
    });
    if ("error" in result) {
      if (codeOf(result.error) === "REVISION_CONFLICT") {
        say("This agent changed somewhere else. Reloaded it — try again.");
        return;
      }
      complain(result.error);
      return;
    }
    // The draft was edits against values that no longer exist.
    dispatch(draftDiscarded());
    say("Every setting follows the agent type's defaults again.");
  }

  return (
    <div className="space-y-5">
      {grouped(form).map(([name, settings]) => (
        <Card key={name} title={name}>
          <div className="space-y-6">
            {settings.map((setting) => (
              <SettingField
                key={setting.key}
                setting={setting}
                value={valueOf(setting, draft)}
                changed={isChanged(setting, draft)}
                {...(problems.get(`settings.${setting.key}`)
                  ? { problem: problems.get(`settings.${setting.key}`) }
                  : {})}
                onChange={(value) =>
                  dispatch(settingTouched({ key: setting.key, value }))
                }
              />
            ))}
          </div>
        </Card>
      ))}

      <LockedRules rules={form.lockedRules} />

      <Card
        title="Start again"
        description="Drops every value this workspace has chosen, settings and abilities, so the agent follows its type's defaults. Nothing is published until you publish."
      >
        <ConfirmButton
          label="Reset everything to defaults"
          confirmLabel="Reset everything"
          busy={resetting.isLoading}
          onConfirm={() => void resetEverything()}
        />
      </Card>

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

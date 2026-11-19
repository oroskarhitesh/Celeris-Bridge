/**
 * Moving the agent onto a newer version of its agent type.
 *
 * The pinned version does not move on its own. The platform publishing a
 * better Marketing agent changes nothing for an existing one until somebody
 * here says so, which is what makes an agent's behaviour stable -- and what
 * makes this panel necessary, because otherwise nobody would ever know a
 * newer version existed.
 *
 * **The preview is read before anything is written.** A newer version can
 * remove a setting, lock one that used to be editable, or narrow what a value
 * may be, and any of those drops an override this workspace chose. The list
 * of what would be lost is shown first, with each reason, and the upgrade
 * takes two clicks when there is anything on it.
 *
 * **An upgrade is not a publish.** It moves the *draft*, so visitors keep
 * being served the old snapshot until the agent is published again. Said out
 * loud here, because "upgraded" sounds like something that already happened
 * to the live agent.
 *
 * The newest version comes from the catalogue, which is the one read that
 * knows what the platform has published -- so an agent already on the latest
 * says so rather than offering an upgrade to the version it is on.
 */

import { useCatalogueQuery } from "@/data/api/catalogueApi";
import {
  useUpgradeAgentMutation,
  useUpgradePreviewQuery,
} from "@/data/api/agentVersionsApi";
import { codeOf } from "@/data/client/errors";
import { useAppDispatch } from "@/data/store/hooks";
import { draftDiscarded } from "@/data/store/slices/draftSlice";
import type { Agent } from "@/data/types/agent";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";
import { useToast } from "@/presentation/hooks/useToast";

export function UpgradePanel({ agent }: { agent: Agent }) {
  const { data: catalogue } = useCatalogueQuery();
  const dispatch = useAppDispatch();
  const { say, complain } = useToast();

  const latest = catalogue?.find((one) => one.templateKey === agent.templateKey)?.version;
  const newer = latest !== undefined && latest > agent.draftTemplateVersion;

  // Asked for only when there is something to move to: the endpoint happily
  // previews an "upgrade" to the version already pinned, and the answer would
  // be a panel full of nothing.
  const preview = useUpgradePreviewQuery({ id: agent.id }, { skip: !newer });
  const [upgrade, upgrading] = useUpgradeAgentMutation();

  async function move(toVersion: number) {
    const result = await upgrade({
      id: agent.id,
      expectRevision: agent.draftRevision,
      toVersion,
    });
    if ("error" in result) {
      if (codeOf(result.error) === "REVISION_CONFLICT") {
        say("This agent changed somewhere else. Reloaded it — try again.");
        return;
      }
      complain(result.error);
      return;
    }
    // Unsaved edits were against the old version's settings; some of them may
    // not exist any more.
    dispatch(draftDiscarded());
    say(`The draft is on agent type v${toVersion}. Publish to make it live.`);
  }

  if (!newer) {
    return (
      <Card title="Agent type">
        <p className="text-sm text-ink-soft">
          Pinned to <span className="text-ink">{agent.templateKey}</span> v
          {agent.draftTemplateVersion}
          {latest !== undefined ? ", which is the newest the platform has published." : "."}
        </p>
      </Card>
    );
  }

  // A const, not ``preview.data``: a narrowed property access is reset
  // inside the callbacks below, a narrowed local is not.
  const plan = preview.data;
  const dropped = plan?.dropped ?? [];
  const added = plan?.addedSettings ?? [];

  return (
    <Card
      title={`Version ${latest} of this agent type is available`}
      description={`This agent is pinned to v${agent.draftTemplateVersion}. Upgrading moves the draft only — visitors keep the published version until you publish again.`}
    >
      {preview.isLoading ? <Spinner label="Checking what would change" /> : null}
      {preview.error ? <ErrorNotice error={preview.error} /> : null}

      {plan ? (
        <div className="space-y-4">
          {dropped.length ? (
            <div className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-3">
              <p className="text-sm font-medium text-warn">
                {dropped.length === 1
                  ? "One of your values cannot come along"
                  : `${dropped.length} of your values cannot come along`}
              </p>
              <ul className="mt-2 space-y-1 text-sm text-ink-soft">
                {dropped.map((one) => (
                  <li key={one.path}>
                    <code className="text-xs text-ink">{one.path}</code>{" — "}
                    <span>{one.reason}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-soft">
                Each of those follows the new version's default instead.
              </p>
            </div>
          ) : (
            <p className="text-sm text-good">
              Nothing you have configured would be lost.
            </p>
          )}

          {added.length ? (
            <p className="text-sm text-ink-soft">
              New settings you will be able to change:{" "}
              <span className="text-ink">{added.join(", ")}</span>.
            </p>
          ) : null}

          {upgrading.error ? <ErrorNotice error={upgrading.error} /> : null}

          {dropped.length ? (
            <ConfirmButton
              label={`Upgrade to v${latest}`}
              confirmLabel={`Upgrade and drop ${dropped.length}`}
              busy={upgrading.isLoading}
              onConfirm={() => void move(plan.toVersion)}
            />
          ) : (
            <Button
              look="primary"
              type="button"
              busy={upgrading.isLoading}
              onClick={() => void move(plan.toVersion)}
            >
              Upgrade to v{latest}
            </Button>
          )}
        </div>
      ) : null}
    </Card>
  );
}

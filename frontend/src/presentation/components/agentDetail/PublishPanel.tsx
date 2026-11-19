/**
 * Making the draft the thing visitors talk to.
 *
 * The screen in this product most able to mislead, so three refusals are
 * built in rather than explained afterwards.
 *
 * **Unsaved changes block it.** Publishing snapshots the *saved* draft, so a
 * publish with edits still on the screen would quietly ship something else.
 * The button says what to do instead of failing in a way that looks fine.
 *
 * **Nothing to publish is a disabled button, not a 409.** The API refuses a
 * publish that would match the live version (``NOTHING_TO_PUBLISH``), and it
 * decides that with the same comparison that produces
 * ``hasUnpublishedChanges`` -- so this button can trust that field exactly.
 *
 * **A never-published agent is told plainly that nobody can reach it.** Its
 * settings can be perfect and its widget still answers nothing, which is the
 * single most confusing state a draft-based system has.
 *
 * The summary is optional and worth asking for: it is the only thing in the
 * version history written by a person, and "raised the turn limit" six weeks
 * later is the difference between a history and a list of numbers.
 */

import { useState } from "react";

import { usePublishAgentMutation } from "@/data/api/agentVersionsApi";
import { codeOf } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";
import { useToast } from "@/presentation/hooks/useToast";

interface PublishPanelProps {
  agent: Agent;
  /** How many changes are still only on the screen. */
  unsaved: number;
}

export function PublishPanel({ agent, unsaved }: PublishPanelProps) {
  const [publish, publishing] = usePublishAgentMutation();
  const { say, complain } = useToast();
  const [summary, setSummary] = useState("");

  const never = agent.publishedVersion === null;
  const nothing = !never && !agent.hasUnpublishedChanges;

  async function submit() {
    const result = await publish({
      id: agent.id,
      expectRevision: agent.draftRevision,
      changeSummary: summary.trim(),
    });
    if ("error" in result) {
      if (codeOf(result.error) === "REVISION_CONFLICT") {
        say("This agent changed somewhere else. Reloaded it — try again.");
        return;
      }
      complain(result.error);
      return;
    }
    setSummary("");
    say(`Version ${result.data.version.version} is live.`);
  }

  return (
    <Card
      title={never ? "Not published yet" : "Publish"}
      description={
        never
          ? "Until this is published the agent answers nobody, however it is configured."
          : `Visitors are served version ${agent.publishedVersion}. Publishing makes a new version from the saved draft.`
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextInput
          label="What changed"
          value={summary}
          maxLength={500}
          onChange={(event) => setSummary(event.target.value)}
          hint="Optional, and the only part of the history a person writes."
        />
        {publishing.error ? <ErrorNotice error={publishing.error} /> : null}
        <div className="flex items-center gap-3">
          <Button
            type="submit"
            look="primary"
            busy={publishing.isLoading}
            disabled={unsaved > 0 || nothing}
          >
            {never ? "Publish and go live" : "Publish new version"}
          </Button>
          {unsaved > 0 ? (
            <span className="text-xs text-warn">
              Save your {unsaved === 1 ? "change" : "changes"} first — publishing
              snapshots the saved draft.
            </span>
          ) : nothing ? (
            <span className="text-xs text-ink-soft">
              The live version already matches this draft.
            </span>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

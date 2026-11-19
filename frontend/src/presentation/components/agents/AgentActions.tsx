/**
 * Pause, resume and delete, for one agent.
 *
 * Each sends the ``draftRevision`` it was rendered with. That is the whole
 * reason this component takes the agent rather than its id: a button that
 * knew only an id would have to guess a revision, and guessing is what
 * ``expectRevision`` exists to prevent.
 *
 * A 409 is not drawn as an error here. It means the agent moved under us,
 * which the invalidated cache tags are already fixing, so the toast says that
 * in words and the screen corrects itself.
 */

import {
  useDeleteAgentMutation,
  usePauseAgentMutation,
  useResumeAgentMutation,
} from "@/data/api/agentsApi";
import { codeOf } from "@/data/client/errors";
import type { ApiError } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import { Button } from "@/presentation/components/ui/Button";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";
import { useToast } from "@/presentation/hooks/useToast";

export function AgentActions({ agent }: { agent: Agent }) {
  const [pause, pausing] = usePauseAgentMutation();
  const [resume, resuming] = useResumeAgentMutation();
  const [remove, removing] = useDeleteAgentMutation();
  const { say, complain } = useToast();

  const revised = { id: agent.id, expectRevision: agent.draftRevision };

  function report(error: unknown, done: string) {
    if (!error) {
      say(done);
      return;
    }
    if (codeOf(error as ApiError) === "REVISION_CONFLICT") {
      say("This agent changed somewhere else. Reloaded it — try again.");
      return;
    }
    complain(error as ApiError);
  }

  return (
    <div className="flex items-center gap-2">
      {agent.status === "paused" ? (
        <Button
          look="secondary"
          busy={resuming.isLoading}
          onClick={async () => {
            const { error } = await resume(revised);
            report(error, agent.label + " is answering again.");
          }}
        >
          Resume
        </Button>
      ) : (
        <Button
          look="secondary"
          busy={pausing.isLoading}
          disabled={agent.status === "draft"}
          title={
            agent.status === "draft" ? "A draft is not answering anyone yet" : undefined
          }
          onClick={async () => {
            const { error } = await pause(revised);
            report(error, agent.label + " has stopped answering.");
          }}
        >
          Pause
        </Button>
      )}
      <ConfirmButton
        label="Delete"
        confirmLabel="Delete for good"
        busy={removing.isLoading}
        onConfirm={async () => {
          const { error } = await remove(revised);
          // "Nothing is removed" is the backend's actual behaviour: the
          // conversations and leads stay readable, so the words here should
          // not promise otherwise.
          report(error, agent.label + " was deleted. Its conversations are kept.");
        }}
      />
    </div>
  );
}

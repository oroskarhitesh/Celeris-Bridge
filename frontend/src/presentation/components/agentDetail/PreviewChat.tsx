/**
 * Talking to the draft, before a visitor can.
 *
 * The transcript is component state and not the query cache, which is the
 * opposite of the rule everywhere else in this application and right here:
 * these are turns that *happened*, in order, each the answer to the one
 * before. A cache entry is a thing that can be refetched, and refetching a
 * conversation would mean saying it all again -- a second set of model calls,
 * billed, with different answers.
 *
 * Three things it is careful to say:
 *
 * **It previews the draft as last saved.** Unsaved edits are not in it. The
 * endpoint resolves the stored draft, so a greeting typed a moment ago and
 * not saved will not appear, and nobody should have to work that out.
 *
 * **It costs what a real turn costs.** Same model, same abilities, same
 * spend. Not a simulation.
 *
 * **It stops when the agent stops.** ``open`` goes false at the turn limit,
 * so the box goes away rather than letting someone type into a conversation
 * that will refuse them -- which is also how the limit itself gets tested.
 */

import { useState } from "react";

import { useSayToPreviewMutation, useStartPreviewMutation } from "@/data/api/agentPreviewApi";
import { codeOf, messageOf } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { useToast } from "@/presentation/hooks/useToast";

import type { PreviewTurn } from "./PreviewMessage";
import { PreviewMessage } from "./PreviewMessage";

export function PreviewChat({ agent }: { agent: Agent }) {
  const [start, starting] = useStartPreviewMutation();
  const [say, saying] = useSayToPreviewMutation();
  const { complain } = useToast();

  const [conversationId, setConversationId] = useState("");
  const [turns, setTurns] = useState<PreviewTurn[]>([]);
  const [open, setOpen] = useState(true);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");

  async function begin() {
    const result = await start({ id: agent.id, pageTitle: "Preview" });
    if ("error" in result) {
      complain(result.error);
      return;
    }
    setConversationId(result.data.conversationId);
    setTurns([]);
    setOpen(true);
    setNote("");
  }

  async function send(message: string) {
    const trimmed = message.trim();
    if (!trimmed || !conversationId) return;

    // Added before the request, because a turn can take seconds and a
    // message that vanishes until the reply lands reads as a lost message.
    const mine: PreviewTurn = { id: `admin-${turns.length}`, from: "admin", text: trimmed };
    setTurns((before) => [...before, mine]);
    setText("");

    const result = await say({ id: agent.id, conversationId, message: trimmed });
    if ("error" in result) {
      if (codeOf(result.error) === "CONVERSATION_TOO_LONG") {
        // Not a failure: the conversation reached its turn limit, which is
        // the setting doing its job. The message was stored either way.
        setOpen(false);
        setNote(messageOf(result.error));
        return;
      }
      complain(result.error);
      // Taken back out: it was not answered, and leaving it would make the
      // transcript claim a turn that did not happen.
      setTurns((before) => before.filter((one) => one.id !== mine.id));
      setText(trimmed);
      return;
    }

    const reply = result.data;
    setOpen(reply.open);
    setTurns((before) => [
      ...before,
      {
        id: `agent-${before.length}`,
        from: "agent",
        text: reply.text,
        ending: reply.ending,
        sources: reply.sources,
        quickReplies: reply.quickReplies,
      },
    ]);
  }

  return (
    <Card
      title="Preview"
      description="Talks to the draft as last saved, with the real model and the real abilities — so it costs what a visitor's turn costs. Unsaved changes are not in it."
      aside={
        conversationId ? (
          <Button type="button" busy={starting.isLoading} onClick={() => void begin()}>
            Start again
          </Button>
        ) : null
      }
    >
      {!conversationId ? (
        <div className="space-y-3">
          <p className="text-sm text-ink-soft">
            A preview conversation is stored like any other, with no version on it —
            which is how it is told apart from real traffic later.
          </p>
          <Button
            look="primary"
            type="button"
            busy={starting.isLoading}
            onClick={() => void begin()}
          >
            Start a preview
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <ol className="max-h-96 space-y-3 overflow-y-auto">
            {turns.map((turn) => (
              <PreviewMessage
                key={turn.id}
                turn={turn}
                onQuickReply={(reply) => void send(reply)}
              />
            ))}
            {saying.isLoading ? (
              <li className="text-xs text-ink-soft">Thinking…</li>
            ) : null}
          </ol>

          {note ? <p className="text-sm text-warn">{note}</p> : null}

          {open ? (
            <form
              className="flex items-start gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send(text);
              }}
            >
              <input
                type="text"
                value={text}
                maxLength={4000}
                aria-label="Message to the draft"
                placeholder="Ask it something a visitor would"
                onChange={(event) => setText(event.target.value)}
                className="w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand"
              />
              <Button
                type="submit"
                look="primary"
                busy={saying.isLoading}
                disabled={!text.trim()}
              >
                Send
              </Button>
            </form>
          ) : (
            <p className="text-sm text-ink-soft">
              This conversation is over. Start another to keep testing.
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

/**
 * Everything the agents have said.
 *
 * Two queries, and the second one is the interesting part: a conversation
 * carries an ``instanceId`` and no label, so the agents list is loaded to
 * turn ids into names. It is the same cache entry the agents screen uses, so
 * on any normal path through the application it is already there.
 *
 * The filter lives in the URL and is sent to the server. There is no
 * client-side filtering at all, deliberately: ``limit`` is applied by the
 * server, so narrowing a list after it arrived would quietly hide rows that
 * were inside the limit and leave the count on the screen lying.
 *
 * The header counts previews separately. They are conversations had against
 * a draft from the admin screen rather than by visitors -- real records, and
 * not traffic. Hiding them would be the worse answer: the backend flags them
 * precisely because a filtered-out conversation is one somebody goes looking
 * for.
 */

import { useSearchParams } from "react-router-dom";

import { useAgentsQuery } from "@/data/api/agentsApi";
import { useConversationsQuery } from "@/data/api/conversationsApi";
import type { Channel, ConversationFilter } from "@/data/types/conversation";
import { ConversationFilters } from "@/presentation/components/conversations/ConversationFilters";
import { ConversationList } from "@/presentation/components/conversations/ConversationList";
import { EmptyState } from "@/presentation/components/ui/EmptyState";
import { tokens } from "@/presentation/format";

const CHANNEL_VALUES: Channel[] = ["web_chat", "email", "voice", "whatsapp"];

export function ConversationsPage() {
  const [params] = useSearchParams();
  const agent = params.get("agent") ?? "";
  const asked = params.get("channel");
  // Validated against the list rather than passed through: an unknown value
  // in the URL would be a 422 from the server's enum, and a bad bookmark
  // should show the unfiltered list rather than an error.
  const channel = CHANNEL_VALUES.find((one) => one === asked);

  const filter: ConversationFilter = {
    ...(agent ? { instanceId: agent } : {}),
    ...(channel ? { channel } : {}),
  };

  const { data, error, isLoading } = useConversationsQuery(filter);
  // Labels only. A failure here is not worth a notice: the list still works
  // and the rows fall back to the id.
  const { data: agents } = useAgentsQuery();

  const conversations = data ?? [];
  const labels = Object.fromEntries(
    (agents ?? []).map((one) => [one.id, one.label] as const),
  );

  const previews = conversations.filter((one) => one.preview).length;
  const spent = conversations.reduce(
    (total, one) => total + one.inputTokens + one.outputTokens,
    0,
  );
  const nothing = !isLoading && !error && conversations.length === 0;
  const filtered = Boolean(agent || channel);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-ink">Conversations</h1>
        <p className="text-sm text-ink-soft">
          {conversations.length
            ? `${conversations.length} ${conversations.length === 1 ? "conversation" : "conversations"} · ${tokens(spent)} tokens` +
              (previews ? ` · ${previews} from the preview panel` : "")
            : "Transcripts of what the agents said, and what each turn cost."}
        </p>
      </header>

      <ConversationFilters
        agents={(agents ?? []).map((one) => ({ id: one.id, label: one.label }))}
        {...(conversations.length
          ? { note: "Newest first, up to the 50 the API returns." }
          : {})}
      />

      {nothing ? (
        <EmptyState
          title={filtered ? "Nothing matches that" : "Nobody has talked to an agent yet"}
          description={
            filtered
              ? "Clear the filter to see the rest."
              : "A conversation appears here as soon as a visitor opens the widget — or as soon as you try a draft in the preview panel."
          }
        />
      ) : (
        <ConversationList
          conversations={data}
          labels={labels}
          loading={isLoading}
          error={error}
        />
      )}
    </div>
  );
}

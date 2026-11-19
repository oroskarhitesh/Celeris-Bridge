/**
 * One message on the visitor's screen.
 *
 * A citation here is a name and a link and nothing else, which is all the
 * public API sends: the passage text, the retrieval score and the source id
 * stay in the stored transcript where the people who own the agent can read
 * them. A link opens with ``rel="noreferrer noopener"`` because this runs
 * inside somebody else's page and a citation must not hand the opened tab a
 * reference back to it.
 *
 * Quick replies are buttons rather than text, because they are the agent
 * offering to be asked something -- and tapping one on a phone is the
 * difference between a conversation and a form.
 */

import type { Message } from "./types";

interface BubbleProps {
  message: Message;
  /** Absent once the conversation is closed: a suggestion that cannot be
   *  sent is worse than no suggestion. */
  onQuickReply?: (text: string) => void;
}

export function Bubble({ message, onQuickReply }: BubbleProps) {
  const cites = message.sources?.filter((one) => one.name || one.url) ?? [];

  return (
    <li className={`line ${message.from}`}>
      <div className="bubble">{message.text}</div>

      {cites.length ? (
        <p className="cites">
          Sources:{" "}
          {cites.map((one, at) => (
            <span key={`${one.name}-${at}`}>
              {at > 0 ? ", " : ""}
              {one.url ? (
                <a href={one.url} target="_blank" rel="noreferrer noopener">
                  {one.name || one.url}
                </a>
              ) : (
                one.name
              )}
            </span>
          ))}
        </p>
      ) : null}

      {onQuickReply && message.quickReplies?.length ? (
        <div className="quick">
          {message.quickReplies.map((reply) => (
            <button key={reply} type="button" onClick={() => onQuickReply(reply)}>
              {reply}
            </button>
          ))}
        </div>
      ) : null}
    </li>
  );
}

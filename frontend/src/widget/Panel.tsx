/**
 * The open chat: the log, and the box.
 *
 * Markup only -- every request lives in ``Widget`` -- so this file can be
 * rendered with a fixed list of messages and read for what a visitor sees.
 *
 * Two behaviours are here rather than in the parent because they are about
 * the screen and not about the conversation.
 *
 * **It scrolls to the newest message.** A reply that arrives below the fold
 * is a reply nobody notices.
 *
 * **The box disappears when the conversation is closed**, rather than
 * refusing a message after it is typed. ``open`` goes false at the agent's
 * turn limit, which is a setting doing its job, and the notice says so.
 */

import { useEffect, useRef, useState } from "react";

import { Bubble } from "./Bubble";
import type { Message } from "./types";

interface PanelProps {
  title: string;
  messages: Message[];
  /** A turn is in flight. A model call takes seconds and silence reads as a
   *  lost message. */
  thinking: boolean;
  /** False when nothing more can be said: the limit, or a refusal that
   *  retrying will not fix. */
  open: boolean;
  /** Why it is closed, or the last failure. The agent's own words when the
   *  backend wrote them for a visitor. */
  notice: string;
  /** False until the conversation has been opened on the server. */
  ready: boolean;
  onSay: (text: string) => void;
  onClose: () => void;
}

export function Panel({
  title,
  messages,
  thinking,
  open,
  notice,
  ready,
  onSay,
  onClose,
}: PanelProps) {
  const [text, setText] = useState("");
  const log = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const element = log.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.length, thinking, notice]);

  return (
    <div className="panel" role="dialog" aria-label={title || "Chat"}>
      <div className="head">
        <p className="title">{title || "Chat"}</p>
        <button type="button" className="close" aria-label="Close chat" onClick={onClose}>
          ×
        </button>
      </div>

      <ol className="log" ref={log}>
        {messages.map((message) => (
          <Bubble
            key={message.id}
            message={message}
            {...(open && ready ? { onQuickReply: onSay } : {})}
          />
        ))}
        {thinking ? (
          <li className="line agent">
            <span className="thinking">Typing…</span>
          </li>
        ) : null}
      </ol>

      {notice ? <p className="notice">{notice}</p> : null}

      {open ? (
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = text.trim();
            if (!trimmed || !ready) return;
            setText("");
            onSay(trimmed);
          }}
        >
          <input
            type="text"
            value={text}
            // The API's own cap. Refusing a 50,000-character paste here
            // costs nothing; refusing it after the model call costs a call.
            maxLength={4000}
            aria-label="Your message"
            placeholder={ready ? "Type a message" : "Connecting…"}
            disabled={!ready}
            onChange={(event) => setText(event.target.value)}
          />
          <button type="submit" disabled={!ready || !text.trim() || thinking}>
            Send
          </button>
        </form>
      ) : null}
    </div>
  );
}

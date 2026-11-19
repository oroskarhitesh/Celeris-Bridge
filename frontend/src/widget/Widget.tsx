/**
 * A visitor talking to a live agent. The whole public application.
 *
 * Five decisions are in this file and each one is about not spending
 * somebody's money or somebody's goodwill.
 *
 * **Nothing is opened until someone clicks.** The face is fetched on load
 * because the launcher cannot be drawn without it and it costs the backend a
 * lookup; a *conversation* is a stored record, so starting one on page load
 * would file a conversation for every visitor who never chats. Opening it
 * makes no model call, which is why it can wait until the click without
 * making the first message slow.
 *
 * **A conversation is never resumed.** Not across a reload, not from
 * ``sessionStorage``. The public API cannot read a transcript back -- by
 * design, that is the admin router's job -- so a remembered id would restore
 * a chat that looks empty to the visitor and is half over to the server.
 * Starting again is the honest behaviour.
 *
 * **The visitor's message appears before the reply.** A turn takes seconds,
 * and a message that vanishes until the answer lands reads as a lost
 * message.
 *
 * **A failed turn gives the message back -- except when the server kept
 * it.** ``CONVERSATION_TOO_LONG`` is a 409 and the backend stores the
 * visitor's message anyway, so taking it off the screen would be the screen
 * lying about what the server holds. Every other failure removes it and puts
 * the words back in the box, because it was not answered and a transcript
 * that claims a turn that did not happen is worse than an error.
 *
 * **A refusal that will not change is final.** Paused agent, origin not
 * allowed, no such agent: the composer goes away rather than inviting a
 * visitor to try again forever at a page that is not going to work.
 */

import { useCallback, useEffect, useState } from "react";

import { faceOf, isFinal, sayToAgent, startConversation, wordsFor } from "./client";
import type { WidgetConfig } from "./client";
import { Launcher } from "./Launcher";
import { Panel } from "./Panel";
import type { AgentFace, Message } from "./types";

/** A greeting is a message with no request behind it. */
function greetingOf(face: AgentFace): Message[] {
  return face.greeting
    ? [{ id: "greeting", from: "agent" as const, text: face.greeting }]
    : [];
}

interface WidgetProps {
  config: WidgetConfig;
  /**
   * The shadow root's host element, when there is one.
   *
   * Which side of the screen the widget sits on is a deployment setting, and
   * the element it applies to is outside the shadow root -- so the host is
   * handed in and this sets one attribute on it once the face has loaded.
   * Optional, because a test renders the widget without an embed around it.
   */
  host?: HTMLElement;
}

export function Widget({ config, host }: WidgetProps) {
  const [face, setFace] = useState<AgentFace | null>(null);
  const [shown, setShown] = useState(false);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [thinking, setThinking] = useState(false);
  const [open, setOpen] = useState(true);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let live = true;
    faceOf(config)
      .then((loaded) => {
        if (live) setFace(loaded);
      })
      .catch(() => {
        // Nothing is drawn. A paused agent, a page that is not in the
        // allowed origins, an id that is wrong: none of them is a visitor's
        // problem, and an error box on somebody's website would be.
        if (live) setFace(null);
      });
    return () => {
      live = false;
    };
  }, [config]);

  useEffect(() => {
    if (!host || !face) return;
    host.dataset["position"] = face.launcher.position === "left" ? "left" : "right";
  }, [host, face]);

  const begin = useCallback(async () => {
    if (!face) return;
    setShown(true);
    setMessages(greetingOf(face));
    if (conversationId) return;
    try {
      const started = await startConversation(config, {
        pageUrl: window.location.href,
        pageTitle: document.title,
      });
      setConversationId(started.conversationId);
    } catch (error) {
      setNotice(wordsFor(error));
      setOpen(false);
    }
  }, [config, conversationId, face]);

  const say = useCallback(
    async (text: string) => {
      if (!conversationId || thinking) return;

      const mine: Message = {
        id: `visitor-${messages.length}`,
        from: "visitor",
        text,
      };
      setMessages((before) => [...before, mine]);
      setNotice("");
      setThinking(true);

      try {
        const reply = await sayToAgent(config, conversationId, text);
        setMessages((before) => [
          ...before,
          {
            id: `agent-${before.length}`,
            from: "agent",
            text: reply.text,
            sources: reply.sources,
            quickReplies: reply.quickReplies,
          },
        ]);
        setOpen(reply.open);
        if (!reply.open) {
          setNotice("This conversation has ended. Thanks for stopping by.");
        }
      } catch (error) {
        setNotice(wordsFor(error));
        if (isFinal(error)) {
          setOpen(false);
          // Kept on the screen: the backend stored it before refusing.
          return;
        }
        setMessages((before) => before.filter((one) => one.id !== mine.id));
      } finally {
        setThinking(false);
      }
    },
    [config, conversationId, messages.length, thinking],
  );

  // Nothing at all until the agent's face says it may be here.
  if (!face) return null;

  if (!shown) {
    return <Launcher text={face.launcher.text} onOpen={() => void begin()} />;
  }

  return (
    <Panel
      title={face.agentName}
      messages={messages}
      thinking={thinking}
      open={open}
      notice={notice}
      ready={Boolean(conversationId)}
      onSay={(text) => void say(text)}
      onClose={() => setShown(false)}
    />
  );
}

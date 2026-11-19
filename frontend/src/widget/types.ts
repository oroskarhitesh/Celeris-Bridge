/**
 * What the public API sends a visitor, and nothing else.
 *
 * A mirror of the views in ``api/routes/widget.py``, which are deliberately
 * narrower than the admin ones. The clearest example is the citation: the
 * retrieval layer hands the turn the whole passage -- its text, its score, its
 * source id -- and all of it is in the stored transcript where an admin can
 * read it. A visitor gets a name and a link, because that is what a citation
 * is *for*. Keeping these types in the widget's own folder is part of that:
 * nothing here can accidentally be given an admin shape.
 */

export interface Citation {
  name: string;
  url: string | null;
}

export interface Launcher {
  /** ``left`` or ``right``. A deployment setting, not a preference. */
  position: string;
  text: string;
}

/** What the widget needs before a conversation exists. */
export interface AgentFace {
  agentName: string;
  greeting: string;
  launcher: Launcher;
  /** The versions serving this visitor. Not shown: they are here so a bug
   *  report from a customer's site can be read against the configuration
   *  that produced it. */
  templateVersion: number;
  instanceVersion: number | null;
}

export interface StartedConversation {
  conversationId: string;
}

export interface Reply {
  conversationId: string;
  text: string;
  /** Why the turn ended. The widget shows nothing for it -- a visitor is not
   *  an audience for "the model could not be reached" -- but it arrives, and
   *  the admin transcript is where it is read. */
  ending: string;
  /** False once the turn limit is reached, so the box goes away rather than
   *  letting somebody type into a conversation that will refuse them. */
  open: boolean;
  sources: Citation[];
  quickReplies: string[];
}

/** One thing on the screen. The greeting is one of these with no request
 *  behind it, which is why the agent's side is not the same as a reply. */
export interface Message {
  id: string;
  from: "visitor" | "agent";
  text: string;
  sources?: Citation[];
  quickReplies?: string[];
}

/**
 * The thing a visitor clicks.
 *
 * It is drawn from the agent's public face -- its own launcher text, its own
 * side of the screen -- because both are deployment settings a customer set,
 * and a widget that puts itself where it likes is a widget that covers
 * somebody's cookie banner.
 *
 * It renders only after the face has loaded, which is also the origin check:
 * a widget embedded on a page the agent is not deployed on fails before it
 * draws anything, rather than on the visitor's first message.
 */

interface LauncherProps {
  text: string;
  onOpen: () => void;
}

export function Launcher({ text, onOpen }: LauncherProps) {
  return (
    <button type="button" className="launcher" onClick={onOpen}>
      {/* Decorative: the button already has its words, and a screen reader
          announcing "speech balloon" before them helps nobody. */}
      <span aria-hidden="true">💬</span>
      <span className="launcher-text">{text || "Chat with us"}</span>
    </button>
  );
}

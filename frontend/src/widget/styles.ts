/**
 * The widget's styles, as one string, for one shadow root.
 *
 * **Not Tailwind, and that is the decision this file exists to record.** The
 * admin application is Tailwind and should be; this is a few hundred bytes of
 * CSS injected into somebody else's website. Tailwind's preflight resets
 * margins, list styles and heading sizes on every element it is loaded
 * beside, so shipping it here would mean a chat widget that quietly
 * restyles a customer's page. A shadow root prevents that in both
 * directions: nothing in here escapes, and the host page's ``* { font-size:
 * 13px !important }`` does not reach in.
 *
 * Everything is scoped under ``:host``, every size is in ``px`` rather than
 * ``rem`` -- a host page that sets ``html { font-size: 62.5% }`` would
 * otherwise shrink the whole widget -- and the font is the system stack, so
 * no web font is fetched from a visitor's browser on a page that did not ask
 * for one.
 */

export const WIDGET_CSS = `
:host {
  --cb-brand: #4f46e5;
  --cb-ink: #1f2330;
  --cb-ink-soft: #666d80;
  --cb-line: #e3e5ec;
  --cb-surface: #ffffff;
  --cb-canvas: #f7f8fa;
  all: initial;
  position: fixed;
  bottom: 20px;
  z-index: 2147483000;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.45;
  color: var(--cb-ink);
}
:host([data-position="left"]) { left: 20px; }
:host([data-position="right"]) { right: 20px; }

* { box-sizing: border-box; }
button { font: inherit; cursor: pointer; }

.launcher {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 280px;
  padding: 12px 18px;
  border: 0;
  border-radius: 999px;
  background: var(--cb-brand);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 6px 20px rgba(20, 24, 40, 0.18);
}
.launcher:hover { filter: brightness(1.07); }
.launcher-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.panel {
  display: flex;
  flex-direction: column;
  width: 360px;
  height: 520px;
  max-height: calc(100vh - 48px);
  overflow: hidden;
  border: 1px solid var(--cb-line);
  border-radius: 16px;
  background: var(--cb-surface);
  box-shadow: 0 16px 48px rgba(20, 24, 40, 0.22);
}
@media (max-width: 420px) {
  .panel { width: calc(100vw - 32px); height: calc(100vh - 80px); }
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--cb-line);
}
.title { margin: 0; font-size: 14px; font-weight: 600; }
.close {
  padding: 2px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--cb-ink-soft);
  font-size: 18px;
}
.close:hover { background: var(--cb-canvas); color: var(--cb-ink); }

.log {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 16px;
  overflow-y: auto;
  list-style: none;
}
.line { display: flex; flex-direction: column; gap: 4px; max-width: 86%; }
.line.visitor { align-self: flex-end; align-items: flex-end; }
.line.agent { align-self: flex-start; align-items: flex-start; }

.bubble { padding: 9px 13px; border-radius: 16px; white-space: pre-wrap; overflow-wrap: anywhere; }
.visitor .bubble { background: var(--cb-brand); color: #fff; }
.agent .bubble { background: var(--cb-canvas); color: var(--cb-ink); }

.cites { margin: 0; font-size: 12px; color: var(--cb-ink-soft); }
.cites a { color: var(--cb-brand); }

.quick { display: flex; flex-wrap: wrap; gap: 6px; }
.quick button {
  padding: 5px 10px;
  border: 1px solid var(--cb-line);
  border-radius: 999px;
  background: var(--cb-surface);
  color: var(--cb-ink-soft);
  font-size: 12px;
}
.quick button:hover { border-color: var(--cb-brand); color: var(--cb-ink); }

.thinking { font-size: 12px; color: var(--cb-ink-soft); }
.notice { margin: 0 16px 12px; font-size: 13px; color: var(--cb-ink-soft); }

.composer {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--cb-line);
}
.composer input {
  flex: 1;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--cb-line);
  border-radius: 10px;
  background: var(--cb-surface);
  color: var(--cb-ink);
  font: inherit;
}
.composer input:focus { outline: 2px solid var(--cb-brand); outline-offset: -1px; }
.composer button {
  padding: 9px 14px;
  border: 0;
  border-radius: 10px;
  background: var(--cb-brand);
  color: #fff;
  font-weight: 600;
}
.composer button[disabled] { opacity: 0.5; cursor: default; }
`;

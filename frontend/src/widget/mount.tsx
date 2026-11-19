/**
 * Putting the widget on somebody else's page.
 *
 * **It mounts into a shadow root.** That is the one non-negotiable thing
 * about an embedded widget: the host page's CSS cannot reach in and this
 * widget's CSS cannot reach out. A chat bubble that renames a customer's
 * headings is a support ticket, and a customer's reset that shrinks the chat
 * box is the same ticket from the other direction.
 *
 * **The ids come from the script tag.** They are the public address of a chat
 * widget -- the same kind of identifier as the script's own URL -- and the
 * public router is built around that: three endpoints, each refusing unless
 * the agent is live, none of which can read a transcript or see another
 * agent. An id in a URL is a capability, so the capability is small.
 *
 * **The API host defaults to wherever this script was served from.** One
 * fewer attribute to get wrong in a snippet somebody pastes into a CMS, and
 * the common deployment serves the widget from the service that answers it.
 *
 * A missing id is the one thing here that writes to the console. It is a
 * mistake in the embed snippet, nobody but the developer who pasted it can
 * fix it, and failing silently would leave them with a blank corner and no
 * idea why.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import type { WidgetConfig } from "./client";
import { WIDGET_CSS } from "./styles";
import { Widget } from "./Widget";

/** The element the widget attaches itself to, so a second script tag on the
 *  same page does not draw a second chat bubble. */
const HOST_ID = "celeris-bridge-widget";

function originOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url, window.location.href).origin;
  } catch {
    return "";
  }
}

/**
 * Where to talk to, and as whom.
 *
 * Attributes first, query string second. The query string is how the
 * development page passes them, and it stays supported because a customer
 * debugging an embed will reach for it too.
 */
export function configFrom(
  script: HTMLScriptElement | null,
  search: string,
): WidgetConfig | null {
  const params = new URLSearchParams(search);
  const workspaceId =
    script?.dataset["workspace"] ?? params.get("workspace") ?? "";
  const instanceId = script?.dataset["instance"] ?? params.get("instance") ?? "";
  if (!workspaceId || !instanceId) return null;

  const api =
    script?.dataset["api"] ??
    params.get("api") ??
    originOf(script?.src) ??
    window.location.origin;

  return { api: api.replace(/\/+$/, ""), workspaceId, instanceId };
}

/**
 * Draw it.
 *
 * ``script`` is passed in rather than read here, because
 * ``document.currentScript`` is only correct while the script is being
 * evaluated -- by the time React has mounted anything it is null.
 */
export function mountWidget(script: HTMLScriptElement | null): void {
  if (document.getElementById(HOST_ID)) return;

  const config = configFrom(script, window.location.search);
  if (!config) {
    console.error(
      "[celeris-bridge] The widget needs data-workspace and data-instance on its script tag.",
    );
    return;
  }

  const host = document.createElement("div");
  host.id = HOST_ID;
  // Set before the face loads so the widget is never briefly in the wrong
  // corner of the screen.
  host.dataset["position"] = "right";
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = WIDGET_CSS;
  shadow.appendChild(style);

  const mount = document.createElement("div");
  shadow.appendChild(mount);

  createRoot(mount).render(
    <StrictMode>
      <Widget config={config} host={host} />
    </StrictMode>,
  );
}

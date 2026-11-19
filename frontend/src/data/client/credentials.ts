/**
 * Where the bearer token is kept, until real authentication replaces it.
 *
 * ``sessionStorage`` and not ``localStorage``: both are readable by any script
 * on the page, so neither is a safe place for a credential, but a session
 * store is gone when the tab closes rather than sitting on the disk of a
 * shared machine. This whole module is a stand-in -- the real answer is a
 * cookie the browser will not hand to JavaScript, which is the authentication
 * chunk, not this one.
 */

const KEY = "celeris.token";

export function readToken(): string {
  try {
    return window.sessionStorage.getItem(KEY) ?? "";
  } catch {
    // Storage can throw outright in a locked-down browser. A session that
    // cannot be remembered is still a session that works.
    return "";
  }
}

export function writeToken(token: string): void {
  try {
    if (token) window.sessionStorage.setItem(KEY, token);
    else window.sessionStorage.removeItem(KEY);
  } catch {
    /* nothing to do: the token stays in memory for this page only */
  }
}

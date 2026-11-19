/**
 * Who we are talking to the API as.
 *
 * This is the clearest example of the rule that a slice holds only what the
 * server does not know. The token is not server data -- it is the thing that
 * fetches server data -- so it lives here, and everything it fetches lives in
 * the query cache instead.
 */

import { createSlice } from "@reduxjs/toolkit";
import type { PayloadAction } from "@reduxjs/toolkit";

import { readToken, writeToken } from "@/data/client/credentials";

export interface SessionState {
  token: string;
}

/**
 * A token already in the tab wins over the one in the build.
 *
 * ``VITE_API_TOKEN`` is a development convenience and it is baked into the
 * JavaScript, so whatever a person pasted in has to take precedence -- the
 * alternative is an environment variable silently overriding the credential
 * somebody chose.
 */
const initialState: SessionState = {
  token: readToken() || (import.meta.env.VITE_API_TOKEN ?? ""),
};

export const sessionSlice = createSlice({
  name: "session",
  initialState,
  reducers: {
    tokenSet(state, action: PayloadAction<string>) {
      state.token = action.payload.trim();
      writeToken(state.token);
    },
    signedOut(state) {
      state.token = "";
      writeToken("");
    },
  },
  selectors: {
    selectToken: (state) => state.token,
    selectSignedIn: (state) => state.token !== "",
  },
});

export const { tokenSet, signedOut } = sessionSlice.actions;
export const { selectToken, selectSignedIn } = sessionSlice.selectors;

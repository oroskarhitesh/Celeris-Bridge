/**
 * The one API object. Every resource injects its endpoints into this.
 *
 * Why one and not several: the cache, the tags and the middleware are all
 * per-``createApi``. Two of them would mean a mutation in one being unable to
 * invalidate a list in the other, which is the bug you find out about by
 * watching a deleted row stay on the screen.
 *
 * It has no endpoints of its own. ``src/data/api/*.ts`` each call
 * ``injectEndpoints`` so that a resource is one file, and adding a resource
 * does not edit this one.
 */

import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import type { RootState } from "@/data/store/store";

import { TAGS } from "./tags";

/** No trailing slash, so every endpoint can start with one. */
const baseUrl = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

export const baseApi = createApi({
  reducerPath: "api",
  tagTypes: TAGS,
  baseQuery: fetchBaseQuery({
    baseUrl,
    prepareHeaders: (headers, { getState }) => {
      // The token comes from the store rather than from storage directly, so
      // that signing out is a dispatch and every in-flight request after it
      // uses the new state.
      const { token } = (getState() as RootState).session;
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return headers;
    },
  }),
  // The workspace is never sent. The backend derives it from the token, and
  // a frontend that could name a workspace would be a frontend that could
  // name somebody else's.
  endpoints: () => ({}),
});

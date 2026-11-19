/**
 * The store: one query cache and two small slices.
 *
 * ``baseApi.reducer`` is the only place server data is kept. The slices hold
 * the session and the interface's own state, which is the whole of what the
 * backend cannot tell us.
 */

import { configureStore } from "@reduxjs/toolkit";
import { setupListeners } from "@reduxjs/toolkit/query";

import { baseApi } from "@/data/client/baseApi";

import { draftSlice } from "./slices/draftSlice";
import { sessionSlice } from "./slices/sessionSlice";
import { uiSlice } from "./slices/uiSlice";

/**
 * A store, not *the* store.
 *
 * The application uses the one below. A test builds its own, because the
 * query cache is part of the store: two tests sharing it would be two tests
 * sharing yesterday's answers.
 */
export function makeStore(preloaded?: Partial<SessionSeed>) {
  return configureStore({
    reducer: {
      [baseApi.reducerPath]: baseApi.reducer,
      [sessionSlice.reducerPath]: sessionSlice.reducer,
      [uiSlice.reducerPath]: uiSlice.reducer,
      [draftSlice.reducerPath]: draftSlice.reducer,
    },
    middleware: (getDefault) => getDefault().concat(baseApi.middleware),
    ...(preloaded?.session ? { preloadedState: { session: preloaded.session } } : {}),
  });
}

interface SessionSeed {
  session: { token: string };
}

export const store = makeStore();

// Refetch when the tab comes back or the network returns. Cheap, and it means
// a laptop reopened an hour later is not showing an hour-old agent list.
setupListeners(store.dispatch);

export type AppStore = ReturnType<typeof makeStore>;
export type RootState = ReturnType<AppStore["getState"]>;
export type AppDispatch = AppStore["dispatch"];

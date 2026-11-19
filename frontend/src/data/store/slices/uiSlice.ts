/**
 * Transient things the server has no opinion about.
 *
 * Toasts are here rather than in a React context because the thing that most
 * often needs to raise one is a mutation's ``catch``, which is not a
 * component: a dispatch works from anywhere, a context does not.
 */

import { createSlice, nanoid } from "@reduxjs/toolkit";
import type { PayloadAction } from "@reduxjs/toolkit";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
}

export interface UiState {
  toasts: Toast[];
}

const initialState: UiState = { toasts: [] };

export const uiSlice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    toastRaised: {
      reducer(state, action: PayloadAction<Toast>) {
        // Newest first, and capped: a failing retry loop should not be able
        // to cover the page it is reporting about.
        state.toasts = [action.payload, ...state.toasts].slice(0, 4);
      },
      prepare(message: string, kind: ToastKind = "info") {
        return { payload: { id: nanoid(), kind, message } };
      },
    },
    toastDismissed(state, action: PayloadAction<string>) {
      state.toasts = state.toasts.filter((one) => one.id !== action.payload);
    },
  },
  selectors: {
    selectToasts: (state) => state.toasts,
  },
});

export const { toastRaised, toastDismissed } = uiSlice.actions;
export const { selectToasts } = uiSlice.selectors;

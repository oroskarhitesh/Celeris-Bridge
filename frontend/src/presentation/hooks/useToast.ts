/**
 * Raise a toast without each caller importing the slice.
 *
 * Takes the error object for a failure, so that the words shown come from the
 * data layer's ``messageOf`` -- the same sentence the inline notices use, not
 * a second opinion written at the call site.
 *
 * ``warn`` is the exception, and it exists for one real case: a response that
 * is not JSON. A failed file download arrives as bytes, so there is no error
 * envelope to read and the caller has to supply the words itself.
 */

import { useCallback } from "react";

import type { ApiError } from "@/data/client/errors";
import { messageOf } from "@/data/client/errors";
import { useAppDispatch } from "@/data/store/hooks";
import { toastRaised } from "@/data/store/slices/uiSlice";

export function useToast() {
  const dispatch = useAppDispatch();

  const say = useCallback(
    (message: string) => dispatch(toastRaised(message, "success")),
    [dispatch],
  );

  const complain = useCallback(
    (error: ApiError | undefined) => dispatch(toastRaised(messageOf(error), "error")),
    [dispatch],
  );

  const warn = useCallback(
    (message: string) => dispatch(toastRaised(message, "error")),
    [dispatch],
  );

  return { say, complain, warn };
}

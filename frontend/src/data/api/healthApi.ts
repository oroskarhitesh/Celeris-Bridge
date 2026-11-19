/**
 * Liveness and readiness.
 *
 * Neither needs a credential, which is what makes them the right first call:
 * if ``/readyz`` answers from a browser then the base URL, CORS and the
 * backend's own database are all working, and anything that fails after this
 * is about the token rather than the wiring.
 */

import { baseApi } from "@/data/client/baseApi";
import type { Liveness, Readiness } from "@/data/types/health";

export const healthApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    liveness: build.query<Liveness, void>({
      query: () => "/health",
      providesTags: ["Health"],
    }),
    readiness: build.query<Readiness, void>({
      query: () => "/readyz",
      providesTags: ["Health"],
    }),
  }),
});

export const { useLivenessQuery, useReadinessQuery } = healthApi;

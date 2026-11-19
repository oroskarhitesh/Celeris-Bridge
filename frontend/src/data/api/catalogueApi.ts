/**
 * What this workspace is allowed to create.
 *
 * The cheapest authenticated read in the API, which is why it doubles as the
 * credential probe on the overview page: one call answers both "is this type
 * available" and "is this token accepted".
 */

import { baseApi } from "@/data/client/baseApi";
import type { CatalogueEntry } from "@/data/types/catalogue";

export const catalogueApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    catalogue: build.query<CatalogueEntry[], void>({
      query: () => "/v1/catalogue",
      providesTags: ["Catalogue"],
    }),
  }),
});

export const { useCatalogueQuery } = catalogueApi;

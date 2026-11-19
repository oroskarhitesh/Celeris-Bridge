import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Explicit, because the suite does not use globals: without this, one test's
// markup is still in the document while the next one queries it.
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

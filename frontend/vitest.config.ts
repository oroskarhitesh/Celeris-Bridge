/**
 * The test run, which is the application's own config plus a DOM.
 *
 * Merged rather than rewritten so that the alias and the plugins cannot drift
 * away from the ones the real build uses -- a test suite that resolves
 * imports differently from production is a test suite that passes
 * differently.
 */
import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: false,
      include: ["tests/**/*.test.{ts,tsx}"],
      setupFiles: ["./tests/support/setup.ts"],
      restoreMocks: true,
    },
  }),
);

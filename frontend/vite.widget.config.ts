/**
 * The widget's own build. A second config, on purpose.
 *
 * The admin application is a hashed bundle loaded by one HTML file this
 * project controls. The widget is the opposite: a single file at a **stable
 * URL**, pasted into a customer's page in a script tag that may sit in a CMS
 * template nobody wants to edit again. A hashed filename would mean asking
 * them to change it on every release, so this builds a library rather than a
 * page.
 *
 * ``iife`` and not ``es``: a classic script works in a plain ``<script>``
 * tag, which is what an embed snippet is, and it needs no ``type="module"``
 * for the person pasting it to remember.
 *
 * It writes to ``dist/widget/`` so that one ``vite build`` for the
 * application and one for this can both be served from the same place
 * without either clearing the other -- hence ``emptyOutDir: false``.
 *
 * No Tailwind plugin here, and nothing imports a stylesheet: the widget's CSS
 * is a string injected into its shadow root. See ``src/widget/styles.ts`` for
 * why that is not laziness.
 */

import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist/widget",
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL("./src/widget/index.tsx", import.meta.url)),
      name: "CelerisBridgeWidget",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    // Nothing is marked external, which is the point: React is bundled in.
    // A customer's page may have its own React, a different version of it,
    // or none at all, and a widget that depends on finding one is a widget
    // that breaks on somebody else's site. The cost is in the file size, and
    // it is paid knowingly.
  },
  define: {
    // Rollup's IIFE build has no ``process``, and React reads this to decide
    // whether to ship its development warnings.
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});

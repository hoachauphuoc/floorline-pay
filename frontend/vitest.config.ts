import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * Scoped deliberately narrowly.
 *
 * This suite covers two things and no more: the `format.ts` helpers against absent
 * input, and each screen's loading / error / empty branches. Both are classes of bug
 * that have already shipped here -- `formatUsd(undefined)` blanked a page, and three
 * separate fields were read under a `!= null` guard that silently rendered nothing.
 *
 * What it deliberately does NOT do is snapshot components. A snapshot asserts that the
 * markup is what it currently is, which passes for as long as nobody looks at it, and
 * this project has already learned to distrust that shape of test: two backend tests
 * re-implemented the code in the test body and asserted on their own literal.
 *
 * No @vitejs/plugin-react: its dependency tree pulls a conflicting @babel/core, and it
 * exists for Fast Refresh, which tests do not use. Vitest transforms JSX through esbuild
 * using the `jsx: "react-jsx"` already set in tsconfig.json.
 */
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    // AG Grid builds its rows asynchronously and a grid page takes 2-3 s to render under jsdom. Under the load of the whole
    // suite that crosses the 5 s default, so the same tests passed alone and failed together.
    testTimeout: 20_000,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // The Next build output and node_modules both contain files that match the include
    // glob once compiled; excluding them keeps a watch run from thrashing.
    exclude: ["node_modules", ".next", "dist"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}"],
      reporter: ["text-summary", "json-summary"],
      // A floor under the figure measured on 2026-10-19 (38% of lines: most screens have no
      // test, by the scope decision above), so it can only go up, not quietly down.
      thresholds: { lines: 35, statements: 35, functions: 60, branches: 75 },
    },
  },
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});

// Unit tests for the browser-side pieces that cannot be checked by the backend tests:
// the HTML cleaning and file recognition in src/grader/csv.ts. Development only.
import { defineConfig } from "vitest/config";

export default defineConfig({
  // The tests read the made-up fixtures the backend tests use too (backend/tests/fixtures).
  server: { fs: { allow: [".."] } },
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.ts"],
  },
});

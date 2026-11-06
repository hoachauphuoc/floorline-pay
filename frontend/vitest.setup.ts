import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// The same reason: findBy* and waitFor give up after 1 s by default, which is shorter than a loaded grid takes to draw its rows.
configure({ asyncUtilTimeout: 5000 });

// jsdom persists the document between tests in the same file, so a component left
// mounted by one test is still in the tree when the next one queries it -- which makes
// `getByText` find the previous test's element and pass for the wrong reason.
afterEach(() => {
  cleanup();
});

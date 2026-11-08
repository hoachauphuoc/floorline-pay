import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createPaymentOrder: vi.fn(),
}));

import BookPage from "./page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BookPage />
    </QueryClientProvider>,
  );
}

describe("BookPage introduction", () => {
  it("says the carrier is paid on a published decision, a person's or the agent's inside a limit", () => {
    renderPage();
    expect(screen.getByText(/only on a published decision/)).toBeInTheDocument();
    expect(screen.getByText(/inside a limit a person set/)).toBeInTheDocument();
    expect(screen.getByText(/returned to you if it is blocked/)).toBeInTheDocument();
  });

  it("no longer says that only a person releases the money", () => {
    renderPage();
    expect(screen.queryByText(/only after a person releases/)).not.toBeInTheDocument();
  });
});

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

describe("BookPage on a phone", () => {
  it("uses 16px fields that grow back to 12px from the md breakpoint", () => {
    renderPage();
    const fields = screen.getAllByRole("textbox");
    expect(fields.length).toBeGreaterThan(5);
    for (const input of fields) {
      const tokens = input.className.split(" ");
      expect(tokens).toEqual(expect.arrayContaining(["text-base", "h-11", "md:text-[12px]", "md:h-8"]));
      expect(tokens).not.toContain("text-[12px]");
    }
  });

  it("makes the Pay with PayPal button 44px tall and full width on a phone", () => {
    renderPage();
    const tokens = screen.getByRole("button", { name: /Pay with PayPal/ }).className.split(" ");
    expect(tokens).toEqual(expect.arrayContaining(["h-11", "w-full", "md:h-8", "md:w-auto"]));
  });
});

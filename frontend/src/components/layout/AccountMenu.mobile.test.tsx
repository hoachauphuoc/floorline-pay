import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { AccountMenu } from "./AccountMenu";

const mocks = vi.hoisted(() => ({ fetch: vi.fn() }));

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({
    data: { authenticated_by: "console_session", role: "governance_admin", role_source: "ADMIN_EMAILS" },
  }),
}));

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

describe("AccountMenu on a phone", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mocks.fetch);
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    mocks.fetch.mockReset();
    mocks.fetch.mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ email: "ada@example.com", exp: 1_900_000_000, amr: "password" }),
    }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("hides the role chip below the sm breakpoint and shows it from sm up", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <AccountMenu />
        </TooltipProvider>
      </QueryClientProvider>,
    );
    const chip = await screen.findByRole("link", { name: /Your role/, hidden: true });
    expect(chip).toHaveClass("hidden", "sm:block");
  });
});

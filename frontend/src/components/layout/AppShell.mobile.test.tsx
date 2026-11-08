import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/pay" }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchReviewQueue: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("@/lib/identity", () => ({ useIdentity: () => ({ data: undefined, isError: false }) }));
vi.mock("@/lib/tenant-context", () => ({
  useTenant: () => ({ tenant: { id: "t1" } }),
  displayTenant: () => undefined,
}));
vi.mock("@/components/layout/Header", () => ({ Header: () => null }));

import { AppShell } from "./AppShell";

it("lets the phone drawer scroll to its last item", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AppShell>
        <p>page</p>
      </AppShell>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
  const nav = within(screen.getByRole("dialog")).getByRole("navigation");
  expect(nav.className.split(" ")).toEqual(expect.arrayContaining(["min-h-0", "flex-1", "overflow-y-auto"]));
});

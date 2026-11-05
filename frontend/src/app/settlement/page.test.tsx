import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Identity } from "@/lib/identity";

const mocks = vi.hoisted(() => ({
  fetchFrozenParties: vi.fn(),
  fetchPaymentLedger: vi.fn(),
  liftFreeze: vi.fn(),
  fetchPendingPayPalEvents: vi.fn(),
  reconcilePayPalEvents: vi.fn(),
  identity: { value: undefined as unknown },
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchFrozenParties: mocks.fetchFrozenParties,
  fetchPaymentLedger: mocks.fetchPaymentLedger,
  liftFreeze: mocks.liftFreeze,
  fetchPendingPayPalEvents: mocks.fetchPendingPayPalEvents,
  reconcilePayPalEvents: mocks.reconcilePayPalEvents,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a", name: "Tenant A", descriptor: "Test tenant" } }),
}));
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({ data: mocks.identity.value, isError: false }),
}));

import SettlementPage from "./page";

// AG Grid and Radix measure their containers with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

function person(role: "governance_admin" | "operator", session: Identity["session_method"] = "password"): Identity {
  return {
    email: "admin@forwarder.example",
    authenticated_by: "console_session",
    acts_for_a_person: true,
    role,
    roles: [role],
    grants: role === "governance_admin" ? ["viewer", "reviewer", "operator", "governance_admin"] : ["viewer", "reviewer", "operator"],
    role_source: "ADMIN_EMAILS",
    tenant_id: null,
    session_method: session,
  };
}

const FROZEN = {
  kind: "carrier", party: "mekonghaulageco", name: "Mekong Haulage Co",
  reason: "a dispute was opened on a payment", source: "event-1", at: "2026-11-03T10:00:00+00:00",
};
const LEDGER = {
  items: [
    {
      case_id: "CASE-A", state: "paid" as const, reason: "captured and paid",
      payout_status: { state: "confirmed" as const, status: "SUCCESS", at: "2026-11-03T10:00:00+00:00" },
    },
    {
      case_id: "CASE-B", state: "paid" as const, reason: "captured and paid",
      payout_status: { state: "failed" as const, status: "RETURNED", at: "2026-11-03T11:00:00+00:00" },
    },
    { case_id: "CASE-C", state: "held" as const, reason: "the payment checks hold this payout" },
  ],
  counts: { paid: 2, held: 1 },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SettlementPage />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("SettlementPage", () => {
  beforeEach(() => {
    mocks.fetchFrozenParties.mockReset();
    mocks.fetchPaymentLedger.mockReset();
    mocks.liftFreeze.mockReset();
    mocks.fetchPendingPayPalEvents.mockReset();
    mocks.reconcilePayPalEvents.mockReset();
    mocks.fetchPendingPayPalEvents.mockResolvedValue({ pending: 0 });
    mocks.identity.value = person("governance_admin");
    mocks.fetchPaymentLedger.mockResolvedValue(LEDGER);
  });

  it("says how many PayPal events wait and lets an operator apply them", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [] });
    mocks.fetchPendingPayPalEvents.mockResolvedValue({ pending: 2 });
    mocks.reconcilePayPalEvents.mockResolvedValue({ applied: 1, pending: 1 });
    mocks.identity.value = person("operator");
    renderPage();
    expect(await screen.findByText(/2 PayPal events arrived before/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply waiting events" }));
    await waitFor(() => expect(mocks.reconcilePayPalEvents).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Applied 1; 1 still waiting.")).toBeInTheDocument();
  });

  it("offers nothing to apply when no event waits", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [] });
    renderPage();
    expect(await screen.findByText("No PayPal event is waiting.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply waiting events" })).not.toBeInTheDocument();
  });

  it("keeps the page when the count cannot be read", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [] });
    mocks.fetchPendingPayPalEvents.mockRejectedValue(new Error("down"));
    renderPage();
    expect(await screen.findByText("Nobody is frozen")).toBeInTheDocument();
    expect(await screen.findByText("That request failed")).toBeInTheDocument();
  });

  it("says so when nobody is frozen", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [] });
    renderPage();
    expect(await screen.findByText("Nobody is frozen")).toBeInTheDocument();
  });

  it("shows each payout as PayPal reports it, and nothing as confirmed when PayPal has not said", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [] });
    renderPage();
    expect(await screen.findByText("Confirmed by PayPal (SUCCESS)")).toBeInTheDocument();
    expect(screen.getByText("Failed, returned or blocked (RETURNED)")).toBeInTheDocument();
    expect(screen.getAllByText(/Confirmed by PayPal/)).toHaveLength(1);
  });

  it("lists a frozen party with the reason", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [FROZEN] });
    renderPage();
    expect(await screen.findByText("Mekong Haulage Co")).toBeInTheDocument();
    expect(screen.getByText("a dispute was opened on a payment")).toBeInTheDocument();
  });

  it("lifts a freeze with the reason a governance admin gives", async () => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [FROZEN] });
    mocks.liftFreeze.mockResolvedValue({ frozen: [] });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Select/ }));
    const lift = screen.getByRole("button", { name: "Lift freeze" });
    expect(lift).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Why it is safe to pay this party again/), {
      target: { value: "  settled with the carrier  " },
    });
    expect(lift).toBeEnabled();
    fireEvent.click(lift);
    await waitFor(() =>
      expect(mocks.liftFreeze).toHaveBeenCalledWith({
        kind: "carrier", name: "Mekong Haulage Co", reason: "settled with the carrier",
      }),
    );
  });

  it.each([
    ["an operator", () => person("operator")],
    ["a governance admin signed in with one click", () => person("governance_admin", "one_click")],
  ])("does not let %s lift a freeze", async (_who, identity) => {
    mocks.fetchFrozenParties.mockResolvedValue({ frozen: [FROZEN] });
    mocks.identity.value = identity();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Select/ }));
    fireEvent.change(screen.getByLabelText(/Why it is safe to pay this party again/), { target: { value: "checked" } });
    expect(screen.getByRole("button", { name: "Lift freeze" })).toBeDisabled();
  });
});

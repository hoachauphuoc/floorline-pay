import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { CarrierRow } from "@/lib/api";
import type { Identity } from "@/lib/identity";

const mocks = vi.hoisted(() => ({
  fetchCarriers: vi.fn(),
  registerCarrierPayee: vi.fn(),
  verifyCarrierPayee: vi.fn(),
  identity: { value: undefined as unknown },
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchCarriers: mocks.fetchCarriers,
  registerCarrierPayee: mocks.registerCarrierPayee,
  verifyCarrierPayee: mocks.verifyCarrierPayee,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a", name: "Tenant A", descriptor: "Test tenant" } }),
}));
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({ data: mocks.identity.value, isError: false }),
}));

import CarriersPage from "./page";

// Radix measures floating content with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

/**
 * The screen of a payee-change request, console side: the person registering can paste the
 * message that asked for the change, and the person verifying is told when it was flagged.
 */

const OPERATOR = "operator@forwarder.example";
const REVIEWER = "reviewer@forwarder.example";

function person(email: string): Identity {
  return {
    email,
    authenticated_by: "console_session",
    acts_for_a_person: true,
    role: "operator",
    roles: ["operator"],
    grants: ["viewer", "reviewer", "operator"],
    role_source: "OPERATOR_EMAILS",
    tenant_id: null,
    session_method: "password",
  };
}

function carrier(overrides: Partial<CarrierRow> = {}): CarrierRow {
  return { carrier_key: "mekonghaulageco", carrier_name: "Mekong Haulage Co", current: null, pending: [], ...overrides };
}

const PENDING = {
  version: 2,
  payee_email: "accounts@mekong-haulage.example",
  registered_by: OPERATOR,
  registered_at: "2026-11-03T10:00:00+00:00",
};
const VERIFIED = {
  version: 1,
  payee_email: "payments@mekong-haulage.example",
  registered_by: OPERATOR,
  verified_by: REVIEWER,
  verified_at: "2026-11-02T10:00:00+00:00",
  changed_at: null,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <CarriersPage />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("CarriersPage payee-change screen", () => {
  beforeEach(() => {
    mocks.fetchCarriers.mockReset();
    mocks.registerCarrierPayee.mockReset();
    mocks.verifyCarrierPayee.mockReset();
    mocks.identity.value = person(REVIEWER);
  });

  it("sends the message that asked for the change, trimmed, with the registration", async () => {
    mocks.fetchCarriers.mockResolvedValue({ items: [] });
    mocks.registerCarrierPayee.mockResolvedValue({ carrier_key: "k", version: 1, status: "pending_verification" });
    mocks.identity.value = person(OPERATOR);
    renderPage();
    const button = await screen.findByRole("button", { name: "Register payee" });
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "Mekong Haulage Co" } });
    fireEvent.change(screen.getByLabelText("PayPal account to pay"), { target: { value: "payments@mekong-haulage.example" } });
    fireEvent.change(screen.getByLabelText(/Message that asked for the change/), { target: { value: "  Please do not call.  " } });
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.registerCarrierPayee).toHaveBeenCalledWith({
        carrier_name: "Mekong Haulage Co",
        payee_email: "payments@mekong-haulage.example",
        request_text: "Please do not call.",
      }),
    );
  });

  it("tells the verifying person why a pending request was flagged", async () => {
    mocks.fetchCarriers.mockResolvedValue({ items: [carrier({
      pending: [{ ...PENDING, change_request_flagged: true, change_request_reasons: ["asks not to be called back"] }],
    })] });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/asks not to be called back/);
    expect(alert.textContent).toMatch(/Call the carrier on a number you already hold/);
  });

  it("shows no warning for a pending request that was not flagged", async () => {
    mocks.fetchCarriers.mockResolvedValue({ items: [carrier({
      pending: [{ ...PENDING, change_request_flagged: false, change_request_reasons: [] }],
    })] });
    renderPage();
    await waitFor(() => expect(screen.getByText("accounts@mekong-haulage.example")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("warns that payouts to a verified payee whose request was flagged are held", async () => {
    mocks.fetchCarriers.mockResolvedValue({ items: [carrier({ current: { ...VERIFIED, change_request_flagged: true } })] });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/payouts to it are held/);
  });

  it("shows no warning for a verified payee whose request was not flagged", async () => {
    mocks.fetchCarriers.mockResolvedValue({ items: [carrier({ current: VERIFIED })] });
    renderPage();
    await waitFor(() => expect(screen.getByText("payments@mekong-haulage.example")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

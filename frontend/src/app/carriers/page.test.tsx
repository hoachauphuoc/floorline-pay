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

const OPERATOR = "operator@forwarder.example";
const REVIEWER = "reviewer@forwarder.example";

function person(email: string, session: Identity["session_method"] = "password"): Identity {
  return {
    email,
    authenticated_by: "console_session",
    acts_for_a_person: true,
    role: "operator",
    roles: ["operator"],
    grants: ["viewer", "reviewer", "operator"],
    role_source: "OPERATOR_EMAILS",
    tenant_id: null,
    session_method: session,
  };
}

function carrier(overrides: Partial<CarrierRow> = {}): CarrierRow {
  return {
    carrier_key: "mekonghaulageco",
    carrier_name: "Mekong Haulage Co",
    current: null,
    pending: [],
    ...overrides,
  };
}

const VERIFIED = {
  version: 1,
  payee_email: "payments@mekong-haulage.example",
  registered_by: OPERATOR,
  verified_by: REVIEWER,
  verified_at: "2026-11-02T10:00:00+00:00",
  changed_at: null,
};
const PENDING = {
  version: 2,
  payee_email: "accounts@mekong-haulage.example",
  registered_by: OPERATOR,
  registered_at: "2026-11-03T10:00:00+00:00",
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

function list(items: CarrierRow[]) {
  mocks.fetchCarriers.mockResolvedValue({ items });
}

describe("CarriersPage", () => {
  beforeEach(() => {
    mocks.fetchCarriers.mockReset();
    mocks.registerCarrierPayee.mockReset();
    mocks.verifyCarrierPayee.mockReset();
    mocks.identity.value = person(OPERATOR);
  });

  it("says so when there are no carriers", async () => {
    list([]);
    renderPage();
    await waitFor(() => expect(screen.getByText("No carriers yet")).toBeInTheDocument());
  });

  it("shows a carrier's verified payee and who verified it", async () => {
    list([carrier({ current: VERIFIED })]);
    renderPage();
    await waitFor(() => expect(screen.getByText("payments@mekong-haulage.example")).toBeInTheDocument());
    expect(screen.getByText(/verified by reviewer@forwarder.example/)).toBeInTheDocument();
  });

  it("says nothing can be paid to a carrier with no verified payee", async () => {
    list([carrier()]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/nothing can be paid to this carrier/)).toBeInTheDocument(),
    );
  });

  it("warns that a changed payee waits out the cooling-off period", async () => {
    list([carrier({ current: { ...VERIFIED, changed_at: "2026-11-02T10:00:00+00:00" } })]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/cooling-off period/)).toBeInTheDocument());
  });

  it("registers a payee for the trimmed carrier and account", async () => {
    list([]);
    mocks.registerCarrierPayee.mockResolvedValue({ carrier_key: "k", version: 1, status: "pending_verification" });
    renderPage();
    const button = await screen.findByRole("button", { name: "Register payee" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "  Mekong Haulage Co " } });
    fireEvent.change(screen.getByLabelText("PayPal account to pay"), {
      target: { value: " payments@mekong-haulage.example " },
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.registerCarrierPayee).toHaveBeenCalledWith({
        carrier_name: "Mekong Haulage Co",
        payee_email: "payments@mekong-haulage.example",
      }),
    );
  });

  it("shows the registry's refusal when a registration fails", async () => {
    list([]);
    mocks.registerCarrierPayee.mockRejectedValue(new Error("this is already the verified payee of this carrier"));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Carrier"), { target: { value: "Mekong" } });
    fireEvent.change(screen.getByLabelText("PayPal account to pay"), { target: { value: "a@b.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Register payee" }));
    await waitFor(() =>
      expect(screen.getByText(/already the verified payee of this carrier/)).toBeInTheDocument(),
    );
  });

  it("verifies a pending payee with the callback note, and only with one", async () => {
    mocks.identity.value = person(REVIEWER);
    list([carrier({ current: VERIFIED, pending: [PENDING] })]);
    mocks.verifyCarrierPayee.mockResolvedValue({ carrier_key: "mekonghaulageco", version: 2, status: "verified" });
    renderPage();
    const button = await screen.findByRole("button", { name: "Verify payee" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Callback note"), {
      target: { value: " called +84 28 5555 0100; they confirmed " },
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.verifyCarrierPayee).toHaveBeenCalledWith(
        "mekonghaulageco",
        2,
        "called +84 28 5555 0100; they confirmed",
      ),
    );
  });

  it("tells the person who registered a payee that a different person must verify it", async () => {
    mocks.identity.value = person(OPERATOR);
    list([carrier({ pending: [PENDING] })]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("A different person must verify this payee.")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Verify payee" })).not.toBeInTheDocument();
  });

  it("locks both actions for a one-click session", async () => {
    mocks.identity.value = person(REVIEWER, "one_click");
    list([carrier({ pending: [PENDING] })]);
    renderPage();
    const verify = await screen.findByRole("button", { name: "Verify payee" });
    fireEvent.change(screen.getByLabelText("Callback note"), { target: { value: "called" } });
    expect(verify).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "Mekong" } });
    fireEvent.change(screen.getByLabelText("PayPal account to pay"), { target: { value: "a@b.example" } });
    expect(screen.getByRole("button", { name: "Register payee" })).toBeDisabled();
  });

  it("shows the error when the registry cannot be read", async () => {
    mocks.fetchCarriers.mockRejectedValue(new Error("registry unavailable"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/registry unavailable/)).toBeInTheDocument());
  });
});

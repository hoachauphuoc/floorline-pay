import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { PaymentControls } from "@/lib/api";
import type { Identity } from "@/lib/identity";

const mocks = vi.hoisted(() => ({
  fetchPaymentControls: vi.fn(),
  pausePayments: vi.fn(),
  resumePayments: vi.fn(),
  publishPaymentLimits: vi.fn(),
  identity: { value: undefined as unknown },
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentControls: mocks.fetchPaymentControls,
  pausePayments: mocks.pausePayments,
  resumePayments: mocks.resumePayments,
  publishPaymentLimits: mocks.publishPaymentLimits,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a", name: "Tenant A", descriptor: "Test tenant" } }),
}));
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({ data: mocks.identity.value, isError: false }),
}));

import ControlsPage from "./page";

// Radix measures floating content with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

function person(role: "operator" | "governance_admin", session: Identity["session_method"] = "password"): Identity {
  const grants: Identity["grants"] =
    role === "governance_admin"
      ? ["viewer", "reviewer", "operator", "governance_admin"]
      : ["viewer", "reviewer", "operator"];
  return {
    email: `${role}@forwarder.example`,
    authenticated_by: "console_session",
    acts_for_a_person: true,
    role,
    roles: [role],
    grants,
    role_source: "ADMIN_EMAILS",
    tenant_id: null,
    session_method: session,
  };
}

const LIMITS = {
  max_payout_usd: 5000,
  carrier_daily_usd: 10000,
  tenant_daily_usd: 50000,
  carrier_daily_count: 5,
};

function controls(overrides: Partial<PaymentControls> = {}): PaymentControls {
  return {
    pause: { paused: false, reason: null, by: null, at: null },
    limits: LIMITS,
    limits_are_defaults: true,
    usage_today: { tenant_usd: "0", carriers: {} },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ControlsPage />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("ControlsPage", () => {
  beforeEach(() => {
    mocks.fetchPaymentControls.mockReset();
    mocks.pausePayments.mockReset();
    mocks.resumePayments.mockReset();
    mocks.publishPaymentLimits.mockReset();
    mocks.identity.value = person("governance_admin");
    mocks.fetchPaymentControls.mockResolvedValue(controls());
  });

  it("says payments are running and offers to pause them", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Payments are running")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Pause payments" })).toBeDisabled();
  });

  it("pauses payments with the reason typed", async () => {
    mocks.pausePayments.mockResolvedValue({ paused: true });
    renderPage();
    const button = await screen.findByRole("button", { name: "Pause payments" });
    fireEvent.change(screen.getByLabelText("Why payments should stop"), {
      target: { value: "  a carrier was phished  " },
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(mocks.pausePayments).toHaveBeenCalledWith("a carrier was phished"));
  });

  it("says who paused payments and why, and offers to resume them instead", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(
      controls({ pause: { paused: true, reason: "a carrier was phished", by: "operator@forwarder.example", at: "2026-11-02T10:00:00+00:00" } }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Payments are paused")).toBeInTheDocument());
    expect(screen.getByText(/a carrier was phished/)).toBeInTheDocument();
    expect(screen.getByText(/A block still returns the shipper/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause payments" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume payments" })).toBeInTheDocument();
  });

  it("resumes payments with the reason typed", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(controls({ pause: { paused: true, reason: "x", by: "o@x.example" } }));
    mocks.resumePayments.mockResolvedValue({ paused: false });
    renderPage();
    const button = await screen.findByRole("button", { name: "Resume payments" });
    fireEvent.change(screen.getByLabelText("Why payments can resume"), { target: { value: "checked by phone" } });
    fireEvent.click(button);
    await waitFor(() => expect(mocks.resumePayments).toHaveBeenCalledWith("checked by phone"));
  });

  it("shows the limits in force and says when they are the defaults", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Largest single payout (USD)")).toBeInTheDocument());
    expect(screen.getByText("5000")).toBeInTheDocument();
    expect(screen.getByText(/nobody has published limits yet/)).toBeInTheDocument();
  });

  it("does not call published limits the defaults", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(controls({ limits_are_defaults: false }));
    renderPage();
    await waitFor(() => expect(screen.getByText("Largest single payout (USD)")).toBeInTheDocument());
    expect(screen.queryByText(/nobody has published limits yet/)).not.toBeInTheDocument();
  });

  it("says nothing is paid when the published limits cannot be read", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(controls({ limits: null }));
    renderPage();
    await waitFor(() => expect(screen.getByText(/nothing is paid until a governance admin/)).toBeInTheDocument());
  });

  it("publishes the limits typed, as numbers, with the reason", async () => {
    mocks.publishPaymentLimits.mockResolvedValue({ limits: LIMITS });
    renderPage();
    const button = await screen.findByRole("button", { name: "Publish limits" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("New: Largest single payout (USD)"), { target: { value: "2000" } });
    fireEvent.change(screen.getByLabelText("New: Per carrier per day (USD)"), { target: { value: "4000" } });
    fireEvent.change(screen.getByLabelText("New: All carriers per day (USD)"), { target: { value: "8000" } });
    fireEvent.change(screen.getByLabelText("New: Payouts per carrier per day"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Why the limits change"), { target: { value: " quarter budget " } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.publishPaymentLimits).toHaveBeenCalledWith(
        { max_payout_usd: 2000, carrier_daily_usd: 4000, tenant_daily_usd: 8000, carrier_daily_count: 3 },
        "quarter budget",
      ),
    );
  });

  it("shows what was paid today in all and per carrier", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(
      controls({ usage_today: { tenant_usd: "2500.10", carriers: { mekonghaulageco: { usd: "1250.10", count: 1 }, other: { usd: "1250.00", count: 2 } } } }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("2500.10")).toBeInTheDocument());
    const list = screen.getByRole("list", { name: "Paid today by carrier" });
    expect(list).toHaveTextContent("mekonghaulageco 1250.10 USD in 1 payout");
    expect(list).toHaveTextContent("other 1250.00 USD in 2 payouts");
  });

  it("locks pausing for a one-click session and resuming and publishing for an operator", async () => {
    mocks.identity.value = person("operator", "one_click");
    renderPage();
    const pause = await screen.findByRole("button", { name: "Pause payments" });
    fireEvent.change(screen.getByLabelText("Why payments should stop"), { target: { value: "stop" } });
    expect(pause).toBeDisabled();
    expect(screen.getByRole("button", { name: "Publish limits" })).toBeDisabled();
  });

  it("shows the error when the controls cannot be read", async () => {
    mocks.fetchPaymentControls.mockRejectedValue(new Error("controls unavailable"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/controls unavailable/)).toBeInTheDocument());
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { PaymentControls } from "@/lib/api";
import type { Identity } from "@/lib/identity";

const mocks = vi.hoisted(() => ({ fetchPaymentControls: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentControls: mocks.fetchPaymentControls,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a", name: "Tenant A", descriptor: "Test tenant" } }),
}));
const ADMIN: Identity = {
  email: "governance_admin@forwarder.example",
  authenticated_by: "console_session",
  acts_for_a_person: true,
  role: "governance_admin",
  roles: ["governance_admin"],
  grants: ["viewer", "reviewer", "operator", "governance_admin"],
  role_source: "ADMIN_EMAILS",
  tenant_id: null,
  session_method: "password",
};
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({ data: ADMIN, isError: false }),
}));

import { helpFor } from "@/lib/help-content";
import { setHelpMode } from "@/lib/help-mode";

import ControlsPage from "./page";

// Radix measures floating content with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

const ENTRIES: Array<[string, string, RegExp]> = [
  ["controls.pause", "Pause switch", /only a governance admin can resume them/],
  ["controls.limits", "Limits in force", /If they cannot be read, nothing is paid/],
  ["controls.max_payout_usd", "Largest single payout (USD)", /held by the settler and nothing is paid/],
  ["controls.carrier_daily_usd", "Per carrier per day (USD)", /one carrier past this total/],
  ["controls.tenant_daily_usd", "All carriers per day (USD)", /all carriers past this waits/],
  ["controls.carrier_daily_count", "Payouts per carrier per day", /Once a carrier has had this many payouts today/],
  ["controls.agent-limit", "Agent payments", /0 turns agent payments off/],
];

function controls(): PaymentControls {
  return {
    pause: { paused: false, reason: null, by: null, at: null },
    limits: { max_payout_usd: 5000, carrier_daily_usd: 10000, tenant_daily_usd: 50000, carrier_daily_count: 5 },
    limits_are_defaults: true,
    usage_today: { tenant_usd: "0", carriers: {} },
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

describe("ControlsPage help", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setHelpMode(true);
    mocks.fetchPaymentControls.mockReset();
    mocks.fetchPaymentControls.mockResolvedValue(controls());
  });
  afterEach(() => setHelpMode(false));

  it.each(ENTRIES)("%s explains itself", (id, title, phrase) => {
    expect(helpFor(id)?.title).toBe(title);
    expect(helpFor(id)?.body).toMatch(phrase);
  });

  it("never says that only a person pays, or that a model lowers risk", () => {
    for (const [id] of ENTRIES) {
      const entry = helpFor(id);
      expect(`${entry?.body} ${entry?.note ?? ""}`).not.toMatch(/only a person (releases|pays)|only after a person|model (can|may) lower/i);
    }
  });

  it("shows a question mark beside every item with help on", async () => {
    renderPage();
    for (const [, title] of ENTRIES) {
      expect(await screen.findByRole("button", { name: `What is ${title}?` })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "What is Sent for payout today in USD?" })).toBeInTheDocument();
  });

  it("shows no question mark with help off", async () => {
    setHelpMode(false);
    renderPage();
    await screen.findByRole("region", { name: "Agent payments" });
    expect(screen.queryByRole("button", { name: /^What is / })).not.toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchDefenseScenarios: vi.fn(),
  fetchDefenseEvaluation: vi.fn(),
}));

import { fetchDefenseEvaluation, fetchDefenseScenarios } from "@/lib/api";

import DefensePage from "./page";

// AG Grid measures its container with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DefensePage />
    </QueryClientProvider>,
  );
}

const RATE = { count: 24, of: 36, rate: 0.6667, low: 0.5, high: 0.7981 };
const NONE = { count: 0, of: 44, rate: 0, low: 0, high: 0.0802 };

const SCENARIOS = {
  scenarios: [
    {
      id: "payee-swap", title: "A different account is named for the payout", attack: "x", layer: "Payee registry and payment checks",
      live: true, outcome: "held for a person", findings: ["PAYEE_MISMATCH"], floor: 80, money_moved_usd: "0.00",
      proof: "tests/test_payment_checks.py", detail: "",
    },
    {
      id: "disputed-carrier", title: "A carrier with a dispute open on an earlier payment", attack: "y", layer: "Freeze on carrier and shipper",
      live: false, outcome: "held until a governance admin lifts the freeze", findings: [], floor: null, money_moved_usd: "0.00",
      proof: "tests/test_settler_settlement.py", detail: "",
    },
  ],
  stopped: 2,
  of: 2,
  note: "A replay on invented data.",
};

const EVALUATION = {
  synthetic: true,
  flag_floor: 40,
  note: "Invented payouts and carriers: the rates describe this corpus only.",
  summary: {
    cases: 80, fraud: 36, legit: 44,
    arms: {
      floor: { recall: RATE, false_positive_rate: NONE },
      identity: { recall: { ...RATE, count: 32, rate: 0.8889 }, false_positive_rate: { ...NONE, count: 6, rate: 0.1364 } },
      analyst: null,
    },
  },
};

describe("DefensePage", () => {
  beforeEach(() => {
    vi.mocked(fetchDefenseScenarios).mockReset();
    vi.mocked(fetchDefenseEvaluation).mockReset();
  });

  it("says how many attacks were stopped and that no money moved", async () => {
    vi.mocked(fetchDefenseScenarios).mockResolvedValue(SCENARIOS);
    vi.mocked(fetchDefenseEvaluation).mockResolvedValue(EVALUATION);
    renderPage();
    const summary = await screen.findByTestId("defense-summary");
    expect(summary.textContent).toMatch(/2 of 2 attacks stopped/);
    expect(summary.textContent).toMatch(/no money moved/);
  });

  it("names the layer, the proof and what money moved for each attack", async () => {
    vi.mocked(fetchDefenseScenarios).mockResolvedValue(SCENARIOS);
    vi.mocked(fetchDefenseEvaluation).mockResolvedValue(EVALUATION);
    renderPage();
    expect(await screen.findByText("A different account is named for the payout")).toBeInTheDocument();
    expect(screen.getByText("Payee registry and payment checks")).toBeInTheDocument();
    expect(screen.getByText("PAYEE_MISMATCH")).toBeInTheDocument();
    expect(screen.getByText("Replayed just now")).toBeInTheDocument();
    expect(screen.getByText("Proved by tests/test_settler_settlement.py")).toBeInTheDocument();
    expect(screen.getAllByText("0.00 USD")).toHaveLength(2);
  });

  it("shows what the checks catch with their intervals and the analyst as not measured", async () => {
    vi.mocked(fetchDefenseScenarios).mockResolvedValue(SCENARIOS);
    vi.mocked(fetchDefenseEvaluation).mockResolvedValue(EVALUATION);
    renderPage();
    expect(await screen.findByText(/24 of 36 \(66\.7%, 95% CI 50\.0-79\.8%\)/)).toBeInTheDocument();
    expect(screen.getByText(/32 of 36/)).toBeInTheDocument();
    expect(screen.getAllByText("Not measured")).toHaveLength(2);
    expect(screen.getByText(/36 fraudulent and 44 legitimate/)).toBeInTheDocument();
    expect(screen.getByText(/describe this corpus only/)).toBeInTheDocument();
  });

  it("shows an attack that was not stopped as a failure", async () => {
    vi.mocked(fetchDefenseScenarios).mockResolvedValue({
      ...SCENARIOS,
      scenarios: [{ ...SCENARIOS.scenarios[0], outcome: "NOT STOPPED" }],
      stopped: 0,
      of: 1,
    });
    vi.mocked(fetchDefenseEvaluation).mockResolvedValue(EVALUATION);
    renderPage();
    expect(await screen.findByText("NOT STOPPED")).toBeInTheDocument();
    expect((await screen.findByTestId("defense-summary")).textContent).toMatch(/0 of 1 attacks stopped/);
  });

  it("shows the error when the replay cannot be read", async () => {
    vi.mocked(fetchDefenseScenarios).mockRejectedValue(new Error("boom"));
    vi.mocked(fetchDefenseEvaluation).mockResolvedValue(EVALUATION);
    renderPage();
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
  });
});

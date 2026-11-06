import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PaymentRisk } from "@/lib/api";

import { PaymentRiskCard } from "./PaymentRiskCard";

const CREDIT = { tier: "C", baseline: "C", suggested: null, cap_usd: "2000", paid_count: 0, frozen: false };

function make(overrides: Partial<PaymentRisk> = {}): PaymentRisk {
  return {
    case_id: "CASE-1",
    payment_case: true,
    findings: [],
    analyst: { verdict: "normal", confidence: 0.8, reasons: [], suggested_tier: null },
    identity: { status: "found", reason: "1 result(s) name the carrier", urls: [], payee_site: "on_site" },
    credit: CREDIT,
    ...overrides,
  };
}

describe("PaymentRiskCard", () => {
  it("shows an elevated reading with its confidence and reasons", () => {
    render(
      <PaymentRiskCard
        risk={make({ analyst: { verdict: "elevated", confidence: 0.87, reasons: ["payout far above freight"], suggested_tier: "D" } })}
      />,
    );
    expect(screen.getByText("The analyst rates this payout as elevated risk (87% confident)")).toBeInTheDocument();
    expect(screen.getByText("payout far above freight")).toBeInTheDocument();
  });

  it.each([
    [null],
    [{ verdict: "unknown", confidence: 0, reasons: ["the analyst could not be reached"], suggested_tier: null }],
  ])("never shows a missing reading as a clear one", (analyst) => {
    render(<PaymentRiskCard risk={make({ analyst })} />);
    expect(screen.getByText("The analyst gave no reading")).toBeInTheDocument();
    expect(screen.queryByText(/raised no concern/)).not.toBeInTheDocument();
  });

  it("says a normal reading lowers nothing", () => {
    render(<PaymentRiskCard risk={make()} />);
    expect(screen.getByText("The analyst raised no concern. That lowers nothing the checks found.")).toBeInTheDocument();
  });

  it("tells the person what to do about a payee off the carrier's website", () => {
    render(
      <PaymentRiskCard
        risk={make({ identity: { status: "found", reason: "x", urls: [], payee_site: "off_site" } })}
      />,
    );
    expect(screen.getByText("The payee is not on the carrier's own website")).toBeInTheDocument();
    expect(screen.getByText("Before you release: call the carrier on a number taken from its website and confirm this payee.")).toBeInTheDocument();
  });

  it.each([
    ["not_found", "No web result names the carrier"],
    ["unverified", "The web check did not run, so nothing confirms the carrier"],
  ])("says what a look-up of %s means and asks for a check", (status, words) => {
    render(<PaymentRiskCard risk={make({ identity: { status, reason: "x", urls: [] } })} />);
    expect(screen.getByText(words)).toBeInTheDocument();
    expect(screen.getByText("Before you release: confirm that this carrier exists.")).toBeInTheDocument();
  });

  it("shows a look-up it does not know as not checked, never as found", () => {
    render(<PaymentRiskCard risk={make({ identity: null })} />);
    expect(screen.getByText("The carrier was not looked up")).toBeInTheDocument();
    expect(screen.queryByText("The carrier was found online")).not.toBeInTheDocument();
  });

  it("offers no step when nothing asks for one", () => {
    render(<PaymentRiskCard risk={make()} />);
    expect(screen.queryByText(/Before you release/)).not.toBeInTheDocument();
  });

  it("shows the credit standing, and that the analyst tightened it", () => {
    render(<PaymentRiskCard risk={make({ credit: { ...CREDIT, tier: "D", baseline: "B", suggested: "D", paid_count: 2 } })} />);
    expect(screen.getByText("Credit standing D, tightened from B by the analyst. Cap 2000 USD, 2 confirmed payouts.")).toBeInTheDocument();
  });

  it("says when the carrier is frozen", () => {
    render(<PaymentRiskCard risk={make({ credit: { ...CREDIT, frozen: true } })} />);
    expect(screen.getByText("Payments to this carrier are frozen")).toBeInTheDocument();
  });

  it("lists what the checks and the AI found", () => {
    const finding = { code: "PAYEE_OFF_CARRIER_SITE", severity: "MEDIUM", floor: 55, detail: "The payee is not on the site." };
    render(<PaymentRiskCard risk={make({ findings: [finding] })} />);
    expect(screen.getByText("The payee is not on the site.")).toBeInTheDocument();
  });
});

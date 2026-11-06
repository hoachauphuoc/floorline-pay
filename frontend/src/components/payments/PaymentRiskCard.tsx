import type { PaymentRisk } from "@/lib/api";
import { cn } from "@/lib/utils";

const LOOK_UP: Record<string, string> = {
  found: "The carrier was found online",
  not_found: "No web result names the carrier",
  unverified: "The web check did not run, so nothing confirms the carrier",
};

const PAYEE_SITE: Record<string, string> = {
  on_site: "The payee is on the carrier's own website",
  off_site: "The payee is not on the carrier's own website",
};

function reading(analyst: PaymentRisk["analyst"]): { text: string; tone: string } {
  if (analyst?.verdict === "elevated") {
    const confident = Math.round((analyst.confidence ?? 0) * 100);
    return { text: `The analyst rates this payout as elevated risk (${confident}% confident)`, tone: "text-risk-warn" };
  }
  if (analyst?.verdict === "normal") {
    return { text: "The analyst raised no concern. That lowers nothing the checks found.", tone: "text-dim" };
  }
  return { text: "The analyst gave no reading", tone: "text-dim" };
}

function nextStep(identity: PaymentRisk["identity"]): string | null {
  if (identity?.payee_site === "off_site") {
    return "Before you release: call the carrier on a number taken from its website and confirm this payee.";
  }
  if (identity?.status === "not_found" || identity?.status === "unverified") {
    return "Before you release: confirm that this carrier exists.";
  }
  return null;
}

function standing(credit: NonNullable<PaymentRisk["credit"]>): string {
  const tightened = credit.tier !== credit.baseline ? `, tightened from ${credit.baseline} by the analyst` : "";
  const cap = credit.cap_usd ? `Cap ${credit.cap_usd} USD` : "No cap set";
  return `Credit standing ${credit.tier}${tightened}. ${cap}, ${credit.paid_count} confirmed payouts.`;
}

/**
 * What the checks and the AI layers found about this payout, for the person who decides. It reads what the analysis stored and calls
 * no model. The AI can only add to this card: a reading that is missing or unknown says so and is never shown as clear.
 */
export function PaymentRiskCard({ risk }: { risk: PaymentRisk }) {
  const analyst = reading(risk.analyst);
  const reasons = risk.analyst?.verdict === "elevated" ? (risk.analyst.reasons ?? []) : [];
  const site = PAYEE_SITE[risk.identity?.payee_site ?? ""];
  const step = nextStep(risk.identity);
  return (
    <section className="bento-card p-4" aria-label="What the AI found">
      <h2 className="text-[12px] font-medium text-white">What the AI found</h2>
      <p className={cn("mt-1 text-[13px] font-medium", analyst.tone)}>{analyst.text}</p>
      {reasons.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[12px] text-dim">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[12px] text-dim">{LOOK_UP[risk.identity?.status ?? ""] ?? "The carrier was not looked up"}</p>
      {site && <p className="mt-1 text-[12px] text-dim">{site}</p>}
      {risk.credit && <p className="mt-1 text-[12px] text-dim">{standing(risk.credit)}</p>}
      {risk.credit?.frozen && <p className="mt-1 text-[12px] text-risk-critical">Payments to this carrier are frozen</p>}
      {risk.findings && risk.findings.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11.5px] text-risk-warn">
          {risk.findings.map((finding) => (
            <li key={finding.code}>{finding.detail}</li>
          ))}
        </ul>
      )}
      {step && <p className="mt-2 text-[12px] font-medium text-white">{step}</p>}
    </section>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColDef } from "ag-grid-community";

import { AgTable } from "@/components/grid/AgTable";
import { HelpDot } from "@/components/help/HelpDot";
import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchDefenseEvaluation,
  fetchDefenseScenarios,
  type DefenseEvaluation,
  type DefenseScenario,
} from "@/lib/api";
import { useTenant } from "@/lib/tenant-context";

const NOT_STOPPED = "NOT STOPPED";

const SCENARIO_COLUMNS: ColDef<DefenseScenario>[] = [
  { headerName: "Attack", field: "title", flex: 2 },
  { headerName: "Layer that stops it", field: "layer", flex: 2 },
  {
    headerName: "Result",
    field: "outcome",
    flex: 2,
    cellClass: (params) => (params.value === NOT_STOPPED ? "text-risk-critical" : "text-risk-clear"),
  },
  {
    headerName: "Proof",
    flex: 2,
    valueGetter: (params) =>
      params.data?.findings.length ? params.data.findings.join(", ") : "",
  },
  { headerName: "Floor", field: "floor", flex: 1, valueFormatter: (params) => (params.value == null ? "" : String(params.value)) },
  {
    headerName: "Money moved",
    flex: 1,
    valueGetter: (params) => `${params.data?.money_moved_usd ?? "0.00"} USD`,
  },
  {
    headerName: "How we know",
    flex: 2,
    valueGetter: (params) =>
      params.data?.live ? "Replayed just now" : `Proved by ${params.data?.proof ?? "a test"}`,
  },
];

type ArmRow = { arm: string; catches: string; falseFlags: string };

function rate(rate: { count: number; of: number; rate: number | null; low: number | null; high: number | null }): string {
  if (rate.rate === null || rate.low === null || rate.high === null) return "n/a";
  return `${rate.count} of ${rate.of} (${(rate.rate * 100).toFixed(1)}%, 95% CI ${(rate.low * 100).toFixed(1)}-${(rate.high * 100).toFixed(1)}%)`;
}

function armRows(evaluation: DefenseEvaluation): ArmRow[] {
  const labels: Array<[keyof DefenseEvaluation["summary"]["arms"], string]> = [
    ["floor", "Deterministic checks and the request screen"],
    ["identity", "+ carrier look-up"],
    ["analyst", "+ payment-risk analyst (model)"],
  ];
  if (evaluation.summary.arms.site) labels.push(["site", "+ payee against the carrier's website"]);
  return labels.map(([key, arm]) => {
    const data = evaluation.summary.arms[key];
    return data
      ? { arm, catches: rate(data.recall), falseFlags: rate(data.false_positive_rate) }
      : { arm, catches: "Not measured", falseFlags: "Not measured" };
  });
}

const ARM_COLUMNS: ColDef<ArmRow>[] = [
  { headerName: "Arm", field: "arm", flex: 3 },
  { headerName: "Catches (of the fraudulent payouts)", field: "catches", flex: 3 },
  { headerName: "False flags (of the legitimate payouts)", field: "falseFlags", flex: 3 },
];

/**
 * Defense: ten ways to be paid wrongly, replayed against the real payment layers, and what the
 * layers catch on a synthetic corpus.
 *
 * Read-only. The replay reads and writes nothing and calls neither PayPal nor a model, so every row
 * says no money moved because none could.
 */
export default function DefensePage() {
  const { tenant } = useTenant();
  const scenarios = useQuery({
    queryKey: ["defense-scenarios", tenant.id],
    queryFn: fetchDefenseScenarios,
    retry: false,
  });
  const evaluation = useQuery({
    queryKey: ["defense-evaluation", tenant.id],
    queryFn: fetchDefenseEvaluation,
    retry: false,
  });

  const heading = (
    <PageHeading title="Defense">
      Ten ways to be paid wrongly, replayed against the same checks the pipeline and the settler
      run. Each row names the layer that stops the attack and the finding that proves it.
    </PageHeading>
  );

  if (scenarios.isError) {
    return (
      <>
        {heading}
        <ErrorState error={scenarios.error} />
      </>
    );
  }
  if (scenarios.isLoading || !scenarios.data) {
    return (
      <>
        {heading}
        <Skeleton className="h-64 rounded-xl bg-white/[0.04]" />
      </>
    );
  }

  const { scenarios: rows, stopped, of, note } = scenarios.data;

  return (
    <>
      {heading}
      <p className="mb-3 text-[13px] text-white/90" data-testid="defense-summary">
        <span className="font-mono tabular-nums text-risk-clear">{stopped} of {of}</span> attacks stopped,
        and no money moved in any of them.
        <HelpDot id="defense.replay" className="ml-1 align-middle" />
      </p>
      <AgTable label="Attack replay" rows={rows} columns={SCENARIO_COLUMNS} rowId={(row) => row.id} />
      <p className="mt-2 text-[11.5px] text-faint">{note}</p>

      <h2 className="mb-2 mt-8 text-[13px] font-medium text-white">What the checks catch <HelpDot id="defense.catches" className="ml-1 align-middle" /></h2>
      {evaluation.isError ? (
        <ErrorState error={evaluation.error} />
      ) : evaluation.isLoading || !evaluation.data ? (
        <Skeleton className="h-32 rounded-xl bg-white/[0.04]" />
      ) : (
        <>
          <AgTable
            label="What the checks catch"
            rows={armRows(evaluation.data)}
            columns={ARM_COLUMNS}
            rowId={(row) => row.arm}
          />
          <p className="mt-2 text-[11.5px] text-faint">
            {evaluation.data.summary.fraud} fraudulent and {evaluation.data.summary.legit} legitimate
            payouts. {evaluation.data.note}
          </p>
        </>
      )}
    </>
  );
}

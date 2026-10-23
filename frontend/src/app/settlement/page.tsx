"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColDef } from "ag-grid-community";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { AgTable } from "@/components/grid/AgTable";
import { Gated } from "@/components/layout/Gated";
import { PageHeading } from "@/components/layout/PageHeading";
import { EmptyState, ErrorState } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchFrozenParties,
  fetchPaymentLedger,
  liftFreeze,
  type FrozenParty,
  type PaymentLedger,
} from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { lockReason, passwordLockReason, useIdentity } from "@/lib/identity";
import { useTenant } from "@/lib/tenant-context";

type PayoutRow = PaymentLedger["items"][number];

const PAYOUT_LABEL: Record<string, string> = {
  confirmed: "Confirmed by PayPal",
  failed: "Failed, returned or blocked",
  pending: "Waiting for PayPal",
  unconfirmed: "Not confirmed yet",
};

function freezeColumns(select: (party: FrozenParty) => void): ColDef<FrozenParty>[] {
  return [
    { headerName: "Party", field: "name", flex: 2 },
    { headerName: "Role", field: "kind", flex: 1 },
    { headerName: "Why it is frozen", field: "reason", flex: 4 },
    { headerName: "Since", flex: 1, valueGetter: (params) => (params.data?.at ? formatRelative(params.data.at) : "") },
    {
      headerName: "",
      flex: 1,
      sortable: false,
      cellRenderer: (params: { data?: FrozenParty }) => (
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[11.5px]"
          aria-label={`Select ${params.data?.name ?? "party"}`}
          onClick={() => params.data && select(params.data)}
        >
          Select
        </Button>
      ),
    },
  ];
}

const PAYOUT_COLUMNS: ColDef<PayoutRow>[] = [
  { headerName: "Case", field: "case_id", flex: 2 },
  { headerName: "Payment", field: "state", flex: 1 },
  {
    headerName: "Payout, as PayPal reports it",
    flex: 2,
    valueGetter: (params) => {
      const status = params.data?.payout_status;
      if (!status) return "";
      return `${PAYOUT_LABEL[status.state] ?? status.state}${status.status ? ` (${status.status})` : ""}`;
    },
  },
  { headerName: "Why", field: "reason", flex: 3 },
];

/**
 * Settlement: what happened to the money after it moved.
 *
 * A payout PayPal accepted is not yet a payout the carrier has: the right column shows what PayPal has
 * said since. A carrier or shipper in dispute, with a reversed capture or a failed payout, is frozen and
 * nothing is paid to it until a governance admin, signed in with a password, lifts the freeze and says why.
 */
export default function SettlementPage() {
  const { tenant } = useTenant();
  const identity = useIdentity().data;
  const queryClient = useQueryClient();
  const frozen = useQuery({
    queryKey: ["frozen-parties", tenant.id],
    queryFn: fetchFrozenParties,
    refetchInterval: 15_000,
    retry: false,
  });
  const ledger = useQuery({
    queryKey: ["payment-ledger", tenant.id],
    queryFn: fetchPaymentLedger,
    refetchInterval: 15_000,
    retry: false,
  });
  const [selected, setSelected] = useState<FrozenParty | null>(null);
  const [reason, setReason] = useState("");
  const lift = useMutation({
    mutationFn: () => liftFreeze({ kind: selected?.kind ?? "", name: selected?.name ?? "", reason: reason.trim() }),
    onSuccess: () => {
      setSelected(null);
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["frozen-parties", tenant.id] });
    },
  });
  const lock = lockReason(identity, "governance_admin") ?? passwordLockReason(identity);

  const heading = (
    <PageHeading title="Settlement">
      What happened to the money after it moved. A payout PayPal accepted is confirmed, failed or still
      pending; a carrier or shipper in dispute is frozen until a governance admin lifts the freeze.
    </PageHeading>
  );

  if (frozen.isError || ledger.isError) {
    return (
      <>
        {heading}
        <ErrorState error={frozen.error ?? ledger.error} />
      </>
    );
  }
  if (frozen.isLoading || ledger.isLoading || !frozen.data || !ledger.data) {
    return (
      <>
        {heading}
        <Skeleton className="h-64 rounded-xl bg-white/[0.04]" />
      </>
    );
  }

  return (
    <>
      {heading}

      <h2 className="mb-2 text-[13px] font-medium text-white">Frozen parties</h2>
      {frozen.data.frozen.length === 0 ? (
        <EmptyState title="Nobody is frozen">
          A dispute, a reversed capture or a failed payout freezes the carrier and the shipper of its case here.
        </EmptyState>
      ) : (
        <>
          <AgTable
            label="Frozen parties"
            rows={frozen.data.frozen}
            columns={freezeColumns(setSelected)}
            rowId={(row) => `${row.kind}-${row.party}`}
          />
          <section className="bento-card mt-3 max-w-3xl p-4" aria-label="Lift a freeze">
            <h3 className="text-[12px] font-medium text-white">Lift a freeze</h3>
            {selected ? (
              <>
                <p className="mt-1 text-[12px] text-dim">
                  {selected.kind} <span className="text-white/90">{selected.name}</span>: {selected.reason}
                </p>
                <Label htmlFor="lift-reason" className="mt-2 block text-[11px] text-dim">
                  Why it is safe to pay this party again
                </Label>
                <Input
                  id="lift-reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
                />
                <Gated reason={lock}>
                  <Button
                    size="sm"
                    disabled={lift.isPending || lock !== null || reason.trim() === ""}
                    onClick={() => lift.mutate()}
                    className="mt-2 h-8 text-[12px]"
                  >
                    {lift.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                    Lift freeze
                  </Button>
                </Gated>
              </>
            ) : (
              <p className="mt-1 text-[12px] text-faint">Choose Select on a frozen party in the table above.</p>
            )}
            {lift.isError && (
              <div className="mt-2">
                <ErrorState error={lift.error} />
              </div>
            )}
          </section>
        </>
      )}

      <h2 className="mb-2 mt-8 text-[13px] font-medium text-white">Payouts</h2>
      {ledger.data.items.length === 0 ? (
        <EmptyState title="No payments yet">
          A payment appears here once a person has released or blocked a booked shipment.
        </EmptyState>
      ) : (
        <AgTable label="Payouts" rows={ledger.data.items} columns={PAYOUT_COLUMNS} rowId={(row) => row.case_id} />
      )}
    </>
  );
}

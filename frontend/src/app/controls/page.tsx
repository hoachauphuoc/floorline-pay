"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Gated } from "@/components/layout/Gated";
import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchPaymentControls,
  pausePayments,
  publishPaymentLimits,
  resumePayments,
  type PaymentLimits,
} from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { lockReason, passwordLockReason, useIdentity } from "@/lib/identity";
import { useTenant } from "@/lib/tenant-context";

const LIMIT_FIELDS: Array<[keyof PaymentLimits, string]> = [
  ["max_payout_usd", "Largest single payout (USD)"],
  ["carrier_daily_usd", "Per carrier per day (USD)"],
  ["tenant_daily_usd", "All carriers per day (USD)"],
  ["carrier_daily_count", "Payouts per carrier per day"],
];

/**
 * Payment controls: whether payments are running, and how much may leave in a day.
 *
 * Neither is up to a model or a shipment event. An operator may pause payments; resuming
 * them and changing the limits needs a governance admin, and all three need a password
 * sign-in. A payout over the largest single amount is held for a person; one that would
 * pass a daily limit waits and goes out when the day turns over.
 */
export default function ControlsPage() {
  const { tenant } = useTenant();
  const identity = useIdentity().data;
  const queryClient = useQueryClient();
  const controls = useQuery({
    queryKey: ["payment-controls", tenant.id],
    queryFn: fetchPaymentControls,
    refetchInterval: 15_000,
    retry: false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["payment-controls", tenant.id] });

  const pauseLock = lockReason(identity, "operator") ?? passwordLockReason(identity);
  const adminLock = lockReason(identity, "governance_admin") ?? passwordLockReason(identity);

  const heading = (
    <PageHeading title="Payment controls">
      Whether payments are running and how much may leave in a day. A pause or a limit is
      set by a person, never by a model or a shipment: any operator can stop payments, and
      only a governance admin can start them again or change the limits.
    </PageHeading>
  );

  if (controls.isError) {
    return (
      <>
        {heading}
        <ErrorState error={controls.error} />
      </>
    );
  }
  if (controls.isLoading || !controls.data) {
    return (
      <>
        {heading}
        <Skeleton className="h-64 rounded-xl bg-white/[0.04]" />
      </>
    );
  }

  const { pause, limits, limits_are_defaults: defaults, usage_today: usage } = controls.data;
  const carriers = Object.entries(usage.carriers);

  return (
    <div className="max-w-3xl space-y-4">
      {heading}

      <section className="bento-card p-4" aria-label="Pause switch">
        {pause.paused ? (
          <>
            <p className="text-[13px] font-medium text-risk-critical">Payments are paused</p>
            <p className="mt-1 text-[12px] text-dim">
              {pause.reason} · by {pause.by} {formatRelative(pause.at)}. Releases wait and are
              retried when payments resume. A block still returns the shipper&apos;s money.
            </p>
            <ReasonAction
              label="Why payments can resume"
              button="Resume payments"
              lock={adminLock}
              run={(reason) => resumePayments(reason).then(refresh)}
            />
          </>
        ) : (
          <>
            <p className="text-[13px] font-medium text-risk-clear">Payments are running</p>
            <ReasonAction
              label="Why payments should stop"
              button="Pause payments"
              lock={pauseLock}
              run={(reason) => pausePayments(reason).then(refresh)}
            />
          </>
        )}
      </section>

      <section className="bento-card p-4" aria-label="Limits">
        <h2 className="text-[12px] font-medium text-white">Limits in force</h2>
        {limits === null ? (
          <p className="mt-1 text-[12px] text-risk-critical">
            The published limits cannot be read, so nothing is paid until a governance admin
            publishes a readable set.
          </p>
        ) : (
          <>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px]">
              {LIMIT_FIELDS.map(([key, label]) => (
                <div key={key} className="contents">
                  <dt className="text-dim">{label}</dt>
                  <dd className="font-mono tabular-nums text-white/90">{limits[key]}</dd>
                </div>
              ))}
            </dl>
            {defaults && (
              <p className="mt-2 text-[11.5px] text-faint">
                These are the defaults: nobody has published limits yet.
              </p>
            )}
          </>
        )}
        <PublishLimits lock={adminLock} start={limits} onDone={refresh} />
      </section>

      <section className="bento-card p-4" aria-label="Paid today">
        <h2 className="text-[12px] font-medium text-white">Paid today (UTC)</h2>
        <p className="mt-1 text-[12px] text-dim">
          All carriers <span className="font-mono text-white/90">{usage.tenant_usd}</span> USD
        </p>
        {carriers.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-[12px]" aria-label="Paid today by carrier">
            {carriers.map(([key, used]) => (
              <li key={key} className="text-dim">
                <span className="font-mono text-white/85">{key || "(no carrier)"}</span> {used.usd} USD in{" "}
                {used.count} {used.count === 1 ? "payout" : "payouts"}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ReasonAction({
  label,
  button,
  lock,
  run,
}: {
  label: string;
  button: string;
  lock: string | null;
  run: (reason: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const action = useMutation({
    mutationFn: () => run(reason.trim()),
    onSuccess: () => setReason(""),
  });
  const id = `reason-${button.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="mt-3">
      <Label htmlFor={id} className="text-[11px] text-dim">
        {label}
      </Label>
      <Input
        id={id}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
      />
      <Gated reason={lock}>
        <Button
          size="sm"
          disabled={action.isPending || lock !== null || reason.trim() === ""}
          onClick={() => action.mutate()}
          className="mt-2 h-8 text-[12px]"
        >
          {action.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          {button}
        </Button>
      </Gated>
      {action.isError && (
        <div className="mt-2">
          <ErrorState error={action.error} />
        </div>
      )}
    </div>
  );
}

function PublishLimits({
  lock,
  start,
  onDone,
}: {
  lock: string | null;
  start: PaymentLimits | null;
  onDone: () => void;
}) {
  const [values, setValues] = useState<Record<keyof PaymentLimits, string>>({
    max_payout_usd: start ? String(start.max_payout_usd) : "",
    carrier_daily_usd: start ? String(start.carrier_daily_usd) : "",
    tenant_daily_usd: start ? String(start.tenant_daily_usd) : "",
    carrier_daily_count: start ? String(start.carrier_daily_count) : "",
  });
  const [reason, setReason] = useState("");
  const publish = useMutation({
    mutationFn: () =>
      publishPaymentLimits(
        {
          max_payout_usd: Number(values.max_payout_usd),
          carrier_daily_usd: Number(values.carrier_daily_usd),
          tenant_daily_usd: Number(values.tenant_daily_usd),
          carrier_daily_count: Number(values.carrier_daily_count),
        },
        reason.trim(),
      ),
    onSuccess: () => {
      setReason("");
      onDone();
    },
  });
  const ready = reason.trim() !== "" && Object.values(values).every((v) => v.trim() !== "");

  return (
    <div className="mt-4 border-t border-white/[0.06] pt-3">
      <h3 className="text-[11.5px] font-medium text-white">Publish new limits</h3>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {LIMIT_FIELDS.map(([key, label]) => (
          <div key={key}>
            <Label htmlFor={`limit-${key}`} className="text-[11px] text-dim">
              {`New: ${label}`}
            </Label>
            <Input
              id={`limit-${key}`}
              value={values[key]}
              onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
            />
          </div>
        ))}
      </div>
      <Label htmlFor="limits-reason" className="mt-2 block text-[11px] text-dim">
        Why the limits change
      </Label>
      <Input
        id="limits-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
      />
      <Gated reason={lock}>
        <Button
          size="sm"
          disabled={publish.isPending || lock !== null || !ready}
          onClick={() => publish.mutate()}
          className="mt-2 h-8 text-[12px]"
        >
          {publish.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          Publish limits
        </Button>
      </Gated>
      {publish.isError && (
        <div className="mt-2">
          <ErrorState error={publish.error} />
        </div>
      )}
    </div>
  );
}

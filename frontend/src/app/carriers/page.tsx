"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Gated } from "@/components/layout/Gated";
import { PageHeading } from "@/components/layout/PageHeading";
import { EmptyState, ErrorState } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchCarriers,
  registerCarrierPayee,
  verifyCarrierPayee,
  type CarrierPayee,
  type CarrierRow,
} from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { lockReason, passwordLockReason, useIdentity } from "@/lib/identity";
import { useTenant } from "@/lib/tenant-context";

/**
 * Carriers: who each carrier may be paid, as the server's registry holds it.
 *
 * A payee is registered by one person and verified by a different one, who calls the
 * carrier back and writes down what they were told. Until then it is pending and the
 * carrier's old payee, if any, stays in force. A payee changed through here is held for a
 * cooling-off period before payouts to it are released.
 */
export default function CarriersPage() {
  const { tenant } = useTenant();
  const identity = useIdentity().data;
  const queryClient = useQueryClient();
  const carriers = useQuery({
    queryKey: ["carriers", tenant.id],
    queryFn: fetchCarriers,
    refetchInterval: 20_000,
    retry: false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["carriers", tenant.id] });

  const registerLock = lockReason(identity, "operator") ?? passwordLockReason(identity);
  const verifyLock = lockReason(identity, "reviewer") ?? passwordLockReason(identity);

  const heading = (
    <PageHeading title="Carriers">
      Who each carrier may be paid. A payee is registered by one person and verified by a
      different one after a callback to the carrier; until then it is pending and the old
      payee, if any, stays in force.
    </PageHeading>
  );

  if (carriers.isError) {
    return (
      <>
        {heading}
        <ErrorState error={carriers.error} />
      </>
    );
  }

  if (carriers.isLoading || !carriers.data) {
    return (
      <>
        {heading}
        <Skeleton className="h-64 rounded-xl bg-white/[0.04]" />
      </>
    );
  }

  const items = carriers.data.items;

  return (
    <>
      {heading}
      <RegisterPayee onDone={refresh} lock={registerLock} />

      {items.length === 0 ? (
        <EmptyState title="No carriers yet">
          Register a payee above. Nothing can be paid to a carrier until its payee is verified.
        </EmptyState>
      ) : (
        <div className="mt-4 space-y-3">
          {items.map((carrier) => (
            <CarrierCard
              key={carrier.carrier_key}
              carrier={carrier}
              email={identity?.email}
              lock={verifyLock}
              onDone={refresh}
            />
          ))}
        </div>
      )}
    </>
  );
}

function RegisterPayee({ onDone, lock }: { onDone: () => void; lock: string | null }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [requestText, setRequestText] = useState("");
  const register = useMutation({
    mutationFn: () =>
      registerCarrierPayee({
        carrier_name: name.trim(),
        payee_email: email.trim(),
        ...(requestText.trim() ? { request_text: requestText.trim() } : {}),
      }),
    onSuccess: () => {
      setName("");
      setEmail("");
      setRequestText("");
      onDone();
    },
  });
  const ready = name.trim() !== "" && email.trim() !== "";

  return (
    <section className="bento-card max-w-3xl p-4" aria-label="Register a payee">
      <h2 className="text-[12px] font-medium text-white">Register a payee</h2>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div>
          <Label htmlFor="carrier-name" className="text-[11px] text-dim">
            Carrier
          </Label>
          <Input
            id="carrier-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Mekong Haulage Co"
            className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
          />
        </div>
        <div>
          <Label htmlFor="carrier-payee" className="text-[11px] text-dim">
            PayPal account to pay
          </Label>
          <Input
            id="carrier-payee"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="payments@mekong-haulage.example"
            className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
          />
        </div>
      </div>
      <div className="mt-2">
        <Label htmlFor="carrier-request" className="text-[11px] text-dim">
          Message that asked for the change (optional)
        </Label>
        <Textarea
          id="carrier-request"
          value={requestText}
          onChange={(e) => setRequestText(e.target.value)}
          rows={2}
          placeholder="Paste what the carrier wrote. Only the screen's reasons are kept, never the text."
          className="mt-1 border-white/10 bg-black/30 text-[12px]"
        />
      </div>
      <Gated reason={lock}>
        <Button
          size="sm"
          disabled={register.isPending || lock !== null || !ready}
          onClick={() => register.mutate()}
          className="mt-3 h-8 text-[12px]"
        >
          {register.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          Register payee
        </Button>
      </Gated>
      {register.isError && (
        <div className="mt-3">
          <ErrorState error={register.error} />
        </div>
      )}
    </section>
  );
}

function CarrierCard({
  carrier,
  email,
  lock,
  onDone,
}: {
  carrier: CarrierRow;
  email: string | undefined;
  lock: string | null;
  onDone: () => void;
}) {
  const live = carrier.current;
  return (
    <section
      className="bento-card p-4"
      aria-label={`Carrier ${carrier.carrier_name ?? carrier.carrier_key}`}
    >
      <h2 className="text-[13px] font-medium text-white">
        {carrier.carrier_name ?? carrier.carrier_key}
      </h2>
      {live ? (
        <p className="mt-1 text-[12px] text-dim">
          Verified payee <span className="font-mono text-white/90">{live.payee_email}</span>, verified
          by {live.verified_by} {formatRelative(live.verified_at)}
          {live.changed_at && (
            <span className="text-risk-warn">
              {" "}
              · changed {formatRelative(live.changed_at)}: payouts to it wait out the cooling-off
              period
            </span>
          )}
        </p>
      ) : (
        <p className="mt-1 text-[12px] text-risk-warn">
          No verified payee yet: nothing can be paid to this carrier.
        </p>
      )}
      {live?.change_request_flagged && (
        <p className="mt-1 text-[12px] text-risk-critical" role="alert">
          The request that put this payee on file was flagged: payouts to it are held until a payee is registered
          and verified again.
        </p>
      )}
      {carrier.pending.length > 0 && (
        <ul className="mt-3 space-y-3" aria-label="Pending registrations">
          {carrier.pending.map((pending) => (
            <PendingPayee
              key={pending.version}
              carrierKey={carrier.carrier_key}
              pending={pending}
              own={email !== undefined && pending.registered_by.toLowerCase() === email.toLowerCase()}
              lock={lock}
              onDone={onDone}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function PendingPayee({
  carrierKey,
  pending,
  own,
  lock,
  onDone,
}: {
  carrierKey: string;
  pending: CarrierPayee;
  own: boolean;
  lock: string | null;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const verify = useMutation({
    mutationFn: () => verifyCarrierPayee(carrierKey, pending.version, note.trim()),
    onSuccess: () => {
      setNote("");
      onDone();
    },
  });
  const id = `note-${carrierKey}-${pending.version}`;

  return (
    <li className="rounded-lg border border-white/[0.08] bg-black/25 p-3">
      <p className="text-[12px] text-white/90">
        Pending <span className="font-mono">{pending.payee_email}</span>
        <span className="text-dim">
          {" "}
          registered by {pending.registered_by} {formatRelative(pending.registered_at)}
        </span>
      </p>
      {pending.change_request_flagged && (
        <p className="mt-1 text-[11.5px] text-risk-critical" role="alert">
          The request was flagged: {(pending.change_request_reasons ?? []).join("; ") || "wording typical of payment-diversion fraud"}.
          Call the carrier on a number you already hold before verifying.
        </p>
      )}
      {own ? (
        <p className="mt-1 text-[11.5px] text-faint">
          A different person must verify this payee.
        </p>
      ) : (
        <>
          <Label htmlFor={id} className="mt-2 block text-[11px] text-dim">
            Callback note
          </Label>
          <Input
            id={id}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Who you called, on what number, and what they confirmed"
            className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
          />
          <Gated reason={lock}>
            <Button
              size="sm"
              disabled={verify.isPending || lock !== null || note.trim() === ""}
              onClick={() => verify.mutate()}
              className="mt-2 h-8 text-[12px]"
            >
              {verify.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
              Verify payee
            </Button>
          </Gated>
          {verify.isError && (
            <div className="mt-2">
              <ErrorState error={verify.error} />
            </div>
          )}
        </>
      )}
    </li>
  );
}

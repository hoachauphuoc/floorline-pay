"use client";

import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { Gated } from "@/components/layout/Gated";
import { ErrorState } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { publishAgentLimit, type AgentLimit } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/**
 * How much the agent may pay by itself. It is off until a governance admin publishes a limit, and zero turns it off again. The limit
 * is a ceiling and not a yes for any one payment: the agent pays only a clean case below it, and anything else waits for a person.
 */
export function AgentLimitCard({
  limit,
  lock,
  onDone,
}: {
  limit: AgentLimit | null;
  lock: string | null;
  onDone: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const publish = useMutation({
    mutationFn: () => publishAgentLimit(Number(amount), reason.trim()),
    onSuccess: () => {
      setAmount("");
      setReason("");
      onDone();
    },
  });
  const ready = amount.trim() !== "" && reason.trim() !== "";

  return (
    <section className="bento-card p-4" aria-label="Agent payments">
      <h2 className="text-[12px] font-medium text-white">Agent payments</h2>
      {limit ? (
        <p className="mt-1 text-[12px] text-dim">
          The agent pays clean cases below <span className="font-mono text-white/90">{limit.limit_usd}</span> USD. Published by{" "}
          {limit.published_by} {formatRelative(limit.at)}
          {limit.reason ? `: ${limit.reason}` : ""}.
        </p>
      ) : (
        <p className="mt-1 text-[12px] text-dim">Off. Every payment waits for a person.</p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div>
          <Label htmlFor="agent-limit" className="text-[11px] text-dim">
            Agent limit in USD (0 turns it off)
          </Label>
          <Input
            id="agent-limit"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
          />
        </div>
        <div>
          <Label htmlFor="agent-limit-reason" className="text-[11px] text-dim">
            Why the agent limit changes
          </Label>
          <Input
            id="agent-limit-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
          />
        </div>
      </div>
      <Gated reason={lock}>
        <Button
          size="sm"
          disabled={publish.isPending || lock !== null || !ready}
          onClick={() => publish.mutate()}
          className="mt-2 h-8 text-[12px]"
        >
          {publish.isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          Publish agent limit
        </Button>
      </Gated>
      {publish.isError && (
        <div className="mt-2">
          <ErrorState error={publish.error} />
        </div>
      )}
    </section>
  );
}

"use client";

import { Cpu, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { AccountMenu } from "@/components/layout/AccountMenu";
import { HelpModeToggle } from "@/components/layout/HelpModeToggle";
import { PayPalLogo } from "@/components/ui/PayPalLogo";
import { TenantSwitcher } from "@/components/layout/TenantSwitcher";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { DEMO_MODE } from "@/lib/config";
import type { Tenant } from "@/lib/types";

export function Header({ display }: { display?: Tenant }) {
  return (
    // Sticky from lg up only. Below that the shell's own mobile bar is the sticky
    // one, and two bars both pinned at top-0 slid this one underneath it.
    <header className="relative z-30 border-b border-white/[0.08] bg-slate-950/70 backdrop-blur-xl lg:sticky lg:top-0">
      {/* Full width rather than a centred max-width container: the shell's
          sidebar already sets the content's left edge, and centring inside that
          put the header controls visibly out of line with the page below. */}
      <div className="flex h-14 w-full items-center gap-3 px-4 sm:px-6 lg:px-8">
        {/* Tenant first, top-left: it scopes everything below it, so it reads
            before the product name rather than after. */}
        <TenantSwitcher display={display} />

        {/* From xl only. Between sm and xl the header also carries the tenant, the
            help and live pills, the signed-in address and the role chip, and the
            product name wrapped onto three lines and pushed the role chip off the
            edge. The sidebar and the mobile bar already name the product. */}
        <Separator
          orientation="vertical"
          className="mx-1 hidden !h-7 bg-white/10 xl:block"
        />

        <div className="hidden shrink-0 items-center gap-2 xl:flex">
          <ShieldCheck className="size-4 text-brand" aria-hidden />
          <span className="whitespace-nowrap text-[13px] font-medium tracking-display text-white/90">
            Floorline
          </span>
        </div>

        <div className="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
          <PayPalBadge />
          <ModelsChip />
          <HelpModeToggle />
          <div className="hidden sm:block">{DEMO_MODE ? <DemoPill /> : <LivePill />}</div>
          <AccountMenu />
        </div>
      </div>
    </header>
  );
}

/**
 * Says so, plainly, when the numbers are fixtures.
 *
 * A compliance console that cannot be told apart from the real thing is a
 * liability: someone screenshots it, and a synthetic sanctions hit against a
 * named company becomes evidence of something that never happened.
 */
function DemoPill() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="badge-risk badge-warn cursor-default">
          <span className="size-1.5 rounded-full bg-current" aria-hidden />
          Demo data
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[20rem]">
        Audits below are synthetic fixtures. Finding codes and model ids are the
        real ones, so the shapes match production, but no shipment, company or
        citation here is real.
      </TooltipContent>
    </Tooltip>
  );
}

function LivePill() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="badge-risk badge-clear cursor-default">
          <span className="size-1.5 rounded-full bg-current" aria-hidden />
          Live
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[20rem]">
        Reading the audit API for the tenant your session is authenticated
        against.
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * What every case on screen is reasoned by, on every screen.
 *
 * The models were named only on the Agent Console, so a visitor -- or a video
 * frame -- on any other screen could not tell what the agents run on. Links to
 * that console, where each agent's model and spend are shown.
 */
function ModelsChip() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/agents"
          className="hidden h-7 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 text-[11px] text-dim transition-colors hover:border-white/25 hover:text-white focus-visible:outline-2 focus-visible:outline-brand lg:inline-flex"
        >
          <Cpu className="size-3.5 text-brand" aria-hidden />
          <span className="whitespace-nowrap">
            NVIDIA Nemotron 3 <span className="text-faint">·</span> Nebius Token Factory
          </span>
        </Link>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[22rem]">
        Every agent runs on Nebius Token Factory. Nemotron 3 Nano screens fraud,
        compliance and HS codes; Super investigates and argues the disputed
        cases. Documents are read by MiniCPM-V, also on Token Factory.
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Who the payments run on, on every screen. The logo sits on a white pill because PayPal's rules
 * allow the mark only unaltered on a clear background, and the header is dark.
 */
function PayPalBadge() {
  return (
    <span className="hidden h-7 shrink-0 items-center gap-2 text-[11px] text-dim sm:inline-flex">
      <span className="whitespace-nowrap">Payments by</span>
      <span className="inline-flex h-6 items-center rounded-md bg-white px-2">
        <PayPalLogo height={14} />
      </span>
    </span>
  );
}

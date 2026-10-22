"use client";

import {
  Activity,
  CalendarCheck,
  ChevronLeft,
  FileClock,
  FlaskConical,
  Gauge,
  Inbox,
  KeyRound,
  LayoutGrid,
  Radar,
  ScrollText,
  Receipt,
  ShieldCheck,
  Terminal,
  Wallet,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { HelpDot } from "@/components/help/HelpDot";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSidebarCollapsed } from "@/lib/sidebar-state";
import { cn } from "@/lib/utils";

export type NavSection = "work" | "oversight";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** One line, shown collapsed as the tooltip body and expanded as a subtitle. */
  blurb: string;
  /** Which group of the sidebar it sits in (NAV_SECTIONS). */
  section: NavSection;
}

/** The sidebar's two groups, in order, with the heading each is shown under. */
export const NAV_SECTIONS: ReadonlyArray<{ id: NavSection; label: string }> = [
  { id: "work", label: "Your work" },
  { id: "oversight", label: "Verification & oversight" },
];

/**
 * Every section of the console, grouped by who it is for.
 *
 * "Your work" comes first and is the operator's day: what to handle today, the
 * decision form, and whether the work got done. Everything else -- the live
 * board, the audit trail, governance, the models, the benchmark, access control
 * -- is how that work is checked, so it sits below under its own heading. The
 * screens did not change; what changed is which ones a person doing the job is
 * pointed at first.
 *
 * Within the oversight group the old order survives, with deliberate differences:
 *
 * - "Dashboard" is called Pipeline, because that is what it shows. A section
 *   named Dashboard in a console that is entirely a dashboard tells a reader
 *   nothing about which screen they are on.
 * - Risk Radar is new rather than ported: it reads the published B2B audit
 *   contract (`/compliance/reports`) rather than the internal case store, so it
 *   is the view an integrator's data actually appears in. It sits second because
 *   it answers "what did we decide" while Pipeline answers "what is happening".
 * - Evaluation and Access Control come last: one says how well the system does
 *   its job, the other who may do what with it. Neither is operational.
 */
export const NAV_ITEMS: NavItem[] = [
  {
    href: "/today",
    label: "Today",
    icon: CalendarCheck,
    blurb: "Cases to handle, who owns them, what next",
    section: "work",
  },
  {
    href: "/review",
    label: "Review Queue",
    icon: Inbox,
    blurb: "Cases waiting on a person",
    section: "work",
  },
  {
    href: "/outcomes",
    label: "Outcomes",
    icon: Gauge,
    blurb: "Resolved, handling time, rounds, cost",
    section: "work",
  },
  {
    href: "/",
    label: "Pipeline",
    icon: LayoutGrid,
    blurb: "Live board, by workflow state",
    section: "oversight",
  },
  {
    href: "/book",
    label: "Book and pay",
    icon: Wallet,
    blurb: "Hold the freight with PayPal",
    section: "work",
  },
  {
    href: "/payments",
    label: "Payments",
    icon: Receipt,
    blurb: "Held, paid and returned funds",
    section: "work",
  },
  {
    href: "/radar",
    label: "Risk Radar",
    icon: Radar,
    blurb: "Completed audits, with provenance",
    section: "oversight",
  },
  {
    href: "/audit",
    label: "Audit Trail",
    icon: ScrollText,
    blurb: "Every action, including refusals",
    section: "oversight",
  },
  {
    href: "/governance",
    label: "Governance",
    icon: ShieldCheck,
    blurb: "Delegated authority and drift",
    section: "oversight",
  },
  {
    href: "/devops",
    label: "DevOps",
    icon: Terminal,
    blurb: "Inject, upload, bulk load",
    section: "oversight",
  },
  {
    href: "/agents",
    label: "Agent Console",
    icon: Activity,
    blurb: "Models, cost and injection probes",
    section: "oversight",
  },
  {
    href: "/evaluation",
    label: "Evaluation",
    icon: FlaskConical,
    blurb: "Measured detection, false alarms, cost",
    section: "oversight",
  },
  {
    href: "/admin",
    label: "Access Control",
    icon: KeyRound,
    blurb: "Your role, the policy, the proxy",
    section: "oversight",
  },
];

/**
 * Whether `pathname` is on the screen `href` names.
 *
 * Exact for "/" so every route does not light up Pipeline; a prefix match for the
 * rest so /review/CASE-1 keeps Review Queue selected. A case page, /cases/<id>, is
 * reached from Today and is shown as part of it.
 */
export function navItemActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/today" && pathname.startsWith("/cases/")) return true;
  return pathname.startsWith(href);
}

export function Sidebar({ reviewCount }: { reviewCount?: number | null }) {
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "sticky top-0 z-30 hidden h-svh shrink-0 flex-col border-r border-white/[0.08]",
        "bg-[#050914]/80 backdrop-blur-xl lg:flex",
        collapsed ? "w-[4.25rem]" : "w-[15.5rem]",
        "transition-[width] duration-200 ease-out",
      )}
    >
      <div className="flex h-14 items-center gap-2.5 px-4">
        <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-brand/15 ring-1 ring-brand/30">
          <FileClock className="size-4 text-brand" aria-hidden />
        </span>
        {!collapsed && (
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold tracking-display text-white">
              Floorline
            </span>
            <span className="block truncate text-[10.5px] leading-tight text-faint">
              Shipment-fraud agents
            </span>
          </span>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto scrollbar-thin px-2 pt-2">
        {NAV_SECTIONS.map((section, index) => (
          <div key={section.id} className={cn("space-y-0.5", index > 0 && "mt-4")}>
            {/* Collapsed there is no room for a heading, so a rule separates the
                groups instead -- the grouping still reads without the words. */}
            {collapsed ? (
              index > 0 && <div className="mx-2 mb-2 border-t border-white/[0.06]" aria-hidden />
            ) : (
              <p className="px-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-faint">
                {section.label}
              </p>
            )}
            {NAV_ITEMS.filter((item) => item.section === section.id).map((item) => (
              // The help dot is a SIBLING of the link, never a child. NavLink renders
              // an <a>, and a <button> inside an anchor is invalid nesting and gives
              // the row two competing click targets.
              //
              // Suppressed while collapsed: there is no room, and the collapsed
              // tooltip already carries the blurb.
              <div key={item.href} className="flex items-center gap-1">
                <div className="min-w-0 flex-1">
                  <NavLink
                    item={item}
                    collapsed={collapsed}
                    active={navItemActive(item.href, pathname)}
                    badge={item.href === "/review" ? reviewCount : undefined}
                  />
                </div>
                {!collapsed && <HelpDot id={`nav:${item.href}`} side="right" />}
              </div>
            ))}
          </div>
        ))}
      </nav>

      <div className="border-t border-white/[0.06] p-2">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className={cn(
            "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[12px]",
            "text-dim transition-colors hover:bg-white/[0.05] hover:text-white",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <ChevronLeft
            className={cn(
              "size-4 shrink-0 transition-transform duration-200",
              collapsed && "rotate-180",
            )}
            aria-hidden
          />
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
}

function NavLink({
  item,
  active,
  collapsed,
  badge,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  badge?: number | null;
}) {
  const Icon = item.icon;

  const body = (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
        active
          ? "bg-white/[0.07] text-white"
          : "text-dim hover:bg-white/[0.04] hover:text-white/90",
        collapsed && "justify-center px-0",
      )}
    >
      {/* The selected rail. Absolute so it does not shift the label, which
          would make the whole list jitter as the active item changes. */}
      {active && (
        <span
          className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-r bg-brand"
          aria-hidden
        />
      )}
      <Icon className="size-4 shrink-0" aria-hidden />
      {!collapsed && (
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
          {item.label}
        </span>
      )}
      {/* Rendered only for a positive count. A badge reading "0" is noise, and
          `badge != null` alone would show it -- the queue being empty is the
          normal state and does not need announcing. */}
      {badge != null && badge > 0 && (
        <span
          className={cn(
            "badge-risk badge-warn px-1.5 py-0 text-[10px] tabular-nums",
            collapsed &&
              "absolute -right-0.5 -top-0.5 min-w-[1.1rem] justify-center px-1",
          )}
        >
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </Link>
  );

  if (!collapsed) return body;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{body}</TooltipTrigger>
      <TooltipContent side="right" className="max-w-[16rem]">
        <span className="block font-medium text-white">{item.label}</span>
        <span className="block text-[11px] text-dim">{item.blurb}</span>
      </TooltipContent>
    </Tooltip>
  );
}

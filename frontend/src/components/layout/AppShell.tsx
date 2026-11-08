"use client";

import { useQuery } from "@tanstack/react-query";
import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Header } from "@/components/layout/Header";
import { NAV_ITEMS, NAV_SECTIONS, navItemActive, Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { fetchReviewQueue, queryKeys } from "@/lib/api";
import { DEMO_MODE } from "@/lib/config";
import { useIdentity } from "@/lib/identity";
import { displayTenant, useTenant } from "@/lib/tenant-context";
import { cn } from "@/lib/utils";

/**
 * The shell every screen renders inside: sidebar, header, and the review badge.
 *
 * A client component because the sidebar needs `usePathname` and the badge needs
 * a query. That is fine here -- nothing in this tree is static, and the pages
 * below it are all client components too, because every one of them polls.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { tenant } = useTenant();
  const [mobileOpen, setMobileOpen] = useState(false);

  // The badge count, and the only query that runs on every screen.
  //
  // Refetched on an interval rather than only on mount: a reviewer working
  // through the queue on another screen should see the count fall. 20s rather
  // than the 2.5s the board uses, because this is a number in a nav item and
  // not the thing being watched.
  //
  // `enabled: !DEMO_MODE` because there is no fixture for it. Without that the
  // query throws DemoModeUnavailable on every screen and React Query retries it,
  // which fills the console with errors for a badge nobody asked for.
  // `enabled` also excludes the sign-in screen, where the shell is not rendered
  // but this hook still runs -- hooks cannot be skipped by the early return below.
  // Without it the login page fires a request that 401s before anyone has signed
  // in, which reads as a broken server in the network log.
  const queue = useQuery({
    queryKey: queryKeys.reviewQueue(tenant.id),
    queryFn: () => fetchReviewQueue({ limit: 40 }),
    enabled: !DEMO_MODE && pathname !== "/login" && pathname !== "/legal" && !pathname.startsWith("/respond/"),
    refetchInterval: 20_000,
    // A failed count must not surface as a broken screen. Rendering no badge is
    // honest -- it says nothing rather than saying zero.
    retry: false,
  });

  // The tenant the header names comes from whoami, not billing.
  //
  // It used to be read off `/billing/usage`, which also returns tenant_id -- but
  // billing is operator-only, so once the console stopped lending its key to
  // anonymous reads, every visitor's header said "Resolving tenant" forever. whoami
  // is readable by every role and returns the tenant the API resolved for this
  // caller, which is the same server-derived fact without the permission. In demo
  // mode it resolves to null and the selected fixture is shown, as before. Not
  // asked on the sign-in and legal pages, which render without the header.
  const identity = useIdentity({
    enabled: pathname !== "/login" && pathname !== "/legal" && !pathname.startsWith("/respond/"),
  });

  const display = displayTenant(tenant, identity.data?.tenant_id, identity.isError);

  // Sign-in and the public legal page render bare. The shell's nav would be a list
  // of links that all bounce straight back to sign-in, and its two queries would
  // fire while signed out and fail, which is how a login screen ends up showing an
  // error banner.
  //
  // Placed after the hooks rather than before them: hooks cannot be called
  // conditionally, and an early return above them would break the render on
  // navigation between these paths and the rest of the console.
  if (pathname === "/login" || pathname === "/legal" || pathname.startsWith("/respond/")) return <>{children}</>;

  // `items.length` is the page, not the total: the queue endpoint is cursor
  // paginated at 40 and reports no count. So the badge means "at least this
  // many", and 99+ is shown past the page size rather than pretending 40 is the
  // whole queue.
  const reviewCount = queue.data?.items.length ?? null;

  return (
    <div className="flex min-h-svh">
      <Sidebar reviewCount={reviewCount} />

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-40 flex items-center gap-1 border-b border-white/[0.08] bg-slate-950/70 backdrop-blur-xl lg:hidden">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="ml-2 size-9 text-dim hover:text-white"
                aria-label="Open navigation"
              >
                <Menu className="size-4" />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="left"
              className="w-[16rem] border-white/[0.08] bg-[#050914] p-0"
            >
              <SheetTitle className="px-4 pt-4 text-[13px] font-semibold tracking-display text-white">
                Floorline
              </SheetTitle>
              <nav className="min-h-0 flex-1 overflow-y-auto p-2">
                {NAV_SECTIONS.map((section, index) => (
                  <div key={section.id} className={cn("space-y-0.5", index > 0 && "mt-3")}>
                    <p className="px-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-faint">
                      {section.label}
                    </p>
                    {NAV_ITEMS.filter((item) => item.section === section.id).map((item) => (
                      <MobileNavLink
                        key={item.href}
                        href={item.href}
                        label={item.label}
                        blurb={item.blurb}
                        current={navItemActive(item.href, pathname)}
                        badge={item.href === "/review" ? reviewCount : null}
                        onNavigate={() => setMobileOpen(false)}
                      />
                    ))}
                  </div>
                ))}
              </nav>
            </SheetContent>
          </Sheet>
          <span className="text-[13px] font-medium tracking-display text-white/90">
            Floorline
          </span>
        </div>

        <Header display={display} />
        <main className="above-glow min-w-0 flex-1 px-4 pb-14 pt-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}

/**
 * Plain anchor rather than next/link.
 *
 * The sheet closes on click, and next/link's client navigation plus a closing
 * animated portal raced often enough on the old dashboard that a tap sometimes
 * left the overlay up over the new screen. A full navigation is slower and
 * always correct, and this is a mobile fallback for a control-room tool.
 */
function MobileNavLink({
  href,
  label,
  blurb,
  current,
  badge,
  onNavigate,
}: {
  href: string;
  label: string;
  blurb: string;
  /** The screen being shown, so the open menu says where you are. */
  current: boolean;
  badge: number | null;
  onNavigate: () => void;
}) {
  return (
    <a
      href={href}
      onClick={onNavigate}
      aria-current={current ? "page" : undefined}
      className={cn(
        "flex items-start gap-2 rounded-lg px-2.5 py-2",
        "transition-colors hover:bg-white/[0.05] hover:text-white focus-visible:outline-2 focus-visible:outline-brand",
        current ? "bg-white/[0.06] text-white" : "text-dim",
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium">{label}</span>
        <span className="block truncate text-[11px] text-faint">{blurb}</span>
      </span>
      {badge != null && badge > 0 && (
        <span className="badge-risk badge-warn px-1.5 py-0 text-[10px] tabular-nums">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </a>
  );
}

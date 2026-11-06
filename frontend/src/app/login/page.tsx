"use client";

import { ArrowLeft, Loader2, MousePointerClick, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { signInAsGuestJudge, useConsolePosture } from "@/lib/console-posture";

/**
 * The published demo accounts, offered as click-to-fill. Addresses only: the
 * passwords are in the submission's private testing instructions, and a login
 * page that printed working credentials would make the audit trail's attribution
 * meaningless.
 */
const DEMO_ACCOUNTS = [
  { email: "judge@vf-logistics.demo", role: "Governance admin" },
  { email: "reviewer@vf-logistics.demo", role: "Reviewer only" },
] as const;

/**
 * The console's sign-in screen.
 *
 * Renders outside the app shell -- AppShell short-circuits on this path -- because
 * a sidebar full of links that all bounce back here is worse than no sidebar.
 *
 * That short-circuit left the page with NO navigation at all, which made it a dead
 * end: a visitor who followed "Sign in" out of curiosity had no way back to the
 * board except editing the URL. Worse, it implied signing in was required to see
 * anything, when reads are public by design. Both are fixed below -- the way out is
 * a link, and the reason you may not need to sign in at all is stated.
 */
export default function LoginPage() {
  return (
    // useSearchParams needs a Suspense boundary to prerender. Without one the
    // build fails rather than degrading, so the fallback is the page's own frame
    // with the form omitted.
    <Suspense fallback={<Frame />}>
      <Frame>
        <LoginForm />
      </Frame>
    </Suspense>
  );
}

function Frame({ children }: { children?: React.ReactNode }) {
  // Public reads are a deployment setting (VF_PUBLIC_READS here, ANONYMOUS_ROLE upstream). Until the
  // posture says they are on, the page promises nothing: a visitor told the board is public and then
  // shown "sign in" has been told something false.
  const publicReads = useConsolePosture().data?.auth.public_reads === true;
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-3 px-4 py-12">
      <div className="bento-card w-full max-w-sm p-7">
        <div className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-lg bg-brand/10 text-brand">
            <ShieldCheck className="size-4" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-[15px] font-semibold tracking-display text-white">
              Floorline
            </h1>
            <p className="truncate text-[11px] text-faint">
              Sign in to record decisions
            </p>
          </div>
        </div>

        {/* In the FRAME, not in the form, and that placement is the point.
            
            LoginForm sits inside a Suspense boundary because useSearchParams needs one
            to prerender, so the static HTML contains the fallback and the form arrives
            on hydration. A note telling a visitor they are in the wrong place is
            useless if it only appears after the form does -- and this text depends on
            no form state, so nothing keeps it there.
            
            The board, every case trace, the review queue and the audit trail are
            readable anonymously: VF_PUBLIC_READS on the console and ANONYMOUS_ROLE on
            the backend govern it. Billing and the archived original documents are not
            -- the API reserves them for operators and reviewers, and the console no
            longer lends a visitor its key to get round that. */}
        <p className="mt-5 rounded-md border border-white/10 bg-white/[0.04] px-2.5 py-2 text-[11px] leading-relaxed text-dim">
          {publicReads ? (
            <>
              <span className="font-medium text-white">
                You do not need an account to look around.
              </span>{" "}
              The board, every case trace, the review queue and the audit trail are public.
              Signing in is required to <em>act</em> — the API gives each account a role,
              and the audit trail names the person who made each decision.
            </>
          ) : (
            <>
              <span className="font-medium text-white">Sign in to look around.</span>{" "}
              On this deployment the board, the case traces, the review queue and the audit
              trail are shown to signed-in accounts only. Each account has a role, and the
              audit trail names the person who made each decision.
            </>
          )}
        </p>

        {children}

        {/* Where an account comes from, without printing one.
            
            Reviewer accounts live in VF_OPERATORS as PBKDF2-SHA256 records, so there is
            no self-service sign-up and no password to show here -- a login screen
            displaying working credentials would make the audit trail's attribution
            meaningless. What was missing was any statement of where to ASK, which left
            the form looking like a wall with no door. */}
        {/* text-dim, not text-faint. Measured at 4.3:1 against this card in the
            deployed page, faint fell under the WCAG AA 4.5:1 minimum for 11px text --
            and this is the note telling a reviewer where their password is, so it was
            the hardest-to-read text on the page and the one most needed. dim (#9ca3af)
            clears the threshold. Changed here rather than in --color-faint, which sets
            the visual hierarchy across the whole console. */}
        <p className="mt-4 border-t border-white/[0.06] pt-3 text-[11px] leading-relaxed text-dim">
          Reviewer accounts are issued by the operator, not self-service. If you are
          assessing this submission, the reviewer address and password are in the private
          testing-instructions field that came with it; if you are running your own
          deployment, see <span className="text-white/80">Signing in</span> in the README.
        </p>
      </div>

      {/* Outside the card, and deliberately so: this is a way out of the page, not
          a step in the form. */}
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-[12px] text-dim transition-colors hover:text-white"
      >
        <ArrowLeft className="size-3.5" />
        Back to the board
      </Link>
    </div>
  );
}

function LoginForm() {
  const params = useSearchParams();
  const posture = useConsolePosture().data;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [oneClickBusy, setOneClickBusy] = useState(false);

  /**
   * Where to land after signing in.
   *
   * Only a same-site absolute path is honoured. An attacker-supplied `next` of
   * `https://elsewhere/` would otherwise turn this form into an open redirect
   * that looks like it belongs to us -- and `//elsewhere` is protocol-relative,
   * so checking for a leading `/` alone is not enough.
   */
  // With no `next`, a signed-in person lands on the Floorline Pay overview -- the life of a payment
  // in four steps -- rather than on the live board, which is for watching the pipeline.
  const next = (() => {
    const raw = params.get("next");
    if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/pay";
    return raw;
  })();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        setError(body.error ?? "Sign-in failed.");
        setBusy(false);
        return;
      }

      // A full navigation rather than router.push: the cookie was just set, and
      // every screen behind this one reads server state that depends on it. A
      // client transition would reuse the React tree that was rendered while
      // signed out.
      window.location.assign(next);
    } catch {
      setError("Could not reach the server.");
      setBusy(false);
    }
  }

  async function oneClick() {
    setOneClickBusy(true);
    setError(null);
    const message = await signInAsGuestJudge(next);
    // Only reached on failure; success navigates away.
    setError(message || null);
    setOneClickBusy(false);
  }

  const offerOneClick = posture?.auth.one_click_judge === true;

  return (
    <div className="mt-5">
      {/* The judge's way in, first. A judge with three minutes should not have to
          find a password in a private field to try the product; this signs them
          in as a guest account with every right of its role except the ones that
          cannot be undone or outlive the visit: releasing a shipment, approving or
          executing a verification-loop action, publishing or revoking the
          boundary, clearing the board, and editing the prefilter rules. */}
      {offerOneClick && (
        <div className="mb-5">
          <Button
            type="button"
            className="h-10 w-full text-[13px]"
            onClick={oneClick}
            disabled={oneClickBusy || busy}
          >
            {oneClickBusy ? (
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <MousePointerClick className="size-4" />
            )}
            Continue as guest judge
          </Button>
          <p className="mt-2 text-[11px] leading-relaxed text-dim">
            One click, no password. You can inject shipments, block a case or ask
            for more information, and simulate policy. Releasing a shipment,
            publishing or revoking the agent&rsquo;s authority, clearing the board
            and editing the screening rules need a password sign-in below, because
            they cannot be undone or would carry over to the next judge.
          </p>
          <div className="mt-4 flex items-center gap-2 text-[11px] text-dim" aria-hidden>
            <span className="h-px flex-1 bg-white/10" />
            or sign in with a password
            <span className="h-px flex-1 bg-white/10" />
          </div>
        </div>
      )}

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="email" className="text-[12px] text-dim">
            Email
          </Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
          />
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.email}
                type="button"
                onClick={() => setEmail(account.email)}
                aria-label={`Use ${account.email} (${account.role})`}
                className="rounded-full border border-white/10 px-2 py-0.5 text-[10.5px] text-dim transition-colors hover:border-white/25 hover:text-white focus-visible:outline-2 focus-visible:outline-brand"
              >
                {account.email.split("@")[0]} · {account.role}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password" className="text-[12px] text-dim">
            Password
          </Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
        </div>

        {error && (
          <p
            role="alert"
            className="rounded-md border border-risk-critical/30 bg-risk-critical/10 px-2.5 py-2 text-[12px] text-risk-critical"
          >
            {error}
          </p>
        )}

        <Button
          type="submit"
          variant={offerOneClick ? "outline" : "default"}
          className="w-full"
          disabled={busy || oneClickBusy || !email || !password}
        >
          {busy && <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" />}
          {busy ? "Signing in" : "Sign in"}
        </Button>

        <p className="text-[11px] leading-relaxed text-dim">
          Every decision you record here is written to the audit trail under this
          address. By signing in you accept the{" "}
          <a href="/legal" className="text-brand hover:underline">
            terms and data handling policy
          </a>
          .
        </p>
      </form>
    </div>
  );
}

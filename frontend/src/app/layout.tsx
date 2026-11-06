import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { AppShell } from "@/components/layout/AppShell";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

// Used for the inference-metadata block, where a proportional font would make
// token counts and hashes ragged and hard to compare down a column.
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  // Floorline: the product's central rule is a risk floor the agents may raise
  // and never lower. VF Logistics is the operator in the demo, not the product.
  title: "Floorline Pay",
  description:
    "Shipment-fraud agents that hold the shipper's funds with PayPal and pay the carrier only after a " +
    "published release, with a risk floor " +
    "the agents may raise but never lower.",
};

// Typed explicitly rather than with Next's global LayoutProps<"/">. That global
// is generated into .next/types by a build, and both .next/ and next-env.d.ts are
// gitignored, so on a fresh clone -- and in CI, which typechecks before it builds
// -- it does not exist and `tsc --noEmit` fails on this line alone.
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      // `dark` is set here rather than toggled at runtime: this console is
      // dark-only, and leaving it to a client effect would flash a light
      // background before hydration.
      className={`dark ${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full">
        <Providers>
          {/* The shell lives here rather than in each page so that navigating
              between sections does not unmount the sidebar -- which would reset
              its scroll position and re-run the review-count query on every
              click. */}
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}

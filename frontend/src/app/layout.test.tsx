import { isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Each font returns the CSS variable it was configured with, so the class assertions
// below check that the layout passes the right variable through.
const fonts = vi.hoisted(() => ({
  Inter: vi.fn((options: { variable: string }) => ({ variable: options.variable })),
  JetBrains_Mono: vi.fn((options: { variable: string }) => ({ variable: options.variable })),
}));

vi.mock("next/font/google", () => fonts);
vi.mock("@/components/layout/AppShell", () => ({
  AppShell: function AppShell() {
    return null;
  },
}));
vi.mock("./providers", () => ({
  Providers: function Providers() {
    return null;
  },
}));

import { AppShell } from "@/components/layout/AppShell";

import RootLayout, { metadata } from "./layout";
import { Providers } from "./providers";

type Props = {
  children?: ReactNode;
  className?: string;
  lang?: string;
  suppressHydrationWarning?: boolean;
};

// The layout returns a tree of elements rather than rendering it into a div, because
// <html> inside a div is invalid DOM nesting and would only add React warnings.
function element(node: ReactNode): ReactElement<Props> {
  if (!isValidElement<Props>(node)) {
    throw new Error("expected a React element");
  }
  return node;
}

function renderLayout(child: ReactNode) {
  const html = element(RootLayout({ children: child }));
  const body = element(html.props.children);
  const providers = element(body.props.children);
  const shell = element(providers.props.children);
  return { html, body, providers, shell };
}

describe("RootLayout", () => {
  it("renders an English html element that is dark-only and not hydration-checked", () => {
    const { html } = renderLayout(<main>page</main>);

    expect(html.type).toBe("html");
    expect(html.props.lang).toBe("en");
    expect(html.props.suppressHydrationWarning).toBe(true);
  });

  it("sets dark mode, the font variables and the base classes on the html element", () => {
    const { html } = renderLayout(<main>page</main>);

    expect(html.props.className?.split(" ")).toEqual([
      "dark",
      "--font-sans",
      "--font-mono",
      "h-full",
      "antialiased",
    ]);
  });

  it("loads Inter and JetBrains Mono as latin subsets with swap display", () => {
    renderLayout(<main>page</main>);

    expect(fonts.Inter).toHaveBeenCalledWith(
      expect.objectContaining({ variable: "--font-sans", subsets: ["latin"], display: "swap" }),
    );
    expect(fonts.JetBrains_Mono).toHaveBeenCalledWith(
      expect.objectContaining({ variable: "--font-mono", subsets: ["latin"], display: "swap" }),
    );
  });

  it("gives the body a full-height class", () => {
    const { body } = renderLayout(<main>page</main>);

    expect(body.type).toBe("body");
    expect(body.props.className).toBe("min-h-full");
  });

  it("wraps the body contents in Providers", () => {
    const { providers } = renderLayout(<main>page</main>);

    expect(providers.type).toBe(Providers);
  });

  it("places the app shell inside Providers and hands it the page unchanged", () => {
    const page = <main>page</main>;
    const { shell } = renderLayout(page);

    expect(shell.type).toBe(AppShell);
    expect(shell.props.children).toBe(page);
  });
});

describe("Floorline metadata", () => {
  it("titles the console Floorline Pay", () => {
    expect(metadata.title).toBe("Floorline Pay");
  });

  it("describes the risk floor the agents may raise but never lower", () => {
    expect(metadata.description).toContain("risk floor the agents may raise but never lower");
    expect(metadata.description).toContain("PayPal");
    expect(metadata.description).not.toContain("Nebius");
  });
});

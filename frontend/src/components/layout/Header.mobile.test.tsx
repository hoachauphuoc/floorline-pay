import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { Header } from "./Header";

const mocks = vi.hoisted(() => ({ demo: false }));

vi.mock("@/lib/config", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/config")>()),
  get DEMO_MODE(): boolean {
    return mocks.demo;
  },
}));
vi.mock("@/components/layout/TenantSwitcher", () => ({
  TenantSwitcher: () => <div data-testid="tenant-switcher" />,
}));
vi.mock("@/components/layout/AccountMenu", () => ({
  AccountMenu: () => <div data-testid="account-menu" />,
}));
vi.mock("@/components/layout/HelpModeToggle", () => ({
  HelpModeToggle: () => <div data-testid="help-mode-toggle" />,
}));

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function renderHeader() {
  return render(
    <TooltipProvider>
      <Header />
    </TooltipProvider>,
  );
}

describe("Header on a phone", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    mocks.demo = false;
  });

  it("hides the Live pill below the sm breakpoint and shows it from sm up", () => {
    renderHeader();
    const wrapper = screen.getByText("Live", { exact: true }).closest("div");
    expect(wrapper).toHaveClass("hidden", "sm:block");
  });

  it("uses a tighter gap in the right-hand group on a phone", () => {
    renderHeader();
    const group = screen.getByTestId("account-menu").parentElement;
    expect(group).toHaveClass("gap-1", "sm:gap-2");
  });
});

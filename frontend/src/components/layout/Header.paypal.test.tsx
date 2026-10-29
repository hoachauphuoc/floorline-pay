import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { Header } from "./Header";

vi.mock("@/components/layout/TenantSwitcher", () => ({
  TenantSwitcher: () => <div data-testid="tenant-switcher" />,
}));
vi.mock("@/components/layout/AccountMenu", () => ({
  AccountMenu: () => <div data-testid="account-menu" />,
}));
vi.mock("@/components/layout/HelpModeToggle", () => ({
  HelpModeToggle: () => <div data-testid="help-mode-toggle" />,
}));

describe("Header PayPal mark", () => {
  it("says who the payments run on, with the official logo, on every page", () => {
    render(
      <TooltipProvider>
        <Header />
      </TooltipProvider>,
    );
    expect(screen.getByText("Payments by")).toBeInTheDocument();
    expect(screen.getByAltText("PayPal")).toHaveAttribute("src", "/paypal/paypal-logo.png");
  });

  it("puts the logo on a white pill so it stays readable on the dark header", () => {
    render(
      <TooltipProvider>
        <Header />
      </TooltipProvider>,
    );
    expect(screen.getByAltText("PayPal").parentElement).toHaveClass("bg-white");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PAYPAL_LOGO_SRC, PayPalLogo } from "./PayPalLogo";

describe("PayPalLogo", () => {
  it("serves the official file from the site itself and names it for screen readers", () => {
    render(<PayPalLogo />);
    const img = screen.getByAltText("PayPal");
    expect(img).toHaveAttribute("src", "/paypal/paypal-logo.png");
    expect(PAYPAL_LOGO_SRC).toBe("/paypal/paypal-logo.png");
  });

  it("is decorative when the words next to it already say PayPal", () => {
    const { container } = render(<PayPalLogo decorative />);
    expect(screen.queryByAltText("PayPal")).toBeNull();
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
  });

  it("keeps the proportions: the height is set and the width follows", () => {
    render(<PayPalLogo height={24} />);
    const style = screen.getByAltText("PayPal").getAttribute("style") ?? "";
    expect(style).toContain("height: 24px");
    expect(style).toContain("width: auto");
  });

  it("takes extra classes", () => {
    render(<PayPalLogo className="mr-2" />);
    expect(screen.getByAltText("PayPal")).toHaveClass("mr-2");
  });
});

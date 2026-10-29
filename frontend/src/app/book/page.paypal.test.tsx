import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import BookPage from "./page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BookPage />
    </QueryClientProvider>,
  );
}

describe("book page PayPal branding", () => {
  it("styles the pay button the way PayPal asks: gold, dark text, the mark inside", () => {
    renderPage();
    const button = screen.getByRole("button", { name: /Pay with PayPal/ });
    expect(button).toHaveClass("bg-[#FFC439]");
    expect(button.querySelector("img")).toHaveAttribute("src", "/paypal/paypal-logo.png");
    expect(button.querySelector("img")).toHaveAttribute("alt", "");
  });

  it("shows the logo again beside a line that says this is the PayPal sandbox", () => {
    renderPage();
    expect(screen.getByAltText("PayPal")).toBeInTheDocument();
    expect(screen.getByText(/PayPal sandbox: no real money moves/)).toBeInTheDocument();
  });
});

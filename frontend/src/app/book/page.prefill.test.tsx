import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createPaymentOrder: vi.fn(),
}));

import { createPaymentOrder } from "@/lib/api";

import BookPage from "./page";

const ORIGINAL_LOCATION = window.location;

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BookPage />
    </QueryClientProvider>,
  );
}

function at(search: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...ORIGINAL_LOCATION, origin: "https://floorline-pay.example", assign: vi.fn(), search },
  });
}

function value(label: string): string {
  return (screen.getByLabelText(label) as HTMLInputElement).value;
}

describe("BookPage pre-filled from a link", () => {
  beforeEach(() => {
    vi.mocked(createPaymentOrder).mockReset();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: ORIGINAL_LOCATION });
  });

  it("fills the fields named in the link", () => {
    at("?shipment_id=SHP-PAY-DEMO-17&shipper_company=Lotus%20Demo%20Ceramics%20Co%20Ltd&shipping_cost=1500&hs_code=6912.00");
    renderPage();
    expect(value("Shipment id")).toBe("SHP-PAY-DEMO-17");
    expect(value("Shipper")).toBe("Lotus Demo Ceramics Co Ltd");
    expect(value("Freight to pay (USD)")).toBe("1500");
    expect(value("HS code")).toBe("6912.00");
  });

  it("leaves the form empty and keeps a generated shipment id when the link has no fields", () => {
    at("");
    renderPage();
    expect(value("Shipment id")).toMatch(/^SHP-PAY-/);
    expect(value("Shipper")).toBe("");
    expect(value("Freight to pay (USD)")).toBe("");
  });

  it("does not fill the carrier PayPal account from a link", () => {
    at("?carrier_payee_email=attacker%40example.com&shipping_cost=1500");
    renderPage();
    expect(value("Carrier PayPal account for this load")).toBe("");
    expect(value("Freight to pay (USD)")).toBe("1500");
  });

  it("never makes an order by itself: the shipper still has to press Pay", () => {
    at("?shipping_cost=1500&shipper_company=Lotus%20Demo%20Ceramics%20Co%20Ltd");
    renderPage();
    expect(createPaymentOrder).not.toHaveBeenCalled();
  });

  it("lets the shipper change a filled field", () => {
    at("?shipping_cost=1500");
    renderPage();
    fireEvent.change(screen.getByLabelText("Freight to pay (USD)"), { target: { value: "900" } });
    expect(value("Freight to pay (USD)")).toBe("900");
  });
});

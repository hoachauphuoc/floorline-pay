import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createPaymentOrder: vi.fn(),
}));

import { createPaymentOrder } from "@/lib/api";

import BookPage from "./page";

const ORIGIN = "https://floorline-pay.example";
const ORIGINAL_LOCATION = window.location;

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BookPage />
    </QueryClientProvider>,
  );
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("BookPage countries", () => {
  const assign = vi.fn();

  beforeEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...ORIGINAL_LOCATION, origin: ORIGIN, assign },
    });
    window.sessionStorage.clear();
    assign.mockClear();
    vi.mocked(createPaymentOrder).mockReset();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: ORIGINAL_LOCATION });
  });

  it("asks for both countries and sends them with the order", async () => {
    vi.mocked(createPaymentOrder).mockResolvedValue({
      order_id: "5O190127TN364715T",
      paypal_status: "PAYER_ACTION_REQUIRED",
      approval_url: "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
    });
    renderPage();
    type("Shipment id", "SHP-PAY-1");
    type("Shipper", "Saigon Textile Export JSC");
    type("Shipper country", "Vietnam");
    type("Receiver country", "Vietnam");
    type("Freight to pay (USD)", "20");
    fireEvent.click(screen.getByRole("button", { name: /Pay with PayPal/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createPaymentOrder).mock.calls[0][0]).toMatchObject({
      booking: { shipper_country: "Vietnam", receiver_country: "Vietnam" },
    });
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BOOKING_KEY } from "@/lib/booking";

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

describe("BookPage", () => {
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

  it("starts with a generated shipment id and a Pay button that waits for a freight", () => {
    renderPage();
    expect((screen.getByLabelText("Shipment id") as HTMLInputElement).value).toMatch(/^SHP-PAY-/);
    expect(screen.getByRole("button", { name: /Pay with PayPal/ })).toBeDisabled();
    expect(screen.getByText(/freight to pay must be a number above zero/)).toBeInTheDocument();
  });

  it("makes the order, keeps the form, and sends the shipper to PayPal", async () => {
    vi.mocked(createPaymentOrder).mockResolvedValue({
      order_id: "5O190127TN364715T",
      paypal_status: "PAYER_ACTION_REQUIRED",
      approval_url: "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
    });
    renderPage();
    type("Shipment id", "SHP-PAY-1");
    type("Freight to pay (USD)", "1300");
    type("Carrier PayPal account for this load", "payments@mekong-haulage.example");
    fireEvent.click(screen.getByRole("button", { name: /Pay with PayPal/ }));

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    expect(assign).toHaveBeenCalledWith(
      "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
    );
    expect(createPaymentOrder).toHaveBeenCalledWith({
      amount: 1300,
      currency: "USD",
      reference_id: "SHP-PAY-1",
      return_url: `${ORIGIN}/book/return`,
      cancel_url: `${ORIGIN}/book`,
    });
    const stored = JSON.parse(window.sessionStorage.getItem(BOOKING_KEY) ?? "{}");
    expect(stored.order_id).toBe("5O190127TN364715T");
    expect(stored.form.carrier_payee_email).toBe("payments@mekong-haulage.example");
  });

  it("does not leave the page, or keep the form, when the order cannot be made", async () => {
    vi.mocked(createPaymentOrder).mockRejectedValue(new Error("PayPal is not configured"));
    renderPage();
    type("Freight to pay (USD)", "1300");
    fireEvent.click(screen.getByRole("button", { name: /Pay with PayPal/ }));

    await waitFor(() => expect(createPaymentOrder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/PayPal is not configured/)).toBeInTheDocument());
    expect(assign).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(BOOKING_KEY)).toBeNull();
  });
});

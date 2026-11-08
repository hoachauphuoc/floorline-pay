import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_BOOKING, saveBooking } from "@/lib/booking";

const ORDER = "5O190127TN364715T";
let query = `token=${ORDER}&PayerID=PAYER1`;

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(query),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  authorizePaymentOrder: vi.fn(),
  fetchBookingDraft: vi.fn(),
  submitShipmentEvent: vi.fn(),
}));

import { ApiError, authorizePaymentOrder, fetchBookingDraft, submitShipmentEvent } from "@/lib/api";

import BookReturnPage from "./page";

const FORM = {
  ...EMPTY_BOOKING,
  shipment_id: "SHP-PAY-1",
  shipping_cost: "1300",
  carrier_payee_email: "payments@mekong-haulage.example",
};
const GONE = `PayPal no longer holds order ${ORDER}: nothing was held or charged. Book again from Book and pay.`;
const AUTHORIZED = /The payment is authorized, not taken/;

describe("BookReturnPage subtitle", () => {
  beforeEach(() => {
    query = `token=${ORDER}&PayerID=PAYER1`;
    window.sessionStorage.clear();
    vi.mocked(authorizePaymentOrder).mockReset();
    vi.mocked(submitShipmentEvent).mockReset();
    vi.mocked(fetchBookingDraft).mockReset();
    vi.mocked(fetchBookingDraft).mockRejectedValue(new ApiError("no booking", 404, "http"));
  });

  it("does not claim an authorization while the payment is still being authorized", () => {
    vi.mocked(authorizePaymentOrder).mockReturnValue(new Promise(() => {}));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    expect(screen.getByText(/The payment is being authorized/)).toBeInTheDocument();
    expect(screen.queryByText(AUTHORIZED)).not.toBeInTheDocument();
  });

  it("says the payment is authorized, not taken, once the shipment is booked", async () => {
    vi.mocked(authorizePaymentOrder).mockResolvedValue({ order_id: ORDER, authorization_id: "0VF52814937998046" });
    vi.mocked(submitShipmentEvent).mockResolvedValue({ case_id: "CASE-9", state: "INGESTED" });
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    expect(screen.getByText(AUTHORIZED)).toBeInTheDocument();
  });

  it("does not claim an authorization when PayPal no longer holds the order", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValue(new ApiError(GONE, 409, "http"));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/no longer holds/)).toBeInTheDocument());
    expect(screen.getByText(/cannot be finished here\. Nothing was submitted\./)).toBeInTheDocument();
    expect(screen.queryByText(AUTHORIZED)).not.toBeInTheDocument();
  });

  it("does not claim an authorization when no booking is waiting", async () => {
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    expect(screen.getByText(/cannot be finished here\. Nothing was submitted\./)).toBeInTheDocument();
    expect(screen.queryByText(AUTHORIZED)).not.toBeInTheDocument();
  });

  it("does not claim an authorization when the payment could not be authorized", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValue(new ApiError("BAD_GATEWAY", 502, "unreachable"));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Reload this page/)).toBeInTheDocument());
    expect(screen.getByText(/the booking did not finish\. No money was paid out\./)).toBeInTheDocument();
    expect(screen.queryByText(AUTHORIZED)).not.toBeInTheDocument();
  });
});

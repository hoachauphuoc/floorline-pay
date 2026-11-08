import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_BOOKING, loadBooking, saveBooking } from "@/lib/booking";

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

describe("BookReturnPage when PayPal no longer holds the order", () => {
  beforeEach(() => {
    query = `token=${ORDER}&PayerID=PAYER1`;
    window.sessionStorage.clear();
    vi.mocked(authorizePaymentOrder).mockReset();
    vi.mocked(submitShipmentEvent).mockReset();
    vi.mocked(fetchBookingDraft).mockReset();
    vi.mocked(fetchBookingDraft).mockRejectedValue(new ApiError("no booking", 404, "http"));
  });

  it("says nothing was held, offers no reload and submits nothing", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValue(new ApiError(GONE, 409, "http"));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/no longer holds/)).toBeInTheDocument());
    expect(screen.getByText(/nothing was held or charged/)).toBeInTheDocument();
    expect(screen.queryByText(/audit API is unreachable/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Reload this page/)).not.toBeInTheDocument();
    expect(submitShipmentEvent).not.toHaveBeenCalled();
  });

  it("still offers a reload when the payment fails for another reason", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValue(new ApiError("BAD_GATEWAY", 502, "unreachable"));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Reload this page/)).toBeInTheDocument());
    expect(submitShipmentEvent).not.toHaveBeenCalled();
    expect(loadBooking(ORDER)).not.toBeNull();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_BOOKING, loadBooking, saveBooking } from "@/lib/booking";

let query = "token=5O190127TN364715T&PayerID=PAYER1";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(query),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  authorizePaymentOrder: vi.fn(),
  submitShipmentEvent: vi.fn(),
}));

import { authorizePaymentOrder, submitShipmentEvent } from "@/lib/api";

import BookReturnPage from "./page";

const ORDER = "5O190127TN364715T";
const FORM = {
  ...EMPTY_BOOKING,
  shipment_id: "SHP-PAY-1",
  shipping_cost: "1300",
  carrier_payee_email: "payments@mekong-haulage.example",
};

describe("BookReturnPage", () => {
  beforeEach(() => {
    query = `token=${ORDER}&PayerID=PAYER1`;
    window.sessionStorage.clear();
    vi.mocked(authorizePaymentOrder).mockReset();
    vi.mocked(submitShipmentEvent).mockReset();
    vi.mocked(authorizePaymentOrder).mockResolvedValue({
      order_id: ORDER,
      authorization_id: "0VF52814937998046",
    });
    vi.mocked(submitShipmentEvent).mockResolvedValue({ case_id: "CASE-9", state: "INGESTED" });
  });

  it("authorizes the approved order, submits the shipment with the authorization, and links the case", async () => {
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);

    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    expect(authorizePaymentOrder).toHaveBeenCalledWith(ORDER);
    const sent = vi.mocked(submitShipmentEvent).mock.calls[0][0];
    expect(sent).toMatchObject({
      shipment_id: "SHP-PAY-1",
      shipping_cost: 1300,
      payout_amount: 1300,
      carrier_payee_email: "payments@mekong-haulage.example",
      paypal_order_id: ORDER,
      paypal_authorization_id: "0VF52814937998046",
    });
    expect(screen.getByRole("link", { name: "Open CASE-9" })).toHaveAttribute("href", "/cases/CASE-9");
  });

  it("submits once even when the page is mounted twice", async () => {
    saveBooking(ORDER, FORM);
    render(
      <StrictMode>
        <BookReturnPage />
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    expect(authorizePaymentOrder).toHaveBeenCalledTimes(1);
    expect(submitShipmentEvent).toHaveBeenCalledTimes(1);
  });

  it("cannot submit the same booking again after a reload", async () => {
    saveBooking(ORDER, FORM);
    const first = render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    first.unmount();

    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    expect(submitShipmentEvent).toHaveBeenCalledTimes(1);
  });

  it("submits nothing when no booking was kept", async () => {
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    expect(authorizePaymentOrder).not.toHaveBeenCalled();
    expect(submitShipmentEvent).not.toHaveBeenCalled();
  });

  it("submits nothing when the order in the address is not the one that was kept", async () => {
    saveBooking("SOME-OTHER-ORDER", FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    expect(authorizePaymentOrder).not.toHaveBeenCalled();
    expect(submitShipmentEvent).not.toHaveBeenCalled();
  });

  it("submits nothing when the address carries no order", async () => {
    query = "PayerID=PAYER1";
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    expect(authorizePaymentOrder).not.toHaveBeenCalled();
  });

  it("keeps the booking when the payment could not be authorized, and a reload finishes it", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValueOnce(new Error("NETWORK_DOWN"));
    saveBooking(ORDER, FORM);
    const first = render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/NETWORK_DOWN/)).toBeInTheDocument());
    expect(loadBooking(ORDER)).toMatchObject({ shipment_id: "SHP-PAY-1" });
    expect(screen.getByText(/Reload this page/)).toBeInTheDocument();
    first.unmount();

    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    expect(submitShipmentEvent).toHaveBeenCalledTimes(1);
    expect(loadBooking(ORDER)).toBeNull();
  });

  it("keeps the booking when the case could not be created, and a reload sends the same shipment", async () => {
    vi.mocked(submitShipmentEvent).mockRejectedValueOnce(new Error("SERVER_BUSY"));
    saveBooking(ORDER, FORM);
    const first = render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/SERVER_BUSY/)).toBeInTheDocument());
    expect(loadBooking(ORDER)).toMatchObject({ shipment_id: "SHP-PAY-1" });
    first.unmount();

    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/Booked\./)).toBeInTheDocument());
    expect(submitShipmentEvent).toHaveBeenCalledTimes(2);
    expect(vi.mocked(submitShipmentEvent).mock.calls[1][0]).toMatchObject({
      shipment_id: "SHP-PAY-1",
      paypal_order_id: ORDER,
    });
    expect(loadBooking(ORDER)).toBeNull();
  });

  it("does not submit the shipment when the payment could not be authorized", async () => {
    vi.mocked(authorizePaymentOrder).mockRejectedValue(new Error("ORDER_NOT_APPROVED"));
    saveBooking(ORDER, FORM);
    render(<BookReturnPage />);
    await waitFor(() => expect(authorizePaymentOrder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/ORDER_NOT_APPROVED/)).toBeInTheDocument());
    expect(submitShipmentEvent).not.toHaveBeenCalled();
    expect(screen.queryByText(/Booked\./)).not.toBeInTheDocument();
  });
});

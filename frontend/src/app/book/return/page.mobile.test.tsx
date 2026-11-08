import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  authorizePaymentOrder: vi.fn(),
  fetchBookingDraft: vi.fn(),
  submitShipmentEvent: vi.fn(),
}));

import { ApiError, fetchBookingDraft } from "@/lib/api";

import BookReturnPage from "./page";

describe("BookReturnPage on a phone", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.mocked(fetchBookingDraft).mockRejectedValue(new ApiError("no booking", 404, "http"));
  });

  it("gives a stopped booking a 44px link back to Book and pay", async () => {
    render(<BookReturnPage />);
    await waitFor(() => expect(screen.getByText(/No booking is waiting/)).toBeInTheDocument());
    const link = screen.getByRole("link", { name: "Go to Book and pay" });
    expect(link).toHaveAttribute("href", "/book");
    expect(link.className.split(" ")).toContain("h-11");
  });
});

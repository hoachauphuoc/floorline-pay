import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  approveCase: vi.fn(async () => ({
    approval: { approval_id: "evapr-9", requires_second: true },
    state: "APPROVED",
  })),
  executeApproval: vi.fn(async () => ({ state: "RELEASED_BY_HUMAN" })),
  secondApprove: vi.fn(async () => ({ state: "APPROVED" })),
}));
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/identity")>()),
  useIdentity: () => ({
    data: {
      email: "second@forwarder.example",
      authenticated_by: "console_session",
      acts_for_a_person: true,
      role: "reviewer",
      roles: ["reviewer"],
      grants: ["viewer", "reviewer", "operator"],
      role_source: "test",
      tenant_id: null,
      session_method: "password",
    },
  }),
}));

import { approveCase, executeApproval, secondApprove } from "@/lib/api";
import type { Case } from "@/lib/types";

import { ReviewPanel } from "./ReviewPanel";

/**
 * Dual approval, console side: a release large enough to need two people is not executed
 * by the person who approved it first, and the second person approves it with a note.
 */

function approval(over: Record<string, unknown> = {}) {
  return {
    approval_id: "evapr-1", action: "release", approver: "first@forwarder.example",
    evidence_version: 1, status: "VALID", at: "", requires_second: true, ...over,
  };
}

function makeCase(state: string, approvals: Record<string, unknown>[] = []): Case {
  return {
    case_id: "CASE-1",
    shipment_id: "CASE-1",
    state,
    verification: {
      evidence_version: 1, requests: [], documents: [], evaluations: [], approvals,
    },
  } as unknown as Case;
}

function renderPanel(c: Case, onDecided = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipPrimitive.Provider>
        <ReviewPanel
          summary={c}
          detail={c}
          detailLoading={false}
          detailError={null}
          onDecided={onDecided}
        />
      </TooltipPrimitive.Provider>
    </QueryClientProvider>,
  );
  return onDecided;
}

describe("ReviewPanel dual approval", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not execute a release that needs a second approver", async () => {
    const onDecided = renderPanel(makeCase("HELD_FOR_REVIEW"));
    fireEvent.click(screen.getByRole("button", { name: "Release" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(approveCase).toHaveBeenCalledWith("CASE-1", "release", ""));
    await waitFor(() => expect(onDecided).toHaveBeenCalledTimes(1));
    expect(executeApproval).not.toHaveBeenCalled();
  });

  it("asks for the second approver's note and sends it with the approval id", async () => {
    const onDecided = renderPanel(makeCase("APPROVED", [approval()]));
    expect(screen.getByTestId("second-approval").textContent).toMatch(/second approver/);
    expect(screen.queryByRole("button", { name: "Execute" })).toBeNull();
    const button = screen.getByRole("button", { name: "Approve as second approver" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("What you checked"), { target: { value: "invoice matches the booking" } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(secondApprove).toHaveBeenCalledWith("CASE-1", "evapr-1", "invoice matches the booking"),
    );
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
  });

  it("tells the first approver that someone else must approve it", () => {
    renderPanel(makeCase("APPROVED", [approval({ approver: "Second@Forwarder.example" })]));
    expect(screen.getByTestId("second-approval").textContent).toMatch(/different reviewer/);
    expect(screen.queryByRole("button", { name: "Approve as second approver" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Execute" })).toBeNull();
  });

  it("offers Execute once the second approval is on file", () => {
    renderPanel(makeCase("APPROVED", [approval({
      second_approval: { approver: "second@forwarder.example", at: "", note: "checked" },
    })]));
    expect(screen.queryByTestId("second-approval")).toBeNull();
    expect(screen.getByRole("button", { name: "Execute" })).toBeTruthy();
  });

  it("offers Execute straight away for a release that needs no second approver", () => {
    renderPanel(makeCase("APPROVED", [approval({ requires_second: undefined })]));
    expect(screen.queryByTestId("second-approval")).toBeNull();
    expect(screen.getByRole("button", { name: "Execute" })).toBeTruthy();
  });
});

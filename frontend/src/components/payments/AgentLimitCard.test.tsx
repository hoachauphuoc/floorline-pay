import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentLimit } from "@/lib/api";

const mocks = vi.hoisted(() => ({ publishAgentLimit: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  publishAgentLimit: mocks.publishAgentLimit,
}));

import { AgentLimitCard } from "./AgentLimitCard";

// Radix measures floating content with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

const LIMIT: AgentLimit = {
  limit_usd: "1000",
  published_by: "admin@forwarder.example",
  reason: "first month",
  at: "2026-11-06T00:00:00+00:00",
};

function show(limit: AgentLimit | null, lock: string | null = null, onDone: () => void = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <AgentLimitCard limit={limit} lock={lock} onDone={onDone} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function fill(amount: string, reason: string) {
  fireEvent.change(screen.getByLabelText("Agent limit in USD (0 turns it off)"), { target: { value: amount } });
  fireEvent.change(screen.getByLabelText("Why the agent limit changes"), { target: { value: reason } });
}

describe("AgentLimitCard", () => {
  beforeEach(() => {
    mocks.publishAgentLimit.mockReset();
  });

  it("says the agent is off until a limit is published", () => {
    show(null);
    expect(screen.getByText("Off. Every payment waits for a person.")).toBeInTheDocument();
  });

  it("shows the limit, who published it and why", () => {
    show(LIMIT);
    const card = screen.getByRole("region", { name: "Agent payments" });
    expect(card).toHaveTextContent("1000");
    expect(card).toHaveTextContent("admin@forwarder.example");
    expect(card).toHaveTextContent("first month");
    expect(screen.queryByText("Off. Every payment waits for a person.")).not.toBeInTheDocument();
  });

  it("publishes the amount with the reason and refreshes", async () => {
    mocks.publishAgentLimit.mockResolvedValue({ agent_limit: LIMIT });
    const onDone = vi.fn();
    show(null, null, onDone);
    fill("1000", "  first month  ");
    fireEvent.click(screen.getByRole("button", { name: "Publish agent limit" }));
    await waitFor(() => expect(mocks.publishAgentLimit).toHaveBeenCalledWith(1000, "first month"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("turns the agent off with zero", async () => {
    mocks.publishAgentLimit.mockResolvedValue({ agent_limit: null });
    show(LIMIT);
    fill("0", "a carrier was phished");
    fireEvent.click(screen.getByRole("button", { name: "Publish agent limit" }));
    await waitFor(() => expect(mocks.publishAgentLimit).toHaveBeenCalledWith(0, "a carrier was phished"));
  });

  it.each([
    ["", "a reason"],
    ["1000", "   "],
  ])("waits for an amount and a reason (%s, %s)", (amount, reason) => {
    show(null);
    fill(amount, reason);
    expect(screen.getByRole("button", { name: "Publish agent limit" })).toBeDisabled();
  });

  it("is locked for anyone who may not publish it", () => {
    show(null, "This needs a governance admin");
    fill("1000", "a reason");
    expect(screen.getByRole("button", { name: "Publish agent limit" })).toBeDisabled();
  });

  it("does not refresh when the server refuses", async () => {
    mocks.publishAgentLimit.mockRejectedValue(new Error("the agent limit must stay below the second-approval threshold"));
    const onDone = vi.fn();
    show(null, null, onDone);
    fill("2500", "too high");
    fireEvent.click(screen.getByRole("button", { name: "Publish agent limit" }));
    await waitFor(() => expect(mocks.publishAgentLimit).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish agent limit" })).toBeEnabled());
    expect(onDone).not.toHaveBeenCalled();
  });
});

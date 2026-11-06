import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ query: { value: "" }, guest: vi.fn() }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mocks.query.value),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
}));
vi.mock("@/lib/console-posture", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/console-posture")>()),
  useConsolePosture: () => ({
    data: { auth: { login_required: true, public_reads: false, session: null, one_click_judge: true } } as never,
  }),
  signInAsGuestJudge: mocks.guest,
}));

import LoginPage from "./page";

function landing(query: string) {
  mocks.query.value = query;
  render(<LoginPage />);
  fireEvent.click(screen.getByRole("button", { name: /Continue as guest judge/ }));
  return mocks.guest.mock.calls[0][0];
}

describe("Login page: where a sign-in lands", () => {
  beforeEach(() => {
    mocks.guest.mockReset();
    mocks.guest.mockResolvedValue("");
  });

  it("lands on the Floorline Pay overview when no page was asked for", () => {
    expect(landing("")).toBe("/pay");
  });

  it("goes to the page that was asked for", () => {
    expect(landing("next=/review")).toBe("/review");
  });

  it("ignores an address that leaves the site", () => {
    expect(landing("next=//elsewhere.example")).toBe("/pay");
  });
});

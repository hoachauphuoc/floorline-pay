import { describe, expect, it } from "vitest";

import { BOARD_AUTO_REFRESH, POLL_BUSY_MS, POLL_IDLE_MS, boardRefetchMs } from "@/lib/pipeline";

describe("boardRefetchMs", () => {
  it("refreshes the board by itself now that the project is billed: fast while work is in flight, slowly when idle", () => {
    expect(BOARD_AUTO_REFRESH).toBe(true);
    expect(boardRefetchMs(undefined)).toBe(POLL_IDLE_MS);
    expect(boardRefetchMs(0)).toBe(POLL_IDLE_MS);
    expect(boardRefetchMs(3)).toBe(POLL_BUSY_MS);
  });

  it("polls fast while work is in flight and slowly when idle once auto refresh is on", () => {
    expect(boardRefetchMs(3, true)).toBe(POLL_BUSY_MS);
    expect(boardRefetchMs(0, true)).toBe(POLL_IDLE_MS);
    expect(boardRefetchMs(undefined, true)).toBe(POLL_IDLE_MS);
  });
});

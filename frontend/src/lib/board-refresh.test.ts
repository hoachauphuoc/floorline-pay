import { describe, expect, it } from "vitest";

import { BOARD_AUTO_REFRESH, POLL_BUSY_MS, POLL_IDLE_MS, boardRefetchMs } from "@/lib/pipeline";

describe("boardRefetchMs", () => {
  it("does not refresh the board by itself while the Firestore read quota is the limit", () => {
    expect(BOARD_AUTO_REFRESH).toBe(false);
    expect(boardRefetchMs(undefined)).toBe(false);
    expect(boardRefetchMs(0)).toBe(false);
    expect(boardRefetchMs(3)).toBe(false);
  });

  it("polls fast while work is in flight and slowly when idle once auto refresh is on", () => {
    expect(boardRefetchMs(3, true)).toBe(POLL_BUSY_MS);
    expect(boardRefetchMs(0, true)).toBe(POLL_IDLE_MS);
    expect(boardRefetchMs(undefined, true)).toBe(POLL_IDLE_MS);
  });
});

import { describe, expect, it } from "vitest";

import { NAV_ITEMS } from "@/components/layout/Sidebar";
import { helpFor } from "@/lib/help-content";

const PAY_ROUTES = ["/pay", "/book", "/payments", "/settlement", "/carriers", "/controls", "/defense"];

describe("help for the Floorline Pay screens", () => {
  it("lists the seven Floorline Pay screens in the sidebar", () => {
    const hrefs = NAV_ITEMS.filter((item) => item.section === "pay").map((item) => item.href);
    expect(hrefs).toEqual(PAY_ROUTES);
  });

  it.each(PAY_ROUTES)("explains %s with a title and a body", (href) => {
    const entry = helpFor(`nav:${href}`);
    expect(entry).toBeDefined();
    expect(entry?.title.length).toBeGreaterThan(0);
    expect(entry?.body.length).toBeGreaterThan(40);
  });

  it("gives every sidebar item an entry", () => {
    const missing = NAV_ITEMS.filter((item) => helpFor(`nav:${item.href}`) === undefined).map((item) => item.href);
    expect(missing).toEqual([]);
  });

  it("says who may stop payments and who may change a limit", () => {
    expect(helpFor("nav:/controls")?.body).toMatch(/only a governance admin/);
  });

  it("says that no replayed attack moves money", () => {
    expect(helpFor("nav:/defense")?.body).toMatch(/None of them moves money/);
  });

  it("does not say that only a person releases the money", () => {
    for (const href of PAY_ROUTES) {
      const entry = helpFor(`nav:${href}`);
      expect(`${entry?.body ?? ""} ${entry?.note ?? ""}`).not.toMatch(/only after a person releases/);
    }
  });
});

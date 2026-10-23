import { describe, expect, it } from "vitest";

import { NAV_ITEMS, NAV_SECTIONS, navItemActive } from "./Sidebar";

describe("The sidebar groups", () => {
  it("opens with Floorline Pay, in the order of a payment's life", () => {
    expect(NAV_SECTIONS[0]).toEqual({ id: "pay", label: "Floorline Pay" });
    const hrefs = NAV_ITEMS.filter((item) => item.section === "pay").map((item) => item.href);
    expect(hrefs).toEqual(["/pay", "/book", "/payments", "/settlement", "/carriers", "/controls", "/defense"]);
  });

  it("puts every screen in a group that is shown, once", () => {
    const shown = new Set(NAV_SECTIONS.map((section) => section.id));
    expect(NAV_ITEMS.every((item) => shown.has(item.section))).toBe(true);
    expect(new Set(NAV_ITEMS.map((item) => item.href)).size).toBe(NAV_ITEMS.length);
  });

  it("keeps the screening screens that were there", () => {
    const rest = NAV_ITEMS.filter((item) => item.section !== "pay").map((item) => item.href);
    for (const href of ["/today", "/review", "/outcomes", "/", "/radar", "/audit", "/admin"]) {
      expect(rest).toContain(href);
    }
  });
});

describe("Which screen is selected", () => {
  it("does not light up the overview on a longer path that starts with it", () => {
    expect(navItemActive("/pay", "/pay")).toBe(true);
    expect(navItemActive("/pay", "/payments")).toBe(false);
    expect(navItemActive("/payments", "/payments")).toBe(true);
  });
});

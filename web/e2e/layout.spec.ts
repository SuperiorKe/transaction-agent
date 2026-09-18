import { expect, test } from "@playwright/test";

import { FIXTURES } from "../src/fixtures";

// DESIGN.md decision 2 / the owner UI's one screen at 1280x720 for the whole two-minute demo:
// nothing may require a page scroll, and Approve/Decline must always be reachable without one.
// A `/ship` design review found that `.transaction-layout`'s three cards (ConstraintCard,
// CallPanel, RecommendationCard), laid out with no row/span assignment, let CallPanel's height
// push RecommendationCard below the fold -- most visibly in the canonical demo scenario (a KES
// 23,000 quote against a KES 20,000 cap, CLAUDE.md's own test scenario) since escalation reasons
// and full transcripts are exactly the content that made the card tall enough to overflow.

test.describe("every fixture fits 1280x720 with no page scroll", () => {
  for (const name of Object.keys(FIXTURES)) {
    test(name, async ({ page }) => {
      await page.goto(`/?mock=${encodeURIComponent(name)}`);
      await page.waitForLoadState("networkidle");

      const { scrollHeight, clientHeight, scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: document.documentElement.clientHeight,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      expect(scrollWidth, "no horizontal overflow").toBe(clientWidth);
      expect(scrollHeight, "no vertical overflow (the page had to scroll)").toBeLessThanOrEqual(
        clientHeight,
      );
    });
  }
});

test("the canonical demo scenario keeps Approve fully on screen", async ({ page }) => {
  await page.goto("/?mock=awaiting-approval");
  await page.waitForLoadState("networkidle");

  const box = await page.getByRole("button", { name: "Approve" }).boundingBox();

  expect(box).not.toBeNull();
  expect(box!.y, "Approve's top edge is on screen").toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height, "Approve's bottom edge is on screen").toBeLessThanOrEqual(720);
});

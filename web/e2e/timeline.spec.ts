import { expect, test } from "@playwright/test";

// The whole pipe: vite build → FastAPI serves web/dist at / → the page polls GET /transactions/{id}
// → the timeline follows real transition() calls within 2 s (issue #7 acceptance criteria 1 and 4).
//
// scripts/e2e_server.py runs the app on a throwaway SQLite file with FakeTelephonyProvider and no
// .env. Status changes go through a test-only route that calls app/states.py's transition(), so
// status.changed rows are written exactly as in production. Nothing here calls /start, so no phone
// can ring.

const MOCK_STRIP = "Mock data — not a live call";

function tomorrowInNairobi(): string {
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Nairobi" }).format(tomorrow);
}

test("the built page is served by FastAPI and follows status changes within 2 s", async ({
  page,
  request,
}) => {
  const created = await request.post("/transactions", {
    data: {
      request: "Photographer for tomorrow in Nairobi. Max KES 20,000. Negotiate twice.",
      service: "photography",
      service_date: tomorrowInNairobi(),
      location: "Nairobi",
      max_budget: 20000,
      max_attempts: 2,
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const { id } = (await created.json()) as { id: string };

  const transition = async (target: string) => {
    const response = await request.post(`/__e2e/transactions/${id}/transition`, {
      data: { target },
    });
    expect(response.status(), await response.text()).toBe(200);
  };

  await page.goto(`/?tx=${id}`);
  await expect(page.getByRole("listitem", { name: "Request: current" })).toBeVisible();
  await expect(page.getByText(MOCK_STRIP)).toHaveCount(0);

  await transition("PROVIDER_SELECTED");
  await transition("CALLING");
  await expect(page.getByRole("listitem", { name: "Calling: current" })).toBeVisible({
    timeout: 2000,
  });
  await expect(page.getByRole("listitem", { name: "Request: reached" })).toBeVisible();

  await transition("UNAVAILABLE");
  await transition("FAILED");
  await expect(page.getByRole("listitem", { name: "Done: failed" })).toBeVisible({
    timeout: 2000,
  });
  await expect(page.getByRole("listitem", { name: "Negotiating: skipped" })).toBeVisible();

  // Terminal: the poll loop has stopped, so no further requests for this transaction.
  const polls: string[] = [];
  page.on("request", (sent) => {
    if (sent.url().includes(`/transactions/${id}`)) polls.push(sent.url());
  });
  await page.waitForTimeout(2500);
  expect(polls).toEqual([]);
});

test("a mock page makes no API requests at all", async ({ page }) => {
  const apiCalls: string[] = [];
  page.on("request", (sent) => {
    if (new URL(sent.url()).pathname.startsWith("/transactions")) apiCalls.push(sent.url());
  });

  await page.goto("/?mock=awaiting-approval");

  await expect(page.getByText(MOCK_STRIP)).toBeVisible();
  await expect(page.getByRole("listitem", { name: "Your decision: current" })).toBeVisible();
  await page.waitForTimeout(2500);
  expect(apiCalls).toEqual([]);
});

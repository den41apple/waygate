import { expect, test } from "@playwright/test";

import { createServerViaRest, deleteServerViaRest, getToken, login } from "./helpers";

test.describe("UpdateAgentModal", () => {
  test("показывает dropdown с версиями из /api/v1/agent-releases", async ({ page, request }) => {
    // Мокаем GitHub-прокси чтобы тест не зависел от сети + не упирался в rate-limit.
    // Должно быть до login (page.goto) — иначе React уже сделал fetch.
    await page.route("**/api/v1/agent-releases", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          releases: [
            {
              tag: "agent-v0.2.0",
              version: "0.2.0",
              name: "agent v0.2.0",
              published_at: "2026-05-03T12:00:00Z",
              wheel_url: "https://example.com/v0.2.0/waygate_agent-py3-none-any.whl",
            },
            {
              tag: "agent-v0.1.17",
              version: "0.1.17",
              name: "agent v0.1.17",
              published_at: "2026-04-01T12:00:00Z",
              wheel_url: "https://example.com/v0.1.17/waygate_agent-py3-none-any.whl",
            },
          ],
        }),
      }),
    );

    await login(page);
    const token = await getToken(page);
    const server = await createServerViaRest(request, token, {
      host: "10.0.53.1",
      name: "e2e-update-srv",
    });

    try {
      await page.reload();
      await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
      await page.locator(".sb-item .name").filter({ hasText: "e2e-update-srv" }).click();
      await page.getByRole("button", { name: /Обновить агент/ }).click();

      // В select'е должны быть оба варианта (latest подсвечен).
      const select = page.locator("select.select").first();
      await expect(select).toBeVisible({ timeout: 5_000 });
      await expect(select.locator("option", { hasText: "0.2.0" })).toBeAttached();
      await expect(select.locator("option", { hasText: "0.1.17" })).toBeAttached();
      await expect(select.locator("option", { hasText: "ввести вручную" })).toBeAttached();
    } finally {
      await deleteServerViaRest(request, token, server.id);
    }
  });
});

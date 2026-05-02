import { expect, test } from "@playwright/test";

import { login } from "./helpers";

test.describe("Список серверов", () => {
  test("создание через REST появляется в sidebar", async ({ page, request }) => {
    await login(page);

    // Берём токен из localStorage — фронт его туда положил после логина.
    const token = await page.evaluate(() => {
      const stored = window.localStorage.getItem("waygate-auth");
      if (!stored) return null;
      const parsed = JSON.parse(stored) as { state?: { token?: string } };
      return parsed.state?.token ?? null;
    });
    expect(token).not.toBeNull();

    const createResponse = await request.post("http://127.0.0.1:8000/api/v1/servers", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        host: "10.0.99.10",
        port: 7743,
        name: "e2e-rest-srv",
        token: "agent-token-fake",
        region: "EU",
      },
    });
    expect(createResponse.status()).toBe(201);
    const server = await createResponse.json();

    // POST /servers сейчас не эмиттит WS-event; sidebar обновится только через
    // refetch. Перезагружаем страницу чтобы увидеть свежий список.
    await page.reload();
    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
    // .sb-item .name — sidebar-only, чтобы не конфликтовать с topbar-title
    // когда сервер автовыбран после reload.
    await expect(page.locator(".sb-item .name").filter({ hasText: "e2e-rest-srv" })).toBeVisible({
      timeout: 10_000,
    });

    // Удаляем — должен пропасть после reload.
    const deleteResponse = await request.delete(
      `http://127.0.0.1:8000/api/v1/servers/${server.id}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(deleteResponse.status()).toBe(204);

    await page.reload();
    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".sb-item .name").filter({ hasText: "e2e-rest-srv" })).toBeHidden({
      timeout: 10_000,
    });
  });
});

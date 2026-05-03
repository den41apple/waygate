import { expect, test } from "@playwright/test";

import { createServerViaRest, deleteServerViaRest, getToken, login } from "./helpers";

test.describe("Редактирование настроек сервера", () => {
  test("через UI меняем имя и регион — изменения сохраняются", async ({ page, request }) => {
    await login(page);
    const token = await getToken(page);
    const server = await createServerViaRest(request, token, {
      host: "10.0.52.1",
      name: "e2e-edit-old",
      region: "OLD",
    });

    try {
      await page.reload();
      await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
      await page.locator(".sb-item .name").filter({ hasText: "e2e-edit-old" }).click();

      // Открыть EditServerModal через Topbar → Настройки.
      await page.getByRole("button", { name: /Настройки/ }).click();

      const nameInput = page.locator('input[placeholder="edge-eu"]');
      await nameInput.fill("e2e-edit-new");
      const regionInput = page.locator('input[placeholder="EU"]');
      await regionInput.fill("APAC");

      await page.getByRole("button", { name: /Сохранить/ }).click();

      // Sidebar должен показать новое имя.
      await expect(
        page.locator(".sb-item .name").filter({ hasText: "e2e-edit-new" }),
      ).toBeVisible({ timeout: 10_000 });
    } finally {
      await deleteServerViaRest(request, token, server.id);
    }
  });
});

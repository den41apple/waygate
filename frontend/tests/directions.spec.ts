import { expect, test } from "@playwright/test";

import { createServerViaRest, deleteServerViaRest, getToken, login } from "./helpers";

test.describe("Routing-направления", () => {
  test("создание через REST → видно в RoutingTab", async ({ page, request }) => {
    await login(page);
    const token = await getToken(page);
    const server = await createServerViaRest(request, token, {
      host: "10.0.50.1",
      name: "e2e-direction-srv",
    });

    try {
      const dirResponse = await request.post(
        `http://127.0.0.1:8000/api/v1/servers/${server.id}/directions`,
        {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            name: "e2e-direction",
            awg_client_id: null,
            via_interface: "awg-test",
            via_gateway: "10.66.66.1",
            geo_list_ids: [],
            dns_rule_ids: [],
            ipset_group_ids: [],
            scope: "host",
            scope_target: null,
            enabled: true,
          },
        },
      );
      expect(dirResponse.status()).toBe(201);

      await page.reload();
      await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
      // Кликаем на сервер в sidebar чтобы он стал active.
      await page.locator(".sb-item .name").filter({ hasText: "e2e-direction-srv" }).click();
      await expect(page.getByText("e2e-direction").first()).toBeVisible({ timeout: 10_000 });
    } finally {
      await deleteServerViaRest(request, token, server.id);
    }
  });

  test("edit-кнопка открывает модалку с pre-filled полями", async ({ page, request }) => {
    /**
     * Регрессия #C6: edit на direction-карточке → AddRoutingDirectionModal
     * запускается с editing prop'ом, поля name/via_interface/via_gateway
     * заполнены текущими значениями. Проверяем именно прокидывание editing'а,
     * не сам PATCH (это проверено в backend-тесте).
     */
    await login(page);
    const token = await getToken(page);
    const server = await createServerViaRest(request, token, {
      host: "10.0.50.2",
      name: "e2e-edit-dir-srv",
    });

    try {
      const dirResponse = await request.post(
        `http://127.0.0.1:8000/api/v1/servers/${server.id}/directions`,
        {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            name: "edit-target",
            awg_client_id: null,
            via_interface: "awg-edit-test",
            via_gateway: "10.77.77.1",
            geo_list_ids: [],
            dns_rule_ids: [],
            ipset_group_ids: [],
            scope: "host",
            scope_target: null,
            enabled: true,
          },
        },
      );
      expect(dirResponse.status()).toBe(201);

      await page.reload();
      await page.locator(".sb-item .name").filter({ hasText: "e2e-edit-dir-srv" }).click();
      await expect(page.getByText("edit-target").first()).toBeVisible({ timeout: 10_000 });

      // На карточке direction'а кликаем edit (рядом с toggle/delete).
      const card = page.locator(".card").filter({ hasText: "edit-target" });
      await card.locator('button[title="Редактировать"]').click();

      // Модалка открыта в edit-режиме — title содержит «Редактировать».
      await expect(page.getByText(/Редактировать направление/i)).toBeVisible({ timeout: 5_000 });
      // Поле имени pre-filled.
      const nameInput = page.locator('.modal input[placeholder*="Streaming"]');
      await expect(nameInput).toHaveValue("edit-target");
    } finally {
      await deleteServerViaRest(request, token, server.id);
    }
  });
});

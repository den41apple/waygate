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
});

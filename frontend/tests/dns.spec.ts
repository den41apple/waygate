import { expect, test } from "@playwright/test";

import { createServerViaRest, deleteServerViaRest, getToken, login } from "./helpers";

test.describe("DNS-правила", () => {
  test("создание через REST → видно в Lists → DNS", async ({ page, request }) => {
    await login(page);
    const token = await getToken(page);
    const server = await createServerViaRest(request, token, {
      host: "10.0.51.1",
      name: "e2e-dns-srv",
    });

    try {
      const dnsResponse = await request.post(
        `http://127.0.0.1:8000/api/v1/servers/${server.id}/dns`,
        {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            name: "e2e-dns-streaming",
            domains: ["example-streaming.test", "*.cdn-streaming.test"],
            ipset_name: "dns-e2e-streaming",
            enabled: true,
          },
        },
      );
      expect(dnsResponse.status()).toBe(201);

      await page.reload();
      await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
      await page.locator(".sb-item .name").filter({ hasText: "e2e-dns-srv" }).click();
      // Перейти на Lists таб → подтаб DNS.
      await page.getByRole("button", { name: /Lists/ }).click();
      await page.getByRole("button", { name: /DNS/ }).click();
      await expect(page.getByText("e2e-dns-streaming").first()).toBeVisible({ timeout: 10_000 });
    } finally {
      await deleteServerViaRest(request, token, server.id);
    }
  });
});

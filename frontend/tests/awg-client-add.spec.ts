import { expect, test } from "@playwright/test";

import { login } from "./helpers";

const VALID_PRIV = "Wyw1Tr4L/NV0SKMDjNtwhAKgQQkY/NlMXhwRjZrVQ4o=";
const VALID_PUB = "k6E1U4ZvkV8Lxay5d8HvPCtHsO0XG6iZzQOvmW+qWrY=";

const VALID_CONFIG = `[Interface]
Address = 10.66.66.2/24
PrivateKey = ${VALID_PRIV}

[Peer]
PublicKey = ${VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
`;

test.describe("AWG-клиент в UI", () => {
  test("открывает модалку, парсит .conf, отправляет на бэкенд", async ({ page, request }) => {
    await login(page);

    // Создадим тестовый сервер через REST — иначе нет куда добавлять клиента.
    const token = await page.evaluate(() => {
      const stored = window.localStorage.getItem("waygate-auth");
      const parsed = stored ? (JSON.parse(stored) as { state?: { token?: string } }) : null;
      return parsed?.state?.token ?? null;
    });
    expect(token).not.toBeNull();

    const created = await request.post("http://127.0.0.1:8000/api/v1/servers", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        host: "10.99.99.1",
        port: 7743,
        name: "e2e-awg-client-host",
        token: "fake",
        region: "EU",
      },
    });
    expect(created.status()).toBe(201);

    // Перезагружаем чтобы новый сервер появился в sidebar и сделался активным.
    await page.reload();
    await page.locator(".sb-item .name").filter({ hasText: "e2e-awg-client-host" }).click();

    // Вкладка Tunnels.
    await page.getByRole("button", { name: /Tunnels/i }).click();

    // Перехватываем POST на агентский /v1/clients — реального docker'а нет.
    // Control-plane упадёт с 502 при попытке достучаться до фейкового агента,
    // поэтому проверяем что запрос ушёл и отображается ошибка.
    let postSeen = false;
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/clients")) {
        postSeen = true;
      }
    });

    await page.getByRole("button", { name: /Добавить клиента/i }).click();
    await expect(page.locator(".modal").getByText("Добавить AmneziaWG-клиента")).toBeVisible();

    // Заполняем форму
    await page.getByPlaceholder("us-fast").fill("us-test");
    // Textarea — единственная в модалке
    await page.locator(".modal .textarea").fill(VALID_CONFIG);

    // Превью валидного .conf — без ошибок
    await expect(page.getByText(/Address:/i)).toBeVisible();

    // Кликаем submit — реальный onbording в test'е не отработает (фейковый
    // host), но запрос на бэк уйдёт.
    await page.getByRole("button", { name: /Развернуть клиента/i }).click();

    // Дать запросу долететь
    await page.waitForTimeout(500);
    expect(postSeen).toBe(true);
  });
});

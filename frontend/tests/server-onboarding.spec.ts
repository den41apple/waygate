import { expect, test } from "@playwright/test";

import { login } from "./helpers";

test.describe("Онбординг сервера", () => {
  // SSH-fail при не-существующем хосте может занять время DNS-таймаута,
  // плюс apt/openssl шаги могут не успеть отвалиться сразу — даём 120с.
  test.setTimeout(120_000);

  test("показывает SSE-лог и кнопку Повторить при ошибке SSH", async ({ page }) => {
    await login(page);

    await page.getByRole("button", { name: "Добавить сервер" }).click();
    await expect(page.getByText("Новый сервер")).toBeVisible();

    // Заведомо нерезолвящийся хост — DNS-фейл случается за миллисекунды.
    await page.locator('input[placeholder="10.0.0.1"]').fill("waygate-e2e-nonexistent.invalid");
    await page.locator("text=Имя сервера").locator("..").locator("input").fill("e2e-fake");
    const passwordInput = page.locator("text=Пароль").locator("..").locator('input[type="password"]');
    await passwordInput.fill("not-real");

    await page.getByRole("button", { name: /Запустить онбординг/ }).click();

    // Ждём появления лога установки.
    await expect(page.getByText("Онбординг сервера")).toBeVisible({ timeout: 10_000 });

    // Дожидаемся error-события (SSH-подключение упадёт)
    await expect(page.locator(".log-line .icon.fail").first()).toBeVisible({ timeout: 90_000 });

    // НЕ должны автоматически уйти на «Готово»-экран. Кнопка «Повторить» видна.
    await expect(page.getByText("Сервер готов")).toBeHidden();
    await expect(page.getByRole("button", { name: /Повторить/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Назад/ })).toBeVisible();

    // Закрываем модалку
    await page.getByText("Отмена").click();
    await expect(page.getByText("Новый сервер")).toBeHidden();
  });
});

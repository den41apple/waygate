import { expect, test } from "@playwright/test";

import { E2E_ADMIN } from "../playwright.config";

test.describe("Auth", () => {
  test.beforeEach(async ({ context }) => {
    // Чистим storage между тестами — у каждого теста свежий старт.
    await context.clearCookies();
  });

  test("показывает форму логина без сессии", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible();
    await expect(page.locator('input[autocomplete="current-password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toContainText("Войти");
  });

  test("отклоняет неверный пароль", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[autocomplete="username"]').fill(E2E_ADMIN.username);
    await page.locator('input[autocomplete="current-password"]').fill("wrong-password");
    await page.locator('button[type="submit"]').click();

    await expect(page.getByText("Неверный логин или пароль")).toBeVisible();
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible();
  });

  test("пускает с правильными кредами и показывает дашборд", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[autocomplete="username"]').fill(E2E_ADMIN.username);
    await page.locator('input[autocomplete="current-password"]').fill(E2E_ADMIN.password);
    await page.locator('button[type="submit"]').click();

    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
    // в sidebar есть label «Серверы», а в основной зоне ещё хинт со словом
    // «Серверы ещё не добавлены...» — берём именно sidebar-label, exact-match.
    await expect(page.locator(".sb-section-label").getByText("Серверы")).toBeVisible();
  });

  test("logout возвращает на login", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[autocomplete="username"]').fill(E2E_ADMIN.username);
    await page.locator('input[autocomplete="current-password"]').fill(E2E_ADMIN.password);
    await page.locator('button[type="submit"]').click();
    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });

    await page.getByTitle("Выйти из панели").click();
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible({ timeout: 10_000 });
  });
});

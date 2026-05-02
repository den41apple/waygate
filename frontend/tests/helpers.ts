import { type Page, expect } from "@playwright/test";

import { E2E_ADMIN } from "../playwright.config";

/**
 * Логинится через UI как тестовый админ. После — на дашборде.
 *
 * Стоит в начале каждого теста, требующего auth. Изоляция простая — мы не
 * используем storageState, потому что в localStorage лежит ещё ui-state, и
 * между тестами нам важна чистая модалка/сайдбар.
 */
export async function login(page: Page): Promise<void> {
  await page.goto("/");
  await page.locator('input[autocomplete="username"]').fill(E2E_ADMIN.username);
  await page.locator('input[autocomplete="current-password"]').fill(E2E_ADMIN.password);
  await page.locator('button[type="submit"]').click();
  await expect(page.locator(".sidebar")).toBeVisible({ timeout: 10_000 });
}

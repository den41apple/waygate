import { type APIRequestContext, type Page, expect } from "@playwright/test";

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

/** Достаёт session-JWT из localStorage после логина. */
export async function getToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const stored = window.localStorage.getItem("waygate-auth");
    if (!stored) return null;
    const parsed = JSON.parse(stored) as { state?: { token?: string } };
    return parsed.state?.token ?? null;
  });
  expect(token).not.toBeNull();
  return token as string;
}

/** Создаёт server через REST (Bearer-token берётся из localStorage). */
export async function createServerViaRest(
  request: APIRequestContext,
  token: string,
  overrides: { host?: string; name?: string; region?: string } = {},
): Promise<{ id: number; host: string; name: string }> {
  const response = await request.post("http://127.0.0.1:8000/api/v1/servers", {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      host: overrides.host ?? "10.0.42.1",
      port: 7743,
      name: overrides.name ?? "e2e-srv",
      token: "agent-token-fake",
      region: overrides.region ?? "EU",
    },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

export async function deleteServerViaRest(
  request: APIRequestContext,
  token: string,
  serverId: number,
): Promise<void> {
  const response = await request.delete(
    `http://127.0.0.1:8000/api/v1/servers/${serverId}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect([204, 404]).toContain(response.status());
}

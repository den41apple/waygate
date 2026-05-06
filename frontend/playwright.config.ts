import { defineConfig, devices } from "@playwright/test";

const ADMIN_USERNAME = "admin";
const ADMIN_PASSWORD = "admin-e2e-pass-123";
// Fixed e2e-only SECRET_KEY (хеш-стиль 64 hex). Без него server.config.py
// падает на старте с RuntimeError (audit-batch 2026-05-05). Безопасно для
// тестов: e2e_test.db wipается при каждом запуске.
const TEST_SECRET_KEY =
  "e2e0123456789abcdef0123456789abcdef0123456789abcdef0123456789ab";

const BACKEND_URL = "http://127.0.0.1:8000";
const FRONTEND_URL = "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false, // у нас одна тестовая БД на все тесты
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],

  use: {
    baseURL: FRONTEND_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    {
      // Backend: чистим e2e-БД, мигрируем, стартуем granian с тестовым админом.
      command: [
        "cd ../backend",
        "&& rm -f server/e2e_test.db",
        `&& (cd server && SECRET_KEY='${TEST_SECRET_KEY}' DATABASE_URL='sqlite+aiosqlite:///e2e_test.db' uv run alembic upgrade head)`,
        `&& SECRET_KEY='${TEST_SECRET_KEY}' DATABASE_URL='sqlite+aiosqlite:///server/e2e_test.db' WAYGATE_ADMIN_USER='${ADMIN_USERNAME}' WAYGATE_ADMIN_PASSWORD='${ADMIN_PASSWORD}' uv run granian --interface asgi --http 1 server.main:app --host 127.0.0.1 --port 8000`,
      ].join(" "),
      url: `${BACKEND_URL}/openapi.json`,
      timeout: 90_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // Frontend через vite dev (а не preview): preview-proxy с SSE капризный,
      // dev-server ходит через тот же http-proxy-middleware но с правильным
      // флагом для streaming. Билд для тестов не требуется.
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      url: FRONTEND_URL,
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});

// Экспорт констант на случай если тестам понадобятся.
export const E2E_ADMIN = { username: ADMIN_USERNAME, password: ADMIN_PASSWORD };

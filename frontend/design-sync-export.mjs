// Конвертер Storybook → preview-карточки для claude.ai/design (фолбэк штатного
// /design-sync, который user-triggered). Рендерит каждую story в headless-chromium,
// инлайнит CSS, оборачивает @dsCard-маркером. Также служит runtime-проверкой рендера.
//
// Запуск (из frontend/): node design-sync-export.mjs
// Требует: предварительно `npm run build-storybook`.

import http from "node:http";
import { readFileSync, writeFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const FRONTEND = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(FRONTEND, "storybook-static");
const OUT = path.join(FRONTEND, "..", ".design-bundle", "ds");
const PORT = 6029;

if (!existsSync(path.join(STATIC, "index.json"))) {
  console.error("Нет storybook-static/index.json — сначала `npm run build-storybook`");
  process.exit(1);
}

const MIME = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".json": "application/json",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

function serve() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let p = decodeURIComponent((req.url || "/").split("?")[0]);
      if (p === "/") p = "/index.html";
      const file = path.join(STATIC, p);
      try {
        const data = readFileSync(file);
        res.setHeader("Content-Type", MIME[path.extname(file)] || "application/octet-stream");
        res.end(data);
      } catch {
        res.statusCode = 404;
        res.end("not found");
      }
    });
    server.on("error", reject);
    server.listen(PORT, "127.0.0.1", () => resolve(server));
  });
}

const safeName = (id) => id.replace(/[^a-z0-9-]+/gi, "-");

async function main() {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });

  const index = JSON.parse(readFileSync(path.join(STATIC, "index.json"), "utf8"));
  const entries = Object.values(index.entries).filter((e) => e.type === "story");
  console.log(`stories: ${entries.length}`);

  const server = await serve();
  console.log(`server up on :${PORT}`);
  const browser = await chromium.launch();
  console.log("browser up");
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  page.setDefaultTimeout(8000);
  page.setDefaultNavigationTimeout(15000);

  const manifest = [];
  let bad = 0;

  for (const entry of entries) {
    const url = `http://localhost:${PORT}/iframe.html?id=${entry.id}&viewMode=story`;
    try {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await page.waitForSelector("#storybook-root *", { timeout: 6000 }).catch(() => {});
      await page.waitForTimeout(350);
    } catch (err) {
      console.log(`  ⚠ goto failed: ${entry.title} / ${entry.name}: ${err.message}`);
    }

    const result = await page.evaluate(() => {
      let css = "";
      for (const sheet of Array.from(document.styleSheets)) {
        try {
          for (const rule of Array.from(sheet.cssRules)) css += rule.cssText + "\n";
        } catch {
          /* cross-origin (google fonts) — пропускаем */
        }
      }
      const root = document.querySelector("#storybook-root") || document.body;
      const errorEl = document.querySelector(".sb-errordisplay, #error-message");
      return {
        html: root.innerHTML,
        css,
        textLen: (root.textContent || "").trim().length,
        childCount: root.childElementCount,
        error: errorEl ? errorEl.textContent : null,
      };
    });

    const group = entry.title.split("/")[0];
    const sub = entry.title.split("/").slice(1).join("/");
    const cardName = entry.name === "Default" ? sub : `${sub} · ${entry.name}`;
    const ok = result.childCount > 0 && !result.error;
    if (!ok) {
      bad++;
      console.log(`  EMPTY/ERROR: ${entry.title} / ${entry.name} (children=${result.childCount}) ${result.error ?? ""}`);
    } else {
      console.log(`  ok: ${entry.title} / ${entry.name} (children=${result.childCount}, text=${result.textLen})`);
    }

    const rel = `${group.toLowerCase()}/${safeName(entry.id)}.html`;
    const file = path.join(OUT, rel);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(
      file,
      `<!-- @dsCard group="${group}" -->
<!DOCTYPE html>
<html data-theme="dark"><head><meta charset="utf-8"><style>
${result.css}
html,body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ui)}
</style></head><body>
${result.html}
</body></html>
`,
      "utf8",
    );
    manifest.push({ path: rel, group, name: cardName, id: entry.id, ok });
  }

  writeFileSync(path.join(OUT, "_manifest.json"), JSON.stringify(manifest, null, 2));
  await browser.close();
  server.close();

  console.log(`\nwrote ${manifest.length} cards → ${OUT}`);
  console.log(`bad (empty/error): ${bad}`);
}

main();

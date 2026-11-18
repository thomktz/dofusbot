// Smoke test against the running dev server: node scripts/e2e.cjs [baseUrl]
// Uses the globally available `playwright` package. Saves docs/screenshot.png.
const { chromium } = require("playwright");
const path = require("path");

const BASE = process.argv[2] || "http://localhost:5173";
const W = 1728, H = 1117;

function assert(cond, msg) { if (!cond) { throw new Error("ASSERT: " + msg); } console.log("  ok -", msg); }

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(`${BASE}/?mock=1`);
  await page.waitForSelector('[data-testid="agent-state"]');
  await page.waitForTimeout(800);

  const scroll = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight,
    cw: document.documentElement.clientWidth, ch: document.documentElement.clientHeight,
  }));
  assert(scroll.sw <= scroll.cw && scroll.sh <= scroll.ch, `no page scroll (${scroll.sw}x${scroll.sh} in ${scroll.cw}x${scroll.ch})`);

  const rect = await page.$eval('[data-testid="slot"]', (el) => el.getBoundingClientRect().toJSON());
  assert(rect.x === 16 && rect.y === 16 && rect.width === 1280 && rect.height === 800, `slot rect ${JSON.stringify(rect)}`);

  const markers = await page.$$eval('[data-testid="slot"] .mk', (els) => els.map((e) => {
    const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height, getComputedStyle(e).backgroundColor];
  }));
  assert(JSON.stringify(markers) === JSON.stringify([
    [16, 16, 12, 12, "rgb(255, 0, 255)"], [1284, 16, 12, 12, "rgb(0, 255, 255)"],
    [16, 804, 12, 12, "rgb(255, 255, 0)"], [1284, 804, 12, 12, "rgb(0, 255, 0)"],
  ]), "corner markers placed and coloured per §3");

  const outline = await page.$eval('[data-testid="slot"]', (el) => getComputedStyle(el).outlineWidth + " " + getComputedStyle(el).outlineStyle);
  assert(outline === "4px solid", "4px solid border outside slot");

  // nothing else overlaps the slot or its border
  const overlaps = await page.evaluate(() => {
    const s = { x: 12, y: 12, r: 1300, b: 820 };
    return [...document.querySelectorAll(".panel, .hdr")].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.left < s.r && r.right > s.x && r.top < s.b && r.bottom > s.y;
    }).length;
  });
  assert(overlaps === 0, "no panel overlaps the slot + border");

  const est = await page.textContent('[data-testid="slot-estimate"]');
  assert(/16, 16\s+1280×800/.test(est), `estimate shown: "${est.trim()}"`);
  assert(await page.isVisible('[data-testid="mock-badge"]'), "MOCK badge visible");

  const state = () => page.textContent('[data-testid="agent-state"]');
  const mode = () => page.textContent('[data-testid="agent-mode"]');

  await page.click('[data-testid="btn-pause"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-state"]').textContent === "paused", null, { timeout: 3000 });
  assert((await state()) === "paused", "Pause → paused");

  await page.click('[data-testid="btn-resume"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-state"]').textContent === "running", null, { timeout: 3000 });
  assert((await state()) === "running", "Resume → running");

  await page.click('[data-testid="mode-auto"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-mode"]').textContent === "auto", null, { timeout: 3000 });
  assert((await mode()) === "auto", "mode → auto");
  await page.click('[data-testid="mode-assist"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-mode"]').textContent === "assist", null, { timeout: 3000 });
  assert((await mode()) === "assist", "mode → assist");

  await page.fill('[data-testid="goal-input"]', "Farmer des Tofus à [4,-18]");
  await page.click('[data-testid="goal-submit"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-goal"]').textContent.includes("Tofus"), null, { timeout: 3000 });
  assert(true, "goal updated");

  await page.click('[data-testid="btn-kill"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-state"]').textContent === "stopped", null, { timeout: 3000 });
  assert((await state()) === "stopped", "KILL → stopped");

  // step while stopped → ack error toast
  await page.click('[data-testid="btn-resume"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-state"]').textContent === "running", null, { timeout: 3000 });
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelector('[data-testid="agent-state"]').textContent === "stopped", null, { timeout: 3000 });
  assert(true, "Escape → stopped");
  // pause while killed is refused by the backend; the UI disables the button, so send it raw
  await page.evaluate(() => window.__dofus.request("steer.pause", {}));
  await page.waitForTimeout(300);
  const toasts = await page.textContent('[data-testid="toasts"]');
  assert(/failed/.test(toasts), `ack failure shown as toast: "${toasts.trim().slice(0, 80)}"`);

  // back to a nice state for the screenshot: running, assist, with a fight on screen
  await page.click('[data-testid="btn-resume"]');
  await page.click('[data-testid="mode-assist"]');
  await page.evaluate(() => document.querySelectorAll(".toast").forEach((t) => t.click()));
  const want = process.env.SHOT_SCREEN || "fight";
  await page.waitForFunction((w) => document.querySelector('[data-testid="game-screen"]')?.textContent === w, want, { timeout: 60000 });
  await page.waitForTimeout(Number(process.env.SHOT_DELAY || 7000));
  await page.evaluate(() => document.querySelectorAll(".toast").forEach((t) => t.click()));
  await page.mouse.move(1500, 200);
  await page.waitForTimeout(300);
  const out = process.env.SHOT || path.join(__dirname, "..", "docs", "screenshot.png");
  await page.screenshot({ path: out });
  console.log("  screenshot →", out);

  assert(errors.length === 0, `no console errors ${errors.join(" | ")}`);
  await browser.close();
  console.log("E2E PASS");
})().catch((e) => { console.error(e); process.exit(1); });

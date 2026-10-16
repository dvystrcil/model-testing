// Tier 3 of model-testing#126: does the REAL OWUI chat paint a rendering tool's output?
//
// Tiers 1-2 (bin/owui-visual-tools-eval.py) prove the model drives the tool and
// the tool's own code renders. Neither proves OWUI's frontend shows it: on
// 2026-10-16 that was the open question for OpenUI, whose HTMLResponse rides
// the per-tool-call embed path the visualizer had abandoned.
//
//   npm ci
//   OWUI_TOKEN_FILE=~/.owui-e2e-token TOOL_NAME="OpenUI - Generative UI" \
//     node chat.js qwen3.8:27B "Use the render_openui tool to show me a bar chart: Jan 120, Feb 95" out/openui-q38
//
// OWUI_TOKEN_FILE holds a session JWT for a TEST user (never an admin's): the
// token goes into localStorage like a real login. The file is read, never
// echoed. The tool is selected through the UI's Integrations > Tools menu,
// because the ?tools= URL parameter is filtered against a tool list that has
// not loaded yet and silently drops the tool. Name the tool in the prompt:
// left to choose, qwen3.8 picked the built-in code interpreter instead.
//
// Writes <out>.png and <out>.json (iframe count, the text inside each frame,
// the chat URL). The chat is left in the test user's history; delete it
// afterwards (DELETE /api/v1/chats/<id>).
//
// Last line: E2E-COMPLETE model=<m> iframes=<n> done=<bool>, or E2E-ERROR.
const { chromium } = require('playwright-core');
const fs = require('fs');
const os = require('os');
const path = require('path');

const [model, prompt, out] = process.argv.slice(2);
if (!model || !prompt || !out) { console.log('usage: node chat.js <model> <prompt> <out-prefix>'); process.exit(2); }
const BASE = process.env.OWUI_BASE || 'https://open-webui.sirddail.net';
const tokenFile = (process.env.OWUI_TOKEN_FILE || '').replace(/^~/, os.homedir());
if (!tokenFile || !fs.existsSync(tokenFile)) { console.log('E2E-ERROR set OWUI_TOKEN_FILE to a test user session token file'); process.exit(2); }
const token = fs.readFileSync(tokenFile, 'utf8').trim();

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const base = path.join(os.homedir(), '.cache/ms-playwright');
  const dirs = fs.existsSync(base) ? fs.readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort() : [];
  if (!dirs.length) return undefined;
  return path.join(base, dirs[dirs.length - 1], 'chrome-linux64/chrome');
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const browser = await chromium.launch({ executablePath: findChrome() });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1400 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  await page.addInitScript((t) => { localStorage.setItem('token', t); }, token);
  await page.goto(`${BASE}/?models=${encodeURIComponent(model)}&q=${encodeURIComponent(prompt)}`, { waitUntil: 'domcontentloaded' });

  const t0 = Date.now();
  const input = page.locator('#chat-input');
  await input.waitFor({ timeout: 60000 });
  await page.waitForTimeout(2500);
  if (process.env.TOOL_NAME) {
    await page.locator('#integration-menu-button').click(); await page.waitForTimeout(800);
    await page.getByText('Tools', { exact: false }).first().click(); await page.waitForTimeout(800);
    await page.getByText(process.env.TOOL_NAME, { exact: true }).first().click(); await page.waitForTimeout(600);
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
  }
  const send = page.locator('#send-message-button');
  if (await send.count()) await send.click(); else { await input.click(); await page.keyboard.press('Enter'); }
  for (let i = 0; i < 40 && !/\/c\//.test(page.url()); i++) await page.waitForTimeout(1500);
  if (!/\/c\//.test(page.url())) {
    await page.screenshot({ path: `${out}-stuck.png` });
    throw new Error('chat never started: ' + (await page.locator('body').innerText()).slice(-300).replace(/\s+/g, ' '));
  }

  // Finished when the response's action row (Regenerate) appears.
  let done = false;
  while (Date.now() - t0 < 600000) {
    await page.waitForTimeout(4000);
    if (await page.locator('.regenerate-response-button, button[aria-label="Regenerate"]').count()) { done = true; break; }
  }
  await page.waitForTimeout(8000); // let iframes load their bundles

  const frames = [];
  for (const f of page.frames()) {
    if (f === page.mainFrame()) continue;
    let text = '';
    try { text = (await f.evaluate(() => (document.body ? document.body.innerText : ''))).slice(0, 400); }
    catch (e) { text = 'ERR ' + e.message.slice(0, 80); }
    frames.push({ url: f.url().slice(0, 80), text });
  }
  const iframes = await page.locator('[id^="message-"] iframe').count();
  await page.screenshot({ path: `${out}.png`, fullPage: true });
  fs.writeFileSync(`${out}.json`, JSON.stringify({ model, tool: process.env.TOOL_NAME || null, prompt, done,
    secs: Math.round((Date.now() - t0) / 1000), chatUrl: page.url(), iframes, frames, consoleErrors: errors.slice(0, 10) }, null, 1));
  console.log(`E2E-COMPLETE model=${model} iframes=${iframes} done=${done}`);
  await browser.close();
})().catch((e) => { console.log('E2E-ERROR ' + e.message); process.exit(1); });

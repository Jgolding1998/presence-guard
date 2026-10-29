"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {chromium} = require(process.env.PRESENCE_PLAYWRIGHT || "playwright");

const root = path.resolve(__dirname, "..");
const captureDir = process.env.PRESENCE_CAPTURE_DIR || path.join(root, "test-results");
let checks = 0;
const check = (value, message) => {assert.ok(value, message); checks += 1;};
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const server = spawn(process.env.PRESENCE_PYTHON || "python", ["-m", "presence_guard", "--demo", "--port", "0"], {cwd: root, stdio: ["ignore", "pipe", "pipe"], windowsHide: true});
  let output = "";
  let errors = "";
  server.stdout.on("data", (chunk) => {output += chunk.toString();});
  server.stderr.on("data", (chunk) => {errors += chunk.toString();});
  let browser;
  try {
    for (let count = 0; count < 100 && !output.includes("http://127.0.0.1:"); count += 1) await delay(50);
    const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
    check(Boolean(match), "Demo server started");
    const origin = match[0];
    browser = await chromium.launch({headless: true, ...(process.env.PRESENCE_CHROME ? {executablePath: process.env.PRESENCE_CHROME} : {})});
    const context = await browser.newContext({viewport: {width: 1366, height: 1000}, reducedMotion: "reduce"});
    const pageErrors = [];
    const external = [];
    await context.route("**/*", async (route) => {
      if (!route.request().url().startsWith(origin + "/")) {
        external.push(route.request().url());
        await route.abort();
      } else {
        await route.continue();
      }
    });
    context.on("page", (page) => page.on("pageerror", (error) => pageErrors.push(error.message)));
    const page = await context.newPage();
    const response = await page.goto(origin);
    check(response.status() === 200, "Dashboard responds");
    check(response.headers()["content-security-policy"].includes("default-src 'none'"), "CSP is present");
    await page.waitForFunction(() => document.querySelector("#source-badge").textContent === "DEMO / NO CAMERA");
    check(await page.locator("#active-pill").textContent() === "Inactive", "Inactive on boot");
    check(await page.locator("#permission-state").textContent() === "Disarmed", "Disarmed on boot");
    check(await page.locator("#start").isEnabled(), "Start available");
    await page.locator("#start").click();
    await page.waitForFunction(() => document.querySelector("#active-pill").textContent === "Active");
    check(await page.locator("#camera-state").textContent() === "Synthetic", "Synthetic camera is honest");
    check(await page.locator("#people-count").textContent() === "1", "Synthetic presence displayed");
    check(await page.locator("#mode").isDisabled(), "Cannot switch active modes");
    check(await page.locator("#start").isDisabled(), "Cannot double start");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Desktop has no horizontal overflow");
    fs.mkdirSync(captureDir, {recursive: true});
    await page.screenshot({path: path.join(captureDir, "dashboard.png"), fullPage: true});
    await page.locator("#stop").click();
    await page.waitForFunction(() => document.querySelector("#active-pill").textContent === "Inactive");
    check(await page.locator("#permission-state").textContent() === "Disarmed", "Stop disarms");
    const flicker = await page.evaluate(() => new Promise((resolve) => {
      let seen = false;
      const observer = new MutationObserver(() => {seen ||= document.querySelector("#active-pill").textContent === "Active";});
      observer.observe(document.querySelector("#active-pill"), {childList: true, characterData: true, subtree: true});
      setTimeout(() => {observer.disconnect(); resolve(seen);}, 1500);
    }));
    check(!flicker, "Stopped state is not overwritten by an older poll");
    await page.locator("#mode").selectOption("privacy");
    check((await page.locator("#arming-note").textContent()).includes("cannot lock"), "Demo arming is clear");
    await page.locator("#start").click();
    await page.waitForFunction(() => document.querySelector("#permission-state").textContent === "Simulated");
    await page.locator('[data-people="0"]').click();
    await page.waitForFunction(() => document.querySelector("#people-count").textContent === "0");
    await delay(900);
    check((await page.locator("#absence-time").textContent()).startsWith("0.0s") === false, "Absence timer advances");
    await page.locator('[data-people="1"]').click();
    await page.waitForFunction(() => document.querySelector("#absence-time").textContent.startsWith("0.0s"));
    check(await page.locator("#people-count").textContent() === "1", "Re-entry resets absence");
    await page.locator("#fault").click();
    await page.waitForFunction(() => document.querySelector("#camera-state").textContent === "Fault");
    check(await page.locator("#people-count").textContent() === "Unknown", "Fault does not claim empty desk");
    check((await page.locator("#absence-note").textContent()).includes("not proof of absence"), "Fault explains uncertainty");
    check((await page.locator("#absence-time").textContent()).startsWith("Unknown"), "Absence timer is not shown during a fault");
    await page.locator("#stop").click();
    await page.waitForFunction(() => document.querySelector("#mode").disabled === false);
    await page.locator("#mode").selectOption("boss");
    await page.locator("#start").click();
    await page.waitForFunction(() => document.querySelector("#active-pill").textContent === "Active");
    await page.locator('[data-people="1"]').click();
    await page.locator('[data-people="2"]').click();
    await page.waitForFunction(() => document.querySelector("#last-action").textContent.includes("Simulated: synthetic Boss Alert workbook"));
    check(await page.locator("#permission-state").textContent() === "Simulated", "Boss Alert stays simulated");
    check((await page.locator("#events").textContent()).includes("synthetic Boss Alert"), "Action event visible");
    check(!fs.existsSync(path.join(root, "runtime")), "Demo creates no workbook directory");
    await page.locator("#stop").click();
    await page.waitForFunction(() => document.querySelector("#mode").disabled === false);
    await page.setViewportSize({width: 390, height: 844});
    await page.locator("#mode").selectOption("observe");
    await page.locator('[data-people="1"]').click();
    await page.locator("#start").click();
    await page.waitForFunction(() => document.querySelector("#active-pill").textContent === "Active");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Phone has no horizontal overflow");
    check(await page.locator("#stop").isEnabled(), "Phone stop remains reachable");
    check(await page.locator("#stop").evaluate((node) => node.getBoundingClientRect().height >= 44), "Stop has a touch target");
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    check(await page.locator("#quick-stop").evaluate((node) => {const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight;}), "Phone stop stays visible while scrolling");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: path.join(captureDir, "dashboard-mobile.png"), fullPage: true});
    check(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0), "No browser storage");
    await page.locator("#quick-stop").click();
    await page.waitForFunction(() => document.querySelector("#active-pill").textContent === "Inactive");
    const fixturePage = await context.newPage();
    const armedFault = {
      ...(await (await context.request.get(origin + "/api/status")).json()),
      demo: false, active: true, mode: "privacy", mode_label: "Privacy lock",
      allowed_modes: ["observe", "privacy"], camera: "fault", camera_closing: true,
      people: null, lock_armed: true, sheet_armed: false,
      events: ['<img src=x onerror="window.injected=true">']
    };
    let fixtureState = armedFault;
    await fixturePage.route("**/api/status", (route) => route.fulfill({json: fixtureState}));
    await fixturePage.route("**/api/stop", (route) => {
      fixtureState = {...armedFault, active: false, lock_armed: false};
      return route.fulfill({json: fixtureState});
    });
    await fixturePage.goto(origin);
    await fixturePage.waitForFunction(() => document.querySelector("#permission-state").textContent === "Lock armed");
    check(await fixturePage.locator("#active-pill").textContent() === "Active", "Release failure does not claim an armed session is inactive");
    check((await fixturePage.locator("#session-label").textContent()).includes("Privacy lock"), "Armed fault keeps the active session label");
    check(await fixturePage.locator("#status-heading").textContent() === "Camera unavailable.", "Armed release failure displays the camera fault");
    check(!(await fixturePage.locator("#status-detail").textContent()).includes("disarmed"), "Armed release failure never claims actions are disarmed");
    check(await fixturePage.locator("#camera-state").textContent() === "Fault", "Active failed camera is reported as a fault");
    check(await fixturePage.locator("#stop").isEnabled(), "Stop remains available for armed release failure");
    check(await fixturePage.locator("#events img").count() === 0, "Event markup is rendered as text");
    check(await fixturePage.evaluate(() => window.injected === undefined), "Event markup cannot execute");
    await fixturePage.screenshot({path: path.join(captureDir, "camera-fault-armed.png"), fullPage: true});
    await fixturePage.locator("#stop").click();
    await fixturePage.waitForFunction(() => document.querySelector("#active-pill").textContent === "Closing");
    check(await fixturePage.locator("#permission-state").textContent() === "Disarmed", "Stopped release failure reports disarmed permissions");
    check((await fixturePage.locator("#status-detail").textContent()).startsWith("Actions are disarmed."), "Closing text is reserved for a stopped session");
    await fixturePage.screenshot({path: path.join(captureDir, "camera-cleanup.png"), fullPage: true});
    await fixturePage.close();
    server.kill();
    await page.waitForFunction(() => document.querySelector("#connection-error").hidden === false);
    check(await page.locator("#start").isDisabled(), "Disconnected controls are disabled");
    check((await page.locator("#connection-error").textContent()).includes("may still be running"), "Disconnect does not claim disarm");
    check(pageErrors.length === 0, "No browser script exceptions");
    check(external.length === 0, "No external resources or telemetry");
    check(errors === "", "No server traceback or request logs");
    console.log(JSON.stringify({checks, pageErrors: pageErrors.length, externalRequests: external.length, screenshots: 4}));
    await context.close();
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}

main().catch((error) => {console.error(error); process.exitCode = 1;});

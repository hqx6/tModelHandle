// Run with the service active. Requires an existing Playwright + Chrome installation.
const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1140 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8080');
    await page.locator('#max-triangles').fill('8');
    await page.getByRole('button', { name: '加载演示模型' }).click();
    await page.getByText('切片完成', { exact: true }).first().waitFor({ timeout: 15000 });
    await page.getByRole('button', { name: '◇ 主楼' }).click();
    assert.match(await page.locator('#properties').innerText(), /办公/);
    // Verify first-click selection, rotation and mode-switch behavior.
    await page.getByRole('button', { name: '◇ 附楼' }).click();
    assert.match(await page.locator('#properties').innerText(), /展览/);
    await page.getByRole('button', { name: '◇ 主楼' }).click();
    const canvas = await page.locator('#canvas').boundingBox();
    await page.mouse.move(canvas.x + 200, canvas.y + 150);
    await page.mouse.down(); await page.mouse.move(canvas.x + 230, canvas.y + 155); await page.mouse.up();
    await page.locator('[name=mode][value=octree]').check();
    assert(await page.locator('#pixel-label').isVisible());
    await page.locator('[name=mode][value=small]').check();
    assert(!(await page.locator('#pixel-label').isVisible()));
    const tasks = await page.evaluate(async () => await (await fetch('/api/tasks')).json());
    const task = tasks.at(-1);
    assert.equal(task.status, 'completed'); assert.equal(task.report.triangles, 24);
    const zip = await page.request.get(`http://127.0.0.1:8080/artifacts/${task.id}/output.zip`);
    assert(zip.ok()); assert.equal((await zip.body()).subarray(0,2).toString(),'PK');
    fs.mkdirSync('verification', { recursive: true });
    await page.screenshot({ path: 'verification/model-workshop.png', fullPage: true });
    fs.writeFileSync('verification/browser-result.json', JSON.stringify({ errors, task, selectedProperties: await page.locator('#properties').innerText() }, null, 2));
    assert.deepEqual(errors, []);
    // Validate actual B3DM rendering and Batch Table properties through Cesium,
    // rather than merely accepting a tileset.json response or tilesLoaded=true.
    const globe = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const globeErrors = [];
    globe.on('pageerror', error => globeErrors.push(error.message));
    await globe.goto(`http://127.0.0.1:8080/cesium.html?task=${task.id}`, { waitUntil: 'networkidle', timeout: 60000 });
    await globe.waitForFunction(() => window.tileset?.tilesLoaded && window.tileset?.statistics.numberOfTrianglesSelected === 24 && window.tileset?.statistics.numberOfCommands > 0, { timeout: 30000 });
    const result = await globe.evaluate(() => {
      const unique = new Map();
      function visit(tile) {
        const content = tile.content;
        for (let i=0;content && i<content.featuresLength;i++) {
          const f = content.getFeature(i);
          unique.set(f.getProperty('id'),{id:f.getProperty('id'),name:f.getProperty('name'),usage:f.getProperty('用途')});
        }
        tile.children.forEach(visit);
      }
      visit(window.tileset.root);
      const features = [...unique.values()];
      return { version: Cesium.VERSION, features, tilesLoaded: window.tileset.tilesLoaded, commands: window.tileset.statistics.numberOfCommands,
               triangles: window.tileset.statistics.numberOfTrianglesSelected };
    });
    assert.equal(result.features.length, 2);
    assert.deepEqual(result.features.map(f => f.usage).sort(), ['办公', '展览']);
    assert(result.commands > 0);
    assert.deepEqual(globeErrors, []);
    await globe.screenshot({ path: 'verification/cesium-globe.png' });
    fs.writeFileSync('verification/cesium-result.json', JSON.stringify({ ...result, errors: globeErrors }, null, 2));
    console.log(JSON.stringify({ task: task.id, ...result, errors: globeErrors }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });

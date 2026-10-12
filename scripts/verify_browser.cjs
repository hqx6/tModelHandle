// Run with the service active. Requires an existing Playwright + Chrome installation.
let chromium;
try { ({ chromium } = require('playwright')); } catch { ({ chromium } = require('playwright-core')); }
const fs = require('fs');
const assert = require('node:assert/strict');
const baseURL = process.env.MODEL_TILER_URL || 'http://127.0.0.1:8080';
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1140 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL);
    await page.locator('#max-triangles').fill('8');
    await page.locator('#draco').check();
    assert(await page.locator('#draco-options').isVisible());
    await page.locator('#draco-level').fill('5');
    await page.locator('#draco-bits').fill('16');
    await page.locator('[name=mode][value=simplify]').check();
    assert(await page.locator('#lod-options').isVisible());
    await page.locator('[name=mode][value=small]').check();
    assert(!(await page.locator('#lod-options').isVisible()));
    await page.locator('#unlit').check();
    await page.locator('#alpha-mode').selectOption('MASK');
    assert(await page.locator('#alpha-cutoff-label').isVisible());
    await page.locator('#alpha-cutoff').fill('0.3');
    await page.locator('#origin').selectOption('center');
    await page.locator('#property-mode').selectOption('selected');
    await page.locator('#property-fields').fill('用途');
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
    assert.equal(task.report.options.unlit,true);
    assert.equal(task.report.options.draco,true);
    assert.equal(task.report.options.draco_level,5);
    assert.equal(task.report.options.draco_bits,16);
    assert.equal(task.report.options.alpha_mode,'MASK');
    assert.equal(task.report.options.origin,'center');
    assert.deepEqual(task.report.options.property_fields,['用途']);
    const zip = await page.request.get(`${baseURL}/artifacts/${task.id}/output.zip`);
    assert(zip.ok()); assert.equal((await zip.body()).subarray(0,2).toString(),'PK');
    await page.waitForFunction(id => [...document.getElementById('resource-task').options].some(o=>o.value===id),task.id);
    await page.locator('#resource-task').selectOption(task.id);
    await page.locator('#property-mode').selectOption('none');
    await page.locator('#max-tile-mb').fill('0.0045');
    await page.locator('[name=mode][value=octree]').check();
    const submission=page.waitForResponse(r=>r.url()===baseURL+'/api/tasks'&&r.request().method()==='POST');
    await page.locator('#submit').click();
    const created=await(await submission).json();
    assert(created.id,JSON.stringify(created));
    let reused;
    for(let i=0;i<100;i++){
      reused=await(await page.request.get(`${baseURL}/api/tasks/${created.id}`)).json();
      if(!['queued','running'].includes(reused.status))break;
      await page.waitForTimeout(100);
    }
    assert.equal(reused.status,'completed',JSON.stringify(reused));
    assert.equal(reused.resourceTask,task.id);
    assert.equal(reused.report.triangles,24);
    assert.deepEqual(reused.report.options.property_fields,[]);
    const tree=await(await page.request.get(`${baseURL}/artifacts/${reused.id}/output/scenetree.json`)).json();
    assert(tree.scenes.every(s=>s.children.every(c=>Object.keys(c.properties).length===0)));
    await page.waitForFunction(() => document.getElementById('task-title').textContent==='切片完成' && document.getElementById('properties').textContent==='');
    fs.mkdirSync('verification', { recursive: true });
    await page.screenshot({ path: 'verification/model-workshop.png', fullPage: true });
    fs.writeFileSync('verification/browser-result.json', JSON.stringify({ errors, task, reused, selectedProperties: await page.locator('#properties').innerText() }, null, 2));
    assert.deepEqual(errors, []);
    // Validate actual B3DM rendering and Batch Table properties through Cesium,
    // rather than merely accepting a tileset.json response or tilesLoaded=true.
    const globe = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const globeErrors = [];
    globe.on('pageerror', error => globeErrors.push(error.message));
    await globe.goto(`${baseURL}/cesium.html?task=${task.id}`, { waitUntil: 'networkidle', timeout: 60000 });
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

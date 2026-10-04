import { chromium } from 'playwright-core';
import fs from 'fs';
const jobs = JSON.parse(fs.readFileSync('jobs.json'));
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1600, height: 1100 } });
p.on('pageerror', e => console.log('ERR', e.message));
await p.goto('http://127.0.0.1:8766/index.html');
await p.waitForFunction('window.ready===true', null, { timeout: 120000 });
for (const j of jobs) {
  await p.evaluate(j => window.renderJob(j), j);
  await p.screenshot({ path: j.out });
  console.log('rendered', j.zone, j.view);
}
await b.close();

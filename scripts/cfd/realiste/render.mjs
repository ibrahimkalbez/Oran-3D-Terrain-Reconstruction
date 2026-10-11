// render a list of frame specs (frames.json) -> <out>/<name or index>.png
import { chromium } from 'playwright-core';
import fs from 'fs';
const [,, port, specFile, out, from = '0', to = '-1'] = process.argv;
const specs = JSON.parse(fs.readFileSync(specFile, 'utf8'));
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
p.on('pageerror', e => console.log('ERR', e.message));
await p.goto(`http://127.0.0.1:${port}/index.html`);
await p.waitForFunction('window.ready===true', null, { timeout: 900000 });
await p.waitForTimeout(3000);   // textures
fs.mkdirSync(out, { recursive: true });
const end = +to < 0 ? specs.length : Math.min(specs.length, +to); const t0 = Date.now();
for (let i = +from; i < end; i++) {
  await p.evaluate(s => window.frame(s), specs[i]);
  await p.screenshot({ path: `${out}/${specs[i].name || String(i).padStart(5, '0')}.png` });
  if (i % 25 === 0) console.log('frame', i, '/', end, ((Date.now() - t0) / 1000).toFixed(0) + 's');
}
await b.close();

import { chromium } from 'playwright-core';
import fs from 'fs';
const [,, from='0', to='-1', step='1', page='index.html'] = process.argv;
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1280, height: 720 } });
p.on('console', m => console.log('page:', m.text())); p.on('pageerror', e => console.log('ERR', e.message));
await p.goto('http://127.0.0.1:8765/' + page);
await p.waitForFunction('window.ready===true', null, { timeout: 300000 });
const N = await p.evaluate('window.N'); const end = (+to < 0) ? N : Math.min(N, +to);
const dir = process.env.FRAMES || 'frames'; fs.mkdirSync(dir, { recursive: true });
const t0 = Date.now();
for (let i = +from; i < end; i += +step) {
  await p.evaluate(i => window.renderFrame(i), i);
  await p.screenshot({ path: `${dir}/f${String(i).padStart(4, '0')}.png` });
  if (i % 24 === 0) console.log('frame', i, '/', N, ((Date.now() - t0) / 1000).toFixed(0) + 's');
}
await b.close();

// headless render of every shot of scene.json -> <out>/<name>.png (1920x1080)
import { chromium } from 'playwright-core';
const out = process.argv[2];
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
p.on('pageerror', e => console.log('ERR', e.message)); p.on('console', m => { if (m.type() === 'error') console.log('console', m.text()); });
await p.goto('http://127.0.0.1:8771/index.html');
await p.waitForFunction('window.ready===true', null, { timeout: 600000 });
const shots = await p.evaluate('window.S.shots.map(s=>s.name)');
for (let i = 0; i < shots.length; i++) {
  await p.evaluate(i => window.renderShot(i), i);
  await p.screenshot({ path: `${out}/${shots[i]}.png` });
  console.log('shot', shots[i]);
}
await b.close();

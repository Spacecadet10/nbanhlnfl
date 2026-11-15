// Dark/light theme contrast of the soccer Markets panel text (WCAG ratio against the first opaque background behind it).
import { writeFileSync } from 'node:fs';
const [port, out] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const res = [];
for (const theme of ['dark', 'light']) for (const width of [1280, 390]) {
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise((r) => ws.addEventListener('open', r));
  let id = 0; const pending = new Map();
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } });
  const send = (method, params = {}) => new Promise((resolve) => { const n = ++id; pending.set(n, (d) => resolve(d.result)); ws.send(JSON.stringify({ id: n, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: width < 600 ? 2 : 1, mobile: width < 600 });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('courtside-theme-choice', '${theme}'); } catch (e) {}` });
  await send('Page.navigate', { url: 'http://127.0.0.1:4191/?sport=soccer&competition=esp.1' });
  await sleep(4000);
  const v = (await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
    document.querySelectorAll('.soccer-markets-match, .soccer-markets-ranking').forEach((d) => { d.open = true; });
    const rgb = (v) => (v.match(/[\\d.]+/g) || []).map(Number);
    const lum = ([r, g, b]) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const behind = (e) => { for (let n = e; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c.length === 3 || (c.length === 4 && c[3] >= 0.99)) return c.slice(0, 3); } return [255, 255, 255]; };
    let min = null, worst = null, count = 0;
    for (const e of document.querySelectorAll('.soccer-markets td, .soccer-markets th, .soccer-markets .pill, .soccer-markets summary, .soccer-markets h4, .soccer-markets p, .soccer-markets li')) {
      if (!e.textContent.trim() || e.getBoundingClientRect().width === 0) continue; count++;
      const a = lum(rgb(getComputedStyle(e).color).slice(0, 3)), b = lum(behind(e));
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (min === null || ratio < min) { min = Math.round(ratio * 100) / 100; worst = e.className + ':' + e.textContent.slice(0, 24); }
    }
    return { theme: document.documentElement.dataset.theme, count, min, worst, overflow: document.documentElement.scrollWidth > innerWidth }; })()` })).result.value;
  await sleep(300);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(`${out}/markets-${theme}-${width}.png`, Buffer.from(shot.data, 'base64'));
  res.push({ theme, width, ...v });
  await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`); ws.close();
}
console.log(JSON.stringify(res, null, 1));
